import logging
import os
import re
from urllib.parse import urlparse

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("mediabot-controller")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MEDIA_ENGINE_URL = os.getenv("MEDIA_ENGINE_URL", "").strip().rstrip("/")
CALL_ENGINE_SECRET = os.getenv("CALL_ENGINE_SECRET", "").strip()
OWNER_ID = os.getenv("OWNER_ID", "").strip()
PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "telegram").strip().strip("/")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")


def valid_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname) and len(value) <= 4096
    except Exception:
        return False


def authorized(update: Update) -> bool:
    if not OWNER_ID:
        return True
    user = update.effective_user
    return bool(user and str(user.id) == OWNER_ID)


def controls():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏸ إيقاف مؤقت", callback_data="pause"),
            InlineKeyboardButton("▶️ استئناف", callback_data="resume"),
        ],
        [InlineKeyboardButton("⏹ إيقاف التشغيل", callback_data="stop")],
    ])


async def call_engine(endpoint: str, payload: dict) -> tuple[bool, str]:
    url = f"{MEDIA_ENGINE_URL}/{endpoint}"
    headers = {"Authorization": f"Bearer {CALL_ENGINE_SECRET}"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10)) as client:
            response = await client.post(url, json=payload, headers=headers)
        if response.is_success:
            return True, "ok"
        try:
            body = response.json()
            return False, str(body.get("detail", f"Engine HTTP {response.status_code}"))
        except Exception:
            return False, f"محرك الوسائط أعاد الخطأ HTTP {response.status_code}."
    except httpx.HTTPError:
        log.exception("Cannot reach media engine")
        return False, "تعذر الاتصال بمحرك الوسائط. تحقق من MEDIA_ENGINE_URL وحالة الخدمة في Render."


def get_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        candidate = context.args[0].strip()
        if valid_url(candidate):
            return candidate
    msg = update.effective_message
    replied = msg.reply_to_message if msg else None
    if replied:
        for text in (replied.text, replied.caption):
            if text:
                match = re.search(r"https?://\S+", text)
                if match:
                    candidate = match.group(0).rstrip(".,!?)]}>\"'")
                    if valid_url(candidate):
                        return candidate
    return None


async def deny(update: Update):
    if update.effective_message:
        await update.effective_message.reply_text("هذا البوت مقيّد بمالك محدد.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return await deny(update)
    await update.effective_message.reply_text(
        "🎵 Mediabot Controller\n\n"
        "/play URL — تشغيل الصوت\n"
        "/video URL — تشغيل الفيديو\n"
        "/pause — إيقاف مؤقت\n"
        "/resume — استئناف\n"
        "/stop — إيقاف التشغيل ومغادرة المكالمة\n"
        "/help — المساعدة\n\n"
        "يمكنك أيضًا الرد على رسالة تحتوي على رابط ثم إرسال /play أو /video.",
        reply_markup=controls(),
    )


async def play_common(update: Update, context: ContextTypes.DEFAULT_TYPE, video: bool):
    if not authorized(update):
        return await deny(update)
    msg, chat = update.effective_message, update.effective_chat
    if not msg or not chat:
        return
    url = get_url(update, context)
    if not url:
        await msg.reply_text(
            "أرسل الرابط مع الأمر، مثل:\n/play https://example.com/audio.mp3\n\n"
            "أو ردّ على رسالة فيها رابط ثم أرسل /play أو /video."
        )
        return
    await msg.reply_text("⏳ جارٍ إرسال طلب التشغيل إلى المحرك...")
    ok, detail = await call_engine("play", {"chat_id": chat.id, "url": url, "video": video})
    if ok:
        mode = "الفيديو" if video else "الصوت"
        await msg.reply_text(
            f"✅ أُرسل طلب تشغيل {mode}. إذا لم يبدأ، تأكد من وجود مكالمة صوتية نشطة "
            "وأن حساب المحرك يستطيع الوصول إلى المجموعة.",
            reply_markup=controls(),
        )
    else:
        await msg.reply_text(f"❌ فشل طلب التشغيل:\n{detail}")


async def play_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await play_common(update, context, False)


async def video_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await play_common(update, context, True)


async def action(update: Update, endpoint: str):
    if not authorized(update):
        return await deny(update)
    msg, chat = update.effective_message, update.effective_chat
    if not msg or not chat:
        return
    ok, detail = await call_engine(endpoint, {"chat_id": chat.id})
    if not ok:
        await msg.reply_text(f"❌ تعذر تنفيذ الأمر:\n{detail}")
        return
    text = {
        "pause": "⏸ تم إرسال أمر الإيقاف المؤقت.",
        "resume": "▶️ تم إرسال أمر الاستئناف.",
        "stop": "⏹ تم إرسال أمر الإيقاف ومغادرة المكالمة.",
    }[endpoint]
    await msg.reply_text(text, reply_markup=controls())


async def pause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await action(update, "pause")


async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await action(update, "resume")


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await action(update, "stop")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    if not authorized(update):
        await query.answer("غير مسموح.", show_alert=True)
        return
    await query.answer()
    if query.data not in {"pause", "resume", "stop"}:
        return
    chat = update.effective_chat
    if not chat:
        await query.message.reply_text("تعذر تحديد المجموعة.")
        return
    ok, detail = await call_engine(query.data, {"chat_id": chat.id})
    if not ok:
        await query.message.reply_text(f"❌ تعذر تنفيذ الأمر:\n{detail}")
        return
    texts = {
        "pause": "⏸ تم إرسال أمر الإيقاف المؤقت.",
        "resume": "▶️ تم إرسال أمر الاستئناف.",
        "stop": "⏹ تم إرسال أمر الإيقاف ومغادرة المكالمة.",
    }
    await query.message.reply_text(texts[query.data], reply_markup=controls())


def main():
    missing = [name for name, value in {
        "BOT_TOKEN": BOT_TOKEN,
        "MEDIA_ENGINE_URL": MEDIA_ENGINE_URL,
        "CALL_ENGINE_SECRET": CALL_ENGINE_SECRET,
        "RENDER_EXTERNAL_URL": RENDER_EXTERNAL_URL,
    }.items() if not value]
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))
    parsed = urlparse(MEDIA_ENGINE_URL)
    if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
        raise RuntimeError("MEDIA_ENGINE_URL must use HTTPS.")
    webhook_url = f"{RENDER_EXTERNAL_URL}/{WEBHOOK_PATH}"

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("play", play_cmd))
    app.add_handler(CommandHandler("video", video_cmd))
    app.add_handler(CommandHandler("pause", pause_cmd))
    app.add_handler(CommandHandler("resume", resume_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CallbackQueryHandler(button_callback))
    log.info("Starting Telegram webhook at %s", webhook_url)
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH,
        webhook_url=webhook_url,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
