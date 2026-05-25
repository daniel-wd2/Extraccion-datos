import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Iterable

# Garantizar que el directorio del script esté en el sys.path para importaciones locales
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
from openpyxl.styles import Alignment
from playwright.sync_api import Error, Locator, Page, TimeoutError, sync_playwright

from config_utils import load_dotenv
from guardar_sesion_forecast import get_chrome_path, save_session, try_auto_login


LOGIN_URL = "https://appfernfutures-test.zafirus.tech/"
URL = "https://appfernfutures-test.zafirus.tech/app/forecast-vs-goal/level2"
BASE_DIR = Path(__file__).resolve().parent
SESSION_FILE = BASE_DIR / "sesion_forecast.json"
SESSION_STORAGE_FILE = BASE_DIR / "sesion_forecast_session_storage.json"
EXPORTS_DIR = BASE_DIR / "exports"
OUTPUT_FILE = BASE_DIR / "resultado_forecast_ferniq.txt"

COMERCIALES = [
    "Ismael Serrano",
    "Javier Barcelo",
    "Jesus Cabello",
    "Miguel Angel Haro",
    "Paco Buendia",
    "Agustin Parejo",
    "Ramón Jimenez",
    "Daniel Amparán",
]

def get_current_spanish_month_year() -> str:
    spanish_months = {
        1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
        5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
        9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"
    }
    now = datetime.now()
    month_name = spanish_months[now.month]
    return f"{month_name} {now.year}"

MES = get_current_spanish_month_year()

METRIC_NAMES = [
    "Forecast",
]

LOGIN_TEXT_PATTERNS = [
    re.compile(r"iniciar sesion", re.I),
    re.compile(r"sign in", re.I),
    re.compile(r"log in", re.I),
    re.compile(r"password", re.I),
    re.compile(r"correo", re.I),
    re.compile(r"usuario", re.I),
    re.compile(r"contraseña", re.I),
]
NOT_FOUND_TEXT_PATTERNS = [
    re.compile(r"404", re.I),
    re.compile(r"not found", re.I),
    re.compile(r"page not found", re.I),
]

SPINNER_SELECTORS = [
    "[aria-busy='true']",
    ".loading",
    ".loader",
    ".spinner",
    ".mat-progress-spinner",
    ".mat-mdc-progress-spinner",
]

EMPTY_COLUMNS = [
    "comercial",
    "mes",
    "forecast",
    "fecha_extraccion",
    "origen",
]

COMERCIAL_ALIASES = {
    "daniel amparan": ["Mi usuario", "My user", "Daniel Amparán"],
    "agustin parejo": ["Agustin Parejo"],
    "ramon jimenez": ["Ramón Jimenez"],
}


def log(message: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}")


def get_parallel_workers() -> int:
    raw_value = os.getenv("FERNIQ_FORECAST_MAX_WORKERS", "").strip()
    default_workers = 3
    max_workers = 4

    if not raw_value:
        return default_workers

    try:
        parsed = int(raw_value)
    except ValueError:
        log(
            "Valor invalido en FERNIQ_FORECAST_MAX_WORKERS. "
            f"Se usaran {default_workers} workers."
        )
        return default_workers

    return max(1, min(max_workers, parsed))


def split_batches(items: list[str], batch_count: int) -> list[list[str]]:
    if batch_count <= 1:
        return [items[:]]

    batches: list[list[str]] = [[] for _ in range(batch_count)]
    for index, item in enumerate(items):
        batches[index % batch_count].append(item)
    return [batch for batch in batches if batch]


def should_use_system_chrome_for_extraction() -> bool:
    return os.getenv("FERNIQ_FORECAST_USE_SYSTEM_CHROME", "").strip().lower() in {
        "1",
        "true",
        "si",
        "yes",
    }


def normalize_text(value: str) -> str:
    if value is None:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value).strip().lower()


def slugify_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", normalize_text(value))
    return cleaned.strip("_") or "archivo"


def first_visible(
    locators: Iterable[Locator],
    description: str,
    timeout_ms: int = 10000,
) -> Locator:
    locator = find_visible(locators, timeout_ms=timeout_ms)
    if locator is not None:
        return locator
    raise RuntimeError(f"No se encontro un locator visible para: {description}")


def find_visible(
    locators: Iterable[Locator],
    timeout_ms: int = 10000,
) -> Locator | None:
    locators = list(locators)
    deadline = time.monotonic() + (timeout_ms / 1000.0)

    while time.monotonic() < deadline:
        for locator in locators:
            try:
                if locator.count() and locator.first.is_visible():
                    return locator.first
            except Error:
                continue
        time.sleep(0.25)

    return None


