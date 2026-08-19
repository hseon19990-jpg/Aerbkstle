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
account_cache = {}
posting_task = None
auto_leave_task = None

# --- Load/Save Data ---
def load_data():
    if not os.path.exists(DATA_FILE):
        default_data = {
            "accounts": [],
            "templates": [],
            "groups": [],
            "timer": 60,
            "is_running": False,
            "stats": {"sent_count": 0, "failed_count": 0},
            "user_state": {},
            "last_message": {},
            "outgoing_messages": {},
            "joined_channels": {},
            "channel_join_time": {}  # لتتبع وقت الانضمام
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

# --- Bot Client ---
app = Client("auto_post_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- Diagnostics ---
@app.on_message(filters.private & filters.incoming, group=2)
async def trace_private_messages(client: Client, message: Message):
    if not message.from_user:
        return
    command = (message.text or message.caption or "<non-text>")[:80].replace("\n", " ")
    print(f"📨 Incoming private message from user_id={message.from_user.id}: {command!r}")

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
        [KeyboardButton("🗑 حذف الكل")]
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

# --- Extract channel references from text ---
def extract_links(text):
    pattern = (
        r'(?:https?://)?t\.me/(?:\+[\w-]+|joinchat/[\w-]+|[A-Za-z0-9_]+)'
        r'|@[A-Za-z0-9_]{4,}'
        r'|(?<!\d)-100\d{6,}'
    )
    links = []
    seen = set()
    for match in re.findall(pattern, text or "", flags=re.IGNORECASE):
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

# --- Get account info with caching ---
async def get_account_info(session_str, index):
    cache_key = f"{index}_{hash(session_str)}"
    if cache_key in account_cache:
        return account_cache[cache_key]
    
    try:
        temp_client = Client(f"info_session_{index}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
        await temp_client.connect()
        me = await temp_client.get_me()
        info = {"phone": me.phone_number or "غير معروف", "name": me.first_name or "غير معروف", "connected": True}
        await temp_client.disconnect()
        account_cache[cache_key] = info
        return info
    except:
        info = {"phone": "غير معروف", "name": "غير متصل", "connected": False}
        account_cache[cache_key] = info
        return info

# --- 🔥 NEW: Random delay distribution ---
def get_random_delays(timer_value, num_accounts):
    """
    توزيع عشوائي للتأخيرات بين الحسابات
    مثلاً: timer=60, 5 حسابات → [0, 12, 25, 40, 55] (أرقام عشوائية)
    """
    if num_accounts <= 0:
        return []
    
    # توليد نقاط عشوائية موزعة على timer_value ثانية
    delays = sorted(random.sample(range(1, timer_value), min(num_accounts, timer_value - 1)))
    
    # إذا كان عدد الحسابات أكبر من timer_value، نضيف تأخيرات إضافية
    while len(delays) < num_accounts:
        delays.append(random.randint(1, timer_value - 1))
        delays.sort()
    
    return delays[:num_accounts]

# --- 🚀 NEW: Optimized Auto Post Loop with Random Distribution ---
async def auto_posting_loop():
    global db, account_cache
    active_clients = []
    account_delays = []  # تأخيرات عشوائية لكل حساب

    try:
        # Connect accounts
        for idx, session_str in enumerate(db["accounts"]):
            try:
                client = Client(f"active_session_{idx}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
                await client.start()
                active_clients.append(client)
                print(f"✅ Account {idx+1} connected")
            except Exception as e:
                print(f"❌ Failed to connect account {idx+1}: {e}")
                active_clients.append(None)

        # حساب التوزيع العشوائي
        timer_value = max(2, int(db.get("timer", 60)))  # الحد الأدنى 2 ثانية
        num_accounts = len([c for c in active_clients if c is not None])
        
        if num_accounts == 0:
            print("❌ No active accounts!")
            db["is_running"] = False
            save_data(db)
            return

        # توليد تأخيرات عشوائية
        account_delays = get_random_delays(timer_value, num_accounts)
        print(f"⏱ Random delays: {account_delays}")

        # دورة البريد العشوائية
        cycle_count = 0
        while db["is_running"]:
            if not db["accounts"] or not db["templates"] or not db["groups"]:
                db["is_running"] = False
                save_data(db)
                break

            # خلط الحسابات لكل دورة (توزيع عشوائي)
            account_indices = list(range(len(active_clients)))
            random.shuffle(account_indices)
            
            # خلط المجموعات لكل دورة
            shuffled_groups = db["groups"].copy()
            random.shuffle(shuffled_groups)

            print(f"\n🔄 Cycle {cycle_count + 1} started with random distribution")

            # إرسال كل حساب مع تأخيره العشوائي
            for acc_idx, account_position in enumerate(account_indices):
                if not db["is_running"]:
                    break

                current_client = active_clients[account_position]
                if current_client is None:
                    continue

                account_number = account_position + 1
                groups_to_remove = []

                # تأخير عشوائي لهذا الحساب (بالنسبة لبداية الدورة)
                delay = account_delays[acc_idx % len(account_delays)]
                print(f"⏱ Acc {account_number} waiting {delay}s before sending...")
                await asyncio.sleep(delay)

                # إرسال لكل المجموعات (مع خلط عشوائي)
                for group in shuffled_groups:
                    if not db["is_running"]:
                        break

                    template = random.choice(db["templates"])

                    try:
                        sent_msg = await current_client.send_message(group, template)
                        db["stats"]["sent_count"] += 1
                        save_data(db)
                        print(f"✅ Acc {account_number} sent to {group}")
                    except Exception as e:
                        db["stats"]["failed_count"] += 1
                        save_data(db)
                        print(f"❌ Acc {account_number} failed to {group}: {e}")
                        
                        # Auto-remove invalid groups
                        if "USERNAME_INVALID" in str(e) or "PEER_ID_INVALID" in str(e) or "ID not found" in str(e):
                            print(f"⚠️ Marking invalid group for removal: {group}")
                            groups_to_remove.append(group)

                    # انتظار قصير بين الرسائل لنفس الحساب
                    await asyncio.sleep(random.uniform(1, 3))

                # حذف المجموعات التالفة
                if groups_to_remove:
                    for bad_group in groups_to_remove:
                        if bad_group in db["groups"]:
                            db["groups"].remove(bad_group)
                    save_data(db)
                    print(f"🧹 Removed {len(groups_to_remove)} invalid group(s)")

                # إعادة توليد التأخيرات العشوائية كل دورة
                if acc_idx == len(account_indices) - 1:
                    account_delays = get_random_delays(timer_value, num_accounts)
                    print(f"🔄 New random delays for next cycle: {account_delays}")

            cycle_count += 1
            print(f"⏱ Cycle {cycle_count} completed, waiting for timer reset...")

            # انتظار المؤقت كاملاً قبل الدورة التالية
            await asyncio.sleep(timer_value)

    finally:
        for client in active_clients:
            if client:
                try:
                    await client.stop()
                except:
                    pass

# --- 🔥 NEW: Auto Join Channels (12 hours instead of 24) ---
async def auto_leave_channels():
    global db
    while True:
        try:
            now = datetime.now()
            to_remove = []
            
            # التحقق من القنوات التي مضى عليها أكثر من 12 ساعة
            for channel, join_time in db.get("joined_channels", {}).items():
                try:
                    join_dt = datetime.fromisoformat(join_time)
                    if now - join_dt > timedelta(hours=12):  # 🟢 تغيير إلى 12 ساعة
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
                
                # إذا نجح جميع الحسابات في المغادرة، نحذف القناة من القائمة
                if success:
                    db["joined_channels"].pop(channel, None)
                    db["channel_join_time"].pop(channel, None)
                    save_data(db)
                    
            await asyncio.sleep(3600)  # التحقق كل ساعة
            
        except Exception as e:
            print(f"❌ Auto leave error: {e}")
            await asyncio.sleep(60)

def ensure_auto_leave_task():
    global auto_leave_task
    if auto_leave_task is None or auto_leave_task.done():
        auto_leave_task = asyncio.create_task(auto_leave_channels())

# --- 🔥 NEW: Join channel for all accounts (with 12h tracking) ---
async def join_channel_for_all_accounts(channel):
    clean_link = clean_group_link(channel)
    if not clean_link:
        return
    
    # التحقق من أن القناة مسجلة بالفعل
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
    
    # إذا انضم حساب واحد على الأقل، نسجل القناة
    if joined_any:
        join_time = datetime.now().isoformat()
        db["joined_channels"][clean_link] = join_time
        db["channel_join_time"][clean_link] = join_time
        save_data(db)
        print(f"✅ Channel {clean_link} registered for auto-leave in 12 hours")

# --- 🔥 NEW: Handle deleted messages (for mandatory channel join) ---
@app.on_message(filters.group & filters.incoming)
async def handle_deleted_messages(client: Client, message: Message):
    """
    عندما يتم حذف رسالة من أحد حساباتنا، ننتظر الرد من البوت
    """
    if not message.from_user:
        return
    
    # تخزين معرف الرسالة المرسلة من حسابنا
    chat_id = str(message.chat.id)
    
    # نتحقق إذا كانت الرسالة من أحد حساباتنا (من خلال معرف المستخدم)
    # نبحث في الحسابات المخزنة
    message_key = f"{chat_id}:{message.id}"
    
    # نخزن معرف الرسالة المرسلة لتتبع الردود
    if chat_id not in db["outgoing_messages"]:
        db["outgoing_messages"][chat_id] = {}
    
    db["outgoing_messages"][chat_id][message.id] = {
        "from_account": True,
        "time": datetime.now().isoformat()
    }
    save_data(db)

# --- 🔥 NEW: Handle bot replies to deleted messages ---
@app.on_message(filters.group & filters.incoming, group=1)
async def handle_replies_to_deleted(client: Client, message: Message):
    """
    عندما يرد البوت على رسالة محذوفة، نستخرج القناة الإجبارية
    """
    if not message.from_user or not message.from_user.is_bot:
        return
    
    replied_message = message.reply_to_message
    if not replied_message or not replied_message.from_user:
        return
    
    # التحقق من أن الرسالة الأصلية كانت من أحد حساباتنا
    chat_id = str(message.chat.id)
    outgoing_key = f"{chat_id}:{replied_message.id}"
    
    # نبحث في الرسائل المرسلة
    is_our_message = False
    if chat_id in db.get("outgoing_messages", {}):
        if replied_message.id in db["outgoing_messages"][chat_id]:
            is_our_message = True
    
    if not is_our_message:
        return
    
    print(f"🤖 Bot replied to our deleted message in {chat_id}")
    
    # استخراج الروابط من رد البوت
    links = extract_links(message.text or message.caption or "")
    
    # استخراج الأزرار أيضاً (قد تكون القناة داخل زر)
    if message.reply_markup:
        for row in message.reply_markup.inline_keyboard:
            for button in row:
                if button.url:
                    links.append(button.url)
                elif button.text and ("قناة" in button.text or "channel" in button.text.lower()):
                    # قد يكون النص يحتوي على رابط
                    extracted = extract_links(button.text)
                    links.extend(extracted)
    
    if not links:
        print("⚠️ No channel links found in bot reply")
        return
    
    print(f"📢 Found {len(links)} channel(s) to join")
    
    # الانضمام لكل القنوات المكتشفة
    for link in links:
        await join_channel_for_all_accounts(link)

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
        f"📡 قنوات إجبارية: {len(db.get('joined_channels', {}))}",
        reply_markup=MAIN_KEYBOARD
    )

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

# --- Main handler ---
@app.on_message(filters.private & filters.user(OWNER_ID) & filters.text)
async def handle_menu(client: Client, message: Message):
    text = message.text.strip()
    if text.lower().startswith("/start"):
        return

    user_id_str = str(OWNER_ID)
    action = get_menu_action(text)
    state = db["user_state"].get(user_id_str)

    if action is not None:
        db["user_state"].pop(user_id_str, None)
        state = None

    # --- States ---
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

    # --- Menu ---
    if action == "accounts":
        if not db["accounts"]:
            return await message.reply_text("❌ لا توجد حسابات مضافة.")
        msg = "📋 الحسابات المضافة:\n\n"
        for i, session_str in enumerate(db["accounts"]):
            info = await get_account_info(session_str, i)
            msg += f"{i+1}. {'✅' if info['connected'] else '❌'} 📱 {info['phone']} - 👤 {info['name']}\n"
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
            f"🔄 توزيع عشوائي للحسابات والجروبات"
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
            f"📡 قنوات إجبارية: {len(db.get('joined_channels', {}))}"
        )

    elif action == "clear":
        db["accounts"] = []
        db["templates"] = []
        db["groups"] = []
        db["stats"] = {"sent_count": 0, "failed_count": 0}
        db["is_running"] = False
        db["joined_channels"] = {}
        db["channel_join_time"] = {}
        save_data(db)
        globals()['account_cache'] = {}
        await message.reply_text("🗑 تم حذف جميع الحسابات والكليشات والكروبات والقنوات الإجبارية.")

    else:
        await message.reply_text("لم أفهم الأمر. أرسل /start ثم اختر أحد أزرار القائمة.")

if __name__ == "__main__":
    print("🤖 Bot running with advanced features...")
    print(f"👤 Owner: {OWNER_ID}")
    print(f"📊 Data: {DATA_FILE}")
    print("✨ Features:")
    print("  🔄 Random account distribution")
    print("  🎲 Random group shuffling")
    print("  📡 Auto-join mandatory channels")
    print("  ⏰ Auto-leave after 12 hours")
    app.run()
