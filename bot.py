import os
import asyncio
import json
import random
import re
from datetime import datetime, timedelta
from pyrogram import Client, filters
from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton, Message
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired

# --- Settings ---
BOT_TOKEN = (os.environ.get("BOT_TOKEN") or "").strip()
OWNER_ID_RAW = (os.environ.get("OWNER_ID") or "").strip()
API_ID_RAW = (os.environ.get("API_ID") or "").strip()
API_HASH = (os.environ.get("API_HASH") or "").strip()

try:
    OWNER_ID = int(OWNER_ID_RAW)
    API_ID = int(API_ID_RAW)
except ValueError as exc:
    raise RuntimeError("OWNER_ID and API_ID must be numeric environment variables") from exc

missing_settings = []
if not BOT_TOKEN:
    missing_settings.append("BOT_TOKEN")
if OWNER_ID <= 0:
    missing_settings.append("OWNER_ID")
if API_ID <= 0:
    missing_settings.append("API_ID")
if not API_HASH:
    missing_settings.append("API_HASH")
if missing_settings:
    raise RuntimeError(f"Missing or invalid environment variables: {', '.join(missing_settings)}")

# --- Data file ---
DATA_FILE = "/app/data/bot_data.json"
login_sessions = {}

# --- Load/Save Data ---
def load_data():
    if not os.path.exists(DATA_FILE):
        default_data = {
            "accounts": [],
            "templates": [],
            "groups": [],
            "timer": 200,
            "is_running": False,
            "stats": {"sent_count": 0, "failed_count": 0},
            "user_state": {},
            "last_message": {},
            "joined_channels": {}
        }
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(default_data, f, ensure_ascii=False, indent=4)
        return default_data
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

db = load_data()
db.setdefault("accounts", [])
db.setdefault("templates", [])
db.setdefault("groups", [])
db.setdefault("timer", 200)
db.setdefault("is_running", False)
db.setdefault("stats", {"sent_count": 0, "failed_count": 0})
db["stats"].setdefault("sent_count", 0)
db["stats"].setdefault("failed_count", 0)
db.setdefault("user_state", {})
db.setdefault("last_message", {})
db.setdefault("joined_channels", {})

# --- Bot Client ---
app = Client("auto_post_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- Diagnostics ---
# Keep a small, non-sensitive trace of incoming private messages.  Previously,
# messages from an account whose OWNER_ID was wrong were silently ignored,
# which made a running Railway process look like a broken bot.
@app.on_message(filters.private & filters.incoming)
async def trace_private_messages(client: Client, message: Message):
    if not message.from_user:
        return
    command = (message.text or message.caption or "<non-text>")[:80].replace("\n", " ")
    print(
        f"📨 Incoming private message from user_id={message.from_user.id}: "
        f"{command!r}"
    )

# --- Main Keyboard ---
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("➕ Add Acc"), KeyboardButton("➖ Del Acc")],
        [KeyboardButton("📋 Accounts"), KeyboardButton("📋 Groups")],
        [KeyboardButton("📝 Add Text"), KeyboardButton("🗑 Del Text")],
        [KeyboardButton("📢 Add Group"), KeyboardButton("❌ Del Group")],
        [KeyboardButton("▶️ Start"), KeyboardButton("⏹ Stop")],
        [KeyboardButton("⏱ Timer"), KeyboardButton("📊 Stats")],
        [KeyboardButton("🗑 Clear All")]
    ],
    resize_keyboard=True
)

# --- Extract links from text ---
def extract_links(text):
    patterns = [
        r'https://t\.me/[a-zA-Z0-9_]+',
        r't\.me/[a-zA-Z0-9_]+',
        r'@[a-zA-Z0-9_]+'
    ]
    links = []
    for pattern in patterns:
        matches = re.findall(pattern, text)
        links.extend(matches)
    return links

# --- Clean group link ---
def clean_group_link(link):
    if link.startswith("https://t.ne/"):
        link = link.replace("https://t.ne/", "@")
    elif link.startswith("https://t.me/"):
        link = link.replace("https://t.me/", "@")
    elif link.startswith("t.me/"):
        link = link.replace("t.me/", "@")
    elif not link.startswith("@"):
        link = f"@{link}"
    return link

