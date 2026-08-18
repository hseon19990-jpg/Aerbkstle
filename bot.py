import os
import asyncio
import json
import random
import re
from pyrogram import Client, filters
from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton, Message
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired

# --- الإعدادات الأساسية ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH")

# --- مسار حفظ البيانات ---
DATA_FILE = "/app/data/bot_data.json"
login_sessions = {}

# --- إدارة قاعدة البيانات ---
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
            "last_message": {}  # لتخزين آخر رسالة في كل كروب
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

# --- إنشاء البوت ---
app = Client("auto_post_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- لوحة التحكم ---
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("➕ إضافة حساب"), KeyboardButton("➖ حذف حساب")],
        [KeyboardButton("📋 قائمة الحسابات"), KeyboardButton("📋 قائمة الكروبات")],
        [KeyboardButton("📝 إضافة كليشة"), KeyboardButton("🗑 حذف كليشة")],
        [KeyboardButton("📢 إضافة كروب"), KeyboardButton("❌ حذف كروب")],
        [KeyboardButton("▶️ تشغيل البوت"), KeyboardButton("⏹ إيقاف البوت")],
        [KeyboardButton("⏱ تغيير المؤقت"), KeyboardButton("📊 الاحصائيات")],
        [KeyboardButton("🗑 مسح الكل")]
    ],
    resize_keyboard=True
)

# --- دالة لاستخراج الروابط من النص ---
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

