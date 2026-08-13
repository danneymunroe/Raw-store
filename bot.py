import json
import logging
import sqlite3
import re
import os
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes


# --- CONFIGURATION ---
TOKEN = '8821498286:AAEndRBjq4k61R4wXGOhP_vP44u8YCsvT2k'  # From BotFather
ADMIN_CHAT_ID = '2087550317'  # From @userinfobot

# ⚠️ VERY IMPORTANT: PUT YOUR HOSTED GITHUB PAGES URL HERE ⚠️
# It MUST start with https://
WEB_APP_URL = "https://danneymunroe.github.io/Raw-store/"


logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- SQLite DATABASE PERSISTENCE SETUP ---
conn = sqlite3.connect('raw_store.db', check_same_thread=False)
cursor = conn.cursor()

# Create Tables
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY, 
        username TEXT, 
        first_name TEXT
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        product_id TEXT, 
        type TEXT, 
        size TEXT, 
        price INTEGER, 
        phone TEXT, 
        location TEXT, 
        lock_name TEXT, 
        user_id INTEGER
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS products (
        id TEXT PRIMARY KEY,
        title TEXT,
        price INTEGER,
        image TEXT,
        is_live INTEGER,
        remaining INTEGER,
        locked_by TEXT
    )
''')
conn.commit()

# Populate Default Catalog if empty
cursor.execute("SELECT COUNT(*) FROM products")
if cursor.fetchone()[0] == 0:
    default_products = [
        ('RAW 017', 'MISERY WINNERS CLUB', 2500, './raw-01.png', 1, 7, None),
        ('RAW 018', 'CREATE YOUR OWN LEGACY', 2200, './raw-02.png', 1, 7, None),
        ('RAW 019', 'IN DREAMS', 2400, './raw-03.png', 1, 7, None),
        ('RAW 020', 'BRAND DEPT.', 2500, './raw-04.png', 1, 7, None)
    ]
    cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", default_products)
    conn.commit()

# Shared Global References
loop = None
tg_app = None

# Admin privilege check
def is_admin(user):
    return str(user.id) == str(ADMIN_CHAT_ID) or (user.username and user.username.lower() == 'mr_two')

# --- BACKGROUND HTTP DAEMON SERVER FOR MINI APP API ---
class RawHTTPHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        """Handle CORS Preflight"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        """Serve Live Database Catalog straight to index.html"""
        if self.path == '/products':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            db_conn = sqlite3.connect('raw_store.db')
            db_cursor = db_conn.cursor()
            db_cursor.execute("SELECT id, title, price, image, is_live, remaining, locked_by FROM products")
            rows = db_cursor.fetchall()
            db_conn.close()
            
            product_list = []
            for r in rows:
                product_list.append({
                    "id": r[0],
                    "title": r[1],
                    "price": r[2],
                    "image": r[3],
                    "isLive": bool(r[4]),
                    "remaining": r[5],
                    "lockedBy": r[6]
                })
            
            self.wfile.write(json.dumps(product_list).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        """Receives Checkout payload, processes inventory decrement or locks"""
        if self.path == '/order':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            order = json.loads(post_data.decode('utf-8'))
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "received"}).encode('utf-8'))

            # Process Order in Database
            db_conn = sqlite3.connect('raw_store.db')
            db_cursor = db_conn.cursor()
            
            item_id = order['id']
            price = order['price']
            
            # Safe parser for user id
            user_id_str = order.get('userId')
            try:
                user_id = int(user_id_str) if user_id_str else int(ADMIN_CHAT_ID)
            except (ValueError, TypeError):
                user_id = int(ADMIN_CHAT_ID)

            # Save the transaction
            db_cursor.execute('''
                INSERT INTO orders (product_id, type, size, price, phone, location, lock_name, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (item_id, order['type'], order['size'], price, order['phone'], order['location'], order['lockName'], user_id))

            if order['type'] == 'LOCK':
                # Murder Drop: Drop remaining to 0 and archive
                db_cursor.execute('''
                    UPDATE products 
                    SET is_live = 0, remaining = 0, locked_by = ? 
                    WHERE id = ?
                ''', (order['lockName'], item_id))
                
                alert_text = (
                    f"💀 KILL SHOT: DROP LOCKED 💀\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"Item: {item_id} - {order['title']}\n"
                    f"Size: {order['size']}\n"
                    f"Archive Name: {order['lockName']}\n"
                    f"Total: {price} BIRR\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"Phone: {order['phone']}\n"
                    f"Location/GPS: {order['location']}"
                )
            else:
                # Normal buy: Decrement inventory by 1
                db_cursor.execute("SELECT remaining FROM products WHERE id = ?", (item_id,))
                curr_remaining = db_cursor.fetchone()[0]
                new_remaining = max(0, curr_remaining - 1)
                
                if new_remaining <= 0:
                    db_cursor.execute("UPDATE products SET is_live = 0, remaining = 0 WHERE id = ?", (item_id,))
                else:
                    db_cursor.execute("UPDATE products SET remaining = ? WHERE id = ?", (new_remaining, item_id))
                    
                alert_text = (
                    f"📦 NEW NORMAL ORDER ({new_remaining}/7 remaining) 📦\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"Item: {item_id} - {order['title']}\n"
                    f"Size: {order['size']}\n"
                    f"Total: {price} BIRR\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"Phone: {order['phone']}\n"
                    f"Location/GPS: {order['location']}"
                )

            db_conn.commit()
            db_conn.close()

            # Async trigger to message the Telegram admin and client from HTTP Thread
            if loop:
                asyncio.run_coroutine_threadsafe(send_notifications(user_id, alert_text), loop)
            else:
                print("Warning: Event loop not registered yet. Notification skipped.")