def click_locator(locator: Locator, description: str) -> None:
    locator.scroll_into_view_if_needed()
    locator.click(timeout=10000)
    log(f"{description}: OK")


def wait_for_dashboard_settle(page: Page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=2000)
    except TimeoutError:
        pass

    for selector in SPINNER_SELECTORS:
        try:
            page.locator(selector).first.wait_for(state="hidden", timeout=1000)
        except TimeoutError:
            continue
        except Error:
            continue


def locator_is_selected(locator: Locator) -> bool:
    for attribute in ("aria-selected", "aria-pressed", "data-state"):
        try:
            value = locator.get_attribute(attribute, timeout=1000)
        except Error:
            value = None
        if value and value.lower() in {"true", "active", "selected"}:
            return True
    return False


def is_forecast_view_ready(page: Page) -> bool:
    current_url = page.url.lower()
    if "forecast" in current_url or "goal" in current_url:
        ready_markers = [
            page.get_by_text(re.compile(r"Forecast", re.I)),
            page.get_by_text(re.compile(r"Goal", re.I)),
            page.get_by_text(re.compile(r"Meta", re.I)),
            *get_comercial_trigger_candidates(page),
        ]
        return find_visible(ready_markers, timeout_ms=2000) is not None

    markers = [
        page.get_by_text(re.compile(r"Forecast", re.I)),
        page.get_by_text(re.compile(r"Objetivo", re.I)),
        page.get_by_text(re.compile(r"Meta", re.I)),
    ]
    if find_visible(markers, timeout_ms=1500) is not None:
        return find_visible(get_comercial_trigger_candidates(page), timeout_ms=2000) is not None

    return False


def is_login_page(page: Page) -> bool:
    candidates = [
        page.locator("input[type='password']"),
        page.locator("input[type='email']"),
        page.locator("input[name*='user' i]"),
        page.locator("input[name*='email' i]"),
    ]
    if find_visible(candidates, timeout_ms=1000) is not None:
        return True

    text_candidates = [page.get_by_text(pattern) for pattern in LOGIN_TEXT_PATTERNS]
    return find_visible(text_candidates, timeout_ms=1000) is not None


def get_comercial_trigger_candidates(page: Page) -> list[Locator]:
    return [
        page.locator("app-seller-selector ion-select"),
        page.locator("ion-select[aria-haspopup='listbox']"),
        page.locator("ion-select"),
        page.get_by_role("button", name=re.compile(r"Comercial", re.I)),
        page.get_by_role("combobox", name=re.compile(r"Comercial", re.I)),
        page.get_by_label(re.compile(r"Comercial", re.I)),
        page.get_by_text(re.compile(r"^Comercial$", re.I)),
        page.locator("[placeholder*='Comercial' i]"),
    ]


def page_debug_context(page: Page) -> str:
    try:
        title = page.title()
    except Error:
        title = ""
    return f"URL actual: {page.url}. Titulo: {title}"


def redirect_to_login(page: Page) -> None:
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=15000)
    except TimeoutError:
        pass
    except Error:
        pass


def try_auto_login_in_page(page: Page) -> bool:
    load_dotenv()
    email = os.getenv("FERNIQ_FORECAST_EMAIL", "").strip()
    if not email:
        email = os.getenv("FERNIQ_EMAIL", "").strip()
        
    password = os.getenv("FERNIQ_FORECAST_PASSWORD", "").strip()
    if not password:
        password = os.getenv("FERNIQ_PASSWORD", "").strip()
        
    if not email or not password:
        return False

    try:
        return try_auto_login(page, email, password)
    except Exception as exc:
        log(f"Login automatico en extraccion no completado: {exc}")
        return False


