import os
import re
import logging
import threading
import asyncio
from typing import List, Tuple
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
TRANSLATE_CHUNK = 1500  # Reduced chunk size for better quality
CONTEXT_OVERLAP = 200   # Overlap between chunks for context
MODE_AUTO = "auto"
MODE_TO_UK = "to_uk"
MODE_TO_EN = "to_en"

# Global variables
chat_modes = {}
telegram_app = None
bot_loop = None

# Enhanced language detection
UA_CYRILLIC_RE = re.compile(r"[А-Яа-яІіЇїЄєҐґ]")
# Improved sentence boundary detection
SENTENCE_END_RE = re.compile(r'(?<=[.!?])\s+(?=[A-ZА-ЯІЇЄҐ])')
# Additional patterns for better sentence detection
SENTENCE_BOUNDARIES = re.compile(r'(?<=[.!?])\s+|(?<=\.\.\.)\s+|(?<=[.!?]")\s+|(?<=[.!?]\')\s+|(?<=[.!?]\u2019)\s+')

# Flask app
app = Flask(__name__)

# -------------------- Enhanced Utilities --------------------
def detect_direction(text: str) -> str:
    return MODE_TO_EN if UA_CYRILLIC_RE.search(text) else MODE_TO_UK

def split_into_sentences(text: str) -> List[str]:
    """Split text into sentences using improved regex patterns"""
    # Handle special cases first
    text = re.sub(r'\s+', ' ', text.strip())  # Normalize whitespace
    
    # Split by sentence boundaries
    sentences = SENTENCE_BOUNDARIES.split(text)
    
    # Clean and filter sentences
    cleaned_sentences = []
    for sentence in sentences:
        sentence = sentence.strip()
        if sentence and len(sentence) > 1:  # Skip very short fragments
            cleaned_sentences.append(sentence)
    
    return cleaned_sentences if cleaned_sentences else [text]

def smart_chunk_text(text: str, max_chunk_size: int = TRANSLATE_CHUNK, overlap_size: int = CONTEXT_OVERLAP) -> List[Tuple[str, str]]:
    """
    Enhanced chunking with context overlap and sentence preservation
    Returns list of tuples: (chunk_text, context_for_next)
    """
    if len(text) <= max_chunk_size:
        return [(text, "")]
    
    sentences = split_into_sentences(text)
    chunks = []
    current_chunk = ""
    context_buffer = ""
    
    i = 0
    while i < len(sentences):
        sentence = sentences[i]
        
        # Check if adding this sentence would exceed limit
        potential_chunk = current_chunk
        if context_buffer and not current_chunk:
            potential_chunk = context_buffer
        
        if potential_chunk:
            potential_chunk += " " + sentence
        else:
            potential_chunk = sentence
        
        if len(potential_chunk) <= max_chunk_size:
            # Add sentence to current chunk
            if current_chunk:
                current_chunk += " " + sentence
            else:
                current_chunk = context_buffer + (" " + sentence if context_buffer else sentence)
            i += 1
        else:
            # Current chunk is full, save it and start new one
            if current_chunk:
                # Extract context for next chunk (last few sentences or chars)
                context_for_next = extract_context(current_chunk, overlap_size)
                chunks.append((current_chunk.strip(), context_for_next))
                context_buffer = context_for_next
                current_chunk = ""
            else:
                # Single sentence is too long, split it by words
                word_chunks = split_long_sentence(sentence, max_chunk_size, overlap_size)
                for j, (word_chunk, word_context) in enumerate(word_chunks):
                    if context_buffer and j == 0:
                        word_chunk = context_buffer + " " + word_chunk
                    chunks.append((word_chunk.strip(), word_context))
                    context_buffer = word_context
                i += 1
    
    # Add remaining chunk
    if current_chunk:
        chunks.append((current_chunk.strip(), ""))
    
    # Clean up empty chunks
    return [(chunk, context) for chunk, context in chunks if chunk.strip()]

def extract_context(text: str, max_context_size: int) -> str:
    """Extract context from the end of text for overlap"""
    if len(text) <= max_context_size:
        return text
    
    # Try to get complete sentences for context
    sentences = split_into_sentences(text)
    context = ""
    
    # Start from the end and work backwards
    for sentence in reversed(sentences):
        potential_context = sentence + (" " + context if context else "")
        if len(potential_context) <= max_context_size:
            context = potential_context
        else:
            break
    
    # If no complete sentences fit, take the last N characters
    if not context:
        context = text[-max_context_size:].strip()
        # Try to start from a word boundary
        space_idx = context.find(' ')
        if space_idx > 0:
            context = context[space_idx:].strip()
    
    return context

