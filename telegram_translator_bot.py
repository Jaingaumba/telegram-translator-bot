import os
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import requests
import re
from flask import Flask
import threading

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# User settings storage
user_settings = {}

# Initialize Flask app
app = Flask(__name__)

# Language detection function
def detect_language(text):
    """Simple but effective language detection for Ukrainian and English"""
    try:
        ukrainian_chars = set('абвгґдеєжзиіїйклмнопрстуфхцчшщьюяАБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯ')
        english_chars = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ')
        
        uk_count = sum(1 for char in text if char in ukrainian_chars)
        en_count = sum(1 for char in text if char in english_chars)
        
        if uk_count > en_count and uk_count > 0:
            return 'uk'
        elif en_count > uk_count and en_count > 0:
            return 'en'
        else:
            return 'unknown'
    except Exception as e:
        logger.error(f"Language detection error: {e}")
        return 'unknown'

# FIXED: Enhanced translation function that handles long text properly
def translate_text(text, target_lang):
    """Translate text using Google Translate free web API - FIXED for long text"""
    try:
        cleaned_text = re.sub(r'\s+', ' ', text.strip())
        if not cleaned_text or len(cleaned_text) < 3:
            return text
            
        # Split long text into chunks to avoid truncation
        max_length = 4000  # Google Translate limit
        if len(cleaned_text) <= max_length:
            chunks = [cleaned_text]
        else:
            # Split by sentences to maintain context
            sentences = re.split(r'[.!?]+', cleaned_text)
            chunks = []
            current_chunk = ""
            
            for sentence in sentences:
                if len(current_chunk + sentence) <= max_length:
                    current_chunk += sentence + ". "
                else:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = sentence + ". "
            
            if current_chunk:
                chunks.append(current_chunk.strip())
        
        translated_chunks = []
        
        for chunk in chunks:
            if not chunk.strip():
                continue
                
            url = "https://translate.googleapis.com/translate_a/single"
            
            params = {
                'client': 'gtx',
                'sl': 'auto',
                'tl': target_lang,
                'dt': 't',
                'q': chunk
            }
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=15)
            
            if response.status_code == 200:
                result = response.json()
                # FIXED: Proper extraction of complete translated text
                if result and len(result) > 0 and result[0]:
                    chunk_translation = ""
                    for segment in result[0]:
                        if segment and segment[0]:
                            chunk_translation += segment[0]
                    
                    if chunk_translation:
                        translated_chunks.append(chunk_translation)
                    else:
                        translated_chunks.append(chunk)
                else:
                    translated_chunks.append(chunk)
            else:
                translated_chunks.append(chunk)
        
        # Join all translated chunks
        final_translation = " ".join(translated_chunks).strip()
        return final_translation if final_translation else text
        
    except Exception as e:
        logger.error(f"Translation error: {e}")
        return text

# User settings functions
def get_user_settings(user_id):
    return user_settings.get(str(user_id), {
        'auto_translate': True,
        'translate_own_messages': True
    })

def update_user_settings(user_id, new_settings):
    user_id = str(user_id)
    if user_id not in user_settings:
        user_settings[user_id] = get_user_settings(user_id)
    user_settings[user_id].update(new_settings)

