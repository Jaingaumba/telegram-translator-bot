import os
import re
import logging
import threading
import asyncio
from typing import List
from flask import Flask, request, jsonify
import concurrent.futures

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
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# -------------------- Config --------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN environment variable")

RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip()
if not RENDER_EXTERNAL_URL:
    service_name = os.getenv("RENDER_SERVICE_NAME", "")
    if service_name:
        RENDER_EXTERNAL_URL = f"https://{service_name}.onrender.com"
    else:
        RENDER_EXTERNAL_URL = "https://your-service-name.onrender.com"

PUBLIC_URL = RENDER_EXTERNAL_URL.rstrip("/")
PORT = int(os.environ.get("PORT", "10000"))

logger.info(f"Webhook URL: {PUBLIC_URL}/webhook")

# Constants
TG_SAFE = 4000
TRANSLATE_CHUNK = 1500  # Smaller chunks for better quality
MODE_AUTO = "auto"
MODE_TO_UK = "to_uk"
MODE_TO_EN = "to_en"

# Global variables
chat_modes = {}
telegram_app = None
bot_loop = None

# Language detection
UA_CYRILLIC_RE = re.compile(r"[А-Яа-яІіЇїЄєҐґ]")

# Flask app
app = Flask(__name__)

# -------------------- Simple & Effective Utilities --------------------
def detect_direction(text: str) -> str:
    return MODE_TO_EN if UA_CYRILLIC_RE.search(text) else MODE_TO_UK

def split_text_preserving_paragraphs(text: str, max_chunk_size: int) -> List[str]:
    """
    Simple but effective: Split text by paragraphs, keep them together as much as possible
    """
    if len(text) <= max_chunk_size:
        return [text]
    
    # Split by double newlines (paragraphs)
    paragraphs = text.split('\n\n')
    
    chunks = []
    current_chunk = ""
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
            
        # If adding this paragraph would exceed the limit
        if current_chunk and len(current_chunk) + len(para) + 2 > max_chunk_size:
            # Save current chunk and start new one
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
            current_chunk = para
        else:
            # Add paragraph to current chunk
            if current_chunk:
                current_chunk += "\n\n" + para
            else:
                current_chunk = para
        
        # If single paragraph is too long, split it by sentences
        if len(current_chunk) > max_chunk_size:
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
            current_chunk = ""
            
            # Split long paragraph by sentences
            sentences = re.split(r'(?<=[.!?])\s+', para)
            temp_chunk = ""
            
            for sentence in sentences:
                if temp_chunk and len(temp_chunk) + len(sentence) + 1 > max_chunk_size:
                    if temp_chunk.strip():
                        chunks.append(temp_chunk.strip())
                    temp_chunk = sentence
                else:
                    if temp_chunk:
                        temp_chunk += " " + sentence
                    else:
                        temp_chunk = sentence
            
            if temp_chunk.strip():
                current_chunk = temp_chunk
    
    # Add remaining chunk
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    
    return [chunk for chunk in chunks if chunk.strip()]

def translate_text_sync(text: str, direction: str) -> str:
    """Simple translation with paragraph preservation - no overcomplicated logic"""
    try:
        if direction == MODE_TO_UK:
            source, target = "en", "uk"
        elif direction == MODE_TO_EN:
            source, target = "uk", "en"
        else:
            source, target = ("uk", "en") if UA_CYRILLIC_RE.search(text) else ("en", "uk")

        # Split text while preserving paragraph structure
        chunks = split_text_preserving_paragraphs(text, TRANSLATE_CHUNK)
        translated_chunks = []

        logger.info(f"Translating {len(chunks)} chunks while preserving paragraphs")

        for i, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            
            try:
                translator = GoogleTranslator(source=source, target=target)
                result = translator.translate(chunk)
                
                if result and result.strip():
                    translated_chunks.append(result.strip())
                else:
                    translated_chunks.append(chunk)
                
                # Small delay to avoid rate limiting
                if i < len(chunks) - 1:
                    import time
                    time.sleep(0.1)
                    
            except Exception as e:
                logger.error(f"Translation error for chunk {i+1}: {e}")
                translated_chunks.append(chunk)

        # Join chunks with double newlines to preserve paragraph structure
        return "\n\n".join(translated_chunks) if translated_chunks else text
        
    except Exception as e:
        logger.error(f"Translation error: {e}")
        return text

