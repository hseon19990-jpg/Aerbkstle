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
            "timer": 200,
            "is_running": False,
            "stats": {"sent_count": 0, "failed_count": 0},
            "user_state": {},
            "last_message": {},
            "outgoing_messages": {},
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
db.setdefault("outgoing_messages", {})
db.setdefault("joined_channels", {})

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

# --- Auto Leave Channels ---
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
                retry_needed = False
                for idx, session_str in enumerate(db["accounts"]):
                    user_app = None
                    try:
                        user_app = Client(f"leave_session_{idx}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
                        await user_app.start()
                        await user_app.leave_chat(channel)
                        print(f"🚪 Acc {idx+1} left {channel}")
                    except Exception as e:
                        retry_needed = True
                        print(f"❌ Leave {channel} failed: {e}")
                    finally:
                        if user_app:
                            try:
                                await user_app.stop()
                            except Exception:
                                pass
                if not retry_needed:
                    db["joined_channels"].pop(channel, None)
                    save_data(db)
            await asyncio.sleep(3600)
        except Exception as e:
            print(f"❌ Auto leave error: {e}")
            await asyncio.sleep(60)

def ensure_auto_leave_task():
    global auto_leave_task
    if auto_leave_task is None or auto_leave_task.done():
        auto_leave_task = asyncio.create_task(auto_leave_channels())

async def join_channel_for_all_accounts(channel):
    clean_link = clean_group_link(channel)
    if not clean_link:
        return
    if clean_link in db.get("joined_channels", {}):
        print(f"⏭️ Already tracking {clean_link}")
        return

    ensure_auto_leave_task()
    joined_any = False
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
        db["joined_channels"][clean_link] = datetime.now().isoformat()
        save_data(db)

# --- Optimized Auto Post Loop (Fixed) ---
async def auto_posting_loop():
    global db, account_cache
    account_index = 0
    active_clients = []

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

        while db["is_running"]:
            if not db["accounts"] or not db["templates"] or not db["groups"]:
                db["is_running"] = False
                save_data(db)
                break

            current_client = active_clients[account_index] if account_index < len(active_clients) else None

            if current_client:
                account_number = account_index + 1
                groups_to_remove = []

                for group in db["groups"]:
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
                        
                        # Auto-remove invalid groups safely
                        if "USERNAME_INVALID" in str(e) or "PEER_ID_INVALID" in str(e) or "ID not found" in str(e):
                            print(f"⚠️ Marking invalid group for removal: {group}")
                            groups_to_remove.append(group)

                    await asyncio.sleep(2)

                # Bulk remove invalid groups safely
                if groups_to_remove:
                    for bad_group in groups_to_remove:
                        if bad_group in db["groups"]:
                            db["groups"].remove(bad_group)
                    save_data(db)
                    print(f"🧹 Removed {len(groups_to_remove)} invalid group(s)")

            timer_value = max(1, int(db.get("timer", 200)))
            print(f"⏱ Waiting {timer_value}s for next account...")
            await asyncio.sleep(timer_value)

            account_index = (account_index + 1) % len(db["accounts"])

    finally:
        for client in active_clients:
            if client:
                try:
                    await client.stop()
                except:
                    pass

# --- Auto Join when a bot replies ---
@app.on_message(filters.group & filters.incoming, group=1)
async def handle_replies(client: Client, message: Message):
    if not message.from_user or not message.from_user.is_bot:
        return
    replied_message = message.reply_to_message
    if not replied_message or not replied_message.from_user:
        return
    outgoing_key = f"{message.chat.id}:{replied_message.id}"
    if outgoing_key not in db.get("outgoing_messages", {}):
        return
    links = extract_links(message.text or message.caption or "")
    if not links:
        return
    print(f"🤖 Bot replied; joining {len(links)} channel(s)")
    for link in links:
        await join_channel_for_all_accounts(link)

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
        f"⏱ المؤقت: {db.get('timer', 200)} ثانية",
        reply_markup=MAIN_KEYBOARD
    )

