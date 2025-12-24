# bot.py — FULL restored, MongoDB + Groq + Heroku-safe (event-loop fix)
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

# ----------------- CONFIG -----------------
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # Groq key
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
    raise ValueError("OPENAI_API_KEY (Groq) missing")
if not MONGO_URI:
    raise ValueError("MONGO_URI missing")

# ----------------- LOGGING -----------------
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------- GROQ CLIENT -----------------
client = OpenAI(api_key=OPENAI_API_KEY, base_url="https://api.groq.com/openai/v1")

# ----------------- MONGO CLIENT (global) -----------------
mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = mongo_client[MONGO_DB]

# ----------------- UTILITIES -----------------
def check_spam(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> float:
    now_ts = time()
    rate_limits = context.application.bot_data.setdefault("rate_limits", {})
    last = rate_limits.get(user_id, 0)
    diff = now_ts - last
    if diff < COOLDOWN_SECONDS:
        return COOLDOWN_SECONDS - diff
    rate_limits[user_id] = now_ts
    return 0.0

async def ask_ai(prompt: str, mode: str = "default") -> str:
    system_map = {
        "notes": "You are a teacher. Produce concise, bulleted notes for students.",
        "explain": "You are a friendly teacher. Explain the concept in simple Hinglish with examples.",
        "mcq": "Generate 5 MCQs for the topic. Provide options A-D and an Answer Key at the end.",
        "summary": "Summarize the text into concise bullet points for students.",
        "solve": "Solve the math/logic problem step-by-step and show final answer.",
        "quiz": "Create a 5-question quiz (mix of MCQ/short). Provide answer key.",
        "current": "Create practice-style current affairs Q&A for students.",
        "default": "You are a helpful AI assistant that answers clearly in Hinglish.",
    }
    system = system_map.get(mode, system_map["default"])
    try:
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=800,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.exception("Groq API error")
        return "AI side pe error aa gaya. Thodi der baad try karo."

# ----------------- DB helpers -----------------
async def register_chat(chat_id: int, chat_type: str, context: ContextTypes.DEFAULT_TYPE, user_obj=None):
    """
    Upsert user or group into MongoDB so data persists across restarts.
    """
    now = datetime.utcnow()
    try:
        if chat_type == "private":
            await db.users.update_one(
                {"user_id": int(chat_id)},
                {"$set": {"last_seen": now, "username": getattr(user_obj, "username", None)},
                 "$setOnInsert": {"first_seen": now}},
                upsert=True,
            )
        else:
            await db.groups.update_one(
                {"chat_id": int(chat_id)},
                {"$set": {"last_seen": now, "type": chat_type},
                 "$setOnInsert": {"first_seen": now}},
                upsert=True,
            )
    except Exception as e:
        logger.warning("DB register failed: %s", e)
        # fallback: keep in-memory sets
        data = context.application.bot_data
        if chat_type == "private":
            data.setdefault("users", set()).add(chat_id)
        else:
            data.setdefault("groups", set()).add(chat_id)

async def create_indexes(app):
    """Optional: create indexes on startup in background."""
    try:
        await db.users.create_index("user_id", unique=True)
        await db.groups.create_index("chat_id", unique=True)
        logger.info("MongoDB indexes ensured")
    except Exception as e:
        logger.warning("Index creation failed: %s", e)

# ----------------- HELP TEXT & UI -----------------
def get_help_text() -> str:
    return (
        "≋ Help & Commands ≋\n\n"
        "Private: direct question bhejo.\n"
        "Group: mention the bot (@BotUsername) or reply to bot's message.\n\n"
        "Study Commands:\n"
        "/notes <topic>\n"
        "/explain <topic>\n"
        "/mcq <topic>\n"
        "/summary <text or reply>\n"
        "/solve <question>\n"
        "/quiz <topic>\n"
        "/currentaffairs\n\n"
        "Owner only: /stats /broadcast"
    )

# ----------------- COMMAND HANDLERS -----------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    await register_chat(chat.id, chat.type, context, user_obj=user)
    welcome = (
        "Hey! 👋\nMain AI Study Bot hoon. Doubts poochho, notes lo, MCQs, summaries aur quizzes.\n"
        "Private me direct bhejo. Group me mention ya reply karo."
    )
    bot_username = context.bot.username or "bot"
    keyboard = [
        [InlineKeyboardButton("✚ ADD ME IN YOUR GROUP ✚", url=f"https://t.me/{bot_username}?startgroup=true")],
        [InlineKeyboardButton("≋ HELP AND COMMANDS ≋", callback_data="help_menu")],
        [
            InlineKeyboardButton("≋ OWNER ≋", url=f"tg://user?id={OWNER_ID}"),
            InlineKeyboardButton("≋ CHANNEL ≋", url=CHANNEL_URL or "https://t.me/"),
        ],
        [InlineKeyboardButton("≋ SUPPORT ≋", url=SUPPORT_URL or "https://t.me/")],
        [InlineKeyboardButton("🧠 QUIZ", callback_data="quiz_info"), InlineKeyboardButton("🛠 AI TOOLS", callback_data="tools_info")],
    ]
    if START_PIC_URL:
        await chat.send_photo(photo=START_PIC_URL, caption=welcome, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await chat.send_message(text=welcome, reply_markup=InlineKeyboardMarkup(keyboard))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_help_text())

# Callback handlers for buttons
async def help_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text(get_help_text())

async def tools_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    text = (
        "🛠 AI Tools Available:\n\n"
        "• /notes <topic>\n"
        "• /explain <topic>\n"
        "• /mcq <topic>\n"
        "• /summary <text ya replied msg>\n"
        "• /solve <question>\n"
        "• /quiz <topic>\n"
        "• /currentaffairs\n"
    )
    await q.message.reply_text(text)

async def quiz_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    text = (
        "🧠 Quiz Mode:\n\n"
        "Command: /quiz <topic>\n"
        "Example:\n"
        "• /quiz Class 10 Physics\n"
        "• /quiz Python basics\n"
        "Bot 5 questions + answer key dega."
                     )
    await q.message.reply_text(text)

# Study commands (all async)
async def notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; user = update.effective_user
    await register_chat(chat.id, chat.type, context, user_obj=user)
    topic = " ".join(context.args).strip()
    if not topic:
        return await update.message.reply_text("Usage: /notes <topic>")
    remaining = check_spam(user.id, context)
    if remaining > 0:
        return await update.message.reply_text(f"Thoda dheere pucho, {int(remaining)} sec baad try karo.")
    await chat.send_action(ChatAction.TYPING)
    reply = await ask_ai(f"Topic: {topic}\nCreate short, structured notes for a student.", mode="notes")
    await update.message.reply_text(reply)

async def explain_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; user = update.effective_user
    await register_chat(chat.id, chat.type, context, user_obj=user)
    topic = " ".join(context.args).strip()
    if not topic:
        return await update.message.reply_text("Usage: /explain <topic>")
    remaining = check_spam(user.id, context)
    if remaining > 0:
        return await update.message.reply_text(f"Thoda dheere pucho, {int(remaining)} sec baad try karo.")
    await chat.send_action(ChatAction.TYPING)
    reply = await ask_ai(f"Explain for a student: {topic}", mode="explain")
    await update.message.reply_text(reply)

async def mcq_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; user = update.effective_user
    await register_chat(chat.id, chat.type, context, user_obj=user)
    topic = " ".join(context.args).strip()
    if not topic:
        return await update.message.reply_text("Usage: /mcq <topic>")
    remaining = check_spam(user.id, context)
    if remaining > 0:
        return await update.message.reply_text(f"Thoda dheere pucho, {int(remaining)} sec baad try karo.")
    await chat.send_action(ChatAction.TYPING)
    reply = await ask_ai(f"Make 5 MCQs for: {topic}", mode="mcq")
    await update.message.reply_text(reply)

async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; user = update.effective_user
    await register_chat(chat.id, chat.type, context, user_obj=user)
    text = " ".join(context.args).strip()
    if not text and update.message.reply_to_message:
        text = update.message.reply_to_message.text
    if not text:
        return await update.message.reply_text("Usage: /summary <text> (or reply to a message with /summary)")
    remaining = check_spam(user.id, context)
    if remaining > 0:
        return await update.message.reply_text(f"Thoda dheere pucho, {int(remaining)} sec baad try karo.")
    await chat.send_action(ChatAction.TYPING)
    reply = await ask_ai(f"Summarize this: {text}", mode="summary")
    await update.message.reply_text(reply)

async def solve_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; user = update.effective_user
    await register_chat(chat.id, chat.type, context, user_obj=user)
    question = " ".join(context.args).strip()
    if not question:
        return await update.message.reply_text("Usage: /solve <math or logic question>")
    remaining = check_spam(user.id, context)
    if remaining > 0:
        return await update.message.reply_text(f"Thoda dheere pucho, {int(remaining)} sec baad try karo.")
    await chat.send_action(ChatAction.TYPING)
    reply = await ask_ai(f"Solve step-by-step: {question}", mode="solve")
    await update.message.reply_text(reply)

async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; user = update.effective_user
    await register_chat(chat.id, chat.type, context, user_obj=user)
    topic = " ".join(context.args).strip() or "general knowledge"
    remaining = check_spam(user.id, context)
    if remaining > 0:
        return await update.message.reply_text(f"Thoda dheere pucho, {int(remaining)} sec baad try karo.")
    await chat.send_action(ChatAction.TYPING)
    reply = await ask_ai(f"Create a 5-question quiz: {topic}", mode="quiz")
    await update.message.reply_text(reply)

async def current_affairs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; user = update.effective_user
    await register_chat(chat.id, chat.type, context, user_obj=user)
    remaining = check_spam(user.id, context)
    if remaining > 0:
        return await update.message.reply_text(f"Thoda dheere pucho, {int(remaining)} sec baad try karo.")
    await chat.send_action(ChatAction.TYPING)
    reply = await ask_ai("Create practice-style current affairs Q&A.", mode="current")
    await update.message.reply_text(reply)

# ----------------- OWNER COMMANDS -----------------
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    try:
        users_count = await db.users.count_documents({})
        groups_count = await db.groups.count_documents({})
        await update.message.reply_text(f"📊 Bot Stats:\n• Total private users: {users_count}\n• Total groups: {groups_count}")
    except Exception as e:
        logger.error("Stats failed: %s", e)
        await update.message.reply_text("DB error while fetching stats.")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    text = " ".join(context.args).strip()
    if not text and update.message.reply_to_message:
        text = update.message.reply_to_message.text
    if not text:
        return await update.message.reply_text("Usage: /broadcast <message> (or reply to a message and /broadcast)")
    sent = 0; failed = 0
    try:
        cursor = db.users.find({}, {"user_id": 1})
        async for u in cursor:
            uid = u.get("user_id")
            try:
                await context.bot.send_message(chat_id=uid, text=text)
                sent += 1
                await asyncio.sleep(0.03)
            except Exception:
                failed += 1
        await update.message.reply_text(f"Broadcast complete. Sent: {sent}, Failed: {failed}")
    except Exception as e:
        logger.error("Broadcast error: %s", e)
        await update.message.reply_text("DB error during broadcast.")

# ----------------- MESSAGE HANDLER -----------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return
    text = message.text.strip()
    chat = update.effective_chat
    user = update.effective_user

    # Register chat
    await register_chat(chat.id, chat.type, context, user_obj=user)

    # Group logic: only respond if mention or reply to bot
    if chat.type in ("group", "supergroup"):
        bot_username = context.bot.username or ""
        mentioned = bot_username and f"@{bot_username.lower()}" in text.lower()
        reply_to_bot = (
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == context.bot.id
        )
        if not (mentioned or reply_to_bot):
            return
        if mentioned and text.lower().strip() in {f"@{bot_username.lower()}", f"/ai@{bot_username.lower()}"}:
            await message.reply_text("Mujhe mention ke saath apna question bhi likho. 🙂")
            return

    # Rate limit
    if user:
        left = check_spam(user.id, context)
        if left > 0:
            return await message.reply_text(f"Thoda dheere pucho, {int(left)} sec baad try karo.")

    await message.chat.send_action(ChatAction.TYPING)
    reply = await ask_ai(text, mode="default")
    await message.reply_text(reply)

# ----------------- ERROR HANDLER -----------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Exception while handling update: %s", context.error)

# ----------------- Graceful shutdown helper -----------------
def _setup_signal_handlers(app):
    loop = asyncio.get_event_loop()
    for sig in (SIGTERM, SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(app.shutdown()))
        except Exception:
            pass

# ----------------- MAIN -----------------
def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # attach DB reference (handlers will use global db but keep in bot_data too)
    app.bot_data["db"] = db

    # Register handlers (after functions defined)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))

    app.add_handler(CommandHandler("notes", notes_command))
    app.add_handler(CommandHandler("explain", explain_command))
    app.add_handler(CommandHandler("mcq", mcq_command))
    app.add_handler(CommandHandler("summary", summary_command))
    app.add_handler(CommandHandler("solve", solve_command))
    app.add_handler(CommandHandler("quiz", quiz_command))
    app.add_handler(CommandHandler("currentaffairs", current_affairs_command))

    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))

    app.add_handler(CallbackQueryHandler(help_button, pattern="^help_menu$"))
    app.add_handler(CallbackQueryHandler(tools_button, pattern="^tools_info$"))
    app.add_handler(CallbackQueryHandler(quiz_button, pattern="^quiz_info$"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.add_error_handler(error_handler)

    # schedule index creation in background (non-blocking)
    try:
        app.create_task(create_indexes(app))
    except Exception:
        # older PTB versions may not have create_task; safe to ignore
        pass

    logger.info("BOT STARTING...")

    # ---- Critical Heroku fix: ensure an event loop exists in MainThread ----
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    _setup_signal_handlers(app)
    app.run_polling()

if __name__ == "__main__":
    main()
