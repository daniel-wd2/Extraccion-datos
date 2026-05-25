import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
from telegram import BotCommand, MenuButtonCommands, Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from config_utils import load_dotenv
from extraer_etapa import OUTPUT_FILE, SESSION_FILE, load_results_dataframe, run_extraction_with_retry


ALLOWED_CHAT_IDS: set[str] = set()
BASE_DIR = Path(__file__).resolve().parent
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
    formatted = f"{number:,.2f}"
    return f"EUR {formatted}"


def build_summary_message(df: pd.DataFrame) -> str:
    if df.empty:
        return "No hay filas en el Excel generado."

    lines = ["Datos de Etapa por comercial:"]

    for comercial, group in df.groupby("comercial", dropna=False):
        group = group.copy()
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
                try:
                    cantidad_text = str(int(cantidad))
                except (TypeError, ValueError):
                    cantidad_text = str(cantidad)

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


async def send_excel(update: Update, excel_path: Path) -> None:
    if not update.message:
        return
    with excel_path.open("rb") as excel_file:
        await update.message.reply_document(
            document=excel_file,
            filename=excel_path.name,
            caption="Excel generado por el scraper de Ferniq.",
        )


async def send_report(update: Update, report_path: Path) -> None:
    if not update.message:
        return
    with report_path.open("rb") as report_file:
        await update.message.reply_document(
            document=report_file,
            filename=report_path.name,
            caption="Reporte de Forecast generado por Ferniq.",
        )


def build_status_message() -> str:
    session_ok = "si" if SESSION_FILE.exists() else "no"
    excel_ok = "si" if OUTPUT_FILE.exists() else "no"
    forecast_session_ok = "si" if FORECAST_SESSION_FILE.exists() else "no"
    forecast_report_ok = "si" if FORECAST_OUTPUT_FILE.exists() else "no"
    token_ok = "si" if os.getenv("TELEGRAM_BOT_TOKEN", "").strip() else "no"
    allowed_chat = ", ".join(sorted(ALLOWED_CHAT_IDS)) if ALLOWED_CHAT_IDS else "sin restriccion"

    return (
        "Estado del bot:\n"
        f"- sesion_ferniq.json: {session_ok}\n"
        f"- resultado_etapa_ferniq.xlsx: {excel_ok}\n"
        f"- sesion_forecast.json: {forecast_session_ok}\n"
        f"- resultado_forecast_ferniq.txt: {forecast_report_ok}\n"
        f"- token cargado: {token_ok}\n"
        f"- chat permitido: {allowed_chat}"
    )


def build_forecast_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in FORECAST_OVERRIDE_ENV_KEYS:
        env.pop(key, None)
    return env


def run_forecast_extraction_subprocess() -> Path:
    if not FORECAST_DIR.exists():
        raise FileNotFoundError(f"No existe la carpeta de forecast: {FORECAST_DIR}")
    if not FORECAST_ENV_FILE.exists():
        raise FileNotFoundError(f"No existe el archivo .env de forecast: {FORECAST_ENV_FILE}")

    command = [
        sys.executable,
        "-c",
        "from extraer_forecast import run_extraction_with_retry; run_extraction_with_retry()",
    ]
    completed = subprocess.run(
        command,
        cwd=str(FORECAST_DIR),
        env=build_forecast_subprocess_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    if completed.returncode != 0:
        output_parts = [completed.stderr.strip(), completed.stdout.strip()]
        error_text = "\n".join(part for part in output_parts if part).strip()
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

    async with RUN_LOCK:
        await update.message.reply_text(
            "Voy a sacar los datos de todos los comerciales. Esto puede tardar un poco."
        )
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING) # type: ignore

        try:
            excel_path = await asyncio.to_thread(run_extraction_with_retry)
            df = await asyncio.to_thread(load_results_dataframe)
        except Exception as exc:
            await update.message.reply_text(f"Error al sacar datos: {exc}")
            return

        summary = build_summary_message(df)

        for chunk in split_message(summary):
            await update.message.reply_text(chunk)

        await send_excel(update, excel_path)


async def handle_sacar_forecast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await ensure_authorized(update):
        return

    async with RUN_LOCK:
        await update.message.reply_text(
            "Voy a sacar los datos de forecast de todos los comerciales. Esto puede tardar un poco."
        )
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING) # type: ignore

        try:
            report_path = await asyncio.to_thread(run_forecast_extraction_subprocess)
            summary = await asyncio.to_thread(load_forecast_results_text)
        except Exception as exc:
            await update.message.reply_text(f"Error al sacar forecast: {exc}")
            return

        for chunk in split_message(summary):
            await update.message.reply_text(chunk)

        await send_report(update, report_path)


async def handle_estado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await ensure_authorized(update):
        return
    await update.message.reply_text(build_status_message())


async def handle_ultimo_excel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await ensure_authorized(update):
        return

    if not OUTPUT_FILE.exists():
        await update.message.reply_text("Todavia no existe ningun Excel generado.")
        return

    await update.message.reply_text("Te envio el ultimo Excel generado.")
    await send_excel(update, OUTPUT_FILE)


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
        "/ultimo_excel\n"
        "/ultimo_forecast\n"
        "/ayuda\n\n"
        "Tambien puedes escribir:\n"
        "- sacar datos\n"
        "- sacar forecast\n"
        "- estado\n"
        "- estado forecast\n"
        "- ultimo excel\n"
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
    if normalized in {"ultimo excel", "/ultimo_excel"}:
        await handle_ultimo_excel(update, context)
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
        BotCommand("sacar_datos", "Extraer datos de Etapa y generar el Excel"),
        BotCommand("sacar_forecast", "Extraer datos de Forecast"),
        BotCommand("estado", "Ver estado general del bot"),
        BotCommand("estado_forecast", "Ver estado general del bot"),
        BotCommand("ultimo_excel", "Recibir el ultimo Excel de Etapa"),
        BotCommand("ultimo_forecast", "Recibir el ultimo reporte de Forecast"),
        BotCommand("ayuda", "Ver ayuda y comandos disponibles"),
    ]
    await application.bot.set_my_commands(commands)
    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())


def main() -> None:
    load_dotenv()
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
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ayuda", handle_ayuda))
    application.add_handler(CommandHandler("sacar_datos", handle_sacar_datos))
    application.add_handler(CommandHandler("sacar_forecast", handle_sacar_forecast))
    application.add_handler(CommandHandler("estado", handle_estado))
    application.add_handler(CommandHandler("estado_forecast", handle_estado))
    application.add_handler(CommandHandler("ultimo_excel", handle_ultimo_excel))
    application.add_handler(CommandHandler("ultimo_forecast", handle_ultimo_forecast))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    print("Bot de Telegram iniciado.")
    print(f"Sesion guardada disponible: {'si' if SESSION_FILE.exists() else 'no'}")
    print(f"Chat permitido: {', '.join(sorted(ALLOWED_CHAT_IDS)) if ALLOWED_CHAT_IDS else 'sin restriccion'}")
    print("Esperando mensajes en Telegram...")
    application.run_polling()


if __name__ == "__main__":
    main()
