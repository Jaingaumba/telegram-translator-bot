import os
import logging
import asyncio
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
import requests
import re
import threading
import json
from typing import Dict, Any

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# User settings storage
user_settings: Dict[str, Dict[str, Any]] = {}

# Initialize Flask app
app = Flask(__name__)

# Global bot instance
bot_instance = None

def detect_language(text: str) -> str:
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

def translate_text_enhanced(text: str, target_lang: str) -> str:
    """Enhanced translation function that handles complete long text"""
    try:
        cleaned_text = re.sub(r'\s+', ' ', text.strip())
        if not cleaned_text or len(cleaned_text) < 3:
            return text
            
        # Handle long text by splitting into manageable chunks
        max_length = 4000
        if len(cleaned_text) <= max_length:
            chunks = [cleaned_text]
        else:
            # Smart chunking by sentences
            sentences = re.split(r'(?<=[.!?])\s+', cleaned_text)
            chunks = []
            current_chunk = ""
            
            for sentence in sentences:
                if len(current_chunk + " " + sentence) <= max_length:
                    current_chunk += " " + sentence if current_chunk else sentence
                else:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = sentence
            
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
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            try:
                response = requests.get(url, params=params, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    result = response.json()
                    
                    # Properly extract all translation segments
                    if result and result[0]:
                        translated_segments = []
                        for segment in result[0]:
                            if segment and len(segment) > 0 and segment[0]:
                                translated_segments.append(segment[0])
                        
                        if translated_segments:
                            chunk_translation = ''.join(translated_segments)
                            translated_chunks.append(chunk_translation)
                        else:
                            translated_chunks.append(chunk)
                    else:
                        translated_chunks.append(chunk)
                else:
                    translated_chunks.append(chunk)
                    
            except Exception as e:
                logger.error(f"Translation request error: {e}")
                translated_chunks.append(chunk)
        
        # Join all translated chunks
        final_translation = ' '.join(translated_chunks).strip()
        return final_translation if final_translation else text
        
    except Exception as e:
        logger.error(f"Translation error: {e}")
        return text

def get_user_settings(user_id: int) -> Dict[str, Any]:
    return user_settings.get(str(user_id), {
        'auto_translate': True,
        'translate_own_messages': True
    })

def update_user_settings(user_id: int, new_settings: Dict[str, Any]) -> None:
    user_id_str = str(user_id)
    if user_id_str not in user_settings:
        user_settings[user_id_str] = get_user_settings(user_id)
    user_settings[user_id_str].update(new_settings)

async def send_long_message(bot: Bot, chat_id: int, text: str, reply_to_message_id: int = None, parse_mode: str = None):
    """Send long messages by splitting them if necessary"""
    max_length = 4000
    
    if len(text) <= max_length:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_to_message_id=reply_to_message_id,
            parse_mode=parse_mode
        )
    else:
        # Split long messages
        parts = []
        remaining = text
        
        while remaining:
            if len(remaining) <= max_length:
                parts.append(remaining)
                break
            
            # Find good break point
            break_point = remaining.rfind('. ', 0, max_length - 100)
            if break_point == -1:
                break_point = remaining.rfind(' ', 0, max_length - 100)
            if break_point == -1:
                break_point = max_length - 100
            
            parts.append(remaining[:break_point + 1])
            remaining = remaining[break_point + 1:].strip()
        
        # Send all parts
        for i, part in enumerate(parts):
            await bot.send_message(
                chat_id=chat_id,
                text=part + (" ..." if i < len(parts) - 1 else ""),
                reply_to_message_id=reply_to_message_id if i == 0 else None,
                parse_mode=parse_mode
            )
            
            if i < len(parts) - 1:
                await asyncio.sleep(0.5)