def load_session_storage_payload() -> dict | None:
    if not SESSION_STORAGE_FILE.exists():
        return None

    try:
        payload = json.loads(SESSION_STORAGE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None

    origin = payload.get("origin")
    entries = payload.get("entries")
    if not isinstance(origin, str) or not isinstance(entries, dict):
        return None

    cleaned_entries = {
        str(key): "" if value is None else str(value)
        for key, value in entries.items()
    }
    return {"origin": origin, "entries": cleaned_entries}


def restore_session_storage(context) -> None:
    payload = load_session_storage_payload()
    if not payload:
        return

    payload_json = json.dumps(payload, ensure_ascii=False)
    context.add_init_script(
        script=f"""
        (() => {{
          const payload = {payload_json};
          if (window.location.origin !== payload.origin) {{
            return;
          }}
          for (const [key, value] of Object.entries(payload.entries)) {{
            window.sessionStorage.setItem(key, value);
          }}
        }})();
        """
    )


def is_not_found_page(page: Page) -> bool:
    try:
        title = page.title()
    except Error:
        title = ""

    if re.search(r"404|not found", title, re.I):
        return True

    text_candidates = [page.get_by_text(pattern) for pattern in NOT_FOUND_TEXT_PATTERNS]
    return find_visible(text_candidates, timeout_ms=1000) is not None


def open_forecast_from_home(page: Page) -> bool:
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=15000)
    except TimeoutError:
        pass
    except Error:
        pass

    wait_for_dashboard_settle(page)
    if is_login_page(page):
        if not try_auto_login_in_page(page):
            return False
        wait_for_dashboard_settle(page)
    if is_forecast_view_ready(page):
        return True

    menu_button = find_visible(
        [
            page.locator("ion-menu-button"),
            page.get_by_role("button", name=re.compile(r"menu", re.I)),
        ],
        timeout_ms=5000,
    )
    if menu_button is not None:
        click_locator(menu_button, "Menu principal")
        page.wait_for_timeout(1000)

    candidates = [
        page.locator("ion-item").filter(has_text=re.compile(r"Forecast", re.I)),
        page.get_by_text(re.compile(r"Forecast vs Goal", re.I)),
        page.get_by_role("link", name=re.compile(r"Forecast", re.I)),
        page.get_by_role("button", name=re.compile(r"Forecast", re.I)),
        page.get_by_text(re.compile(r"Forecast", re.I)),
        page.locator("a[href*='forecast' i]"),
        page.locator("a[href*='goal' i]"),
    ]

    target = find_visible(candidates, timeout_ms=8000)
    if target is None:
        return False

    try:
        href = target.get_attribute("href", timeout=1000)
    except Error:
        href = None

    if href:
        try:
            page.goto(href, wait_until="domcontentloaded", timeout=15000)
        except TimeoutError:
            pass
        except Error:
            click_locator(target, "Acceso a Forecast")
    else:
        click_locator(target, "Acceso a Forecast")

    wait_for_dashboard_settle(page)
    return is_forecast_view_ready(page)


def open_forecast_view(page: Page) -> None:
    try:
        page.goto(URL, wait_until="domcontentloaded", timeout=15000)
    except TimeoutError:
        pass
    except Error:
        pass

    wait_for_dashboard_settle(page)

    if is_login_page(page):
        if try_auto_login_in_page(page):
            wait_for_dashboard_settle(page)
            if is_forecast_view_ready(page):
                return
        else:
            redirect_to_login(page)
            raise RuntimeError(
                "La sesion no esta activa o la web ha redirigido al login. "
                f"Ejecuta guardar_sesion_forecast.py otra vez. {page_debug_context(page)}"
            )

    if is_forecast_view_ready(page):
        return

    if is_not_found_page(page):
        log("La URL directa de Forecast devuelve 404; se intentara abrir desde la home")

    if open_forecast_from_home(page):
        return

    if is_login_page(page):
        if not try_auto_login_in_page(page):
            redirect_to_login(page)
            raise RuntimeError(
                "La sesion no esta activa o la web ha redirigido al login. "
                f"Ejecuta guardar_sesion_forecast.py otra vez. {page_debug_context(page)}"
            )
        wait_for_dashboard_settle(page)
        if open_forecast_from_home(page):
            return


def ensure_forecast_tab(page: Page) -> None:
    wait_for_dashboard_settle(page)
    if is_forecast_view_ready(page):
        log("Vista Forecast ya activa")
        return

    if is_login_page(page):
        redirect_to_login(page)
        raise RuntimeError(
            "La sesion no esta activa o la web ha redirigido al login. "
            f"Ejecuta guardar_sesion_forecast.py otra vez. {page_debug_context(page)}"
        )

    candidates = [
        page.get_by_role("tab", name=re.compile(r"Forecast", re.I)),
        page.locator("[role='tab']").filter(has_text=re.compile(r"Forecast", re.I)),
        page.get_by_role("button", name=re.compile(r"Forecast", re.I)),
        page.get_by_text(re.compile(r"Forecast", re.I)),
    ]

    tab = find_visible(candidates, timeout_ms=5000)
    if tab is not None:
        if not locator_is_selected(tab):
            click_locator(tab, "Pestana Forecast")
        else:
            log("Pestana Forecast ya seleccionada")
        wait_for_dashboard_settle(page)
    else:
        log("Pestana Forecast no visible; se intentara continuar si Comercial ya esta disponible")

    if is_forecast_view_ready(page):
        return

    if is_login_page(page):
        redirect_to_login(page)
        raise RuntimeError(
            "La sesion no esta activa o la web ha redirigido al login. "
            f"Ejecuta guardar_sesion_forecast.py otra vez. {page_debug_context(page)}"
        )

    raise RuntimeError(
        "No se detecto la vista de Forecast ni el selector Comercial. "
        f"{page_debug_context(page)}"
    )


