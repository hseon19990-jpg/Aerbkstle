import os
import asyncio
import json
import random
from pyrogram import Client, filters
from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton, Message
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired

# --- الإعدادات الأساسية (تقرأ من المتغيرات البيئية) ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH")

DATA_FILE = "bot_data.json"
login_sessions = {}

# --- باقي الكود كما هو ---
def load_data():
    if not os.path.exists(DATA_FILE):
        default_data = {
            "accounts": [],
            "templates": [],
            "groups": [],
            "timer": 60,
            "is_running": False,
            "stats": {"sent_count": 0, "failed_count": 0},
            "user_state": {}
        }
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(default_data, f, ensure_ascii=False, indent=4)
        return default_data
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

db = load_data()

app = Client("auto_post_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("➕ إضافة حساب"), KeyboardButton("➖ حذف حساب")],
        [KeyboardButton("📝 إضافة كليشة"), KeyboardButton("🗑 حذف كليشة")],
        [KeyboardButton("📢 إضافة كروب"), KeyboardButton("❌ حذف كروب")],
        [KeyboardButton("▶️ تشغيل البوت"), KeyboardButton("⏹ إيقاف البوت")],
        [KeyboardButton("⏱ تغيير المؤقت"), KeyboardButton("📊 الاحصائيات")],
        [KeyboardButton("🗑 مسح الكل")]
    ],
    resize_keyboard=True
)

async def auto_posting_loop():
    global db
    while db["is_running"]:
        if not db["accounts"] or not db["templates"] or not db["groups"]:
            db["is_running"] = False
            save_data(db)
            break

        accounts = db["accounts"]
        templates = db["templates"]
        groups = db["groups"]
        
        for idx, session_str in enumerate(accounts):
            if not db["is_running"]:
                break
                
            try:
                user_app = Client(f"user_session_{idx}", api_id=API_ID, api_hash=API_HASH, session_string=session_str)
                await user_app.start()
                
                for group in groups:
                    if not db["is_running"]:
                        break
                        
                    template = random.choice(templates)
                    
                    try:
                        await user_app.send_message(group, template)
                        db["stats"]["sent_count"] += 1
                        print(f"✅ تم الإرسال من حساب {idx+1} إلى {group}")
                    except Exception as e:
                        db["stats"]["failed_count"] += 1
                        print(f"❌ فشل الإرسال من حساب {idx+1} إلى {group}: {e}")
                    
                    await asyncio.sleep(2)
                
                await user_app.stop()
                
            except Exception as e:
                db["stats"]["failed_count"] += 1
                print(f"❌ خطأ في الحساب {idx+1}: {e}")

        save_data(db)
        await asyncio.sleep(db.get("timer", 60))

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
        "• حدد المؤقت بين كل دورة نشر\n\n"
        "⚠️ تأكد من إضافة حساب واحد على الأقل، كليشة واحدة، وكروب واحد قبل التشغيل.",
        reply_markup=MAIN_KEYBOARD
    )

