import os
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import re
import threading
from flask import Flask

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize translator - using requests instead of googletrans to avoid conflicts
import requests
import json
from urllib.parse import quote

# User settings storage
user_settings = {}

# Flask app for Render (keeps bot awake)
app = Flask(__name__)

@app.route('/')
def home():
    return "🌍 Telegram Translation Bot is running! ✅"

@app.route('/health')
def health():
    return "OK"

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

# Alternative translation using Google Translate API (more reliable)
def detect_language(text):
    try:
        # Simple language detection based on character sets
        ukrainian_chars = set('абвгґдеєжзиіїйклмнопрстуфхцчшщьюя')
        english_chars = set('abcdefghijklmnopqrstuvwxyz')
        
        text_lower = text.lower()
        uk_count = sum(1 for char in text_lower if char in ukrainian_chars)
        en_count = sum(1 for char in text_lower if char in english_chars)
        
        if uk_count > en_count:
            return 'uk'
        elif en_count > uk_count:
            return 'en'
        else:
            return 'unknown'
    except:
        return 'unknown'

async def translate_text(text, target_lang):
    try:
        # Clean text
        cleaned_text = re.sub(r'\s+', ' ', text.strip())
        if not cleaned_text or len(cleaned_text) < 3:
            return text
        
        # Use Google Translate via web API (more reliable)
        base_url = "https://translate.googleapis.com/translate_a/single"
        params = {
            'client': 'gtx',
            'sl': 'auto',
            'tl': target_lang,
            'dt': 't',
            'q': cleaned_text
        }
        
        response = requests.get(base_url, params=params, timeout=10)
        if response.status_code == 200:
            result = response.json()
            if result and result[0] and result[0][0]:
                return result[0][0][0]
        
        return text  # Return original if translation fails
        
    except Exception as e:
        logger.error(f"Translation error: {e}")
        return text

# Get user settings
def get_user_settings(user_id):
    return user_settings.get(str(user_id), {
        'auto_translate': True,
        'source_lang': 'auto',
        'target_lang_en': 'en',
        'target_lang_uk': 'uk',
        'translate_own_messages': True
    })

