import os
import re
import logging
import threading
import asyncio
from typing import List, Optional
from flask import Flask, request, jsonify
import concurrent.futures

from deep_translator import GoogleTranslator, PonsTranslator, LingueeTranslator
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
TRANSLATE_CHUNK = 1500
MODE_AUTO = "auto"
MODE_TO_UK = "to_uk"
MODE_TO_EN = "to_en"

# Global variables
chat_modes = {}
user_private_chats = {}  # Store users who have private chats with bot
authorized_users = set()  # Users who can use the bot
telegram_app = None
bot_loop = None

# Language detection
UA_CYRILLIC_RE = re.compile(r"[А-Яа-яІіЇїЄєҐґ]")

# Flask app
app = Flask(__name__)

# -------------------- Enhanced Translation Utilities --------------------
def detect_direction(text: str) -> str:
    return MODE_TO_EN if UA_CYRILLIC_RE.search(text) else MODE_TO_UK

def split_text_preserving_paragraphs(text: str, max_chunk_size: int) -> List[str]:
    """Split text by paragraphs, keep them together as much as possible"""
    if len(text) <= max_chunk_size:
        return [text]
    
    paragraphs = text.split('\n\n')
    chunks = []
    current_chunk = ""
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
            
        if current_chunk and len(current_chunk) + len(para) + 2 > max_chunk_size:
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
            current_chunk = para
        else:
            if current_chunk:
                current_chunk += "\n\n" + para
            else:
                current_chunk = para
        
        if len(current_chunk) > max_chunk_size:
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
            current_chunk = ""
            
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
    
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    
    return [chunk for chunk in chunks if chunk.strip()]

def enhanced_translate_text(text: str, direction: str) -> str:
    """
    Enhanced translation using multiple services for better quality
    Tries Google Translate first, falls back to alternatives if needed
    """
    try:
        if direction == MODE_TO_UK:
            source, target = "en", "uk"
        elif direction == MODE_TO_EN:
            source, target = "uk", "en"
        else:
            source, target = ("uk", "en") if UA_CYRILLIC_RE.search(text) else ("en", "uk")

        chunks = split_text_preserving_paragraphs(text, TRANSLATE_CHUNK)
        translated_chunks = []

        logger.info(f"Enhanced translation: {len(chunks)} chunks, {source} → {target}")

        for i, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            
            translated_chunk = None
            
            # Try Google Translate first (most reliable)
            try:
                translator = GoogleTranslator(source=source, target=target)
                result = translator.translate(chunk)
                if result and result.strip() and result != chunk:
                    translated_chunk = result.strip()
                    logger.debug(f"Google Translate successful for chunk {i+1}")
            except Exception as e:
                logger.warning(f"Google Translate failed for chunk {i+1}: {e}")
            
            # If Google Translate failed or gave poor result, try alternatives
            if not translated_chunk:
                # Try Linguee (good for context and phrases)
                try:
                    if source == "uk" and target == "en":
                        # Linguee has limited Ukrainian support, but let's try
                        linguee = LingueeTranslator(source="ukrainian", target="english")
                        result = linguee.translate(chunk, return_all=False)
                        if result and result.strip() and result != chunk:
                            translated_chunk = result.strip()
                            logger.debug(f"Linguee successful for chunk {i+1}")
                except Exception as e:
                    logger.debug(f"Linguee failed for chunk {i+1}: {e}")
            
            # If still no good translation, try a more robust Google approach
            if not translated_chunk:
                try:
                    # Add context hints for better translation
                    context_text = chunk
                    if "переклалося" in chunk.lower():
                        context_text = "Context: informal expression. " + chunk
                    elif any(word in chunk.lower() for word in ["не", "нема", "немає"]):
                        context_text = "Context: negation. " + chunk
                    
                    translator = GoogleTranslator(source=source, target=target)
                    result = translator.translate(context_text)
                    
                    if result and result.strip():
                        # Remove context hint from result
                        if result.startswith("Context:"):
                            result = result.split(". ", 1)[-1] if ". " in result else result
                        translated_chunk = result.strip()
                        logger.debug(f"Enhanced Google Translate successful for chunk {i+1}")
                except Exception as e:
                    logger.error(f"Enhanced translation failed for chunk {i+1}: {e}")
            
            # Fallback to original text if all translation attempts failed
            translated_chunks.append(translated_chunk or chunk)
            
            # Small delay between chunks
            if i < len(chunks) - 1:
                import time
                time.sleep(0.2)

        # Join with paragraph breaks
        result = "\n\n".join(translated_chunks) if translated_chunks else text
        
        # Post-process common Ukrainian-English translation issues
        result = post_process_translation(result, source, target)
        
        return result
        
    except Exception as e:
        logger.error(f"Enhanced translation error: {e}")
        return text

def post_process_translation(text: str, source: str, target: str) -> str:
    """
    Post-process translation to fix common issues
    """
    if source == "uk" and target == "en":
        # Common Ukrainian-English fixes
        fixes = [
            (r"I did not translate\s*\(", "I didn't understand ("),
            (r"did not translate", "didn't understand"),
            (r"not translated", "didn't understand"),
            (r"переклалося", "understood"),  # In case it wasn't translated
        ]
        
        for pattern, replacement in fixes:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    
    return text

