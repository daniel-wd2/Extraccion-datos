import asyncio
import atexit
import msvcrt
import os
import re
import sys
import time
from pathlib import Path

import pandas as pd
from telegram import BotCommand, MenuButtonCommands, Update
from telegram.error import BadRequest, TimedOut
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from config_utils import load_dotenv
from extraer_etapa import (
    COMERCIALES,
    OUTPUT_FILE,
    SESSION_FILE,
    TEXT_OUTPUT_FILE,
    load_results_dataframe,
    run_extraction_with_retry,
)


ALLOWED_CHAT_IDS: set[str] = set()
BASE_DIR = Path(__file__).resolve().parent
LOCK_FILE = BASE_DIR / ".telegram_bot.lock"
FORECAST_DIR = BASE_DIR.parent / "ferniq_forecast_scraper"
FORECAST_OUTPUT_FILE = FORECAST_DIR / "resultado_forecast_ferniq.txt"
FORECAST_SESSION_FILE = FORECAST_DIR / "sesion_forecast.json"
FORECAST_ENV_FILE = FORECAST_DIR / ".env"
FORECAST_OVERRIDE_ENV_KEYS = {
    "FERNIQ_EMAIL",
    "FERNIQ_PASSWORD",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ALLOWED_CHAT_ID",
    "TELEGRAM_FORECAST_BOT_TOKEN",
    "TELEGRAM_FORECAST_ALLOWED_CHAT_ID",
    "USE_SYSTEM_CHROME_PROFILE",
    "CHROME_PROFILE_DIRECTORY",
    "CHROME_USER_DATA_DIR",
    "CHROME_EXECUTABLE",
    "BRAVE_EXECUTABLE",
}

RUN_LOCK = asyncio.Lock()
LOCK_HANDLE = None
CURRENT_JOB_LABEL = ""


def acquire_single_instance_lock() -> None:
    global LOCK_HANDLE

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = LOCK_FILE.open("a+")

    try:
        msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock_handle.close()
        raise RuntimeError(
            "Ya hay otra instancia del bot de Telegram ejecutandose en este equipo."
        )

    lock_handle.seek(0)
    lock_handle.truncate()
    lock_handle.write(str(os.getpid()))
    lock_handle.flush()
    LOCK_HANDLE = lock_handle


