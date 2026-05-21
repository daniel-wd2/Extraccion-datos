import os
import re
import time
from pathlib import Path

from playwright.sync_api import Error, Locator, Page, TimeoutError, sync_playwright


URL = "https://ferniq.fernfutures.com/app/opportunity-flow/stage"
SESSION_FILE = Path(__file__).resolve().parent / "sesion_ferniq.json"
DASHBOARD_MARKERS = [
    re.compile(r"FO\s*-\s*Etapa", re.I),
    re.compile(r"Etapa", re.I),
    re.compile(r"Comercial", re.I),
]


def first_visible(*locators: Locator) -> Locator:
    for locator in locators:
        try:
            if locator.count() and locator.first.is_visible():
                return locator.first
        except Error:
            continue
    raise RuntimeError("No se encontro un elemento visible compatible.")


def is_dashboard_ready(page: Page) -> bool:
    for pattern in DASHBOARD_MARKERS:
        try:
            if page.get_by_text(pattern).first.is_visible():
                return True
        except Error:
            continue
    return False


def wait_for_dashboard(page: Page, timeout_ms: int = 20000) -> bool:
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except TimeoutError:
        pass

    end_time = time.monotonic() + (timeout_ms / 1000.0)
    while time.monotonic() < end_time:
        if is_dashboard_ready(page):
            return True
        page.wait_for_timeout(500)
    return is_dashboard_ready(page)


def try_auto_login(page: Page, email: str, password: str) -> bool:
    print("Intentando login automatico con variables de entorno...")

    email_candidates = [
        page.get_by_label(re.compile(r"(email|correo|usuario|user)", re.I)),
        page.get_by_placeholder(re.compile(r"(email|correo|usuario|user)", re.I)),
        page.locator("input[type='email']"),
        page.locator("input[name*='email' i]"),
        page.locator("input[name*='user' i]"),
    ]
    password_candidates = [
        page.get_by_label(re.compile(r"password|contrasena|clave", re.I)),
        page.get_by_placeholder(re.compile(r"password|contrasena|clave", re.I)),
        page.locator("input[type='password']"),
    ]
    submit_candidates = [
        page.get_by_role("button", name=re.compile(r"(login|log in|sign in|acceder|entrar|iniciar sesion)", re.I)),
        page.locator("button[type='submit']"),
        page.locator("input[type='submit']"),
    ]

    try:
        email_input = first_visible(*email_candidates)
        password_input = first_visible(*password_candidates)
        email_input.fill(email, timeout=10000)
        password_input.fill(password, timeout=10000)

        submit = first_visible(*submit_candidates)
        submit.click(timeout=10000)

        if wait_for_dashboard(page, timeout_ms=30000):
            print("Login automatico completado.")
            return True
    except Exception as exc:
        print(f"No se pudo completar el login automatico: {exc}")

    return False


def main() -> None:
    email = os.getenv("FERNIQ_EMAIL", "").strip()
    password = os.getenv("FERNIQ_PASSWORD", "").strip()

    print("Abriendo Chromium...")
    print(f"URL: {URL}")
    print(f"Sesion destino: {SESSION_FILE}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(URL, wait_until="domcontentloaded")

        auto_login_ok = False
        if email and password and not is_dashboard_ready(page):
            auto_login_ok = try_auto_login(page, email, password)

        if not auto_login_ok and not is_dashboard_ready(page):
            print("Inicia sesion manualmente en la ventana del navegador.")
            print("Cuando veas el dashboard listo, vuelve a esta consola y pulsa ENTER.")
            input("Pulsa ENTER para continuar... ")

        wait_for_dashboard(page, timeout_ms=10000)
        context.storage_state(path=str(SESSION_FILE))
        print(f"Sesion guardada en: {SESSION_FILE}")
        browser.close()


if __name__ == "__main__":
    main()