async def send_notifications(user_id, alert_text):
    """Sends confirmations to both customer and @mr_two"""
    try:
        await tg_app.bot.send_message(chat_id=ADMIN_CHAT_ID, text=alert_text)
        
        # Customer receipt delivery
        if user_id and str(user_id) != str(ADMIN_CHAT_ID):
            cust_msg = "📦 RAW ORDER CONFIRMED.\nYour ticket has been generated. Keep it saved for physical delivery proof within 48 hours."
            await tg_app.bot.send_message(chat_id=user_id, text=cust_msg)
    except Exception as e:
        print(f"Error sending automated notifications: {e}")


def run_http_server():
    """Runs standard library server bounded to the Render PORT environment variable"""
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), RawHTTPHandler)
    print(f"Background HTTP Service running on port {port}...")
    server.serve_forever()


# --- BOT TELEGRAM STREAM HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Save User into DB for broadcast
    cursor.execute("INSERT OR REPLACE INTO users (id, username, first_name) VALUES (?, ?, ?)",
                   (user.id, user.username, user.first_name))
    conn.commit()
    
    kb = [
        [KeyboardButton("ENTER RAW STORE", web_app=WebAppInfo(url=WEB_APP_URL))]
    ]
    reply_markup = ReplyKeyboardMarkup(kb, resize_keyboard=True)

    await update.message.reply_text(
        "RAW | STRAIGHT TO THE MEAT\nNO ADDITIVES.\n\nClick below to enter the private collection.",
        reply_markup=reply_markup
    )


# --- DYNAMIC ADMIN CMS COMMANDS ---
async def add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Adds a new item to the store: /add ID | TITLE | PRICE | IMAGE"""
    if not is_admin(update.effective_user):
        return
        
    raw_args = " ".join(context.args)
    try:
        parts = [p.strip() for p in raw_args.split('|')]
        if len(parts) != 4:
            raise ValueError
        
        p_id, title, price, image = parts
        cursor.execute("INSERT INTO products VALUES (?, ?, ?, ?, 1, 7, NULL)", (p_id, title, int(price), f"./{image}"))
        conn.commit()
        await update.message.reply_text(f"Successfully added {p_id} ({title}) to the live catalog.")
    except Exception:
        await update.message.reply_text("Format: `/add RAW 021 | BOX HOODIE | 3200 | raw-05.png`", parse_mode='Markdown')


async def edit_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edits any details of a live product: /edit ID | FIELD | NEW_VALUE"""
    if not is_admin(update.effective_user):
        return
        
    raw_args = " ".join(context.args)
    try:
        parts = [p.strip() for p in raw_args.split('|')]
        if len(parts) != 3:
            raise ValueError
            
        p_id, field, value = parts
        
        # Safety check fields
        allowed_fields = ['title', 'price', 'image', 'remaining', 'is_live']
        if field.lower() not in allowed_fields:
            await update.message.reply_text(f"Invalid field. Editable fields: {allowed_fields}")
            return
            
        if field.lower() == 'price' or field.lower() == 'remaining' or field.lower() == 'is_live':
            value = int(value)
            
        query = f"UPDATE products SET {field.lower()} = ? WHERE id = ?"
        cursor.execute(query, (value, p_id))
        conn.commit()
        
        await update.message.reply_text(f"Updated field '{field}' to '{value}' on product {p_id}.")
    except Exception:
        await update.message.reply_text("Format: `/edit RAW 017 | price | 2800` or `/edit RAW 017 | remaining | 4`", parse_mode='Markdown')


