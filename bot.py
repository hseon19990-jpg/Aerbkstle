import os
import asyncio
import json
import random
import re
from datetime import datetime, timedelta
from pyrogram import Client, filters
from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton, Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import (
    SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired, 
    FloodWait, AuthKeyUnregistered, PeerIdInvalid, UserBannedInChannel
)

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
account_cache = {}
posting_task = None
auto_leave_task = None
account_status = {}

# --- Load/Save Data ---
def load_data():
    if not os.path.exists(DATA_FILE):
        default_data = {
            "accounts": [],
            "templates": [],
            "groups": [],
            "group_activity": {},
            "timer": 60,
            "is_running": False,
            "stats": {"sent_count": 0, "failed_count": 0},
            "user_state": {},
            "last_message": {},
            "outgoing_messages": {},
            "joined_channels": {},
            "channel_join_time": {},
            "account_errors": {},
            "last_group_index": {},
            "template_index": 0,
            "auto_join_groups": True
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
db.setdefault("group_activity", {})
db.setdefault("timer", 60)
db.setdefault("is_running", False)
db.setdefault("stats", {"sent_count": 0, "failed_count": 0})
db["stats"].setdefault("sent_count", 0)
db["stats"].setdefault("failed_count", 0)
db.setdefault("user_state", {})
db.setdefault("last_message", {})
db.setdefault("outgoing_messages", {})
db.setdefault("joined_channels", {})
db.setdefault("channel_join_time", {})
db.setdefault("account_errors", {})
db.setdefault("last_group_index", {})
db.setdefault("template_index", 0)
db.setdefault("auto_join_groups", True)

# --- Bot Client ---
app = Client("auto_post_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- Main Keyboard ---
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("➕ إضافة حساب"), KeyboardButton("🔄 استرداد حساب")],
        [KeyboardButton("🗑 حذف حساب"), KeyboardButton("📋 قائمة الحسابات")],
        [KeyboardButton("📋 قائمة الكروبات")],
        [KeyboardButton("📝 إضافة كليشة"), KeyboardButton("🗑 حذف كليشة")],
        [KeyboardButton("📢 إضافة كروب"), KeyboardButton("❌ حذف كروب")],
        [KeyboardButton("▶️ تشغيل البوت"), KeyboardButton("⏹ إيقاف البوت")],
        [KeyboardButton("⏱ المؤقت"), KeyboardButton("📊 الإحصائيات")],
        [KeyboardButton("🗑 حذف الكل")],
        [KeyboardButton("👥 الردود الواردة")],
        [KeyboardButton("🔄 حالة الردود")],
        [KeyboardButton("⚙️ إعدادات الردود")]
    ],
    resize_keyboard=True
)

MENU_ACTIONS = {
    "accounts": {"📋 قائمة الحسابات", "قائمة الحسابات", "📋 Accounts"},
    "groups": {"📋 قائمة الكروبات", "قائمة الكروبات", "📋 Groups"},
    "add_account": {"➕ إضافة حساب", "إضافة حساب", "➕ Add Acc"},
    "recover_account": {"🔄 استرداد حساب", "استرداد حساب", "🔄 Recover"},
    "delete_account": {"🗑 حذف حساب", "حذف حساب", "🗑 Delete Acc"},
    "add_text": {"📝 إضافة كليشة", "إضافة كليشة", "إضافة كليشه", "📝 Add Text"},
    "delete_text": {"🗑 حذف كليشة", "حذف كليشة", "حذف كليشه", "🗑 Del Text"},
    "add_group": {"📢 إضافة كروب", "إضافة كروب", "إضافة قروب", "📢 Add Group"},
    "delete_group": {"❌ حذف كروب", "حذف كروب", "حذف قروب", "❌ Del Group"},
    "start": {"▶️ تشغيل البوت", "تشغيل البوت", "▶️ Start"},
    "stop": {"⏹ إيقاف البوت", "إيقاف البوت", "⏹ Stop"},
    "timer": {"⏱ المؤقت", "المؤقت", "⏱ Timer"},
    "stats": {"📊 الإحصائيات", "الإحصائيات", "📊 Stats"},
    "clear": {"🗑 حذف الكل", "حذف الكل", "🗑 Clear All"},
    "incoming_replies": {"👥 الردود الواردة", "الردود الواردة", "👥 Replies"},
    "reply_status": {"🔄 حالة الردود", "حالة الردود", "🔄 Status"},
    "reply_settings": {"⚙️ إعدادات الردود", "إعدادات الردود", "⚙️ Settings"}
}

def normalize_button_text(value):
    return re.sub(r"\s+", " ", value.replace("\ufe0f", "").replace("\u200d", "").strip()).casefold()

