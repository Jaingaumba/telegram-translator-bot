import os
import re
import logging
from typing import List
import asyncio
from flask import Flask, request, jsonify

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
# Try multiple ways to get the bot token (Render-specific)
TELEGRAM_BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN") or 
    os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
)

if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN not found in environment variables")
    logger.error(f"Available env vars: {list(os.environ.keys())}")
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN environment variable")

# Render-specific URL handling
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip()
if not RENDER_EXTERNAL_URL:
    # Try to get from Render's automatic variables
    service_name = os.getenv("RENDER_SERVICE_NAME", "")
    if service_name:
        RENDER_EXTERNAL_URL = f"https://{service_name}.onrender.com"
    else:
        # Fallback - user needs to set this manually
        RENDER_EXTERNAL_URL = "https://your-service-name.onrender.com"

PUBLIC_URL = RENDER_EXTERNAL_URL.rstrip("/")
PORT = int(os.environ.get("PORT", "10000"))

logger.info(f"Bot Token: {'*' * (len(TELEGRAM_BOT_TOKEN) - 8) + TELEGRAM_BOT_TOKEN[-8:]}")
logger.info(f"Public URL: {PUBLIC_URL}")
logger.info(f"Port: {PORT}")

# Telegram message safety
TG_MAX = 4096
TG_SAFE = 4000
TRANSLATE_CHUNK = 1800

# Modes
MODE_AUTO = "auto"
MODE_TO_UK = "to_uk"
MODE_TO_EN = "to_en"

# Chat modes storage
chat_modes = {}

# Language detection regex
UA_CYRILLIC_RE = re.compile(r"[А-Яа-яІіЇїЄєҐґ]")

# -------------------- Flask App Setup --------------------
app = Flask(__name__)

# Global application variable
telegram_app = None

# -------------------- Utilities --------------------
def detect_direction(text: str) -> str:
    """Detect if text contains Ukrainian/Cyrillic characters"""
    return MODE_TO_EN if UA_CYRILLIC_RE.search(text) else MODE_TO_UK

def chunk_text(text: str, limit: int) -> List[str]:
    """Chunk text safely by paragraphs -> sentences -> words"""
    if len(text) <= limit:
        return [text]

    chunks = []
    current = ""

    def push():
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    # Split by paragraphs first
    paragraphs = text.split('\n\n')
    
    for para in paragraphs:
        if len(current) + len(para) + 2 <= limit:
            if current:
                current += '\n\n' + para
            else:
                current = para
        else:
            push()
            
            if len(para) > limit:
                # Split by sentences
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
                            # Split by words
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
    """Translate text using Google Translate"""
    try:
        if direction == MODE_TO_UK:
            source, target = "en", "uk"
        elif direction == MODE_TO_EN:
            source, target = "uk", "en"
        else:
            if UA_CYRILLIC_RE.search(text):
                source, target = "uk", "en"
            else:
                source, target = "en", "uk"

        chunks = chunk_text(text, TRANSLATE_CHUNK)
        translated_chunks = []

        for chunk in chunks:
            if not chunk.strip():
                continue
            
            try:
                translator = GoogleTranslator(source=source, target=target)
                result = translator.translate(chunk)
                if result and result.strip():
                    translated_chunks.append(result)
                else:
                    translated_chunks.append(chunk)
            except Exception as e:
                logger.error(f"Translation error for chunk: {e}")
                translated_chunks.append(chunk)

        return '\n\n'.join(translated_chunks).strip() or text
        
    except Exception as e:
        logger.error(f"Translation error: {e}")
        return text

async def send_long_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Send long text by chunking it"""
    parts = chunk_text(text, TG_SAFE)
    if not parts:
        return
    
    try:
        await update.message.reply_text(parts[0])
        for part in parts[1:]:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=part)
    except Exception as e:
        logger.error(f"Error sending message: {e}")
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
        
        if text.startswith("/") or len(text) < 2:
            return

        chat_id = update.effective_chat.id
        mode = chat_modes.get(chat_id, MODE_AUTO)
        
        direction = detect_direction(text) if mode == MODE_AUTO else mode

        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        except:
            pass
        
        translated = await context.application.run_in_threadpool(translate_text, text, direction)
        
        if not translated or translated == text:
            if mode == MODE_AUTO:
                await update.message.reply_text("🤔 I couldn't detect the language. Try /to_en or /to_uk")
            else:
                await update.message.reply_text("⚠️ Translation failed. Please try again.")
            return
        
        await send_long_text(update, context, translated)
        
    except Exception as e:
        logger.error(f"Translation failed: {e}")
        try:
            await update.message.reply_text("❌ Translation error. Please try again later.")
        except:
            pass

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors"""
    logger.error("Exception while handling an update:", exc_info=context.error)

# -------------------- Flask Routes --------------------
@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "Telegram Translator Bot is running!", "webhook_url": f"{PUBLIC_URL}/webhook"})

@app.route("/webhook", methods=["POST"])
def webhook():
    """Handle webhook updates"""
    try:
        if not telegram_app:
            logger.error("Telegram app not initialized")
            return jsonify({"error": "Bot not initialized"}), 500
            
        update = Update.de_json(request.get_json(force=True), telegram_app.bot)
        
        # Process update in async context
        asyncio.create_task(telegram_app.process_update(update))
        
        return jsonify({"status": "ok"})
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/set_webhook", methods=["POST"])
def set_webhook():
    """Set webhook manually"""
    try:
        if not telegram_app:
            return jsonify({"error": "Bot not initialized"}), 500
            
        webhook_url = f"{PUBLIC_URL}/webhook"
        
        # Use synchronous method
        import requests
        
        set_webhook_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook"
        response = requests.post(set_webhook_url, json={"url": webhook_url})
        
        if response.ok:
            return jsonify({"status": "Webhook set successfully", "url": webhook_url})
        else:
            return jsonify({"error": "Failed to set webhook", "response": response.text}), 400
            
    except Exception as e:
        logger.error(f"Set webhook error: {e}")
        return jsonify({"error": str(e)}), 500

# -------------------- Bot Setup --------------------
def create_application() -> Application:
    """Create and configure the Telegram application"""
    return (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )

async def setup_application():
    """Setup the Telegram application with handlers"""
    global telegram_app
    
    telegram_app = create_application()
    
    # Add handlers
    telegram_app.add_handler(CommandHandler("start", start_cmd))
    telegram_app.add_handler(CommandHandler("help", help_cmd))
    telegram_app.add_handler(CommandHandler("auto", auto_cmd))
    telegram_app.add_handler(CommandHandler("to_en", to_en_cmd))
    telegram_app.add_handler(CommandHandler("to_uk", to_uk_cmd))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, translate_msg))
    telegram_app.add_error_handler(error_handler)
    
    # Initialize the application
    await telegram_app.initialize()
    await telegram_app.start()
    
    # Set webhook
    webhook_url = f"{PUBLIC_URL}/webhook"
    await telegram_app.bot.set_webhook(
        url=webhook_url,
        drop_pending_updates=True
    )
    
    logger.info(f"Webhook set to: {webhook_url}")
    return telegram_app

def main():
    """Main function"""
    logger.info("Starting Telegram Translator Bot with Flask...")
    
    # Setup the application in a new event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(setup_application())
        logger.info("Telegram application initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize Telegram app: {e}")
        raise
    
    # Run Flask app
    logger.info(f"Starting Flask server on 0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)

if __name__ == "__main__":
    main()