# Message handlers
async def handle_start(bot: Bot, update_data: dict):
    user_id = update_data['message']['from']['id']
    chat_id = update_data['message']['chat']['id']
    
    if str(user_id) not in user_settings:
        user_settings[str(user_id)] = get_user_settings(user_id)
    
    welcome_text = """🌍 **Telegram Auto-Translator Bot**

Welcome! I automatically translate between English and Ukrainian.

**Features:**
• Ukrainian → English (for you)
• English → Ukrainian (for your colleagues)
• Complete message translation (handles long text)
• Smart language detection
• Works in groups and private chats

**Commands:**
/start - Show this welcome message
/toggle - Turn auto-translation on/off
/help - Get detailed help

Ready to start translating!"""

    keyboard = {
        "inline_keyboard": [
            [{"text": "❓ Help", "callback_data": "help"}],
            [{"text": "⚙️ Toggle Translation", "callback_data": "toggle"}]
        ]
    }
    
    await bot.send_message(
        chat_id=chat_id,
        text=welcome_text,
        parse_mode='Markdown',
        reply_markup=keyboard
    )

async def handle_toggle(bot: Bot, update_data: dict):
    user_id = update_data['message']['from']['id']
    chat_id = update_data['message']['chat']['id']
    
    settings = get_user_settings(user_id)
    new_status = not settings['auto_translate']
    update_user_settings(user_id, {'auto_translate': new_status})
    
    status_text = "enabled ✅" if new_status else "disabled ❌"
    await bot.send_message(
        chat_id=chat_id,
        text=f"Auto-translation {status_text}"
    )

async def handle_help(bot: Bot, update_data: dict):
    chat_id = update_data['message']['chat']['id']
    
    help_text = """❓ **Help & Instructions**

**Setup:**
1. Add this bot to your group chat
2. Make sure the bot can read messages
3. Start chatting normally!

**Translation Features:**
• Translates complete messages (not just first sentences)
• Handles long paragraphs and multiple sentences
• Ukrainian ↔ English translation
• Smart language detection
• Real-time translation

**Commands:**
• /start - Welcome message and setup
• /toggle - Turn translation on/off
• /help - Show this help

**Tips:**
• Bot translates entire messages, maintaining context
• Works with very long messages
• Translation appears as replies to original messages
• Use /toggle to turn translation on/off anytime

Ready to communicate seamlessly!"""

    await bot.send_message(
        chat_id=chat_id,
        text=help_text,
        parse_mode='Markdown'
    )

async def handle_callback_query(bot: Bot, update_data: dict):
    callback_query = update_data['callback_query']
    data = callback_query['data']
    chat_id = callback_query['message']['chat']['id']
    message_id = callback_query['message']['message_id']
    user_id = callback_query['from']['id']
    
    if data == "help":
        help_text = """❓ **Quick Help**

**How to use:**
1. Add me to your group chat
2. I automatically translate complete messages:
   - Ukrainian → English
   - English → Ukrainian
3. Use /toggle to turn translation on/off

**Features:**
• Translates full messages, not just first lines
• Handles long content and paragraphs
• Maintains message structure

The bot works automatically - no setup needed!"""

        keyboard = {
            "inline_keyboard": [
                [{"text": "🔙 Back to Main", "callback_data": "back"}]
            ]
        }
        
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=help_text,
            parse_mode='Markdown',
            reply_markup=keyboard
        )
    
    elif data == "toggle":
        settings = get_user_settings(user_id)
        new_status = not settings['auto_translate']
        update_user_settings(user_id, {'auto_translate': new_status})
        
        status_text = "enabled ✅" if new_status else "disabled ❌"
        message_text = f"Auto-translation {status_text}"
        
        keyboard = {
            "inline_keyboard": [
                [{"text": "🔙 Back to Main", "callback_data": "back"}]
            ]
        }
        
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=message_text,
            reply_markup=keyboard
        )
    
    elif data == "back":
        welcome_text = """🌍 **Telegram Auto-Translator Bot**

Welcome! I automatically translate between English and Ukrainian.

Ready to start translating!"""
        
        keyboard = {
            "inline_keyboard": [
                [{"text": "❓ Help", "callback_data": "help"}],
                [{"text": "⚙️ Toggle Translation", "callback_data": "toggle"}]
            ]
        }
        
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=welcome_text,
            parse_mode='Markdown',
            reply_markup=keyboard
        )

