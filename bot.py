import os
import asyncio
import json
import random
import re
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

# --- Data file path ---
# Set DATA_FILE to a mounted volume path when persistent storage is available.
DATA_FILE = os.environ.get("DATA_FILE") or os.path.join(
    os.getcwd(), "data", "bot_data.json"
)
login_sessions = {}

# --- Data management ---
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
            "last_message": {}
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

# --- Bot Client ---
app = Client("auto_post_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- Main Keyboard ---
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("➕ إضافة حساب"), KeyboardButton("➖ حذف حساب")],
        [KeyboardButton("📋 قائمة الحسابات"), KeyboardButton("📋 قائمة الكروبات")],
        [KeyboardButton("📝 إضافة كليشة"), KeyboardButton("🗑 حذف كليشة")],
        [KeyboardButton("📢 إضافة كروب"), KeyboardButton("❌ حذف كروب")],
        [KeyboardButton("▶️ تشغيل البوت"), KeyboardButton("⏹ إيقاف البوت")],
        [KeyboardButton("⏱ تغيير المؤقت"), KeyboardButton("📊 الإحصائيات")],
        [KeyboardButton("🗑 مسح الكل")]
    ],
    resize_keyboard=True
)

# Keep keyboards sent by older versions working after the button language change.
BUTTON_ALIASES = {
    "➕ إضافة حساب": "➕ Add Acc",
    "➖ حذف حساب": "➖ Del Acc",
    "➖ حذف رقم": "➖ Del Acc",
    "📋 قائمة الحسابات": "📋 Accounts",
    "📋 قائمة الكروبات": "📋 Groups",
    "📝 إضافة كليشة": "📝 Add Text",
    "🗑 حذف كليشة": "🗑 Del Text",
    "📢 إضافة كروب": "📢 Add Group",
    "❌ حذف كروب": "❌ Del Group",
    "▶️ تشغيل البوت": "▶️ Start",
    "⏹ إيقاف البوت": "⏹ Stop",
    "⏱ تغيير المؤقت": "⏱ Timer",
    "📊 الاحصائيات": "📊 Stats",
    "📊 الإحصائيات": "📊 Stats",
    "🗑 مسح الكل": "🗑 Clear All",
}

DELETE_CANCEL = "↩️ إلغاء"