def split_long_sentence(sentence: str, max_size: int, overlap_size: int) -> List[Tuple[str, str]]:
    """Split a long sentence by words when it exceeds max_size"""
    words = sentence.split()
    chunks = []
    current_chunk = ""
    
    for word in words:
        potential_chunk = current_chunk + (" " + word if current_chunk else word)
        
        if len(potential_chunk) <= max_size:
            current_chunk = potential_chunk
        else:
            if current_chunk:
                context = extract_context(current_chunk, overlap_size)
                chunks.append((current_chunk, context))
                current_chunk = word
            else:
                # Single word is too long, just include it
                chunks.append((word, ""))
    
    if current_chunk:
        chunks.append((current_chunk, ""))
    
    return chunks

def chunk_text(text: str, limit: int) -> List[str]:
    """Legacy chunking function for Telegram message splitting"""
    if len(text) <= limit:
        return [text]

    chunks = []
    current = ""

    def push():
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    paragraphs = text.split('\n\n')
    
    for para in paragraphs:
        if len(current) + len(para) + 2 <= limit:
            current += ('\n\n' + para) if current else para
        else:
            push()
            
            if len(para) > limit:
                sentences = split_into_sentences(para)
                for sentence in sentences:
                    if len(current) + len(sentence) + 1 <= limit:
                        current += (' ' + sentence) if current else sentence
                    else:
                        push()
                        if len(sentence) <= limit:
                            current = sentence
                        else:
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

def translate_text_sync(text: str, direction: str) -> str:
    """Enhanced synchronous translation with context-aware chunking"""
    try:
        if direction == MODE_TO_UK:
            source, target = "en", "uk"
        elif direction == MODE_TO_EN:
            source, target = "uk", "en"
        else:
            source, target = ("uk", "en") if UA_CYRILLIC_RE.search(text) else ("en", "uk")

        # Use smart chunking for better context preservation
        chunk_data = smart_chunk_text(text, TRANSLATE_CHUNK, CONTEXT_OVERLAP)
        translated_chunks = []

        logger.info(f"Translating {len(chunk_data)} chunks with context overlap")

        for i, (chunk, context) in enumerate(chunk_data):
            if not chunk.strip():
                continue
            
            try:
                # For chunks with context, include it in translation for better coherence
                text_to_translate = chunk
                if context and i > 0:  # Don't add context to first chunk
                    # Add context marker to help translator understand context
                    text_to_translate = f"[Context: {context}] {chunk}"
                    logger.debug(f"Chunk {i+1} with context: {len(context)} chars")
                
                translator = GoogleTranslator(source=source, target=target)
                result = translator.translate(text_to_translate)
                
                if result and result.strip():
                    # If we added context, try to remove the translated context part
                    if context and i > 0 and result.startswith('['):
                        # Find the end of context marker and remove it
                        context_end = result.find('] ')
                        if context_end != -1:
                            result = result[context_end + 2:].strip()
                    
                    translated_chunks.append(result)
                else:
                    translated_chunks.append(chunk)
                
                # Small delay between chunks to avoid rate limiting
                if i < len(chunk_data) - 1:
                    import time
                    time.sleep(0.1)
                    
            except Exception as e:
                logger.error(f"Translation error for chunk {i+1}: {e}")
                translated_chunks.append(chunk)

        # Join chunks with proper spacing
        final_result = []
        for chunk in translated_chunks:
            if chunk.strip():
                final_result.append(chunk.strip())
        
        return ' '.join(final_result) or text
        
    except Exception as e:
        logger.error(f"Translation error: {e}")
        return text

