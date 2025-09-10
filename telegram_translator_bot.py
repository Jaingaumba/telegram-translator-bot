import os
import re
import logging
import threading
import asyncio
from typing import List, Dict, Optional
from flask import Flask, request, jsonify
import concurrent.futures

from deep_translator import GoogleTranslator
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
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

# Enhanced translation constants
TRANSLATE_CHUNK_SIZE = 1500    # Optimal chunk size for translation quality
CONTEXT_OVERLAP = 200          # Overlap between chunks for context preservation
MIN_TRANSLATE_LENGTH = 10      # Minimum message length to show translate button
MAX_BUTTON_TEXT_LENGTH = 100   # Max chars to show in button

# Modes
MODE_AUTO = "auto"
MODE_TO_UK = "to_uk" 
MODE_TO_EN = "to_en"

# Global variables
chat_modes = {}
telegram_app = None
bot_loop = None
message_cache = {}  # Cache original messages and translations

# Enhanced language detection regex
UA_CYRILLIC_RE = re.compile(r"[А-Яа-яІіЇїЄєҐґ]")

# Flask app
app = Flask(__name__)

# -------------------- Enhanced Translation Utilities --------------------
def detect_direction(text: str) -> str:
    """Enhanced direction detection"""
    return MODE_TO_EN if UA_CYRILLIC_RE.search(text) else MODE_TO_UK

def get_language_info(direction: str) -> tuple:
    """Get language information for direction"""
    if direction == MODE_TO_EN:
        return "uk", "en", "🇺🇦→🇺🇸", "Ukrainian → English"
    else:
        return "en", "uk", "🇺🇸→🇺🇦", "English → Ukrainian"

def enhanced_sentence_split(text: str) -> List[str]:
    """
    Advanced sentence splitting that handles multiple languages and edge cases
    """
    # Enhanced regex for sentence boundaries
    sentence_pattern = r'(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=[.!?])\s+(?=[A-ZА-ЯІЇЄҐ])'
    
    # Split by sentence boundaries
    sentences = re.split(sentence_pattern, text.strip())
    
    # Clean up and filter empty sentences
    cleaned_sentences = []
    for sentence in sentences:
        sentence = sentence.strip()
        if sentence and len(sentence) > 1:
            cleaned_sentences.append(sentence)
    
    return cleaned_sentences if cleaned_sentences else [text]

def create_context_aware_chunks(text: str, chunk_size: int = TRANSLATE_CHUNK_SIZE, overlap: int = CONTEXT_OVERLAP) -> List[str]:
    """
    Enhanced chunking algorithm that preserves sentence boundaries and maintains context overlap
    """
    if len(text) <= chunk_size:
        return [text]

    # Split into sentences using enhanced algorithm
    sentences = enhanced_sentence_split(text)
    
    if not sentences:
        return [text]
    
    chunks = []
    current_chunk = ""
    overlap_sentences = []
    
    i = 0
    while i < len(sentences):
        sentence = sentences[i]
        
        # Calculate potential chunk size with current sentence
        potential_chunk = current_chunk
        if potential_chunk and not potential_chunk.endswith(' '):
            potential_chunk += " "
        potential_chunk += sentence
        
        # If adding this sentence exceeds chunk size
        if len(potential_chunk) > chunk_size and current_chunk:
            # Finalize current chunk
            chunks.append(current_chunk.strip())
            
            # Prepare overlap for next chunk
            # Take last few sentences that fit within overlap limit
            overlap_text = ""
            overlap_sentences = []
            
            # Work backwards from current position to build overlap
            j = i - 1
            temp_overlap = ""
            while j >= 0 and len(temp_overlap + " " + sentences[j]) <= overlap:
                temp_overlap = sentences[j] + " " + temp_overlap
                overlap_sentences.insert(0, sentences[j])
                j -= 1
            
            # Start new chunk with overlap + current sentence
            if temp_overlap.strip():
                current_chunk = temp_overlap.strip() + " " + sentence
            else:
                current_chunk = sentence
            
        else:
            # Add sentence to current chunk
            if current_chunk:
                current_chunk += " " + sentence
            else:
                current_chunk = sentence
        
        i += 1
    
    # Add final chunk if it has content
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    
    # Ensure we have at least one chunk
    return chunks if chunks else [text]

