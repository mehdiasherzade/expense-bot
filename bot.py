import os
import re
import logging
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters
from telegram.request import HTTPXRequest
from openpyxl import Workbook
from io import BytesIO

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
    ["➕ ثبت هزینه", "📊 گزارش امروز"],
    ["📅 گزارش ماه", "📋 هزینه‌های اخیر"],
    ["📈 گزارش پیشرفته", "📅 گزارش تاریخ"],
    ["🗑️ حذف/ویرایش", "⚙️ تنظیمات"],
    ["📊 خروجی اکسل"],
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

def add_expense(user_id, amount, description, category):
    data = {
        "user_id": user_id,
        "amount": amount,
        "description": description,
        "category": category,
        "created_at": datetime.now().isoformat()
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
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        return
    today = datetime.now().strftime("%Y-%m-%d")
    rows = get_day_expenses(user_id, today)
    if not rows:
        await update.message.reply_text("📊 امروز هنوز هزینه‌ای ثبت نشده.", reply_markup=main_keyboard())
        return
    total = sum(row[1] for row in rows)
    text = f"📊 گزارش امروز\n📅 {today}\n\n"
    for expense_id, amount, description, category, created_at in rows:
        time = created_at[11:16] if len(created_at) > 11 else ""
        text += f"#{expense_id} {category}\n💰 {amount:,} تومان\n📝 {description} | 🕐 {time}\n\n"
    text += "━━━━━━━━━━━━\n"
    text += f"🧾 تعداد: {len(rows)}\n💵 جمع امروز: {total:,} تومان"
    await update.message.reply_text(text, reply_markup=main_keyboard())

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

async def date_report_button(update, context):
    context.user_data.clear()
    context.user_data["waiting_report_date"] = True
    await update.message.reply_text(
        "📅 گزارش تاریخ دلخواه\n\nتاریخ را با فرمت زیر بفرست:\n\n2026-08-13\n\nمثال:\n2026-08-01",
        reply_markup=back_keyboard()
    )

async def show_date_report(update, context, date_text):
    user_id = update.effective_user.id
    rows = get_day_expenses(user_id, date_text)
    if not rows:
        await update.message.reply_text(f"📅 برای {date_text} هزینه‌ای ثبت نشده.", reply_markup=main_keyboard())
        context.user_data.clear()
        return
    total = sum(row[1] for row in rows)
    text = f"📅 گزارش {date_text}\n\n"
    for expense_id, amount, description, category, created_at in rows:
        text += f"#{expense_id} {category}\n💰 {amount:,} تومان\n📝 {description}\n🕐 {created_at[11:16]}\n\n"
    text += "━━━━━━━━━━━━\n"
    text += f"🧾 تعداد: {len(rows)}\n💵 مجموع: {total:,} تومان"
    context.user_data.clear()
    await update.message.reply_text(text, reply_markup=main_keyboard())

async def advanced_report_button(update, context):
    context.user_data.clear()
    context.user_data["waiting_advanced_start"] = True
    await update.message.reply_text(
        "📈 گزارش پیشرفته\n\nتاریخ شروع را وارد کن:\n\nمثال:\n2026-08-01",
        reply_markup=back_keyboard()
    )

async def show_advanced_report(update, context, start_date, end_date):
    user_id = update.effective_user.id
    (total, count, average, maximum), daily_rows, category_rows = get_advanced_stats(user_id, start_date, end_date)
    if count == 0:
        await update.message.reply_text("📊 در این بازه هزینه‌ای ثبت نشده.", reply_markup=main_keyboard())
        context.user_data.clear()
        return
    text = f"📈 گزارش پیشرفته\n\n📅 از {start_date}\n📅 تا {end_date}\n\n━━━━━━━━━━━━\n"
    text += f"💵 مجموع: {total:,} تومان\n🧾 تعداد: {count}\n📊 میانگین هر هزینه: {average:,}\n🔝 بیشترین هزینه: {maximum:,}\n\n"
    text += "━━━━━━━━━━━━\n📊 بر اساس دسته‌بندی\n\n"
    for category, amount, cnt in category_rows:
        text += f"{category}\n💰 {amount:,} تومان ({cnt} مورد)\n\n"
    if daily_rows:
        text += "━━━━━━━━━━━━\n📅 روند روزانه\n\n"
        for date_text, amount, _ in daily_rows:
            text += f"{date_text}: {amount:,} تومان\n"
    context.user_data.clear()
    await update.message.reply_text(text, reply_markup=main_keyboard())

async def edit_delete_menu(update, context):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        return
    rows = get_recent_expenses(user_id)
    if not rows:
        await update.message.reply_text("📋 هنوز هزینه‌ای ثبت نشده.", reply_markup=main_keyboard())
        return
    buttons = []
    for expense_id, amount, description, category, created_at in rows:
        buttons.append([
            InlineKeyboardButton(f"✏️ ویرایش #{expense_id}", callback_data=f"edit:{expense_id}"),
            InlineKeyboardButton(f"🗑️ حذف #{expense_id}", callback_data=f"delete:{expense_id}"),
        ])
    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_menu")])
    await update.message.reply_text(
        "🗑️ حذف / ✏️ ویرایش\n\nهزینه موردنظر را انتخاب کن:",
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
        f"✏️ ویرایش هزینه #{expense_id}\n\n{category}\n💰 مبلغ فعلی: {amount:,}\n📝 {description}\n\nمبلغ و توضیح جدید را بفرست.\n\nمثال:\n95000 ناهار رستوران"
    )
    await context.bot.send_message(chat_id=user_id, text="🔙 بازگشت", reply_markup=back_keyboard())

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
    await query.edit_message_text("🏠 برگشتیم به منوی اصلی.")
    await context.bot.send_message(chat_id=query.from_user.id, text="منوی اصلی:", reply_markup=main_keyboard())

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
# تابع خروجی اکسل
# ==========================================

async def export_excel(update, context):
    user_id = update.effective_user.id

    if not is_allowed(user_id):
        await update.message.reply_text("⛔ شما اجازه استفاده از این بخش را ندارید.")
        return

    month = datetime.now().strftime("%Y-%m")

    try:
        # ابتدای ماه جاری
        start_date = f"{month}-01 00:00:00"

        # اولین روز ماه بعد
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

        # دریافت هزینه‌های ماه جاری
        response = (
            supabase
            .table("expenses")
            .select("*")
            .eq("user_id", user_id)
            .gte("created_at", start_date)
            .lt("created_at", end_date)
            .order("created_at", desc=False)
            .execute()
        )

        rows = response.data or []

        logger.info(f"Export Excel | found {len(rows)} expenses")

        if not rows:
            await update.message.reply_text(
                "📊 این ماه هنوز هزینه‌ای ثبت نشده.",
                reply_markup=main_keyboard()
            )
            return

        # ساخت فایل اکسل
        wb = Workbook()
        ws = wb.active
        ws.title = "هزینه‌ها"

        headers = [
            "ردیف",
            "دسته‌بندی",
            "مبلغ (تومان)",
            "توضیحات",
            "تاریخ",
            "ساعت"
        ]

        ws.append(headers)

        # استایل هدر
        from openpyxl.styles import Font, Alignment

        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center")

        # اطلاعات هزینه‌ها
        total = 0

        for i, row in enumerate(rows, start=1):

            created_at = str(row.get("created_at", ""))

            date_part = created_at[:10] if len(created_at) >= 10 else ""
            time_part = created_at[11:16] if len(created_at) >= 16 else ""

            amount = int(row.get("amount", 0))

            ws.append([
                i,
                row.get("category", ""),
                amount,
                row.get("description", ""),
                date_part,
                time_part
            ])

            total += amount

        # جمع کل
        ws.append([])
        ws.append([
            "",
            "",
            total,
            "جمع کل",
            "",
            ""
        ])

        # تنظیمات فایل
        ws.freeze_panes = "A2"

        ws.column_dimensions["A"].width = 8
        ws.column_dimensions["B"].width = 20
        ws.column_dimensions["C"].width = 18
        ws.column_dimensions["D"].width = 40
        ws.column_dimensions["E"].width = 16
        ws.column_dimensions["F"].width = 12

        # وسط‌چین کردن ستون‌های مشخص
        for row in ws.iter_rows(
            min_row=1,
            max_row=ws.max_row
        ):
            row[0].alignment = Alignment(horizontal="center")
            row[1].alignment = Alignment(horizontal="center")
            row[2].alignment = Alignment(horizontal="center")
            row[4].alignment = Alignment(horizontal="center")
            row[5].alignment = Alignment(horizontal="center")

        # ذخیره در حافظه
        output = BytesIO()
        wb.save(output)
        output.seek(0)

        filename = f"expenses_{month}.xlsx"

        # ارسال فایل به تلگرام
        await update.message.reply_document(
            document=output,
            filename=filename,
            caption=(
                f"📊 گزارش هزینه‌های ماه {month}\n"
                f"💰 مجموع: {total:,} تومان\n"
                f"📝 تعداد: {len(rows)} مورد"
            ),
            reply_markup=main_keyboard()
        )

        logger.info(
            f"Export Excel successful | user={user_id} | "
            f"rows={len(rows)} | total={total}"
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

async def handle_message(update, context):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ شما اجازه استفاده از این ربات را ندارید.")
        return
    
    message = update.message.text.strip()
    
    if message == "🔙 بازگشت":
        await go_back(update, context)
        return
    
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
    
    if context.user_data.get("waiting_report_date"):
        date_text = normalize_digits(message)
        try:
            datetime.strptime(date_text, "%Y-%m-%d")
        except ValueError:
            await update.message.reply_text("❌ تاریخ نامعتبر است.\n\nفرمت درست:\n2026-08-13", reply_markup=back_keyboard())
            return
        await show_date_report(update, context, date_text)
        return
    
    if context.user_data.get("waiting_advanced_start"):
        start_date = normalize_digits(message)
        try:
            datetime.strptime(start_date, "%Y-%m-%d")
        except ValueError:
            await update.message.reply_text("❌ تاریخ نامعتبر است.\n\nفرمت:\n2026-08-01", reply_markup=back_keyboard())
            return
        context.user_data.clear()
        context.user_data["waiting_advanced_end"] = True
        context.user_data["advanced_start"] = start_date
        await update.message.reply_text("📅 تاریخ پایان را وارد کن:\n\nمثال:\n2026-08-13", reply_markup=back_keyboard())
        return
    
    if context.user_data.get("waiting_advanced_end"):
        end_date = normalize_digits(message)
        try:
            datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError:
            await update.message.reply_text("❌ تاریخ نامعتبر است.\n\nفرمت:\n2026-08-13", reply_markup=back_keyboard())
            return
        start_date = context.user_data["advanced_start"]
        if end_date < start_date:
            await update.message.reply_text("❌ تاریخ پایان نمی‌تواند قبل از تاریخ شروع باشد.", reply_markup=back_keyboard())
            return
        await show_advanced_report(update, context, start_date, end_date)
        return
    
    if message == "➕ ثبت هزینه":
        await expense_button(update, context)
        return
    if message == "📊 گزارش امروز":
        await report(update, context)
        return
    if message == "📅 گزارش ماه":
        await monthly_report(update, context)
        return
    if message == "📋 هزینه‌های اخیر":
        await recent(update, context)
        return
    if message == "📈 گزارش پیشرفته":
        await advanced_report_button(update, context)
        return
    if message == "📅 گزارش تاریخ":
        await date_report_button(update, context)
        return
    if message == "🗑️ حذف/ویرایش":
        await edit_delete_menu(update, context)
        return
    if message == "⚙️ تنظیمات":
        await settings(update, context)
        return
    if message == "📊 خروجی اکسل":
        await export_excel(update, context)
        return
    
    if context.user_data.get("waiting_for_expense"):
        categories = [name for _, name in get_categories()]
        if message in categories:
            await choose_category(update, context, message)
            return
    
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
    
    if context.user_data.get("waiting_for_amount"):
        parsed = parse_expense_text(message)
        if not parsed:
            await update.message.reply_text("❌ فرمت درست نیست.\n\nمثال:\n85000 ناهار", reply_markup=back_keyboard())
            return
        amount, description = parsed
        category = context.user_data.get("selected_category", "📦 سایر")
        add_expense(user_id, amount, description, category)
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ هزینه ثبت شد!\n\n{category}\n💰 {amount:,} تومان\n📝 {description}",
            reply_markup=main_keyboard()
        )
        return
    
    parsed = parse_expense_text(message)
    if parsed:
        amount, description = parsed
        add_expense(user_id, amount, description, "📦 سایر")
        await update.message.reply_text(
            f"✅ هزینه ثبت شد!\n\n📦 سایر\n💰 {amount:,} تومان\n📝 {description}",
            reply_markup=main_keyboard()
        )
        return
    
    await update.message.reply_text(
        "❓ از دکمه‌های منو استفاده کن.\n\nیا برای ثبت سریع بنویس:\n85 ناهار",
        reply_markup=main_keyboard()
    )

# ==========================================
# اجرای ربات
# ==========================================

def main():
    init_db()
    
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
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ ربات اجرا شد!")
    app.run_polling()

if __name__ == "__main__":
    main()
