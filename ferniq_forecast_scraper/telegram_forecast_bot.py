import asyncio
import os
import sys
from pathlib import Path

# Garantizar que el directorio del script esté en el sys.path para importaciones locales
sys.path.insert(0, str(Path(__file__).resolve().parent))

from telegram import BotCommand, MenuButtonCommands, Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from config_utils import load_dotenv
from extraer_forecast import OUTPUT_FILE, SESSION_FILE, load_results_text, run_extraction_with_retry


ALLOWED_CHAT_IDS: set[str] = set()
TRIGGER_TEXTS = {
    "sacar forecast",
    "/sacar_forecast",
    "estado forecast",
    "ultimo forecast",
    "ultimo excel forecast"
}

RUN_LOCK = asyncio.Lock()


def is_allowed_chat(chat_id: int) -> bool:
    if not ALLOWED_CHAT_IDS:
        return True
    return str(chat_id) in ALLOWED_CHAT_IDS


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
    report_ok = "si" if OUTPUT_FILE.exists() else "no"
    token_ok = "si" if (os.getenv("TELEGRAM_FORECAST_BOT_TOKEN", "").strip() or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()) else "no"
    allowed_chat = ", ".join(sorted(ALLOWED_CHAT_IDS)) if ALLOWED_CHAT_IDS else "sin restriccion"

    return (
        "Estado del bot de Forecast:\n"
        f"- sesion_forecast.json: {session_ok}\n"
        f"- resultado_forecast_ferniq.txt: {report_ok}\n"
        f"- token cargado: {token_ok}\n"
        f"- chat permitido: {allowed_chat}"
    )


async def handle_sacar_forecast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await ensure_authorized(update):
        return

    async with RUN_LOCK:
        await update.message.reply_text(
            "Voy a sacar los datos de forecast de todos los comerciales. Esto se hara lo mas rapido posible."
        )
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING) # type: ignore

        try:
            report_path = await asyncio.to_thread(run_extraction_with_retry)
            summary = await asyncio.to_thread(load_results_text)
        except Exception as exc:
            await update.message.reply_text(f"Error al sacar datos de forecast: {exc}")
            return

        for chunk in split_message(summary):
            await update.message.reply_text(chunk)

        await send_report(update, report_path)


async def handle_estado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await ensure_authorized(update):
        return
    await update.message.reply_text(build_status_message())


async def handle_ultimo_reporte(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await ensure_authorized(update):
        return

    if not OUTPUT_FILE.exists():
        await update.message.reply_text("Todavia no existe ningun reporte de forecast generado.")
        return

    summary = load_results_text()
    for chunk in split_message(summary):
         await update.message.reply_text(chunk)
         
    await send_report(update, OUTPUT_FILE)


async def handle_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await ensure_authorized(update):
        return

    await update.message.reply_text(
        "Comandos disponibles para Forecast:\n"
        "/start\n"
        "/sacar_forecast\n"
        "/estado_forecast\n"
        "/ultimo_forecast\n"
        "/ayuda_forecast\n\n"
        "Tambien puedes escribir:\n"
        "- sacar forecast\n"
        "- estado forecast\n"
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
        "Bot de Forecast listo. Usa el menu de comandos de Telegram o escribe /ayuda_forecast."
    )


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    normalized = update.message.text.strip().lower() # type: ignore
    if normalized in {"sacar forecast", "/sacar_forecast"}:
        await handle_sacar_forecast(update, context)
        return
    if normalized in {"estado forecast", "/estado_forecast"}:
        await handle_estado(update, context)
        return
    if normalized in {"ultimo forecast", "ultimo excel forecast", "ultimo excel", "/ultimo_forecast"}:
        await handle_ultimo_reporte(update, context)
        return
    if normalized in {"ayuda forecast", "/ayuda_forecast"}:
        await handle_ayuda(update, context)
        return

    if not await ensure_authorized(update):
        return

    await update.message.reply_text("No te he entendido. Usa /ayuda_forecast para ver los comandos.")


async def post_init(application: Application) -> None:
    commands = [
        BotCommand("start", "Abrir el bot"),
        BotCommand("sacar_forecast", "Extraer datos de forecast lo mas rapido posible"),
        BotCommand("estado_forecast", "Ver estado del bot y de la sesion"),
        BotCommand("ultimo_forecast", "Recibir el ultimo reporte de forecast"),
        BotCommand("ayuda_forecast", "Ver ayuda y comandos disponibles"),
    ]
    await application.bot.set_my_commands(commands)
    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())


def main() -> None:
    load_dotenv()
    bot_token = os.getenv("TELEGRAM_FORECAST_BOT_TOKEN", "").strip()
    if not bot_token:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

    if not bot_token:
        raise RuntimeError("Falta TELEGRAM_FORECAST_BOT_TOKEN o TELEGRAM_BOT_TOKEN en las variables de entorno.")

    global ALLOWED_CHAT_IDS
    raw_allowed_chat_ids = os.getenv("TELEGRAM_FORECAST_ALLOWED_CHAT_ID", "").strip()
    if not raw_allowed_chat_ids:
        raw_allowed_chat_ids = os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "").strip()

    ALLOWED_CHAT_IDS = {
        chat_id.strip()
        for chat_id in raw_allowed_chat_ids.split(",")
        if chat_id.strip()
    }

    application = Application.builder().token(bot_token).post_init(post_init).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ayuda_forecast", handle_ayuda))
    application.add_handler(CommandHandler("sacar_forecast", handle_sacar_forecast))
    application.add_handler(CommandHandler("estado_forecast", handle_estado))
    application.add_handler(CommandHandler("ultimo_forecast", handle_ultimo_reporte))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    print("Bot de Telegram de Forecast iniciado.")
    print(f"Sesion guardada disponible: {'si' if SESSION_FILE.exists() else 'no'}")
    print(f"Chat permitido: {', '.join(sorted(ALLOWED_CHAT_IDS)) if ALLOWED_CHAT_IDS else 'sin restriccion'}")
    print("Esperando mensajes en Telegram...")
    application.run_polling()


if __name__ == "__main__":
    main()