def enhanced_translate_with_context(text: str, source: str, target: str) -> str:
    """
    Enhanced translation with superior context preservation and sentence boundary respect
    """
    try:
        # Use enhanced context-aware chunking
        chunks = create_context_aware_chunks(text)
        translated_chunks = []
        
        logger.info(f"Translating text in {len(chunks)} context-aware chunks")
        
        for i, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            
            try:
                # Create translator with consistent settings
                translator = GoogleTranslator(source=source, target=target)
                
                # Translate the chunk
                result = translator.translate(chunk.strip())
                
                if result and result.strip():
                    translated_result = result.strip()
                    
                    # Clean up common translation artifacts
                    translated_result = re.sub(r'\s+', ' ', translated_result)  # Multiple spaces
                    translated_result = re.sub(r'\s+([.!?,:;])', r'\1', translated_result)  # Space before punctuation
                    
                    translated_chunks.append(translated_result)
                    logger.debug(f"Chunk {i+1}/{len(chunks)} translated successfully")
                else:
                    translated_chunks.append(chunk)
                    logger.warning(f"Chunk {i+1}/{len(chunks)} translation returned empty result")
                
            except Exception as e:
                logger.error(f"Translation error for chunk {i+1}/{len(chunks)}: {e}")
                translated_chunks.append(chunk)  # Fallback to original
        
        # Intelligent chunk joining
        if len(translated_chunks) == 1:
            final_translation = translated_chunks[0]
        else:
            # Join chunks with smart spacing
            final_translation = ""
            for i, chunk in enumerate(translated_chunks):
                if i == 0:
                    final_translation = chunk
                else:
                    # Determine if we need space between chunks
                    if (final_translation.endswith('.') or 
                        final_translation.endswith('!') or 
                        final_translation.endswith('?') or
                        final_translation.endswith('\n')):
                        final_translation += " " + chunk
                    else:
                        final_translation += " " + chunk
        
        # Final cleanup
        final_translation = re.sub(r'\s+', ' ', final_translation).strip()
        
        return final_translation if final_translation else text
        
    except Exception as e:
        logger.error(f"Enhanced translation error: {e}")
        return text

def should_add_translate_button(text: str) -> bool:
    """Determine if message should have translate button"""
    if not text or len(text.strip()) < MIN_TRANSLATE_LENGTH:
        return False
    
    if text.strip().startswith('/'):
        return False
    
    # Skip if message is mostly emojis/symbols
    letter_count = len(re.findall(r'[a-zA-Zа-яА-ЯіІїЇєЄґҐ]', text))
    if letter_count < len(text.strip()) * 0.3:
        return False
    
    return True

def truncate_for_button(text: str, max_length: int = MAX_BUTTON_TEXT_LENGTH) -> str:
    """Truncate text for button display"""
    if len(text) <= max_length:
        return text
    return text[:max_length-3] + "..."

def create_translate_button(message_id: int, direction: str) -> InlineKeyboardMarkup:
    """Create translate button"""
    source, target, flag_emoji, description = get_language_info(direction)
    
    callback_data = f"translate_{message_id}_{direction}"
    button_text = f"🔄 Translate {flag_emoji}"
    
    button = InlineKeyboardButton(
        text=button_text,
        callback_data=callback_data
    )
    
    return InlineKeyboardMarkup([[button]])