def chunk_text_for_telegram(text: str, limit: int = TG_SAFE) -> List[str]:
    """Split text for Telegram while preserving paragraph breaks"""
    if len(text) <= limit:
        return [text]

    chunks = []
    current = ""
    paragraphs = text.split('\n\n')
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
            
        if current and len(current) + len(para) + 2 > limit:
            if current.strip():
                chunks.append(current.strip())
            current = para
        else:
            if current:
                current += "\n\n" + para
            else:
                current = para
        
        if len(current) > limit:
            if '\n\n' in current:
                parts = current.split('\n\n')
                save_parts = '\n\n'.join(parts[:-1])
                if save_parts.strip():
                    chunks.append(save_parts.strip())
                current = parts[-1]
            
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

async def send_private_message(context: ContextTypes.DEFAULT_TYPE, user_id: int, text: str, original_message: str = None):
    """Send private message to user with translation"""
    try:
        parts = chunk_text_for_telegram(text, TG_SAFE)
        
        # Send header message
        header = "🔄 **Translation** (sent privately to avoid group clutter)\n"
        if original_message:
            original_preview = (original_message[:100] + "...") if len(original_message) > 100 else original_message
            header += f"**Original:** {original_preview}\n**Translation:**"
        
        await context.bot.send_message(chat_id=user_id, text=header, parse_mode='Markdown')
        
        # Send translation parts
        for part in parts:
            await context.bot.send_message(chat_id=user_id, text=part)
            
        logger.info(f"Private translation sent to user {user_id}")
        
    except Exception as e:
        logger.error(f"Failed to send private message to {user_id}: {e}")
        # If private message fails, we'll handle it in the main function
        raise

