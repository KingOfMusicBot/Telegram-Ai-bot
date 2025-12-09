# bot.py
import logging
import os
import asyncio
from time import time
from signal import SIGTERM, SIGINT

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

# Load .env if present (local dev). Heroku uses Config Vars.
load_dotenv()

# ---------- CONFIG (from ENV) ----------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # Groq API key
OWNER_ID = int(os.getenv("OWNER_ID", "0"))  # MUST set on Heroku (numeric)
CHANNEL_URL = os.getenv("CHANNEL_URL", "")   # optional
SUPPORT_URL = os.getenv("SUPPORT_URL", "")   # optional
START_PIC_URL = os.getenv("START_PIC_URL", "")  # optional image url
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "5"))

# ---------- Basic sanity checks ----------
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable missing")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY environment variable missing")
if OWNER_ID == 0:
    logging.warning("OWNER_ID not set or zero. Owner-only commands will be inaccessible until set.")

# ---------- Logging ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------- Groq/OpenAI Client ----------
client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url="https://api.groq.com/openai/v1",
)

# ---------- Helper + Bot logic ----------
def register_chat(chat_id: int, chat_type: str, context: ContextTypes.DEFAULT_TYPE):
    data = context.application.bot_data
    users = data.setdefault("users", set())
    groups = data.setdefault("groups", set())
    if chat_type == "private":
        users.add(chat_id)
    elif chat_type in ("group", "supergroup"):
        groups.add(chat_id)

async def ask_ai(prompt: str, mode: str = "default") -> str:
    # same system prompts as before (kept short)
    systems = {
        "notes": "Tum ek expert teacher ho. Student ke liye concise, bullet-point notes banao.",
        "explain": "Tum friendly teacher ho. Concepts simple Hinglish me explain karo with examples.",
        "mcq": "Student ke liye 5 MCQs banao. A-D options. End me Answer Key:",
        "summary": "Summarize given text into short bullet points for students.",
        "solve": "Solve step-by-step and explain each step.",
        "quiz": "Make a 5-question quiz for students. Provide answer key.",
        "current": "Create practice-style current affairs Q&A for students.",
        "default": "Tum ek helpful AI assistant ho. Seedhe aur clear answers do in Hinglish.",
    }
    system = systems.get(mode, systems["default"])

    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=800,
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        logger.exception("AI error")
        return "AI side pe error aa gaya. Thodi der baad try karo."

