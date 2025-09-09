import os
import re
import logging
from typing import List
import asyncio

from deep_translator import GoogleTranslator
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

# Get the Render URL from environment or use a placeholder
PUBLIC_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
if not PUBLIC_URL:
    # Try to construct from Render service name
    service_name = os.environ.get("RENDER_SERVICE_NAME", "your-service-name")
    PUBLIC_URL = f"https://{service_name}.onrender.com"

PUBLIC_URL = PUBLIC_URL.rstrip("/")

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

    # First try splitting by paragraphs (double newlines)
    paragraphs = text.split('\n\n')
    
    for para in paragraphs:
        if len(current) + len(para) + 2 <= limit:  # +2 for \n\n
            if current:
                current += '\n\n' + para
            else:
                current = para
        else:
            # Push current chunk if it exists
            push()
            
            # If paragraph is too long, split by sentences
            if len(para) > limit:
                sentences = re.split(r'(?<=[.!?])\s+', para)
                for sentence in sentences:
                    if len(current) + len(sentence) + 1 <= limit:
                        if current:
                            current += ' ' + sentence
                        else:
                            current = sentence
                    else:
                        push()
                        if len(sentence) <= limit:
                            current = sentence
                        else:
                            # Split long sentence by words
                            words = sentence.split()
                            temp = ""
                            for word in words:
                                if len(temp) + len(word) + 1 <= limit:
                                    temp += (word + ' ')
                                else:
                                    if temp.strip():
                                        chunks.append(temp.strip())
                                    temp = word + ' '
                            if temp.strip():
                                current = temp.strip()
            else:
                current = para

    push()
    return [chunk for chunk in chunks if chunk.strip()]

def translate_text(text: str, direction: str) -> str:
    """Translate text using Google Translate via deep-translator"""
    try:
        if direction == MODE_TO_UK:
            source, target = "en", "uk"
        elif direction == MODE_TO_EN:
            source, target = "uk", "en"
        else:
            # Auto mode - detect based on content
            if UA_CYRILLIC_RE.search(text):
                source, target = "uk", "en"
            else:
                source, target = "en", "uk"

        # Split text into chunks if necessary
        in_chunks = chunk_text(text, TRANSLATE_CHUNK)
        out_chunks: List[str] = []

        for chunk in in_chunks:
            if not chunk.strip():
                continue
            
            try:
                # Use GoogleTranslator from deep-translator
                translator = GoogleTranslator(source=source, target=target)
                result = translator.translate(chunk)
                
                if result and result.strip():
                    out_chunks.append(result)
                else:
                    out_chunks.append(chunk)  # fallback to original if translation fails
                    
            except Exception as e:
                logger.error(f"Translation error for chunk: {e}")
                out_chunks.append(chunk)  # fallback to original

        final_result = '\n\n'.join(out_chunks).strip()
        return final_result if final_result else text
        
    except Exception as e:
        logger.error(f"Translation error: {e}")
        return text  # Return original text if translation fails

async def send_long_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Send long text by chunking it into multiple messages"""
    parts = chunk_text(text, TG_SAFE)
    if not parts:
        return
    
    try:
        # Reply to the first message, then send follow-ups
        await update.message.reply_text(parts[0])
        for part in parts[1:]:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=part)
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        # Fallback: try to send a simple error message
        try:
            await update.message.reply_text("❌ Failed to send translation. Please try again.")
        except:
            pass

# -------------------- Handlers --------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    try:
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
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        await update.message.reply_text("Hello! I'm a translation bot. Send me text to translate!")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    try:
        help_text = (
            "**Available Commands:**\n\n"
            "/auto – Auto-detect language per message\n"
            "/to_en – Force Ukrainian → English\n"
            "/to_uk – Force English → Ukrainian\n"
            "/help – Show this help\n\n"
            "Just send any text and I'll translate it automatically!"
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in help command: {e}")
        await update.message.reply_text("Available commands: /auto /to_en /to_uk /help")

async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set mode to auto-detect"""
    try:
        chat_modes[update.effective_chat.id] = MODE_AUTO
        await update.message.reply_text("✅ Mode set to **auto-detect**", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in auto command: {e}")
        await update.message.reply_text("✅ Mode set to auto-detect")

async def to_en_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set mode to Ukrainian -> English"""
    try:
        chat_modes[update.effective_chat.id] = MODE_TO_EN
        await update.message.reply_text("✅ Mode set to **Ukrainian → English**", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in to_en command: {e}")
        await update.message.reply_text("✅ Mode set to Ukrainian → English")

async def to_uk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set mode to English -> Ukrainian"""
    try:
        chat_modes[update.effective_chat.id] = MODE_TO_UK
        await update.message.reply_text("✅ Mode set to **English → Ukrainian**", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in to_uk command: {e}")
        await update.message.reply_text("✅ Mode set to English → Ukrainian")

async def translate_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages and translate them"""
    try:
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

        # Show typing indicator
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        except:
            pass
        
        # Translate text in a thread to avoid blocking
        translated = await context.application.run_in_threadpool(translate_text, text, direction)
        
        if not translated or translated == text:
            # If translation failed or returned same text
            if mode == MODE_AUTO:
                await update.message.reply_text("🤔 I couldn't detect the language. Try /to_en or /to_uk")
            else:
                await update.message.reply_text("⚠️ Translation failed. Please try again.")
            return
        
        # Send translated text
        await send_long_text(update, context, translated)
        
    except Exception as e:
        logger.error(f"Translation failed for chat {update.effective_chat.id if update.effective_chat else 'unknown'}: {e}")
        try:
            await update.message.reply_text("❌ Translation error. Please try again later.")
        except:
            pass

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors"""
    logger.error("Exception while handling an update:", exc_info=context.error)

# -------------------- App setup --------------------
def create_application() -> Application:
    """Create and configure the Telegram application"""
    # Build application with proper timeout settings
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )
    
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

def main():
    """Main function to run the bot"""
    logger.info("Starting Telegram Translator Bot...")
    logger.info(f"Using webhook URL: {PUBLIC_URL}")
    
    # Create application
    application = create_application()
    
    # Always use webhook for Render deployment
    logger.info("Running in webhook mode")
    
    try:
        # Run webhook server with proper configuration
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="webhook",
            webhook_url=f"{PUBLIC_URL}/webhook",
            drop_pending_updates=True,
            stop_signals=None,  # Let Render handle shutdown signals
        )
    except Exception as e:
        logger.error(f"Failed to start webhook: {e}")
        raise

if __name__ == "__main__":
    main()