def release_single_instance_lock() -> None:
    global LOCK_HANDLE

    if LOCK_HANDLE is None:
        return

    try:
        LOCK_HANDLE.seek(0)
        LOCK_HANDLE.truncate()
        LOCK_HANDLE.flush()
        msvcrt.locking(LOCK_HANDLE.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        LOCK_HANDLE.close()
        LOCK_HANDLE = None


atexit.register(release_single_instance_lock)


def is_allowed_chat(chat_id: int) -> bool:
    if not ALLOWED_CHAT_IDS:
        return True
    return str(chat_id) in ALLOWED_CHAT_IDS


def format_money(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    integer_part, decimal_part = f"{number:,.2f}".split(".")
    integer_part = integer_part.replace(",", ".")
    return f"{integer_part},{decimal_part} €"


def format_quantity(value: object) -> str:
    if value is None or pd.isna(value):
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)

    if number.is_integer():
        return f"{int(number):,}".replace(",", ".")

    integer_part, decimal_part = f"{number:,.2f}".split(".")
    integer_part = integer_part.replace(",", ".")
    return f"{integer_part},{decimal_part}"


def build_summary_message(df: pd.DataFrame) -> str:
    if df.empty:
        return "No hay filas en el TXT generado."

    lines = ["Datos de Etapa por comercial:"]
    detected_comerciales = [
        value
        for value in df.get("comercial", pd.Series(dtype=str)).dropna().astype(str).tolist()
        if value.strip()
    ]
    ordered_comerciales = list(dict.fromkeys([*COMERCIALES, *detected_comerciales]))

    for comercial in ordered_comerciales:
        group = df[df["comercial"].astype(str) == comercial].copy()
        if group.empty:
            total_precio = 0.0
        else:
            group["precio"] = pd.to_numeric(group["precio"], errors="coerce")
            total_precio = group["precio"].sum(min_count=1)

        lines.append("")
        lines.append(f"{comercial}: total {format_money(total_precio)}")

        for _, row in group.sort_values(["etapa"], na_position="last").iterrows():
            etapa = row.get("etapa", "")
            cantidad = row.get("cantidad")
            precio = row.get("precio")

            cantidad_text = "-"
            if pd.notna(cantidad):
                cantidad_text = format_quantity(cantidad)

            lines.append(
                f"- {etapa}: cantidad {cantidad_text}, precio {format_money(precio)}"
            )

    return "\n".join(lines).strip()


async def ensure_authorized(update: Update) -> bool:
    chat = update.effective_chat
    if chat is None:
        return False
    if is_allowed_chat(chat.id):
        return True
    if update.message:
        await update.message.reply_text(
            "Este chat no esta autorizado para usar este bot. "
            f"Tu chat_id es: {chat.id}"
        )
    return False


async def send_stage_report(update: Update, report_path: Path) -> None:
    if not update.message:
        return
    with report_path.open("rb") as report_file:
        await update.message.reply_document(
            document=report_file,
            filename=report_path.name,
            caption="TXT generado por el scraper de Ferniq.",
            read_timeout=120,
            write_timeout=120,
            connect_timeout=30,
        )


async def send_report(update: Update, report_path: Path) -> None:
    if not update.message:
        return
    with report_path.open("rb") as report_file:
        await update.message.reply_document(
            document=report_file,
            filename=report_path.name,
            caption="Reporte de Forecast generado por Ferniq.",
            read_timeout=120,
            write_timeout=120,
            connect_timeout=30,
        )


def build_status_message() -> str:
    session_ok = "si" if SESSION_FILE.exists() else "no"
    stage_report_ok = "si" if TEXT_OUTPUT_FILE.exists() else "no"
    forecast_session_ok = "si" if FORECAST_SESSION_FILE.exists() else "no"
    forecast_report_ok = "si" if FORECAST_OUTPUT_FILE.exists() else "no"
    token_ok = "si" if os.getenv("TELEGRAM_BOT_TOKEN", "").strip() else "no"
    allowed_chat = ", ".join(sorted(ALLOWED_CHAT_IDS)) if ALLOWED_CHAT_IDS else "sin restriccion"

    return (
        "Estado del bot:\n"
        f"- sesion_ferniq.json: {session_ok}\n"
        f"- resultado_etapa_ferniq.txt: {stage_report_ok}\n"
        f"- sesion_forecast.json: {forecast_session_ok}\n"
        f"- resultado_forecast_ferniq.txt: {forecast_report_ok}\n"
        f"- token cargado: {token_ok}\n"
        f"- chat permitido: {allowed_chat}"
    )


def format_elapsed_seconds(started_at: float) -> str:
    elapsed = max(0, int(time.monotonic() - started_at))
    minutes, seconds = divmod(elapsed, 60)
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def build_forecast_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in FORECAST_OVERRIDE_ENV_KEYS:
        env.pop(key, None)
    return env


def parse_simple_env_file(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().lstrip("\ufeff")] = value.strip().strip('"').strip("'")
    return values


def get_forecast_missing_credentials() -> list[str]:
    env_values = parse_simple_env_file(FORECAST_ENV_FILE)
    email = env_values.get("FERNIQ_FORECAST_EMAIL") or env_values.get("FERNIQ_EMAIL") or ""
    password = env_values.get("FERNIQ_FORECAST_PASSWORD") or env_values.get("FERNIQ_PASSWORD") or ""

    missing: list[str] = []
    if not email.strip():
        missing.append("FERNIQ_FORECAST_EMAIL o FERNIQ_EMAIL")
    if not password.strip():
        missing.append("FERNIQ_FORECAST_PASSWORD o FERNIQ_PASSWORD")
    return missing


def summarize_error_message(exc: Exception, limit: int = 3000) -> str:
    text = str(exc).strip()
    if not text:
        return "Se produjo un error sin detalle."

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return text[:limit]

    runtime_lines = [line for line in lines if line.startswith("RuntimeError:")]
    if runtime_lines:
        selected_runtime = runtime_lines[-3:]
        cleaned_runtime = "\n".join(selected_runtime).strip()
        if len(cleaned_runtime) > limit:
            return cleaned_runtime[: limit - 3].rstrip() + "..."
        return cleaned_runtime

    selected: list[str] = []
    for line in lines:
        if line.startswith("Traceback"):
            continue
        if line.startswith("File "):
            continue
        if line.startswith("During handling of the above exception"):
            continue
        if line.startswith("return "):
            continue
        if line.startswith("comerciales ="):
            continue
        if line.startswith("open_"):
            continue
        if line.startswith("raise RuntimeError("):
            continue
        if line.startswith("~"):
            continue
        selected.append(line)
        if len("\n".join(selected)) >= limit:
            break

    cleaned = "\n".join(selected).strip() or text
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 3].rstrip() + "..."
    return cleaned


