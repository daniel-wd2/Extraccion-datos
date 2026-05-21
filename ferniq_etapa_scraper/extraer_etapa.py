import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
from playwright.sync_api import Error, Locator, Page, TimeoutError, sync_playwright


URL = "https://ferniq.fernfutures.com/app/opportunity-flow/stage"
BASE_DIR = Path(__file__).resolve().parent
SESSION_FILE = BASE_DIR / "sesion_ferniq.json"
EXPORTS_DIR = BASE_DIR / "exports"
OUTPUT_FILE = BASE_DIR / "resultado_etapa_ferniq.xlsx"

COMERCIALES = [
    "Ismael Serrano",
    "Javier Barcelo",
    "Jesus Cabello",
    "Miguel Angel Haro",
    "Paco Buendia",
    "Francisco Javier Alba",
    "Ramón Jimenez",
]

MES = "Mayo 2026"

STAGE_NAMES = [
    "Aceptado",
    "Anulado / Perdido",
    "Contacto inicial",
    "Envio de presupuesto",
    "Negociacion",
    "Oportunidad de valor",
    "Por confirmar / Sin respuesta",
    "Proceso de compras",
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
    "etapa",
    "cantidad",
    "valor_texto",
    "precio",
    "fecha_extraccion",
    "origen",
]


def log(message: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}")


def normalize_text(value: str) -> str:
    if value is None:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value).strip().lower()


def slugify_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", normalize_text(value))
    return cleaned.strip("_") or "archivo"


def first_visible(locators: Iterable[Locator]) -> Locator:
    for locator in locators:
        try:
            if locator.count() and locator.first.is_visible():
                return locator.first
        except Error:
            continue
    raise RuntimeError("No se encontro un locator visible.")


def click_locator(locator: Locator, description: str) -> None:
    locator.scroll_into_view_if_needed()
    locator.click(timeout=10000)
    log(f"{description}: OK")


