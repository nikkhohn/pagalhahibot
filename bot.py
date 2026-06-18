import os
import re
import time
import asyncio
import requests
import logging
import base64
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

# ── Config ──
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
FIREBASE_URL = os.environ.get("FIREBASE_URL", "https://pagalbhabhi-1ac18-default-rtdb.asia-southeast1.firebasedatabase.app")
ADMIN_ID     = int(os.environ.get("ADMIN_ID", "0"))
PORT         = int(os.environ.get("PORT", "10000"))
BOT_SECRET   = "pagalbhabhi_bot_secret_2024"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

# ── Flask ──
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "✅ Pagal Bhabhi Bot is running!"

@flask_app.route('/health')
def health():
    return "OK", 200

# ── Semaphore — ek waqt mein sirf 1 post process ho ──
upload_semaphore = None  # Bot loop mein initialize hoga

# ── Terabox domains ──
TERABOX_DOMAINS = [
    'terabox.com','1024terabox.com','teraboxapp.com','terabox.app',
    'nephobox.com','mirrorbox.com','momerybox.com','freeterabox.com',
    'teraboxlink.com','4funbox.com','terafileshare.com','teraboxshare.com',
    'terasharelink.com','1024tera.com','mirrobox.com','tibibox.com'
]

def is_terabox(url):
    return any(d in url for d in TERABOX_DOMAINS)

def extract_terabox_link(text):
    for url in re.findall(r'https?://[^\s\n]+', text or ''):
        if is_terabox(url.strip()):
            return url.strip()
    return None

def extract_title(text):
    for line in [l.strip() for l in (text or '').split('\n') if l.strip()]:
        if not line.startswith('http') and not line.startswith('/') and not line.startswith('#'):
            return line[:80]
    return None

def upload_image(file_bytes, filename="image.jpg"):
    try:
        res = requests.post(
            'https://catbox.moe/user/api.php',
            data={'reqtype': 'fileupload'},
            files={'fileToUpload': (filename, file_bytes, 'image/jpeg')},
            timeout=30
        )
        url = res.text.strip()
        if url.startswith('https://'):
            log.info(f"Catbox OK: {url}")
            return url
    except Exception as e:
        log.error(f"Catbox error: {e}")
    try:
        b64 = base64.b64encode(file_bytes).decode()
        res2 = requests.post(
            'https://freeimage.host/api/1/upload',
            data={'key': '6d207e02198a847aa98d0a2a901485a5', 'action': 'upload', 'source': b64, 'format': 'json'},
            timeout=30
        )
        data = res2.json()
        if data.get('status_code') == 200:
            url = data['image']['url']
            log.info(f"Freeimage OK: {url}")
            return url
    except Exception as e:
        log.error(f"Freeimage error: {e}")
    return None

def get_max_order():
    try:
        res = requests.get(f"{FIREBASE_URL}/posts.json", timeout=10)
        data = res.json()
        if not data:
            return int(time.time() * 1000)
        max_order = 0
        for v in data.values():
            if isinstance(v, dict):
                max_order = max(max_order, v.get('order', 0))
        return max_order + 1
    except:
        return int(time.time() * 1000)

def save_to_firebase(title, image_url, terabox_url, is_premium=False):
    post_id = f"post_{int(time.time() * 1000)}"
    post = {
        "name": title,
        "image": image_url,
        "redirect": terabox_url,
        "premium": is_premium,
        "isNew": True,
        "order": get_max_order() + 1,
        "createdAt": int(time.time() * 1000),
        "_botKey": BOT_SECRET
    }
    try:
        res = requests.put(f"{FIREBASE_URL}/posts/{post_id}.json", json=post, timeout=15)
        log.info(f"Firebase save status: {res.status_code}, response: {res.text[:200]}")
        return res.status_code == 200, post_id
    except Exception as e:
        log.error(f"Firebase error: {e}")
        return False, post_id

def update_post_premium(post_id, is_premium):
    try:
        res = requests.patch(
            f"{FIREBASE_URL}/posts/{post_id}.json",
            json={"premium": is_premium, "_botKey": BOT_SECRET},
            timeout=15
        )
        return res.status_code == 200
    except:
        return False

def get_recent_posts(limit=10):
    try:
        res = requests.get(f"{FIREBASE_URL}/posts.json?orderBy=\"createdAt\"&limitToLast={limit}", timeout=10)
        data = res.json()
        if not data:
            return []
        posts = [{"id": k, **v} for k, v in data.items()]
        posts.sort(key=lambda x: x.get('createdAt', 0), reverse=True)
        return posts
    except:
        return []

