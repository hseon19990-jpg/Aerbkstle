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
BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH")

# --- Data file (Volume path) ---
DATA_FILE = "/app/data/bot_data.json"
login_sessions = {}

# --- Load/Save Data ---
def load_data():
    if not os.path.exists(DATA_FILE):
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
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
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(default_data, f, ensure_ascii=False, indent=4)
        return default_data
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "accounts": [], "templates": [], "groups": [], "timer": 200,
            "is_running": False, "stats": {"sent_count": 0, "failed_count": 0},
            "user_state": {}, "last_message": {}, "joined_channels": {}
        }

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

db = load_data()

# --- Bot Client ---
app = Client("auto_post_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

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

# --- Helpers ---
def extract_links(text):
    patterns = [
        r'https://t\.me/[a-zA-Z0-9_]+',
        r't\.me/[a-zA-Z0-9_]+',
        r'@[a-zA-Z0-9_]+'
    ]
    links = []
    for pattern in patterns:
        links.extend(re.findall(pattern, text))
    return links

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

# --- Auto Leave Channels ---
async def auto_leave_channels():
    global db
    while True:
        try:
            now = datetime.now()
            to_remove = []
            
            for channel, join_time in list(db.get("joined_channels", {}).items()):
                try:
                    join_dt = datetime.fromisoformat(join_time)
                    if now - join_dt > timedelta(hours=12):
                        to_remove.append(channel)
                except Exception:
                    to_remove.append(channel)
            
            for channel in to_remove:
                for idx, session_str in enumerate(db.get("accounts", [])):
                    try:
                        user_app = Client(f"leave_{idx}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
                        await user_app.start()
                        await user_app.leave_chat(channel)
                        await user_app.stop()
                    except Exception as e:
                        print(f"Leave error: {e}")
                
                db["joined_channels"].pop(channel, None)
                save_data(db)
            
            await asyncio.sleep(3600)
        except Exception as e:
            print(f"Auto leave error: {e}")
            await asyncio.sleep(60)

# --- Auto Post Loop ---
async def auto_posting_loop():
    global db
    account_index = 0
    asyncio.create_task(auto_leave_channels())
    
    while db.get("is_running", False):
        if not db["accounts"] or not db["templates"] or not db["groups"]:
            db["is_running"] = False
            save_data(db)
            break
        
        session_str = db["accounts"][account_index]
        try:
            user_app = Client(f"post_{account_index}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
            await user_app.start()
            
            for group in db["groups"]:
                if not db.get("is_running", False):
                    break
                
                if group in db.get("last_message", {}):
                    if db["last_message"][group].get("from_our_account", False):
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
                except Exception as e:
                    db["stats"]["failed_count"] += 1
                    save_data(db)
                
                await asyncio.sleep(2)
            
            await user_app.stop()
        except Exception as e:
            print(f"Post error: {e}")
        
        account_index = (account_index + 1) % len(db["accounts"])
        await asyncio.sleep(db.get("timer", 200))

# --- Handlers ---

# 1. أوامر المالك (Start)
@app.on_message(filters.command("start") & filters.user(OWNER_ID))
async def start_cmd(client: Client, message: Message):
    db["user_state"].pop(str(OWNER_ID), None)
    save_data(db)
    await message.reply_text(
        "🤖 **Auto Post Bot**\n\n"
        f"📊 الحسابات: {len(db['accounts'])}\n"
        f"📝 النصوص: {len(db['templates'])}\n"
        f"📢 المجموعات: {len(db['groups'])}\n"
        f"⏱ المؤقت: {db.get('timer', 200)} ثانية",
        reply_markup=MAIN_KEYBOARD
    )

# 2. تفاعل القائمة للمالك
@app.on_message(filters.user(OWNER_ID) & filters.text)
async def handle_menu(client: Client, message: Message):
    text = message.text
    user_id_str = str(OWNER_ID)
    state = db["user_state"].get(user_id_str)

    if state == "WAITING_PHONE":
        phone = text.strip()
        session_name = f"temp_{OWNER_ID}"
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
            return await message.reply_text("📩 أرسل كود التحقق (OTP):")
        except Exception as e:
            await temp_client.disconnect()
            if os.path.exists(f"{session_name}.session"):
                os.remove(f"{session_name}.session")
            return await message.reply_text(f"❌ خطأ: `{e}`")

    elif state == "WAITING_OTP":
        otp = text.strip()
        session_info = login_sessions.get(OWNER_ID)
        if not session_info:
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text("❌ انتهت الجلسة، ابدأ من جديد.")

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
            return await message.reply_text("✅ تم إضافة الحساب بنجاح!")
        except SessionPasswordNeeded:
            db["user_state"][user_id_str] = "WAITING_PASSWORD"
            save_data(db)
            return await message.reply_text("🔐 الحساب محمي بكلمة سر (2FA)، أرسلها الآن:")
        except (PhoneCodeInvalid, PhoneCodeExpired):
            return await message.reply_text("❌ الكود غير صحيح أو منتهي الصلاحية، أعد المحاولة:")
        except Exception as e:
            await temp_client.disconnect()
            if os.path.exists(f"{session_name}.session"):
                os.remove(f"{session_name}.session")
            del login_sessions[OWNER_ID]
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text(f"❌ خطأ: `{e}`")

    elif state == "WAITING_PASSWORD":
        password = text.strip()
        session_info = login_sessions.get(OWNER_ID)
        if not session_info:
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text("❌ انتهت الجلسة.")

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
            return await message.reply_text("✅ تم تفعيل الحساب وحفظه بنجاح!")
        except Exception as e:
            return await message.reply_text(f"❌ كلمة المرور غير صحيحة: `{e}`")

    elif state == "WAITING_TEMPLATE":
        db["templates"].append(text)
        db["user_state"].pop(user_id_str, None)
        save_data(db)
        return await message.reply_text("✅ تم حفظ النص الإعلاني!")

    elif state == "WAITING_GROUP":
        group = clean_group_link(text.strip())
        db["groups"].append(group)
        db["user_state"].pop(user_id_str, None)
        save_data(db)
        return await message.reply_text(f"✅ تم إضافة المجموعة: {group}")

    elif state == "WAITING_TIMER":
        if text.isdigit():
            db["timer"] = int(text)
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text(f"✅ تم تعديل المؤقت إلى: {text} ثانية")
        return await message.reply_text("❌ الرجاء إرسال أرقام فقط.")

    # القوائم
    if text == "📋 Accounts":
        if not db["accounts"]:
            return await message.reply_text("❌ لا يوجد حسابات مضافة.")
        msg = "📋 قائمة الحسابات:\n\n"
        for i in range(len(db["accounts"])):
            msg += f"{i+1}. Account {i+1}\n"
        await message.reply_text(msg)

    elif text == "📋 Groups":
        if not db["groups"]:
            return await message.reply_text("❌ لا توجد مجموعات.")
        msg = "📋 قائمة المجموعات:\n\n"
        for i, g in enumerate(db["groups"], 1):
            msg += f"{i}. {g}\n"
        await message.reply_text(msg)

    elif text == "➕ Add Acc":
        db["user_state"][user_id_str] = "WAITING_PHONE"
        save_data(db)
        await message.reply_text("📱 أرسل رقم الهاتف مع رمز الدولة (مثال: +9647800000000):")

    elif text == "➖ Del Acc":
        if not db["accounts"]:
            return await message.reply_text("❌ لا توجد حسابات لحذفها.")
        db["accounts"].pop()
        save_data(db)
        await message.reply_text("🗑 تم حذف آخر حساب.")

    elif text == "📝 Add Text":
        db["user_state"][user_id_str] = "WAITING_TEMPLATE"
        save_data(db)
        await message.reply_text("📝 أرسل النص الإعلاني:")

    elif text == "🗑 Del Text":
        if not db["templates"]:
            return await message.reply_text("❌ لا توجد نصوص.")
        db["templates"].pop()
        save_data(db)
        await message.reply_text("🗑 تم حذف آخر نص.")

    elif text == "📢 Add Group":
        db["user_state"][user_id_str] = "WAITING_GROUP"
        save_data(db)
        await message.reply_text("📢 أرسل يوزر أو رابط المجموعة:")

    elif text == "❌ Del Group":
        if not db["groups"]:
            return await message.reply_text("❌ لا توجد مجموعات.")
        db["groups"].pop()
        save_data(db)
        await message.reply_text("🗑 تم حذف آخر مجموعة.")

    elif text == "▶️ Start":
        if db.get("is_running"):
            return await message.reply_text("⚠️ النشر التلقائي يعمل بالفعل!")
        if not db["accounts"] or not db["templates"] or not db["groups"]:
            return await message.reply_text("❌ يجب إضافة حسابات، نصوص، ومجموعات أولاً.")
        
        db["is_running"] = True
        save_data(db)
        asyncio.create_task(auto_posting_loop())
        await message.reply_text("🚀 تم بدء النشر التلقائي بنجاح!")

    elif text == "⏹ Stop":
        if not db.get("is_running"):
            return await message.reply_text("⚠️ النشر متوقف بالفعل!")
        db["is_running"] = False
        save_data(db)
        await message.reply_text("🛑 تم إيقاف النشر التلقائي.")

    elif text == "⏱ Timer":
        db["user_state"][user_id_str] = "WAITING_TIMER"
        save_data(db)
        await message.reply_text(f"⏱ المؤقت الحالي: {db.get('timer', 200)} ثانية\nأرسل القيمة الجديدة بالثواني:")

    elif text == "📊 Stats":
        status = "🟢 يعمل" if db.get("is_running") else "🔴 متوقف"
        await message.reply_text(
            f"📊 **الإحصائيات الحالية**:\n\n"
            f"الحالة: {status}\n"
            f"الحسابات: {len(db['accounts'])}\n"
            f"النصوص: {len(db['templates'])}\n"
            f"المجموعات: {len(db['groups'])}\n"
            f"المؤقت: {db.get('timer', 200)}s\n"
            f"✅ تم الإرسال: {db['stats']['sent_count']}\n"
            f"❌ فشل: {db['stats']['failed_count']}"
        )

    elif text == "🗑 Clear All":
        db["accounts"] = []
        db["templates"] = []
        db["groups"] = []
        db["stats"] = {"sent_count": 0, "failed_count": 0}
        db["is_running"] = False
        db["joined_channels"] = {}
        save_data(db)
        await message.reply_text("🗑 تم تصفير جميع البيانات بنجاح.")

# 3. معالجة الروابط من المستخدمين الآخرين (باستثناء المالك عبر الفلتر مباشرة)
@app.on_message(filters.text & filters.private & ~filters.user(OWNER_ID))
async def handle_replies(client: Client, message: Message):
    links = extract_links(message.text)
    if not links:
        return
    
    for idx, session_str in enumerate(db.get("accounts", [])):
        try:
            user_app = Client(f"reply_{idx}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
            await user_app.start()
            for link in links:
                try:
                    clean_link = clean_group_link(link)
                    await user_app.join_chat(clean_link)
                    db["joined_channels"][clean_link] = datetime.now().isoformat()
                    save_data(db)
                except Exception as e:
                    print(f"Join error: {e}")
            await user_app.stop()
            break
        except Exception as e:
            print(f"Account error: {e}")

# 4. تتبع رسائل المجموعات
@app.on_message(filters.group & filters.incoming)
async def track_group_messages(client: Client, message: Message):
    chat_id = str(message.chat.id)
    chat_username = f"@{message.chat.username}" if message.chat.username else None
    
    if chat_id in db.get("groups", []) or (chat_username and chat_username in db.get("groups", [])):
        if chat_id not in db["last_message"]:
            db["last_message"][chat_id] = {}
        
        db["last_message"][chat_id]["from_our_account"] = False
        db["last_message"][chat_id]["message_id"] = message.id
        save_data(db)

if __name__ == "__main__":
    print(f"🤖 Bot is starting for Owner: {OWNER_ID}")
    app.run()
