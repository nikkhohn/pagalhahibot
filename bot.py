import os
import re
import time
import asyncio
import requests
import logging
import base64
from queue import Queue
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

# ── Config ──
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
FIREBASE_URL = os.environ.get("FIREBASE_URL", "https://pagalbhabhi-1ac18-default-rtdb.asia-southeast1.firebasedatabase.app")
ADMIN_ID     = int(os.environ.get("ADMIN_ID", "0"))
PORT         = int(os.environ.get("PORT", "10000"))

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

# ── Queue ──
post_queue = Queue()

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
    """Pehla terabox link nikalo"""
    for url in re.findall(r'https?://[^\s\n]+', text or ''):
        if is_terabox(url.strip()):
            return url.strip()
    return None

def extract_title(text):
    """Pehli non-link, non-tag line = title"""
    for line in [l.strip() for l in (text or '').split('\n') if l.strip()]:
        if not line.startswith('http') and not line.startswith('/') and not line.startswith('#'):
            return line[:80]
    return None

def upload_image(file_bytes, filename="image.jpg"):
    """Image upload — Catbox primary, freeimage fallback"""
    # Catbox
    try:
        res = requests.post(
            'https://catbox.moe/user/api.php',
            data={'reqtype': 'fileupload'},
            files={'fileToUpload': (filename, file_bytes, 'image/jpeg')},
            timeout=30
        )
        url = res.text.strip()
        if url.startswith('https://'):
            log.info(f"Catbox upload success: {url}")
            return url
    except Exception as e:
        log.error(f"Catbox error: {e}")

    # freeimage.host fallback
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
            log.info(f"Freeimage upload success: {url}")
            return url
    except Exception as e:
        log.error(f"Freeimage error: {e}")

    return None

def get_max_order():
    try:
        res = requests.get(f"{FIREBASE_URL}/posts.json?shallow=true", timeout=10)
        data = res.json()
        if not data:
            return 0
        # Count posts as order
        return len(data)
    except:
        return int(time.time() // 1000)

def save_to_firebase(title, image_url, terabox_url, is_premium=False):
    post_id = f"post_{int(time.time() * 1000)}"
    post = {
        "name": title,
        "image": image_url,
        "redirect": terabox_url,
        "premium": is_premium,
        "isNew": True,
        "order": get_max_order() + 1,
        "createdAt": int(time.time() * 1000)
    }
    res = requests.put(f"{FIREBASE_URL}/posts/{post_id}.json", json=post, timeout=15)
    return res.status_code == 200, post_id

def update_post_premium(post_id, is_premium):
    res = requests.patch(
        f"{FIREBASE_URL}/posts/{post_id}.json",
        json={"premium": is_premium},
        timeout=15
    )
    return res.status_code == 200

def get_recent_posts(limit=10):
    """Recent posts fetch karo"""
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

# ── Queue Worker ──
def queue_worker():
    while True:
        try:
            job = post_queue.get()
            if job is None:
                break
            asyncio.run(process_job(job))
            post_queue.task_done()
            time.sleep(1)
        except Exception as e:
            log.error(f"Queue worker error: {e}")

async def process_job(job):
    bot       = job['bot']
    chat_id   = job['chat_id']
    status_id = job['status_id']
    text      = job['text']
    file_id   = job['file_id']
    pos       = job['pos']
    total     = job['total']

    prefix = f"[{pos}/{total}] " if total > 1 else ""

    async def edit(msg):
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=status_id, text=msg)
        except Exception as e:
            log.error(f"Edit error: {e}")

    # Terabox link
    terabox_url = extract_terabox_link(text)
    if not terabox_url:
        await edit(f"{prefix}⚠️ Terabox link nahi mila!")
        return

    # Title
    title = extract_title(text) or f"Video {pos}"
    is_premium = '#premium' in text.lower()

    # Image upload
    image_url = None
    if file_id:
        await edit(f"{prefix}📤 Image upload ho rahi hai...")
        try:
            file = await bot.get_file(file_id)
            file_bytes = await file.download_as_bytearray()
            image_url = upload_image(bytes(file_bytes))
            if not image_url:
                await edit(f"{prefix}❌ Image upload fail! Dobara try karo.")
                return
        except Exception as e:
            await edit(f"{prefix}❌ Image error: {str(e)[:100]}")
            return
    else:
        image_url = "https://i.imgur.com/placeholder.jpg"

    # Firebase save
    await edit(f"{prefix}💾 Site pe save ho raha hai...")
    success, post_id = save_to_firebase(title, image_url, terabox_url, is_premium)

    if success:
        prem = "👑 Premium" if is_premium else "💚 Free"
        await edit(
            f"{prefix}✅ Post upload ho gayi!\n\n"
            f"📌 Title: {title}\n"
            f"🖼️ Image: ✓\n"
            f"🔗 Link: ✓\n"
            f"🏷️ Type: {prem}\n"
            f"🆔 ID: `{post_id}`\n\n"
            f"{'💡 Premium karne ke liye: /premium ' + post_id if not is_premium else ''}"
        )
    else:
        await edit(f"{prefix}❌ Firebase save fail! Dobara try karo.")

