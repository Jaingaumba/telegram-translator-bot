import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import requests
import re
from flask import Flask, request

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

# Translation function using Google Translate web API
async def translate_text(text, target_lang):
    """Translate text using Google Translate free web API"""
    try:
        cleaned_text = re.sub(r'\s+', ' ', text.strip())
        if not cleaned_text or len(cleaned_text) < 3:
            return text
            
        url = "https://translate.googleapis.com/translate_a/single"
        
        params = {
            'client': 'gtx',
            'sl': 'auto',
            'tl': target_lang,
            'dt': 't',
            'q': cleaned_text
        }
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            # Extract all translated segments
            if result and len(result) > 0 and result[0]:
                translated_text = ''.join([segment[0] for segment in result[0] if segment[0]])
                return translated_text if translated_text else text
        
        return text
        
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

**Commands:**
/start - Show this welcome message
/toggle - Turn auto-translation on/off
/help - Get detailed help"""

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
3. Start chatting normally!"""

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
2. I automatically translate messages:
   - Ukrainian → English
   - English → Ukrainian
3. Use /toggle to turn translation on/off"""

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

# Main message handler for translation
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
            target_lang = 'en'
        
        if not target_lang:
            return
            
        translated_text = await translate_text(text, target_lang)
        
        if translated_text and translated_text.lower().strip() != text.lower().strip():
            lang_names = {'en': 'English', 'uk': 'Ukrainian', 'unknown': 'Auto'}
            from_lang = lang_names.get(detected_lang, detected_lang.upper())
            to_lang = lang_names.get(target_lang, target_lang.upper())
            
            translation_message = f"🌍 **{from_lang} → {to_lang}**\n{translated_text}"
            
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=translation_message,
                parse_mode='Markdown',
                reply_to_message_id=update.message.message_id
            )
    
    except Exception as e:
        logger.error(f"Error handling message: {e}")

# Flask Routes for Render
@app.route('/')
def home():
    return "🌍 Telegram Translation Bot is running! ✅"

@app.route('/health')
def health():
    return "OK", 200

@app.route('/webhook', methods=['POST'])
async def webhook():
    """Route that receives updates from Telegram"""
    try:
        json_data = request.get_json()
        update = Update.de_json(json_data, application.bot)
        await application.process_update(update)
        return 'OK', 200
    except Exception as e:
        logger.error(f"Error in webhook route: {e}")
        return 'Error', 500

def set_webhook():
    """Set the webhook URL for this bot"""
    try:
        PUBLIC_URL = os.environ.get('RENDER_EXTERNAL_URL')
        if not PUBLIC_URL:
            logger.error("❌ RENDER_EXTERNAL_URL environment variable not set!")
            return False

        webhook_url = f"{PUBLIC_URL}/webhook"
        token = os.environ.get('BOT_TOKEN')
        set_webhook_url = f"https://api.telegram.org/bot{token}/setWebhook?url={webhook_url}"

        response = requests.get(set_webhook_url, timeout=10)
        result = response.json()

        if result.get('ok'):
            logger.info(f"✅ Webhook set successfully: {webhook_url}")
            return True
        else:
            logger.error(f"❌ Failed to set webhook: {result}")
            return False
    except Exception as e:
        logger.error(f"❌ Exception setting webhook: {e}")
        return False

# Main function to run the bot
async def main():
    """Main function to start the bot"""
    # Get bot token from environment variable
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    
    if not BOT_TOKEN:
        logger.error("❌ ERROR: BOT_TOKEN environment variable not set!")
        logger.error("Please set your bot token in Render dashboard > Environment")
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
    
    # Set the webhook
    webhook_set = set_webhook()
    if not webhook_set:
        logger.error("Failed to set webhook. Check your BOT_TOKEN and RENDER_EXTERNAL_URL.")
        return
    
    logger.info("✅ Bot handlers configured")
    logger.info("🌍 Translation Bot is now running 24/7!")
    logger.info("💬 Ready to translate Ukrainian ↔ English")
    
    # Start the Flask server
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Starting Flask server on port {port}")
    
    # For production, we need to run Flask in a separate way
    # This will keep the application running
    try:
        # Import here to avoid circular imports
        from waitress import serve
        serve(app, host="0.0.0.0", port=port)
    except ImportError:
        logger.warning("Waitress not available, using Flask development server")
        app.run(host="0.0.0.0", port=port, debug=False)

# Run the application
if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
