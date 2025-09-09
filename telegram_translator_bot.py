import os
import re
import logging
from typing import List
import asyncio

from googletrans import Translator
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# -------------------- Logging --------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# -------------------- Config --------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN environment variable")

# Fixed Render URL - you'll need to update this with your actual Render URL
PUBLIC_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://your-app-name.onrender.com").rstrip("/")

# Render provides a port in $PORT for web services
PORT = int(os.environ.get("PORT", "10000"))

# Telegram message safety
TG_MAX = 4096
TG_SAFE = 4000  # safety margin below hard limit
TRANSLATE_CHUNK = 1800  # conservative chunk size for translation backend

# Modes
MODE_AUTO = "auto"
MODE_TO_UK = "to_uk"
MODE_TO_EN = "to_en"

# Simple per-chat mode store (resets when app restarts)
chat_modes = {}

# Heuristic: detect Ukrainian/Cyrillic
UA_CYRILLIC_RE = re.compile(r"[А-Яа-яІіЇїЄєҐґ]")

# Google Translate client
translator = Translator(service_urls=["translate.googleapis.com", "translate.google.com"])

# -------------------- Utilities --------------------
def detect_direction(text: str) -> str:
    """Detect if text contains Ukrainian/Cyrillic characters"""
    return MODE_TO_EN if UA_CYRILLIC_RE.search(text) else MODE_TO_UK

def chunk_text(text: str, limit: int) -> List[str]:
    """
    Chunk text safely by paragraphs -> sentences -> words, keeping order and not breaking words.
    """
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    current = ""

    def push():
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    # Split by double newlines, keeping structure
    parts = re.split(r"(\n{2,})", text)
    for part in parts:
        if len(current) + len(part) <= limit:
            current += part
            continue

        # Push current chunk and start new one
        push()
        
        # If part is too long, split by sentences
        if len(part) > limit:
            sentences = re.split(r"(?<=[.!?])\s+", part)
            for sentence in sentences:
                if len(current) + len(sentence) + 1 <= limit:
                    current += sentence + " "
                elif len(sentence) <= limit:
                    push()
                    current = sentence + " "
                else:
                    # Split long sentences by words
                    push()
                    words = sentence.split(" ")
                    temp_sentence = ""
                    for word in words:
                        if len(temp_sentence) + len(word) + 1 <= limit:
                            temp_sentence += word + " "
                        else:
                            if temp_sentence.strip():
                                chunks.append(temp_sentence.strip())
                            temp_sentence = word + " "
                    if temp_sentence.strip():
                        current = temp_sentence
        else:
            current = part

    push()
    return [chunk for chunk in chunks if chunk.strip()]

def translate_text(text: str, direction: str) -> str:
    """Translate text using Google Translate"""
    try:
        if direction == MODE_TO_UK:
            src, dest = "en", "uk"
        elif direction == MODE_TO_EN:
            src, dest = "uk", "en"
        else:
            src, dest = "auto", "uk"

        in_chunks = chunk_text(text, TRANSLATE_CHUNK)
        out_chunks: List[str] = []

        for chunk in in_chunks:
            if not chunk.strip():
                continue
            
            # googletrans returns an object; we need .text
            result = translator.translate(chunk, src=src, dest=dest)
            if result and result.text:
                out_chunks.append(result.text)
            else:
                out_chunks.append(chunk)  # fallback to original if translation fails

        return "\n".join(out_chunks).strip()
    except Exception as e:
        logger.error(f"Translation error: {e}")
        return text  # Return original text if translation fails