# ── Handlers ──

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    # Sirf admin
    if ADMIN_ID and msg.from_user.id != ADMIN_ID:
        await msg.reply_text("❌ Access denied!")
        return

    text = msg.caption or msg.text or ""

    # Terabox link check
    if not extract_terabox_link(text):
        await msg.reply_text(
            "⚠️ Terabox link nahi mila!\n\n"
            "📋 Sahi format:\n"
            "```\nVideo Title\nhttps://terasharelink.com/s/xxx\n```\n"
            "+ Image attach karo",
            parse_mode='Markdown'
        )
        return

    # Photo file_id
    file_id = None
    if msg.photo:
        file_id = msg.photo[-1].file_id  # Pehli/sabse badi image
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith('image/'):
        file_id = msg.document.file_id

    queue_size = post_queue.qsize()
    status_msg = await msg.reply_text(
        f"⏳ Queue mein add hua! Position: #{queue_size + 1}\nThoda wait karo..." if queue_size > 0
        else "⏳ Processing shuru..."
    )

    post_queue.put({
        'bot':       context.bot,
        'chat_id':   msg.chat_id,
        'status_id': status_msg.message_id,
        'text':      text,
        'file_id':   file_id,
        'pos':       queue_size + 1,
        'total':     queue_size + 1
    })

async def cmd_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /premium post_id → post ko premium karo
    /premium post_id free → post ko free karo
    /premium → recent posts dikhao
    """
    msg = update.message
    if ADMIN_ID and msg.from_user.id != ADMIN_ID:
        await msg.reply_text("❌ Access denied!")
        return

    args = context.args

    # /premium — recent posts dikhao
    if not args:
        await msg.reply_text("⏳ Recent posts fetch ho rahi hain...")
        posts = get_recent_posts(10)
        if not posts:
            await msg.reply_text("❌ Koi post nahi mili!")
            return
        text = "📋 *Recent 10 Posts:*\n\n"
        for i, p in enumerate(posts, 1):
            status = "👑" if p.get('premium') else "💚"
            name = p.get('name', 'No title')[:30]
            pid = p.get('id', '')
            text += f"{i}. {status} `{pid}`\n    📌 {name}\n\n"
        text += "💡 Premium karne ke liye:\n`/premium post_id`\nFree karne ke liye:\n`/premium post_id free`"
        await msg.reply_text(text, parse_mode='Markdown')
        return

    post_id = args[0].strip()
    make_free = len(args) > 1 and args[1].lower() == 'free'

    # Post exist check
    try:
        res = requests.get(f"{FIREBASE_URL}/posts/{post_id}.json", timeout=10)
        post_data = res.json()
        if not post_data:
            await msg.reply_text(f"❌ Post `{post_id}` nahi mili!", parse_mode='Markdown')
            return
    except:
        await msg.reply_text("❌ Firebase se connect nahi ho pa raha!")
        return

    # Update premium status
    new_status = not make_free
    success = update_post_premium(post_id, new_status)

    if success:
        emoji = "👑 Premium" if new_status else "💚 Free"
        name = post_data.get('name', 'Unknown')[:40]
        await msg.reply_text(
            f"✅ Post update ho gayi!\n\n"
            f"📌 {name}\n"
            f"🏷️ Status: {emoji}\n"
            f"🆔 `{post_id}`",
            parse_mode='Markdown'
        )
    else:
        await msg.reply_text("❌ Update fail! Dobara try karo.")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Queue aur bot status dikhao"""
    msg = update.message
    if ADMIN_ID and msg.from_user.id != ADMIN_ID:
        return

    q_size = post_queue.qsize()
    posts = get_recent_posts(5)

    text = f"🤖 *Bot Status*\n\n"
    text += f"📊 Queue: {q_size} posts pending\n\n"

    if posts:
        text += "📋 *Last 5 Uploads:*\n"
        for p in posts:
            status = "👑" if p.get('premium') else "💚"
            name = p.get('name', 'No title')[:25]
            text += f"{status} {name}\n"

    await msg.reply_text(text, parse_mode='Markdown')

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if ADMIN_ID and msg.from_user.id != ADMIN_ID:
        return
    await msg.reply_text(
        "📖 *Bot Commands:*\n\n"
        "📤 *Post Upload:*\n"
        "Image ke saath message bhejo:\n"
        "```\nVideo Title\nhttps://terabox_link\n```\n\n"
        "👑 *Premium Commands:*\n"
        "`/premium` — recent posts dekho\n"
        "`/premium post_id` — post ko premium karo\n"
        "`/premium post_id free` — post ko free karo\n\n"
        "📊 *Other:*\n"
        "`/status` — bot aur queue status\n"
        "`/help` — yeh message\n\n"
        "💡 *Tips:*\n"
        "• 10 posts ek saath forward karo — queue mein jayenge\n"
        "• Pehli image aur pehla terabox link use hoga\n"
        "• `#premium` likhne se directly premium upload hoga",
        parse_mode='Markdown'
    )

def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT)

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN set nahi hai!")

    # Queue worker
    Thread(target=queue_worker, daemon=True).start()
    # Flask
    Thread(target=run_flask, daemon=True).start()

    # Bot
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("premium", cmd_premium))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

    log.info(f"✅ Bot start ho gaya! Port: {PORT}")
    app.run_polling(allowed_updates=["message"])

if __name__ == "__main__":
    main()