def get_comercial_trigger(page: Page) -> Locator:
    candidates = get_comercial_trigger_candidates(page)
    return first_visible(candidates, "selector Comercial")


def canonical_comercial_name(value: str) -> str:
    normalized = normalize_text(value)
    alias_to_canonical = {
        "mi usuario": "Daniel Amparán",
        "my user": "Daniel Amparán",
        "daniel amparan": "Daniel Amparán",
        "ramon jimenez": "Ramón Jimenez",
    }
    return alias_to_canonical.get(normalized, value.strip())


def is_valid_comercial_option(value: str) -> bool:
    normalized = normalize_text(value)
    if not normalized:
        return False

    invalid_values = {
        "comercial",
        "selecciona",
        "seleccione",
        "select",
        "todos",
        "all",
    }
    return normalized not in invalid_values


def get_available_comerciales(page: Page) -> list[str]:
    candidates = [
        page.locator("app-seller-selector ion-select-option"),
        page.locator("ion-select-option"),
    ]

    detected: list[str] = []
    seen: set[str] = set()

    for locator in candidates:
        try:
            total = locator.count()
        except Error:
            continue

        for index in range(total):
            option = locator.nth(index)
            try:
                text = option.inner_text(timeout=1000).strip()
            except Error:
                try:
                    text = (option.get_attribute("value", timeout=1000) or "").strip()
                except Error:
                    text = ""

            if not is_valid_comercial_option(text):
                continue

            canonical = canonical_comercial_name(text)
            normalized = normalize_text(canonical)
            if normalized in seen:
                continue

            seen.add(normalized)
            detected.append(canonical)

        if detected:
            break

    return detected


def get_comerciales_to_process(page: Page) -> list[str]:
    detected = get_available_comerciales(page)
    if detected:
        official_by_normalized = {
            normalize_text(canonical_comercial_name(comercial)): canonical_comercial_name(comercial)
            for comercial in COMERCIALES
        }
        filtered = [
            official_by_normalized[normalize_text(canonical_comercial_name(comercial))]
            for comercial in detected
            if normalize_text(canonical_comercial_name(comercial)) in official_by_normalized
        ]
        if filtered:
            return filtered
        return detected
    return COMERCIALES


def open_comercial_popup(page: Page) -> Locator:
    trigger = get_comercial_trigger(page)
    click_locator(trigger, "Selector Comercial")

    popup_candidates = [
        page.locator("ion-alert"),
        page.locator("[role='alertdialog']"),
        page.get_by_role("dialog"),
        page.locator("[role='dialog']"),
        page.locator(".cdk-overlay-pane"),
        page.locator(".v-overlay__content"),
        page.locator(".mat-mdc-select-panel"),
        page.locator(".alert-wrapper"),
    ]

    popup = first_visible(popup_candidates, "popup de Comercial")
    popup.wait_for(state="visible", timeout=10000)
    return popup


def uncheck_all_selected(popup: Locator) -> None:
    role_checkboxes = popup.get_by_role("checkbox")
    try:
        total = role_checkboxes.count()
    except Error:
        total = 0

    for index in range(total):
        checkbox = role_checkboxes.nth(index)
        try:
            checked = checkbox.is_checked(timeout=1000)
        except Error:
            checked = checkbox.get_attribute("aria-checked") == "true"
        if checked:
            checkbox.click(timeout=5000, force=True)

    native_checked = popup.locator("input[type='checkbox']:checked")
    try:
        native_total = native_checked.count()
    except Error:
        native_total = 0

    for index in range(native_total):
        checkbox = native_checked.nth(index)
        try:
            checkbox.click(timeout=5000, force=True)
        except Error:
            try:
                checkbox.evaluate("(el) => el.click()")
            except Error:
                continue


def select_comercial(popup: Locator, comercial: str) -> None:
    candidate_names = [comercial, *COMERCIAL_ALIASES.get(normalize_text(comercial), [])]

    for candidate_name in candidate_names:
        candidates = [
            popup.get_by_role("checkbox", name=re.compile(rf"^{re.escape(candidate_name)}$", re.I)),
            popup.get_by_label(re.compile(rf"^{re.escape(candidate_name)}$", re.I)),
            popup.get_by_text(re.compile(rf"^{re.escape(candidate_name)}$", re.I)),
        ]
        try:
            option = first_visible(candidates, f"comercial {candidate_name}", timeout_ms=5000)
            click_locator(option, f"Seleccionar comercial {comercial} ({candidate_name})")
            return
        except RuntimeError:
            pass

    checkbox_like = popup.locator("label, [role='checkbox'], .mat-checkbox, .mdc-form-field")
    total = checkbox_like.count()
    target_names = {normalize_text(name) for name in candidate_names}

    for index in range(total):
        option = checkbox_like.nth(index)
        try:
            text = option.inner_text(timeout=1000)
        except Error:
            continue
        if normalize_text(text) not in target_names:
            continue
        click_locator(option, f"Seleccionar comercial {comercial}")
        return

    alias_text = ", ".join(candidate_names)
    raise RuntimeError(f"No se encontro el comercial '{comercial}' en el popup. Alias probados: {alias_text}")