async def reply_error_text(update: Update, prefix: str, exc: Exception) -> None:
    if not update.message:
        return

    message = f"{prefix}: {summarize_error_message(exc)}"
    for chunk in split_message(message, limit=3000):
        try:
            await update.message.reply_text(
                chunk,
                read_timeout=60,
                write_timeout=60,
                connect_timeout=20,
            )
        except BadRequest:
            fallback = chunk[:2900].rstrip() + "..."
            await update.message.reply_text(
                fallback,
                read_timeout=60,
                write_timeout=60,
                connect_timeout=20,
            )
        except TimedOut:
            continue


async def safe_reply_text(message, text: str) -> None:
    try:
        await message.reply_text(
            text,
            read_timeout=60,
            write_timeout=60,
            connect_timeout=20,
        )
    except TimedOut:
        return


async def safe_edit_message(message, text: str) -> None:
    try:
        await message.edit_text(
            text,
            read_timeout=60,
            write_timeout=60,
            connect_timeout=20,
        )
    except BadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        raise
    except TimedOut:
        return


async def send_typing_heartbeat(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    stop_event: asyncio.Event,
    interval_seconds: float = 4.0,
) -> None:
    while not stop_event.is_set():
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)  # type: ignore[arg-type]
        except Exception:
            pass

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue


async def send_waiting_updates(
    message,
    base_text: str,
    stop_event: asyncio.Event,
    interval_seconds: float = 15.0,
) -> None:
    started_at = time.monotonic()
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            break
        except TimeoutError:
            elapsed = format_elapsed_seconds(started_at)
            await safe_edit_message(
                message,
                f"{base_text}\nSigo trabajando. Tiempo transcurrido: {elapsed}.",
            )


def filter_official_comerciales(comerciales: list[str]) -> list[str]:
    return [item.strip() for item in comerciales if item.strip()]


def extract_log_message(line: str) -> str:
    cleaned = line.strip()
    match = re.match(r"^\[\d{2}:\d{2}:\d{2}\]\s*(.+)$", cleaned)
    if match:
        return match.group(1).strip()
    return cleaned


def build_forecast_progress_text(progress: dict[str, object], started_at: float) -> str:
    lines = ["Forecast en marcha."]

    stage = str(progress.get("stage") or "").strip()
    if stage:
        lines.append(f"Estado: {stage}")

    total = progress.get("total")
    completed = int(progress.get("completed") or 0)
    if isinstance(total, int) and total > 0:
        lines.append(f"Comerciales completados: {completed}/{total}")

    workers = progress.get("workers")
    if isinstance(workers, int) and workers > 0:
        lines.append(f"Workers en paralelo: {workers}")

    last_detail = str(progress.get("last_detail") or "").strip()
    if last_detail:
        lines.append(f"Ultimo avance: {last_detail}")

    lines.append(f"Tiempo transcurrido: {format_elapsed_seconds(started_at)}")
    return "\n".join(lines)


def update_forecast_progress(progress: dict[str, object], raw_line: str) -> bool:
    line = extract_log_message(raw_line)
    if not line:
        return False

    progress["last_detail"] = line

    if line.startswith("Intentando extraccion en modo headless"):
        progress["stage"] = "Abriendo Forecast en modo rapido"
        return True
    if line.startswith("La URL directa de Forecast devuelve 404"):
        progress["stage"] = "Entrando a Forecast desde la home"
        return True
    if line.startswith("Intentando regenerar sesion automaticamente"):
        progress["stage"] = "Regenerando la sesion de Forecast"
        return True
    if line.startswith("Sesion regenerada automaticamente"):
        progress["stage"] = "Sesion regenerada. Reintentando"
        return True
    if line.startswith("Reintentando extraccion en modo visible"):
        progress["stage"] = "Reintentando en modo visible"
        return True

    if line.startswith("Comerciales detectados:"):
        raw_items = line.split(":", 1)[1].strip()
        comerciales = filter_official_comerciales([item.strip() for item in raw_items.split(",")])
        progress["total"] = len(comerciales)
        progress["stage"] = "Comerciales detectados"
        return True

    if line.startswith("Procesando Forecast con"):
        match = re.search(r"(\d+)", line)
        if match:
            progress["workers"] = int(match.group(1))
        progress["stage"] = "Sacando datos por comercial"
        return True

    if line.startswith("Datos extraidos para "):
        progress["completed"] = int(progress.get("completed") or 0) + 1
        progress["stage"] = "Sacando datos por comercial"
        return True

    if line.startswith("Sin datos visibles para "):
        progress["completed"] = int(progress.get("completed") or 0) + 1
        progress["stage"] = "Sacando datos por comercial"
        return True

    if line.startswith("Error procesando comercial "):
        progress["completed"] = int(progress.get("completed") or 0) + 1
        progress["stage"] = "Sacando datos por comercial"
        return True

    if line.startswith("No se extrajeron datos"):
        progress["stage"] = "Generando reporte vacio"
        return True

    if "resultado_forecast_ferniq" in line.lower():
        progress["stage"] = "Generando reporte final"
        return True

    return False