NORMALIZED_MENU_ACTIONS = {
    action: {normalize_button_text(label) for label in labels}
    for action, labels in MENU_ACTIONS.items()
}

def get_menu_action(text):
    normalized_text = normalize_button_text(text)
    for action, labels in NORMALIZED_MENU_ACTIONS.items():
        if normalized_text in labels:
            return action
    return None

# --- 🛠️ دالة استخراج الروابط (محسّنة بقوة) ---
def extract_all_links(message: Message):
    links = []
    seen = set()
    
    # 1. النص أو الكابشن
    text = (message.text or message.caption or "")
    pattern = (
        r'(?:https?://)?t\.me/(?:\+[\w-]+|joinchat/[\w-]+|[A-Za-z0-9_]+)'
        r'|@[A-Za-z0-9_]{4,}'
        r'|(?<!\d)-100\d{6,}'
    )
    for match in re.findall(pattern, text, flags=re.IGNORECASE):
        if match not in seen:
            links.append(match)
            seen.add(match)
    
    # 2. الأزرار (كل الصفوف والأزرار)
    if message.reply_markup:
        for row in message.reply_markup.inline_keyboard:
            for button in row:
                if button.url:
                    clean = button.url.strip()
                    if clean not in seen:
                        links.append(clean)
                        seen.add(clean)
                if button.text:
                    for match in re.findall(pattern, button.text, flags=re.IGNORECASE):
                        if match not in seen:
                            links.append(match)
                            seen.add(match)
    
    # 3. التعامل مع الروابط بدون "http" (مثل t.me/xxx)
    for match in re.findall(r't\.me/[^\s]+', text):
        if match not in seen:
            links.append(match)
            seen.add(match)
            
    return links

# --- Clean group link ---
def clean_group_link(link):
    link = link.strip().rstrip(".,;:!?)]}")
    if re.fullmatch(r"-?\d+", link):
        return link
    if link.startswith(("https://t.me/", "http://t.me/", "t.me/")):
        prefix = "https://t.me/" if link.startswith("https://t.me/") else (
            "http://t.me/" if link.startswith("http://t.me/") else "t.me/"
        )
        suffix = link[len(prefix):]
        if suffix.startswith(("+", "joinchat/")):
            return link
        return f"@{suffix}"
    if not link.startswith("@"):
        link = f"@{link}"
    return link

def get_next_group(account_index):
    groups = db.get("groups", [])
    if not groups:
        return None
    last_index = db.get("last_group_index", {}).get(str(account_index), -1)
    next_index = (last_index + 1) % len(groups)
    db["last_group_index"][str(account_index)] = next_index
    save_data(db)
    return groups[next_index]

def get_next_template():
    templates = db.get("templates", [])
    if not templates:
        return None
    template_index = db.get("template_index", 0)
    template = templates[template_index]
    db["template_index"] = (template_index + 1) % len(templates)
    save_data(db)
    return template

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
            "connected": True,
            "id": me.id,
            "username": me.username or ""
        }
        await temp_client.disconnect()
        account_cache[cache_key] = info
        return info
    except:
        info = {"phone": "غير معروف", "name": "غير متصل", "connected": False, "id": None, "username": ""}
        account_cache[cache_key] = info
        return info

# --- 🔥 Account Status Check ---
async def check_account_status(client, account_number):
    try:
        me = await client.get_me()
        return {"status": "active", "message": f"✅ الحساب {account_number} يعمل بشكل طبيعي"}
    except FloodWait as e:
        wait_time = e.x
        error_msg = f"⏳ الحساب {account_number} ممنوع مؤقتاً لمدة {wait_time} ثانية"
        print(f"⚠️ {error_msg}")
        await notify_owner(error_msg)
        return {"status": "flood", "message": error_msg, "wait": wait_time}
    except Exception as e:
        error_msg = f"❌ الحساب {account_number} عالق أو محظور: {str(e)[:50]}"
        print(f"⚠️ {error_msg}")
        await notify_owner(error_msg)
        return {"status": "error", "message": error_msg}

async def notify_owner(message):
    try:
        await app.send_message(OWNER_ID, f"⚠️ تنبيه البوت:\n{message}")
    except:
        print(f"⚠️ فشل إرسال إشعار للمالك: {message}")