async def inventory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays entire store stock details"""
    if not is_admin(update.effective_user):
        return
        
    cursor.execute("SELECT id, title, price, remaining, is_live, locked_by FROM products")
    rows = cursor.fetchall()
    
    report = "📦 RAW LIVE INVENTORY REPORT 📦\n\n"
    for r in rows:
        status = "LIVE" if r[4] == 1 else "GONE"
        lock_text = f" (Locked by {r[5]})" if r[5] else ""
        report += f"• *{r[0]}* - {r[1]}\n  Price: {r[2]} BIRR | Stock: {r[3]}/7 | Status: {status}{lock_text}\n\n"
        
    await update.message.reply_text(report, parse_mode='Markdown')


async def blast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a global broadcast to all saved users inside the SQLite DB"""
    if not is_admin(update.effective_user):
        return
        
    broadcast_msg = " ".join(context.args)
    if not broadcast_msg:
        await update.message.reply_text("Usage: /blast [message]")
        return

    cursor.execute("SELECT id FROM users")
    saved_users = cursor.fetchall()
    
    await update.message.reply_text(f"Starting broadcast to {len(saved_users)} customers...")
    success_count = 0
    
    for u in saved_users:
        try:
            await context.bot.send_message(chat_id=u[0], text=f"📢 RAW UPDATE:\n\n{broadcast_msg}")
            success_count += 1
        except Exception:
            pass
            
    await update.message.reply_text(f"Broadcast complete. Delivered to {success_count}/{len(saved_users)} users.")


# --- TWO-WAY CLIENT-ADMIN CHAT BRIDGE ---
async def chat_bridge_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # If @mr_two replies to a forwarded customer thread
    if is_admin(user) and update.message.reply_to_message:
        replied_text = update.message.reply_to_message.text or update.message.reply_to_message.caption
        if replied_text:
            id_match = re.search(r"\(ID:\s*(\d+)\)", replied_text)
            if id_match:
                recipient_id = int(id_match.group(1))
                try:
                    await context.bot.send_message(
                        chat_id=recipient_id,
                        text=f"💬 MESSAGE FROM RAW ADMIN:\n\n{update.message.text}"
                    )
                    await update.message.reply_text("Reply forwarded.")
                    return
                except Exception as e:
                    await update.message.reply_text(f"Forward failed: {e}")
                    return

    # If a normal user messages the bot, bridge it to @mr_two
    if not is_admin(user):
        forward_text = (
            f"💬 CLIENT MESSAGE\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"From: @{user.username or 'NoUsername'} ({user.first_name})\n"
            f"Account ID: (ID: {user.id})\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{update.message.text}"
        )
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=forward_text)
        await update.message.reply_text("Message received. Admin will reply directly to this thread.")


# --- DYNAMIC EVENT LOOP REGISTRATION IN POST_INIT HOOK ---
async def post_init(application: Application):
    """Registers the active loop context natively so threads can run async requests safely"""
    global loop
    loop = asyncio.get_running_loop()


if __name__ == '__main__':
    # Spin up background HTTP Service running on port
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()

    print("RAW Telegram Bot is booting up...")
    tg_app = Application.builder().token(TOKEN).post_init(post_init).build()

    # Commands
    tg_app.add_handler(CommandHandler('start', start))
    tg_app.add_handler(CommandHandler('add', add_product))
    tg_app.add_handler(CommandHandler('edit', edit_product))
    tg_app.add_handler(CommandHandler('inventory', inventory))
    tg_app.add_handler(CommandHandler('blast', blast))
    
    # Route support bridge messages
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_bridge_handler))

    print("Bot is live. Waiting for users...")
    tg_app.run_polling()
