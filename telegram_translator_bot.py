import os
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import requests
import json
import re
import threading
from flask import Flask

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# User settings storage
user_settings = {}

# Flask app for Render (keeps bot alive)
app = Flask(__name__)

@app.route('/')
def home():
    return "🌍 Telegram Translation Bot is running! ✅"

@app.route('/health')
def health():
    return "OK", 200

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# Language detection function
def detect_language(text):
    """Simple but effective language detection for Ukrainian and English"""
    try:
        # Ukrainian alphabet characters
        ukrainian_chars = set('абвгґдеєжзиіїйклмнопрстуфхцчшщьюяАБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯ')
        # English alphabet characters  
        english_chars = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ')
        
        # Count characters
        uk_count = sum(1 for char in text if char in ukrainian_chars)
        en_count = sum(1 for char in text if char in english_chars)
        
        # Determine language based on character count
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
        # Clean and validate input
        cleaned_text = re.sub(r'\s+', ' ', text.strip())
        if not cleaned_text or len(cleaned_text) < 3:
            return text
            
        # Google Translate web API endpoint
        url = "https://translate.googleapis.com/translate_a/single"
        
        # Parameters for the API call
        params = {
            'client': 'gtx',
            'sl': 'auto',  # Auto-detect source language
            'tl': target_lang,  # Target language
            'dt': 't',  # Return translation
            'q': cleaned_text
        }
        
        # Headers to mimic a browser request
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # Make the API call with timeout
        response = requests.get(url, params=params, headers=headers, timeout=10)
        
        if response.status_code == 200:
            # Parse the JSON response
            result = response.json()
            
            # Extract translation from response structure
            if result and len(result) > 0 and result[0] and len(result[0]) > 0:
                translated = result[0][0][0]
                return translated if translated else text
        
        # Return original text if translation fails
        return text
        
    except Exception as e:
        logger.error(f"Translation error: {e}")
        return text

# User settings functions
def get_user_settings(user_id):
    """Get user settings with defaults"""
    return user_settings.get(str(user_id), {
        'auto_translate': True,
        'translate_own_messages': True
    })

def update_user_settings(user_id, new_settings):
    """Update user settings"""
    user_id = str(user_id)
    if user_id not in user_settings:
        user_settings[user_id] = get_user_settings(user_id)
    user_settings[user_id].update(new_settings)