async def send_long_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Send long text by chunking"""
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
    try:
        chat_id = update.effective_chat.id
        chat_modes[chat_id] = MODE_AUTO
        
        welcome_text = (
            "🔄 **Enhanced Telegram Translator Bot**\n\n"
            "I translate between English and Ukrainian with improved context awareness!\n\n"
            "**New Features:**\n"
            "• 🧠 Smart context-aware chunking\n"
            "• 📝 Sentence boundary preservation\n"
            "• 🔗 Context overlap for better coherence\n"
            "• 🎯 Enhanced translation quality\n\n"
            "**How it works:**\n"
            "• Send any text - I'll auto-detect and translate\n"
            "• Latin text → Ukrainian\n"
            "• Cyrillic text → English\n\n"
            "**Commands:**\n"
            "• /auto - Auto-detect language (default)\n"
            "• /to_en - Force Ukrainian → English\n"
            "• /to_uk - Force English → Ukrainian\n"
            "• /help - Show help\n\n"
            "Ready to translate with enhanced context! 🚀"
        )
        
        await update.message.reply_text(welcome_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        await update.message.reply_text("Hello! I'm an enhanced translation bot. Send me text to translate!")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        help_text = (
            "**Enhanced Translation Bot Commands:**\n\n"
            "/auto – Auto-detect language per message\n"
            "/to_en – Force Ukrainian → English\n"
            "/to_uk – Force English → Ukrainian\n"
            "/help – Show this help\n\n"
            "**New Features:**\n"
            "• Smart chunking preserves sentence boundaries\n"
            "• Context overlap for better translation coherence\n"
            "• Improved handling of long texts\n\n"
            "Just send any text and I'll translate it with enhanced context awareness!"
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in help command: {e}")
        await update.message.reply_text("Available commands: /auto /to_en /to_uk /help")

async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_modes[update.effective_chat.id] = MODE_AUTO
        await update.message.reply_text("✅ Mode set to **auto-detect** with context awareness", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in auto command: {e}")
        await update.message.reply_text("✅ Mode set to auto-detect")

async def to_en_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_modes[update.effective_chat.id] = MODE_TO_EN
        await update.message.reply_text("✅ Mode set to **Ukrainian → English** with context preservation", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in to_en command: {e}")
        await update.message.reply_text("✅ Mode set to Ukrainian → English")

async def to_uk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_modes[update.effective_chat.id] = MODE_TO_UK
        await update.message.reply_text("✅ Mode set to **English → Ukrainian** with context preservation", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in to_uk command: {e}")
        await update.message.reply_text("✅ Mode set to English → Ukrainian")

async def translate_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages and translate them with enhanced context awareness"""
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
        
        # Log translation details for debugging
        logger.info(f"Translating {len(text)} chars with {len(text.split(chr(10)+chr(10)))} paragraphs in mode '{mode}' -> '{direction}'")
        
        # Use enhanced thread pool executor for better performance
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            loop = asyncio.get_event_loop()
            translated = await loop.run_in_executor(executor, translate_text_sync, text, direction)
        
        if not translated or translated == text:
            if mode == MODE_AUTO:
                await update.message.reply_text("🤔 I couldn't detect the language. Try /to_en or /to_uk")
            else:
                await update.message.reply_text("⚠️ Translation failed. Please try again.")
            return
        
        logger.info(f"Translation completed: {len(translated)} chars output")
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
    
    logger.info(f"✅ Enhanced webhook set to: {webhook_url}")
    return telegram_app

def run_bot_in_thread():
    global bot_loop
    
    def bot_thread():
        global bot_loop
        bot_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(bot_loop)
        
        try:
            bot_loop.run_until_complete(setup_bot())
            logger.info("✅ Enhanced bot initialized successfully")
            # Keep the loop running
            bot_loop.run_forever()
        except Exception as e:
            logger.error(f"❌ Enhanced bot initialization failed: {e}")
            raise
    
    bot_thread_obj = threading.Thread(target=bot_thread, daemon=True)
    bot_thread_obj.start()
    
    # Wait for initialization
    import time
    time.sleep(3)
    
    if not bot_loop or not telegram_app:
        raise RuntimeError("Enhanced bot initialization failed")
    
    logger.info("✅ Enhanced bot thread started successfully")

# -------------------- Flask Routes --------------------
@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "status": "Enhanced Telegram Translator Bot is running!",
        "webhook_url": f"{PUBLIC_URL}/webhook",
        "bot_initialized": telegram_app is not None,
        "features": [
            "Context-aware chunking",
            "Sentence boundary preservation", 
            "Smart overlap between chunks",
            "Enhanced translation quality"
        ]
    })

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        if not telegram_app or not bot_loop:
            logger.error("Enhanced bot not initialized")
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
        
        # Wait briefly for processing to start but don't block Flask
        try:
            future.result(timeout=0.1)
        except concurrent.futures.TimeoutError:
            # This is fine, processing will continue in background
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
            return jsonify({"error": "Enhanced bot not initialized"}), 500
            
        webhook_url = f"{PUBLIC_URL}/webhook"
        
        future = asyncio.run_coroutine_threadsafe(
            telegram_app.bot.set_webhook(url=webhook_url, drop_pending_updates=True),
            bot_loop
        )
        
        success = future.result(timeout=10)
        if success:
            return jsonify({"status": "Enhanced webhook set successfully", "url": webhook_url})
        else:
            return jsonify({"error": "Failed to set enhanced webhook"}), 400
            
    except Exception as e:
        logger.error(f"Set webhook error: {e}")
        return jsonify({"error": str(e)}), 500

# -------------------- Main --------------------
def main():
    logger.info("🚀 Starting Enhanced Telegram Translator Bot...")
    
    try:
        # Initialize bot in separate thread
        run_bot_in_thread()
        
        # Start Flask server
        logger.info(f"🌐 Starting Flask server on 0.0.0.0:{PORT}")
        app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
        
    except Exception as e:
        logger.error(f"❌ Enhanced startup failed: {e}")
        raise

if __name__ == "__main__":
    main()