async def run_forecast_extraction_subprocess(
    on_output_line=None,
) -> Path:
    if not FORECAST_DIR.exists():
        raise FileNotFoundError(f"No existe la carpeta de forecast: {FORECAST_DIR}")
    if not FORECAST_ENV_FILE.exists():
        raise FileNotFoundError(f"No existe el archivo .env de forecast: {FORECAST_ENV_FILE}")

    command = [
        sys.executable,
        "-u",
        "-c",
        "from extraer_forecast import run_extraction_with_retry; run_extraction_with_retry()",
    ]
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(FORECAST_DIR),
        env=build_forecast_subprocess_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output_lines: list[str] = []

    assert process.stdout is not None
    while True:
        raw_line = await process.stdout.readline()
        if not raw_line:
            break
        decoded_line = raw_line.decode("utf-8", errors="replace").rstrip()
        if not decoded_line:
            continue
        output_lines.append(decoded_line)
        if on_output_line is not None:
            try:
                await on_output_line(decoded_line)
            except TimedOut:
                continue

    return_code = await process.wait()

    if return_code != 0:
        error_text = "\n".join(output_lines).strip()
        raise RuntimeError(error_text or "La extraccion de forecast termino con error.")

    if not FORECAST_OUTPUT_FILE.exists():
        raise FileNotFoundError(f"No existe el reporte final de forecast: {FORECAST_OUTPUT_FILE}")

    return FORECAST_OUTPUT_FILE


def load_forecast_results_text() -> str:
    if not FORECAST_OUTPUT_FILE.exists():
        raise FileNotFoundError(f"No existe el reporte final de forecast: {FORECAST_OUTPUT_FILE}")
    return FORECAST_OUTPUT_FILE.read_text(encoding="utf-8")


async def handle_sacar_datos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await ensure_authorized(update):
        return

    global CURRENT_JOB_LABEL
    if RUN_LOCK.locked():
        current_job = CURRENT_JOB_LABEL or "otro proceso"
        await update.message.reply_text(
            f"Ya estoy ejecutando {current_job}. Cuando termine te paso el resultado."
        )
        return

    async with RUN_LOCK:
        CURRENT_JOB_LABEL = "la extraccion de Etapa"
        try:
            status_message = await update.message.reply_text(
                "Voy a sacar los datos de todos los comerciales. Esto puede tardar un poco.",
                read_timeout=60,
                write_timeout=60,
                connect_timeout=20,
            )
            stop_event = asyncio.Event()
            typing_task = asyncio.create_task(
                send_typing_heartbeat(context, update.effective_chat.id, stop_event)  # type: ignore[arg-type]
            )
            waiting_task = asyncio.create_task(
                send_waiting_updates(
                    status_message,
                    "Voy a sacar los datos de todos los comerciales. Esto puede tardar un poco.",
                    stop_event,
                )
            )

            try:
                await asyncio.to_thread(run_extraction_with_retry)
                df = await asyncio.to_thread(load_results_dataframe)
            except Exception as exc:
                await reply_error_text(update, "Error al sacar datos", exc)
                return
            finally:
                stop_event.set()
                await asyncio.gather(typing_task, waiting_task, return_exceptions=True)

            summary = build_summary_message(df)
            await safe_edit_message(status_message, "Extraccion de Etapa completada. Te envio el resumen y el TXT.")

            for chunk in split_message(summary):
                await safe_reply_text(update.message, chunk)

            if not TEXT_OUTPUT_FILE.exists():
                raise FileNotFoundError(f"No existe el TXT final de Etapa: {TEXT_OUTPUT_FILE}")
            await send_stage_report(update, TEXT_OUTPUT_FILE)
        finally:
            CURRENT_JOB_LABEL = ""