def wait_for_dashboard_settle(page: Page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except TimeoutError:
        pass

    for selector in SPINNER_SELECTORS:
        try:
            page.locator(selector).first.wait_for(state="hidden", timeout=3000)
        except TimeoutError:
            continue
        except Error:
            continue


def ensure_stage_tab(page: Page) -> None:
    candidates = [
        page.get_by_role("tab", name=re.compile(r"^Etapa$", re.I)),
        page.get_by_text(re.compile(r"^Etapa$", re.I)),
        page.locator("text=Etapa"),
    ]
    tab = first_visible(candidates)
    click_locator(tab, "Pestana Etapa")
    wait_for_dashboard_settle(page)


def get_comercial_trigger(page: Page) -> Locator:
    candidates = [
        page.get_by_role("button", name=re.compile(r"Comercial", re.I)),
        page.get_by_role("combobox", name=re.compile(r"Comercial", re.I)),
        page.get_by_label(re.compile(r"Comercial", re.I)),
        page.get_by_text(re.compile(r"^Comercial$", re.I)),
        page.locator("[placeholder*='Comercial' i]"),
    ]
    return first_visible(candidates)


def open_comercial_popup(page: Page) -> Locator:
    trigger = get_comercial_trigger(page)
    click_locator(trigger, "Selector Comercial")

    popup_candidates = [
        page.get_by_role("dialog"),
        page.locator("[role='dialog']"),
        page.locator(".cdk-overlay-pane"),
        page.locator(".v-overlay__content"),
        page.locator(".mat-mdc-select-panel"),
    ]

    popup = first_visible(popup_candidates)
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
    candidates = [
        popup.get_by_role("checkbox", name=re.compile(rf"^{re.escape(comercial)}$", re.I)),
        popup.get_by_label(re.compile(rf"^{re.escape(comercial)}$", re.I)),
        popup.get_by_text(re.compile(rf"^{re.escape(comercial)}$", re.I)),
    ]
    try:
        option = first_visible(candidates)
        click_locator(option, f"Seleccionar comercial {comercial}")
        return
    except RuntimeError:
        pass

    checkbox_like = popup.locator("label, [role='checkbox'], .mat-checkbox, .mdc-form-field")
    total = checkbox_like.count()
    target = normalize_text(comercial)

    for index in range(total):
        option = checkbox_like.nth(index)
        try:
            text = option.inner_text(timeout=1000)
        except Error:
            continue
        if normalize_text(text) != target:
            continue
        click_locator(option, f"Seleccionar comercial {comercial}")
        return

    raise RuntimeError(f"No se encontro el comercial '{comercial}' en el popup.")


def confirm_popup(popup: Locator) -> None:
    candidates = [
        popup.get_by_role("button", name=re.compile(r"^OK$", re.I)),
        popup.get_by_text(re.compile(r"^OK$", re.I)),
    ]
    ok_button = first_visible(candidates)
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


def parse_quantity(value: str) -> int | None:
    if not value:
        return None

    cleaned = value.replace(".", "").replace(",", "")
    cleaned = re.sub(r"[^\d-]", "", cleaned)
    if not cleaned:
        return None

    try:
        return int(cleaned)
    except ValueError:
        return None


def try_extract_download(page: Page, comercial: str, extraction_date: str) -> list[dict]:
    download_button_candidates = [
        page.get_by_role("button", name=re.compile(r"Descargar", re.I)),
        page.get_by_text(re.compile(r"^Descargar$", re.I)),
    ]

    try:
        download_button = first_visible(download_button_candidates)
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
            log("La descarga no contenia datos utiles para Etapa; se usara lectura DOM.")
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
    etapa_col = match_first_column(columns, ["etapa", "stage", "estado"])
    cantidad_col = match_first_column(columns, ["cantidad", "count", "qty", "numero", "numero_de"])
    valor_col = match_first_column(columns, ["valor", "precio", "importe", "amount", "total"])

    records: list[dict] = []

    if etapa_col:
        for _, row in df.iterrows():
            etapa = str(row.get(etapa_col, "")).strip()
            if not etapa or etapa.lower() == "nan":
                continue

            stage_match = find_stage_name(etapa)
            if not stage_match:
                continue

            valor_texto = ""
            if valor_col:
                raw_value = row.get(valor_col)
                if pd.notna(raw_value):
                    valor_texto = str(raw_value).strip()

            cantidad = None
            if cantidad_col:
                raw_count = row.get(cantidad_col)
                if pd.notna(raw_count):
                    cantidad = parse_quantity(str(raw_count))

            records.append(
                build_record(
                    comercial=comercial,
                    etapa=stage_match,
                    cantidad=cantidad,
                    valor_texto=valor_texto,
                    precio=limpiar_precio(valor_texto),
                    extraction_date=extraction_date,
                    origen=origen,
                )
            )
        return records

    for column in df.columns:
        stage_match = find_stage_name(column)
        if not stage_match:
            continue
        series = df[column].dropna()
        if series.empty:
            continue
        first_value = series.iloc[0]
        value_text = str(first_value).strip()
        records.append(
            build_record(
                comercial=comercial,
                etapa=stage_match,
                cantidad=parse_quantity(value_text),
                valor_texto=value_text,
                precio=limpiar_precio(value_text),
                extraction_date=extraction_date,
                origen=origen,
            )
        )

    return records


def build_record(
    comercial: str,
    etapa: str,
    cantidad: int | None,
    valor_texto: str,
    precio: float | None,
    extraction_date: str,
    origen: str,
) -> dict:
    return {
        "comercial": comercial,
        "mes": MES,
        "etapa": etapa,
        "cantidad": cantidad,
        "valor_texto": valor_texto.strip(),
        "precio": precio,
        "fecha_extraccion": extraction_date,
        "origen": origen,
    }


def find_stage_name(text: str) -> str | None:
    normalized = normalize_text(text)
    for stage in STAGE_NAMES:
        if normalize_text(stage) in normalized:
            return canonical_stage_name(stage)
    return None


def canonical_stage_name(stage: str) -> str:
    mapping = {
        "envio de presupuesto": "Envio de presupuesto",
        "negociacion": "Negociacion",
    }
    normalized = normalize_text(stage)
    return mapping.get(normalized, stage)


def collect_stage_blocks(page: Page) -> list[str]:
    return page.evaluate(
        """
        ({ stageNames }) => {
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

          const unique = new Set();
          const results = [];
          const normalizedNames = stageNames.map((name) => normalize(name).toLowerCase());

          for (const el of document.querySelectorAll("body *")) {
            if (!visible(el)) continue;
            const text = normalize(el.innerText);
            if (!text || text.length > 240) continue;
            const lowered = text.toLowerCase();
            if (!normalizedNames.some((name) => lowered.includes(name))) continue;
            const lineCount = text.split(/\\n+/).length;
            if (lineCount > 8) continue;
            if (!unique.has(text)) {
              unique.add(text);
              results.push(text);
            }
          }

          return results;
        }
        """,
        {"stageNames": STAGE_NAMES},
    )


def extract_records_from_block(block: str, comercial: str, extraction_date: str) -> list[dict]:
    stage = find_stage_name(block)
    if not stage:
        return []

    lines = [line.strip() for line in re.split(r"[\n\r]+", block) if line.strip()]
    merged_text = " | ".join(lines)

    price_match = re.search(
        r"(€\s*[\d.,]+|[\d.,]+\s*€|EUR\s*[\d.,]+|[\d.,]+\s*EUR)",
        merged_text,
        flags=re.I,
    )
    valor_texto = price_match.group(1).strip() if price_match else ""
    precio = limpiar_precio(valor_texto)

    quantity = None
    quantity_candidates = re.findall(r"(?<![\d.,])\d{1,3}(?:[.,]\d{3})*(?![\d.,])", merged_text)
    for candidate in quantity_candidates:
        parsed = parse_quantity(candidate)
        if parsed is None:
            continue
        if precio is not None and abs(parsed - precio) < 0.0001:
            continue
        quantity = parsed
        break

    return [
        build_record(
            comercial=comercial,
            etapa=stage,
            cantidad=quantity,
            valor_texto=valor_texto or block,
            precio=precio,
            extraction_date=extraction_date,
            origen="dom",
        )
    ]


def extract_records_from_body_text(body_text: str, comercial: str, extraction_date: str) -> list[dict]:
    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    records: list[dict] = []

    for index, line in enumerate(lines):
        stage = find_stage_name(line)
        if not stage:
            continue

        window = lines[index : index + 4]
        window_text = " | ".join(window)

        price_match = re.search(
            r"(€\s*[\d.,]+|[\d.,]+\s*€|EUR\s*[\d.,]+|[\d.,]+\s*EUR)",
            window_text,
            flags=re.I,
        )
        valor_texto = price_match.group(1).strip() if price_match else line
        precio = limpiar_precio(valor_texto)

        quantity = None
        for candidate in re.findall(r"(?<![\d.,])\d{1,3}(?:[.,]\d{3})*(?![\d.,])", window_text):
            parsed = parse_quantity(candidate)
            if parsed is None:
                continue
            if precio is not None and abs(parsed - precio) < 0.0001:
                continue
            quantity = parsed
            break

        records.append(
            build_record(
                comercial=comercial,
                etapa=stage,
                cantidad=quantity,
                valor_texto=valor_texto,
                precio=precio,
                extraction_date=extraction_date,
                origen="dom_body",
            )
        )

    return deduplicate_records(records)


def deduplicate_records(records: list[dict]) -> list[dict]:
    best_by_key: dict[tuple[str, str], dict] = {}

    for record in records:
        key = (record["comercial"], record["etapa"])
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
        1 if record.get("cantidad") is not None else 0,
        1 if record.get("precio") is not None else 0,
        len(str(record.get("valor_texto", ""))),
    )