# --- حلقة النشر التلقائي (المفهوم الجديد) ---
async def auto_posting_loop():
    global db
    
    account_index = 0  # مؤشر للحساب الحالي
    
    while db["is_running"]:
        # التحقق من وجود بيانات كافية
        if not db["accounts"] or not db["templates"] or not db["groups"]:
            db["is_running"] = False
            save_data(db)
            break
        
        # اختيار الحساب الحالي (بالتناوب)
        session_str = db["accounts"][account_index]
        account_number = account_index + 1
        
        try:
            user_app = Client(f"user_session_{account_index}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
            await user_app.start()
            
            # إرسال رسالة إلى كل الكروبات
            for group in db["groups"]:
                if not db["is_running"]:
                    break
                
                # التحقق من أن الكروب ليس ميتاً
                if group in db.get("last_message", {}):
                    last_msg = db["last_message"][group]
                    if last_msg.get("from_our_account", False):
                        print(f"⏭️ تخطي {group} - آخر رسالة من حسابنا")
                        continue
                
                template = random.choice(db["templates"])
                
                try:
                    sent_msg = await user_app.send_message(group, template)
                    db["stats"]["sent_count"] += 1
                    
                    # تسجيل أن هذه الرسالة من حسابنا
                    if group not in db["last_message"]:
                        db["last_message"][group] = {}
                    db["last_message"][group]["from_our_account"] = True
                    db["last_message"][group]["message_id"] = sent_msg.id
                    
                    save_data(db)
                    print(f"✅ الحساب {account_number} أرسل إلى {group}")
                except Exception as e:
                    db["stats"]["failed_count"] += 1
                    save_data(db)
                    print(f"❌ فشل الحساب {account_number} إلى {group}: {e}")
                
                await asyncio.sleep(2)  # انتظار قصير بين الرسائل
            
            await user_app.stop()
            
        except Exception as e:
            print(f"❌ خطأ في الحساب {account_number}: {e}")
        
        # التبديل إلى الحساب التالي
        account_index = (account_index + 1) % len(db["accounts"])
        
        # انتظار المؤقت قبل الحساب التالي
        await asyncio.sleep(db.get("timer", 200))

# --- مراقبة الردود والانضمام التلقائي ---
@app.on_message(filters.text & filters.private & filters.user(OWNER_ID))
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
                        print(f"✅ الحساب {idx+1} انضم إلى {link}")
                    except Exception as e:
                        print(f"❌ فشل انضمام الحساب {idx+1} إلى {link}: {e}")
                
                await user_app.stop()
                break
            except Exception as e:
                print(f"❌ خطأ في حساب {idx+1}: {e}")

# --- مراقبة الرسائل في الكروبات لتحديث حالة التفاعل ---
@app.on_message(filters.group & filters.incoming)
async def track_group_messages(client: Client, message: Message):
    chat_id = str(message.chat.id)
    chat_username = f"@{message.chat.username}" if message.chat.username else None
    
    # التحقق من أن الكروب في قائمتنا
    if chat_id in db["groups"] or (chat_username and chat_username in db["groups"]):
        if chat_id not in db["last_message"]:
            db["last_message"][chat_id] = {}
        
        # التحقق إذا كانت الرسالة من حسابنا
        is_our_account = False
        # يمكن تحسين هذه الطريقة
        for session_str in db["accounts"]:
            try:
                # محاولة معرفة إذا كان المرسل هو حسابنا
                pass
            except:
                pass
        
        db["last_message"][chat_id]["from_our_account"] = False
        db["last_message"][chat_id]["message_id"] = message.id
        db["last_message"][chat_id]["sender"] = message.from_user.id if message.from_user else None
        save_data(db)

# --- أمر /start ---
@app.on_message(filters.command("start") & filters.user(OWNER_ID))
async def start_cmd(client: Client, message: Message):
    db["user_state"].pop(str(OWNER_ID), None)
    save_data(db)
    await message.reply_text(
        "👋 أهلاً بك في بوت النشر التلقائي!\n\n"
        "📌 يمكنك التحكم في البوت من خلال الأزرار أدناه:\n"
        "• أضف حساب للنشر من خلاله\n"
        "• أضف كليشات للرسائل\n"
        "• أضف كروبات للنشر فيها\n"
        "• حدد المؤقت بين كل حساب والآخر\n\n"
        "⚠️ المؤقت الجديد: كل حساب يرسل حسب الدور\n"
        "مثال: 10 حسابات، مؤقت 200 ثانية = كل حساب يرسل كل 2000 ثانية",
        reply_markup=MAIN_KEYBOARD
    )

# --- معالج الرسائل والأوامر ---
@app.on_message(filters.user(OWNER_ID) & filters.text)
async def handle_menu(client: Client, message: Message):
    text = message.text
    user_id_str = str(OWNER_ID)
    state = db["user_state"].get(user_id_str)

    # 1. مرحلة إدخال رقم الهاتف
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
            return await message.reply_text("📩 تم إرسال كود التحقق إلى حسابك في تليجرام.\nأرسل رمز التحقق الآن:")
        except Exception as e:
            await temp_client.disconnect()
            if os.path.exists(f"{session_name}.session"):
                os.remove(f"{session_name}.session")
            return await message.reply_text(f"❌ حدث خطأ أثناء إرسال الكود:\n`{e}`\nيرجى إعادة المحاولة مع مفتاح الدولة (مثال: +91... أو +964...).")

    # 2. مرحلة إدخال رمز التحقق OTP
    elif state == "WAITING_OTP":
        otp = text.strip()
        session_info = login_sessions.get(OWNER_ID)
        if not session_info:
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text("❌ انتهت الجلسة، اضغط على إضافة حساب وابدأ من جديد.")

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
            return await message.reply_text("✅ تم تسجيل الدخول وإضافة الحساب بنجاح!")
        except SessionPasswordNeeded:
            db["user_state"][user_id_str] = "WAITING_PASSWORD"
            save_data(db)
            return await message.reply_text("🔐 الحساب محمي بالتحقق بخطوتين (2FA).\nأرسل كلمة المرور الخاصة بك:")
        except (PhoneCodeInvalid, PhoneCodeExpired):
            return await message.reply_text("❌ رمز التحقق غير صحيح أو منتهي الصلاحية. يرجى إرسال الرمز الصحيح:")
        except Exception as e:
            await temp_client.disconnect()
            if os.path.exists(f"{session_name}.session"):
                os.remove(f"{session_name}.session")
            del login_sessions[OWNER_ID]
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text(f"❌ خطأ: `{e}`")

    # 3. مرحلة إدخال كلمة المرور (2FA)
    elif state == "WAITING_PASSWORD":
        password = text.strip()
        session_info = login_sessions.get(OWNER_ID)
        if not session_info:
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text("❌ انتهت الجلسة، حاول الإضافة مجدداً.")

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
            return await message.reply_text("✅ تم تأكيد كلمة المرور وإضافة الحساب بنجاح!")
        except Exception as e:
            return await message.reply_text(f"❌ كلمة المرور غير صحيحة. حاول مرة أخرى:\n`{e}`")

    # إضافة كليشة
    elif state == "WAITING_TEMPLATE":
        db["templates"].append(text)
        db["user_state"].pop(user_id_str, None)
        save_data(db)
        return await message.reply_text("✅ تم إضافة الكليشة بنجاح.")

    # إضافة كروب
    elif state == "WAITING_GROUP":
        db["groups"].append(text.strip())
        db["user_state"].pop(user_id_str, None)
        save_data(db)
        return await message.reply_text("✅ تم إضافة الكروب بنجاح.")

    # تغيير المؤقت
    elif state == "WAITING_TIMER":
        if text.isdigit():
            db["timer"] = int(text)
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text(f"✅ تم تغيير المؤقت إلى {text} ثانية.")
        else:
            return await message.reply_text("❌ يرجى إرسال رقم صحيح بالثواني.")

    # --- القوائم الرئيسية ---
    
    # عرض قائمة الحسابات
    if text == "📋 قائمة الحسابات":
        if not db["accounts"]:
            return await message.reply_text("❌ لا توجد حسابات مضافة.")
        
        msg = "📋 **قائمة الحسابات:**\n\n"
        for i in range(len(db["accounts"])):
            msg += f"{i+1}. الحساب {i+1}\n"
        msg += "\nلحذف حساب استخدم: ➖ حذف حساب"
        await message.reply_text(msg)

    # عرض قائمة الكروبات
    elif text == "📋 قائمة الكروبات":
        if not db["groups"]:
            return await message.reply_text("❌ لا توجد كروبات مضافة.")
        
        msg = "📋 **قائمة الكروبات:**\n\n"
        for i, group in enumerate(db["groups"], 1):
            msg += f"{i}. {group}\n"
        msg += "\nلحذف كروب استخدم: ❌ حذف كروب"
        await message.reply_text(msg)

    # إضافة حساب
    elif text == "➕ إضافة حساب":
        db["user_state"][user_id_str] = "WAITING_PHONE"
        save_data(db)
        await message.reply_text("📱 أرسل الآن رقم الهاتف الخاص بالحساب مع رمز الدولة:\n(مثال: `+919876543210` أو `+9647800000000`)")

    # حذف حساب
    elif text == "➖ حذف حساب":
        if not db["accounts"]:
            return await message.reply_text("❌ لا توجد حسابات مضافة حالياً.")
        db["accounts"].pop()
        save_data(db)
        await message.reply_text("🗑 تم حذف آخر حساب مضاف.")

    # إضافة كليشة
    elif text == "📝 إضافة كليشة":
        db["user_state"][user_id_str] = "WAITING_TEMPLATE"
        save_data(db)
        await message.reply_text("📝 أرسل نص الكليشة التي تريد نشرها:")

    # حذف كليشة
    elif text == "🗑 حذف كليشة":
        if not db["templates"]:
            return await message.reply_text("❌ لا توجد كليشات مضافة.")
        db["templates"].pop()
        save_data(db)
        await message.reply_text("🗑 تم حذف آخر كليشة مضافة.")

    # إضافة كروب
    elif text == "📢 إضافة كروب":
        db["user_state"][user_id_str] = "WAITING_GROUP"
        save_data(db)
        await message.reply_text("📢 أرسل يوزر الكروب (مثال: @mygroup) أو الآيدي الخاص به:")

    # حذف كروب
    elif text == "❌ حذف كروب":
        if not db["groups"]:
            return await message.reply_text("❌ لا توجد كروبات مضافة.")
        db["groups"].pop()
        save_data(db)
        await message.reply_text("🗑 تم حذف آخر كروب مضاف.")

    # تشغيل البوت
    elif text == "▶️ تشغيل البوت":
        if db["is_running"]:
            return await message.reply_text("⚠️ البوت يعمل بالفعل!")
        if not db["accounts"] or not db["templates"] or not db["groups"]:
            return await message.reply_text("❌ يجب إضافة حساب وكليشة وكروب واحد على الأقل قبل التشغيل.")
        
        db["is_running"] = True
        save_data(db)
        asyncio.create_task(auto_posting_loop())
        await message.reply_text("🚀 تم تشغيل النشر التلقائي بنجاح!\n\n" +
                                f"📊 عدد الحسابات: {len(db['accounts'])}\n" +
                                f"⏱ المؤقت: {db.get('timer', 200)} ثانية بين كل حساب\n" +
                                f"⏰ كل حساب يرسل كل {db.get('timer', 200) * len(db['accounts'])} ثانية")

    # إيقاف البوت
    elif text == "⏹ إيقاف البوت":
        if not db["is_running"]:
            return await message.reply_text("⚠️ البوت متوقف بالفعل!")
        db["is_running"] = False
        save_data(db)
        await message.reply_text("🛑 تم إيقاف النشر التلقائي.")

    # تغيير المؤقت
    elif text == "⏱ تغيير المؤقت":
        db["user_state"][user_id_str] = "WAITING_TIMER"
        save_data(db)
        await message.reply_text(f"⏱ المؤقت الحالي هو {db.get('timer', 200)} ثانية بين كل حساب.\nأرسل الوقت الجديد بالثواني:")

    # الاحصائيات
    elif text == "📊 الاحصائيات":
        status = "شغال 🟢" if db["is_running"] else "متوقف 🔴"
        total_time = db.get('timer', 200) * len(db["accounts"]) if db["accounts"] else 0
        stats_msg = (
            f"📊 **إحصائيات البوت:**\n\n"
            f"🔹 حالة البوت: {status}\n"
            f"🔹 عدد الحسابات: {len(db['accounts'])}\n"
            f"🔹 عدد الكليشات: {len(db['templates'])}\n"
            f"🔹 عدد الكروبات: {len(db['groups'])}\n"
            f"⏱ المؤقت: {db.get('timer', 200)} ثانية بين كل حساب\n"
            f"⏰ كل حساب يرسل كل {total_time} ثانية\n\n"
            f"✅ الرسائل الناجحة: {db['stats']['sent_count']}\n"
            f"❌ الرسائل الفاشلة: {db['stats']['failed_count']}"
        )
        await message.reply_text(stats_msg)
    
    # مسح الكل
    elif text == "🗑 مسح الكل":
        db["accounts"] = []
        db["templates"] = []
        db["groups"] = []
        db["stats"] = {"sent_count": 0, "failed_count": 0}
        db["is_running"] = False
        save_data(db)
        await message.reply_text("🗑 تم مسح جميع البيانات بنجاح!")

if __name__ == "__main__":
    print("🤖 Bot is running with new features...")
    print(f"👤 Owner ID: {OWNER_ID}")
    print(f"📊 Data file: {DATA_FILE}")
    print(f"⏱ Timer: {db.get('timer', 200)} seconds between accounts")
    app.run()
