import json
import os
import re
import time
from pathlib import Path

from playwright.sync_api import Error, Locator, Page, TimeoutError, sync_playwright

from config_utils import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
LOGIN_URL = "https://ferniq.fernfutures.com/"
TARGET_URL = "https://ferniq.fernfutures.com/app/opportunity-flow/stage"
SESSION_FILE = BASE_DIR / "sesion_ferniq.json"
SESSION_STORAGE_FILE = BASE_DIR / "sesion_ferniq_session_storage.json"
PERSISTENT_PROFILE_DIR = BASE_DIR / ".ferniq_browser_profile"
DEFAULT_CHROME_USER_DATA_DIR = (
    Path(os.getenv("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
)
CHROME_CANDIDATES = [
    Path(os.getenv("CHROME_EXECUTABLE", "")).expanduser() if os.getenv("CHROME_EXECUTABLE") else None,
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(os.getenv("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
]
DASHBOARD_MARKERS = [
    re.compile(r"FO\s*-\s*Etapa", re.I),
    re.compile(r"Etapa", re.I),
    re.compile(r"Comercial", re.I),
]
LOGIN_MARKERS = [
    re.compile(r"iniciar sesion", re.I),
    re.compile(r"sign in", re.I),
    re.compile(r"log in", re.I),
    re.compile(r"password", re.I),
    re.compile(r"correo", re.I),
    re.compile(r"usuario", re.I),
]
NOT_FOUND_MARKERS = [
    re.compile(r"404", re.I),
    re.compile(r"not found", re.I),
    re.compile(r"page not found", re.I),
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


def is_login_page(page: Page) -> bool:
    field_candidates = [
        page.locator("input[type='password']"),
        page.locator("input[type='email']"),
        page.locator("input[name*='user' i]"),
        page.locator("input[name*='email' i]"),
    ]

    for locator in field_candidates:
        try:
            if locator.count() and locator.first.is_visible():
                return True
        except Error:
            continue

    for pattern in LOGIN_MARKERS:
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


def get_chrome_path() -> Path | None:
    for candidate in CHROME_CANDIDATES:
        if candidate and candidate.exists():
            return candidate
    return None


def use_system_chrome_profile() -> bool:
    return os.getenv("USE_SYSTEM_CHROME_PROFILE", "").strip().lower() in {"1", "true", "si", "yes"}


def get_persistent_user_data_dir() -> Path:
    if use_system_chrome_profile():
        configured = os.getenv("CHROME_USER_DATA_DIR", "").strip()
        if configured:
            return Path(configured).expanduser()
        return DEFAULT_CHROME_USER_DATA_DIR
    return PERSISTENT_PROFILE_DIR


def get_profile_directory_argument() -> str | None:
    if not use_system_chrome_profile():
        return None
    profile_directory = os.getenv("CHROME_PROFILE_DIRECTORY", "").strip()
    return profile_directory or "Default"


def open_target_dashboard(page: Page) -> bool:
    try:
        page.goto(TARGET_URL, wait_until="domcontentloaded")
    except TimeoutError:
        pass
    return wait_for_dashboard(page, timeout_ms=30000)


def open_login_page(page: Page) -> None:
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
    except TimeoutError:
        pass


def is_not_found_page(page: Page) -> bool:
    try:
        title = page.title()
    except Error:
        title = ""

    if re.search(r"404|not found", title, re.I):
        return True

    for pattern in NOT_FOUND_MARKERS:
        try:
            if page.get_by_text(pattern).first.is_visible():
                return True
        except Error:
            continue

    return False


def open_stage_from_home(page: Page) -> bool:
    open_login_page(page)
    if is_login_page(page):
        return False
    if is_dashboard_ready(page):
        return True

    candidates = [
        page.get_by_role("link", name=re.compile(r"FO\s*-\s*Etapa", re.I)),
        page.get_by_role("button", name=re.compile(r"FO\s*-\s*Etapa", re.I)),
        page.get_by_text(re.compile(r"FO\s*-\s*Etapa", re.I)),
        page.get_by_role("link", name=re.compile(r"\bEtapa\b", re.I)),
        page.get_by_role("button", name=re.compile(r"\bEtapa\b", re.I)),
        page.locator("a[href*='opportunity-flow' i]"),
        page.locator("a[href*='stage' i]"),
    ]

    target = None
    for locator in candidates:
        try:
            if locator.count() and locator.first.is_visible():
                target = locator.first
                break
        except Error:
            continue

    if target is None:
        return False

    try:
        href = target.get_attribute("href", timeout=1000)
    except Error:
        href = None

    try:
        if href:
            page.goto(href, wait_until="domcontentloaded")
        else:
            target.click(timeout=10000)
    except (TimeoutError, Error):
        return False

    return wait_for_dashboard(page, timeout_ms=30000)


def ensure_dashboard_ready(page: Page) -> None:
    if is_dashboard_ready(page):
        return

    open_target_dashboard(page)
    if is_dashboard_ready(page):
        return

    if is_not_found_page(page) and open_stage_from_home(page):
        return

    current_url = page.url
    if is_login_page(page):
        raise RuntimeError(
            "No se pudo completar el login; sigues en la pagina de acceso. "
            f"URL actual: {current_url}"
        )

    raise RuntimeError(
        "No se pudo verificar una sesion autenticada en Ferniq. "
        f"URL actual: {current_url}"
    )


def extract_session_storage(page: Page) -> dict[str, str]:
    return page.evaluate(
        """
        () => Object.fromEntries(
          Array.from({ length: window.sessionStorage.length }, (_, index) => {
            const key = window.sessionStorage.key(index);
            return [key, window.sessionStorage.getItem(key) ?? ""];
          })
        )
        """
    )


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

        if wait_for_dashboard(page, timeout_ms=10000) or open_target_dashboard(page):
            print("Login automatico completado.")
            return True
    except Exception as exc:
        print(f"No se pudo completar el login automatico: {exc}")

    return False


def save_session(allow_manual: bool = True, headless: bool = False) -> None:
    load_dotenv()
    email = os.getenv("FERNIQ_EMAIL", "").strip()
    password = os.getenv("FERNIQ_PASSWORD", "").strip()
    chrome_path = get_chrome_path()

    if chrome_path and not headless:
        print(f"Abriendo Chrome: {chrome_path}")
    else:
        print("Se abrira Chromium de Playwright.")

    print(f"Login URL: {LOGIN_URL}")
    print(f"URL destino: {TARGET_URL}")
    print(f"Sesion destino: {SESSION_FILE}")
    user_data_dir = get_persistent_user_data_dir()
    profile_directory = get_profile_directory_argument()
    print(f"Perfil persistente: {user_data_dir}")
    if profile_directory:
        print(f"Directorio de perfil Chrome: {profile_directory}")

    with sync_playwright() as p:
        launch_kwargs = {"headless": headless}
        if chrome_path:
            launch_kwargs["executable_path"] = str(chrome_path)
        if profile_directory:
            launch_kwargs["args"] = [f"--profile-directory={profile_directory}"]

        try:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                **launch_kwargs,
            )
        except Exception:
            if not use_system_chrome_profile():
                raise

            fallback_dir = PERSISTENT_PROFILE_DIR
            fallback_kwargs = dict(launch_kwargs)
            fallback_kwargs.pop("args", None)
            print(
                "No se pudo abrir el perfil de Chrome en uso. "
                f"Reintentando con un perfil dedicado: {fallback_dir}"
            )
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(fallback_dir),
                **fallback_kwargs,
            )
        page = context.pages[0] if context.pages else context.new_page()
        open_login_page(page)

        auto_login_ok = False
        if email and password and not is_dashboard_ready(page):
            auto_login_ok = try_auto_login(page, email, password)
        elif not email or not password:
            print("No hay credenciales en .env; se usara login manual.")

        if not auto_login_ok and not is_dashboard_ready(page) and allow_manual:
            print("Inicia sesion manualmente en la ventana del navegador.")
            print("El navegador se queda en la URL de login hasta que completes el acceso.")
            input("Pulsa ENTER para continuar... ")
        elif not auto_login_ok and not is_dashboard_ready(page) and not allow_manual:
            context.close()
            raise RuntimeError(
                "No se pudo regenerar la sesion automaticamente. "
                "Revisa FERNIQ_EMAIL/FERNIQ_PASSWORD o ejecuta guardar_sesion.py manualmente."
            )

        ensure_dashboard_ready(page)
        session_storage = {
            "origin": page.evaluate("() => window.location.origin"),
            "entries": extract_session_storage(page),
        }
        context.storage_state(path=str(SESSION_FILE))
        SESSION_STORAGE_FILE.write_text(
            json.dumps(session_storage, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        cookies_count = len(context.cookies())
        session_storage_count = len(session_storage["entries"])
        print(f"Sesion guardada en: {SESSION_FILE}")
        print(f"Session storage guardado en: {SESSION_STORAGE_FILE}")
        print(f"Cookies guardadas: {cookies_count}")
        print(f"Claves de sessionStorage guardadas: {session_storage_count}")
        context.close()


def main() -> None:
    save_session(allow_manual=True, headless=False)


if __name__ == "__main__":
    main()