def confirm_popup(popup: Locator) -> None:
    candidates = [
        popup.get_by_role("button", name=re.compile(r"^OK$", re.I)),
        popup.get_by_text(re.compile(r"^OK$", re.I)),
    ]
    ok_button = first_visible(candidates, "boton OK del popup")
    click_locator(ok_button, "Boton OK")


def select_only_comercial(page: Page, comercial: str) -> None:
    popup = open_comercial_popup(page)
    uncheck_all_selected(popup)
    select_comercial(popup, comercial)
    confirm_popup(popup)
    wait_for_dashboard_settle(page)
    log("Seleccionado correctamente")


def limpiar_precio(valor) -> float | None:
    if valor is None:
        return None

    text = str(valor).strip()
    if not text:
        return None

    text = (
        text.replace("EUR", "")
        .replace("eur", "")
        .replace("€", "")
        .replace("\u00a0", "")
        .replace(" ", "")
    )

    if not text:
        return None

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        if len(parts[-1]) in {1, 2}:
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "." in text:
        parts = text.split(".")
        if len(parts[-1]) == 3 and len(parts) > 1:
            text = text.replace(".", "")

    text = re.sub(r"[^0-9.-]", "", text)
    if not text or text in {"-", ".", "-.", ".-"}:
        return None

    try:
        return float(text)
    except ValueError:
        return None


def try_extract_download(page: Page, comercial: str, extraction_date: str) -> list[dict]:
    download_button_candidates = [
        page.get_by_role("button", name=re.compile(r"Descargar", re.I)),
        page.get_by_text(re.compile(r"^Descargar$", re.I)),
    ]

    try:
        download_button = first_visible(download_button_candidates, "boton Descargar", timeout_ms=1000)
    except RuntimeError:
        log("Boton Descargar no encontrado; se usara lectura DOM.")
        return []

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        with page.expect_download(timeout=15000) as download_info:
            click_locator(download_button, "Boton Descargar")
        download = download_info.value
        suggested_name = download.suggested_filename
        file_path = EXPORTS_DIR / f"{slugify_filename(comercial)}_{timestamp}_{suggested_name}"
        download.save_as(str(file_path))
        log(f"Archivo descargado: {file_path.name}")
        records = read_download_records(file_path, comercial, extraction_date)
        if records:
            log("Datos obtenidos desde descarga")
        else:
            log("La descarga no contenia datos utiles para Forecast; se usara lectura DOM.")
        return records
    except TimeoutError:
        log("La descarga no arranco a tiempo; se usara lectura DOM.")
        return []
    except Error as exc:
        log(f"Descarga no utilizable: {exc}")
        return []


def read_download_records(file_path: Path, comercial: str, extraction_date: str) -> list[dict]:
    extension = file_path.suffix.lower()
    dataframes: list[tuple[str, pd.DataFrame]] = []

    try:
        if extension == ".csv":
            dataframes.append((file_path.name, pd.read_csv(file_path)))
        elif extension in {".xlsx", ".xls"}:
            sheets = pd.read_excel(file_path, sheet_name=None)
            dataframes.extend(sheets.items())
        else:
            return []
    except Exception as exc:
        log(f"No se pudo leer el archivo descargado {file_path.name}: {exc}")
        return []

    records: list[dict] = []
    for sheet_name, dataframe in dataframes:
        normalized = normalize_download_dataframe(
            dataframe=dataframe,
            origen=f"descarga:{file_path.name}:{sheet_name}",
            comercial=comercial,
            extraction_date=extraction_date,
        )
        records.extend(normalized)

    return deduplicate_records(records)


def normalize_column_name(name: str) -> str:
    return normalize_text(str(name)).replace(" ", "_")


def match_first_column(columns: list[str], patterns: list[str]) -> str | None:
    for column in columns:
        if any(pattern in column for pattern in patterns):
            return column
    return None