# ── Process ek post ──
async def process_post(update, context, text, file_id):
    global upload_semaphore
    msg = update.message

    async with upload_semaphore:
        terabox_url = extract_terabox_link(text)
        if not terabox_url:
            await msg.reply_text("⚠️ Terabox link nahi mila!")
            return

        title = extract_title(text) or f"Video {int(time.time())}"
        is_premium = '#premium' in text.lower()

        status_msg = await msg.reply_text("⏳ Processing...")

        async def edit(text):
            try:
                await status_msg.edit_text(text)
            except Exception as e:
                log.error(f"Edit error: {e}")

        # Image upload
        image_url = None
        if file_id:
            await edit("📤 Image upload ho rahi hai...")
            try:
                file = await context.bot.get_file(file_id)
                file_bytes = await file.download_as_bytearray()
                # Run blocking upload in thread
                image_url = await asyncio.get_event_loop().run_in_executor(
                    None, upload_image, bytes(file_bytes)
                )
                if not image_url:
                    await edit("❌ Image upload fail!")
                    return
            except Exception as e:
                await edit(f"❌ Image error: {str(e)[:100]}")
                return
        else:
            image_url = "https://files.catbox.moe/placeholder.jpg"

        # Firebase save
        await edit("💾 Site pe save ho raha hai...")
        success, post_id = await asyncio.get_event_loop().run_in_executor(
            None, save_to_firebase, title, image_url, terabox_url, is_premium
        )

        if success:
            prem = "👑 Premium" if is_premium else "💚 Free"
            await edit(
                f"✅ Post upload ho gayi!\n\n"
                f"📌 {title}\n"
                f"🖼️ Image: ✓\n"
                f"🔗 Link: ✓\n"
                f"🏷️ {prem}\n"
                f"🆔 `{post_id}`\n\n"
                f"{'💡 /premium ' + post_id if not is_premium else ''}"
            )
        else:
            await edit("❌ Firebase save fail!")

# ── Handlers ──
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    if ADMIN_ID and msg.from_user.id != ADMIN_ID:
        await msg.reply_text("❌ Access denied!")
        return

    text = msg.caption or msg.text or ""

    if not extract_terabox_link(text):
        await msg.reply_text(
            "⚠️ Terabox link nahi mila!\n\n"
            "Format:\n```\nVideo Title\nhttps://terabox_link\n```\n+ Image attach karo",
            parse_mode='Markdown'
        )
        return

    file_id = None
    if msg.photo:
        file_id = msg.photo[-1].file_id
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith('image/'):
        file_id = msg.document.file_id

    # Queue size batao
    pending = upload_semaphore._value if upload_semaphore else 1
    if pending == 0:
        await msg.reply_text("⏳ Ek post process ho rahi hai, tumhari bari aa rahi hai...")

    # Background task
    asyncio.create_task(process_post(update, context, text, file_id))

async def cmd_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if ADMIN_ID and msg.from_user.id != ADMIN_ID:
        return

    args = context.args
    if not args:
        await msg.reply_text("⏳ Posts fetch ho rahi hain...")
        posts = await asyncio.get_event_loop().run_in_executor(None, get_recent_posts, 10)
        if not posts:
            await msg.reply_text("❌ Koi post nahi mili!")
            return
        text = "📋 *Recent 10 Posts:*\n\n"
        for i, p in enumerate(posts, 1):
            status = "👑" if p.get('premium') else "💚"
            name = p.get('name', 'No title')[:30]
            pid = p.get('id', '')
            text += f"{i}. {status} `{pid}`\n    📌 {name}\n\n"
        text += "💡 `/premium post_id` — premium karo\n`/premium post_id free` — free karo"
        await msg.reply_text(text, parse_mode='Markdown')
        return

    post_id = args[0].strip()
    make_free = len(args) > 1 and args[1].lower() == 'free'

    try:
        res = requests.get(f"{FIREBASE_URL}/posts/{post_id}.json", timeout=10)
        post_data = res.json()
        if not post_data:
            await msg.reply_text(f"❌ Post `{post_id}` nahi mili!", parse_mode='Markdown')
            return
    except:
        await msg.reply_text("❌ Firebase connect error!")
        return

    new_status = not make_free
    success = await asyncio.get_event_loop().run_in_executor(None, update_post_premium, post_id, new_status)

    if success:
        emoji = "👑 Premium" if new_status else "💚 Free"
        await msg.reply_text(
            f"✅ Updated!\n\n"
            f"📌 {post_data.get('name','')[:40]}\n"
            f"🏷️ {emoji}\n"
            f"🆔 `{post_id}`",
            parse_mode='Markdown'
        )
    else:
        await msg.reply_text("❌ Update fail!")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if ADMIN_ID and msg.from_user.id != ADMIN_ID:
        return
    posts = await asyncio.get_event_loop().run_in_executor(None, get_recent_posts, 5)
    text = "🤖 *Bot Status: Running ✅*\n\n"
    if posts:
        text += "📋 *Last 5 Posts:*\n"
        for p in posts:
            s = "👑" if p.get('premium') else "💚"
            text += f"{s} {p.get('name','')[:30]}\n"
    await msg.reply_text(text, parse_mode='Markdown')

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if ADMIN_ID and msg.from_user.id != ADMIN_ID:
        return
    await msg.reply_text(
        "📖 *Commands:*\n\n"
        "📤 Post bhejne ka format:\n"
        "```\nVideo Title\nhttps://terabox_link\n```\n"
        "+ Image attach karo\n\n"
        "👑 `/premium` — recent posts\n"
        "👑 `/premium post_id` — premium karo\n"
        "💚 `/premium post_id free` — free karo\n"
        "📊 `/status` — bot status\n\n"
        "💡 `#premium` likhne se directly premium upload hoga",
        parse_mode='Markdown'
    )

async def run_bot_async():
    global upload_semaphore
    upload_semaphore = asyncio.Semaphore(1)  # Ek waqt mein sirf 1

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("premium", cmd_premium))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

    log.info("Bot polling shuru ho gaya!")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=["message"], drop_pending_updates=True)
    while True:
        await asyncio.sleep(3600)

def run_bot_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_bot_async())

if __name__ == "__main__":
    Thread(target=run_bot_thread, daemon=True).start()
    log.info(f"Flask server port {PORT} pe start ho raha hai...")
    flask_app.run(host='0.0.0.0', port=PORT)