def check_spam(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> float:
    now = time()
    data = context.application.bot_data
    rate_limits = data.setdefault("rate_limits", {})
    last_time = rate_limits.get(user_id, 0)
    diff = now - last_time
    if diff < COOLDOWN_SECONDS:
        return COOLDOWN_SECONDS - diff
    rate_limits[user_id] = now
    return 0.0

def get_help_text() -> str:
    return (
        "≋ Help & Commands ≋\n\n"
        "• Private: direct question bhejo.\n"
        "• Group: bot ko mention karke (@BotName) ya uske message ko reply karke question bhejo.\n\n"
        "Study Commands:\n"
        "/notes <topic>\n/explain <topic>\n/mcq <topic>\n/summary <text or reply>\n/solve <question>\n/quiz <topic>\n/currentaffairs\n\n"
        "Owner only:\n/stats\n/broadcast <message>\n"
    )

# Handlers (start/help + callback + study commands) - minimal changes: same behavior as your code
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    register_chat(chat.id, chat.type, context)
    welcome_text = (
        "Hey! 👋\n\nMain ek AI Study bot hoon. Doubts, notes, MCQs, quiz, summary, maths solve.\n"
        "Private: direct question. Group: mention @BotName or reply.\n"
    )
    bot_username = context.bot.username or "this_bot"
    keyboard = [
        [InlineKeyboardButton("✚ ADD ME IN YOUR GROUP ✚", url=f"https://t.me/{bot_username}?startgroup=true")],
        [InlineKeyboardButton("≋ HELP AND COMMANDS ≋", callback_data="help_menu")],
        [InlineKeyboardButton("≋ OWNER ≋", url=f"tg://user?id={OWNER_ID}"), InlineKeyboardButton("≋ CHANNEL ≋", url=CHANNEL_URL or "https://t.me/")],
        [InlineKeyboardButton("≋ SUPPORT ≋", url=SUPPORT_URL or "https://t.me/")],
        [InlineKeyboardButton("🧠 QUIZ", callback_data="quiz_info"), InlineKeyboardButton("🛠 AI TOOLS", callback_data="tools_info")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if START_PIC_URL:
        await chat.send_photo(photo=START_PIC_URL, caption=welcome_text, reply_markup=reply_markup)
    else:
        await chat.send_message(text=welcome_text, reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_help_text())

async def help_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(get_help_text())

async def tools_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "• /notes <topic>\n• /explain <topic>\n• /mcq <topic>\n• /summary <text/reply)\n• /solve <question>\n• /quiz <topic>\n• /currentaffairs"
    await query.message.reply_text(text)

async def quiz_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "Command: /quiz <topic>\nExample: /quiz Class 10 Physics"
    await query.message.reply_text(text)

# Study commands (examples)
async def notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; user = update.effective_user
    register_chat(chat.id, chat.type, context)
    topic = " ".join(context.args).strip()
    if not topic:
        await update.message.reply_text("Usage: /notes <topic>")
        return
    if user:
        remaining = check_spam(user.id, context)
        if remaining > 0:
            await update.message.reply_text(f"Thoda dheere pucho, {int(remaining)} sec baad try karo.")
            return
    await chat.send_action(ChatAction.TYPING)
    prompt = f"Topic: {topic}\nStudent ke liye short, structured notes banao."
    reply = await ask_ai(prompt, mode="notes")
    await update.message.reply_text(reply)

# (Other commands: explain, mcq, summary, solve, quiz, current_affairs) - keep same as your file
# For brevity include only a few; copy the remaining handlers from your original file if desired.

async def explain_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; user = update.effective_user
    register_chat(chat.id, chat.type, context)
    topic = " ".join(context.args).strip()
    if not topic:
        await update.message.reply_text("Usage: /explain <topic>")
        return
    if user:
        remaining = check_spam(user.id, context)
        if remaining > 0:
            await update.message.reply_text(f"Thoda dheere pucho, {int(remaining)} sec baad try karo.")
            return
    await chat.send_action(ChatAction.TYPING)
    prompt = f"Explain this topic for a student: {topic}"
    reply = await ask_ai(prompt, mode="explain")
    await update.message.reply_text(reply)

async def mcq_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; user = update.effective_user
    register_chat(chat.id, chat.type, context)
    topic = " ".join(context.args).strip()
    if not topic:
        await update.message.reply_text("Usage: /mcq <topic>")
        return
    if user:
        remaining = check_spam(user.id, context)
        if remaining > 0:
            await update.message.reply_text(f"Thoda dheere pucho, {int(remaining)} sec baad try karo.")
            return
    await chat.send_action(ChatAction.TYPING)
    prompt = f"Make 5 MCQs for topic: {topic}"
    reply = await ask_ai(prompt, mode="mcq")
    await update.message.reply_text(reply)

# Owner commands
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id != OWNER_ID:
        return
    data = context.application.bot_data
    users = data.get("users", set()); groups = data.get("groups", set())
    msg = f"📊 Bot Stats:\n• Total private users: {len(users)}\n• Total groups: {len(groups)}"
    await update.message.reply_text(msg)

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id != OWNER_ID:
        return
    data = context.application.bot_data
    users = list(data.get("users", set()))
    text = " ".join(context.args).strip()
    if not text and update.message.reply_to_message:
        text = update.message.reply_to_message.text
    if not text:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    await update.message.reply_text(f"Broadcast starting... ({len(users)} users)")
    sent = failed = 0
    for uid in users:
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"Broadcast error to {uid}: {e}")
            failed += 1
    await update.message.reply_text(f"Broadcast complete.\nSent: {sent}\nFailed: {failed}")

# Main message handler (private: answer; group: mention/reply)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message: return
    user = update.effective_user; chat = update.effective_chat; text = message.text or ""
    register_chat(chat.id, chat.type, context)
    if user and user.is_bot: return
    logger.info(f"Msg from {user.id if user else 'unk'} (@{user.username if user else 'unk'}) in {chat.id}: {text}")
    if len(text) > 2000:
        await message.reply_text("Itna lamba message mat bhejo, thoda short me puchho.")
        return
    if chat.type in ("group", "supergroup"):
        bot_username = context.bot.username
        text_lower = text.lower()
        mentioned = bot_username and f"@{bot_username.lower()}" in text_lower
        reply_to_bot = (message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == context.bot.id)
        if not (mentioned or reply_to_bot):
            return
        if mentioned and text_lower.strip() in {f"@{bot_username.lower()}", f"/ai@{bot_username.lower()}"}:
            await message.reply_text("Mujhe mention ke saath apna question bhi likho. 🙂")
            return
    if user:
        remaining = check_spam(user.id, context)
        if remaining > 0:
            await message.reply_text(f"Thoda dheere pucho, spam mat karo. {int(remaining)} sec baad fir try karo.")
            return
    await chat.send_action(ChatAction.TYPING)
    reply = await ask_ai(text, mode="default")
    await message.reply_text(reply)

# Error handler
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Exception while handling update")

# Graceful shutdown handler (Heroku sends SIGTERM on dyno stop)
def _setup_signal_handlers(application):
    loop = asyncio.get_running_loop()
    for sig in (SIGTERM, SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(application.shutdown()))
        except NotImplementedError:
            # Windows / restricted env might not support signal handlers
            pass

def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers
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

    # signal handlers for graceful shutdown on Heroku
    _setup_signal_handlers(app)

    logger.info("Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