async def handle_message(bot: Bot, update_data: dict):
    """Handle regular messages for translation"""
    message = update_data['message']
    
    if 'text' not in message:
        return
    
    text = message['text']
    user_id = message['from']['id']
    chat_id = message['chat']['id']
    message_id = message['message_id']
    
    # Skip commands
    if text.startswith('/'):
        return
    
    # Skip very short messages
    if len(text.strip()) < 3:
        return
    
    # Check user settings
    settings = get_user_settings(user_id)
    if not settings['auto_translate']:
        return
    
    # Detect language and determine target
    detected_lang = detect_language(text)
    target_lang = None
    
    if detected_lang == 'uk':
        target_lang = 'en'
    elif detected_lang == 'en':
        target_lang = 'uk'
    else:
        target_lang = 'en'  # Default to English
    
    if not target_lang:
        return
    
    # Translate the message
    translated_text = translate_text_enhanced(text, target_lang)
    
    # Only send if translation is different
    if translated_text and translated_text.lower().strip() != text.lower().strip():
        lang_names = {'en': 'English', 'uk': 'Ukrainian', 'unknown': 'Auto'}
        from_lang = lang_names.get(detected_lang, detected_lang.upper())
        to_lang = lang_names.get(target_lang, target_lang.upper())
        
        translation_message = f"🌍 **{from_lang} → {to_lang}**\n{translated_text}"
        
        await send_long_message(
            bot=bot,
            chat_id=chat_id,
            text=translation_message,
            reply_to_message_id=message_id,
            parse_mode='Markdown'
        )

async def process_update(update_data: dict):
    """Process incoming Telegram updates"""
    global bot_instance
    
    try:
        if 'message' in update_data:
            message = update_data['message']
            
            if 'text' in message:
                text = message['text']
                
                if text == '/start':
                    await handle_start(bot_instance, update_data)
                elif text == '/toggle':
                    await handle_toggle(bot_instance, update_data)
                elif text == '/help':
                    await handle_help(bot_instance, update_data)
                else:
                    await handle_message(bot_instance, update_data)
        
        elif 'callback_query' in update_data:
            await handle_callback_query(bot_instance, update_data)
            
    except Exception as e:
        logger.error(f"Error processing update: {e}")

# Flask Routes
@app.route('/')
def home():
    return "🌍 Telegram Translation Bot is running! ✅"

@app.route('/health')
def health():
    return jsonify({"status": "OK"}), 200

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming webhook from Telegram"""
    try:
        update_data = request.json
        
        # Process update asynchronously
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(process_update(update_data))
        loop.close()
        
        return jsonify({"status": "OK"}), 200
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"error": str(e)}), 500

def set_webhook():
    """Set up webhook with Telegram"""
    try:
        token = os.environ.get('BOT_TOKEN')
        webhook_url = os.environ.get('WEBHOOK_URL')
        
        if not token:
            logger.error("BOT_TOKEN not found in environment variables")
            return False
            
        if not webhook_url:
            logger.error("WEBHOOK_URL not found in environment variables")
            return False
        
        url = f"https://api.telegram.org/bot{token}/setWebhook"
        data = {
            'url': f"{webhook_url}/webhook",
            'allowed_updates': ['message', 'callback_query']
        }
        
        response = requests.post(url, json=data, timeout=10)
        result = response.json()
        
        if result.get('ok'):
            logger.info(f"✅ Webhook set successfully: {webhook_url}/webhook")
            return True
        else:
            logger.error(f"❌ Failed to set webhook: {result}")
            return False
            
    except Exception as e:
        logger.error(f"Exception setting webhook: {e}")
        return False

def main():
    """Initialize bot and start Flask server"""
    global bot_instance
    
    # Get environment variables
    token = os.environ.get('BOT_TOKEN')
    
    if not token:
        logger.error("❌ BOT_TOKEN environment variable not set!")
        return
    
    # Initialize bot instance
    bot_instance = Bot(token=token)
    
    logger.info("🚀 Starting Telegram Translation Bot...")
    
    # Set webhook
    if set_webhook():
        logger.info("✅ Bot configured successfully")
        logger.info("🌍 Translation Bot is ready!")
        logger.info("💬 Supports complete message translation (Ukrainian ↔ English)")
    else:
        logger.error("❌ Failed to configure webhook")
        return
    
    # Start Flask server
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Starting server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)

if __name__ == '__main__':
    main()
