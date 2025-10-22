# file: forward_pair_webhook.py
import os
import json
import logging
import asyncio
from typing import Optional, Dict

from fastapi import FastAPI, Request, HTTPException
from telegram import Update
from telegram.ext import (
    Application, ApplicationBuilder,
    CommandHandler, MessageHandler, ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pair-bot")

# === ENV ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
APP_BASE_URL = os.getenv("APP_BASE_URL")  # напр. https://tg-forward-bot.onrender.com
WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", "use-long-random")
PORT = int(os.getenv("PORT", "10000"))

PAIRS_FILE = os.getenv("PAIRS_FILE", "pairs.json")  # куда сохраняем пары (chat_id -> partner_id)

if not BOT_TOKEN or not APP_BASE_URL:
    raise SystemExit("Set BOT_TOKEN and APP_BASE_URL env vars!")

# === STORAGE (pairs) ===
_pairs_lock = asyncio.Lock()
_pairs: Dict[str, int] = {}  # "chat_id" -> partner_id (int)

def _ensure_dir(path: str):
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)

async def load_pairs():
    """Загружаем пары из JSON на старте."""
    global _pairs
    if not os.path.exists(PAIRS_FILE):
        _pairs = {}
        return
    try:
        async with _pairs_lock:
            with open(PAIRS_FILE, "r", encoding="utf-8") as f:
                _pairs = {k: int(v) for k, v in json.load(f).items()}
    except Exception as e:
        log.exception("Failed to load pairs: %s", e)
        _pairs = {}

async def save_pairs():
    """Сохраняем пары в JSON после изменений."""
    try:
        async with _pairs_lock:
            _ensure_dir(PAIRS_FILE)
            with open(PAIRS_FILE, "w", encoding="utf-8") as f:
                json.dump(_pairs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.exception("Failed to save pairs: %s", e)

async def get_partner(chat_id: int) -> Optional[int]:
    return _pairs.get(str(chat_id))

async def set_pair(a: int, b: int):
    _pairs[str(a)] = b
    _pairs[str(b)] = a
    await save_pairs()

async def unlink(a: int):
    b = _pairs.pop(str(a), None)
    if b is not None:
        _pairs.pop(str(b), None)
    await save_pairs()
    return b

# === FASTAPI + PTB ===
app = FastAPI()
tg_app: Application = ApplicationBuilder().token(BOT_TOKEN).build()

HELP_TEXT = (
    "Я связываю два аккаунта и пересылаю сообщения между ними.\n\n"
    "Твой ID: <code>{cid}</code>\n"
    "Команды:\n"
    "/myid — показать твой ID\n"
    "/link &lt;ID&gt; — связать с другим аккаунтом (вставь его ID)\n"
    "/unlink — разорвать связь\n\n"
    "Пример: <code>/link 123456789</code>\n"
    "Подсказка: отправь этот ID второму аккаунту, он сделает /link {cid} — и вы будете связаны.\n"
)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    partner = await get_partner(cid)
    msg = "👋 Привет! Я бот-«пересылатель».\n\n" + HELP_TEXT.format(cid=cid)
    if partner:
        msg += f"\n✅ Уже связан с: <code>{partner}</code>\nНапиши любое сообщение — я отправлю его партнёру."
    else:
        # ВАЖНО: угловые скобки экранированы, чтобы не ломать HTML
        msg += "\nСвязи пока нет. Сделай /link &lt;ID&gt; — и поехали."
    await update.message.reply_html(msg)

async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    await update.message.reply_html(f"Твой ID: <code>{cid}</code>")

async def link_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Укажи ID: /link 123456789")
        return
    try:
        other = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом. Пример: /link 123456789")
        return
    if other == cid:
        await update.message.reply_text("Нельзя связать самого себя 🙂")
        return

    # Устанавливаем связь в обе стороны (предыдущие связи перезаписываются)
    await set_pair(cid, other)

    # Уведомляем вторую сторону (если бот уже в ЛС у неё)
    try:
        await context.bot.send_message(
            other,
            f"🔗 Вас связали с аккаунтом <code>{cid}</code>.\n"
            f"Теперь сообщения будут пересылаться автоматически.",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await update.message.reply_html(
        f"Готово! 🔗 Связал с <code>{other}</code>.\n"
        "Напиши любое сообщение — я отправлю его партнёру.\n"
        "Чтобы отменить: /unlink"
    )

async def unlink_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    other = await unlink(cid)
    if other:
        try:
            await context.bot.send_message(other, "❌ Связь разорвана партнёром.")
        except Exception:
            pass
        await update.message.reply_text("Связь разорвана.")
    else:
        await update.message.reply_text("Связи не было.")

async def relay_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat

    # Только личные чаты; игнорируем ботов (включая нас самих)
    if chat.type != "private":
        return
    if msg.from_user and msg.from_user.is_bot:
        return

    partner = await get_partner(chat.id)
    if not partner:
        return

    try:
        # copy_message сохраняет тип контента и форматирование
        await context.bot.copy_message(
            chat_id=partner,
            from_chat_id=chat.id,
            message_id=msg.message_id,
            protect_content=False
        )
    except Exception as e:
        log.exception("Forward error: %s", e)

# === PTB handlers ===
tg_app.add_handler(CommandHandler("start", start_cmd))
tg_app.add_handler(CommandHandler("myid", myid_cmd))
tg_app.add_handler(CommandHandler("link", link_cmd))
tg_app.add_handler(CommandHandler("unlink", unlink_cmd))
tg_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, relay_messages))

# === lifecycle ===
@app.on_event("startup")
async def on_startup():
    await load_pairs()
    url = APP_BASE_URL.rstrip("/") + f"/webhook/{WEBHOOK_SECRET_TOKEN}"
    await tg_app.bot.set_webhook(
        url=url,
        secret_token=WEBHOOK_SECRET_TOKEN,
        allowed_updates=["message", "edited_message"],
        drop_pending_updates=True
    )
    await tg_app.initialize()
    await tg_app.start()
    log.info("Webhook set to %s", url)

@app.on_event("shutdown")
async def on_shutdown():
    await tg_app.stop()
    await tg_app.shutdown()

@app.post(f"/webhook/{WEBHOOK_SECRET_TOKEN}")
async def telegram_webhook(req: Request):
    # Проверяем секрет Telegram → наш WEBHOOK_SECRET_TOKEN
    if req.headers.get("x-telegram-bot-api-secret-token") != WEBHOOK_SECRET_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid secret token")
    data = await req.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