async def handle_sacar_forecast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await ensure_authorized(update):
        return

    global CURRENT_JOB_LABEL
    if RUN_LOCK.locked():
        current_job = CURRENT_JOB_LABEL or "otro proceso"
        await update.message.reply_text(
            f"Ya estoy ejecutando {current_job}. Cuando termine te paso el resultado."
        )
        return

    async with RUN_LOCK:
        CURRENT_JOB_LABEL = "la extraccion de Forecast"
        try:
            missing_credentials = get_forecast_missing_credentials()
            if missing_credentials:
                await update.message.reply_text(
                    "Forecast no puede regenerar la sesion automaticamente porque faltan credenciales en "
                    f"{FORECAST_ENV_FILE.name}: {', '.join(missing_credentials)}.",
                    read_timeout=60,
                    write_timeout=60,
                    connect_timeout=20,
                )
                return

            started_at = time.monotonic()
            status_message = await update.message.reply_text(
                "Iniciando forecast. Te ire avisando del progreso.",
                read_timeout=60,
                write_timeout=60,
                connect_timeout=20,
            )
            progress: dict[str, object] = {
                "stage": "Preparando la extraccion",
                "completed": 0,
                "total": None,
                "workers": None,
                "last_detail": "",
            }
            last_status_text = build_forecast_progress_text(progress, started_at)
            await safe_edit_message(status_message, last_status_text)

            stop_event = asyncio.Event()
            typing_task = asyncio.create_task(
                send_typing_heartbeat(context, update.effective_chat.id, stop_event)  # type: ignore[arg-type]
            )
            last_sent_at = 0.0

            async def on_output_line(line: str) -> None:
                nonlocal last_status_text, last_sent_at

                changed = update_forecast_progress(progress, line)
                if not changed:
                    return

                new_text = build_forecast_progress_text(progress, started_at)
                now = time.monotonic()
                force = any(
                    marker in extract_log_message(line)
                    for marker in (
                        "Comerciales detectados:",
                        "Procesando Forecast con",
                        "Datos extraidos para ",
                        "Sin datos visibles para ",
                        "Error procesando comercial ",
                    )
                )
                if not force and now - last_sent_at < 2.5:
                    return
                if new_text == last_status_text:
                    return

                await safe_edit_message(status_message, new_text)
                last_status_text = new_text
                last_sent_at = now

            try:
                report_path = await run_forecast_extraction_subprocess(on_output_line=on_output_line)
                summary = await asyncio.to_thread(load_forecast_results_text)
            except Exception as exc:
                await reply_error_text(update, "Error al sacar forecast", exc)
                return
            finally:
                stop_event.set()
                await asyncio.gather(typing_task, return_exceptions=True)

            await safe_edit_message(
                status_message,
                "Forecast completado. Te envio el resumen y el reporte.",
            )

            for chunk in split_message(summary):
                await safe_reply_text(update.message, chunk)

            try:
                await send_report(update, report_path)
            except TimedOut:
                await safe_reply_text(
                    update.message,
                    "El forecast se genero bien, pero Telegram ha tardado demasiado al enviar el archivo. "
                    "Usa /ultimo_forecast y te lo vuelvo a mandar.",
                )
        finally:
            CURRENT_JOB_LABEL = ""


async def handle_estado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await ensure_authorized(update):
        return
    await update.message.reply_text(build_status_message())


async def handle_ultimo_txt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await ensure_authorized(update):
        return

    if not TEXT_OUTPUT_FILE.exists():
        await update.message.reply_text("Todavia no existe ningun TXT generado.")
        return

    await update.message.reply_text("Te envio el ultimo TXT generado.")
    await send_stage_report(update, TEXT_OUTPUT_FILE)


async def handle_ultimo_forecast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await ensure_authorized(update):
        return

    if not FORECAST_OUTPUT_FILE.exists():
        await update.message.reply_text("Todavia no existe ningun reporte de forecast generado.")
        return

    summary = load_forecast_results_text()
    for chunk in split_message(summary):
        await update.message.reply_text(chunk)

    await send_report(update, FORECAST_OUTPUT_FILE)