def normalize_download_dataframe(
    dataframe: pd.DataFrame,
    origen: str,
    comercial: str,
    extraction_date: str,
) -> list[dict]:
    if dataframe.empty:
        return []

    df = dataframe.copy()
    df.columns = [normalize_column_name(column) for column in df.columns]
    df = df.dropna(how="all")
    if df.empty:
        return []

    columns = list(df.columns)
    forecast_col = match_first_column(columns, ["forecast", "pronostico", "estimado", "estimacion"])

    records: list[dict] = []

    if forecast_col:
        for _, row in df.iterrows():
            f_val = limpiar_precio(row.get(forecast_col))
            if f_val is not None:
                records.append(
                    build_record(
                        comercial=comercial,
                        forecast=f_val,
                        extraction_date=extraction_date,
                        origen=origen,
                    )
                )
        return records

    return records


def build_record(
    comercial: str,
    forecast: float | None,
    extraction_date: str,
    origen: str,
) -> dict:
    return {
        "comercial": comercial,
        "mes": MES,
        "forecast": forecast,
        "fecha_extraccion": extraction_date,
        "origen": origen,
    }


def collect_forecast_blocks(page: Page) -> list[dict]:
    """
    Evaluates the page to look for key numbers associated with metrics (Forecast, Goal, Ventas).
    It tries to find cards, labels, or containers that contain numeric currency representations
    and associates them by matching text nearby.
    """
    return page.evaluate(
        """
        () => {
          const normalize = (text) =>
            (text || "")
              .replace(/\\u00a0/g, " ")
              .replace(/\\s+/g, " ")
              .trim();

          const visible = (el) => {
            const style = window.getComputedStyle(el);
            if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) {
              return false;
            }
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
          };

          const parseCurrency = (str) => {
            if (!str) return null;
            const cleaned = str.replace(/EUR/gi, "").replace(/€/g, "").replace(/\\s+/g, "").trim();
            // Match typical currency format, e.g. 10.000,50 or 10,000.50
            if (/[\\d]/.test(cleaned)) {
              return cleaned;
            }
            return null;
          };

          // Search containers like ion-card, mat-card, or generic divs with card-like classes
          const cards = Array.from(document.querySelectorAll("ion-card, mat-card, .card, .bg-blue, .bg-light, ion-item, div"));
          const results = [];

          cards.forEach((card) => {
            if (!visible(card)) return;
            const text = normalize(card.innerText);
            if (!text || text.length > 300) return;

            // If it contains metric terms and currency patterns
            const hasMetric = /forecast|goal|objetivo|meta|venta|real/i.test(text);
            const hasCurrency = /[\\d]+[.,]?[\\d]*\\s*(€|EUR)/i.test(text) || /(€|EUR)\\s*[\\d]+/i.test(text);

            if (hasMetric && hasCurrency) {
              results.push({
                text: text,
                html: card.outerHTML.substring(0, 150)
              });
            }
          });

          // Also scan the whole body for text lines if simple container scanning is limited
          const lines = normalize(document.body.innerText).split(/\\n+/);
          const lineResults = [];
          lines.forEach((line) => {
            if (line.length < 100 && /forecast|goal|objetivo|meta|venta|real/i.test(line) && /[\\d]/.test(line)) {
              lineResults.push(line);
            }
          });

          return { cards: results, lines: lineResults };
        }
        """
    )


def extract_forecast_data_from_dom(page: Page, comercial: str, extraction_date: str) -> list[dict]:
    data = collect_forecast_blocks(page)
    cards = data.get("cards", [])
    lines = data.get("lines", [])

    forecast = None

    # Helper function to find currency in a piece of text
    def find_currency_val(text: str, patterns: list[str]) -> float | None:
        for pattern in patterns:
            # Look for lines containing the keyword and a currency
            if re.search(pattern, text, re.I):
                # Search for currency format
                matches = re.findall(
                    r"(?:€\s*[\d.,]+|[\d.,]+\s*€|EUR\s*[\d.,]+|[\d.,]+\s*EUR)",
                    text,
                    flags=re.I,
                )
                if matches:
                    val = limpiar_precio(matches[0])
                    if val is not None:
                        return val
                # Fallback to simple number
                num_matches = re.findall(r"\b\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?\b", text)
                for num in num_matches:
                    val = limpiar_precio(num)
                    if val is not None and val > 10:  # Avoid small counts
                        return val
        return None

    # 1. Search in card blocks first (most structured)
    for card in cards:
        text = card["text"]
        if forecast is None:
            forecast = find_currency_val(text, [r"forecast", r"pronostico", r"estimado", r"estimacion"])

    # 2. Search in body lines (less structured fallback)
    for line in lines:
        if forecast is None:
            forecast = find_currency_val(line, [r"forecast", r"pronostico", r"estimado", r"estimacion"])

    if forecast is not None:
        log(f"Datos DOM extraidos: Forecast={forecast}")
        return [
            build_record(
                comercial=comercial,
                forecast=forecast,
                extraction_date=extraction_date,
                origen="dom",
            )
        ]

    # 3. Last resort: scan all text in body
    body_text = page.locator("body").inner_text(timeout=5000)
    forecast = find_currency_val(body_text, [r"forecast", r"pronostico", r"estimado", r"estimacion"])

    if forecast is not None:
        log(f"Datos DOM Body extraidos: Forecast={forecast}")
        return [
            build_record(
                comercial=comercial,
                forecast=forecast,
                extraction_date=extraction_date,
                origen="dom_body",
            )
        ]

    return []