def chunk_text_for_telegram(text: str, limit: int = TG_SAFE) -> List[str]:
    """Split text for Telegram while preserving paragraph breaks"""
    if len(text) <= limit:
        return [text]

    chunks = []
    current = ""

    # Split by paragraphs first
    paragraphs = text.split('\n\n')
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
            
        # If adding this paragraph would exceed limit
        if current and len(current) + len(para) + 2 > limit:
            if current.strip():
                chunks.append(current.strip())
            current = para
        else:
            if current:
                current += "\n\n" + para
            else:
                current = para
        
        # If single paragraph is too long, split by sentences
        if len(current) > limit:
            if '\n\n' in current:
                # Multiple paragraphs in current, save what we can
                parts = current.split('\n\n')
                save_parts = '\n\n'.join(parts[:-1])
                if save_parts.strip():
                    chunks.append(save_parts.strip())
                current = parts[-1]
            
            # Split current by sentences if still too long
            if len(current) > limit:
                sentences = re.split(r'(?<=[.!?])\s+', current)
                temp = ""
                
                for sentence in sentences:
                    if temp and len(temp) + len(sentence) + 1 > limit:
                        if temp.strip():
                            chunks.append(temp.strip())
                        temp = sentence
                    else:
                        if temp:
                            temp += " " + sentence
                        else:
                            temp = sentence
                
                current = temp if temp.strip() else ""
    
    if current.strip():
        chunks.append(current.strip())
    
    return [chunk for chunk in chunks if chunk.strip()]

async def send_long_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Send long text by chunking while preserving paragraphs"""
    parts = chunk_text_for_telegram(text, TG_SAFE)
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
    try:
        chat_id = update.effective_chat.id
        chat_modes[chat_id] = MODE_AUTO
        
        welcome_text = (
            "🔄 **Paragraph-Preserving Telegram Translator**\n\n"
            "I translate between English and Ukrainian while keeping your paragraph structure intact!\n\n"
            "**Features:**\n"
            "• 📝 Preserves paragraph breaks (\\n\\n)\n"
            "• 🎯 Smart chunking for better quality\n"
            "• 🔄 Auto-detect or manual language selection\n\n"
            "**How it works:**\n"
            "• Send any text with paragraphs\n"
            "• Latin text → Ukrainian\n"
            "• Cyrillic text → English\n"
            "• Paragraph structure maintained!\n\n"
            "**Commands:**\n"
            "• /auto - Auto-detect language (default)\n"
            "• /to_en - Force Ukrainian → English\n"
            "• /to_uk - Force English → Ukrainian\n"
            "• /help - Show help\n\n"
            "Ready to translate with perfect formatting! 🚀"
        )
        
        await update.message.reply_text(welcome_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        await update.message.reply_text("Hello! I'm a paragraph-preserving translation bot. Send me text to translate!")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        help_text = (
            "**Paragraph-Preserving Translation Bot**\n\n"
            "**Commands:**\n"
            "/auto – Auto-detect language per message\n"
            "/to_en – Force Ukrainian → English\n"
            "/to_uk – Force English → Ukrainian\n"
            "/help – Show this help\n\n"
            "**Key Feature:**\n"
            "✅ Maintains paragraph breaks in translations\n"
            "✅ Preserves text structure and formatting\n"
            "✅ Smart chunking for long texts\n\n"
            "Just send text with paragraphs and see the magic! 📝"
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in help command: {e}")
        await update.message.reply_text("Available commands: /auto /to_en /to_uk /help")

async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_modes[update.effective_chat.id] = MODE_AUTO
        await update.message.reply_text("✅ Mode: **Auto-detect** with paragraph preservation", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in auto command: {e}")
        await update.message.reply_text("✅ Mode set to auto-detect")

async def to_en_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_modes[update.effective_chat.id] = MODE_TO_EN
        await update.message.reply_text("✅ Mode: **Ukrainian → English** with paragraph preservation", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in to_en command: {e}")
        await update.message.reply_text("✅ Mode set to Ukrainian → English")

async def to_uk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_modes[update.effective_chat.id] = MODE_TO_UK
        await update.message.reply_text("✅ Mode: **English → Ukrainian** with paragraph preservation", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in to_uk command: {e}")
        await update.message.reply_text("✅ Mode set to English → Ukrainian")

async def translate_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages and translate them while preserving paragraph structure"""
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
        
        # Count paragraphs for logging
        paragraph_count = len([p for p in text.split('\n\n') if p.strip()])
        logger.info(f"Translating {len(text)} chars, {paragraph_count} paragraphs, mode: {mode} -> {direction}")
        
        # Use thread pool for translation
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            loop = asyncio.get_event_loop()
            translated = await loop.run_in_executor(executor, translate_text_sync, text, direction)
        
        if not translated or translated == text:
            if mode == MODE_AUTO:
                await update.message.reply_text("🤔 I couldn't detect the language. Try /to_en or /to_uk")
            else:
                await update.message.reply_text("⚠️ Translation failed. Please try again.")
            return
        
        # Verify paragraph structure is preserved
        original_para_count = len([p for p in text.split('\n\n') if p.strip()])
        translated_para_count = len([p for p in translated.split('\n\n') if p.strip()])
        logger.info(f"Translation completed: {original_para_count} → {translated_para_count} paragraphs")
        
        await send_long_text(update, context, translated)
        
    except Exception as e:
        logger.error(f"Translation failed: {e}")
        try:
            await update.message.reply_text("❌ Translation error. Please try again later.")
        except:
            pass

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)