# --- Auto Leave Channels (After 12 Hours) ---
async def auto_leave_channels():
    global db
    while True:
        try:
            now = datetime.now()
            to_remove = []
            
            for channel, join_time in db.get("joined_channels", {}).items():
                try:
                    join_dt = datetime.fromisoformat(join_time)
                    if now - join_dt > timedelta(hours=12):
                        to_remove.append(channel)
                except:
                    to_remove.append(channel)
            
            for channel in to_remove:
                for idx, session_str in enumerate(db["accounts"]):
                    try:
                        user_app = Client(f"leave_session_{idx}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
                        await user_app.start()
                        await user_app.leave_chat(channel)
                        await user_app.stop()
                        print(f"🚪 Acc {idx+1} left {channel}")
                    except Exception as e:
                        print(f"❌ Leave {channel} failed: {e}")
                
                db["joined_channels"].pop(channel, None)
                save_data(db)
            
            await asyncio.sleep(3600)
        except Exception as e:
            print(f"❌ Auto leave error: {e}")
            await asyncio.sleep(60)

# --- Auto Post Loop ---
async def auto_posting_loop():
    global db
    account_index = 0
    
    asyncio.create_task(auto_leave_channels())
    
    while db["is_running"]:
        if not db["accounts"] or not db["templates"] or not db["groups"]:
            db["is_running"] = False
            save_data(db)
            break
        
        session_str = db["accounts"][account_index]
        account_number = account_index + 1
        
        try:
            user_app = Client(f"user_session_{account_index}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
            await user_app.start()
            
            for group in db["groups"]:
                if not db["is_running"]:
                    break
                
                if group in db.get("last_message", {}):
                    last_msg = db["last_message"][group]
                    if last_msg.get("from_our_account", False):
                        print(f"⏭️ Skip {group}")
                        continue
                
                template = random.choice(db["templates"])
                
                try:
                    sent_msg = await user_app.send_message(group, template)
                    db["stats"]["sent_count"] += 1
                    
                    if group not in db["last_message"]:
                        db["last_message"][group] = {}
                    db["last_message"][group]["from_our_account"] = True
                    db["last_message"][group]["message_id"] = sent_msg.id
                    
                    save_data(db)
                    print(f"✅ Acc {account_number} sent to {group}")
                except Exception as e:
                    db["stats"]["failed_count"] += 1
                    save_data(db)
                    print(f"❌ Acc {account_number} failed to {group}: {e}")
                
                await asyncio.sleep(2)
            
            await user_app.stop()
            
        except Exception as e:
            print(f"❌ Error in acc {account_number}: {e}")
        
        account_index = (account_index + 1) % len(db["accounts"])
        await asyncio.sleep(db.get("timer", 200))

# --- Auto Join on Reply (Any user) ---
@app.on_message(filters.text & filters.private)
async def handle_replies(client: Client, message: Message):
    if message.from_user.id == OWNER_ID:
        return
    
    links = extract_links(message.text)
    if not links:
        return
    
    for idx, session_str in enumerate(db["accounts"]):
        try:
            user_app = Client(f"reply_session_{idx}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
            await user_app.start()
            
            for link in links:
                try:
                    clean_link = clean_group_link(link)
                    await user_app.join_chat(clean_link)
                    
                    db["joined_channels"][clean_link] = datetime.now().isoformat()
                    save_data(db)
                    print(f"✅ Acc {idx+1} joined {clean_link} (will leave after 12h)")
                    
                except Exception as e:
                    print(f"❌ Join {link} failed: {e}")
            
            await user_app.stop()
            break
        except Exception as e:
            print(f"❌ Error in acc {idx+1}: {e}")

# --- Track group messages ---
@app.on_message(filters.group & filters.incoming)
async def track_group_messages(client: Client, message: Message):
    chat_id = str(message.chat.id)
    chat_username = f"@{message.chat.username}" if message.chat.username else None
    
    if chat_id in db["groups"] or (chat_username and chat_username in db["groups"]):
        if chat_id not in db["last_message"]:
            db["last_message"][chat_id] = {}
        
        db["last_message"][chat_id]["from_our_account"] = False
        db["last_message"][chat_id]["message_id"] = message.id
        save_data(db)

# --- /start command ---
@app.on_message(group=-1)
async def start_cmd(client: Client, message: Message):
    raw_text = (message.text or message.caption or "").strip()
    command = raw_text.split(maxsplit=1)[0].split("@", 1)[0].lower()
    if command != "/start":
        return

    if not message.from_user:
        return

    if message.from_user.id != OWNER_ID:
        print(
            f"⚠️ Unauthorized /start from user_id={message.from_user.id}; "
            f"configured OWNER_ID={OWNER_ID}"
        )
        return await message.reply_text(
            "⛔ هذا البوت مخصص لمالكه فقط.\n"
            "رقم حسابك لا يطابق OWNER_ID الموجود في إعدادات التشغيل."
        )

    db["user_state"].pop(str(OWNER_ID), None)
    save_data(db)
    await message.reply_text(
        "🤖 Auto Post Bot\n\n"
        f"📊 Accs: {len(db['accounts'])}\n"
        f"📝 Texts: {len(db['templates'])}\n"
        f"📢 Groups: {len(db['groups'])}\n"
        f"⏱ Timer: {db.get('timer', 200)}s",
        reply_markup=MAIN_KEYBOARD
    )

# --- Main handler ---
@app.on_message(filters.user(OWNER_ID) & filters.text)
async def handle_menu(client: Client, message: Message):
    text = message.text
    user_id_str = str(OWNER_ID)
    state = db["user_state"].get(user_id_str)

    # --- States ---
    if state == "WAITING_PHONE":
        phone = text.strip()
        session_name = f"temp_session_{OWNER_ID}"
        temp_client = Client(session_name, api_id=API_ID, api_hash=API_HASH)
        await temp_client.connect()
        try:
            sent_code = await temp_client.send_code(phone)
            login_sessions[OWNER_ID] = {
                "client": temp_client,
                "phone": phone,
                "hash": sent_code.phone_code_hash,
                "session_name": session_name
            }
            db["user_state"][user_id_str] = "WAITING_OTP"
            save_data(db)
            return await message.reply_text("📩 Enter OTP:")
        except Exception as e:
            await temp_client.disconnect()
            if os.path.exists(f"{session_name}.session"):
                os.remove(f"{session_name}.session")
            return await message.reply_text(f"❌ Error: `{e}`")

    elif state == "WAITING_OTP":
        otp = text.strip()
        session_info = login_sessions.get(OWNER_ID)
        if not session_info:
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text("❌ Session expired.")

        temp_client = session_info["client"]
        session_name = session_info["session_name"]
        try:
            await temp_client.sign_in(session_info["phone"], session_info["hash"], otp)
            session_string = await temp_client.export_session_string()
            db["accounts"].append(session_string)
            await temp_client.disconnect()
            
            if os.path.exists(f"{session_name}.session"):
                os.remove(f"{session_name}.session")
                
            del login_sessions[OWNER_ID]
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text("✅ Account added!")
        except SessionPasswordNeeded:
            db["user_state"][user_id_str] = "WAITING_PASSWORD"
            save_data(db)
            return await message.reply_text("🔐 2FA password:")
        except (PhoneCodeInvalid, PhoneCodeExpired):
            return await message.reply_text("❌ Invalid OTP. Try again:")
        except Exception as e:
            await temp_client.disconnect()
            if os.path.exists(f"{session_name}.session"):
                os.remove(f"{session_name}.session")
            del login_sessions[OWNER_ID]
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text(f"❌ Error: `{e}`")

    elif state == "WAITING_PASSWORD":
        password = text.strip()
        session_info = login_sessions.get(OWNER_ID)
        if not session_info:
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text("❌ Session expired.")

        temp_client = session_info["client"]
        session_name = session_info["session_name"]
        try:
            await temp_client.check_password(password)
            session_string = await temp_client.export_session_string()
            db["accounts"].append(session_string)
            await temp_client.disconnect()
            
            if os.path.exists(f"{session_name}.session"):
                os.remove(f"{session_name}.session")
                
            del login_sessions[OWNER_ID]
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text("✅ Account added!")
        except Exception as e:
            return await message.reply_text(f"❌ Wrong password: `{e}`")

    elif state == "WAITING_TEMPLATE":
        db["templates"].append(text)
        db["user_state"].pop(user_id_str, None)
        save_data(db)
        return await message.reply_text("✅ Text added!")

    elif state == "WAITING_GROUP":
        group = clean_group_link(text.strip())
        db["groups"].append(group)
        db["user_state"].pop(user_id_str, None)
        save_data(db)
        return await message.reply_text(f"✅ Group: {group}")

    elif state == "WAITING_TIMER":
        if text.isdigit():
            db["timer"] = int(text)
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text(f"✅ Timer: {text}s")
        else:
            return await message.reply_text("❌ Enter a number")

    # --- Menu ---
    if text == "📋 Accounts":
        if not db["accounts"]:
            return await message.reply_text("❌ No accounts.")
        msg = "📋 Accounts:\n\n"
        for i in range(len(db["accounts"])):
            msg += f"{i+1}. Acc {i+1}\n"
        await message.reply_text(msg)

    elif text == "📋 Groups":
        if not db["groups"]:
            return await message.reply_text("❌ No groups.")
        msg = "📋 Groups:\n\n"
        for i, g in enumerate(db["groups"], 1):
            msg += f"{i}. {g}\n"
        await message.reply_text(msg)

    elif text == "➕ Add Acc":
        db["user_state"][user_id_str] = "WAITING_PHONE"
        save_data(db)
        await message.reply_text("📱 Send phone:\nExample: +9647800000000")

    elif text == "➖ Del Acc":
        if not db["accounts"]:
            return await message.reply_text("❌ No accounts.")
        db["accounts"].pop()
        save_data(db)
        await message.reply_text("🗑 Last acc deleted.")

    elif text == "📝 Add Text":
        db["user_state"][user_id_str] = "WAITING_TEMPLATE"
        save_data(db)
        await message.reply_text("📝 Send text:")

    elif text == "🗑 Del Text":
        if not db["templates"]:
            return await message.reply_text("❌ No texts.")
        db["templates"].pop()
        save_data(db)
        await message.reply_text("🗑 Last text deleted.")

    elif text == "📢 Add Group":
        db["user_state"][user_id_str] = "WAITING_GROUP"
        save_data(db)
        await message.reply_text("📢 Send group:\nExample: @mygroup")

    elif text == "❌ Del Group":
        if not db["groups"]:
            return await message.reply_text("❌ No groups.")
        db["groups"].pop()
        save_data(db)
        await message.reply_text("🗑 Last group deleted.")

    elif text == "▶️ Start":
        if db["is_running"]:
            return await message.reply_text("⚠️ Already running!")
        if not db["accounts"] or not db["templates"] or not db["groups"]:
            return await message.reply_text("❌ Need: accs, texts, groups.")
        
        db["is_running"] = True
        save_data(db)
        asyncio.create_task(auto_posting_loop())
        total = db.get('timer', 200) * len(db["accounts"])
        await message.reply_text(
            f"🚀 Started!\n"
            f"📊 Accs: {len(db['accounts'])}\n"
            f"⏱ Timer: {db.get('timer', 200)}s\n"
            f"⏰ Each acc: {total}s"
        )

    elif text == "⏹ Stop":
        if not db["is_running"]:
            return await message.reply_text("⚠️ Already stopped!")
        db["is_running"] = False
        save_data(db)
        await message.reply_text("🛑 Stopped.")

    elif text == "⏱ Timer":
        db["user_state"][user_id_str] = "WAITING_TIMER"
        save_data(db)
        await message.reply_text(f"⏱ Current: {db.get('timer', 200)}s\nSend new:")

    elif text == "📊 Stats":
        status = "🟢 Running" if db["is_running"] else "🔴 Stopped"
        total = db.get('timer', 200) * len(db["accounts"]) if db["accounts"] else 0
        await message.reply_text(
            f"📊 Stats:\n\n"
            f"Status: {status}\n"
            f"Accs: {len(db['accounts'])}\n"
            f"Texts: {len(db['templates'])}\n"
            f"Groups: {len(db['groups'])}\n"
            f"Timer: {db.get('timer', 200)}s\n"
            f"Each acc: {total}s\n"
            f"✅ Sent: {db['stats']['sent_count']}\n"
            f"❌ Failed: {db['stats']['failed_count']}"
        )

    elif text == "🗑 Clear All":
        db["accounts"] = []
        db["templates"] = []
        db["groups"] = []
        db["stats"] = {"sent_count": 0, "failed_count": 0}
        db["is_running"] = False
        db["joined_channels"] = {}
        save_data(db)
        await message.reply_text("🗑 All cleared!")

    else:
        await message.reply_text(
            "لم أفهم الأمر. أرسل /start ثم اختر أحد أزرار القائمة."
        )

if __name__ == "__main__":
    print("🤖 Bot running...")
    print(f"👤 Owner: {OWNER_ID}")
    print(f"📊 Data: {DATA_FILE}")
    print(f"⏱ Timer: {db.get('timer', 200)}s")
    app.run()
