import os
import threading
import re
import logging
import jdatetime
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from supabase import create_client, Client
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters
from telegram.request import HTTPXRequest
from openpyxl import Workbook
from io import BytesIO
from http.server import BaseHTTPRequestHandler, HTTPServer

TEHRAN_TZ = ZoneInfo("Asia/Tehran")
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)
# ==========================================
# بارگذاری متغیرهای محیطی
# ==========================================
load_dotenv()

TOKEN = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))

if not TOKEN or not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("متغیرهای محیطی تنظیم نشده‌اند!")

# ==========================================
# اتصال به Supabase
# ==========================================
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==========================================
# منوی اصلی
# ==========================================
MAIN_KEYBOARD = [
    ["📥 ثبت هزینه", "🧾 هزینه‌های سریع"],
    ["📊 گزارش‌ها", "✏️ مدیریت هزینه‌ها"],
    ["📤 خروجی اکسل", "⚙️ تنظیمات"],
]

def main_keyboard():
    return ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)

def back_keyboard():
    return ReplyKeyboardMarkup([["🔙 بازگشت"]], resize_keyboard=True)

# ==========================================
# دسته‌بندی‌های پیش‌فرض
# ==========================================
DEFAULT_CATEGORIES = [
    "🍔 غذا", "🚕 حمل‌ونقل", "🛒 خرید", "🏠 خانه",
    "🎮 تفریح", "💊 درمان", "💳 قبض", "📦 سایر"
]

# ==========================================
# کلمات کلیدی برای تشخیص خودکار دسته‌بندی
# ==========================================
CATEGORY_KEYWORDS = {
    "🍔 غذا": ["ناهار", "شام", "صبحانه", "غذا", "پیتزا", "برگر", "کباب", "ساندویچ", "فست‌فود", "رستوران"],
    "🚕 حمل‌ونقل": ["تاکسی", "اتوبوس", "مترو", "اسنپ", "قطار", "بی‌آرتی", "پمپ بنزین"],
    "🛒 خرید": ["خرید", "فروشگاه", "سوپرمارکت", "لباس", "کفش", "لوازم", "خواربار"],
    "🏠 خانه": ["خانه", "اجاره", "تعمیرات", "مبلمان", "لوازم خانگی"],
    "🎮 تفریح": ["تفریح", "سینما", "بازی", "کنسرت", "ورزش", "باشگاه"],
    "💊 درمان": ["درمان", "دارو", "دکتر", "بیمارستان", "آزمایشگاه", "مطب"],
    "💳 قبض": ["قبض برق", "برق", "قبض گاز", "قبض اب", "تلفن", "اینترنت", "موبایل"],
    "☕ کافه": ["کافه", "قهوه", "چای", "نسکافه", "کاپوچینو", "ابمیوه"],
}

def init_db():
    try:
        supabase.table("expenses").select("*").limit(1).execute()
    except:
        print("⚠️ لطفاً جدول‌ها را در Supabase بسازید!")
        return
    
    for category in DEFAULT_CATEGORIES:
        try:
            supabase.table("categories").insert({"name": category}).execute()
        except:
            pass

# ==========================================
# توابع دیتابیس
# ==========================================
def get_categories():
    response = supabase.table("categories").select("*").order("id").execute()
    return [(row["id"], row["name"]) for row in response.data]

def category_exists(name):
    response = supabase.table("categories").select("id").eq("name", name).execute()
    return len(response.data) > 0

def add_category(name):
    try:
        supabase.table("categories").insert({"name": name}).execute()
        return True
    except:
        return False

def rename_category(category_id, new_name):
    response = supabase.table("categories").select("name").eq("id", category_id).execute()
    if not response.data:
        return False
    old_name = response.data[0]["name"]
    try:
        supabase.table("categories").update({"name": new_name}).eq("id", category_id).execute()
        supabase.table("expenses").update({"category": new_name}).eq("category", old_name).execute()
        return True
    except:
        return False

def delete_category(category_id):
    response = supabase.table("categories").select("name").eq("id", category_id).execute()
    if not response.data:
        return False
    category_name = response.data[0]["name"]
    if category_name == "📦 سایر":
        return False
    supabase.table("expenses").update({"category": "📦 سایر"}).eq("category", category_name).execute()
    supabase.table("categories").delete().eq("id", category_id).execute()
    return True
    
# ==========================================
# توابع دیتابیس برای هزینه‌های سریع
# ==========================================
def add_quick_expense(user_id, name, amount, category):
    """افزودن هزینه سریع جدید"""
    data = {
        "user_id": user_id,
        "name": name,
        "amount": amount,
        "category": category,
        "created_at": datetime.now(TEHRAN_TZ).isoformat()
    }
    supabase.table("quick_expenses").insert(data).execute()

def get_quick_expenses(user_id):
    """دریافت لیست هزینه‌های سریع کاربر"""
    response = supabase.table("quick_expenses").select("*").eq("user_id", user_id).order("id").execute()
    return response.data

def delete_quick_expense(user_id, expense_id):
    """حذف هزینه سریع"""
    response = supabase.table("quick_expenses").delete().eq("id", expense_id).eq("user_id", user_id).execute()
    return len(response.data) > 0

def update_quick_expense(user_id, expense_id, name, amount, category):
    """ویرایش هزینه سریع"""
    data = {"name": name, "amount": amount, "category": category}
    response = supabase.table("quick_expenses").update(data).eq("id", expense_id).eq("user_id", user_id).execute()
    return len(response.data) > 0    

def add_expense(user_id, amount, description, category):
    data = {
        "user_id": user_id,
        "amount": amount,
        "description": description,
        "category": category,
        "created_at": datetime.now(TEHRAN_TZ).isoformat()
    }
    supabase.table("expenses").insert(data).execute()

def get_expense(user_id, expense_id):
    response = supabase.table("expenses").select("*").eq("id", expense_id).eq("user_id", user_id).execute()
    if response.data:
        row = response.data[0]
        return (row["id"], row["amount"], row["description"], row["category"], row["created_at"])
    return None

def delete_expense(user_id, expense_id):
    response = supabase.table("expenses").delete().eq("id", expense_id).eq("user_id", user_id).execute()
    return len(response.data) > 0

def update_expense(user_id, expense_id, amount, description, category):
    data = {"amount": amount, "description": description, "category": category}
    response = supabase.table("expenses").update(data).eq("id", expense_id).eq("user_id", user_id).execute()
    return len(response.data) > 0

def get_recent_expenses(user_id, limit=10):
    response = supabase.table("expenses").select("*").eq("user_id", user_id).order("id", desc=True).limit(limit).execute()
    return [(row["id"], row["amount"], row["description"], row["category"], row["created_at"]) for row in response.data]

