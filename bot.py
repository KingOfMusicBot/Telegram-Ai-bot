# bot.py  -- Groq + Telegram bot + MongoDB persistence (motor)
import logging
import os
import asyncio
from time import time
from signal import SIGTERM, SIGINT
from datetime import datetime

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
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
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # Groq API Key
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
CHANNEL_URL = os.getenv("CHANNEL_URL", "")
SUPPORT_URL = os.getenv("SUPPORT_URL", "")
START_PIC_URL = os.getenv("START_PIC_URL", "")
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "5"))
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB", "telegram_bot")

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN missing")
if not OPENAI_API_KEY:
    raise ValueError("GROQ API KEY missing")
if not MONGO_URI:
    raise ValueError("MONGO_URI missing - set your MongoDB connection string in env")

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
# GLOBAL DB HOLDER (set in main)
# ---------------------------
# We'll store DB client in app.bot_data['db']
# ---------------------------

# ---------------------------
# RATE LIMITER (in-memory)
# ---------------------------
def check_spam(user_id, context: ContextTypes.DEFAULT_TYPE):
    now = time()
    limits = context.application.bot_data.setdefault("rate_limits", {})
    last = limits.get(user_id, 0)
    diff = now - last
    if diff < COOLDOWN_SECONDS:
        return COOLDOWN_SECONDS - diff
    limits[user_id] = now
    return 0

# ---------------------------
# AI FUNCTION
# ---------------------------
async def ask_ai(prompt, mode="default"):
    modes = {
        "notes": "Short, clean notes banao.",
        "explain": "Topic ko Hinglish me simple explain karo.",
        "mcq": "Topic par 5 MCQ banao options A-D ke saath, end me answer key.",
        "summary": "Given text ko short bullet summary me convert karo.",
        "solve": "Math ya logical question ko step-by-step solve karo.",
        "quiz": "Topic par 5 question quiz banao.",
        "current": "Latest current affairs Q&A banao.",
        "default": "Tum ek helpful AI ho, concise answer do."
    }
    try:
        res = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": modes.get(mode, modes["default"])},
                {"role": "user", "content": prompt},
            ],
            max_tokens=700,
        )
        return res.choices[0].message.content.strip()
    except Exception as e:
        logger.error("AI error: %s", e)
        return "AI error aa gaya. Thodi der baad try karo."

# ---------------------------
# HELP TEXT
# ---------------------------
def help_text():
    return (
        "≋ HELP & COMMANDS ≋\n\n"
        "📌 Private me direct question bhejo.\n"
        "📌 Group me @mention karke pucho (ya bot ke message ko reply karo).\n\n"
        "STUDY COMMANDS:\n"
        "/notes <topic>\n"
        "/explain <topic>\n"
        "/mcq <topic>\n"
        "/summary <text or reply>\n"
        "/solve <question>\n"
        "/quiz <topic>\n"
        "/currentaffairs\n\n"
        "OWNER: /stats /broadcast"
    )

# ---------------------------
# MONGODB HELPERS (async)
# ---------------------------
async def init_mongo(app):
    """Initialize motor client and ensure indexes. Save db to app.bot_data['db']"""
    uri = MONGO_URI
    client_motor = motor.motor_asyncio.AsyncIOMotorClient(uri)
    db = client_motor[MONGO_DB]

    # ensure simple indexes
    try:
        await db.users.create_index("user_id", unique=True)
        await db.groups.create_index("chat_id", unique=True)
    except Exception as e:
        logger.warning("Index create warning: %s", e)

    app.bot_data["db"] = db
    logger.info("MongoDB initialized")

