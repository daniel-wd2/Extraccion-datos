import asyncio
import os
from pathlib import Path

import pandas as pd
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from config_utils import load_dotenv
from extraer_etapa import load_results_dataframe, run_extraction


ALLOWED_CHAT_ID = ""
TRIGGER_TEXTS = {"sacar datos", "/sacar_datos"}

RUN_LOCK = asyncio.Lock()


def is_allowed_chat(chat_id: int) -> bool:
    if not ALLOWED_CHAT_ID:
        return True
    return str(chat_id) == ALLOWED_CHAT_ID


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
        await update.message.reply_text("Este chat no esta autorizado para usar este bot.")
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


async def handle_sacar_datos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await ensure_authorized(update):
        return

    async with RUN_LOCK:
        await update.message.reply_text(
            "Voy a sacar los datos de todos los comerciales. Esto puede tardar un poco."
        )
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        try:
            excel_path = await asyncio.to_thread(run_extraction, True)
            df = await asyncio.to_thread(load_results_dataframe)
        except Exception as exc:
            await update.message.reply_text(f"Error al sacar datos: {exc}")
            return

        summary = build_summary_message(df)

        for chunk in split_message(summary):
            await update.message.reply_text(chunk)

        await send_excel(update, excel_path)


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
        "Bot listo. Escribe 'sacar datos' para ejecutar el scraper y recibir el Excel."
    )


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    normalized = update.message.text.strip().lower()
    if normalized in TRIGGER_TEXTS:
        await handle_sacar_datos(update, context)
        return

    if not await ensure_authorized(update):
        return

    await update.message.reply_text("No te he entendido. Prueba con 'sacar datos'.")


def main() -> None:
    load_dotenv()
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

    if not bot_token:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN en las variables de entorno.")

    global ALLOWED_CHAT_ID
    ALLOWED_CHAT_ID = os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "").strip()

    application = Application.builder().token(bot_token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("sacar_datos", handle_sacar_datos))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    application.run_polling()


if __name__ == "__main__":
    main()
