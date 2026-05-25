import asyncio
import os
from pathlib import Path

import pandas as pd
from telegram import BotCommand, MenuButtonCommands, Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from config_utils import load_dotenv
from extraer_etapa import OUTPUT_FILE, SESSION_FILE, load_results_dataframe, run_extraction_with_retry


ALLOWED_CHAT_IDS: set[str] = set()
TRIGGER_TEXTS = {"sacar datos", "/sacar_datos", "estado", "ultimo excel"}

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


def build_status_message() -> str:
    session_ok = "si" if SESSION_FILE.exists() else "no"
    excel_ok = "si" if OUTPUT_FILE.exists() else "no"
    token_ok = "si" if os.getenv("TELEGRAM_BOT_TOKEN", "").strip() else "no"
    allowed_chat = ", ".join(sorted(ALLOWED_CHAT_IDS)) if ALLOWED_CHAT_IDS else "sin restriccion"

    return (
        "Estado del bot:\n"
        f"- sesion_ferniq.json: {session_ok}\n"
        f"- resultado_etapa_ferniq.xlsx: {excel_ok}\n"
        f"- token cargado: {token_ok}\n"
        f"- chat permitido: {allowed_chat}"
    )


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


async def handle_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await ensure_authorized(update):
        return

    await update.message.reply_text(
        "Comandos disponibles:\n"
        "/start\n"
        "/sacar_datos\n"
        "/estado\n"
        "/ultimo_excel\n"
        "/ayuda\n\n"
        "Tambien puedes escribir:\n"
        "- sacar datos\n"
        "- estado\n"
        "- ultimo excel"
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
        "Bot listo. Usa el menu de comandos de Telegram o escribe /ayuda."
    )


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    normalized = update.message.text.strip().lower() # type: ignore
    if normalized in {"sacar datos", "/sacar_datos"}:
        await handle_sacar_datos(update, context)
        return
    if normalized == "estado":
        await handle_estado(update, context)
        return
    if normalized == "ultimo excel":
        await handle_ultimo_excel(update, context)
        return

    if not await ensure_authorized(update):
        return

    await update.message.reply_text("No te he entendido. Usa /ayuda para ver los comandos.")


async def post_init(application: Application) -> None:
    commands = [
        BotCommand("start", "Abrir el bot"),
        BotCommand("sacar_datos", "Extraer datos y generar el Excel"),
        BotCommand("estado", "Ver estado del bot y de la sesion"),
        BotCommand("ultimo_excel", "Recibir el ultimo Excel generado"),
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
    application.add_handler(CommandHandler("estado", handle_estado))
    application.add_handler(CommandHandler("ultimo_excel", handle_ultimo_excel))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    print("Bot de Telegram iniciado.")
    print(f"Sesion guardada disponible: {'si' if SESSION_FILE.exists() else 'no'}")
    print(f"Chat permitido: {', '.join(sorted(ALLOWED_CHAT_IDS)) if ALLOWED_CHAT_IDS else 'sin restriccion'}")
    print("Esperando mensajes en Telegram...")
    application.run_polling()


if __name__ == "__main__":
    main()