def deduplicate_records(records: list[dict]) -> list[dict]:
    best_by_key: dict[tuple[str, str], dict] = {}

    for record in records:
        key = (record["comercial"], record["mes"])
        current = best_by_key.get(key)
        if current is None:
            best_by_key[key] = record
            continue

        current_score = score_record(current)
        new_score = score_record(record)
        if new_score > current_score:
            best_by_key[key] = record

    return list(best_by_key.values())


def score_record(record: dict) -> tuple[int, int, int]:
    return (
        1 if record.get("forecast") is not None else 0,
        len(str(record.get("forecast", ""))),
        0,
    )


def format_money(value: float | int | None) -> str:
    if value is None:
        return "0,00"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0,00"
    return f"{number:.2f}".replace(".", ",")


def save_results(records: list[dict]) -> Path:
    # Inicializar un diccionario con todos los comerciales de la lista oficial en 0.00
    forecasts = {comercial: 0.0 for comercial in COMERCIALES}
    
    # Rellenar con los valores extraídos con éxito usando normalización para el matching
    for record in records:
        comercial = record.get("comercial")
        if not comercial:
            continue
            
        canonical = canonical_comercial_name(comercial)
        fc = record.get("forecast")
        if fc is not None:
            # Buscar coincidencia normalizada en la lista de comerciales oficiales
            matched = False
            for c_official in COMERCIALES:
                if normalize_text(c_official) == normalize_text(canonical):
                    try:
                        forecasts[c_official] = float(fc)
                        matched = True
                        break
                    except ValueError:
                        pass
            # Si no coincide con ninguno oficial, lo añadimos directamente
            if not matched:
                try:
                    forecasts[canonical] = float(fc)
                except ValueError:
                    pass

    # Construir las líneas con el formato exacto: "Nombre Comercial: decimal"
    lines = []
    for comercial, fc in forecasts.items():
        lines.append(f"{comercial}: {format_money(fc)}")
        
    OUTPUT_FILE.write_text("\n".join(lines), encoding="utf-8")
    log(f"Reporte de texto generado: {OUTPUT_FILE}")
    return OUTPUT_FILE


def load_results_text() -> str:
    if not OUTPUT_FILE.exists():
        raise FileNotFoundError(f"No existe el reporte de texto final: {OUTPUT_FILE}")
    return OUTPUT_FILE.read_text(encoding="utf-8")


def load_results_dataframe() -> pd.DataFrame:
    # Placeholder to keep backwards compatibility
    return pd.DataFrame(columns=["comercial", "mes", "forecast"])


def is_auth_error(exc: Exception) -> bool:
    message = str(exc)
    return "La sesion no esta activa o la web ha redirigido al login." in message


def is_missing_session_error(exc: Exception) -> bool:
    return isinstance(exc, FileNotFoundError) and str(SESSION_FILE) in str(exc)


def try_refresh_session_automatically(headless: bool) -> bool:
    log("Intentando regenerar sesion automaticamente")
    try:
        save_session(allow_manual=False, headless=headless)
        return True
    except Exception as exc:
        log(f"No se pudo regenerar la sesion automaticamente: {exc}")
        return False