# -------------------- Bot Setup --------------------
def create_application() -> Application:
    return (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )

async def setup_bot():
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
    
    # Initialize and start
    await telegram_app.initialize()
    await telegram_app.start()
    
    # Set webhook
    webhook_url = f"{PUBLIC_URL}/webhook"
    await telegram_app.bot.set_webhook(
        url=webhook_url,
        drop_pending_updates=True
    )
    
    logger.info(f"✅ Paragraph-preserving bot webhook set: {webhook_url}")
    return telegram_app

def run_bot_in_thread():
    global bot_loop
    
    def bot_thread():
        global bot_loop
        bot_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(bot_loop)
        
        try:
            bot_loop.run_until_complete(setup_bot())
            logger.info("✅ Paragraph-preserving bot initialized successfully")
            bot_loop.run_forever()
        except Exception as e:
            logger.error(f"❌ Bot initialization failed: {e}")
            raise
    
    bot_thread_obj = threading.Thread(target=bot_thread, daemon=True)
    bot_thread_obj.start()
    
    # Wait for initialization
    import time
    time.sleep(3)
    
    if not bot_loop or not telegram_app:
        raise RuntimeError("Bot initialization failed")
    
    logger.info("✅ Paragraph-preserving bot thread started successfully")

# -------------------- Flask Routes --------------------
@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "status": "Paragraph-Preserving Telegram Translator Bot is running!",
        "webhook_url": f"{PUBLIC_URL}/webhook",
        "bot_initialized": telegram_app is not None,
        "key_feature": "Maintains paragraph structure in translations"
    })

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        if not telegram_app or not bot_loop:
            logger.error("Bot not initialized")
            return jsonify({"error": "Bot not initialized"}), 500
            
        json_data = request.get_json(force=True)
        if not json_data:
            return jsonify({"error": "No data received"}), 400
            
        update = Update.de_json(json_data, telegram_app.bot)
        if not update:
            return jsonify({"error": "Invalid update"}), 400
        
        # Process update in bot's event loop
        future = asyncio.run_coroutine_threadsafe(
            telegram_app.process_update(update), 
            bot_loop
        )
        
        # Don't block Flask
        try:
            future.result(timeout=0.1)
        except concurrent.futures.TimeoutError:
            pass
        except Exception as e:
            logger.error(f"Update processing error: {e}")
        
        return jsonify({"status": "ok"})
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/set_webhook", methods=["POST", "GET"])
def set_webhook():
    try:
        if not telegram_app or not bot_loop:
            return jsonify({"error": "Bot not initialized"}), 500
            
        webhook_url = f"{PUBLIC_URL}/webhook"
        
        future = asyncio.run_coroutine_threadsafe(
            telegram_app.bot.set_webhook(url=webhook_url, drop_pending_updates=True),
            bot_loop
        )
        
        success = future.result(timeout=10)
        if success:
            return jsonify({"status": "Paragraph-preserving webhook set successfully", "url": webhook_url})
        else:
            return jsonify({"error": "Failed to set webhook"}), 400
            
    except Exception as e:
        logger.error(f"Set webhook error: {e}")
        return jsonify({"error": str(e)}), 500

# -------------------- Main --------------------
def main():
    logger.info("🚀 Starting Paragraph-Preserving Telegram Translator Bot...")
    
    try:
        # Initialize bot in separate thread
        run_bot_in_thread()
        
        # Start Flask server
        logger.info(f"🌐 Starting Flask server on 0.0.0.0:{PORT}")
        app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
        
    except Exception as e:
        logger.error(f"❌ Startup failed: {e}")
        raise

if __name__ == "__main__":
    main()