@app.on_message(filters.user(OWNER_ID) & filters.text)
async def handle_menu(client: Client, message: Message):
    text = message.text
    user_id_str = str(OWNER_ID)
    state = db["user_state"].get(user_id_str)

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

    elif state == "WAITING_TEMPLATE":
        db["templates"].append(text)
        db["user_state"].pop(user_id_str, None)
        save_data(db)
        return await message.reply_text("✅ تم إضافة الكليشة بنجاح.")

    elif state == "WAITING_GROUP":
        db["groups"].append(text.strip())
        db["user_state"].pop(user_id_str, None)
        save_data(db)
        return await message.reply_text("✅ تم إضافة الكروب بنجاح.")

    elif state == "WAITING_TIMER":
        if text.isdigit():
            db["timer"] = int(text)
            db["user_state"].pop(user_id_str, None)
            save_data(db)
            return await message.reply_text(f"✅ تم تغيير المؤقت إلى {text} ثانية.")
        else:
            return await message.reply_text("❌ يرجى إرسال رقم صحيح بالثواني.")

    if text == "➕ إضافة حساب":
        db["user_state"][user_id_str] = "WAITING_PHONE"
        save_data(db)
        await message.reply_text("📱 أرسل الآن رقم الهاتف الخاص بالحساب مع رمز الدولة:\n(مثال: `+919876543210` أو `+9647800000000`)")

    elif text == "➖ حذف حساب":
        if not db["accounts"]:
            return await message.reply_text("❌ لا توجد حسابات مضافة حالياً.")
        db["accounts"].pop()
        save_data(db)
        await message.reply_text("🗑 تم حذف آخر حساب مضاف.")

    elif text == "📝 إضافة كليشة":
        db["user_state"][user_id_str] = "WAITING_TEMPLATE"
        save_data(db)
        await message.reply_text("📝 أرسل نص الكليشة التي تريد نشرها:")

    elif text == "🗑 حذف كليشة":
        if not db["templates"]:
            return await message.reply_text("❌ لا توجد كليشات مضافة.")
        db["templates"].pop()
        save_data(db)
        await message.reply_text("🗑 تم حذف آخر كليشة مضافة.")

    elif text == "📢 إضافة كروب":
        db["user_state"][user_id_str] = "WAITING_GROUP"
        save_data(db)
        await message.reply_text("📢 أرسل يوزر الكروب (مثال: @mygroup) أو الآيدي الخاص به:")

    elif text == "❌ حذف كروب":
        if not db["groups"]:
            return await message.reply_text("❌ لا توجد كروبات مضافة.")
        db["groups"].pop()
        save_data(db)
        await message.reply_text("🗑 تم حذف آخر كروب مضاف.")

    elif text == "▶️ تشغيل البوت":
        if db["is_running"]:
            return await message.reply_text("⚠️ البوت يعمل بالفعل!")
        if not db["accounts"] or not db["templates"] or not db["groups"]:
            return await message.reply_text("❌ يجب إضافة حساب وكليشة وكروب واحد على الأقل قبل التشغيل.")
        
        db["is_running"] = True
        save_data(db)
        asyncio.create_task(auto_posting_loop())
        await message.reply_text("🚀 تم تشغيل النشر التلقائي بنجاح!")

    elif text == "⏹ إيقاف البوت":
        if not db["is_running"]:
            return await message.reply_text("⚠️ البوت متوقف بالفعل!")
        db["is_running"] = False
        save_data(db)
        await message.reply_text("🛑 تم إيقاف النشر التلقائي.")

    elif text == "⏱ تغيير المؤقت":
        db["user_state"][user_id_str] = "WAITING_TIMER"
        save_data(db)
        await message.reply_text(f"⏱ المؤقت الحالي هو {db.get('timer', 60)} ثانية.\nأرسل الوقت الجديد بالثواني:")

    elif text == "📊 الاحصائيات":
        status = "شغال 🟢" if db["is_running"] else "متوقف 🔴"
        stats_msg = (
            f"📊 **إحصائيات البوت:**\n\n"
            f"🔹 حالة البوت: {status}\n"
            f"🔹 عدد الحسابات: {len(db['accounts'])}\n"
            f"🔹 عدد الكليشات: {len(db['templates'])}\n"
            f"🔹 عدد الكروبات: {len(db['groups'])}\n"
            f"⏱ المؤقت: كل {db.get('timer', 60)} ثانية\n\n"
            f"✅ الرسائل الناجحة: {db['stats']['sent_count']}\n"
            f"❌ الرسائل الفاشلة: {db['stats']['failed_count']}"
        )
        await message.reply_text(stats_msg)
    
    elif text == "🗑 مسح الكل":
        db["accounts"] = []
        db["templates"] = []
        db["groups"] = []
        db["stats"] = {"sent_count": 0, "failed_count": 0}
        db["is_running"] = False
        save_data(db)
        await message.reply_text("🗑 تم مسح جميع البيانات بنجاح!")

if __name__ == "__main__":
    print("🤖 Bot is running...")
    print(f"👤 Owner ID: {OWNER_ID}")
    print(f"📊 Data file: {DATA_FILE}")
    app.run()
