import os
import re
import time
import asyncio
import requests
import logging
from queue import Queue
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# ── Config ──
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
FIREBASE_URL = os.environ.get("FIREBASE_URL", "https://pagalbhabhi-1ac18-default-rtdb.asia-southeast1.firebasedatabase.app")
ADMIN_ID     = int(os.environ.get("ADMIN_ID", "0"))
PORT         = int(os.environ.get("PORT", "10000"))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ── Flask app (Render web service ke liye) ──
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "✅ Pagal Bhabhi Bot is running!"

@flask_app.route('/health')
def health():
    return "OK", 200

# ── Post Queue (ek ek karke process ho) ──
post_queue = Queue()

# ── Terabox domains ──
TERABOX_DOMAINS = [
    'terabox.com','1024terabox.com','teraboxapp.com','terabox.app',
    'nephobox.com','mirrorbox.com','momerybox.com','freeterabox.com',
    'teraboxlink.com','4funbox.com','terafileshare.com','teraboxshare.com',
    'terasharelink.com','1024tera.com','mirrobox.com','tibibox.com'
]

def is_terabox(url):
    for domain in TERABOX_DOMAINS:
        if domain in url:
            return True
    return False

def extract_terabox_link(text):
    urls = re.findall(r'https?://[^\s\n]+', text or '')
    for url in urls:
        if is_terabox(url.strip()):
            return url.strip()
    return None

def extract_title(text):
    if not text:
        return None
    lines = [l.strip() for l in text.strip().split('\n') if l.strip()]
    for line in lines:
        if not line.startswith('http') and not line.startswith('#'):
            return line[:80]
    return None

def upload_to_catbox(file_bytes, filename="image.jpg"):
    # Primary: Catbox
    try:
        res = requests.post(
            'https://catbox.moe/user/api.php',
            data={'reqtype': 'fileupload'},
            files={'fileToUpload': (filename, file_bytes, 'image/jpeg')},
            timeout=30
        )
        url = res.text.strip()
        if url.startswith('https://'):
            return url
    except Exception as e:
        log.error(f"Catbox error: {e}")

    # Fallback: freeimage.host
    try:
        import base64
        b64 = base64.b64encode(file_bytes).decode()
        res2 = requests.post(
            'https://freeimage.host/api/1/upload',
            data={
                'key': '6d207e02198a847aa98d0a2a901485a5',
                'action': 'upload',
                'source': b64,
                'format': 'json'
            },
            timeout=30
        )
        data = res2.json()
        if data.get('status_code') == 200:
            return data['image']['url']
    except Exception as e:
        log.error(f"Freeimage error: {e}")

    return None

def save_to_firebase(title, image_url, terabox_url, is_premium=False):
    post_id = f"post_{int(time.time() * 1000)}"
    try:
        res = requests.get(f"{FIREBASE_URL}/posts.json?orderBy=\"order\"&limitToLast=1", timeout=10)
        data = res.json()
        max_order = 0
        if data:
            for v in data.values():
                max_order = max(max_order, v.get('order', 0))
    except:
        max_order = int(time.time())

    post = {
        "name": title,
        "image": image_url,
        "redirect": terabox_url,
        "premium": is_premium,
        "isNew": True,
        "order": max_order + 1,
        "createdAt": int(time.time() * 1000)
    }

    res = requests.put(
        f"{FIREBASE_URL}/posts/{post_id}.json",
        json=post,
        timeout=15
    )
    return res.status_code == 200, post_id

# ── Queue Worker (background thread) ──
def queue_worker():
    """Queue se ek ek post process karo"""
    while True:
        try:
            job = post_queue.get()
            if job is None:
                break
            asyncio.run(process_job(job))
            post_queue.task_done()
            time.sleep(1)  # Har post ke baad 1 sec gap
        except Exception as e:
            log.error(f"Queue worker error: {e}")

async def process_job(job):
    """Ek post ko process karo"""
    bot         = job['bot']
    chat_id     = job['chat_id']
    status_id   = job['status_id']
    text        = job['text']
    file_id     = job['file_id']
    queue_pos   = job['queue_pos']
    total       = job['total']

    async def edit(msg):
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=status_id, text=msg)
        except:
            pass

    prefix = f"[{queue_pos}/{total}] " if total > 1 else ""

    # Terabox link
    terabox_url = extract_terabox_link(text)
    if not terabox_url:
        await edit(f"{prefix}⚠️ Terabox link nahi mila!")
        return

    # Title
    title = extract_title(text) or f"Video {int(time.time())}"
    is_premium = '#premium' in text.lower()

    # Image upload
    image_url = None
    if file_id:
        await edit(f"{prefix}📤 Image upload ho rahi hai...")
        try:
            file = await bot.get_file(file_id)
            file_bytes = await file.download_as_bytearray()
            image_url = upload_to_catbox(bytes(file_bytes))
            if not image_url:
                await edit(f"{prefix}❌ Image upload fail!")
                return
        except Exception as e:
            await edit(f"{prefix}❌ Image error: {e}")
            return
    else:
        image_url = "https://files.catbox.moe/placeholder.jpg"

    # Firebase save
    await edit(f"{prefix}💾 Site pe save ho raha hai...")
    success, post_id = save_to_firebase(title, image_url, terabox_url, is_premium)

    if success:
        prem = " 👑 Premium" if is_premium else " 💚 Free"
        await edit(
            f"{prefix}✅ Done!\n\n"
            f"📌 {title}\n"
            f"🖼️ Image: ✓\n"
            f"🔗 Terabox: ✓\n"
            f"🏷️{prem}"
        )
    else:
        await edit(f"{prefix}❌ Firebase save fail!")

# ── Telegram Handler ──
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
    terabox_url = extract_terabox_link(text)
    if not terabox_url:
        await msg.reply_text("⚠️ Terabox link nahi mila!\n\nFormat:\nTitle\nhttps://terabox_link")
        return

    # Photo/image file_id
    file_id = None
    if msg.photo:
        file_id = msg.photo[-1].file_id
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith('image/'):
        file_id = msg.document.file_id

    # Queue mein add karo
    queue_size = post_queue.qsize()
    status_msg = await msg.reply_text(
        f"⏳ Queue mein add hua! Position: #{queue_size + 1}" if queue_size > 0
        else "⏳ Processing shuru..."
    )

    post_queue.put({
        'bot':       context.bot,
        'chat_id':   msg.chat_id,
        'status_id': status_msg.message_id,
        'text':      text,
        'file_id':   file_id,
        'queue_pos': queue_size + 1,
        'total':     queue_size + 1
    })

def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT)

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN set nahi hai!")

    # Queue worker thread start karo
    worker_thread = Thread(target=queue_worker, daemon=True)
    worker_thread.start()

    # Flask thread start karo
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Telegram bot start karo
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.ALL, handle_message))

    log.info(f"Bot start ho gaya! Port: {PORT}")
    app.run_polling(allowed_updates=["message"])

if __name__ == "__main__":
    main()
