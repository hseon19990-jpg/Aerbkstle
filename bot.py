import os
import asyncio
import json
import random
import re
from datetime import datetime, timedelta
from pyrogram import Client, filters
from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton, Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired, FloodWait, AuthKeyUnregistered

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
# Cache for account info to avoid repeated connections
account_cache = {}

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
@app.on_message(filters.private & filters.incoming, group=2)
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
        [KeyboardButton("➕ إضافة حساب"), KeyboardButton("🔄 استرداد حساب")],
        [KeyboardButton("📋 قائمة الحسابات"), KeyboardButton("📋 قائمة الكروبات")],
        [KeyboardButton("📝 إضافة كليشة"), KeyboardButton("🗑 حذف كليشة")],
        [KeyboardButton("📢 إضافة كروب"), KeyboardButton("❌ حذف كروب")],
        [KeyboardButton("▶️ تشغيل البوت"), KeyboardButton("⏹ إيقاف البوت")],
        [KeyboardButton("⏱ المؤقت"), KeyboardButton("📊 الإحصائيات")],
        [KeyboardButton("🗑 حذف الكل")]
    ],
    resize_keyboard=True
)

MENU_ACTIONS = {
    "accounts": {"📋 قائمة الحسابات", "قائمة الحسابات", "📋 Accounts"},
    "groups": {"📋 قائمة الكروبات", "قائمة الكروبات", "📋 Groups"},
    "add_account": {"➕ إضافة حساب", "إضافة حساب", "➕ Add Acc"},
    "recover_account": {"🔄 استرداد حساب", "استرداد حساب", "🔄 Recover"},
    "add_text": {"📝 إضافة كليشة", "إضافة كليشة", "إضافة كليشه", "📝 Add Text"},
    "delete_text": {"🗑 حذف كليشة", "حذف كليشة", "حذف كليشه", "🗑 Del Text"},
    "add_group": {"📢 إضافة كروب", "إضافة كروب", "إضافة قروب", "📢 Add Group"},
    "delete_group": {"❌ حذف كروب", "حذف كروب", "حذف قروب", "❌ Del Group"},
    "start": {"▶️ تشغيل البوت", "تشغيل البوت", "▶️ Start"},
    "stop": {"⏹ إيقاف البوت", "إيقاف البوت", "⏹ Stop"},
    "timer": {"⏱ المؤقت", "المؤقت", "⏱ Timer"},
    "stats": {"📊 الإحصائيات", "الإحصائيات", "📊 Stats"},
    "clear": {"🗑 حذف الكل", "حذف الكل", "🗑 Clear All"},
}

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