# --- Helper function for selection lists ---
def create_selection_list(items, item_type, action, display_func=None):
    keyboard = []
    for i, item in enumerate(items):
        if display_func:
            display_text = display_func(item, i)
        else:
            display_text = f"{i+1}. {item[:30]}..." if len(item) > 30 else f"{i+1}. {item}"
        keyboard.append([InlineKeyboardButton(display_text, callback_data=f"{action}_{i}")])
    keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
    return InlineKeyboardMarkup(keyboard)

# --- Handle callback queries (FIXED DELETION) ---
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
    
    # --- FIXED: delete_group_ is correctly detected ---
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
            await callback_query.answer("العنصر غير موجود")
    await callback_query.answer()

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
        # --- NEW: Multiple groups per line ---
        lines = text.strip().split('\n')
        added_count = 0
        for line in lines:
            if line.strip():
                group = clean_group_link(line.strip())
                if group not in db["groups"]:  # Prevent duplicates
                    db["groups"].append(group)
                    added_count += 1
        db["user_state"].pop(user_id_str, None)
        save_data(db)
        return await message.reply_text(f"✅ تمت إضافة {added_count} كروب!")

    elif state == "WAITING_TIMER":
        if text.isdigit() and 1 <= int(text) <= 86400:
            db["timer"] = int(text)
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text(f"✅ تم ضبط المؤقت على {text} ثانية")
        else:
            return await message.reply_text("❌ أرسل رقمًا صحيحًا بين 1 و86400")

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
        keyboard = create_selection_list(db["templates"], "template", "delete")
        await message.reply_text("🗑 اختر الكليشة لحذفها:", reply_markup=keyboard)

    elif action == "add_group":
        db["user_state"][user_id_str] = "WAITING_GROUP"
        save_data(db)
        await message.reply_text("📢 أرسل الكروبات (كل كروب في سطر منفصل):\nمثال:\n@group1\n@group2\nhttps://t.me/+xxxxx")

    elif action == "delete_group":
        if not db["groups"]:
            return await message.reply_text("❌ لا توجد كروبات لحذفها.")
        keyboard = create_selection_list(db["groups"], "group", "delete")
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
        timer_value = db.get('timer', 200)
        await message.reply_text(f"🚀 تم تشغيل البوت!\n⏱ المؤقت: {timer_value} ثانية\n📊 الحسابات: {len(db['accounts'])}")

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
        await message.reply_text(f"⏱ المؤقت الحالي: {db.get('timer', 200)} ثانية\nأرسل القيمة الجديدة (بالثواني):")

    elif action == "stats":
        status = "🟢 يعمل" if db["is_running"] else "🔴 متوقف"
        await message.reply_text(
            f"📊 الإحصائيات:\n\nالحالة: {status}\nالحسابات: {len(db['accounts'])}\nالكليشات: {len(db['templates'])}\nالكروبات: {len(db['groups'])}\n✅ تم الإرسال: {db['stats']['sent_count']}\n❌ فشل الإرسال: {db['stats']['failed_count']}"
        )

    elif action == "clear":
        db["accounts"] = []
        db["templates"] = []
        db["groups"] = []
        db["stats"] = {"sent_count": 0, "failed_count": 0}
        db["is_running"] = False
        db["joined_channels"] = {}
        save_data(db)
        globals()['account_cache'] = {}
        await message.reply_text("🗑 تم حذف جميع الحسابات والكليشات والكروبات.")

    else:
        await message.reply_text("لم أفهم الأمر. أرسل /start ثم اختر أحد أزرار القائمة.")

if __name__ == "__main__":
    print("🤖 Bot running...")
    print(f"👤 Owner: {OWNER_ID}")
    print(f"📊 Data: {DATA_FILE}")
    app.run()