def run_extraction(headless: bool = False) -> Path:
    load_dotenv()
    if not SESSION_FILE.exists():
        raise FileNotFoundError(
            f"No existe la sesion guardada en {SESSION_FILE}. Ejecuta primero guardar_sesion_forecast.py."
        )

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    extraction_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results: list[dict] = []

    def launch_browser_instance(playwright):
        launch_kwargs = {"headless": headless}
        chrome_path = get_chrome_path() if should_use_system_chrome_for_extraction() else None
        if chrome_path:
            launch_kwargs["executable_path"] = str(chrome_path)
        try:
            return playwright.chromium.launch(ignore_default_args=["--no-sandbox"], **launch_kwargs)
        except Exception as launch_exc:
            log(f"No se pudo lanzar Chrome del sistema ({launch_exc}). Intentando con Chromium de Playwright...")
            if "executable_path" in launch_kwargs:
                del launch_kwargs["executable_path"]
            return playwright.chromium.launch(ignore_default_args=["--no-sandbox"], **launch_kwargs)

    def detect_comerciales() -> list[str]:
        with sync_playwright() as p:
            browser = launch_browser_instance(p)
            context = browser.new_context(storage_state=str(SESSION_FILE), accept_downloads=True)
            restore_session_storage(context)
            page = context.new_page()
            try:
                open_forecast_view(page)
                ensure_forecast_tab(page)
                return get_comerciales_to_process(page)
            finally:
                context.close()
                browser.close()

    def extract_for_comercial_with_page(page: Page, comercial: str) -> list[dict]:
        ensure_forecast_tab(page)
        select_only_comercial(page, comercial)
        records = try_extract_download(page, comercial, extraction_date)
        if not records:
            records = extract_forecast_data_from_dom(page, comercial, extraction_date)
        return records

    def extract_for_comercial(comercial: str) -> list[dict]:
        with sync_playwright() as p:
            browser = launch_browser_instance(p)
            context = browser.new_context(storage_state=str(SESSION_FILE), accept_downloads=True)
            restore_session_storage(context)
            page = context.new_page()
            try:
                open_forecast_view(page)
                return extract_for_comercial_with_page(page, comercial)
            finally:
                context.close()
                browser.close()

    def extract_for_comerciales_batch(comercial_batch: list[str]) -> list[tuple[str, list[dict] | None, Exception | None]]:
        batch_results: list[tuple[str, list[dict] | None, Exception | None]] = []
        with sync_playwright() as p:
            browser = launch_browser_instance(p)
            context = browser.new_context(storage_state=str(SESSION_FILE), accept_downloads=True)
            restore_session_storage(context)
            page = context.new_page()
            try:
                open_forecast_view(page)
                ensure_forecast_tab(page)

                for comercial in comercial_batch:
                    try:
                        records = extract_for_comercial_with_page(page, comercial)
                    except Exception:
                        # Reabrir la vista una vez evita perder todo el batch por una sesion inestable.
                        open_forecast_view(page)
                        ensure_forecast_tab(page)
                        try:
                            records = extract_for_comercial_with_page(page, comercial)
                        except Exception as exc:
                            batch_results.append((comercial, None, exc))
                            continue

                    batch_results.append((comercial, records, None))
            finally:
                context.close()
                browser.close()

        return batch_results

    comerciales = detect_comerciales()
    log(f"Comerciales detectados: {', '.join(comerciales)}")

    workers = min(get_parallel_workers(), max(1, len(comerciales)))
    log(f"Procesando Forecast con {workers} worker(s) en paralelo")

    if workers == 1:
        for comercial in comerciales:
            log(f"Procesando comercial: {comercial}")
            try:
                records = extract_for_comercial(comercial)
                if records:
                    results.extend(records)
                    log(f"Datos extraidos para {comercial}")
                else:
                    log(f"Sin datos visibles para {comercial}")
            except Exception as exc:
                log(f"Error procesando comercial {comercial}: {exc}")
                continue
    else:
        comercial_batches = split_batches(comerciales, workers)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_batch = {
                executor.submit(extract_for_comerciales_batch, comercial_batch): comercial_batch
                for comercial_batch in comercial_batches
            }
            for future in as_completed(future_to_batch):
                try:
                    batch_results = future.result()
                except Exception as exc:
                    batch_name = ", ".join(future_to_batch[future])
                    log(f"Error procesando lote de comerciales {batch_name}: {exc}")
                    continue

                for comercial, records, error in batch_results:
                    if error is not None:
                        log(f"Error procesando comercial {comercial}: {error}")
                        continue
                    if records:
                        results.extend(records)
                        log(f"Datos extraidos para {comercial}")
                    else:
                        log(f"Sin datos visibles para {comercial}")

    deduped = deduplicate_records(results)
    if not deduped:
        log("No se extrajeron datos; se generara un Excel vacio con columnas.")
        return save_results([])

    return save_results(deduped)


def run_extraction_with_retry() -> Path:
    try:
        log("Intentando extraccion en modo headless")
        return run_extraction(headless=True)
    except Exception as exc:
        log(f"Fallo en modo headless: {exc}")

        if is_auth_error(exc) or is_missing_session_error(exc):
            if try_refresh_session_automatically(headless=True):
                log("Sesion regenerada automaticamente; reintentando extraccion en modo headless")
                return run_extraction(headless=True)
            if try_refresh_session_automatically(headless=False):
                log("Sesion regenerada automaticamente en modo visible; reintentando extraccion en modo visible")
                return run_extraction(headless=False)

        log("Reintentando extraccion en modo visible")
        return run_extraction(headless=False)


def main() -> None:
    run_extraction(headless=False)


if __name__ == "__main__":
    main()