def get_day_expenses(user_id, date_text):
    # بازه‌ی دقیق یک روز؛ مستقل از طول زمان/میلی‌ثانیه‌ی created_at
    next_date = (datetime.strptime(date_text, "%Y-%m-%d") + __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d")
    response = (
        supabase.table("expenses")
        .select("*")
        .eq("user_id", user_id)
        .gte("created_at", f"{date_text} 00:00:00")
        .lt("created_at", f"{next_date} 00:00:00")
        .order("id", desc=True)
        .execute()
    )
    return [(row["id"], row["amount"], row["description"], row["category"], row["created_at"]) for row in response.data]

def get_month_expenses(user_id, month_text):
    # ماه را با اولین روز ماه بعد محدود می‌کنیم؛ بنابراین برای فوریه، آوریل و ... هم درست است.
    first_day = datetime.strptime(f"{month_text}-01", "%Y-%m-%d")
    if first_day.month == 12:
        next_month = first_day.replace(year=first_day.year + 1, month=1, day=1)
    else:
        next_month = first_day.replace(month=first_day.month + 1, day=1)
    next_month_text = next_month.strftime("%Y-%m-%d")

    response = (
        supabase.table("expenses")
        .select("*")
        .eq("user_id", user_id)
        .gte("created_at", f"{month_text}-01 00:00:00")
        .lt("created_at", f"{next_month_text} 00:00:00")
        .order("id", desc=True)
        .execute()
    )
    return response.data

def get_advanced_stats(user_id, start_date, end_date):
    response = supabase.table("expenses").select("*").eq("user_id", user_id).gte("created_at", f"{start_date} 00:00:00").lte("created_at", f"{end_date} 23:59:59").execute()
    rows = response.data
    if not rows:
        return (0, 0, 0, 0), [], []
    total = sum(r["amount"] for r in rows)
    count = len(rows)
    average = total // count if count > 0 else 0
    maximum = max(r["amount"] for r in rows) if rows else 0
    daily = {}
    category = {}
    for row in rows:
        date_key = row["created_at"][:10]
        daily[date_key] = daily.get(date_key, 0) + row["amount"]
        cat = row["category"]
        if cat not in category:
            category[cat] = {"total": 0, "count": 0}
        category[cat]["total"] += row["amount"]
        category[cat]["count"] += 1
    daily_rows = [(d, a, 1) for d, a in daily.items()]
    category_rows = [(c, d["total"], d["count"]) for c, d in category.items()]
    return (total, count, average, maximum), daily_rows, category_rows

# ==========================================
# توابع کمکی
# ==========================================
def normalize_digits(text):
    translation = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return text.translate(translation)

def parse_amount(text):
    text = normalize_digits(text)
    text = text.replace(",", "").replace("٬", "").replace("،", "").replace(" ", "")
    if not text.isdigit():
        return None
    amount = int(text)
    return amount if amount > 0 else None

def parse_expense_text(message):
    message = normalize_digits(message.strip())
    if not message:
        return None
    match = re.match(r"^([\d,\u066C\u060C]+)\s+(.+)$", message)
    if not match:
        return None
    amount_text = match.group(1)
    description = match.group(2).strip()
    amount = parse_amount(amount_text)
    if amount is None or not description:
        return None
    return amount, description

def detect_category(description):
    """تشخیص خودکار دسته‌بندی بر اساس توضیحات"""
    description_lower = description.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in description_lower:
                return category
    return "📦 سایر"  # دسته‌بندی پیش‌فرض

# ==========================================
# تابع تبدیل تاریخ میلادی به شمسی
# ==========================================
def to_jalali(date_str):
    """تبدیل تاریخ میلادی به شمسی با فرمت YYYY-MM-DD"""
    if not date_str:
        return ""
    try:
        # استخراج بخش تاریخ (10 کاراکتر اول)
        date_part = date_str[:10]
        year, month, day = map(int, date_part.split('-'))
        gregorian = datetime(year, month, day)
        jalali = jdatetime.date.fromgregorian(date=gregorian)
        return f"{jalali.year:04d}-{jalali.month:02d}-{jalali.day:02d}"
    except:
        # اگر خطایی رخ داد، همان تاریخ میلادی را برگردان
        return date_str[:10] if len(date_str) >= 10 else ""


def parse_date_input(date_text):
    """
    تنها موتور تشخیص تاریخ در کل ربات.

    ورودی:
        1405-05-23
        1405/05/23
        1405.05.23
        ۲۰۲۶-۰۸-۱۴
        2026-08-14

    خروجی:
        تاریخ میلادی با فرمت YYYY-MM-DD
        یا None
    """

    if not date_text:
        return None

    date_text = normalize_digits(str(date_text).strip())

    # یکسان‌سازی جداکننده‌ها
    date_text = (
        date_text
        .replace("/", "-")
        .replace(".", "-")
        .replace("\\", "-")
    )

    # حذف فاصله اطراف -
    date_text = re.sub(r"\s*-\s*", "-", date_text)

    parts = date_text.split("-")

    if len(parts) != 3:
        return None

    try:
        year, month, day = map(int, parts)
    except ValueError:
        return None

    try:
        # میلادی
        if 1700 <= year <= 3000:
            gregorian = datetime(year, month, day)
            return gregorian.strftime("%Y-%m-%d")

        # شمسی
        if 1200 <= year <= 1600:
            jalali = jdatetime.date(year, month, day)
            gregorian = jalali.togregorian()
            return gregorian.strftime("%Y-%m-%d")

    except (ValueError, TypeError):
        return None

    return None

def get_date_info(date_text):
    """
    اطلاعات کامل تاریخ را بر اساس parse_date_input برمی‌گرداند.

    تمام بخش‌های ربات باید برای تشخیص تاریخ
    از parse_date_input استفاده کنند.
    """

    if not date_text:
        return None

    original = normalize_digits(str(date_text).strip())

    gregorian = parse_date_input(original)

    if not gregorian:
        return None

    try:
        gregorian_date = datetime.strptime(
            gregorian,
            "%Y-%m-%d"
        )

        jalali_date = jdatetime.date.fromgregorian(
            date=gregorian_date
        )

        jalali = (
            f"{jalali_date.year:04d}-"
            f"{jalali_date.month:02d}-"
            f"{jalali_date.day:02d}"
        )

        # تشخیص نوع تقویم فقط برای اطلاعات خروجی
        normalized = (
            original
            .replace("/", "-")
            .replace(".", "-")
            .replace("\\", "-")
        )

        input_year = int(normalized.split("-")[0])

        calendar = (
            "gregorian"
            if 1700 <= input_year <= 3000
            else "jalali"
        )

        return {
            "input": original,
            "calendar": calendar,
            "gregorian": gregorian,
            "jalali": jalali
        }

    except (ValueError, TypeError):
        return None

def category_keyboard():
    categories = get_categories()
    keyboard = []
    row = []
    for _, name in categories:
        row.append(name)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append(["🔙 بازگشت"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def is_allowed(user_id):
    return user_id == ALLOWED_USER_ID

# ==========================================
# هندلرها
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ شما اجازه استفاده از این ربات را ندارید.")
        return
    context.user_data.clear()
    await update.message.reply_text(
        "سلام 👋\n\n💰 دفتر هزینه شخصی آماده است.\n\n⚡ ثبت سریع:\n85 ناهار\n85000 خرید\n\nاز منوی پایین انتخاب کن.",
        reply_markup=main_keyboard()
    )

async def go_back(update, context):
    context.user_data.clear()
    await update.message.reply_text("🏠 برگشتیم به منوی اصلی.", reply_markup=main_keyboard())

async def quick_expenses_menu(update, context):
    """منوی هزینه‌های سریع"""
    user_id = update.effective_user.id

    if not is_allowed(user_id):
        return

    # دریافت از دیتابیس
    quick_items = get_quick_expenses(user_id)

    if not quick_items:
        # اگر هیچ هزینه سریعی وجود نداشت، پیام نمایش بده
        await update.message.reply_text(
            "🧾 **هزینه‌های سریع**\n\n"
            "هیچ هزینه سریعی ثبت نشده.\n\n"
            "برای افزودن، به بخش مدیریت هزینه‌های سریع برو.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️ مدیریت هزینه‌های سریع", callback_data="quick_manage")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="back_menu")]
            ])
        )
        return

    # ساخت دکمه‌ها از دیتابیس
    keyboard = []
    row = []
    for item in quick_items:
        row.append(InlineKeyboardButton(
            f"{item['name']} ({item['amount']:,})",
            callback_data=f"quick_{item['id']}"
        ))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("⚙️ مدیریت هزینه‌های سریع", callback_data="quick_manage")])
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_menu")])

    await update.message.reply_text(
        "🧾 **هزینه‌های سریع**\n\n"
        "یکی از گزینه‌های زیر رو انتخاب کن تا هزینه ثبت بشه:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def quick_menu_callback(update, context):
    """بازگشت به منوی هزینه‌های سریع"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not is_allowed(user_id):
        return

    quick_items = [
        ["🍔 ناهار", "🚕 تاکسی", "☕ کافه"],
        ["🛒 خرید", "💳 قبض", "🏠 اجاره"],
    ]

    keyboard = []
    for row in quick_items:
        keyboard.append([InlineKeyboardButton(item, callback_data=f"quick_{item}") for item in row])

    keyboard.append([InlineKeyboardButton("⚙️ مدیریت هزینه‌های سریع", callback_data="quick_manage")])
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_menu")])

    await query.edit_message_text(
        "🧾 **هزینه‌های سریع**\n\n"
        "یکی از گزینه‌های زیر رو انتخاب کن تا هزینه با مبلغ پیش‌فرض ثبت بشه:\n\n"
        "🍔 ناهار: ۸۵,۰۰۰ تومان\n"
        "🚕 تاکسی: ۵۰,۰۰۰ تومان\n"
        "☕ کافه: ۳۵,۰۰۰ تومان\n"
        "🛒 خرید: ۲۰۰,۰۰۰ تومان\n"
        "💳 قبض: ۱۵۰,۰۰۰ تومان\n"
        "🏠 اجاره: ۵۰۰,۰۰۰ تومان\n\n"
        "یا می‌تونی با فرمت `مبلغ توضیح` ثبت کنی:\n"
        "مثال: `85000 ناهار`",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def quick_manage_callback(update, context):
    """منوی مدیریت هزینه‌های سریع"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not is_allowed(user_id):
        return

    buttons = [
        [InlineKeyboardButton("➕ افزودن هزینه سریع", callback_data="quick_add")],
        [InlineKeyboardButton("✏️ ویرایش هزینه سریع", callback_data="quick_edit")],
        [InlineKeyboardButton("🗑️ حذف هزینه سریع", callback_data="quick_delete")],
        [InlineKeyboardButton("🔙 بازگشت به هزینه‌های سریع", callback_data="quick_menu")],
    ]

    await query.edit_message_text(
        "⚙️ **مدیریت هزینه‌های سریع**\n\n"
        "می‌توانی هزینه‌های سریع رو مدیریت کنی:\n"
        "➕ افزودن هزینه جدید\n"
        "✏️ ویرایش مبلغ یا نام\n"
        "🗑️ حذف هزینه‌های غیرضروری\n\n"
        "یکی از گزینه‌های زیر رو انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def quick_callback(update, context):
    """ثبت هزینه‌های سریع"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not is_allowed(user_id):
        return

    # دریافت id هزینه سریع
    quick_id = int(query.data.replace("quick_", ""))

    # دریافت از دیتابیس
    response = supabase.table("quick_expenses").select("*").eq("id", quick_id).eq("user_id", user_id).execute()
    if not response.data:
        await query.edit_message_text("❌ هزینه سریع پیدا نشد.")
        return

    item = response.data[0]
    name = item["name"]
    amount = item["amount"]
    category = item["category"]

    # ثبت هزینه اصلی
    add_expense(user_id, amount, name, category)

    await query.edit_message_text(
        f"✅ هزینه ثبت شد!\n\n"
        f"{category}\n"
        f"💰 {amount:,} تومان\n"
        f"📝 {name}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 بازگشت به منو", callback_data="back_menu")]
        ])
    )

async def expense_button(update, context):
    context.user_data.clear()
    context.user_data["waiting_for_expense"] = True
    await update.message.reply_text("➕ ثبت هزینه\n\nدسته‌بندی را انتخاب کن:", reply_markup=category_keyboard())

async def choose_category(update, context, category):
    context.user_data["selected_category"] = category
    context.user_data["waiting_for_expense"] = False
    context.user_data["waiting_for_amount"] = True
    await update.message.reply_text(
        f"{category}\n\nمبلغ و توضیح هزینه را وارد کن.\n\nمثال:\n85000 ناهار",
        reply_markup=back_keyboard()
    )

async def report(update, context):
    """گزارش امروز با صفحه‌بندی"""
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        return
    
    today = datetime.now(TEHRAN_TZ).strftime("%Y-%m-%d")
    today_jalali = to_jalali(today)
    
    # دریافت صفحه از context
    page = context.user_data.get("report_page", 0)
    limit = 5  # تعداد آیتم در هر صفحه
    
    # دریافت همه هزینه‌های امروز
    rows = get_day_expenses(user_id, today)
    
    if not rows:
        await update.message.reply_text("📊 امروز هنوز هزینه‌ای ثبت نشده.", reply_markup=main_keyboard())
        return
    
    # محاسبه صفحه‌بندی
    total_items = len(rows)
    total_pages = (total_items + limit - 1) // limit
    offset = page * limit
    page_rows = rows[offset:offset + limit]
    
    if not page_rows and page > 0:
        page = total_pages - 1
        context.user_data["report_page"] = page
        offset = page * limit
        page_rows = rows[offset:offset + limit]
    
    total = sum(row[1] for row in rows)
    
    # ساخت متن گزارش
    text = f"📊 گزارش امروز\n📅 {today_jalali}\n"
    text += f"📄 صفحه {page + 1} از {total_pages}\n"
    text += f"💰 مجموع کل: {total:,} تومان\n"
    text += f"━━━━━━━━━━━━\n\n"
    
    # ✅ اینجا باید page_rows رو استفاده کنید، نه rows رو
    for expense_id, amount, description, category, created_at in page_rows:
        time = created_at[11:16] if len(created_at) > 11 else ""
        text += f"#{expense_id} {category}\n💰 {amount:,} تومان\n📝 {description} | 🕐 {time}\n\n"
    
    # ساخت دکمه‌های صفحه‌بندی
    buttons = []
    nav_buttons = []
    
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"report_page:{page-1}"))
    
    nav_buttons.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="ignore"))
    
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("➡️ بعدی", callback_data=f"report_page:{page+1}"))
    
    if nav_buttons:
        buttons.append(nav_buttons)
    
    buttons.append([InlineKeyboardButton("🔙 بازگشت به منو", callback_data="back_menu")])
    
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def report_page_callback(update, context):
    """تغییر صفحه در گزارش امروز"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if not is_allowed(user_id):
        return
    
    # دریافت صفحه جدید
    page = int(query.data.split(":")[1])
    context.user_data["report_page"] = page
    
    today = datetime.now(TEHRAN_TZ).strftime("%Y-%m-%d")
    today_jalali = to_jalali(today)
    
    # دریافت همه هزینه‌های امروز
    rows = get_day_expenses(user_id, today)
    
    if not rows:
        await query.edit_message_text("📊 امروز هنوز هزینه‌ای ثبت نشده.", reply_markup=main_keyboard())
        return
    
    # محاسبه صفحه‌بندی
    limit = 5
    total_items = len(rows)
    total_pages = (total_items + limit - 1) // limit
    offset = page * limit
    page_rows = rows[offset:offset + limit]
    
    total = sum(row[1] for row in rows)
    
    # ساخت متن گزارش
    text = f"📊 گزارش امروز\n📅 {today_jalali}\n"
    text += f"📄 صفحه {page + 1} از {total_pages}\n"
    text += f"💰 مجموع کل: {total:,} تومان\n"
    text += f"━━━━━━━━━━━━\n\n"
    
    for expense_id, amount, description, category, created_at in page_rows:
        time = created_at[11:16] if len(created_at) > 11 else ""
        text += f"#{expense_id} {category}\n💰 {amount:,} تومان\n📝 {description} | 🕐 {time}\n\n"
    
    # ساخت دکمه‌های صفحه‌بندی
    buttons = []
    nav_buttons = []
    
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"report_page:{page-1}"))
    
    nav_buttons.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="ignore"))
    
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("➡️ بعدی", callback_data=f"report_page:{page+1}"))
    
    if nav_buttons:
        buttons.append(nav_buttons)
    
    buttons.append([InlineKeyboardButton("🔙 بازگشت به منو", callback_data="back_menu")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def monthly_report(update, context):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        return
    month = datetime.now().strftime("%Y-%m")
    rows = get_month_expenses(user_id, month)
    if not rows:
        await update.message.reply_text("📅 این ماه هنوز هزینه‌ای ثبت نشده.", reply_markup=main_keyboard())
        return
    categories = {}
    for row in rows:
        cat = row["category"]
        if cat not in categories:
            categories[cat] = {"total": 0, "count": 0}
        categories[cat]["total"] += row["amount"]
        categories[cat]["count"] += 1
    total = sum(c["total"] for c in categories.values())
    count = sum(c["count"] for c in categories.values())
    text = "📅 گزارش ماه جاری\n\n"
    for category, data in categories.items():
        text += f"{category}\n💰 {data['total']:,} تومان ({data['count']} مورد)\n\n"
    text += "━━━━━━━━━━━━\n"
    text += f"🧾 تعداد هزینه‌ها: {count}\n💵 مجموع: {total:,} تومان"
    await update.message.reply_text(text, reply_markup=main_keyboard())

async def recent(update, context):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        return
    rows = get_recent_expenses(user_id)
    if not rows:
        await update.message.reply_text("📋 هنوز هیچ هزینه‌ای ثبت نشده.", reply_markup=main_keyboard())
        return
    text = "📋 آخرین هزینه‌ها\n\n"
    for expense_id, amount, description, category, created_at in rows:
        text += f"#{expense_id} {category}\n💰 {amount:,} تومان\n📝 {description}\n📅 {created_at[:10]} | 🕐 {created_at[11:16]}\n\n"
    await update.message.reply_text(text, reply_markup=main_keyboard())

async def stats(update, context):
    """نمایش آمار کلی هزینه‌ها"""
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        return
    
    # دریافت همه هزینه‌ها
    response = supabase.table("expenses").select("*").eq("user_id", user_id).execute()
    rows = response.data
    
    if not rows:
        await update.message.reply_text("📊 هنوز هیچ هزینه‌ای ثبت نشده.", reply_markup=main_keyboard())
        return
    
    # محاسبه آمار
    total = sum(row["amount"] for row in rows)
    count = len(rows)
    average = total // count if count > 0 else 0
    maximum = max(row["amount"] for row in rows) if rows else 0
    minimum = min(row["amount"] for row in rows) if rows else 0
    
    # هزینه‌های امروز
    today = datetime.now(TEHRAN_TZ).strftime("%Y-%m-%d")
    today_rows = [row for row in rows if row["created_at"].startswith(today)]
    today_count = len(today_rows)
    today_total = sum(row["amount"] for row in today_rows)
    
    # هزینه‌های این ماه
    month = datetime.now(TEHRAN_TZ).strftime("%Y-%m")
    month_rows = [row for row in rows if row["created_at"].startswith(month)]
    month_count = len(month_rows)
    month_total = sum(row["amount"] for row in month_rows)
    
    # تبدیل تاریخ امروز به شمسی
    today_jalali = to_jalali(today)
    
    text = "📊 **آمار کلی هزینه‌ها**\n\n"
    text += f"💰 **مجموع کل:** {total:,} تومان\n"
    text += f"🧾 **تعداد کل:** {count} هزینه\n"
    text += f"📊 **میانگین هر هزینه:** {average:,} تومان\n"
    text += f"🔺 **بیشترین هزینه:** {maximum:,} تومان\n"
    text += f"🔻 **کمترین هزینه:** {minimum:,} تومان\n\n"
    text += "━━━━━━━━━━━━\n"
    text += f"📅 **امروز ({today_jalali})**\n"
    text += f"🧾 {today_count} هزینه - 💰 {today_total:,} تومان\n\n"
    text += f"📅 **این ماه**\n"
    text += f"🧾 {month_count} هزینه - 💰 {month_total:,} تومان"
    
    await update.message.reply_text(text, reply_markup=main_keyboard())

async def show_date_report(update, context, date_text):
    """گزارش تاریخ دلخواه"""

    user_id = update.effective_user.id

    # تاریخ باید از قبل توسط parse_date_input تبدیل شده باشد
    gregorian_date = parse_date_input(date_text)

    if not gregorian_date:
        await update.message.reply_text(
            "❌ تاریخ نامعتبر است.",
            reply_markup=main_keyboard()
        )
        context.user_data.clear()
        return

    rows = get_day_expenses(user_id, gregorian_date)

    if not rows:
        date_jalali = to_jalali(gregorian_date)

        await update.message.reply_text(
            f"📅 برای {date_jalali} هزینه‌ای ثبت نشده.",
            reply_markup=main_keyboard()
        )

        context.user_data.clear()
        return

    page = context.user_data.get("date_report_page", 0)
    limit = 5

    total_items = len(rows)
    total_pages = (total_items + limit - 1) // limit

    offset = page * limit
    page_rows = rows[offset:offset + limit]

    if not page_rows and page > 0:
        page = total_pages - 1
        context.user_data["date_report_page"] = page

        offset = page * limit
        page_rows = rows[offset:offset + limit]

    total = sum(row[1] for row in rows)

    date_jalali = to_jalali(gregorian_date)

    text = f"📅 گزارش {date_jalali}\n"
    text += f"📄 صفحه {page + 1} از {total_pages}\n"
    text += f"💰 مجموع کل: {total:,} تومان\n"
    text += "━━━━━━━━━━━━\n\n"

    for expense_id, amount, description, category, created_at in page_rows:
        time = created_at[11:16] if len(created_at) > 11 else ""

        text += (
            f"#{expense_id} {category}\n"
            f"💰 {amount:,} تومان\n"
            f"📝 {description}\n"
            f"🕐 {time}\n\n"
        )

    text += "━━━━━━━━━━━━\n"
    text += f"🧾 تعداد: {len(rows)}\n"
    text += f"💵 مجموع: {total:,} تومان"

    buttons = []
    nav_buttons = []

    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                "⬅️ قبلی",
                callback_data=f"date_page:{gregorian_date}:{page-1}"
            )
        )

    nav_buttons.append(
        InlineKeyboardButton(
            f"📄 {page + 1}/{total_pages}",
            callback_data="ignore"
        )
    )

    if page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton(
                "➡️ بعدی",
                callback_data=f"date_page:{gregorian_date}:{page+1}"
            )
        )

    buttons.append(nav_buttons)

    buttons.append([
        InlineKeyboardButton(
            "🔙 بازگشت به منو",
            callback_data="back_menu"
        )
    ])

    context.user_data["date_report_date"] = gregorian_date
    context.user_data["date_report_page"] = page

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def date_page_callback(update, context):
    """تغییر صفحه در گزارش تاریخ"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not is_allowed(user_id):
        return

    # دریافت تاریخ و صفحه جدید
    parts = query.data.split(":")
    date_text = parts[1]
    page = int(parts[2])

    # اطمینان از اینکه تاریخ همیشه میلادی استاندارد است
    gregorian_date = parse_date_input(date_text)

    if not gregorian_date:
        await query.edit_message_text(
            "❌ تاریخ گزارش نامعتبر است.",
            reply_markup=main_keyboard()
        )
        return

    context.user_data["date_report_page"] = page
    context.user_data["date_report_date"] = gregorian_date

    # دریافت همه هزینه‌های اون روز
    rows = get_day_expenses(user_id, gregorian_date)

    if not rows:
        await query.edit_message_text(
            f"📅 برای {date_text} هزینه‌ای ثبت نشده.",
            reply_markup=main_keyboard()
        )
        return

    # محاسبه صفحه‌بندی
    limit = 5
    total_items = len(rows)
    total_pages = (total_items + limit - 1) // limit
    offset = page * limit
    page_rows = rows[offset:offset + limit]

    total = sum(row[1] for row in rows)
    date_jalali = to_jalali(gregorian_date)

    # ساخت متن گزارش
    text = f"📅 گزارش {date_jalali}\n"
    text += f"📄 صفحه {page + 1} از {total_pages}\n"
    text += f"💰 مجموع کل: {total:,} تومان\n"
    text += f"━━━━━━━━━━━━\n\n"

    for expense_id, amount, description, category, created_at in page_rows:
        time = created_at[11:16] if len(created_at) > 11 else ""
        text += f"#{expense_id} {category}\n💰 {amount:,} تومان\n📝 {description}\n🕐 {time}\n\n"

    text += "━━━━━━━━━━━━\n"
    text += f"🧾 تعداد: {len(rows)}\n"
    text += f"💵 مجموع: {total:,} تومان"

    # ساخت دکمه‌های صفحه‌بندی
    buttons = []
    nav_buttons = []

    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                "⬅️ قبلی",
                callback_data=f"date_page:{gregorian_date}:{page-1}"
            )
        )

    nav_buttons.append(
        InlineKeyboardButton(
            f"📄 {page + 1}/{total_pages}",
            callback_data="ignore"
        )
    )

    if page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton(
                "➡️ بعدی",
                callback_data=f"date_page:{gregorian_date}:{page+1}"
            )
        )

    buttons.append(nav_buttons)

    buttons.append([
        InlineKeyboardButton(
            "🔙 بازگشت به منو",
            callback_data="back_menu"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def advanced_report_button(update, context):
    """شروع گزارش پیشرفته با دکمه‌های میانبر"""
    context.user_data.clear()
    context.user_data["waiting_advanced_start"] = True
    
    # دکمه‌های میانبر برای تاریخ
    keyboard = [
        [
            InlineKeyboardButton("📅 امروز", callback_data="adv_today"),
            InlineKeyboardButton("📅 این هفته", callback_data="adv_this_week"),
        ],
        [
            InlineKeyboardButton("📅 هفته گذشته", callback_data="adv_week"),
            InlineKeyboardButton("📅 ماه جاری", callback_data="adv_month"),
        ],
        [
            InlineKeyboardButton("📅 سه ماه اخیر", callback_data="adv_quarter"),
            InlineKeyboardButton("✏️ وارد کردن دستی", callback_data="adv_manual"),
        ],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_menu")],
    ]
    
    await update.message.reply_text(
        "📈 گزارش پیشرفته\n\n"
        "یک بازه زمانی را انتخاب کن:\n"
        "یا دکمه «وارد کردن دستی» را بزن تا تاریخ را خودت وارد کنی.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def advanced_quick_callback(update, context):
    """دکمه‌های میانبر برای گزارش پیشرفته"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not is_allowed(user_id):
        return

    today = datetime.now().date()
    action = query.data

    if action == "adv_today":
        start_date = today.strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")

    elif action == "adv_this_week":
        # شروع هفته از دوشنبه
        start_of_week = today - timedelta(days=today.weekday())
        start_date = start_of_week.strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")

    elif action == "adv_week":
        start_date = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")

    elif action == "adv_month":
        # اولین روز ماه شمسی
        today_jalali = jdatetime.date.fromgregorian(date=today)

        first_day_jalali = jdatetime.date(
            today_jalali.year,
            today_jalali.month,
            1
        )

        start_date = (
            first_day_jalali
            .togregorian()
            .strftime("%Y-%m-%d")
        )

        end_date = today.strftime("%Y-%m-%d")

    elif action == "adv_quarter":
        start_date = (today - timedelta(days=90)).strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")

    elif action == "adv_manual":
        await query.edit_message_text(
            "📅 تاریخ شروع را وارد کن:\n\n"
            "مثال:\n"
            "2026-08-01"
        )

        context.user_data["waiting_advanced_start"] = True
        return

    else:
        await query.edit_message_text("❌ گزینه نامعتبر.")
        return

    context.user_data.clear()

    await show_advanced_report(
        update,
        context,
        start_date,
        end_date,
        from_callback=True
    )