def extract_stage_data_from_dom(page: Page, comercial: str, extraction_date: str) -> list[dict]:
    blocks = collect_stage_blocks(page)
    records: list[dict] = []

    for block in blocks:
        records.extend(extract_records_from_block(block, comercial, extraction_date))

    records = deduplicate_records(records)
    if records:
        log("Datos extraidos desde bloques visibles del DOM")
        return records

    body_text = page.locator("body").inner_text(timeout=10000)
    records = extract_records_from_body_text(body_text, comercial, extraction_date)
    if records:
        log("Datos extraidos desde texto visible del body")
    return records


def results_to_dataframe(records: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(records, columns=EMPTY_COLUMNS)


def save_results(records: list[dict]) -> Path:
    df = results_to_dataframe(records)
    df.to_excel(OUTPUT_FILE, index=False)
    log(f"Excel final generado: {OUTPUT_FILE}")
    return OUTPUT_FILE


def load_results_dataframe() -> pd.DataFrame:
    if not OUTPUT_FILE.exists():
        raise FileNotFoundError(f"No existe el Excel final: {OUTPUT_FILE}")
    return pd.read_excel(OUTPUT_FILE)


def run_extraction(headless: bool = False) -> Path:
    if not SESSION_FILE.exists():
        raise FileNotFoundError(
            f"No existe la sesion guardada en {SESSION_FILE}. Ejecuta primero guardar_sesion.py."
        )

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    extraction_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=str(SESSION_FILE), accept_downloads=True)
        page = context.new_page()

        try:
            page.goto(URL, wait_until="domcontentloaded")
            wait_for_dashboard_settle(page)
            ensure_stage_tab(page)

            for comercial in COMERCIALES:
                log(f"Procesando comercial: {comercial}")
                try:
                    select_only_comercial(page, comercial)
                    records = try_extract_download(page, comercial, extraction_date)
                    if not records:
                        records = extract_stage_data_from_dom(page, comercial, extraction_date)

                    if records:
                        results.extend(records)
                        log("Datos extraidos")
                    else:
                        log(f"Sin datos visibles para {comercial}")
                except Exception as exc:
                    log(f"Error procesando comercial {comercial}: {exc}")
                    continue
        finally:
            browser.close()

    deduped = deduplicate_records(results)
    if not deduped:
        log("No se extrajeron datos; se generara un Excel vacio con columnas.")
        return save_results([])

    return save_results(deduped)


def main() -> None:
    run_extraction(headless=False)


if __name__ == "__main__":
    main()
