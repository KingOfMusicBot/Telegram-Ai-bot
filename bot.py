# ============================
#   FINAL PATCHED bot.py
#   (Groq + MongoDB + Heroku Safe)
# ============================

import logging
import os
import asyncio
from time import time
from datetime import datetime
from signal import SIGTERM, SIGINT

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from openai import OpenAI
import motor.motor_asyncio

# ---------------------------
# LOAD ENV
# ---------------------------
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB", "telegram_bot")
CHANNEL_URL = os.getenv("CHANNEL_URL", "")
SUPPORT_URL = os.getenv("SUPPORT_URL", "")
START_PIC_URL = os.getenv("START_PIC_URL", "")
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "5"))

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN missing")
if not OPENAI_API_KEY:
    raise ValueError("GROQ API KEY missing")
if not MONGO_URI:
    raise ValueError("MONGO_URI missing")

# ---------------------------
# LOGGER
# ---------------------------
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------
# GROQ CLIENT
# ---------------------------
client = OpenAI(api_key=OPENAI_API_KEY, base_url="https://api.groq.com/openai/v1")

# ---------------------------
# RATE LIMITER
# ---------------------------
def check_spam(uid, context):
    now = time()
    data = context.application.bot_data.setdefault("rate_limits", {})
    last = data.get(uid, 0)
    if now - last < COOLDOWN_SECONDS:
        return COOLDOWN_SECONDS - (now - last)
    data[uid] = now
    return 0

# ---------------------------
# MONGODB CLIENT (GLOBAL)
# ---------------------------
mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = mongo_client[MONGO_DB]

# ---------------------------
# AI HANDLER
# ---------------------------
async def ask_ai(prompt, mode="default"):
    modes = {
        "notes": "Short clean notes.",
        "explain": "Explain simple Hinglish.",
        "mcq": "Make 5 MCQs + answer key.",
        "summary": "Summarize short bullet points.",
        "solve": "Solve step by step.",
        "quiz": "Make quiz 5 questions.",
        "current": "Current affairs Q&A.",
        "default": "Helpful AI assistant."
    }

    try:
        out = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": modes.get(mode, modes["default"])},
                {"role": "user", "content": prompt},
            ],
            max_tokens=700
        )
        return out.choices[0].message.content.strip()
    except Exception as e:
        logger.error("AI error: %s", e)
        return "AI error. Try later."

# ---------------------------
# REGISTRATION (MongoDB)
# ---------------------------
async def register_chat(chat, user):
    now = datetime.utcnow()

    if chat.type == "private":
        await db.users.update_one(
            {"user_id": chat.id},
            {"$set": {"username": user.username, "last_seen": now}, "$setOnInsert": {"first_seen": now}},
            upsert=True
        )
    else:
        await db.groups.update_one(
            {"chat_id": chat.id},
            {"$set": {"type": chat.type, "last_seen": now}, "$setOnInsert": {"first_seen": now}},
            upsert=True
        )

# ---------------------------
# HELP TEXT
# ---------------------------
def help_text():
    return (
        "≋ HELP ≋\n\n"
        "/notes <topic>\n"
        "/explain <topic>\n"
        "/mcq <topic>\n"
        "/summary <text/reply>\n"
        "/solve <question>\n"
        "/quiz <topic>\n"
        "/currentaffairs\n\n"
        "OWNER:\n"
        "/stats\n"
        "/broadcast <msg>"
    )

# ---------------------------
# START COMMAND
# ---------------------------
async def start(update, context):
    chat = update.effective_chat
    user = update.effective_user
    await register_chat(chat, user)

    msg = (
        "Hey! 👋 Main ek AI study bot hoon.\n"
        "Private me direct question bhejo.\n"
        "Group me mention karke pucho."
    )

    btn = [
        [InlineKeyboardButton("✚ ADD ME IN GROUP ✚", url=f"https://t.me/{context.bot.username}?startgroup=true")],
        [InlineKeyboardButton("≋ HELP ≋", callback_data="help")],
        [
            InlineKeyboardButton("OWNER", url=f"tg://user?id={OWNER_ID}"),
            InlineKeyboardButton("CHANNEL", url=CHANNEL_URL),
        ],
        [InlineKeyboardButton("SUPPORT", url=SUPPORT_URL)],
    ]

    if START_PIC_URL:
        await chat.send_photo(START_PIC_URL, caption=msg, reply_markup=InlineKeyboardMarkup(btn))
    else:
        await chat.send_message(msg, reply_markup=InlineKeyboardMarkup(btn))