def create_translated_button(original_text: str, translated_text: str, message_id: int, direction: str) -> InlineKeyboardMarkup:
    """Create button showing translation with option to show original"""
    
    # Truncate translated text for button
    display_text = truncate_for_button(translated_text)
    
    source, target, flag_emoji, description = get_language_info(direction)
    
    callback_data = f"show_original_{message_id}_{direction}"
    button_text = f"✅ {display_text}"
    
    button = InlineKeyboardButton(
        text=button_text,
        callback_data=callback_data
    )
    
    return InlineKeyboardMarkup([[button]])

def create_original_button(original_text: str, message_id: int, direction: str) -> InlineKeyboardMarkup:
    """Create button showing original with option to show translation"""
    
    # Truncate original text for button
    display_text = truncate_for_button(original_text)
    
    source, target, flag_emoji, description = get_language_info(direction)
    reverse_flag = "🇺🇦→🇺🇸" if flag_emoji == "🇺🇸→🇺🇦" else "🇺🇸→🇺🇦"
    
    callback_data = f"show_translation_{message_id}_{direction}"
    button_text = f"📝 {display_text}"
    
    button = InlineKeyboardButton(
        text=button_text,
        callback_data=callback_data
    )
    
    return InlineKeyboardMarkup([[button]])

# -------------------- Handlers --------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    try:
        chat_id = update.effective_chat.id
        chat_modes[chat_id] = MODE_AUTO
        
        welcome_text = (
            "🔄 **Enhanced Context-Aware Translator**\n\n"
            "**New Features:**\n"
            "✅ Enhanced context-aware translation\n"
            "✅ Sentence boundary preservation\n"
            "✅ Smart chunking with overlap\n"
            "✅ Inline translation buttons (no chat clutter!)\n\n"
            "**How it works:**\n"
            "1️⃣ Send any text message\n"
            "2️⃣ Click the 🔄 Translate button\n"
            "3️⃣ Button shows translation inline\n"
            "4️⃣ Click again to toggle back to original\n\n"
            "**Commands:**\n"
            "• /auto - Auto-detect language (default)\n"
            "• /to_en - Force Ukrainian → English\n"
            "• /to_uk - Force English → Ukrainian\n"
            "• /help - Show help\n\n"
            "**Try it:** Send any message! 🚀"
        )
        
        await update.message.reply_text(welcome_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        await update.message.reply_text("Hello! I'm an enhanced context-aware translation bot!")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    try:
        help_text = (
            "**Enhanced Translation Bot Help**\n\n"
            "**Features:**\n"
            "🧠 **Smart Context-Aware Translation**\n"
            "• Preserves sentence boundaries\n"
            "• Uses context overlap for coherence\n"
            "• Optimal chunk sizing (1500 chars)\n"
            "• Enhanced sentence detection\n\n"
            "🎯 **Inline Translation Interface**\n"
            "• Click 🔄 Translate button\n"
            "• Button shows translation inline\n"
            "• Toggle between original/translated\n"
            "• No chat clutter or spam\n\n"
            "**Commands:**\n"
            "/auto - Auto-detect language\n"
            "/to_en - Force Ukrainian → English\n"
            "/to_uk - Force English → Ukrainian\n\n"
            "**Works perfectly in groups and private chats!**"
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in help command: {e}")
        await update.message.reply_text("Available commands: /auto /to_en /to_uk /help")

async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set mode to auto-detect"""
    try:
        chat_modes[update.effective_chat.id] = MODE_AUTO
        await update.message.reply_text("✅ Mode: **Auto-detect language** 🔄", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in auto command: {e}")
        await update.message.reply_text("✅ Mode set to auto-detect")

async def to_en_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set mode to Ukrainian -> English"""
    try:
        chat_modes[update.effective_chat.id] = MODE_TO_EN
        await update.message.reply_text("✅ Mode: **Ukrainian → English** 🇺🇦→🇺🇸", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in to_en command: {e}")
        await update.message.reply_text("✅ Mode set to Ukrainian → English")

async def to_uk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set mode to English -> Ukrainian"""
    try:
        chat_modes[update.effective_chat.id] = MODE_TO_UK
        await update.message.reply_text("✅ Mode: **English → Ukrainian** 🇺🇸→🇺🇦", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in to_uk command: {e}")
        await update.message.reply_text("✅ Mode set to English → Ukrainian")

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages and add translate buttons"""
    try:
        if not update.message or not update.message.text:
            return
        
        text = update.message.text.strip()
        
        # Skip commands and messages that shouldn't be translated
        if text.startswith("/") or not should_add_translate_button(text):
            return

        message_id = update.message.message_id
        chat_id = update.effective_chat.id
        
        # Store message for translation
        cache_key = f"{chat_id}_{message_id}"
        message_cache[cache_key] = {
            'original_text': text,
            'translated_text': None,
            'user_id': update.message.from_user.id,
            'username': update.message.from_user.username or update.message.from_user.first_name,
            'direction': None,
            'state': 'original'  # 'original' or 'translated'
        }
        
        # Determine translation direction
        mode = chat_modes.get(chat_id, MODE_AUTO)
        if mode == MODE_AUTO:
            direction = detect_direction(text)
        else:
            direction = mode
        
        message_cache[cache_key]['direction'] = direction
        
        # Create translate button
        keyboard = create_translate_button(message_id, direction)
        
        # Add translate button as reply to original message
        await context.bot.send_message(
            chat_id=chat_id,
            text="👆 Click to translate",
            reply_to_message_id=message_id,
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Error handling text message: {e}")

async def handle_translate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle translate button clicks"""
    try:
        query = update.callback_query
        await query.answer("Translating... 🔄")
        
        # Parse callback data
        callback_data = query.data
        if not callback_data.startswith("translate_"):
            return
        
        parts = callback_data.split("_")
        if len(parts) != 3:
            await query.answer("Invalid request", show_alert=True)
            return
        
        message_id = int(parts[1])
        direction = parts[2]
        
        chat_id = query.message.chat.id
        
        # Get cached message
        cache_key = f"{chat_id}_{message_id}"
        if cache_key not in message_cache:
            await query.answer("Message not found or expired", show_alert=True)
            return
        
        cached_msg = message_cache[cache_key]
        original_text = cached_msg['original_text']
        
        # Get language info
        source, target, flag_emoji, description = get_language_info(direction)
        
        # Show typing action
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        # Perform enhanced translation
        with concurrent.futures.ThreadPoolExecutor() as executor:
            loop = asyncio.get_event_loop()
            translated_text = await loop.run_in_executor(
                executor, enhanced_translate_with_context, original_text, source, target
            )
        
        if not translated_text or translated_text == original_text:
            await query.answer("Translation failed. Please try again.", show_alert=True)
            return
        
        # Store translation in cache
        cached_msg['translated_text'] = translated_text
        cached_msg['state'] = 'translated'
        
        # Update button to show translation
        new_keyboard = create_translated_button(original_text, translated_text, message_id, direction)
        
        await query.edit_message_reply_markup(reply_markup=new_keyboard)
        
        logger.info(f"Successfully translated message {message_id} from {source} to {target}")
        
    except Exception as e:
        logger.error(f"Translation callback error: {e}")
        await query.answer("Translation error. Please try again.", show_alert=True)

async def handle_show_original_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle showing original text"""
    try:
        query = update.callback_query
        await query.answer("Showing original text 📝")
        
        # Parse callback data
        callback_data = query.data
        parts = callback_data.split("_")
        if len(parts) != 4:
            return
        
        message_id = int(parts[2])
        direction = parts[3]
        chat_id = query.message.chat.id
        
        # Get cached message
        cache_key = f"{chat_id}_{message_id}"
        if cache_key not in message_cache:
            await query.answer("Message not found", show_alert=True)
            return
        
        cached_msg = message_cache[cache_key]
        original_text = cached_msg['original_text']
        
        # Update state
        cached_msg['state'] = 'original'
        
        # Update button to show original with option to translate again
        new_keyboard = create_original_button(original_text, message_id, direction)
        
        await query.edit_message_reply_markup(reply_markup=new_keyboard)
        
    except Exception as e:
        logger.error(f"Show original callback error: {e}")

async def handle_show_translation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle showing translation again"""
    try:
        query = update.callback_query
        await query.answer("Showing translation 🔄")
        
        # Parse callback data
        callback_data = query.data
        parts = callback_data.split("_")
        if len(parts) != 4:
            return
        
        message_id = int(parts[2])
        direction = parts[3]
        chat_id = query.message.chat.id
        
        # Get cached message
        cache_key = f"{chat_id}_{message_id}"
        if cache_key not in message_cache:
            await query.answer("Message not found", show_alert=True)
            return
        
        cached_msg = message_cache[cache_key]
        original_text = cached_msg['original_text']
        translated_text = cached_msg['translated_text']
        
        if not translated_text:
            await query.answer("Translation not available", show_alert=True)
            return
        
        # Update state
        cached_msg['state'] = 'translated'
        
        # Update button to show translation
        new_keyboard = create_translated_button(original_text, translated_text, message_id, direction)
        
        await query.edit_message_reply_markup(reply_markup=new_keyboard)
        
    except Exception as e:
        logger.error(f"Show translation callback error: {e}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors"""
    logger.error("Exception while handling an update:", exc_info=context.error)

# -------------------- Bot Setup --------------------
def create_application() -> Application:
    """Create Telegram application"""
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
    """Setup bot with handlers"""
    global telegram_app
    
    telegram_app = create_application()
    
    # Add handlers
    telegram_app.add_handler(CommandHandler("start", start_cmd))
    telegram_app.add_handler(CommandHandler("help", help_cmd))
    telegram_app.add_handler(CommandHandler("auto", auto_cmd))
    telegram_app.add_handler(CommandHandler("to_en", to_en_cmd))
    telegram_app.add_handler(CommandHandler("to_uk", to_uk_cmd))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    telegram_app.add_handler(CallbackQueryHandler(handle_translate_callback, pattern="^translate_"))
    telegram_app.add_handler(CallbackQueryHandler(handle_show_original_callback, pattern="^show_original_"))
    telegram_app.add_handler(CallbackQueryHandler(handle_show_translation_callback, pattern="^show_translation_"))
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
    
    logger.info(f"✅ Enhanced context-aware translator webhook set to: {webhook_url}")
    return telegram_app

def run_bot_in_thread():
    """Run bot in separate thread"""
    global bot_loop
    
    def bot_thread():
        global bot_loop
        bot_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(bot_loop)
        
        try:
            bot_loop.run_until_complete(setup_bot())
            logger.info("✅ Enhanced context-aware bot initialized successfully")
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
    
    logger.info("✅ Enhanced bot thread started successfully")

# -------------------- Flask Routes --------------------
@app.route("/", methods=["GET"])
def index():
    """Health check endpoint"""
    return jsonify({
        "status": "Enhanced Context-Aware Telegram Translator Bot is running!",
        "features": [
            "context_aware_translation",
            "sentence_boundary_preservation", 
            "smart_chunking_with_overlap",
            "inline_translation_buttons",
            "toggle_original_translated"
        ],
        "translation_settings": {
            "chunk_size": TRANSLATE_CHUNK_SIZE,
            "context_overlap": CONTEXT_OVERLAP,
            "min_translate_length": MIN_TRANSLATE_LENGTH
        },
        "webhook_url": f"{PUBLIC_URL}/webhook",
        "bot_initialized": telegram_app is not None
    })

@app.route("/webhook", methods=["POST"])
def webhook():
    """Handle Telegram webhooks"""
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
        
        # Don't wait for completion to avoid blocking Flask
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

# -------------------- Main --------------------
def main():
    """Main function"""
    logger.info("🚀 Starting Enhanced Context-Aware Telegram Translator Bot...")
    
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