async def register_chat(chat_id: int, chat_type: str, context: ContextTypes.DEFAULT_TYPE, user_obj=None):
    """
    Store chat info into MongoDB:
    - users collection stores user_id, username, first_seen
    - groups collection stores chat_id, title, type, first_seen
    """
    db = context.application.bot_data.get("db")
    if not db:
        # fallback to in-memory sets if DB not ready
        data = context.application.bot_data
        if chat_type == "private":
            data.setdefault("users", set()).add(chat_id)
        else:
            data.setdefault("groups", set()).add(chat_id)
        return

    now = datetime.utcnow()
    if chat_type == "private":
        doc = {
            "user_id": int(chat_id),
            "username": user_obj.username if user_obj and getattr(user_obj, "username", None) else None,
            "first_seen": now,
            "last_seen": now,
        }
        # upsert
        await db.users.update_one({"user_id": int(chat_id)}, {"$set": {"last_seen": now, "username": doc["username"]}, "$setOnInsert": {"first_seen": now}}, upsert=True)
    elif chat_type in ("group", "supergroup"):
        # chat title may be in user_obj (not necessary) - we will attempt to fetch from context
        chat = context.application.bot_data.get("_last_chat_cache", {})
        # upsert group
        await db.groups.update_one({"chat_id": int(chat_id)}, {"$set": {"last_seen": now, "type": chat_type}, "$setOnInsert": {"first_seen": now}}, upsert=True)

# ---------------------------
# START COMMAND
# ---------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    # register in DB
    await register_chat(chat.id, chat.type, context, user_obj=user)

    text = (
        "Hey! 👋\nMain ek AI study bot hoon. Notes, explain, MCQ, quiz, summary, maths solve.\n"
        "Private me direct message karo. Group me bot ko @mention karke pucho ya uske message ko reply karo.\n"
    )
    bot_username = context.bot.username or "this_bot"

    buttons = [
        [InlineKeyboardButton("✚ ADD ME IN GROUP ✚", url=f"https://t.me/{bot_username}?startgroup=true")],
        [InlineKeyboardButton("≋ HELP & COMMANDS ≋", callback_data="help_menu")],
        [InlineKeyboardButton("≋ OWNER ≋", url=f"tg://user?id={OWNER_ID}"), InlineKeyboardButton("≋ CHANNEL ≋", url=CHANNEL_URL or "https://t.me/")],
        [InlineKeyboardButton("≋ SUPPORT ≋", url=SUPPORT_URL or "https://t.me/")],
        [InlineKeyboardButton("🧠 QUIZ GUIDE", callback_data="quiz_info"), InlineKeyboardButton("🛠 AI TOOLS", callback_data="tools_info")],
    ]

    if START_PIC_URL:
        await chat.send_photo(photo=START_PIC_URL, caption=text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await chat.send_message(text, reply_markup=InlineKeyboardMarkup(buttons))

# ---------------------------
# CALLBACKS
# ---------------------------
async def help_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text(help_text())

async def tools_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("Tools:\n/notes\n/explain\n/mcq\n/summary\n/solve\n/quiz\n/currentaffairs")

async def quiz_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("Use: /quiz <topic>")

# ---------------------------
# STUDY COMMANDS
# ---------------------------
async def notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args).strip()
    if not topic:
        return await update.message.reply_text("Use: /notes <topic>")
    await update.message.reply_chat_action(ChatAction.TYPING)
    res = await ask_ai(f"Notes on: {topic}", mode="notes")
    await update.message.reply_text(res)

async def explain_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args).strip()
    if not topic:
        return await update.message.reply_text("Use: /explain <topic>")
    res = await ask_ai(topic, mode="explain")
    await update.message.reply_text(res)

async def mcq_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args).strip()
    if not topic:
        return await update.message.reply_text("Use: /mcq <topic>")
    res = await ask_ai(topic, mode="mcq")
    await update.message.reply_text(res)

async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message and update.message.reply_to_message.text:
        text = update.message.reply_to_message.text
    else:
        text = " ".join(context.args).strip()
    if not text:
        return await update.message.reply_text("Reply to a message or use: /summary <text>")
    res = await ask_ai(text, mode="summary")
    await update.message.reply_text(res)

async def solve_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = " ".join(context.args).strip()
    if not q:
        return await update.message.reply_text("Use: /solve <question>")
    res = await ask_ai(q, mode="solve")
    await update.message.reply_text(res)

async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args).strip()
    if not topic:
        return await update.message.reply_text("Use: /quiz <topic>")
    res = await ask_ai(topic, mode="quiz")
    await update.message.reply_text(res)

async def current_affairs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    res = await ask_ai("current affairs", mode="current")
    await update.message.reply_text(res)