# -------------------- Handlers --------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        # Store user's private chat capability
        user_private_chats[user_id] = True
        authorized_users.add(user_id)
        chat_modes[chat_id] = MODE_AUTO
        
        welcome_text = (
            "🔄 **Private Translation Bot**\n\n"
            "I translate between English and Ukrainian with enhanced quality!\n\n"
            "**Key Features:**\n"
            "• 🔒 **Private translations** - sent to your DM to avoid group clutter\n"
            "• 🧠 **Enhanced translation quality** - multiple translation engines\n"
            "• 📝 **Paragraph structure preserved**\n"
            "• 🎯 **Context-aware translations**\n\n"
            "**How it works in groups:**\n"
            "• I detect Ukrainian messages from your colleagues\n"
            "• I translate Ukrainian → English and send privately to you\n"
            "• English messages are ignored (no translation needed)\n"
            "• Your group stays clean and organized! ✨\n\n"
            "**Commands:**\n"
            "• /auto - Auto-detect language (default)\n"
            "• /to_en - Force Ukrainian → English\n"
            "• /to_uk - Force English → Ukrainian\n"
            "• /help - Show help\n\n"
            "**Important:** Start this bot privately first so I can send you translations!\n\n"
            "Ready for private, high-quality translations! 🚀"
        )
        
        await update.message.reply_text(welcome_text, parse_mode='Markdown')
        logger.info(f"User {user_id} authorized for private translations")
        
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        await update.message.reply_text("Hello! I'm a private translation bot. Send me text to translate!")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        help_text = (
            "**Private Translation Bot Help**\n\n"
            "**Commands:**\n"
            "/auto – Auto-detect language\n"
            "/to_en – Ukrainian → English\n"
            "/to_uk – English → Ukrainian\n"
            "/help – Show this help\n\n"
            "**Private Translation Features:**\n"
            "✅ Translations sent to your private DM\n"
            "✅ Group chats stay uncluttered\n"
            "✅ Enhanced translation quality\n"
            "✅ Paragraph structure preserved\n"
            "✅ Context-aware translation\n\n"
            "**Setup:**\n"
            "1. Start this bot privately (send /start)\n"
            "2. Add bot to your group\n"
            "3. Bot will send translations privately to you!\n\n"
            "**Tip:** If you haven't started the bot privately, I can't send you private messages due to Telegram's privacy rules."
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in help command: {e}")
        await update.message.reply_text("Available commands: /auto /to_en /to_uk /help")

async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_modes[update.effective_chat.id] = MODE_AUTO
        user_private_chats[update.effective_user.id] = True
        authorized_users.add(update.effective_user.id)
        await update.message.reply_text("✅ Mode: **Auto-detect** with private translations", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in auto command: {e}")
        await update.message.reply_text("✅ Mode set to auto-detect")

async def to_en_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_modes[update.effective_chat.id] = MODE_TO_EN
        user_private_chats[update.effective_user.id] = True
        authorized_users.add(update.effective_user.id)
        await update.message.reply_text("✅ Mode: **Ukrainian → English** with private translations", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in to_en command: {e}")
        await update.message.reply_text("✅ Mode set to Ukrainian → English")

async def to_uk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_modes[update.effective_chat.id] = MODE_TO_UK
        user_private_chats[update.effective_user.id] = True
        authorized_users.add(update.effective_user.id)
        await update.message.reply_text("✅ Mode: **English → Ukrainian** with private translations", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in to_uk command: {e}")
        await update.message.reply_text("✅ Mode set to English → Ukrainian")

async def translate_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages and send translations privately"""
    try:
        if not update.message or not update.message.text:
            return
        
        text = update.message.text.strip()
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        if text.startswith("/") or len(text) < 2:
            return

        # Check if user is authorized (has started the bot privately)
        if user_id not in authorized_users:
            # Send a one-time instruction in the group
            instruction_text = (
                f"👋 @{update.effective_user.username or 'User'}, to receive private translations, "
                "please start a private chat with me first by clicking @YourBotName and sending /start"
            )
            try:
                await update.message.reply_text(instruction_text)
                authorized_users.add(user_id)  # Don't spam this message
            except:
                pass
            return

        mode = chat_modes.get(chat_id, MODE_AUTO)
        direction = detect_direction(text) if mode == MODE_AUTO else mode

        # Send typing indicator to the group briefly
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        except:
            pass
        
        # Count paragraphs for logging
        paragraph_count = len([p for p in text.split('\n\n') if p.strip()])
        logger.info(f"Translating {len(text)} chars, {paragraph_count} paragraphs privately for user {user_id}")
        
        # Translate in background thread
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            loop = asyncio.get_event_loop()
            translated = await loop.run_in_executor(executor, enhanced_translate_text, text, direction)
        
        if not translated or translated == text:
            # Send failure message privately
            try:
                failure_msg = "🤔 I couldn't translate that text. It might be in an unsupported language or too ambiguous."
                await context.bot.send_message(chat_id=user_id, text=failure_msg)
            except:
                pass
            return
        
        # Send translation privately
        try:
            await send_private_message(context, user_id, translated, text)
            
            # Optional: Send a very brief confirmation in the group (can be removed if too cluttered)
            try:
                confirmation = "✅ Translation sent privately"
                sent_msg = await update.message.reply_text(confirmation)
                # Delete confirmation after a few seconds to keep group clean
                asyncio.create_task(delete_message_after_delay(context, chat_id, sent_msg.message_id, 5))
            except:
                pass
                
        except Exception as private_error:
            # If private message fails, send in group as fallback
            logger.warning(f"Private message failed for user {user_id}, sending in group: {private_error}")
            try:
                fallback_msg = (
                    f"🔄 **Translation** (private message failed - sent here instead)\n"
                    f"**Original:** {(text[:100] + '...') if len(text) > 100 else text}\n"
                    f"**Translation:** {translated}"
                )
                await update.message.reply_text(fallback_msg, parse_mode='Markdown')
            except:
                await update.message.reply_text(f"Translation: {translated}")
        
    except Exception as e:
        logger.error(f"Translation failed: {e}")
        try:
            await context.bot.send_message(
                chat_id=user_id, 
                text="❌ Translation error. Please try again later."
            )
        except:
            try:
                await update.message.reply_text("❌ Translation error. Please try again later.")
            except:
                pass

async def delete_message_after_delay(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay: int):
    """Delete a message after specified delay"""
    try:
        await asyncio.sleep(delay)
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except:
        pass  # Message might already be deleted or bot lacks permissions

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
    
    logger.info(f"✅ Private translation bot webhook set: {webhook_url}")
    return telegram_app

def run_bot_in_thread():
    global bot_loop
    
    def bot_thread():
        global bot_loop
        bot_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(bot_loop)
        
        try:
            bot_loop.run_until_complete(setup_bot())
            logger.info("✅ Private translation bot initialized successfully")
            bot_loop.run_forever()
        except Exception as e:
            logger.error(f"❌ Bot initialization failed: {e}")
            raise
    
    bot_thread_obj = threading.Thread(target=bot_thread, daemon=True)
    bot_thread_obj.start()
    
    import time
    time.sleep(3)
    
    if not bot_loop or not telegram_app:
        raise RuntimeError("Bot initialization failed")
    
    logger.info("✅ Private translation bot thread started successfully")

# -------------------- Flask Routes --------------------
@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "status": "Private Translation Bot is running!",
        "webhook_url": f"{PUBLIC_URL}/webhook",
        "bot_initialized": telegram_app is not None,
        "features": [
            "Private DM translations (no group clutter)",
            "Enhanced translation quality",
            "Multiple translation engines",
            "Paragraph structure preservation",
            "Context-aware translations"
        ],
        "authorized_users": len(authorized_users)
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
            return jsonify({"status": "Private translation webhook set successfully", "url": webhook_url})
        else:
            return jsonify({"error": "Failed to set webhook"}), 400
            
    except Exception as e:
        logger.error(f"Set webhook error: {e}")
        return jsonify({"error": str(e)}), 500

# -------------------- Main --------------------
def main():
    logger.info("🚀 Starting Private Translation Bot...")
    
    try:
        run_bot_in_thread()
        logger.info(f"🌐 Starting Flask server on 0.0.0.0:{PORT}")
        app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
        
    except Exception as e:
        logger.error(f"❌ Startup failed: {e}")
        raise

if __name__ == "__main__":
    main()