# Bot command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) not in user_settings:
        user_settings[str(user_id)] = get_user_settings(user_id)
    
    welcome_text = """🌍 **Telegram Auto-Translator Bot**

Welcome! I automatically translate between English and Ukrainian.

**Features:**
• Ukrainian → English (for you)
• English → Ukrainian (for your colleagues)
• Works with long messages and complete paragraphs
• Smart language detection
• Works in groups and private chats

**Commands:**
/start - Show this welcome message
/toggle - Turn auto-translation on/off
/help - Get detailed help

Ready to start translating! 🚀"""

    keyboard = [
        [InlineKeyboardButton("❓ Help", callback_data="help")],
        [InlineKeyboardButton("⚙️ Toggle Translation", callback_data="toggle")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        await update.message.reply_text("Bot started successfully!")

async def toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        settings = get_user_settings(user_id)
        new_status = not settings['auto_translate']
        update_user_settings(user_id, {'auto_translate': new_status})
        status_text = "enabled ✅" if new_status else "disabled ❌"
        await update.message.reply_text(f"Auto-translation {status_text}")
    except Exception as e:
        logger.error(f"Error in toggle command: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """❓ **Help & Instructions**

**Setup:**
1. Add this bot to your group chat
2. Make sure the bot can read messages
3. Start chatting normally!

**Translation Features:**
• Handles long messages and complete paragraphs
• Maintains formatting and context
• Ukrainian ↔ English translation
• Smart language detection
• Works in real-time

**Commands:**
• /start - Welcome message and setup
• /toggle - Turn translation on/off
• /help - Show this help

**Tips:**
• Bot translates complete messages, not just first sentences
• Works best with proper punctuation
• Supports messages up to Telegram's character limit
• Translation appears as replies to original messages

Ready to communicate seamlessly! 🌍"""

    try:
        await update.message.reply_text(help_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in help command: {e}")
        await update.message.reply_text("Use /start to begin, /toggle to turn translation on/off")

# Button callback handlers
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        if query.data == "help":
            await help_callback(query, context)
        elif query.data == "toggle":
            await toggle_callback(query, context)
        elif query.data == "back":
            await back_callback(query, context)
    except Exception as e:
        logger.error(f"Error in button callback: {e}")

async def help_callback(query, context):
    help_text = """❓ **Quick Help**

**How to use:**
1. Add me to your group chat
2. I automatically translate complete messages:
   - Ukrainian → English
   - English → Ukrainian
3. Use /toggle to turn translation on/off

**Features:**
• Translates full messages, not just first lines
• Maintains paragraph structure
• Works with long content

The bot works automatically - no setup needed!"""

    keyboard = [[InlineKeyboardButton("🔙 Back to Main", callback_data="back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(help_text, reply_markup=reply_markup, parse_mode='Markdown')

async def toggle_callback(query, context):
    try:
        user_id = query.from_user.id
        settings = get_user_settings(user_id)
        new_status = not settings['auto_translate']
        update_user_settings(user_id, {'auto_translate': new_status})
        status_text = "enabled ✅" if new_status else "disabled ❌"
        message_text = f"Auto-translation {status_text}"
        keyboard = [[InlineKeyboardButton("🔙 Back to Main", callback_data="back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message_text, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error in toggle callback: {e}")

async def back_callback(query, context):
    welcome_text = """🌍 **Telegram Auto-Translator Bot**

Welcome! I automatically translate between English and Ukrainian.

Ready to start translating! 🚀"""
    
    keyboard = [
        [InlineKeyboardButton("❓ Help", callback_data="help")],
        [InlineKeyboardButton("⚙️ Toggle Translation", callback_data="toggle")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

# FIXED: Enhanced message handler that processes complete messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.message.text:
            return
        
        text = update.message.text
        user_id = update.effective_user.id
        
        if text.startswith('/'):
            return
        
        if len(text.strip()) < 3:
            return
        
        settings = get_user_settings(user_id)
        if not settings['auto_translate']:
            return
        
        detected_lang = detect_language(text)
        target_lang = None
        
        if detected_lang == 'uk':
            target_lang = 'en'
        elif detected_lang == 'en':
            target_lang = 'uk'
        else:
            target_lang = 'en'  # Default to English for unknown languages
        
        if not target_lang:
            return
            
        # FIXED: Use the enhanced translation function
        translated_text = translate_text(text, target_lang)
        
        if translated_text and translated_text.lower().strip() != text.lower().strip():
            lang_names = {'en': 'English', 'uk': 'Ukrainian', 'unknown': 'Auto'}
            from_lang = lang_names.get(detected_lang, detected_lang.upper())
            to_lang = lang_names.get(target_lang, target_lang.upper())
            
            # Split long translations if needed for Telegram's message limit
            max_telegram_length = 4000
            if len(translated_text) <= max_telegram_length:
                translation_message = f"🌍 **{from_lang} → {to_lang}**\n{translated_text}"
                
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=translation_message,
                    parse_mode='Markdown',
                    reply_to_message_id=update.message.message_id
                )
            else:
                # Send long translations in multiple parts
                header = f"🌍 **{from_lang} → {to_lang}**\n"
                parts = []
                remaining_text = translated_text
                
                while remaining_text:
                    if len(remaining_text) <= (max_telegram_length - len(header)):
                        parts.append(header + remaining_text)
                        break
                    else:
                        # Find a good break point (sentence end)
                        break_point = max_telegram_length - len(header) - 50
                        break_pos = remaining_text.rfind('. ', 0, break_point)
                        if break_pos == -1:
                            break_pos = remaining_text.rfind(' ', 0, break_point)
                        if break_pos == -1:
                            break_pos = break_point
                        
                        parts.append(header + remaining_text[:break_pos + 1])
                        remaining_text = remaining_text[break_pos + 1:].strip()
                        header = "🌍 **(continued)**\n"  # For subsequent parts
                
                # Send all parts
                for i, part in enumerate(parts):
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=part,
                        parse_mode='Markdown',
                        reply_to_message_id=update.message.message_id if i == 0 else None
                    )
                    
                    # Small delay between parts to avoid rate limiting
                    if i < len(parts) - 1:
                        await asyncio.sleep(0.5)
    
    except Exception as e:
        logger.error(f"Error handling message: {e}")

# Flask Routes for Render
@app.route('/')
def home():
    return "🌍 Telegram Translation Bot is running! ✅"

@app.route('/health')
def health():
    return "OK", 200

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# FIXED: Proper async bot initialization and polling
async def run_bot():
    """Run the bot with proper async handling"""
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    
    if not BOT_TOKEN:
        logger.error("❌ ERROR: BOT_TOKEN environment variable not set!")
        return
    
    logger.info("🚀 Starting Telegram Translation Bot...")
    
    # Create bot application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("toggle", toggle))
    application.add_handler(CommandHandler("help", help_command))
    
    # Add button callback handler
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Add message handler for translation
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    
    logger.info("✅ Bot handlers configured")
    logger.info("🌍 Translation Bot is now running 24/7!")
    logger.info("💬 Ready to translate Ukrainian ↔ English (complete messages)")
    
    # Run the bot with polling (more reliable than webhooks on Render)
    await application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        close_loop=False
    )

def main():
    """Main function"""
    # Start Flask server in background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask server started for Render health checks")
    
    # Run the bot
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot error: {e}")

if __name__ == '__main__':
    main()