# Update user settings
def update_user_settings(user_id, new_settings):
    user_id = str(user_id)
    if user_id not in user_settings:
        user_settings[user_id] = get_user_settings(user_id)
    user_settings[user_id].update(new_settings)

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Initialize user settings if not exists
    if str(user_id) not in user_settings:
        user_settings[str(user_id)] = get_user_settings(user_id)
    
    welcome_text = """
🌍 **Telegram Auto-Translator Bot**

Welcome! I'll help you with automatic translation between English and Ukrainian.

**Features:**
• Auto-detect language and translate
• English ↔ Ukrainian translation
• Works in groups and private chats
• Toggle translation on/off
• Customizable settings

**Commands:**
/start - Show this welcome message
/settings - Configure translation preferences
/toggle - Turn auto-translation on/off
/help - Show detailed help

**How it works:**
1. Add me to your group or chat with me directly
2. I'll automatically translate messages:
   - Ukrainian → English (for you)
   - English → Ukrainian (for your colleagues)
3. Use /settings to customize behavior

Ready to start translating! 🚀
    """
    
    keyboard = [
        [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

# Settings command
async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    settings = get_user_settings(user_id)
    
    status = "✅ ON" if settings['auto_translate'] else "❌ OFF"
    
    settings_text = f"""
⚙️ **Translation Settings**

**Status:** {status}

**Current Settings:**
• Auto-translate: {settings['auto_translate']}
• Translate own messages: {settings['translate_own_messages']}
• English target: {settings['target_lang_en']}
• Ukrainian target: {settings['target_lang_uk']}

Use the buttons below to modify settings:
    """
    
    keyboard = [
        [InlineKeyboardButton(f"{'🔴 Turn OFF' if settings['auto_translate'] else '🟢 Turn ON'}", 
                            callback_data="toggle_auto")],
        [InlineKeyboardButton(f"Own msgs: {'ON' if settings['translate_own_messages'] else 'OFF'}", 
                            callback_data="toggle_own")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(settings_text, reply_markup=reply_markup, parse_mode='Markdown')

# Toggle command
async def toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    settings = get_user_settings(user_id)
    
    new_status = not settings['auto_translate']
    update_user_settings(user_id, {'auto_translate': new_status})
    
    status_text = "enabled ✅" if new_status else "disabled ❌"
    await update.message.reply_text(f"Auto-translation {status_text}")

# Help command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
❓ **Help & Instructions**

**Setup:**
1. Add this bot to your group chat
2. Give the bot permission to read messages
3. Start chatting - translation is automatic!

**How Translation Works:**
• I detect the language of each message
• Ukrainian messages → Translated to English
• English messages → Translated to Ukrainian
• Other languages → Translated to English

**Commands:**
• `/start` - Welcome message
• `/settings` - Configure preferences
• `/toggle` - Quick on/off switch
• `/help` - This help message

**Tips:**
• Translation works best with complete sentences
• Very short messages (1-2 words) might not be translated
• You can turn off translation for your own messages in settings
• The bot needs to stay in the group to work

**Privacy:**
• Messages are only sent to Google Translate for translation
• No messages are stored or logged permanently
• Settings are saved locally on the bot server

Need more help? Contact the bot administrator.
    """
    
    keyboard = [[InlineKeyboardButton("⚙️ Settings", callback_data="settings")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(help_text, reply_markup=reply_markup, parse_mode='Markdown')

# Handle button callbacks
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    await query.answer()
    
    if query.data == "settings":
        await settings_callback(query, context)
    elif query.data == "help":
        await help_callback(query, context)
    elif query.data == "toggle_auto":
        await toggle_auto_callback(query, context)
    elif query.data == "toggle_own":
        await toggle_own_callback(query, context)
    elif query.data == "back_main":
        await back_main_callback(query, context)

async def settings_callback(query, context):
    user_id = query.from_user.id
    settings = get_user_settings(user_id)
    
    status = "✅ ON" if settings['auto_translate'] else "❌ OFF"
    
    settings_text = f"""
⚙️ **Translation Settings**

**Status:** {status}

**Current Settings:**
• Auto-translate: {settings['auto_translate']}
• Translate own messages: {settings['translate_own_messages']}
• English target: {settings['target_lang_en']}
• Ukrainian target: {settings['target_lang_uk']}

Use the buttons below to modify settings:
    """
    
    keyboard = [
        [InlineKeyboardButton(f"{'🔴 Turn OFF' if settings['auto_translate'] else '🟢 Turn ON'}", 
                            callback_data="toggle_auto")],
        [InlineKeyboardButton(f"Own msgs: {'ON' if settings['translate_own_messages'] else 'OFF'}", 
                            callback_data="toggle_own")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(settings_text, reply_markup=reply_markup, parse_mode='Markdown')

async def help_callback(query, context):
    help_text = """
❓ **Help & Instructions**

**Setup:**
1. Add this bot to your group chat
2. Give the bot permission to read messages
3. Start chatting - translation is automatic!

**How Translation Works:**
• I detect the language of each message
• Ukrainian messages → Translated to English
• English messages → Translated to Ukrainian
• Other languages → Translated to English

**Commands:**
• `/start` - Welcome message
• `/settings` - Configure preferences
• `/toggle` - Quick on/off switch
• `/help` - This help message

**Tips:**
• Translation works best with complete sentences
• Very short messages (1-2 words) might not be translated
• You can turn off translation for your own messages in settings

Need more help? Contact the bot administrator.
    """
    
    keyboard = [[InlineKeyboardButton("⚙️ Settings", callback_data="settings")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(help_text, reply_markup=reply_markup, parse_mode='Markdown')

async def toggle_auto_callback(query, context):
    user_id = query.from_user.id
    settings = get_user_settings(user_id)
    
    new_status = not settings['auto_translate']
    update_user_settings(user_id, {'auto_translate': new_status})
    
    await settings_callback(query, context)

async def toggle_own_callback(query, context):
    user_id = query.from_user.id
    settings = get_user_settings(user_id)
    
    new_status = not settings['translate_own_messages']
    update_user_settings(user_id, {'translate_own_messages': new_status})
    
    await settings_callback(query, context)

async def back_main_callback(query, context):
    welcome_text = """
🌍 **Telegram Auto-Translator Bot**

Welcome! I'll help you with automatic translation between English and Ukrainian.

**Features:**
• Auto-detect language and translate
• English ↔ Ukrainian translation
• Works in groups and private chats
• Toggle translation on/off
• Customizable settings

Ready to start translating! 🚀
    """
    
    keyboard = [
        [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

# Handle messages for translation
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Skip if no text message
    if not update.message or not update.message.text:
        return
    
    text = update.message.text
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Skip commands
    if text.startswith('/'):
        return
    
    # Get user settings
    settings = get_user_settings(user_id)
    
    # Skip if auto-translation is disabled
    if not settings['auto_translate']:
        return
    
    # Skip very short messages
    if len(text.strip()) < 3:
        return
    
    # Detect language
    detected_lang = detect_language(text)
    
    # Determine target language
    target_lang = None
    translation_needed = False
    
    if detected_lang == 'uk':  # Ukrainian -> English
        target_lang = 'en'
        translation_needed = True
    elif detected_lang == 'en':  # English -> Ukrainian
        target_lang = 'uk'
        translation_needed = True
    elif detected_lang not in ['uk', 'en']:  # Other languages -> English
        target_lang = 'en'
        translation_needed = True
    
    if not translation_needed:
        return
    
    try:
        # Translate the message
        translated_text = await translate_text(text, target_lang)
        
        # Only send translation if it's different from original
        if translated_text.lower() != text.lower():
            # Format the translation message
            lang_names = {'en': 'English', 'uk': 'Ukrainian', 'ru': 'Russian'}
            from_lang = lang_names.get(detected_lang, detected_lang.upper())
            to_lang = lang_names.get(target_lang, target_lang.upper())
            
            translation_message = f"🌍 **{from_lang} → {to_lang}**\n{translated_text}"
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=translation_message,
                parse_mode='Markdown',
                reply_to_message_id=update.message.message_id
            )
    
    except Exception as e:
        logger.error(f"Error handling message: {e}")

# Main function
def main():
    # Get bot token from environment variable (for Render deployment)
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    
    if not BOT_TOKEN:
        logger.error("❌ ERROR: BOT_TOKEN environment variable not set!")
        logger.error("Please set your bot token in Render dashboard")
        return
    
    # Start Flask server in background (required for Render)
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("settings", settings))
    application.add_handler(CommandHandler("toggle", toggle))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Start the bot
    logger.info("🚀 Bot starting on Render...")
    logger.info("Bot is running 24/7 with auto-wake functionality!")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