# ---------------------------
# CALLBACK BUTTONS
# ---------------------------
async def help_btn(update, context):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text(help_text())

# ---------------------------
# STUDY COMMANDS
# ---------------------------
async def notes(update, context):
    t = " ".join(context.args)
    if not t: return await update.message.reply_text("Use: /notes <topic>")
    out = await ask_ai(t, "notes")
    await update.message.reply_text(out)

async def explain(update, context):
    t = " ".join(context.args)
    if not t: return await update.message.reply_text("Use: /explain <topic>")
    out = await ask_ai(t, "explain")
    await update.message.reply_text(out)

async def mcq(update, context):
    t = " ".join(context.args)
    if not t: return await update.message.reply_text("Use: /mcq <topic>")
    out = await ask_ai(t, "mcq")
    await update.message.reply_text(out)

async def summary(update, context):
    if update.message.reply_to_message:
        text = update.message.reply_to_message.text
    else:
        text = " ".join(context.args)
    if not text:
        return await update.message.reply_text("Reply or /summary <text>")
    out = await ask_ai(text, "summary")
    await update.message.reply_text(out)

async def solve(update, context):
    t = " ".join(context.args)
    if not t: return await update.message.reply_text("Use: /solve <question>")
    out = await ask_ai(t, "solve")
    await update.message.reply_text(out)

async def quiz(update, context):
    t = " ".join(context.args)
    if not t: return await update.message.reply_text("Use: /quiz <topic>")
    out = await ask_ai(t, "quiz")
    await update.message.reply_text(out)

async def current(update, context):
    out = await ask_ai("current affairs", "current")
    await update.message.reply_text(out)

# ---------------------------
# OWNER COMMANDS
# ---------------------------
async def stats(update, context):
    if update.effective_user.id != OWNER_ID:
        return
    u = await db.users.count_documents({})
    g = await db.groups.count_documents({})
    await update.message.reply_text(f"Users: {u}\nGroups: {g}")

async def broadcast(update, context):
    if update.effective_user.id != OWNER_ID:
        return
    text = " ".join(context.args)
    if not text:
        return await update.message.reply_text("Use: /broadcast <msg>")

    cursor = db.users.find({})
    sent = 0
    async for u in cursor:
        try:
            await context.bot.send_message(u["user_id"], text)
            sent += 1
            await asyncio.sleep(0.03)
        except:
            pass

    await update.message.reply_text(f"Broadcast sent to {sent} users!")

# ---------------------------
# NORMAL MESSAGE
# ---------------------------
async def msg_handler(update, context):
    msg = update.message
    chat = update.effective_chat
    user = update.effective_user
    text = msg.text

    await register_chat(chat, user)

    # GROUP: respond only on mention or reply
    if chat.type in ("group", "supergroup"):
        bot = context.bot.username.lower()
        mention = f"@{bot}" in text.lower()
        reply_bot = msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id
        if not (mention or reply_bot):
            return

    # anti spam
    left = check_spam(user.id, context)
    if left > 0:
        return await msg.reply_text(f"Slow down… {int(left)} sec wait.")

    out = await ask_ai(text, "default")
    await msg.reply_text(out)

# ---------------------------
# ERROR HANDLER
# ---------------------------
async def err(update, context):
    logger.error(context.error)

# ---------------------------
# MAIN (NO ASYNCIO.RUN)
# ---------------------------
def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", lambda u, c: u.message.reply_text(help_text())))

    app.add_handler(CommandHandler("notes", notes))
    app.add_handler(CommandHandler("explain", explain))
    app.add_handler(CommandHandler("mcq", mcq))
    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(CommandHandler("solve", solve))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("currentaffairs", current))

    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("broadcast", broadcast))

    app.add_handler(CallbackQueryHandler(help_btn, pattern="help"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_handler))

    app.add_error_handler(err)

    logger.info("BOT STARTED…")
    app.run_polling()


if __name__ == "__main__":
    main()