# --- Get account info with caching ---
async def get_account_info(session_str, index):
    cache_key = f"{index}_{hash(session_str)}"
    if cache_key in account_cache:
        return account_cache[cache_key]
    
    try:
        temp_client = Client(f"info_session_{index}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
        await temp_client.connect()
        me = await temp_client.get_me()
        info = {
            "phone": me.phone_number or "غير معروف",
            "name": me.first_name or "غير معروف",
            "connected": True
        }
        await temp_client.disconnect()
        # Cache for 5 minutes
        account_cache[cache_key] = info
        return info
    except:
        info = {
            "phone": "غير معروف",
            "name": "غير متصل",
            "connected": False
        }
        account_cache[cache_key] = info
        return info

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

# --- Optimized Auto Post Loop ---
async def auto_posting_loop():
    global db, account_cache
    account_index = 0
    
    asyncio.create_task(auto_leave_channels())
    
    # Pre-warm accounts
    active_clients = []
    for idx, session_str in enumerate(db["accounts"]):
        try:
            client = Client(f"active_session_{idx}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
            await client.start()
            active_clients.append(client)
            print(f"✅ Account {idx+1} connected and ready")
        except Exception as e:
            print(f"❌ Failed to connect account {idx+1}: {e}")
            active_clients.append(None)
    
    while db["is_running"]:
        if not db["accounts"] or not db["templates"] or not db["groups"]:
            db["is_running"] = False
            save_data(db)
            break
        
        # Get current client
        current_client = active_clients[account_index] if account_index < len(active_clients) else None
        
        if current_client:
            account_number = account_index + 1
            
            for group in db["groups"]:
                if not db["is_running"]:
                    break
                
                if group in db.get("last_message", {}):
                    last_msg = db["last_message"][group]
                    if last_msg.get("from_our_account", False):
                        print(f"⏭️ Skip {group}")
                        continue
                
                # اختيار كليشة عشوائية من القائمة
                template = random.choice(db["templates"])
                
                try:
                    sent_msg = await current_client.send_message(group, template)
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
        
        # حساب المؤقت: كل حساب يرسل بعد انتهاء دورة جميع الحسابات
        timer_value = db.get("timer", 200)
        total_cycle_time = timer_value * len(db["accounts"])
        print(f"⏱ Waiting {total_cycle_time}s for next account cycle...")
        await asyncio.sleep(total_cycle_time)
        
        account_index = (account_index + 1) % len(db["accounts"])
    
    # Cleanup
    for client in active_clients:
        if client:
            try:
                await client.stop()
            except:
                pass

# --- Auto Join on Reply (Any user) ---
@app.on_message(filters.text & filters.private, group=1)
async def handle_replies(client: Client, message: Message):
    if message.from_user.id == OWNER_ID:
        return
    
    links = extract_links(message.text)
    if not links:
        return
    
    # Use first available account
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
            "رقم حسابك لا يطابق رقم المالك الموجود في إعدادات التشغيل."
        )

    db["user_state"].pop(str(OWNER_ID), None)
    save_data(db)
    await message.reply_text(
        "🤖 بوت النشر التلقائي\n\n"
        f"📊 الحسابات: {len(db['accounts'])}\n"
        f"📝 الكليشات: {len(db['templates'])}\n"
        f"📢 الكروبات: {len(db['groups'])}\n"
        f"⏱ المؤقت: {db.get('timer', 200)} ثانية",
        reply_markup=MAIN_KEYBOARD
    )

# --- Helper function for selection lists ---
def create_selection_list(items, item_type, action, display_func=None):
    """Creates inline keyboard for selecting items to delete"""
    keyboard = []
    for i, item in enumerate(items):
        if display_func:
            display_text = display_func(item, i)
        else:
            display_text = f"{i+1}. {item[:30]}..." if len(item) > 30 else f"{i+1}. {item}"
        keyboard.append([InlineKeyboardButton(display_text, callback_data=f"{action}_{i}")])
    keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
    return InlineKeyboardMarkup(keyboard)

# --- Handle callback queries for selection ---
@app.on_callback_query()
async def handle_callback(client: Client, callback_query):
    if callback_query.from_user.id != OWNER_ID:
        return await callback_query.answer("غير مصرح")
    
    data = callback_query.data
    
    if data == "cancel":
        await callback_query.message.delete()
        return await callback_query.answer("تم الإلغاء")
    
    if data.startswith("delete_template_"):
        index = int(data.split("_")[2])
        if 0 <= index < len(db["templates"]):
            deleted = db["templates"].pop(index)
            save_data(db)
            await callback_query.message.delete()
            await callback_query.message.reply_text(f"🗑 تم حذف الكليشة: {deleted[:50]}...")
        else:
            await callback_query.answer("العنصر غير موجود")
    
    elif data.startswith("delete_group_"):
        index = int(data.split("_")[2])
        if 0 <= index < len(db["groups"]):
            deleted = db["groups"].pop(index)
            save_data(db)
            await callback_query.message.delete()
            await callback_query.message.reply_text(f"🗑 تم حذف الكروب: {deleted}")
        else:
            await callback_query.answer("العنصر غير موجود")
    
    elif data.startswith("delete_account_"):
        index = int(data.split("_")[2])
        if 0 <= index < len(db["accounts"]):
            # حذف الحساب نهائياً وتسجيل خروج
            session_str = db["accounts"].pop(index)
            try:
                # محاولة تسجيل الخروج
                temp_client = Client(f"logout_session_{index}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
                await temp_client.start()
                await temp_client.log_out()
                await temp_client.stop()
                await callback_query.message.reply_text(f"🗑 تم حذف الحساب رقم {index+1} وتسجيل الخروج بنجاح!")
            except Exception as e:
                # في حالة فشل تسجيل الخروج، نحذف الجلسة فقط
                await callback_query.message.reply_text(f"🗑 تم حذف الحساب رقم {index+1} (تعذر تسجيل الخروج: {e})")
            save_data(db)
            await callback_query.message.delete()
            
            # Clear cache for this account
            global account_cache
            account_cache = {}
        else:
            await callback_query.answer("العنصر غير موجود")
    
    await callback_query.answer()

# --- Main handler ---
@app.on_message(filters.user(OWNER_ID) & filters.text)
async def handle_menu(client: Client, message: Message):
    text = message.text.strip()
    if text.lower().startswith("/start"):
        return

    user_id_str = str(OWNER_ID)
    state = db["user_state"].get(user_id_str)

    # --- States ---
    if state == "WAITING_PHONE":
        phone = text.strip()
        
        # التحقق مما إذا كان الرقم موجوداً بالفعل
        for session_str in db["accounts"]:
            try:
                temp_client = Client(f"check_session_{OWNER_ID}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
                await temp_client.connect()
                me = await temp_client.get_me()
                if me.phone_number == phone:
                    await temp_client.disconnect()
                    return await message.reply_text("⚠️ هذا الرقم موجود بالفعل في الحسابات المضافة!")
                await temp_client.disconnect()
            except:
                continue
        
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
            return await message.reply_text("📩 أرسل رمز التحقق:")
        except Exception as e:
            await temp_client.disconnect()
            if os.path.exists(f"{session_name}.session"):
                os.remove(f"{session_name}.session")
            return await message.reply_text(f"❌ حدث خطأ: `{e}`")

    elif state == "WAITING_OTP":
        otp = text.strip()
        session_info = login_sessions.get(OWNER_ID)
        if not session_info:
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text("❌ انتهت جلسة تسجيل الدخول. ابدأ إضافة الحساب من جديد.")

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
            return await message.reply_text("✅ تمت إضافة الحساب بنجاح!")
        except SessionPasswordNeeded:
            db["user_state"][user_id_str] = "WAITING_PASSWORD"
            save_data(db)
            return await message.reply_text("🔐 أرسل كلمة مرور التحقق بخطوتين:")
        except (PhoneCodeInvalid, PhoneCodeExpired):
            return await message.reply_text("❌ رمز التحقق غير صحيح أو منتهي. حاول مرة أخرى:")
        except Exception as e:
            await temp_client.disconnect()
            if os.path.exists(f"{session_name}.session"):
                os.remove(f"{session_name}.session")
            del login_sessions[OWNER_ID]
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text(f"❌ حدث خطأ: `{e}`")

    elif state == "WAITING_PASSWORD":
        password = text.strip()
        session_info = login_sessions.get(OWNER_ID)
        if not session_info:
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text("❌ انتهت جلسة تسجيل الدخول. ابدأ إضافة الحساب من جديد.")

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
            return await message.reply_text("✅ تمت إضافة الحساب بنجاح!")
        except Exception as e:
            return await message.reply_text(f"❌ كلمة المرور غير صحيحة: `{e}`")

    elif state == "WAITING_RECOVER":
        session_str = text.strip()
        try:
            # محاولة الاتصال بالجلسة
            temp_client = Client(f"recover_session_{OWNER_ID}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
            await temp_client.connect()
            me = await temp_client.get_me()
            await temp_client.disconnect()
            
            # التحقق من عدم وجود الجلسة مسبقاً
            for existing_session in db["accounts"]:
                try:
                    check_client = Client(f"check_session_{OWNER_ID}", api_id=API_ID, api_hash=API_HASH, session_string=existing_session)
                    await check_client.connect()
                    check_me = await check_client.get_me()
                    if check_me.phone_number == me.phone_number:
                        await check_client.disconnect()
                        return await message.reply_text("⚠️ هذا الحساب موجود بالفعل في القائمة!")
                    await check_client.disconnect()
                except:
                    continue
            
            db["accounts"].append(session_str)
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            
            # Clear cache
            global account_cache
            account_cache = {}
            
            return await message.reply_text(f"✅ تم استرداد الحساب بنجاح!\nالرقم: {me.phone_number}\nالاسم: {me.first_name}")
        except Exception as e:
            return await message.reply_text(f"❌ فشل استرداد الحساب: `{e}`")

    elif state == "WAITING_TEMPLATE":
        # السماح بإضافة كليشات متعددة في سطر واحد، كل كليشة في سطر منفصل
        lines = text.strip().split('\n')
        added_count = 0
        for line in lines:
            if line.strip():
                db["templates"].append(line.strip())
                added_count += 1
        db["user_state"].pop(user_id_str, None)
        save_data(db)
        return await message.reply_text(f"✅ تمت إضافة {added_count} كليشة!")

    elif state == "WAITING_GROUP":
        group = clean_group_link(text.strip())
        db["groups"].append(group)
        db["user_state"].pop(user_id_str, None)
        save_data(db)
        return await message.reply_text(f"✅ تمت إضافة الكروب: {group}")

    elif state == "WAITING_TIMER":
        if text.isdigit():
            db["timer"] = int(text)
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text(f"✅ تم ضبط المؤقت على {text} ثانية")
        else:
            return await message.reply_text("❌ أرسل رقمًا صحيحًا للثواني")

    # --- Menu ---
    if text in MENU_ACTIONS["accounts"]:
        if not db["accounts"]:
            return await message.reply_text("❌ لا توجد حسابات مضافة.")
        msg = "📋 الحسابات المضافة:\n\n"
        for i, session_str in enumerate(db["accounts"]):
            info = await get_account_info(session_str, i)
            phone = info["phone"]
            name = info["name"]
            status = "✅" if info["connected"] else "❌"
            msg += f"{i+1}. {status} 📱 {phone} - 👤 {name}\n"
        await message.reply_text(msg)

    elif text in MENU_ACTIONS["groups"]:
        if not db["groups"]:
            return await message.reply_text("❌ لا توجد كروبات مضافة.")
        msg = "📋 الكروبات المضافة:\n\n"
        for i, g in enumerate(db["groups"], 1):
            msg += f"{i}. {g}\n"
        await message.reply_text(msg)

    elif text in MENU_ACTIONS["add_account"]:
        db["user_state"][user_id_str] = "WAITING_PHONE"
        save_data(db)
        await message.reply_text("📱 أرسل رقم الهاتف مع مفتاح الدولة:\nمثال: +9647800000000")

    elif text in MENU_ACTIONS["recover_account"]:
        db["user_state"][user_id_str] = "WAITING_RECOVER"
        save_data(db)
        await message.reply_text(
            "🔄 أرسل جلسة الاسترداد (Session String):\n\n"
            "ملاحظة: يمكنك الحصول على الجلسة من تطبيقات استخراج الجلسات"
        )

    elif text in MENU_ACTIONS["delete_account"]:
        if not db["accounts"]:
            return await message.reply_text("❌ لا توجد حسابات لحذفها.")
        
        # عرض قائمة للحذف مع عرض رقم الهاتف
        async def display_account(item, index):
            info = await get_account_info(item, index)
            phone = info["phone"]
            status = "✅" if info["connected"] else "❌"
            return f"{index+1}. {status} 📱 {phone}"
        
        keyboard = create_selection_list(db["accounts"], "account", "delete", display_account)
        await message.reply_text(
            "🗑 اختر الحساب لحذفه نهائياً (سيتم تسجيل الخروج):",
            reply_markup=keyboard
        )

    elif text in MENU_ACTIONS["add_text"]:
        db["user_state"][user_id_str] = "WAITING_TEMPLATE"
        save_data(db)
        await message.reply_text(
            "📝 أرسل الكليشة الجديدة (يمكنك إرسال عدة كليشات، كل كليشة في سطر منفصل):"
        )

    elif text in MENU_ACTIONS["delete_text"]:
        if not db["templates"]:
            return await message.reply_text("❌ لا توجد كليشات لحذفها.")
        
        # عرض قائمة للحذف
        keyboard = create_selection_list(db["templates"], "template", "delete")
        await message.reply_text(
            "🗑 اختر الكليشة لحذفها:",
            reply_markup=keyboard
        )

    elif text in MENU_ACTIONS["add_group"]:
        db["user_state"][user_id_str] = "WAITING_GROUP"
        save_data(db)
        await message.reply_text("📢 أرسل معرف الكروب أو رابطه:\nمثال: @mygroup")

    elif text in MENU_ACTIONS["delete_group"]:
        if not db["groups"]:
            return await message.reply_text("❌ لا توجد كروبات لحذفها.")
        
        # عرض قائمة للحذف
        keyboard = create_selection_list(db["groups"], "group", "delete")
        await message.reply_text(
            "🗑 اختر الكروب لحذفه:",
            reply_markup=keyboard
        )

    elif text in MENU_ACTIONS["start"]:
        if db["is_running"]:
            return await message.reply_text("⚠️ البوت يعمل حاليًا بالفعل.")
        if not db["accounts"] or not db["templates"] or not db["groups"]:
            return await message.reply_text("❌ يجب إضافة حساب وكليشة وكروب أولًا.")
        
        db["is_running"] = True
        save_data(db)
        asyncio.create_task(auto_posting_loop())
        timer_value = db.get('timer', 200)
        total_cycle = timer_value * len(db["accounts"])
        await message.reply_text(
            f"🚀 تم تشغيل البوت!\n"
            f"📊 الحسابات: {len(db['accounts'])}\n"
            f"⏱ المؤقت بين الحسابات: {timer_value} ثانية\n"
            f"⏰ وقت دورة الحسابات الكاملة: {total_cycle} ثانية\n"
            f"📝 عدد الكليشات: {len(db['templates'])}\n"
            f"📢 عدد الكروبات: {len(db['groups'])}"
        )

    elif text in MENU_ACTIONS["stop"]:
        if not db["is_running"]:
            return await message.reply_text("⚠️ البوت متوقف حاليًا.")
        db["is_running"] = False
        save_data(db)
        await message.reply_text("🛑 تم إيقاف البوت.")

    elif text in MENU_ACTIONS["timer"]:
        db["user_state"][user_id_str] = "WAITING_TIMER"
        save_data(db)
        await message.reply_text(
            f"⏱ المؤقت الحالي: {db.get('timer', 200)} ثانية\n"
            f"أرسل القيمة الجديدة (بالثواني):\n"
            f"ملاحظة: كل حساب سيرسل رسالة بعد مرور هذا الوقت على إرسال الحساب السابق"
        )

    elif text in MENU_ACTIONS["stats"]:
        status = "🟢 يعمل" if db["is_running"] else "🔴 متوقف"
        timer_value = db.get('timer', 200)
        total_cycle = timer_value * len(db["accounts"]) if db["accounts"] else 0
        await message.reply_text(
            f"📊 الإحصائيات:\n\n"
            f"الحالة: {status}\n"
            f"الحسابات: {len(db['accounts'])}\n"
            f"الكليشات: {len(db['templates'])}\n"
            f"الكروبات: {len(db['groups'])}\n"
            f"المؤقت بين الحسابات: {timer_value} ثانية\n"
            f"وقت دورة الحسابات الكاملة: {total_cycle} ثانية\n"
            f"✅ تم الإرسال: {db['stats']['sent_count']}\n"
            f"❌ فشل الإرسال: {db['stats']['failed_count']}"
        )

    elif text in MENU_ACTIONS["clear"]:
        db["accounts"] = []
        db["templates"] = []
        db["groups"] = []
        db["stats"] = {"sent_count": 0, "failed_count": 0}
        db["is_running"] = False
        db["joined_channels"] = {}
        save_data(db)
        global account_cache
        account_cache = {}
        await message.reply_text("🗑 تم حذف جميع الحسابات والكليشات والكروبات.")

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