async def send_long_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Send long text by chunking it into multiple messages"""
    parts = chunk_text(text, TG_SAFE)
    if not parts:
        return
    
    # Reply to the first message, then send follow-ups
    await update.message.reply_text(parts[0])
    for part in parts[1:]:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=part)

# -------------------- Handlers --------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    chat_id = update.effective_chat.id
    chat_modes[chat_id] = MODE_AUTO
    
    welcome_text = (
        "🔄 **Telegram Translator Bot**\n\n"
        "I automatically translate between English and Ukrainian!\n\n"
        "**How it works:**\n"
        "• Send any text - I'll auto-detect and translate\n"
        "• Latin text → Ukrainian\n"
        "• Cyrillic text → English\n\n"
        "**Commands:**\n"
        "• /auto - Auto-detect language (default)\n"
        "• /to_en - Force Ukrainian → English\n"
        "• /to_uk - Force English → Ukrainian\n"
        "• /help - Show help\n\n"
        "Ready to translate! 🚀"
    )
    
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    help_text = (
        "**Available Commands:**\n\n"
        "/auto – Auto-detect language per message\n"
        "/to_en – Force Ukrainian → English\n"
        "/to_uk – Force English → Ukrainian\n"
        "/help – Show this help\n\n"
        "Just send any text and I'll translate it automatically!"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set mode to auto-detect"""
    chat_modes[update.effective_chat.id] = MODE_AUTO
    await update.message.reply_text("✅ Mode set to **auto-detect**", parse_mode='Markdown')

async def to_en_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set mode to Ukrainian -> English"""
    chat_modes[update.effective_chat.id] = MODE_TO_EN
    await update.message.reply_text("✅ Mode set to **Ukrainian → English**", parse_mode='Markdown')

async def to_uk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set mode to English -> Ukrainian"""
    chat_modes[update.effective_chat.id] = MODE_TO_UK
    await update.message.reply_text("✅ Mode set to **English → Ukrainian**", parse_mode='Markdown')

async def translate_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages and translate them"""
    if not update.message or not update.message.text:
        return
    
    text = update.message.text.strip()
    
    # Skip commands
    if text.startswith("/"):
        return
    
    # Skip very short messages
    if len(text) < 2:
        return

    chat_id = update.effective_chat.id
    mode = chat_modes.get(chat_id, MODE_AUTO)
    
    # Determine translation direction
    if mode == MODE_AUTO:
        direction = detect_direction(text)
    else:
        direction = mode

    try:
        # Show typing indicator
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        # Translate text
        translated = await context.application.run_in_threadpool(translate_text, text, direction)
        
        if not translated or translated == text:
            # If translation failed or returned same text, try to give helpful feedback
            if mode == MODE_AUTO:
                await update.message.reply_text("🤔 I couldn't detect the language or translate that. Try using /to_en or /to_uk commands.")
            else:
                await update.message.reply_text("⚠️ Translation failed. Please try again.")
            return
        
        # Send translated text
        await send_long_text(update, context, translated)
        
    except Exception as e:
        logger.error(f"Translation failed for chat {chat_id}: {e}")
        await update.message.reply_text("❌ Translation error. Please try again later.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors"""
    logger.error("Exception while handling an update:", exc_info=context.error)

# -------------------- Webhook Handler --------------------
async def webhook_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle webhook updates"""
    try:
        await context.application.process_update(update)
    except Exception as e:
        logger.error(f"Error processing update: {e}")

# -------------------- App setup --------------------
def create_application() -> Application:
    """Create and configure the Telegram application"""
    # Build application
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("auto", auto_cmd))
    app.add_handler(CommandHandler("to_en", to_en_cmd))
    app.add_handler(CommandHandler("to_uk", to_uk_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, translate_msg))
    
    # Add error handler
    app.add_error_handler(error_handler)
    
    return app

async def setup_webhook(app: Application):
    """Set up webhook for the application"""
    try:
        # Initialize the application
        await app.initialize()
        await app.start()
        
        # Set webhook
        webhook_url = f"{PUBLIC_URL}/webhook"
        await app.bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=True
        )
        
        logger.info(f"Webhook set to: {webhook_url}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to setup webhook: {e}")
        return False

def main():
    """Main function to run the bot"""
    logger.info("Starting Telegram Translator Bot...")
    
    # Create application
    application = create_application()
    
    # Use webhook for production (Render)
    if os.environ.get("RENDER"):
        logger.info("Running in webhook mode for Render deployment")
        
        # Run webhook server
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="webhook",
            webhook_url=f"{PUBLIC_URL}/webhook",
            drop_pending_updates=True,
            stop_signals=None,
        )
    else:
        # For local development, use polling
        logger.info("Running in polling mode for local development")
        application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