async def show_advanced_report(update, context, start_date, end_date, from_callback=False):
    """نمایش گزارش پیشرفته با تاریخ شمسی"""
    user_id = update.effective_user.id
    
    # تبدیل تاریخ‌ها به شمسی برای نمایش
    start_jalali = to_jalali(start_date)
    end_jalali = to_jalali(end_date)
    
    (total, count, average, maximum), daily_rows, category_rows = get_advanced_stats(user_id, start_date, end_date)
    
    if count == 0:
        if from_callback and update.callback_query:
            await update.callback_query.edit_message_text("📊 در این بازه هزینه‌ای ثبت نشده.")
        else:
            await update.message.reply_text("📊 در این بازه هزینه‌ای ثبت نشده.", reply_markup=main_keyboard())
        context.user_data.clear()
        return
    
    # مرتب‌سازی دسته‌بندی‌ها بر اساس مبلغ (بیشترین اول)
    category_rows_sorted = sorted(category_rows, key=lambda x: x[1], reverse=True)
    
    text = f"📈 گزارش پیشرفته\n\n"
    text += f"📅 از {start_jalali}\n"
    text += f"📅 تا {end_jalali}\n\n"
    text += "━━━━━━━━━━━━\n"
    text += f"💵 مجموع: {total:,} تومان\n"
    text += f"🧾 تعداد: {count}\n"
    text += f"📊 میانگین هر هزینه: {average:,}\n"
    text += f"🔝 بیشترین هزینه: {maximum:,}\n\n"
    
    text += "━━━━━━━━━━━━\n"
    text += "📊 بر اساس دسته‌بندی\n\n"
    
    for category, amount, cnt in category_rows_sorted:
        text += f"{category}\n💰 {amount:,} تومان ({cnt} مورد)\n\n"
    
    if daily_rows:
        text += "━━━━━━━━━━━━\n"
        text += "📅 روند روزانه\n\n"
        # مرتب‌سازی روزها (جدیدترین اول)
        daily_rows_sorted = sorted(daily_rows, key=lambda x: x[0], reverse=True)
        for date_text, amount, _ in daily_rows_sorted:
            date_jalali = to_jalali(date_text)
            text += f"{date_jalali}: {amount:,} تومان\n"
    
    context.user_data.clear()
    
    # دکمه بازگشت
    buttons = [[InlineKeyboardButton("🔙 بازگشت به منو", callback_data="back_menu")]]
    
    if from_callback and update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )

async def edit_delete_menu(update, context):
    """نمایش هزینه‌ها با صفحه‌بندی برای حذف/ویرایش"""
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        return
    
    # دریافت صفحه از context (پیش‌فرض ۰)
    page = context.user_data.get("edit_page", 0)
    limit = 5  # تعداد آیتم در هر صفحه
    
    # دریافت هزینه‌ها با صفحه‌بندی
    offset = page * limit
    response = (
        supabase.table("expenses")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    
    rows = [(row["id"], row["amount"], row["description"], row["category"], row["created_at"]) for row in response.data]
    
    # بررسی وجود صفحه بعدی
    next_check = (
        supabase.table("expenses")
        .select("id")
        .eq("user_id", user_id)
        .range(offset + limit, offset + limit)
        .execute()
    )
    has_next = len(next_check.data) > 0
    
    if not rows and page == 0:
        await update.message.reply_text("📋 هنوز هزینه‌ای ثبت نشده.", reply_markup=main_keyboard())
        return
    
    if not rows:
        await update.message.reply_text("📋 صفحه خالی است.", reply_markup=main_keyboard())
        return
    
    # ساخت دکمه‌ها
    buttons = []
    for expense_id, amount, description, category, created_at in rows:
        date_part = created_at[:10] if len(created_at) >= 10 else ""
        buttons.append([
            InlineKeyboardButton(
                f"✏️ #{expense_id} | {amount:,} تومان",
                callback_data=f"edit:{expense_id}"
            ),
            InlineKeyboardButton(
                f"🗑️ حذف",
                callback_data=f"delete:{expense_id}"
            ),
        ])
    
    # دکمه‌های صفحه‌بندی
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"edit_page:{page-1}"))
    
    # شماره صفحه
    nav_buttons.append(InlineKeyboardButton(f"📄 {page + 1}", callback_data="ignore"))
    
    if has_next:
        nav_buttons.append(InlineKeyboardButton("➡️ بعدی", callback_data=f"edit_page:{page+1}"))
    
    buttons.append(nav_buttons)
    buttons.append([InlineKeyboardButton("🔙 بازگشت به منو", callback_data="back_menu")])
    
    # ذخیره صفحه فعلی در context
    context.user_data["edit_page"] = page
    
    await update.message.reply_text(
        f"🗑️ حذف / ✏️ ویرایش\n"
        f"📄 صفحه {page + 1}\n"
        f"📋 {len(rows)} هزینه در این صفحه\n\n"
        "هزینه موردنظر را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def delete_callback(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not is_allowed(user_id):
        return
    expense_id = int(query.data.split(":")[1])
    expense = get_expense(user_id, expense_id)
    if not expense:
        await query.edit_message_text("❌ هزینه پیدا نشد.")
        return
    _, amount, description, category, _ = expense
    buttons = [
        [
            InlineKeyboardButton("✅ بله، حذف کن", callback_data=f"confirm_delete:{expense_id}"),
            InlineKeyboardButton("❌ انصراف", callback_data="cancel_delete"),
        ]
    ]
    await query.edit_message_text(
        f"⚠️ حذف این هزینه؟\n\n{category}\n💰 {amount:,} تومان\n📝 {description}",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def confirm_delete_callback(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not is_allowed(user_id):
        return
    expense_id = int(query.data.split(":")[1])
    deleted = delete_expense(user_id, expense_id)
    if deleted:
        await query.edit_message_text(f"✅ هزینه #{expense_id} حذف شد.")
    else:
        await query.edit_message_text("❌ هزینه پیدا نشد.")

async def cancel_delete_callback(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ حذف لغو شد.")

async def edit_callback(update, context):
    """ویرایش هزینه انتخاب شده"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not is_allowed(user_id):
        return
    
    expense_id = int(query.data.split(":")[1])
    expense = get_expense(user_id, expense_id)
    if not expense:
        await query.edit_message_text("❌ هزینه پیدا نشد.")
        return
    
    _, amount, description, category, _ = expense
    context.user_data.clear()
    context.user_data["editing_expense"] = expense_id
    context.user_data["editing_category"] = category
    context.user_data["waiting_for_edit"] = True
    
    await query.edit_message_text(
        f"✏️ ویرایش هزینه #{expense_id}\n\n"
        f"{category}\n"
        f"💰 مبلغ فعلی: {amount:,} تومان\n"
        f"📝 {description}\n\n"
        "مبلغ و توضیح جدید را بفرست.\n\n"
        "مثال:\n95000 ناهار رستوران"
    )
    await context.bot.send_message(
        chat_id=user_id,
        text="🔙 بازگشت",
        reply_markup=back_keyboard()
    )

async def edit_page_callback(update, context):
    """تغییر صفحه در منوی حذف/ویرایش"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if not is_allowed(user_id):
        return
    
    # دریافت صفحه جدید
    page = int(query.data.split(":")[1])
    context.user_data["edit_page"] = page
    
    # شبیه‌سازی منوی حذف/ویرایش
    limit = 5
    offset = page * limit
    
    response = (
        supabase.table("expenses")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    
    rows = [(row["id"], row["amount"], row["description"], row["category"], row["created_at"]) for row in response.data]
    
    # بررسی وجود صفحه بعدی
    next_check = (
        supabase.table("expenses")
        .select("id")
        .eq("user_id", user_id)
        .range(offset + limit, offset + limit)
        .execute()
    )
    has_next = len(next_check.data) > 0
    
    if not rows:
        await query.edit_message_text("📋 صفحه خالی است.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="back_menu")]]))
        return
    
    # ساخت دکمه‌ها
    buttons = []
    for expense_id, amount, description, category, created_at in rows:
        buttons.append([
            InlineKeyboardButton(
                f"✏️ #{expense_id} | {amount:,} تومان",
                callback_data=f"edit:{expense_id}"
            ),
            InlineKeyboardButton(
                f"🗑️ حذف",
                callback_data=f"delete:{expense_id}"
            ),
        ])
    
    # دکمه‌های صفحه‌بندی
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"edit_page:{page-1}"))
    
    nav_buttons.append(InlineKeyboardButton(f"📄 {page + 1}", callback_data="ignore"))
    
    if has_next:
        nav_buttons.append(InlineKeyboardButton("➡️ بعدی", callback_data=f"edit_page:{page+1}"))
    
    buttons.append(nav_buttons)
    buttons.append([InlineKeyboardButton("🔙 بازگشت به منو", callback_data="back_menu")])
    
    await query.edit_message_text(
        f"🗑️ حذف / ✏️ ویرایش\n"
        f"📄 صفحه {page + 1}\n"
        f"📋 {len(rows)} هزینه در این صفحه\n\n"
        "هزینه موردنظر را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def settings(update, context):
    buttons = [
        [InlineKeyboardButton("🏷️ دسته‌بندی‌ها", callback_data="manage_categories")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_menu")],
    ]
    await update.message.reply_text("⚙️ تنظیمات", reply_markup=InlineKeyboardMarkup(buttons))

async def manage_categories(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not is_allowed(user_id):
        return
    context.user_data.clear()
    categories = get_categories()
    text = "🏷️ مدیریت دسته‌بندی‌ها\n\n"
    for category_id, name in categories:
        text += f"• {name}\n"
    buttons = [
        [InlineKeyboardButton("➕ افزودن دسته", callback_data="category_add")],
        [InlineKeyboardButton("✏️ تغییر نام", callback_data="category_rename"), InlineKeyboardButton("🗑️ حذف", callback_data="category_delete")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="settings_menu")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))

async def ignore_callback(update, context):
    """دکمه‌های غیرفعال (شماره صفحه)"""
    query = update.callback_query
    await query.answer("📄 این دکمه فقط نمایشی است")

async def category_add_callback(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data["waiting_category_add"] = True
    await query.edit_message_text("➕ افزودن دسته\n\nنام دسته جدید را بفرست.\n\nمثال:\n☕ کافه\n\n🔙 برای لغو، بازگشت را بزن.")
    await context.bot.send_message(chat_id=query.from_user.id, text="🔙 بازگشت", reply_markup=back_keyboard())

async def category_rename_callback(update, context):
    query = update.callback_query
    await query.answer()
    categories = get_categories()
    buttons = []
    for category_id, name in categories:
        buttons.append([InlineKeyboardButton(name, callback_data=f"rename_select:{category_id}")])
    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="manage_categories")])
    await query.edit_message_text("✏️ کدام دسته را می‌خواهی تغییر نام بدهی؟", reply_markup=InlineKeyboardMarkup(buttons))

async def rename_select_callback(update, context):
    query = update.callback_query
    await query.answer()
    category_id = int(query.data.split(":")[1])
    context.user_data.clear()
    context.user_data["waiting_category_rename"] = True
    context.user_data["rename_category_id"] = category_id
    await query.edit_message_text("✏️ نام جدید دسته را بفرست.\n\nمثال:\n☕ کافه")
    await context.bot.send_message(chat_id=query.from_user.id, text="🔙 بازگشت", reply_markup=back_keyboard())

async def category_delete_callback(update, context):
    query = update.callback_query
    await query.answer()
    categories = get_categories()
    buttons = []
    for category_id, name in categories:
        if name == "📦 سایر":
            continue
        buttons.append([InlineKeyboardButton(f"🗑️ {name}", callback_data=f"delete_category:{category_id}")])
    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="manage_categories")])
    await query.edit_message_text(
        "🗑️ کدام دسته را می‌خواهی حذف کنی؟\n\n⚠️ هزینه‌های آن دسته به «📦 سایر» منتقل می‌شوند.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def delete_category_callback(update, context):
    query = update.callback_query
    await query.answer()
    category_id = int(query.data.split(":")[1])
    categories = get_categories()
    category_name = None
    for cid, name in categories:
        if cid == category_id:
            category_name = name
            break
    if not category_name:
        await query.edit_message_text("❌ دسته پیدا نشد.")
        return
    buttons = [
        [
            InlineKeyboardButton("✅ بله، حذف کن", callback_data=f"confirm_category_delete:{category_id}"),
            InlineKeyboardButton("❌ انصراف", callback_data="manage_categories"),
        ]
    ]
    await query.edit_message_text(
        f"⚠️ حذف دسته «{category_name}»؟\n\nهزینه‌های قبلی این دسته به «📦 سایر» منتقل می‌شوند.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def confirm_category_delete_callback(update, context):
    query = update.callback_query
    await query.answer()
    category_id = int(query.data.split(":")[1])
    deleted = delete_category(category_id)
    if deleted:
        await query.edit_message_text("✅ دسته حذف شد.\nهزینه‌های قبلی آن به «📦 سایر» منتقل شدند.")
    else:
        await query.edit_message_text("❌ این دسته قابل حذف نیست.")

async def back_callback(update, context):
    query = update.callback_query
    await query.answer()

    context.user_data.clear()

    await query.edit_message_text(
        "🏠 منوی اصلی"
    )

    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="از منوی پایین انتخاب کن 👇",
        reply_markup=main_keyboard()
    )

async def settings_menu_callback(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not is_allowed(user_id):
        return
    buttons = [
        [InlineKeyboardButton("🏷️ دسته‌بندی‌ها", callback_data="manage_categories")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_menu")],
    ]
    await query.edit_message_text("⚙️ تنظیمات", reply_markup=InlineKeyboardMarkup(buttons))

# ==========================================
# توابع مدیریت هزینه‌های سریع
# ==========================================
async def quick_add_callback(update, context):
    """افزودن هزینه سریع جدید - روش خیلی ساده"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not is_allowed(user_id):
        return

    # دریافت دسته‌بندی‌ها
    categories = get_categories()
    
    # دکمه‌های دسته‌بندی
    category_buttons = []
    row = []
    for cat_id, cat_name in categories:
        row.append(InlineKeyboardButton(cat_name, callback_data=f"quick_add_cat_{cat_id}"))
        if len(row) == 2:
            category_buttons.append(row)
            row = []
    if row:
        category_buttons.append(row)

    category_buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="quick_manage")])

    context.user_data["quick_add_step"] = "category"

    await query.edit_message_text(
        "➕ **افزودن هزینه سریع جدید**\n\n"
        "۱. دسته‌بندی رو انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(category_buttons)
    )

async def quick_add_category_callback(update, context):
    """انتخاب دسته‌بندی برای هزینه سریع"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not is_allowed(user_id):
        return

    # دریافت دسته‌بندی انتخاب شده
    cat_id = int(query.data.replace("quick_add_cat_", ""))
    categories = get_categories()
    
    category_name = None
    for cid, name in categories:
        if cid == cat_id:
            category_name = name
            break

    if not category_name:
        await query.edit_message_text("❌ دسته‌بندی پیدا نشد.")
        return

    context.user_data["quick_add_category"] = category_name
    context.user_data["waiting_quick_add"] = True

    await query.edit_message_text(
        f"➕ **افزودن هزینه سریع**\n\n"
        f"📂 دسته‌بندی: {category_name}\n\n"
        "۲. مبلغ رو وارد کن (فقط عدد):\n\n"
        "مثال: `85000`\n\n"
        "یا با توضیح: `85000 ناهار`",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 بازگشت", callback_data="quick_manage")]
        ])
    )
async def quick_delete_confirm_callback(update, context):
    """تأیید حذف هزینه سریع"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not is_allowed(user_id):
        return

    # دریافت id هزینه سریع
    quick_id = int(query.data.replace("quick_delete_confirm_", ""))

    # حذف از دیتابیس
    deleted = delete_quick_expense(user_id, quick_id)

    if deleted:
        await query.edit_message_text(
            "✅ **هزینه سریع با موفقیت حذف شد!**",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت به مدیریت", callback_data="quick_manage")]
            ])
        )
    else:
        await query.edit_message_text(
            "❌ خطا در حذف هزینه سریع.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت", callback_data="quick_manage")]
            ])
        )

async def quick_edit_select_callback(update, context):
    """انتخاب هزینه برای ویرایش"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not is_allowed(user_id):
        return

    # دریافت id هزینه سریع
    quick_id = int(query.data.replace("quick_edit_select_", ""))

    # دریافت از دیتابیس
    response = supabase.table("quick_expenses").select("*").eq("id", quick_id).eq("user_id", user_id).execute()
    if not response.data:
        await query.edit_message_text("❌ هزینه سریع پیدا نشد.")
        return

    item = response.data[0]
    context.user_data["quick_edit_id"] = quick_id
    context.user_data["quick_edit_name"] = item["name"]
    context.user_data["waiting_quick_edit"] = True

    await query.edit_message_text(
        f"✏️ **ویرایش {item['name']}**\n\n"
        "مبلغ جدید رو وارد کن:\n\n"
        f"مبلغ فعلی: {item['amount']:,} تومان\n"
        f"دسته‌بندی: {item['category']}\n\n"
        "مثال: `75000`\n\n"
        "برای تغییر نام و دسته‌بندی، از فرمت زیر استفاده کن:\n"
        "`نام جدید|مبلغ جدید|دسته‌بندی جدید`\n\n"
        "مثال:\n"
        "`صبحانه|45000|🍔 غذا`",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 بازگشت", callback_data="quick_manage")]
        ])
    )