# ---------------------------
# OWNER COMMANDS (DB-backed)
# ---------------------------
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    db = context.application.bot_data.get("db")
    if not db:
        return await update.message.reply_text("DB not ready")
    users_count = await db.users.count_documents({})
    groups_count = await db.groups.count_documents({})
    await update.message.reply_text(f"Users: {users_count}\nGroups: {groups_count}")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    db = context.application.bot_data.get("db")
    if not db:
        return await update.message.reply_text("DB not ready")
    text = " ".join(context.args).strip()
    if not text and update.message.reply_to_message:
        text = update.message.reply_to_message.text
    if not text:
        return await update.message.reply_text("Use: /broadcast <message>")
    cursor = db.users.find({}, {"user_id": 1})
    sent = 0; failed = 0
    async for u in cursor:
        uid = u.get("user_id")
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
            await asyncio.sleep(0.03)
        except Exception as e:
            logger.error("Broadcast to %s failed: %s", uid, e)
            failed += 1
    await update.message.reply_text(f"Broadcast finished. Sent: {sent}, Failed: {failed}")

# ---------------------------
# MESSAGE HANDLER (registers to DB)
# ---------------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    chat = update.effective_chat
    user = update.effective_user
    text = msg.text or ""

    # register chat in DB
    try:
        await register_chat(chat.id, chat.type, context, user_obj=user)
    except Exception as e:
        logger.warning("register_chat failed: %s", e)

    # group: only reply when mentioned or reply-to-bot
    if chat.type in ("group", "supergroup"):
        bot_name = context.bot.username or ""
        is_mention = bot_name and f"@{bot_name.lower()}" in text.lower()
        reply_to_bot = msg.reply_to_message and msg.reply_to_message.from_user and msg.reply_to_message.from_user.id == context.bot.id
        if not (is_mention or reply_to_bot):
            return
        if is_mention and text.strip().lower() in {f"@{bot_name.lower()}", f"/ai@{bot_name.lower()}"}:
            await msg.reply_text("Mujhe mention ke saath apna question bhi likho. 🙂")
            return

    # spam
    if user:
        left = check_spam(user.id, context)
        if left > 0:
            return await msg.reply_text(f"Thoda dheere pucho. {int(left)} sec baad try karo.")

    await msg.reply_chat_action(ChatAction.TYPING)
    reply = await ask_ai(text, mode="default")
    await msg.reply_text(reply)

# ---------------------------
# ERROR HANDLER
# ---------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Exception while handling update: %s", context.error)

# ---------------------------
# GRACEFUL SHUTDOWN HANDLER
# ---------------------------
def _setup_signal_handlers(app):
    loop = asyncio.get_event_loop()
    for sig in (SIGTERM, SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(app.shutdown()))
        except NotImplementedError:
            pass

# ---------------------------
# MAIN
# ---------------------------
def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # initialize mongo BEFORE starting handlers so it's available to handlers
    # use app.post_init pattern: but ApplicationBuilder doesn't expose post_init easily,
    # so we'll init right here synchronously via asyncio.run on coroutine before starting polling.
    # However we need the app object for storing db; create task in event loop after build.
    async def _init_and_start():
        await init_mongo(app)

        # register handlers AFTER db init & function defs
        app.add_handler(CommandHandler("start", start_command))
        app.add_handler(CommandHandler("help", lambda u,c: u.message.reply_text(help_text())))

        app.add_handler(CommandHandler("notes", notes_command))
        app.add_handler(CommandHandler("explain", explain_command))
        app.add_handler(CommandHandler("mcq", mcq_command))
        app.add_handler(CommandHandler("summary", summary_command))
        app.add_handler(CommandHandler("solve", solve_command))
        app.add_handler(CommandHandler("quiz", quiz_command))
        app.add_handler(CommandHandler("currentaffairs", current_affairs_command))

        app.add_handler(CommandHandler("stats", stats_command))
        app.add_handler(CommandHandler("broadcast", broadcast_command))

        app.add_handler(CallbackQueryHandler(help_button, pattern="help_menu"))
        app.add_handler(CallbackQueryHandler(tools_button, pattern="tools_info"))
        app.add_handler(CallbackQueryHandler(quiz_button, pattern="quiz_info"))

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.add_error_handler(error_handler)

        _setup_signal_handlers(app)
        logger.info("BOT STARTING...")
        app.run_polling()

    # run the async init + start
    asyncio.run(_init_and_start())

if __name__ == "__main__":
    main()