async def handle_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await ensure_authorized(update):
        return

    await update.message.reply_text(
        "Comandos disponibles:\n"
        "/start\n"
        "/sacar_datos\n"
        "/sacar_forecast\n"
        "/estado\n"
        "/estado_forecast\n"
        "/ultimo_txt\n"
        "/ultimo_forecast\n"
        "/ayuda\n\n"
        "Tambien puedes escribir:\n"
        "- sacar datos\n"
        "- sacar forecast\n"
        "- estado\n"
        "- estado forecast\n"
        "- ultimo txt\n"
        "- ultimo forecast"
    )


def split_message(text: str, limit: int = 3500) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = []
    current_len = 0

    for line in text.splitlines():
        extra = len(line) + 1
        if current and current_len + extra > limit:
            chunks.append("\n".join(current))
            current = [line]
            current_len = extra
        else:
            current.append(line)
            current_len += extra

    if current:
        chunks.append("\n".join(current))

    return chunks


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await ensure_authorized(update):
        return
    await update.message.reply_text(
        "Bot listo. Usa /ayuda para ver los comandos de Etapa y Forecast."
    )


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    normalized = update.message.text.strip().lower() # type: ignore
    if normalized in {"sacar datos", "/sacar_datos"}:
        await handle_sacar_datos(update, context)
        return
    if normalized in {"sacar forecast", "/sacar_forecast"}:
        await handle_sacar_forecast(update, context)
        return
    if normalized in {"estado", "/estado"}:
        await handle_estado(update, context)
        return
    if normalized in {"estado forecast", "/estado_forecast"}:
        await handle_estado(update, context)
        return
    if normalized in {"ultimo txt", "/ultimo_txt", "ultimo excel", "/ultimo_excel"}:
        await handle_ultimo_txt(update, context)
        return
    if normalized in {"ultimo forecast", "ultimo excel forecast", "/ultimo_forecast"}:
        await handle_ultimo_forecast(update, context)
        return

    if not await ensure_authorized(update):
        return

    await update.message.reply_text("No te he entendido. Usa /ayuda para ver los comandos.")


async def post_init(application: Application) -> None:
    commands = [
        BotCommand("start", "Abrir el bot"),
        BotCommand("sacar_datos", "Extraer datos de Etapa y generar el TXT"),
        BotCommand("sacar_forecast", "Extraer datos de Forecast"),
        BotCommand("estado", "Ver estado general del bot"),
        BotCommand("estado_forecast", "Ver estado general del bot"),
        BotCommand("ultimo_txt", "Recibir el ultimo TXT de Etapa"),
        BotCommand("ultimo_forecast", "Recibir el ultimo reporte de Forecast"),
        BotCommand("ayuda", "Ver ayuda y comandos disponibles"),
    ]
    await application.bot.set_my_commands(commands)
    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())


async def handle_application_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    if error is None:
        return
    print(f"Error controlado del bot: {summarize_error_message(error, limit=1200)}")


def main() -> None:
    load_dotenv()
    acquire_single_instance_lock()
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

    if not bot_token:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN en las variables de entorno.")

    global ALLOWED_CHAT_IDS
    raw_allowed_chat_ids = os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
    ALLOWED_CHAT_IDS = {
        chat_id.strip()
        for chat_id in raw_allowed_chat_ids.split(",")
        if chat_id.strip()
    }

    application = Application.builder().token(bot_token).post_init(post_init).build()
    application.add_error_handler(handle_application_error)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ayuda", handle_ayuda))
    application.add_handler(CommandHandler("sacar_datos", handle_sacar_datos))
    application.add_handler(CommandHandler("sacar_forecast", handle_sacar_forecast))
    application.add_handler(CommandHandler("estado", handle_estado))
    application.add_handler(CommandHandler("estado_forecast", handle_estado))
    application.add_handler(CommandHandler("ultimo_txt", handle_ultimo_txt))
    application.add_handler(CommandHandler("ultimo_excel", handle_ultimo_txt))
    application.add_handler(CommandHandler("ultimo_forecast", handle_ultimo_forecast))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    print("Bot de Telegram iniciado.")
    print(f"Sesion guardada disponible: {'si' if SESSION_FILE.exists() else 'no'}")
    print(f"Chat permitido: {', '.join(sorted(ALLOWED_CHAT_IDS)) if ALLOWED_CHAT_IDS else 'sin restriccion'}")
    print("Esperando mensajes en Telegram...")
    application.run_polling()


if __name__ == "__main__":
    main()