async def quick_edit_callback(update, context):
    """ویرایش هزینه سریع"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not is_allowed(user_id):
        return

    # دریافت هزینه‌های سریع از دیتابیس
    quick_items = get_quick_expenses(user_id)

    if not quick_items:
        await query.edit_message_text(
            "📋 **هیچ هزینه سریعی برای ویرایش وجود ندارد.**\n\n"
            "ابتدا از طریق «➕ افزودن هزینه سریع» یک هزینه اضافه کن.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت", callback_data="quick_manage")]
            ])
        )
        return

    buttons = []
    for item in quick_items:
        buttons.append([
            InlineKeyboardButton(
                f"✏️ {item['name']} ({item['amount']:,})",
                callback_data=f"quick_edit_select_{item['id']}"
            )
        ])

    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="quick_manage")])

    await query.edit_message_text(
        "✏️ **ویرایش هزینه سریع**\n\n"
        "هزینه‌ای که میخوای ویرایش کنی رو انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def quick_delete_callback(update, context):
    """حذف هزینه سریع"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not is_allowed(user_id):
        return

    # دریافت هزینه‌های سریع از دیتابیس
    quick_items = get_quick_expenses(user_id)

    if not quick_items:
        await query.edit_message_text(
            "📋 **هیچ هزینه سریعی برای حذف وجود ندارد.**",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت", callback_data="quick_manage")]
            ])
        )
        return

    buttons = []
    for item in quick_items:
        buttons.append([
            InlineKeyboardButton(
                f"🗑️ {item['name']} ({item['amount']:,})",
                callback_data=f"quick_delete_confirm_{item['id']}"
            )
        ])

    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="quick_manage")])

    await query.edit_message_text(
        "🗑️ **حذف هزینه سریع**\n\n"
        "هزینه‌ای که میخوای حذف کنی رو انتخاب کن:\n\n"
        "⚠️ فقط از لیست هزینه‌های سریع حذف میشه.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# ==========================================
# تابع خروجی اکسل
# ==========================================

async def export_excel(update, context):
    user_id = update.effective_user.id

    if not is_allowed(user_id):
        await update.message.reply_text(
            "⛔ شما اجازه استفاده از این بخش را ندارید."
        )
        return

    month = datetime.now().strftime("%Y-%m")

    try:
        # ==========================================
        # محاسبه بازه ماه
        # ==========================================
        start_date = f"{month}-01 00:00:00"

        current_month = datetime.now()

        if current_month.month == 12:
            next_month = current_month.replace(
                year=current_month.year + 1,
                month=1,
                day=1
            )
        else:
            next_month = current_month.replace(
                month=current_month.month + 1,
                day=1
            )

        end_date = next_month.strftime("%Y-%m-%d 00:00:00")

        logger.info(
            f"Export Excel | user={user_id} | "
            f"start={start_date} | end={end_date}"
        )

        # ==========================================
        # دریافت هزینه‌ها از Supabase
        # ==========================================
        response = (
            supabase
            .table("expenses")
            .select("*")
            .eq("user_id", user_id)
            .gte("created_at", start_date)
            .lt("created_at", end_date)
            .order("created_at", desc=True)
            .execute()
        )

        rows = response.data or []

        if not rows:
            await update.message.reply_text(
                "📊 این ماه هنوز هزینه‌ای ثبت نشده.",
                reply_markup=main_keyboard()
            )
            return

        # ==========================================
        # Importهای مربوط به Excel
        # ==========================================
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        from openpyxl.chart import PieChart, BarChart, Reference
        from openpyxl.worksheet.table import Table, TableStyleInfo

        # ==========================================
        # ساخت Workbook
        # ==========================================
        wb = Workbook()

        # ==========================================
        # شیت اول: هزینه‌ها
        # ==========================================
        ws = wb.active
        ws.title = "هزینه‌ها"

        # ==========================================
        # عنوان فایل
        # ==========================================
                # تبدیل ماه به شمسی برای عنوان
        month_parts = month.split('-')
        year, month_num = int(month_parts[0]), int(month_parts[1])
        gregorian_date = datetime(year, month_num, 1)
        jalali_date = jdatetime.date.fromgregorian(date=gregorian_date)
        jalali_month = f"{jalali_date.year:04d}-{jalali_date.month:02d}"
        
        ws.merge_cells("A1:F1")
        ws["A1"] = f"💰 گزارش هزینه‌های ماه {jalali_month}"

        ws["A1"].font = Font(
            bold=True,
            size=18
        )

        ws["A1"].alignment = Alignment(
            horizontal="center",
            vertical="center"
        )

        ws.row_dimensions[1].height = 35

        # ==========================================
        # اطلاعات خلاصه
        # ==========================================
        total = sum(int(row.get("amount", 0)) for row in rows)
        count = len(rows)

        ws["A2"] = "تعداد هزینه‌ها"
        ws["B2"] = count

        ws["D2"] = "مجموع هزینه"
        ws["E2"] = total

        ws["A2"].font = Font(bold=True)
        ws["D2"].font = Font(bold=True)

        ws["B2"].font = Font(bold=True)
        ws["E2"].font = Font(bold=True)

        # فرمت مبلغ
        ws["E2"].number_format = '#,##0" تومان"'

        # ==========================================
        # هدر جدول
        # ==========================================
        headers = [
            "ردیف",
            "دسته‌بندی",
            "مبلغ (تومان)",
            "توضیحات",
            "تاریخ",
            "ساعت"
        ]

        header_row = 4

        for col, header in enumerate(headers, 1):
            cell = ws.cell(
                row=header_row,
                column=col,
                value=header
            )

            cell.font = Font(
                bold=True,
                size=11
            )

            cell.alignment = Alignment(
                horizontal="center",
                vertical="center"
            )

        # ==========================================
        # وارد کردن اطلاعات
        # ==========================================
        for i, row in enumerate(rows, start=1):

            created_at = str(row.get("created_at", ""))

            # استفاده از تابع تبدیل به شمسی
            date_part = to_jalali(created_at)

            time_part = (
                created_at[11:16]
                if len(created_at) >= 16
                else ""
            )

            amount = int(row.get("amount", 0))

            excel_row = header_row + i

            ws.cell(excel_row, 1, i)
            ws.cell(excel_row, 2, row.get("category", ""))
            ws.cell(excel_row, 3, amount)
            ws.cell(excel_row, 4, row.get("description", ""))
            ws.cell(excel_row, 5, date_part)
            ws.cell(excel_row, 6, time_part)

            # فرمت مبلغ
            ws.cell(
                excel_row,
                3
            ).number_format = '#,##0" تومان"'

            # تراز وسط برای اطلاعات عددی
            ws.cell(excel_row, 1).alignment = Alignment(
                horizontal="center"
            )

            ws.cell(excel_row, 3).alignment = Alignment(
                horizontal="center"
            )

            ws.cell(excel_row, 5).alignment = Alignment(
                horizontal="center"
            )

            ws.cell(excel_row, 6).alignment = Alignment(
                horizontal="center"
            )

            # راست‌چین برای متن
            ws.cell(excel_row, 2).alignment = Alignment(
                horizontal="right"
            )

            ws.cell(excel_row, 4).alignment = Alignment(
                horizontal="right"
            )

        # ==========================================
        # ردیف جمع کل
        # ==========================================
        total_row = header_row + len(rows) + 2

        ws.cell(total_row, 2, "💰 جمع کل")
        ws.cell(total_row, 3, total)

        ws.cell(total_row, 2).font = Font(
            bold=True,
            size=12
        )

        ws.cell(total_row, 3).font = Font(
            bold=True,
            size=12
        )

        ws.cell(
            total_row,
            3
        ).number_format = '#,##0" تومان"'

        # ==========================================
        # جدول Excel
        # ==========================================
        table_end_row = header_row + len(rows)

        table_ref = f"A{header_row}:F{table_end_row}"

        tab = Table(
            displayName="ExpensesTable",
            ref=table_ref
        )

        style = TableStyleInfo(
            name="TableStyleMedium2",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False
        )

        tab.tableStyleInfo = style
        ws.add_table(tab)

        # ==========================================
        # فریز کردن هدر
        # ==========================================
        ws.freeze_panes = "A5"

        # ==========================================
        # فیلتر
        # ==========================================
        ws.auto_filter.ref = table_ref

        # ==========================================
        # عرض ستون‌ها
        # ==========================================
        ws.column_dimensions["A"].width = 10
        ws.column_dimensions["B"].width = 22
        ws.column_dimensions["C"].width = 20
        ws.column_dimensions["D"].width = 45
        ws.column_dimensions["E"].width = 16
        ws.column_dimensions["F"].width = 12

        # ==========================================
        # راست‌چین و Wrap Text
        # ==========================================
        for row in ws.iter_rows():
            for cell in row:
                cell.alignment = Alignment(
                    horizontal=cell.alignment.horizontal or "right",
                    vertical="center",
                    wrap_text=True
                )

        # ==========================================
        # شیت دوم: گزارش ماه
        # ==========================================
        report_ws = wb.create_sheet("گزارش ماه")

        # استفاده از jalali_month که قبلاً محاسبه شد
        report_ws.merge_cells("A1:D1")
        report_ws["A1"] = f"📊 خلاصه هزینه‌های ماه {jalali_month}"

        report_ws["A1"].font = Font(
            bold=True,
            size=18
        )

        report_ws["A1"].alignment = Alignment(
            horizontal="center",
            vertical="center"
        )

        report_ws.row_dimensions[1].height = 35

        # ==========================================
        # اطلاعات کلی
        # ==========================================
        report_ws["A3"] = "مجموع هزینه‌ها"
        report_ws["B3"] = total

        report_ws["A4"] = "تعداد هزینه‌ها"
        report_ws["B4"] = count

        average = total // count if count else 0

        report_ws["A5"] = "میانگین هر هزینه"
        report_ws["B5"] = average

        for cell in ["A3", "A4", "A5"]:
            report_ws[cell].font = Font(bold=True)

        for cell in ["B3", "B5"]:
            report_ws[cell].number_format = '#,##0" تومان"'

        # ==========================================
        # محاسبه دسته‌بندی‌ها
        # ==========================================
        categories = {}

        for row in rows:

            category = row.get(
                "category",
                "📦 سایر"
            )

            amount = int(
                row.get("amount", 0)
            )

            if category not in categories:
                categories[category] = {
                    "total": 0,
                    "count": 0
                }

            categories[category]["total"] += amount
            categories[category]["count"] += 1

        # مرتب‌سازی از بیشترین هزینه
        sorted_categories = sorted(
            categories.items(),
            key=lambda x: x[1]["total"],
            reverse=True
        )

        # ==========================================
        # جدول دسته‌بندی‌ها
        # ==========================================
        category_header_row = 8

        category_headers = [
            "دسته‌بندی",
            "مجموع (تومان)",
            "تعداد",
            "درصد از کل"
        ]

        for col, header in enumerate(
            category_headers,
            start=1
        ):

            cell = report_ws.cell(
                category_header_row,
                col,
                header
            )

            cell.font = Font(
                bold=True
            )

            cell.alignment = Alignment(
                horizontal="center"
            )

        # ==========================================
        # اطلاعات دسته‌ها
        # ==========================================
        for i, (category, data) in enumerate(
            sorted_categories,
            start=1
        ):

            row_num = category_header_row + i

            category_total = data["total"]
            category_count = data["count"]

            percentage = (
                category_total / total
                if total > 0
                else 0
            )

            report_ws.cell(
                row_num,
                1,
                category
            )

            report_ws.cell(
                row_num,
                2,
                category_total
            )

            report_ws.cell(
                row_num,
                3,
                category_count
            )

            report_ws.cell(
                row_num,
                4,
                percentage
            )

            report_ws.cell(
                row_num,
                2
            ).number_format = '#,##0" تومان"'

            report_ws.cell(
                row_num,
                4
            ).number_format = "0.00%"

        # ==========================================
        # جدول دسته‌بندی
        # ==========================================
        category_end_row = (
            category_header_row +
            len(sorted_categories)
        )

        category_table = Table(
            displayName="CategoryTable",
            ref=f"A{category_header_row}:D{category_end_row}"
        )

        category_style = TableStyleInfo(
            name="TableStyleMedium4",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False
        )

        category_table.tableStyleInfo = category_style

        report_ws.add_table(category_table)

        # ==========================================
        # نمودار دایره‌ای
        # ==========================================
        if len(sorted_categories) > 0:

            pie = PieChart()

            labels = Reference(
                report_ws,
                min_col=1,
                min_row=category_header_row + 1,
                max_row=category_end_row
            )

            data = Reference(
                report_ws,
                min_col=2,
                min_row=category_header_row,
                max_row=category_end_row
            )

            pie.add_data(
                data,
                titles_from_data=True
            )

            pie.set_categories(labels)

            pie.title = "سهم هزینه‌ها بر اساس دسته‌بندی"

            pie.height = 8
            pie.width = 12

            report_ws.add_chart(
                pie,
                "F3"
            )

        # ==========================================
        # نمودار میله‌ای
        # ==========================================
        if len(sorted_categories) > 0:

            bar = BarChart()

            data = Reference(
                report_ws,
                min_col=2,
                min_row=category_header_row,
                max_row=category_end_row
            )

            labels = Reference(
                report_ws,
                min_col=1,
                min_row=category_header_row + 1,
                max_row=category_end_row
            )

            bar.add_data(
                data,
                titles_from_data=True
            )

            bar.set_categories(labels)

            bar.title = "مقایسه هزینه دسته‌بندی‌ها"
            bar.y_axis.title = "مبلغ"
            bar.x_axis.title = "دسته‌بندی"

            bar.height = 8
            bar.width = 14

            report_ws.add_chart(
                bar,
                "F20"
            )

        # ==========================================
        # عرض ستون‌های گزارش
        # ==========================================
        report_ws.column_dimensions["A"].width = 25
        report_ws.column_dimensions["B"].width = 22
        report_ws.column_dimensions["C"].width = 12
        report_ws.column_dimensions["D"].width = 18

        # ==========================================
        # Freeze
        # ==========================================
        report_ws.freeze_panes = "A9"

        # ==========================================
        # ذخیره فایل در حافظه
        # ==========================================
        output = BytesIO()

        wb.save(output)

        output.seek(0)

        filename = f"گزارش_هزینه_{jalali_month}.xlsx"
        
        # ==========================================
        # ارسال فایل
        # ==========================================
        await update.message.reply_document(
            document=output,
            filename=filename,
            caption=(
                f"📊 گزارش کامل هزینه‌های ماه {jalali_month}\n\n"
                f"🧾 تعداد: {count} مورد\n"
                f"💰 مجموع: {total:,} تومان\n"
                f"📊 میانگین: {average:,} تومان"
            ),
            reply_markup=main_keyboard()
        )

        logger.info(
            f"Export Excel successful | "
            f"user={user_id} | "
            f"rows={count} | "
            f"total={total}"
        )

    except Exception as e:

        logger.exception(
            f"خطا در خروجی اکسل برای user={user_id}"
        )

        await update.message.reply_text(
            f"❌ خطا در ایجاد فایل اکسل:\n\n{str(e)}",
            reply_markup=main_keyboard()
        )
# ==========================================
# هندلر اصلی پیام‌ها
# ==========================================
async def reports_menu(update, context):
    """منوی گزارش‌ها"""
    user_id = update.effective_user.id

    if not is_allowed(user_id):
        return

    buttons = [
        [
            InlineKeyboardButton("📊 گزارش امروز", callback_data="report_today"),
            InlineKeyboardButton("📅 گزارش تاریخ", callback_data="report_date"),
        ],
        [
            InlineKeyboardButton("📊 گزارش ماه", callback_data="report_month"),
            InlineKeyboardButton("📈 گزارش پیشرفته", callback_data="report_advanced"),
        ],
        [
            InlineKeyboardButton("📋 هزینه‌های اخیر", callback_data="report_recent"),
            InlineKeyboardButton("📊 آمار کلی", callback_data="report_stats"),
        ],
        [
            InlineKeyboardButton("🔙 بازگشت", callback_data="back_menu")
        ]
    ]

    await update.message.reply_text(
        "📊 گزارش‌ها\n\n"
        "نوع گزارش موردنظر را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def reports_callback(update, context):
    """مدیریت دکمه‌های منوی گزارش‌ها"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if not is_allowed(user_id):
        return

    action = query.data

    # ==========================================
    # گزارش امروز
    # ==========================================
    if action == "report_today":

        context.user_data["report_page"] = 0

        today = datetime.now(TEHRAN_TZ).strftime("%Y-%m-%d")

        rows = get_day_expenses(user_id, today)

        if not rows:
            await query.edit_message_text(
                "📊 امروز هنوز هزینه‌ای ثبت نشده."
            )
            return

        limit = 5
        page = 0

        total_items = len(rows)
        total_pages = (total_items + limit - 1) // limit

        page_rows = rows[:limit]

        total = sum(row[1] for row in rows)

        today_jalali = to_jalali(today)

        text = f"📊 گزارش امروز\n"
        text += f"📅 {today_jalali}\n"
        text += f"📄 صفحه {page + 1} از {total_pages}\n"
        text += f"💰 مجموع کل: {total:,} تومان\n"
        text += "━━━━━━━━━━━━\n\n"

        for expense_id, amount, description, category, created_at in page_rows:

            time = created_at[11:16] if len(created_at) >= 16 else ""

            text += (
                f"#{expense_id} {category}\n"
                f"💰 {amount:,} تومان\n"
                f"📝 {description}\n"
                f"🕐 {time}\n\n"
            )

        buttons = []

        nav_buttons = []

        nav_buttons.append(
            InlineKeyboardButton(
                f"📄 {page + 1}/{total_pages}",
                callback_data="ignore"
            )
        )

        if total_pages > 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    "➡️ بعدی",
                    callback_data="report_page:1"
                )
            )

        buttons.append(nav_buttons)

        buttons.append([
            InlineKeyboardButton(
                "🔙 بازگشت به گزارش‌ها",
                callback_data="reports_menu"
            )
        ])

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )

        return

    # ==========================================
    # گزارش تاریخ
    # ==========================================
    if action == "report_date":

        context.user_data.clear()

        context.user_data["waiting_report_date"] = True
        context.user_data["date_report_page"] = 0

        await query.edit_message_text(
            "📅 گزارش تاریخ\n\n"
            "تاریخ موردنظر را وارد کن:\n\n"
            "📅 شمسی:\n"
            "1405-05-23\n\n"
            "📅 میلادی:\n"
            "2026-08-14\n\n"
            "فرمت‌های قابل قبول:\n"
            "1405/05/23\n"
            "1405.05.23"
        )

        await context.bot.send_message(
            chat_id=user_id,
            text="🔙 برای بازگشت، دکمه زیر را بزن.",
            reply_markup=back_keyboard()
        )

        return

    # ==========================================
    # گزارش ماه
    # ==========================================
    if action == "report_month":

        context.user_data.clear()

        today = datetime.now(TEHRAN_TZ)

        month = today.strftime("%Y-%m")

        rows = get_month_expenses(user_id, month)

        if not rows:

            await query.edit_message_text(
                "📅 این ماه هنوز هزینه‌ای ثبت نشده."
            )

            return

        categories = {}

        for row in rows:

            category = row["category"]

            if category not in categories:
                categories[category] = {
                    "total": 0,
                    "count": 0
                }

            categories[category]["total"] += row["amount"]
            categories[category]["count"] += 1

        total = sum(
            data["total"]
            for data in categories.values()
        )

        count = sum(
            data["count"]
            for data in categories.values()
        )

        jalali_month = to_jalali(
            f"{month}-01"
        )[:7]

        text = (
            f"📅 گزارش ماه جاری\n"
            f"📆 {jalali_month}\n\n"
        )

        for category, data in categories.items():

            text += (
                f"{category}\n"
                f"💰 {data['total']:,} تومان "
                f"({data['count']} مورد)\n\n"
            )

        text += "━━━━━━━━━━━━\n"
        text += f"🧾 تعداد هزینه‌ها: {count}\n"
        text += f"💵 مجموع: {total:,} تومان"

        buttons = [[
            InlineKeyboardButton(
                "🔙 بازگشت به گزارش‌ها",
                callback_data="reports_menu"
            )
        ]]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )

        return

    # ==========================================
    # هزینه‌های اخیر
    # ==========================================
    if action == "report_recent":

        context.user_data.clear()

        rows = get_recent_expenses(
            user_id,
            limit=10
        )

        if not rows:

            await query.edit_message_text(
                "📋 هنوز هیچ هزینه‌ای ثبت نشده."
            )

            return

        text = "📋 آخرین هزینه‌ها\n\n"

        for expense_id, amount, description, category, created_at in rows:

            date_part = to_jalali(created_at)

            time = (
                created_at[11:16]
                if len(created_at) >= 16
                else ""
            )

            text += (
                f"#{expense_id} {category}\n"
                f"💰 {amount:,} تومان\n"
                f"📝 {description}\n"
                f"📅 {date_part} | 🕐 {time}\n\n"
            )

        buttons = [[
            InlineKeyboardButton(
                "🔙 بازگشت به گزارش‌ها",
                callback_data="reports_menu"
            )
        ]]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )

        return

    # ==========================================
    # آمار کلی
    # ==========================================
    if action == "report_stats":

        context.user_data.clear()

        response = (
            supabase
            .table("expenses")
            .select("*")
            .eq("user_id", user_id)
            .execute()
        )

        rows = response.data or []

        if not rows:

            await query.edit_message_text(
                "📊 هنوز هیچ هزینه‌ای ثبت نشده."
            )

            return

        total = sum(
            int(row["amount"])
            for row in rows
        )

        count = len(rows)

        average = (
            total // count
            if count
            else 0
        )

        maximum = max(
            int(row["amount"])
            for row in rows
        )

        minimum = min(
            int(row["amount"])
            for row in rows
        )

        # امروز
        today = datetime.now(
            TEHRAN_TZ
        ).strftime("%Y-%m-%d")

        today_rows = [
            row
            for row in rows
            if str(row["created_at"]).startswith(today)
        ]

        today_count = len(today_rows)

        today_total = sum(
            int(row["amount"])
            for row in today_rows
        )

        # ماه جاری
        month = today[:7]

        month_rows = [
            row
            for row in rows
            if str(row["created_at"]).startswith(month)
        ]

        month_count = len(month_rows)

        month_total = sum(
            int(row["amount"])
            for row in month_rows
        )

        today_jalali = to_jalali(today)

        text = "📊 آمار کلی هزینه‌ها\n\n"

        text += f"💰 مجموع کل: {total:,} تومان\n"
        text += f"🧾 تعداد کل: {count} هزینه\n"
        text += f"📊 میانگین هر هزینه: {average:,} تومان\n"
        text += f"🔺 بیشترین هزینه: {maximum:,} تومان\n"
        text += f"🔻 کمترین هزینه: {minimum:,} تومان\n\n"

        text += "━━━━━━━━━━━━\n"

        text += (
            f"📅 امروز ({today_jalali})\n"
            f"🧾 {today_count} هزینه - "
            f"💰 {today_total:,} تومان\n\n"
        )

        text += (
            "📅 این ماه\n"
            f"🧾 {month_count} هزینه - "
            f"💰 {month_total:,} تومان"
        )

        buttons = [[
            InlineKeyboardButton(
                "🔙 بازگشت به گزارش‌ها",
                callback_data="reports_menu"
            )
        ]]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )

        return

    # ==========================================
    # گزارش پیشرفته
    # ==========================================
    if action == "report_advanced":

        context.user_data.clear()

        context.user_data["waiting_advanced_start"] = True

        keyboard = [
            [
                InlineKeyboardButton(
                    "📅 امروز",
                    callback_data="adv_today"
                ),
                InlineKeyboardButton(
                    "📅 این هفته",
                    callback_data="adv_this_week"
                )
            ],
            [
                InlineKeyboardButton(
                    "📅 هفته گذشته",
                    callback_data="adv_week"
                ),
                InlineKeyboardButton(
                    "📅 ماه جاری",
                    callback_data="adv_month"
                )
            ],
            [
                InlineKeyboardButton(
                    "📅 سه ماه اخیر",
                    callback_data="adv_quarter"
                ),
                InlineKeyboardButton(
                    "✏️ وارد کردن دستی",
                    callback_data="adv_manual"
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 بازگشت",
                    callback_data="reports_menu"
                )
            ]
        ]

        await query.edit_message_text(
            "📈 گزارش پیشرفته\n\n"
            "یک بازه زمانی را انتخاب کن:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return

async def date_report_button(update, context):
    """شروع گزارش برای یک تاریخ دلخواه"""
    user_id = update.effective_user.id

    if not is_allowed(user_id):
        return

    context.user_data.clear()
    context.user_data["waiting_report_date"] = True
    context.user_data["date_report_page"] = 0

    await update.message.reply_text(
        "📅 گزارش تاریخ\n\n"
        "تاریخ موردنظر را وارد کن:\n\n"
        "📅 شمسی:\n"
        "1405-05-23\n\n"
        "📅 میلادی:\n"
        "2026-08-14\n\n"
        "فرمت‌های قابل قبول:\n"
        "1405/05/23\n"
        "1405.05.23",
        reply_markup=back_keyboard()
    )

async def handle_message(update, context):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ شما اجازه استفاده از این ربات را ندارید.")
        return
    
    message = update.message.text.strip()
    
    if message == "🔙 بازگشت":
        await go_back(update, context)
        return
    
    # ==========================================
    # مدیریت دسته‌بندی‌ها
    # ==========================================
    if context.user_data.get("waiting_category_add"):
        if not message:
            return
        if category_exists(message):
            await update.message.reply_text("❌ این دسته از قبل وجود دارد.", reply_markup=back_keyboard())
            return
        if add_category(message):
            context.user_data.clear()
            await update.message.reply_text(f"✅ دسته «{message}» اضافه شد.", reply_markup=main_keyboard())
        else:
            await update.message.reply_text("❌ خطا در افزودن دسته.", reply_markup=back_keyboard())
        return
    
    if context.user_data.get("waiting_category_rename"):
        category_id = context.user_data["rename_category_id"]
        if category_exists(message):
            await update.message.reply_text("❌ این نام قبلاً وجود دارد.", reply_markup=back_keyboard())
            return
        if rename_category(category_id, message):
            context.user_data.clear()
            await update.message.reply_text(f"✅ دسته به «{message}» تغییر کرد.", reply_markup=main_keyboard())
        else:
            await update.message.reply_text("❌ تغییر نام انجام نشد.", reply_markup=back_keyboard())
        return
    
    # ==========================================
    # گزارش‌ها
    # ==========================================
    if context.user_data.get("waiting_report_date"):
        date_info = get_date_info(message)

        if not date_info:
            await update.message.reply_text(
                "❌ تاریخ نامعتبر است.\n\n"
                "فرمت‌های قابل قبول:\n\n"
                "📅 شمسی:\n"
                "1405-05-23\n\n"
                "📅 میلادی:\n"
                "2026-08-14\n\n"
                "مثال:\n"
                "1405/05/23",
                reply_markup=back_keyboard()
            )
            return

        context.user_data["date_report_date"] = date_info["gregorian"]
        context.user_data["date_report_page"] = 0

        await show_date_report(
            update,
            context,
            date_info["gregorian"]
        )

        return
    
    if context.user_data.get("waiting_advanced_start"):
        date_info = get_date_info(message)

        if not date_info:
            await update.message.reply_text(
                "❌ تاریخ نامعتبر است.\n\n"
                "مثال شمسی:\n"
                "1405-05-01\n\n"
                "مثال میلادی:\n"
                "2026-08-01",
                reply_markup=back_keyboard()
            )
            return

        start_date = date_info["gregorian"]

        context.user_data.clear()
        context.user_data["waiting_advanced_end"] = True
        context.user_data["advanced_start"] = start_date

        await update.message.reply_text(
            "📅 تاریخ پایان را وارد کن:\n\n"
            "شمسی:\n"
            "1405-05-23\n\n"
            "میلادی:\n"
            "2026-08-14",
            reply_markup=back_keyboard()
        )

        return
    
    if context.user_data.get("waiting_advanced_end"):
        date_info = get_date_info(message)

        if not date_info:
            await update.message.reply_text(
                "❌ تاریخ نامعتبر است.\n\n"
                "مثال شمسی:\n"
                "1405-05-23\n\n"
                "مثال میلادی:\n"
                "2026-08-14",
                reply_markup=back_keyboard()
            )
            return

        end_date = date_info["gregorian"]
        start_date = context.user_data["advanced_start"]

        if end_date < start_date:
            await update.message.reply_text(
                "❌ تاریخ پایان نمی‌تواند قبل از تاریخ شروع باشد.",
                reply_markup=back_keyboard()
            )
            return

        await show_advanced_report(
            update,
            context,
            start_date,
            end_date
        )

        return
    
    # ==========================================
    # منوی اصلی
    # ==========================================
    if message == "📥 ثبت هزینه":
        await expense_button(update, context)
        return
    
    if message == "🧾 هزینه‌های سریع":
        await quick_expenses_menu(update, context)
        return
    
    if message == "📊 گزارش‌ها":
        await reports_menu(update, context)
        return
    
    if message == "✏️ مدیریت هزینه‌ها":
        await edit_delete_menu(update, context)
        return
    
    if message == "📤 خروجی اکسل":
        await export_excel(update, context)
        return
    
    if message == "⚙️ تنظیمات":
        await settings(update, context)
        return
    
    # ==========================================
    # ثبت هزینه با انتخاب دسته
    # ==========================================
    if context.user_data.get("waiting_for_expense"):
        categories = [name for _, name in get_categories()]
        if message in categories:
            await choose_category(update, context, message)
            return
    
    # ==========================================
    # ویرایش هزینه
    # ==========================================
    if context.user_data.get("waiting_for_edit"):
        parsed = parse_expense_text(message)
        if not parsed:
            await update.message.reply_text("❌ فرمت درست نیست.\n\nمثال:\n95000 ناهار رستوران", reply_markup=back_keyboard())
            return
        amount, description = parsed
        expense_id = context.user_data["editing_expense"]
        category = context.user_data.get("editing_category", "📦 سایر")
        updated = update_expense(user_id, expense_id, amount, description, category)
        context.user_data.clear()
        if updated:
            await update.message.reply_text(
                f"✅ هزینه #{expense_id} ویرایش شد.\n\n{category}\n💰 {amount:,} تومان\n📝 {description}",
                reply_markup=main_keyboard()
            )
        return
    

        # ==========================================
    # افزودن هزینه سریع جدید (روش خیلی ساده)
    # ==========================================
    if context.user_data.get("waiting_quick_add"):
        category = context.user_data.get("quick_add_category", "📦 سایر")
        message_text = message.strip()
        
        # بررسی فرمت: "مبلغ توضیح" یا فقط "مبلغ"
        parsed = parse_expense_text(message_text)
        
        if parsed:
            amount, name = parsed
        else:
            # فقط مبلغ وارد شده
            try:
                amount = int(message_text)
                # اگر نامی وارد نشده، از نام دسته‌بندی استفاده کن
                name = category
            except ValueError:
                await update.message.reply_text(
                    "❌ فقط عدد وارد کن!\n\n"
                    "مثال: `85000`\n"
                    "یا: `85000 ناهار`",
                    reply_markup=back_keyboard()
                )
                return

        # ذخیره در دیتابیس هزینه‌های سریع
        add_quick_expense(user_id, name, amount, category)

        context.user_data.clear()
        await update.message.reply_text(
            f"✅ **هزینه سریع جدید اضافه شد!**\n\n"
            f"📝 {name}\n"
            f"💰 {amount:,} تومان\n"
            f"📂 {category}\n\n"
            "می‌توانی از منوی هزینه‌های سریع استفاده کنی.",
            reply_markup=main_keyboard()
        )
        return

    # ==========================================
    # ویرایش هزینه سریع
    # ==========================================
    if context.user_data.get("waiting_quick_edit"):
        quick_id = context.user_data.get("quick_edit_id")
        old_name = context.user_data.get("quick_edit_name")
        
        if not quick_id:
            await update.message.reply_text("❌ خطا در ویرایش.", reply_markup=main_keyboard())
            return

        if '|' in message:
            parts = message.split('|')
            if len(parts) != 3:
                await update.message.reply_text(
                    "❌ فرمت اشتباه!\n\n"
                    "فرمت درست:\n"
                    "`نام|مبلغ|دسته‌بندی`\n\n"
                    "مثال:\n"
                    "`صبحانه|45000|🍔 غذا`",
                    reply_markup=back_keyboard()
                )
                return
            name = parts[0].strip()
            try:
                amount = int(parts[1].strip())
            except ValueError:
                await update.message.reply_text("❌ مبلغ باید عدد باشد.", reply_markup=back_keyboard())
                return
            category = parts[2].strip()
            categories = [cat for _, cat in get_categories()]
            if category not in categories:
                await update.message.reply_text(
                    f"❌ دسته‌بندی «{category}» وجود ندارد.",
                    reply_markup=back_keyboard()
                )
                return
        else:
            try:
                amount = int(message.strip())
            except ValueError:
                await update.message.reply_text("❌ مبلغ باید عدد باشد.", reply_markup=back_keyboard())
                return
            response = supabase.table("quick_expenses").select("*").eq("id", quick_id).eq("user_id", user_id).execute()
            if not response.data:
                await update.message.reply_text("❌ هزینه سریع پیدا نشد.", reply_markup=main_keyboard())
                return
            item = response.data[0]
            name = item["name"]
            category = item["category"]

        updated = update_quick_expense(user_id, quick_id, name, amount, category)
        context.user_data.clear()
        if updated:
            await update.message.reply_text(
                f"✅ **هزینه {name} ویرایش شد!**\n\n"
                f"💰 مبلغ جدید: {amount:,} تومان\n"
                f"📂 {category}",
                reply_markup=main_keyboard()
            )
        else:
            await update.message.reply_text("❌ خطا در ویرایش.", reply_markup=main_keyboard())
        return

    # ==========================================
    # ثبت سریع هزینه (بدون دسته)
    # ==========================================
    parsed = parse_expense_text(message)
    if parsed:
        amount, description = parsed
        category = detect_category(description)
        add_expense(user_id, amount, description, category)
        await update.message.reply_text(
            f"✅ هزینه ثبت شد!\n\n{category}\n💰 {amount:,} تومان\n📝 {description}",
            reply_markup=main_keyboard()
        )
        return
    
    await update.message.reply_text(
        "❓ از دکمه‌های منو استفاده کن.\n\nیا برای ثبت سریع بنویس:\n85 ناهار",
        reply_markup=main_keyboard()
    )

async def reports_menu_callback(update, context):
    """بازگشت به منوی گزارش‌ها"""

    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if not is_allowed(user_id):
        return

    context.user_data.clear()

    buttons = [
        [
            InlineKeyboardButton(
                "📊 گزارش امروز",
                callback_data="report_today"
            ),
            InlineKeyboardButton(
                "📅 گزارش تاریخ",
                callback_data="report_date"
            ),
        ],
        [
            InlineKeyboardButton(
                "📊 گزارش ماه",
                callback_data="report_month"
            ),
            InlineKeyboardButton(
                "📈 گزارش پیشرفته",
                callback_data="report_advanced"
            ),
        ],
        [
            InlineKeyboardButton(
                "📋 هزینه‌های اخیر",
                callback_data="report_recent"
            ),
            InlineKeyboardButton(
                "📊 آمار کلی",
                callback_data="report_stats"
            ),
        ],
        [
            InlineKeyboardButton(
                "🔙 بازگشت",
                callback_data="back_menu"
            )
        ]
    ]

    await query.edit_message_text(
        "📊 گزارش‌ها\n\n"
        "نوع گزارش موردنظر را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

    class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return


def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"Health server running on port {port}")
    server.serve_forever()
# ==========================================
# اجرای ربات
# ==========================================

def main():

    # سرور Health برای Render
    health_thread = threading.Thread(
        target=run_health_server,
        daemon=True
    )
    health_thread.start()
    
    request = HTTPXRequest(connect_timeout=60, read_timeout=60, write_timeout=60, pool_timeout=60)
    app = Application.builder().token(TOKEN).request(request).get_updates_request(request).build()
    
    app.add_handler(CommandHandler("start", start))
    
    app.add_handler(CallbackQueryHandler(delete_callback, pattern=r"^delete:\d+$"))
    app.add_handler(CallbackQueryHandler(confirm_delete_callback, pattern=r"^confirm_delete:\d+$"))
    app.add_handler(CallbackQueryHandler(cancel_delete_callback, pattern=r"^cancel_delete$"))
    app.add_handler(CallbackQueryHandler(edit_callback, pattern=r"^edit:\d+$"))
    app.add_handler(CallbackQueryHandler(manage_categories, pattern=r"^manage_categories$"))
    app.add_handler(CallbackQueryHandler(category_add_callback, pattern=r"^category_add$"))
    app.add_handler(CallbackQueryHandler(category_rename_callback, pattern=r"^category_rename$"))
    app.add_handler(CallbackQueryHandler(rename_select_callback, pattern=r"^rename_select:\d+$"))
    app.add_handler(CallbackQueryHandler(category_delete_callback, pattern=r"^category_delete$"))
    app.add_handler(CallbackQueryHandler(delete_category_callback, pattern=r"^delete_category:\d+$"))
    app.add_handler(CallbackQueryHandler(confirm_category_delete_callback, pattern=r"^confirm_category_delete:\d+$"))
    app.add_handler(CallbackQueryHandler(settings_menu_callback, pattern=r"^settings_menu$"))
    app.add_handler(CallbackQueryHandler(back_callback, pattern=r"^back_menu$"))
    app.add_handler(CallbackQueryHandler(edit_page_callback, pattern=r"^edit_page:\d+$"))
    app.add_handler(CallbackQueryHandler(advanced_quick_callback, pattern=r"^adv_"))
    app.add_handler(CallbackQueryHandler(ignore_callback, pattern=r"^ignore$"))
    app.add_handler(CallbackQueryHandler(report_page_callback, pattern=r"^report_page:\d+$"))
    app.add_handler(CallbackQueryHandler(date_page_callback, pattern=r"^date_page:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # ==========================================
    # مدیریت هزینه‌های سریع
    # ==========================================
    app.add_handler(CallbackQueryHandler(quick_manage_callback, pattern=r"^quick_manage$"))
    app.add_handler(CallbackQueryHandler(quick_add_callback, pattern=r"^quick_add$"))
    app.add_handler(CallbackQueryHandler(quick_edit_callback, pattern=r"^quick_edit$"))
    app.add_handler(CallbackQueryHandler(quick_delete_callback, pattern=r"^quick_delete$"))
    app.add_handler(CallbackQueryHandler(quick_menu_callback, pattern=r"^quick_menu$"))
    app.add_handler(CallbackQueryHandler(quick_callback, pattern=r"^quick_\d+$"))
    app.add_handler(CallbackQueryHandler(quick_edit_select_callback, pattern=r"^quick_edit_select_"))
    app.add_handler(CallbackQueryHandler(quick_delete_confirm_callback, pattern=r"^quick_delete_confirm_"))
    app.add_handler(CallbackQueryHandler(quick_add_category_callback, pattern=r"^quick_add_cat_"))

    app.add_handler(CallbackQueryHandler(reports_menu_callback, pattern=r"^reports_menu$"))
    app.add_handler(CallbackQueryHandler(reports_callback, pattern=r"^report_(today|date|month|advanced|recent|stats)$"))
    
    print("✅ ربات اجرا شد!")
    app.run_polling()

if __name__ == "__main__":
    main()
