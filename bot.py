import json
import logging
import sqlite3
import re
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- CONFIGURATION ---
TOKEN = '8821498286:AAEndRBjq4k61R4wXGOhP_vP44u8YCsvT2k'  # From BotFather
ADMIN_CHAT_ID = '2087550317'  # From @userinfobot

# ⚠️ VERY IMPORTANT: PUT YOUR HOSTED GITHUB PAGES URL HERE ⚠️
# It MUST start with https://
WEB_APP_URL = "https://danneymunroe.github.io/Raw-store/"


logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- UPGRADE 4: PRODUCTION SQLITE DATABASE ---
conn = sqlite3.connect('raw_store.db', check_same_thread=False)
cursor = conn.cursor()

# Create Tables if they do not exist
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
    CREATE TABLE IF NOT EXISTS locks (
        product_id TEXT PRIMARY KEY, 
        lock_name TEXT
    )
''')
conn.commit()

# Load initial state of locks on boot
locked_drops = {}
cursor.execute("SELECT product_id, lock_name FROM locks")
for row in cursor.fetchall():
    locked_drops[row[0]] = row[1]

print(f"Loaded locks from DB: {locked_drops}")


def build_web_app_url():
    """Generates the URL with current locked drop query parameters dynamically"""
    if not locked_drops:
        return WEB_APP_URL
        
    params = []
    for item_id, user_name in locked_drops.items():
        formatted_id = item_id.replace(" ", "_")
        params.append(f"{formatted_id}:{user_name}")
        
    query_string = f"?locked={','.join(params)}"
    return WEB_APP_URL + query_string


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends the Web App Button with the dynamic state tracking URL"""
    user = update.effective_user
    
    # Save user into DB for Broadcast Tool
    cursor.execute("INSERT OR REPLACE INTO users (id, username, first_name) VALUES (?, ?, ?)",
                   (user.id, user.username, user.first_name))
    conn.commit()
    
    # Notify Admin of new traffic
    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID, 
            text=f"👁️ TRAFFIC ALERT\n@{user.username or user.first_name} just entered the bot."
        )
    except Exception as e:
        print(f"Admin alert failed: {e}")

    current_url = build_web_app_url()

    kb = [
        [KeyboardButton("ENTER RAW STORE", web_app=WebAppInfo(url=current_url))]
    ]
    reply_markup = ReplyKeyboardMarkup(kb, resize_keyboard=True)

    await update.message.reply_text(
        "RAW | STRAIGHT TO THE MEAT\nNO ADDITIVES.\n\nClick below to enter the private collection.",
        reply_markup=reply_markup
    )


async def web_app_data_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives checkout orders, stores locked drops persistently, and updates state"""
    user = update.effective_user
    data = json.loads(update.message.web_app_data.data)
    
    item_id = data['id']
    price = data['price']
    
    # Save Order into SQLite
    cursor.execute('''
        INSERT INTO orders (product_id, type, size, price, phone, location, lock_name, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (item_id, data['type'], data['size'], price, data['phone'], data['location'], data['lockName'], user.id))
    conn.commit()
    
    if data['type'] == 'LOCK':
        # Add lock state to memory and persistent DB
        locked_drops[item_id] = data['lockName']
        cursor.execute("INSERT OR REPLACE INTO locks (product_id, lock_name) VALUES (?, ?)", (item_id, data['lockName']))
        conn.commit()
        
        customer_msg = (
            f"💀 DROP MURDERED.\n\n"
            f"{item_id} is now permanently closed and archived under {data['lockName']}.\n"
            f"Total: {price} BIRR.\n"
            f"We will contact your phone {data['phone']} for delivery."
        )
        
        admin_alert = (
            f"🩸 KILL SHOT: DROP LOCKED 🩸\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Item: {item_id} - {data['title']}\n"
            f"Size: {data['size']}\n"
            f"Archive Name: {data['lockName']}\n"
            f"Total Value: {price} BIRR\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Customer: @{user.username or user.first_name}\n"
            f"Phone: {data['phone']}\n"
            f"Location/GPS: {data['location']}"
        )
    else:
        customer_msg = (
            f"GOT IT.\n\n"
            f"Your order for {item_id} (Size {data['size']}) has been secured.\n"
            f"Total: {price} BIRR.\n"
            f"Guaranteed delivery in 48 hours."
        )
        
        admin_alert = (
            f"📦 NEW NORMAL ORDER 📦\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Item: {item_id} - {data['title']}\n"
            f"Size: {data['size']}\n"
            f"Total Value: {price} BIRR\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Customer: @{user.username or user.first_name}\n"
            f"Phone: {data['phone']}\n"
            f"Location/GPS: {data['location']}"
        )

    await update.message.reply_text(customer_msg)
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_alert)


# --- UPGRADE 5: TWO-WAY CLIENT-ADMIN CHAT BRIDGE ---
async def chat_bridge_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Intercepts client chat messages and routes them to admin with reply handles"""
    user = update.effective_user
    
    # Check if this is the Admin replying to a routed user message
    if str(user.id) == str(ADMIN_CHAT_ID) and update.message.reply_to_message:
        replied_text = update.message.reply_to_message.text or update.message.reply_to_message.caption
        if replied_text:
            # Parse User ID out of the original metadata wrapper "(ID: 12345)"
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

    # If it's a regular user sending a chat message, route it to Admin
    if str(user.id) != str(ADMIN_CHAT_ID):
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


# --- UPGRADE 6: ADMIN VIP BROADCAST TOOL ---
async def blast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a global broadcast to all saved users inside the SQLite DB"""
    user = update.effective_user
    if str(user.id) != str(ADMIN_CHAT_ID):
        return  # Silent ignore
        
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
            await context.bot.send_message(
                chat_id=u[0],
                text=f"📢 RAW UPDATE:\n\n{broadcast_msg}"
            )
            success_count += 1
        except Exception:
            pass # Handles cases where user blocked the bot
            
    await update.message.reply_text(f"Broadcast complete. Delivered to {success_count}/{len(saved_users)} users.")


if __name__ == '__main__':
    print("RAW Telegram Bot is booting up...")
    app = Application.builder().token(TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('blast', blast))
    
    # Receive data from WebApp
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_data_handler))
    
    # Route chat message stream through bridge
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_bridge_handler))

    print("Bot is live. Waiting for users...")
    app.run_polling()