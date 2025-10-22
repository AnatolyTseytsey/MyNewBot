import os
import logging
from typing import Optional
from fastapi import FastAPI, Request, HTTPException
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("forward-bot-webhook")

BOT_TOKEN = os.getenv("BOT_TOKEN")
TARGET_CHAT_ID = int(os.getenv("TARGET_CHAT_ID", "0"))
SOURCE_CHAT_ID: Optional[int] = int(os.getenv("SOURCE_CHAT_ID")) if os.getenv("SOURCE_CHAT_ID") else None
WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", "use-long-random")
APP_BASE_URL = os.getenv("APP_BASE_URL")  # например, https://tg-forward-bot.onrender.com
PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET_TOKEN}"

if not BOT_TOKEN or not TARGET_CHAT_ID or not APP_BASE_URL:
    raise SystemExit("Set BOT_TOKEN, TARGET_CHAT_ID, APP_BASE_URL env vars!")

app = FastAPI()
tg_app: Application = ApplicationBuilder().token(BOT_TOKEN).build()

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я пересылаю сообщения.\n"
        f"Назначение: {TARGET_CHAT_ID}\n"
        + (f"Источник ограничен: {SOURCE_CHAT_ID}" if SOURCE_CHAT_ID else "Источник: любой")
    )

async def forward_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    if SOURCE_CHAT_ID and chat.id != SOURCE_CHAT_ID:
        return
    if chat.id == TARGET_CHAT_ID:
        return
    await context.bot.copy_message(
        chat_id=TARGET_CHAT_ID,
        from_chat_id=chat.id,
        message_id=msg.message_id,
        protect_content=False
    )

tg_app.add_handler(CommandHandler("start", start_cmd))
tg_app.add_handler(MessageHandler(filters.ALL, forward_all))

@app.on_event("startup")
async def on_startup():
    url = APP_BASE_URL.rstrip("/") + WEBHOOK_PATH
    await tg_app.bot.set_webhook(
        url=url,
        secret_token=WEBHOOK_SECRET_TOKEN,
        allowed_updates=["message","edited_message","channel_post","edited_channel_post"],
        drop_pending_updates=True
    )
    await tg_app.initialize()
    await tg_app.start()
    log.info("Webhook set to %s", url)

@app.on_event("shutdown")
async def on_shutdown():
    await tg_app.stop()
    await tg_app.shutdown()

@app.post(WEBHOOK_PATH)
async def telegram_webhook(req: Request):
    if req.headers.get("x-telegram-bot-api-secret-token") != WEBHOOK_SECRET_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid secret token")
    data = await req.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