# Bot command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user_id = update.effective_user.id
    
    # Initialize user settings
    if str(user_id) not in user_settings:
        user_settings[str(user_id)] = get_user_settings(user_id)
    
    welcome_text = """🌍 **Telegram Auto-Translator Bot**

Welcome! I automatically translate between English and Ukrainian.

**Features:**
• Ukrainian → English (for you)
• English → Ukrainian (for your colleagues)
• Works in groups and private chats
• Smart language detection
• Instant translations

**Commands:**
/start - Show this welcome message
/toggle - Turn auto-translation on/off
/help - Get detailed help

**How it works:**
1. Add me to your group chat
2. I'll automatically detect and translate messages
3. Translations appear as replies to original messages

Ready to start translating! 🚀"""
    
    keyboard = [
        [InlineKeyboardButton("❓ Help", callback_data="help")],
        [InlineKeyboardButton("⚙️ Toggle Translation", callback_data="toggle")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await update.message.reply_text(
            welcome_text, 
            reply_markup=reply_markup, 
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        await update.message.reply_text("Bot started successfully!")

async def toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /toggle command"""
    try:
        user_id = update.effective_user.id
        settings = get_user_settings(user_id)
        
        # Toggle the setting
        new_status = not settings['auto_translate']
        update_user_settings(user_id, {'auto_translate': new_status})
        
        # Send confirmation
        status_text = "enabled ✅" if new_status else "disabled ❌"
        await update.message.reply_text(f"Auto-translation {status_text}")
    except Exception as e:
        logger.error(f"Error in toggle command: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    help_text = """❓ **Help & Instructions**

**Setup:**
1. Add this bot to your group chat
2. Make sure the bot can read messages
3. Start chatting normally!

**How Translation Works:**
• I automatically detect message language
• Ukrainian messages → Translated to English  
• English messages → Translated to Ukrainian
• Other languages → Translated to English
• Very short messages (under 3 characters) are ignored

**Commands:**
• `/start` - Welcome message and setup
• `/toggle` - Turn translation on/off for you
• `/help` - Show this help message

**Tips:**
• Translation works best with complete sentences
• The bot wakes up instantly when messages arrive
• You can turn off translation anytime with /toggle
• Bot works in both group chats and private messages

**Privacy & Performance:**
• Messages are only sent to Google Translate
• No data is stored permanently
• Bot runs 24/7 on professional cloud hosting
• Translations typically appear within 1-2 seconds

Need more help? The bot is designed to work automatically!"""
    
    try:
        await update.message.reply_text(help_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in help command: {e}")
        await update.message.reply_text("Bot help: Use /start to begin, /toggle to turn translation on/off")

# Button callback handlers
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button presses"""
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
    """Handle help button press"""
    help_text = """❓ **Quick Help**

**How to use:**
1. Add me to your group chat
2. I automatically translate messages:
   - Ukrainian → English
   - English → Ukrainian
3. Use /toggle to turn translation on/off

**Commands:**
• /start - Main menu
• /toggle - Turn translation on/off  
• /help - Detailed help

The bot works automatically - no setup needed!"""
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Main", callback_data="back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_text(
            help_text, 
            reply_markup=reply_markup, 
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error in help callback: {e}")

async def toggle_callback(query, context):
    """Handle toggle button press"""
    try:
        user_id = query.from_user.id
        settings = get_user_settings(user_id)
        
        # Toggle the setting
        new_status = not settings['auto_translate']
        update_user_settings(user_id, {'auto_translate': new_status})
        
        # Update message
        status_text = "enabled ✅" if new_status else "disabled ❌"
        message_text = f"Auto-translation {status_text}"
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Main", callback_data="back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            message_text,
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error in toggle callback: {e}")

async def back_callback(query, context):
    """Handle back button press"""
    welcome_text = """🌍 **Telegram Auto-Translator Bot**

Welcome! I automatically translate between English and Ukrainian.

Ready to start translating! 🚀"""
    
    keyboard = [
        [InlineKeyboardButton("❓ Help", callback_data="help")],
        [InlineKeyboardButton("⚙️ Toggle Translation", callback_data="toggle")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_text(
            welcome_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error in back callback: {e}")

# Main message handler for translation
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages for translation"""
    try:
        # Validate message
        if not update.message or not update.message.text:
            return
        
        text = update.message.text
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        # Skip bot commands
        if text.startswith('/'):
            return
        
        # Skip very short messages
        if len(text.strip()) < 3:
            return
        
        # Check user settings
        settings = get_user_settings(user_id)
        if not settings['auto_translate']:
            return
        
        # Detect language
        detected_lang = detect_language(text)
        
        # Determine target language and if translation is needed
        target_lang = None
        if detected_lang == 'uk':
            target_lang = 'en'
        elif detected_lang == 'en':
            target_lang = 'uk'
        else:
            # For other languages, translate to English
            target_lang = 'en'
        
        # Skip if no target language determined
        if not target_lang:
            return
            
        # Translate the message
        translated_text = await translate_text(text, target_lang)
        
        # Only send translation if it's different from original
        if translated_text and translated_text.lower().strip() != text.lower().strip():
            # Format the translation message
            lang_names = {
                'en': 'English', 
                'uk': 'Ukrainian', 
                'ru': 'Russian',
                'unknown': 'Auto'
            }
            from_lang = lang_names.get(detected_lang, detected_lang.upper())
            to_lang = lang_names.get(target_lang, target_lang.upper())
            
            translation_message = f"🌍 **{from_lang} → {to_lang}**\n{translated_text}"
            
            # Send translation as a reply
            await context.bot.send_message(
                chat_id=chat_id,
                text=translation_message,
                parse_mode='Markdown',
                reply_to_message_id=update.message.message_id
            )
    
    except Exception as e:
        logger.error(f"Error handling message: {e}")
        # Don't send error messages to users - just log them

# Main application function
async def main():
    """Main function to start the bot"""
    # Get bot token from environment variable
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    
    if not BOT_TOKEN:
        logger.error("❌ ERROR: BOT_TOKEN environment variable not set!")
        logger.error("Please set your bot token in Render dashboard > Environment")
        return
    
    logger.info("🚀 Starting Telegram Translation Bot...")
    
    # Create bot application with proper async context
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
    
    # Start the bot
    logger.info("✅ Bot handlers configured")
    logger.info("🌍 Translation Bot is now running 24/7!")
    logger.info("💬 Ready to translate Ukrainian ↔ English")
    
    # Run the bot with proper async handling
    async with application:
        await application.start()
        await application.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        
        # Keep running
        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            await application.updater.stop()
            await application.stop()

def run_bot():
    """Run the bot in async context"""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot error: {e}")

if __name__ == '__main__':
    # Start Flask server in background thread (required for Render)
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask server started for Render")
    
    # Start the bot
    run_bot()