# --- Auto Leave Channels (24 hours) ---
async def auto_leave_channels():
    global db
    while True:
        try:
            now = datetime.now()
            to_remove = []
            for channel, join_time in db.get("joined_channels", {}).items():
                try:
                    join_dt = datetime.fromisoformat(join_time)
                    if now - join_dt > timedelta(hours=24):
                        to_remove.append(channel)
                except:
                    to_remove.append(channel)
            for channel in to_remove:
                success = True
                for idx, session_str in enumerate(db["accounts"]):
                    user_app = None
                    try:
                        user_app = Client(f"leave_session_{idx}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
                        await user_app.start()
                        await user_app.leave_chat(channel)
                        print(f"🚪 Acc {idx+1} left {channel}")
                    except Exception as e:
                        success = False
                        print(f"❌ Leave {channel} failed: {e}")
                    finally:
                        if user_app:
                            try:
                                await user_app.stop()
                            except Exception:
                                pass
                if success:
                    db["joined_channels"].pop(channel, None)
                    db["channel_join_time"].pop(channel, None)
                    save_data(db)
            await asyncio.sleep(3600)
        except Exception as e:
            print(f"❌ Auto leave error: {e}")
            await asyncio.sleep(60)

def ensure_auto_leave_task():
    global auto_leave_task
    if auto_leave_task is None or auto_leave_task.done():
        auto_leave_task = asyncio.create_task(auto_leave_channels())

# --- Join channel for all accounts ---
async def join_channel_for_all_accounts(channel):
    clean_link = clean_group_link(channel)
    if not clean_link:
        return
    if clean_link in db.get("joined_channels", {}):
        print(f"⏭️ Already tracking {clean_link}")
        return

    ensure_auto_leave_task()
    joined_any = False
    print(f"📢 Joining {clean_link} for all accounts...")
    for idx, session_str in enumerate(db["accounts"]):
        user_app = None
        try:
            user_app = Client(f"reply_session_{idx}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
            await user_app.start()
            try:
                await user_app.join_chat(clean_link)
                joined_any = True
                print(f"✅ Acc {idx+1} joined {clean_link}")
            except Exception as e:
                error_text = str(e).upper()
                if "ALREADY_PARTICIPANT" in error_text or "USER_ALREADY_PARTICIPANT" in error_text:
                    joined_any = True
                    print(f"✅ Acc {idx+1} is already in {clean_link}")
                else:
                    print(f"❌ Acc {idx+1} failed to join {clean_link}: {e}")
        except Exception as e:
            print(f"❌ Error opening acc {idx+1} for {clean_link}: {e}")
        finally:
            if user_app:
                try:
                    await user_app.stop()
                except Exception:
                    pass
    if joined_any:
        join_time = datetime.now().isoformat()
        db["joined_channels"][clean_link] = join_time
        db["channel_join_time"][clean_link] = join_time
        save_data(db)
        print(f"✅ Channel {clean_link} registered for auto-leave in 24 hours")

# --- 🚀 MAIN POSTING LOOP ---
async def auto_posting_loop():
    global db, account_cache, account_status
    active_clients = []
    account_info = []
    try:
        for idx, session_str in enumerate(db["accounts"]):
            try:
                client = Client(f"active_session_{idx}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
                await client.start()
                me = await client.get_me()
                active_clients.append(client)
                account_info.append({
                    "index": idx,
                    "number": idx + 1,
                    "phone": me.phone_number,
                    "name": me.first_name,
                    "client": client
                })
                print(f"✅ Account {idx+1} connected: {me.phone_number}")
            except FloodWait as e:
                error_msg = f"⏳ الحساب {idx+1} ممنوع مؤقتاً لمدة {e.x} ثانية"
                print(f"⚠️ {error_msg}")
                await notify_owner(error_msg)
                active_clients.append(None)
                account_info.append({"index": idx, "number": idx+1, "status": "flood", "error": str(e)})
            except Exception as e:
                error_msg = f"❌ فشل اتصال الحساب {idx+1}: {str(e)[:50]}"
                print(f"⚠️ {error_msg}")
                await notify_owner(error_msg)
                active_clients.append(None)
                account_info.append({"index": idx, "number": idx+1, "status": "error", "error": str(e)})

        timer_value = max(5, int(db.get("timer", 60)))
        valid_accounts = [info for info in account_info if info.get("client") is not None]
        if not valid_accounts:
            error_msg = "❌ لا يوجد حسابات نشطة! إيقاف البوت."
            print(error_msg)
            await notify_owner(error_msg)
            db["is_running"] = False
            save_data(db)
            return

        print(f"🚀 Starting with {len(valid_accounts)} active accounts, timer: {timer_value}s")
        await notify_owner(f"🚀 بدء تشغيل البوت\n📊 {len(valid_accounts)} حساب نشط\n⏱ {timer_value} ثانية")

        consecutive_errors = {info["number"]: 0 for info in valid_accounts}
        max_errors = 5

        while db["is_running"]:
            if not db["accounts"] or not db["templates"] or not db["groups"]:
                db["is_running"] = False
                save_data(db)
                break

            for acc_info in valid_accounts:
                client = acc_info["client"]
                acc_number = acc_info["number"]
                acc_index = acc_info["index"]
                group = get_next_group(acc_index)
                if group is None:
                    continue
                template = get_next_template()
                if template is None:
                    continue

                try:
                    status = await check_account_status(client, acc_number)
                    if status["status"] == "flood":
                        wait_time = status.get("wait", timer_value)
                        print(f"⏳ Acc {acc_number} flood wait {wait_time}s")
                        await asyncio.sleep(wait_time)
                        continue
                    if status["status"] != "active":
                        consecutive_errors[acc_number] += 1
                        if consecutive_errors[acc_number] >= max_errors:
                            error_msg = f"🚨 الحساب {acc_number} عالق/محظور! تم إيقاف نشاطه."
                            print(f"❌ {error_msg}")
                            await notify_owner(error_msg)
                        continue

                    sent_msg = await client.send_message(group, template)
                    db["stats"]["sent_count"] += 1
                    db.setdefault("outgoing_messages", {})
                    db["outgoing_messages"].setdefault(str(sent_msg.chat.id), {})
                    db["outgoing_messages"][str(sent_msg.chat.id)][sent_msg.id] = {
                        "from_account": acc_number,
                        "time": datetime.now().isoformat(),
                        "template": template
                    }
                    db["last_group_index"][str(acc_index)] = db["groups"].index(group)
                    save_data(db)
                    consecutive_errors[acc_number] = 0
                    print(f"✅ Acc {acc_number} sent message to {group}")

                except FloodWait as e:
                    db["stats"]["failed_count"] += 1
                    save_data(db)
                    error_msg = f"⏳ Acc {acc_number} flood wait {e.x}s on {group}"
                    print(f"⚠️ {error_msg}")
                    await notify_owner(error_msg)
                    await asyncio.sleep(e.x)

                except UserBannedInChannel:
                    db["stats"]["failed_count"] += 1
                    save_data(db)
                    error_msg = f"🚫 الحساب {acc_number} ممنوع في {group}"
                    print(f"❌ {error_msg}")
                    await notify_owner(error_msg)
                    if group in db["groups"]:
                        db["groups"].remove(group)
                        save_data(db)

                except Exception as e:
                    db["stats"]["failed_count"] += 1
                    save_data(db)
                    error_text = str(e)
                    print(f"❌ Acc {acc_number} failed to send to {group}: {error_text}")
                    consecutive_errors[acc_number] += 1
                    if "USERNAME_INVALID" in error_text or "PEER_ID_INVALID" in error_text:
                        if group in db["groups"]:
                            db["groups"].remove(group)
                            save_data(db)
                            print(f"🧹 Removed invalid group: {group}")

            await asyncio.sleep(timer_value)
    except Exception as e:
        error_msg = f"❌ خطأ رئيسي في حلقة النشر: {str(e)}"
        print(f"❌ {error_msg}")
        await notify_owner(error_msg)
    finally:
        for client in active_clients:
            if client:
                try:
                    await client.stop()
                except:
                    pass
        if db["is_running"]:
            db["is_running"] = False
            save_data(db)
            await notify_owner("🛑 تم إيقاف البوت تلقائياً بسبب خطأ")

# --- ✅ المعالج الأهم والأول: أي رسالة من بوت تحتوي روابط ---
# تم وضعه كأول معالج (group=0) ليعمل قبل أي شيء آخر
@app.on_message(filters.group & filters.incoming, group=0)
async def handle_bot_messages_with_links(client: Client, message: Message):
    # 1. يجب أن تكون الرسالة من بوت
    if not message.from_user or not message.from_user.is_bot:
        return

    # 2. استخراج جميع الروابط من النص والأزرار
    links = extract_all_links(message)
    if not links:
        return

    # 3. الانضمام لجميع الحسابات
    print(f"🤖 Bot '{message.from_user.username}' sent a message with {len(links)} channel link(s). Joining...")
    for link in links:
        await join_channel_for_all_accounts(link)

# --- Handle user replies to bot messages (in groups) ---
@app.on_message(filters.group & filters.incoming, group=1)
async def handle_user_replies(client: Client, message: Message):
    if not message.from_user or message.from_user.is_bot:
        return
    replied_message = message.reply_to_message
    if not replied_message or not replied_message.from_user:
        return
    chat_id = str(message.chat.id)
    is_our_message = False
    if chat_id in db.get("outgoing_messages", {}):
        if replied_message.id in db["outgoing_messages"][chat_id]:
            is_our_message = True
    if not is_our_message:
        return
    user_info = f"""
📩 **رد وارد على رسالة البوت**

👤 **المرسل:**
• الأيدي: `{message.from_user.id}`
• اليوزر: @{message.from_user.username or 'لا يوجد'}
• الاسم: {message.from_user.first_name} {message.from_user.last_name or ''}

📍 **المكان:**
• النوع: كروب
• الأيدي: `{message.chat.id}`
• الاسم: {message.chat.title or 'بدون اسم'}

💬 **الرسالة:**
{message.text or message.caption or '[وسائط]'}

📋 **معلومات الرسالة:**
• الحساب المرسل: {db['outgoing_messages'][chat_id][replied_message.id].get('from_account', '؟')}
• وقت الإرسال: {db['outgoing_messages'][chat_id][replied_message.id].get('time', '؟')}
• الكليشة: {db['outgoing_messages'][chat_id][replied_message.id].get('template', '؟')[:50]}...

🔄 **للرد:** أرسل رسالة تحتوي على:
`/reply {message.from_user.id} {message.chat.id} {message.id} رسالتك`
"""
    await app.send_message(OWNER_ID, user_info)

# --- Handle private messages from users (non-owner) ---
@app.on_message(filters.private & filters.incoming & ~filters.user(OWNER_ID), group=2)
async def handle_private_messages(client: Client, message: Message):
    if not message.from_user:
        return
    user_info = f"""
📩 **رسالة خاصة جديدة**

👤 **المرسل:**
• الأيدي: `{message.from_user.id}`
• اليوزر: @{message.from_user.username or 'لا يوجد'}
• الاسم: {message.from_user.first_name} {message.from_user.last_name or ''}

📍 **المكان:**
• النوع: خاص

💬 **الرسالة:**
{message.text or message.caption or '[وسائط]'}

🔄 **للرد:** أرسل رسالة تحتوي على:
`/reply_private {message.from_user.id} {message.id} رسالتك`
"""
    await app.send_message(OWNER_ID, user_info)

# --- MAIN HANDLER FOR OWNER (UNIFIED) ---
@app.on_message(filters.private & filters.user(OWNER_ID) & filters.text, group=3)
async def handle_owner_commands(client: Client, message: Message):
    text = message.text.strip()
    user_id_str = str(OWNER_ID)

    if text.startswith("/reply"):
        parts = text.split(maxsplit=3)
        if len(parts) >= 4:
            try:
                user_id = int(parts[1])
                chat_id = int(parts[2])
                reply_text = parts[3]
                account_number = None
                if str(chat_id) in db.get("outgoing_messages", {}):
                    for msg_id, msg_info in db["outgoing_messages"][str(chat_id)].items():
                        if int(msg_id) == int(parts[2]) or True:
                            account_number = msg_info.get("from_account")
                            break
                if account_number:
                    account_index = account_number - 1
                    if account_index < len(db["accounts"]):
                        session_str = db["accounts"][account_index]
                        user_client = Client(f"reply_client_{account_index}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
                        await user_client.start()
                        await user_client.send_message(chat_id, reply_text, reply_to_message_id=int(parts[2]))
                        await user_client.stop()
                        await message.reply_text(f"✅ تم إرسال الرد من الحساب {account_number}")
                    else:
                        await message.reply_text("❌ الحساب غير موجود")
                else:
                    await message.reply_text("❌ لم يتم العثور على الحساب المرسل")
            except Exception as e:
                await message.reply_text(f"❌ خطأ: {e}")
        return

    if text.startswith("/reply_private"):
        parts = text.split(maxsplit=2)
        if len(parts) >= 3:
            try:
                user_id = int(parts[1])
                reply_text = parts[2]
                await app.send_message(user_id, f"💬 **رد من الإدارة:**\n\n{reply_text}")
                await message.reply_text("✅ تم إرسال الرد")
            except Exception as e:
                await message.reply_text(f"❌ خطأ: {e}")
        return

    if text.lower().startswith("/start"):
        return

    state = db["user_state"].get(user_id_str)
    if state:
        if state == "WAITING_PHONE":
            phone = text.strip()
            for session_str in db["accounts"]:
                try:
                    temp_client = Client(f"check_session_{OWNER_ID}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
                    await temp_client.connect()
                    me = await temp_client.get_me()
                    if me.phone_number == phone:
                        await temp_client.disconnect()
                        return await message.reply_text("⚠️ هذا الرقم موجود بالفعل!")
                    await temp_client.disconnect()
                except:
                    continue
            session_name = f"temp_session_{OWNER_ID}"
            temp_client = Client(session_name, api_id=API_ID, api_hash=API_HASH)
            await temp_client.connect()
            try:
                sent_code = await temp_client.send_code(phone)
                login_sessions[OWNER_ID] = {
                    "client": temp_client, "phone": phone, "hash": sent_code.phone_code_hash, "session_name": session_name
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
                return await message.reply_text("❌ انتهت الجلسة. أعد المحاولة.")
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
                return await message.reply_text("❌ رمز التحقق غير صحيح. حاول مرة أخرى:")
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
                return await message.reply_text("❌ انتهت الجلسة. أعد المحاولة.")
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
                temp_client = Client(f"recover_session_{OWNER_ID}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
                await temp_client.connect()
                me = await temp_client.get_me()
                await temp_client.disconnect()
                for existing_session in db["accounts"]:
                    try:
                        check_client = Client(f"check_session_{OWNER_ID}", api_id=API_ID, api_hash=API_HASH, session_string=existing_session)
                        await check_client.connect()
                        check_me = await check_client.get_me()
                        if check_me.phone_number == me.phone_number:
                            await check_client.disconnect()
                            return await message.reply_text("⚠️ هذا الحساب موجود بالفعل!")
                        await check_client.disconnect()
                    except:
                        continue
                db["accounts"].append(session_str)
                db["user_state"].pop(user_id_str, None)
                save_data(db)
                globals()['account_cache'] = {}
                return await message.reply_text(f"✅ تم استرداد الحساب!\nالرقم: {me.phone_number}\nالاسم: {me.first_name}")
            except Exception as e:
                return await message.reply_text(f"❌ فشل الاسترداد: `{e}`")

        elif state == "WAITING_TEMPLATE":
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
            lines = text.strip().split('\n')
            added_count = 0
            for line in lines:
                if line.strip():
                    group = clean_group_link(line.strip())
                    if group not in db["groups"]:
                        db["groups"].append(group)
                        db.setdefault("group_activity", {}).setdefault(group, 0)
                        added_count += 1
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text(f"✅ تمت إضافة {added_count} كروب!")

        elif state == "WAITING_TIMER":
            if text.isdigit() and 2 <= int(text) <= 86400:
                db["timer"] = int(text)
                db["user_state"].pop(user_id_str, None)
                save_data(db)
                return await message.reply_text(f"✅ تم ضبط المؤقت على {text} ثانية")
            else:
                return await message.reply_text("❌ أرسل رقمًا صحيحًا بين 2 و86400")

    action = get_menu_action(text)
    if action:
        db["user_state"].pop(user_id_str, None)

        if action == "accounts":
            if not db["accounts"]:
                return await message.reply_text("❌ لا توجد حسابات مضافة.")
            msg = "📋 الحسابات المضافة:\n\n"
            for i, session_str in enumerate(db["accounts"]):
                info = await get_account_info(session_str, i)
                status = "✅" if info['connected'] else "❌"
                msg += f"{i+1}. {status} 📱 {info['phone']} - 👤 {info['name']}\n"
            await message.reply_text(msg)

        elif action == "groups":
            if not db["groups"]:
                return await message.reply_text("❌ لا توجد كروبات مضافة.")
            msg = "📋 الكروبات المضافة:\n\n"
            for i, g in enumerate(db["groups"], 1):
                msg += f"{i}. {g}\n"
            await message.reply_text(msg)

        elif action == "add_account":
            db["user_state"][user_id_str] = "WAITING_PHONE"
            save_data(db)
            await message.reply_text("📱 أرسل رقم الهاتف مع مفتاح الدولة:\nمثال: +9647800000000")

        elif action == "recover_account":
            db["user_state"][user_id_str] = "WAITING_RECOVER"
            save_data(db)
            await message.reply_text("🔄 أرسل جلسة الاسترداد (Session String):")

        elif action == "delete_account":
            if not db["accounts"]:
                return await message.reply_text("❌ لا توجد حسابات لحذفها.")
            if db["is_running"]:
                return await message.reply_text("⚠️ أوقف البوت أولًا.")
            account_labels = []
            for index, session_str in enumerate(db["accounts"]):
                info = await get_account_info(session_str, index)
                account_labels.append(f"{index+1}. {'✅' if info['connected'] else '❌'} 📱 {info['phone']}")
            keyboard = create_selection_list(account_labels, "account", "delete_account")
            await message.reply_text("🗑 اختر الحساب لحذفه نهائياً:", reply_markup=keyboard)

        elif action == "add_text":
            db["user_state"][user_id_str] = "WAITING_TEMPLATE"
            save_data(db)
            await message.reply_text("📝 أرسل الكليشة الجديدة (يمكنك إرسال عدة كليشات، كل كليشة في سطر منفصل):")

        elif action == "delete_text":
            if not db["templates"]:
                return await message.reply_text("❌ لا توجد كليشات لحذفها.")
            keyboard = create_selection_list(db["templates"], "template", "delete_template")
            await message.reply_text("🗑 اختر الكليشة لحذفها:", reply_markup=keyboard)

        elif action == "add_group":
            db["user_state"][user_id_str] = "WAITING_GROUP"
            save_data(db)
            await message.reply_text("📢 أرسل الكروبات (كل كروب في سطر منفصل):\nمثال:\n@group1\n@group2\nhttps://t.me/+xxxxx")

        elif action == "delete_group":
            if not db["groups"]:
                return await message.reply_text("❌ لا توجد كروبات لحذفها.")
            keyboard = create_selection_list(db["groups"], "group", "delete_group")
            await message.reply_text("🗑 اختر الكروب لحذفه:", reply_markup=keyboard)

        elif action == "start":
            if db["is_running"]:
                return await message.reply_text("⚠️ البوت يعمل حاليًا.")
            if not db["accounts"] or not db["templates"] or not db["groups"]:
                return await message.reply_text("❌ يجب إضافة حساب وكليشة وكروب أولًا.")
            global posting_task
            db["is_running"] = True
            save_data(db)
            posting_task = asyncio.create_task(auto_posting_loop())
            timer_value = db.get('timer', 60)
            await message.reply_text(
                f"🚀 تم تشغيل البوت!\n"
                f"⏱ المؤقت: {timer_value} ثانية\n"
                f"📊 الحسابات: {len(db['accounts'])}\n"
                f"📢 الكروبات: {len(db['groups'])}\n"
                f"📝 الكليشات: {len(db['templates'])}\n"
                f"🔄 توزيع عشوائي للحسابات والجروبات\n"
                f"📡 مراقبة الحظر والتجميد مفعلة\n"
                f"👥 نظام الردود الآلي مفعل"
            )

        elif action == "stop":
            if not db["is_running"]:
                return await message.reply_text("⚠️ البوت متوقف حاليًا.")
            db["is_running"] = False
            save_data(db)
            if posting_task and not posting_task.done():
                posting_task.cancel()
                try:
                    await posting_task
                except asyncio.CancelledError:
                    pass
                posting_task = None
            await message.reply_text("🛑 تم إيقاف البوت.")

        elif action == "timer":
            db["user_state"][user_id_str] = "WAITING_TIMER"
            save_data(db)
            await message.reply_text(f"⏱ المؤقت الحالي: {db.get('timer', 60)} ثانية\nأرسل القيمة الجديدة (بالثواني، حد أدنى 2):")

        elif action == "stats":
            status = "🟢 يعمل" if db["is_running"] else "🔴 متوقف"
            await message.reply_text(
                f"📊 الإحصائيات:\n\n"
                f"الحالة: {status}\n"
                f"الحسابات: {len(db['accounts'])}\n"
                f"الكليشات: {len(db['templates'])}\n"
                f"الكروبات: {len(db['groups'])}\n"
                f"✅ تم الإرسال: {db['stats']['sent_count']}\n"
                f"❌ فشل الإرسال: {db['stats']['failed_count']}\n"
                f"📡 قنوات إجبارية: {len(db.get('joined_channels', {}))}\n"
                f"👥 ردود واردة: {len(db.get('outgoing_messages', {}))}"
            )

        elif action == "clear":
            db["accounts"] = []
            db["templates"] = []
            db["groups"] = []
            db["group_activity"] = {}
            db["stats"] = {"sent_count": 0, "failed_count": 0}
            db["is_running"] = False
            db["joined_channels"] = {}
            db["channel_join_time"] = {}
            db["account_errors"] = {}
            db["last_group_index"] = {}
            db["template_index"] = 0
            save_data(db)
            globals()['account_cache'] = {}
            await message.reply_text("🗑 تم حذف جميع الحسابات والكليشات والكروبات والقنوات الإجبارية.")

        elif action == "incoming_replies":
            if not db.get("outgoing_messages", {}):
                return await message.reply_text("❌ لا توجد ردود واردة.")
            msg = "👥 الردود الواردة:\n\n"
            for chat_id, messages in db["outgoing_messages"].items():
                for msg_id, msg_info in messages.items():
                    msg += f"📍 كروب: {chat_id}\n"
                    msg += f"💬 رسالة ID: {msg_id}\n"
                    msg += f"🤖 حساب: {msg_info.get('from_account', '؟')}\n"
                    msg += f"⏰ وقت: {msg_info.get('time', '؟')}\n"
                    msg += "---\n"
            await message.reply_text(msg)

        elif action == "reply_status":
            status = "🟢 يعمل" if db["is_running"] else "🔴 متوقف"
            await message.reply_text(
                f"🔄 حالة نظام الردود:\n\n"
                f"الحالة: {status}\n"
                f"الردود المستلمة: {len(db.get('outgoing_messages', {}))}\n"
                f"الانضمام التلقائي: {'مفعل' if db.get('auto_join_groups', True) else 'معطل'}\n"
                f"آخر تحديث: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )

        elif action == "reply_settings":
            current = db.get("auto_join_groups", True)
            await message.reply_text(
                f"⚙️ إعدادات الردود:\n\n"
                f"الانضمام التلقائي للكروبات: {'✅ مفعل' if current else '❌ معطل'}\n\n"
                f"لتبديل الحالة أرسل: /toggle_auto_join"
            )

        return

    await message.reply_text("لم أفهم الأمر. أرسل /start ثم اختر أحد أزرار القائمة.")

# --- Selection Helper ---
def create_selection_list(items, item_type, action):
    keyboard = []
    for i, item in enumerate(items):
        display_text = f"{i+1}. {item[:30]}..." if len(item) > 30 else f"{i+1}. {item}"
        keyboard.append([InlineKeyboardButton(display_text, callback_data=f"{action}_{i}")])
    keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
    return InlineKeyboardMarkup(keyboard)

# --- Handle Callback Query ---
@app.on_callback_query()
async def handle_callback(client: Client, callback_query):
    if callback_query.from_user.id != OWNER_ID:
        return await callback_query.answer("غير مصرح")
    data = callback_query.data
    await callback_query.answer()
    if data == "cancel":
        await callback_query.message.delete()
        return
    if data.startswith("delete_template_"):
        index = int(data.split("_")[2])
        if 0 <= index < len(db["templates"]):
            deleted = db["templates"].pop(index)
            save_data(db)
            await callback_query.message.delete()
            await callback_query.message.reply_text(f"🗑 تم حذف الكليشة: {deleted[:50]}...")
        else:
            await callback_query.message.reply_text("❌ العنصر غير موجود.")
    elif data.startswith("delete_group_"):
        index = int(data.split("_")[2])
        if 0 <= index < len(db["groups"]):
            deleted = db["groups"].pop(index)
            save_data(db)
            await callback_query.message.delete()
            await callback_query.message.reply_text(f"🗑 تم حذف الكروب: {deleted}")
        else:
            await callback_query.message.reply_text("❌ العنصر غير موجود.")
    elif data.startswith("delete_account_"):
        index = int(data.split("_")[2])
        if 0 <= index < len(db["accounts"]):
            session_str = db["accounts"].pop(index)
            try:
                temp_client = Client(f"logout_session_{index}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
                await temp_client.start()
                await temp_client.log_out()
                await temp_client.stop()
                await callback_query.message.reply_text(f"🗑 تم حذف الحساب رقم {index+1} وتسجيل الخروج بنجاح!")
            except Exception as e:
                await callback_query.message.reply_text(f"🗑 تم حذف الحساب رقم {index+1} (تعذر تسجيل الخروج: {e})")
            save_data(db)
            await callback_query.message.delete()
            globals()['account_cache'] = {}
        else:
            await callback_query.message.reply_text("❌ العنصر غير موجود.")

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
        return await message.reply_text("⛔ هذا البوت مخصص لمالكه فقط.")
    if db.get("joined_channels"):
        ensure_auto_leave_task()
    db["user_state"].pop(str(OWNER_ID), None)
    save_data(db)
    await message.reply_text(
        "🤖 بوت النشر التلقائي\n\n"
        f"📊 الحسابات: {len(db['accounts'])}\n"
        f"📝 الكليشات: {len(db['templates'])}\n"
        f"📢 الكروبات: {len(db['groups'])}\n"
        f"⏱ المؤقت: {db.get('timer', 60)} ثانية\n"
        f"📡 قنوات إجبارية: {len(db.get('joined_channels', {}))}\n"
        f"📩 ردود واردة: {len(db.get('outgoing_messages', {}))}",
        reply_markup=MAIN_KEYBOARD
    )

# --- Toggle auto join ---
@app.on_message(filters.private & filters.user(OWNER_ID) & filters.command("toggle_auto_join"))
async def toggle_auto_join(client: Client, message: Message):
    db["auto_join_groups"] = not db.get("auto_join_groups", True)
    save_data(db)
    status = "مفعل" if db["auto_join_groups"] else "معطل"
    await message.reply_text(f"✅ الانضمام التلقائي للكروبات الآن: {status}")

# --- Main execution ---
if __name__ == "__main__":
    print("🤖 Bot running with advanced features...")
    print(f"👤 Owner: {OWNER_ID}")
    print(f"📊 Data: {DATA_FILE}")
    print("✨ Features:")
    print("  🔄 Sequential posting system")
    print("  🎯 Template rotation (1, 2, 3...)")
    print("  🔄 Group rotation for each account")
    print("  📡 Auto-join channels from ANY bot message")
    print("  ⏰ Auto-leave after 24 hours")
    print("  👥 Reply forwarding to owner")
    print("  💬 Owner reply system")
    print("  📱 Private message handling")
    print("  🛡️ Account ban/freeze monitoring")
    app.run()