def make_selection_keyboard(items, label_builder):
    rows = [
        [KeyboardButton(f"{index}. {label_builder(item)}")]
        for index, item in enumerate(items, 1)
    ]
    rows.append([KeyboardButton(DELETE_CANCEL)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)

def parse_selection(text, item_count):
    if text == DELETE_CANCEL:
        return None
    match = re.match(r"^\s*(\d+)", text)
    if not match:
        return "invalid"
    index = int(match.group(1)) - 1
    return index if 0 <= index < item_count else "invalid"

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

# --- Auto Post Loop (New concept) ---
async def auto_posting_loop():
    global db
    account_index = 0
    
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
                        print(f"⏭️ Skip {group} - last msg from us")
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

# --- Handle replies & auto join ---
@app.on_message(
    filters.text
    & filters.private
    & filters.user(OWNER_ID)
    & filters.regex(
        r"^\s*(?:(?:https?://)?t\.me/[A-Za-z0-9_]+|@[A-Za-z0-9_]+)"
        r"(?:\s+(?:(?:https?://)?t\.me/[A-Za-z0-9_]+|@[A-Za-z0-9_]+))*\s*$"
    )
)
async def handle_replies(client: Client, message: Message):
    links = extract_links(message.text)
    if links:
        for idx, session_str in enumerate(db["accounts"]):
            try:
                user_app = Client(f"reply_session_{idx}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
                await user_app.start()
                
                for link in links:
                    try:
                        if link.startswith('@'):
                            await user_app.join_chat(link)
                        elif 't.me' in link:
                            await user_app.join_chat(link)
                        print(f"✅ Acc {idx+1} joined {link}")
                    except Exception as e:
                        print(f"❌ Acc {idx+1} join {link} failed: {e}")
                
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
        db["last_message"][chat_id]["sender"] = message.from_user.id if message.from_user else None
        save_data(db)

# --- /start command ---
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    # Do not stay silent when Railway has an incorrect OWNER_ID.
    # The Telegram ID is safe to show to the person who sent /start and
    # lets the owner correct the deployment variable immediately.
    sender_id = message.from_user.id if message.from_user else None
    if sender_id != OWNER_ID:
        print(f"Unauthorized /start from Telegram user {sender_id}; configured OWNER_ID={OWNER_ID}")
        await message.reply_text(
            "⚠️ هذا الحساب غير مضاف كمالك للبوت.\n\n"
            f"Telegram ID الخاص بك هو: `{sender_id}`\n"
            "ضع هذا الرقم في Railway ضمن OWNER_ID ثم أعد تشغيل الخدمة."
        )
        return

    db["user_state"].pop(str(OWNER_ID), None)
    save_data(db)
    await message.reply_text(
        "👋 أهلاً بك في بوت النشر التلقائي!\n\n"
        "📌 استخدم الأزرار أدناه للتحكم:\n"
        "• إضافة حسابات للنشر\n"
        "• إضافة الكليشات\n"
        "• إضافة الكروبات\n"
        "• تحديد المؤقت\n\n"
        f"⏱ المؤقت الحالي: {db.get('timer', 200)} ثانية بين الحسابات",
        reply_markup=MAIN_KEYBOARD
    )

# --- Main handler ---
@app.on_message(filters.user(OWNER_ID) & filters.text)
async def handle_menu(client: Client, message: Message):
    text = BUTTON_ALIASES.get(message.text, message.text)
    user_id_str = str(OWNER_ID)
    state = db["user_state"].get(user_id_str)

    # --- States ---
    if state == "WAITING_DELETE_ACCOUNT":
        index = parse_selection(text, len(db["accounts"]))
        if index is None:
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text("↩️ تم إلغاء الحذف.", reply_markup=MAIN_KEYBOARD)
        if index == "invalid":
            return await message.reply_text("❌ اختر رقمًا من القائمة أو اضغط إلغاء.")
        db["accounts"].pop(index)
        db["user_state"].pop(user_id_str, None)
        save_data(db)
        return await message.reply_text(
            f"✅ تم حذف الحساب رقم {index + 1}.",
            reply_markup=MAIN_KEYBOARD,
        )

    elif state == "WAITING_DELETE_TEMPLATE":
        index = parse_selection(text, len(db["templates"]))
        if index is None:
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text("↩️ تم إلغاء الحذف.", reply_markup=MAIN_KEYBOARD)
        if index == "invalid":
            return await message.reply_text("❌ اختر رقمًا من القائمة أو اضغط إلغاء.")
        db["templates"].pop(index)
        db["user_state"].pop(user_id_str, None)
        save_data(db)
        return await message.reply_text(
            f"✅ تم حذف الكليشة رقم {index + 1}.",
            reply_markup=MAIN_KEYBOARD,
        )

    elif state == "WAITING_DELETE_GROUP":
        index = parse_selection(text, len(db["groups"]))
        if index is None:
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text("↩️ تم إلغاء الحذف.", reply_markup=MAIN_KEYBOARD)
        if index == "invalid":
            return await message.reply_text("❌ اختر رقمًا من القائمة أو اضغط إلغاء.")
        deleted_group = db["groups"].pop(index)
        db["user_state"].pop(user_id_str, None)
        db["last_message"].pop(deleted_group, None)
        save_data(db)
        return await message.reply_text(
            f"✅ تم حذف الكروب {deleted_group}.",
            reply_markup=MAIN_KEYBOARD,
        )

    elif state == "WAITING_PHONE":
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
            return await message.reply_text("📩 تم إرسال رمز التحقق. أرسل الرمز الآن:")
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
            return await message.reply_text("❌ انتهت الجلسة. حاول إضافة الحساب من جديد.")

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
            return await message.reply_text("🔐 التحقق بخطوتين مفعّل. أرسل كلمة المرور:")
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
            return await message.reply_text("❌ انتهت الجلسة. حاول إضافة الحساب من جديد.")

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

    elif state == "WAITING_TEMPLATE":
        db["templates"].append(text)
        db["user_state"].pop(user_id_str, None)
        save_data(db)
        return await message.reply_text("✅ تمت إضافة الكليشة بنجاح!")

    elif state == "WAITING_GROUP":
        group = text.strip()
        # Clean invalid formats
        if group.startswith("https://t.me/"):
            group = group.replace("https://t.me/", "@")
        elif group.startswith("t.me/"):
            group = group.replace("t.me/", "@")
        
        if not group.startswith("@"):
            group = f"@{group}"
        
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
            return await message.reply_text("❌ أرسل رقمًا صحيحًا بالثواني")

    # --- Main Menu ---
    if text == "📋 Accounts":
        if not db["accounts"]:
            return await message.reply_text("❌ لا توجد حسابات مضافة.")
        msg = "📋 **الحسابات المضافة:**\n\n"
        for i in range(len(db["accounts"])):
            msg += f"{i+1}. الحساب رقم {i+1}\n"
        await message.reply_text(msg)

    elif text == "📋 Groups":
        if not db["groups"]:
            return await message.reply_text("❌ لا توجد كروبات مضافة.")
        msg = "📋 **الكروبات المضافة:**\n\n"
        for i, g in enumerate(db["groups"], 1):
            msg += f"{i}. {g}\n"
        await message.reply_text(msg)

    elif text == "➕ Add Acc":
        db["user_state"][user_id_str] = "WAITING_PHONE"
        save_data(db)
        await message.reply_text(
            "📱 أرسل رقم الهاتف مع مفتاح الدولة:\n"
            "مثال: +9647800000000"
        )

    elif text == "➖ Del Acc":
        if not db["accounts"]:
            return await message.reply_text("❌ لا توجد حسابات مضافة.")
        db["user_state"][user_id_str] = "WAITING_DELETE_ACCOUNT"
        save_data(db)
        await message.reply_text(
            "🗑 اختر رقم الحساب الذي تريد حذفه:",
            reply_markup=make_selection_keyboard(
                db["accounts"], lambda _: "الحساب"
            ),
        )

    elif text == "📝 Add Text":
        db["user_state"][user_id_str] = "WAITING_TEMPLATE"
        save_data(db)
        await message.reply_text("📝 أرسل نص الكليشة:")

    elif text == "🗑 Del Text":
        if not db["templates"]:
            return await message.reply_text("❌ لا توجد كليشات مضافة.")
        db["user_state"][user_id_str] = "WAITING_DELETE_TEMPLATE"
        save_data(db)
        await message.reply_text(
            "🗑 اختر الكليشة التي تريد حذفها:",
            reply_markup=make_selection_keyboard(
                db["templates"],
                lambda item: (str(item).replace("\n", " ")[:35] or "كليشة فارغة"),
            ),
        )

    elif text == "📢 Add Group":
        db["user_state"][user_id_str] = "WAITING_GROUP"
        save_data(db)
        await message.reply_text("📢 أرسل يوزر الكروب:\nمثال: @mygroup")

    elif text == "❌ Del Group":
        if not db["groups"]:
            return await message.reply_text("❌ لا توجد كروبات مضافة.")
        db["user_state"][user_id_str] = "WAITING_DELETE_GROUP"
        save_data(db)
        await message.reply_text(
            "🗑 اختر الكروب الذي تريد حذفه:",
            reply_markup=make_selection_keyboard(db["groups"], lambda item: str(item)),
        )

    elif text == "▶️ Start":
        if db["is_running"]:
            return await message.reply_text("⚠️ البوت يعمل بالفعل!")
        if not db["accounts"] or not db["templates"] or not db["groups"]:
            return await message.reply_text(
                "❌ يجب إضافة حساب وكليشة وكروب واحد على الأقل قبل التشغيل."
            )
        
        db["is_running"] = True
        save_data(db)
        asyncio.create_task(auto_posting_loop())
        total_time = db.get('timer', 200) * len(db["accounts"])
        await message.reply_text(
            f"🚀 تم تشغيل البوت!\n"
            f"📊 عدد الحسابات: {len(db['accounts'])}\n"
            f"⏱ المؤقت: {db.get('timer', 200)} ثانية بين الحسابات\n"
            f"⏰ كل حساب يرسل كل {total_time} ثانية"
        )

    elif text == "⏹ Stop":
        if not db["is_running"]:
            return await message.reply_text("⚠️ البوت متوقف بالفعل!")
        db["is_running"] = False
        save_data(db)
        await message.reply_text("🛑 تم إيقاف البوت.")

    elif text == "⏱ Timer":
        db["user_state"][user_id_str] = "WAITING_TIMER"
        save_data(db)
        await message.reply_text(
            f"⏱ المؤقت الحالي: {db.get('timer', 200)} ثانية\n"
            "أرسل الوقت الجديد بالثواني:"
        )

    elif text == "📊 Stats":
        status = "🟢 يعمل" if db["is_running"] else "🔴 متوقف"
        total_time = db.get('timer', 200) * len(db["accounts"]) if db["accounts"] else 0
        await message.reply_text(
            f"📊 **إحصائيات البوت:**\n\n"
            f"🔹 الحالة: {status}\n"
            f"🔹 الحسابات: {len(db['accounts'])}\n"
            f"🔹 الكليشات: {len(db['templates'])}\n"
            f"🔹 الكروبات: {len(db['groups'])}\n"
            f"⏱ المؤقت: {db.get('timer', 200)} ثانية\n"
            f"⏰ كل حساب يرسل كل {total_time} ثانية\n"
            f"✅ الرسائل الناجحة: {db['stats']['sent_count']}\n"
            f"❌ الرسائل الفاشلة: {db['stats']['failed_count']}"
        )

    elif text == "🗑 Clear All":
        db["accounts"] = []
        db["templates"] = []
        db["groups"] = []
        db["stats"] = {"sent_count": 0, "failed_count": 0}
        db["is_running"] = False
        db["last_message"] = {}
        save_data(db)
        await message.reply_text("🗑 تم مسح جميع البيانات.", reply_markup=MAIN_KEYBOARD)

if __name__ == "__main__":
    print("🤖 Bot running...")
    print(f"👤 Owner: {OWNER_ID}")
    print(f"📊 Data: {DATA_FILE}")
    print(f"⏱ Timer: {db.get('timer', 200)}s")
    app.run()
