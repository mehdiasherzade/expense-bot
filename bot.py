import os
import re
from datetime import datetime

from dotenv import load_dotenv
from supabase import create_client, Client

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from telegram.request import HTTPXRequest


# ============================================================
# تنظیمات
# ============================================================

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

try:
    ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))
except ValueError:
    ALLOWED_USER_ID = 0


if not TOKEN:
    raise ValueError("BOT_TOKEN تنظیم نشده است!")

if not SUPABASE_URL:
    raise ValueError("SUPABASE_URL تنظیم نشده است!")

if not SUPABASE_KEY:
    raise ValueError("SUPABASE_KEY تنظیم نشده است!")

if ALLOWED_USER_ID == 0:
    raise ValueError("ALLOWED_USER_ID تنظیم نشده است!")


# ============================================================
# اتصال به Supabase
# ============================================================

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY,
)


# ============================================================
# منوی اصلی
# ============================================================

MAIN_KEYBOARD = [
    ["➕ ثبت هزینه", "📊 گزارش امروز"],
    ["📅 گزارش ماه", "📋 هزینه‌های اخیر"],
    ["📈 گزارش پیشرفته", "📅 گزارش تاریخ"],
    ["🗑️ حذف/ویرایش", "⚙️ تنظیمات"],
]


def main_keyboard():
    return ReplyKeyboardMarkup(
        MAIN_KEYBOARD,
        resize_keyboard=True,
    )


def back_keyboard():
    return ReplyKeyboardMarkup(
        [["🔙 بازگشت"]],
        resize_keyboard=True,
    )


# ============================================================
# دسته‌بندی‌های پیش‌فرض
# ============================================================

DEFAULT_CATEGORIES = [
    "🍔 غذا",
    "🚕 حمل‌ونقل",
    "🛒 خرید",
    "🏠 خانه",
    "🎮 تفریح",
    "💊 درمان",
    "💳 قبض",
    "📦 سایر",
]


# ============================================================
# بررسی دیتابیس
# ============================================================

def init_db():
    """
    بررسی می‌کند جدول‌های Supabase وجود دارند یا خیر.

    ساخت جدول‌ها باید یک بار از طریق SQL Editor در Supabase انجام شود.
    """

    try:
        supabase.table("expenses").select("id").limit(1).execute()
        print("✅ جدول expenses موجود است.")
    except Exception as e:
        print("⚠️ جدول expenses پیدا نشد.")
        print("SQL موردنیاز:")
        print(
            """
CREATE TABLE IF NOT EXISTS expenses (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    amount BIGINT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '📦 سایر',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS categories (
    id BIGSERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);
"""
        )
        print(f"Database error: {e}")

    try:
        supabase.table("categories").select("id").limit(1).execute()
        print("✅ جدول categories موجود است.")
    except Exception as e:
        print("⚠️ جدول categories پیدا نشد.")
        print(f"Database error: {e}")

    # اضافه کردن دسته‌های پیش‌فرض
    try:
        for category in DEFAULT_CATEGORIES:
            try:
                supabase.table("categories").insert(
                    {"name": category}
                ).execute()
            except Exception:
                # اگر قبلاً وجود داشته باشد، نادیده گرفته می‌شود.
                pass

        print("✅ دسته‌بندی‌های پیش‌فرض بررسی شدند.")

    except Exception as e:
        print(f"⚠️ خطا در بررسی دسته‌بندی‌ها: {e}")


# ============================================================
# توابع دیتابیس
# ============================================================

def get_categories():
    response = (
        supabase
        .table("categories")
        .select("*")
        .order("id")
        .execute()
    )

    return [
        (row["id"], row["name"])
        for row in response.data
    ]


def category_exists(name):
    response = (
        supabase
        .table("categories")
        .select("id")
        .eq("name", name)
        .execute()
    )

    return len(response.data) > 0


def add_category(name):
    try:
        supabase.table("categories").insert(
            {"name": name}
        ).execute()

        return True

    except Exception as e:
        print(f"add_category error: {e}")
        return False


def rename_category(category_id, new_name):
    try:
        response = (
            supabase
            .table("categories")
            .select("name")
            .eq("id", category_id)
            .execute()
        )

        if not response.data:
            return False

        old_name = response.data[0]["name"]

        # جلوگیری از تغییر نام «سایر»
        if old_name == "📦 سایر":
            return False

        # تغییر نام دسته
        (
            supabase
            .table("categories")
            .update({"name": new_name})
            .eq("id", category_id)
            .execute()
        )

        # به‌روزرسانی هزینه‌های قبلی
        (
            supabase
            .table("expenses")
            .update({"category": new_name})
            .eq("category", old_name)
            .execute()
        )

        return True

    except Exception as e:
        print(f"rename_category error: {e}")
        return False


def delete_category(category_id):
    try:
        response = (
            supabase
            .table("categories")
            .select("name")
            .eq("id", category_id)
            .execute()
        )

        if not response.data:
            return False

        category_name = response.data[0]["name"]

        # «سایر» نباید حذف شود
        if category_name == "📦 سایر":
            return False

        # انتقال هزینه‌های قبلی
        (
            supabase
            .table("expenses")
            .update({"category": "📦 سایر"})
            .eq("category", category_name)
            .execute()
        )

        # حذف دسته
        (
            supabase
            .table("categories")
            .delete()
            .eq("id", category_id)
            .execute()
        )

        return True

    except Exception as e:
        print(f"delete_category error: {e}")
        return False


def add_expense(user_id, amount, description, category):
    try:
        data = {
            "user_id": user_id,
            "amount": amount,
            "description": description,
            "category": category,
            "created_at": datetime.now().isoformat(),
        }

        response = (
            supabase
            .table("expenses")
            .insert(data)
            .execute()
        )

        return bool(response.data)

    except Exception as e:
        print(f"add_expense error: {e}")
        return False


def get_expense(user_id, expense_id):
    try:
        response = (
            supabase
            .table("expenses")
            .select("*")
            .eq("id", expense_id)
            .eq("user_id", user_id)
            .execute()
        )

        if response.data:
            row = response.data[0]

            return (
                row["id"],
                row["amount"],
                row["description"],
                row["category"],
                row["created_at"],
            )

        return None

    except Exception as e:
        print(f"get_expense error: {e}")
        return None


def delete_expense(user_id, expense_id):
    try:
        response = (
            supabase
            .table("expenses")
            .delete()
            .eq("id", expense_id)
            .eq("user_id", user_id)
            .execute()
        )

        return bool(response.data)

    except Exception as e:
        print(f"delete_expense error: {e}")
        return False


def update_expense(
    user_id,
    expense_id,
    amount,
    description,
    category,
):
    try:
        data = {
            "amount": amount,
            "description": description,
            "category": category,
        }

        response = (
            supabase
            .table("expenses")
            .update(data)
            .eq("id", expense_id)
            .eq("user_id", user_id)
            .execute()
        )

        return bool(response.data)

    except Exception as e:
        print(f"update_expense error: {e}")
        return False


def get_recent_expenses(user_id, limit=10):
    try:
        response = (
            supabase
            .table("expenses")
            .select("*")
            .eq("user_id", user_id)
            .order("id", desc=True)
            .limit(limit)
            .execute()
        )

        return [
            (
                row["id"],
                row["amount"],
                row["description"],
                row["category"],
                row["created_at"],
            )
            for row in response.data
        ]

    except Exception as e:
        print(f"get_recent_expenses error: {e}")
        return []


def get_day_expenses(user_id, date_text):
    try:
        response = (
            supabase
            .table("expenses")
            .select("*")
            .eq("user_id", user_id)
            .gte(
                "created_at",
                f"{date_text} 00:00:00",
            )
            .lt(
                "created_at",
                f"{date_text} 23:59:59.999999",
            )
            .order("id", desc=True)
            .execute()
        )

        return [
            (
                row["id"],
                row["amount"],
                row["description"],
                row["category"],
                row["created_at"],
            )
            for row in response.data
        ]

    except Exception as e:
        print(f"get_day_expenses error: {e}")
        return []


def get_month_expenses(user_id, month_text):
    try:
        response = (
            supabase
            .table("expenses")
            .select("*")
            .eq("user_id", user_id)
            .gte(
                "created_at",
                f"{month_text}-01 00:00:00",
            )
            .lt(
                "created_at",
                f"{month_text}-31 23:59:59.999999",
            )
            .order("id", desc=True)
            .execute()
        )

        return response.data

    except Exception as e:
        print(f"get_month_expenses error: {e}")

        # fallback
        try:
            response = (
                supabase
                .table("expenses")
                .select("*")
                .eq("user_id", user_id)
                .like(
                    "created_at",
                    f"{month_text}%",
                )
                .order("id", desc=True)
                .execute()
            )

            return response.data

        except Exception as second_error:
            print(
                f"get_month_expenses fallback error: "
                f"{second_error}"
            )
            return []


def get_advanced_stats(
    user_id,
    start_date,
    end_date,
):
    try:
        response = (
            supabase
            .table("expenses")
            .select("*")
            .eq("user_id", user_id)
            .gte(
                "created_at",
                f"{start_date} 00:00:00",
            )
            .lte(
                "created_at",
                f"{end_date} 23:59:59.999999",
            )
            .order("created_at")
            .execute()
        )

        rows = response.data

    except Exception as e:
        print(f"get_advanced_stats error: {e}")
        return (0, 0, 0, 0), [], []

    if not rows:
        return (0, 0, 0, 0), [], []

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

    # ----------------------------
    # گروه‌بندی روزانه
    # ----------------------------

    daily = {}

    # ----------------------------
    # گروه‌بندی دسته‌بندی
    # ----------------------------

    categories = {}

    for row in rows:

        created_at = str(
            row.get("created_at", "")
        )

        date_key = (
            created_at[:10]
            if len(created_at) >= 10
            else "نامشخص"
        )

        daily[date_key] = (
            daily.get(date_key, 0)
            + int(row["amount"])
        )

        category = row.get(
            "category",
            "📦 سایر",
        )

        if category not in categories:
            categories[category] = {
                "total": 0,
                "count": 0,
            }

        categories[category]["total"] += int(
            row["amount"]
        )

        categories[category]["count"] += 1

    daily_rows = [
        (date_text, amount)
        for date_text, amount
        in sorted(daily.items())
    ]

    category_rows = [
        (
            category,
            data["total"],
            data["count"],
        )
        for category, data
        in sorted(
            categories.items(),
            key=lambda x: x[1]["total"],
            reverse=True,
        )
    ]

    return (
        total,
        count,
        average,
        maximum,
    ), daily_rows, category_rows


# ============================================================
# توابع کمکی
# ============================================================

def normalize_digits(text):
    translation = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789",
    )

    return text.translate(translation)


def parse_amount(text):
    text = normalize_digits(text)

    text = (
        text
        .replace(",", "")
        .replace("٬", "")
        .replace("،", "")
        .replace(" ", "")
    )

    if not text.isdigit():
        return None

    amount = int(text)

    if amount <= 0:
        return None

    return amount


def parse_expense_text(message):
    message = normalize_digits(
        message.strip()
    )

    if not message:
        return None

    match = re.match(
        r"^([\d,\u066C\u060C]+)\s+(.+)$",
        message,
    )

    if not match:
        return None

    amount_text = match.group(1)
    description = match.group(2).strip()

    amount = parse_amount(amount_text)

    if amount is None:
        return None

    if not description:
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

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
    )


def is_allowed(user_id):
    return user_id == ALLOWED_USER_ID


def valid_date(date_text):
    try:
        datetime.strptime(
            date_text,
            "%Y-%m-%d",
        )
        return True
    except ValueError:
        return False


def format_time(created_at):
    if not created_at:
        return ""

    created_at = str(created_at)

    if "T" in created_at:
        return created_at[11:16]

    return created_at[11:16]


# ============================================================
# /start
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id

    if not is_allowed(user_id):
        await update.message.reply_text(
            "⛔ شما اجازه استفاده از این ربات را ندارید."
        )
        return

    context.user_data.clear()

    await update.message.reply_text(
        "سلام 👋\n\n"
        "💰 دفتر هزینه شخصی آماده است.\n\n"
        "⚡ ثبت سریع:\n"
        "85 ناهار\n"
        "85000 خرید\n\n"
        "از منوی پایین انتخاب کن.",
        reply_markup=main_keyboard(),
    )


# ============================================================
# بازگشت
# ============================================================

async def go_back(
    update,
    context,
):
    context.user_data.clear()

    await update.message.reply_text(
        "🏠 برگشتیم به منوی اصلی.",
        reply_markup=main_keyboard(),
    )


# ============================================================
# ثبت هزینه
# ============================================================

async def expense_button(
    update,
    context,
):
    context.user_data.clear()

    context.user_data[
        "waiting_for_expense"
    ] = True

    await update.message.reply_text(
        "➕ ثبت هزینه\n\n"
        "دسته‌بندی را انتخاب کن:",
        reply_markup=category_keyboard(),
    )


async def choose_category(
    update,
    context,
    category,
):
    context.user_data[
        "selected_category"
    ] = category

    context.user_data[
        "waiting_for_expense"
    ] = False

    context.user_data[
        "waiting_for_amount"
    ] = True

    await update.message.reply_text(
        f"{category}\n\n"
        "مبلغ و توضیح هزینه را وارد کن.\n\n"
        "مثال:\n"
        "85000 ناهار",
        reply_markup=back_keyboard(),
    )


# ============================================================
# گزارش امروز
# ============================================================

async def report(
    update,
    context,
):
    user_id = update.effective_user.id

    if not is_allowed(user_id):
        return

    today = datetime.now().strftime(
        "%Y-%m-%d"
    )

    rows = get_day_expenses(
        user_id,
        today,
    )

    if not rows:
        await update.message.reply_text(
            "📊 امروز هنوز هزینه‌ای ثبت نشده.",
            reply_markup=main_keyboard(),
        )
        return

    total = sum(
        row[1]
        for row in rows
    )

    text = (
        f"📊 گزارش امروز\n"
        f"📅 {today}\n\n"
    )

    for (
        expense_id,
        amount,
        description,
        category,
        created_at,
    ) in rows:

        time = format_time(
            created_at
        )

        text += (
            f"#{expense_id} {category}\n"
            f"💰 {amount:,} تومان\n"
            f"📝 {description}\n"
            f"🕐 {time}\n\n"
        )

    text += "━━━━━━━━━━━━\n"
    text += (
        f"🧾 تعداد: {len(rows)}\n"
        f"💵 جمع امروز: {total:,} تومان"
    )

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


# ============================================================
# گزارش ماه
# ============================================================

async def monthly_report(
    update,
    context,
):
    user_id = update.effective_user.id

    if not is_allowed(user_id):
        return

    month = datetime.now().strftime(
        "%Y-%m"
    )

    rows = get_month_expenses(
        user_id,
        month,
    )

    if not rows:
        await update.message.reply_text(
            "📅 این ماه هنوز هزینه‌ای ثبت نشده.",
            reply_markup=main_keyboard(),
        )
        return

    categories = {}

    for row in rows:

        category = row.get(
            "category",
            "📦 سایر",
        )

        amount = int(
            row["amount"]
        )

        if category not in categories:
            categories[category] = {
                "total": 0,
                "count": 0,
            }

        categories[category]["total"] += amount
        categories[category]["count"] += 1

    total = sum(
        data["total"]
        for data in categories.values()
    )

    count = sum(
        data["count"]
        for data in categories.values()
    )

    text = (
        "📅 گزارش ماه جاری\n\n"
    )

    for category, data in sorted(
        categories.items(),
        key=lambda x: x[1]["total"],
        reverse=True,
    ):
        text += (
            f"{category}\n"
            f"💰 {data['total']:,} تومان "
            f"({data['count']} مورد)\n\n"
        )

    text += "━━━━━━━━━━━━\n"
    text += (
        f"🧾 تعداد هزینه‌ها: {count}\n"
        f"💵 مجموع: {total:,} تومان"
    )

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


# ============================================================
# هزینه‌های اخیر
# ============================================================

async def recent(
    update,
    context,
):
    user_id = update.effective_user.id

    if not is_allowed(user_id):
        return

    rows = get_recent_expenses(
        user_id
    )

    if not rows:
        await update.message.reply_text(
            "📋 هنوز هیچ هزینه‌ای ثبت نشده.",
            reply_markup=main_keyboard(),
        )
        return

    text = "📋 آخرین هزینه‌ها\n\n"

    for (
        expense_id,
        amount,
        description,
        category,
        created_at,
    ) in rows:

        text += (
            f"#{expense_id} {category}\n"
            f"💰 {amount:,} تومان\n"
            f"📝 {description}\n"
            f"📅 {str(created_at)[:10]} "
            f"| 🕐 {format_time(created_at)}\n\n"
        )

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


# ============================================================
# گزارش تاریخ
# ============================================================

async def date_report_button(
    update,
    context,
):
    context.user_data.clear()

    context.user_data[
        "waiting_report_date"
    ] = True

    await update.message.reply_text(
        "📅 گزارش تاریخ دلخواه\n\n"
        "تاریخ را با فرمت زیر بفرست:\n\n"
        "2026-08-13\n\n"
        "مثال:\n"
        "2026-08-01",
        reply_markup=back_keyboard(),
    )


async def show_date_report(
    update,
    context,
    date_text,
):
    user_id = update.effective_user.id

    rows = get_day_expenses(
        user_id,
        date_text,
    )

    if not rows:
        context.user_data.clear()

        await update.message.reply_text(
            f"📅 برای {date_text} هزینه‌ای ثبت نشده.",
            reply_markup=main_keyboard(),
        )
        return

    total = sum(
        row[1]
        for row in rows
    )

    text = (
        f"📅 گزارش {date_text}\n\n"
    )

    for (
        expense_id,
        amount,
        description,
        category,
        created_at,
    ) in rows:

        text += (
            f"#{expense_id} {category}\n"
            f"💰 {amount:,} تومان\n"
            f"📝 {description}\n"
            f"🕐 {format_time(created_at)}\n\n"
        )

    text += "━━━━━━━━━━━━\n"
    text += (
        f"🧾 تعداد: {len(rows)}\n"
        f"💵 مجموع: {total:,} تومان"
    )

    context.user_data.clear()

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


# ============================================================
# گزارش پیشرفته
# ============================================================

async def advanced_report_button(
    update,
    context,
):
    context.user_data.clear()

    context.user_data[
        "waiting_advanced_start"
    ] = True

    await update.message.reply_text(
        "📈 گزارش پیشرفته\n\n"
        "تاریخ شروع را وارد کن:\n\n"
        "مثال:\n"
        "2026-08-01",
        reply_markup=back_keyboard(),
    )


async def show_advanced_report(
    update,
    context,
    start_date,
    end_date,
):
    user_id = update.effective_user.id

    (
        stats,
        daily_rows,
        category_rows,
    ) = get_advanced_stats(
        user_id,
        start_date,
        end_date,
    )

    total, count, average, maximum = stats

    if count == 0:
        context.user_data.clear()

        await update.message.reply_text(
            "📊 در این بازه هزینه‌ای ثبت نشده.",
            reply_markup=main_keyboard(),
        )
        return

    text = (
        "📈 گزارش پیشرفته\n\n"
        f"📅 از {start_date}\n"
        f"📅 تا {end_date}\n\n"
        "━━━━━━━━━━━━\n"
        f"💵 مجموع: {total:,} تومان\n"
        f"🧾 تعداد: {count}\n"
        f"📊 میانگین هر هزینه: {average:,} تومان\n"
        f"🔝 بیشترین هزینه: {maximum:,} تومان\n\n"
        "━━━━━━━━━━━━\n"
        "📊 بر اساس دسته‌بندی\n\n"
    )

    for (
        category,
        amount,
        cnt,
    ) in category_rows:

        text += (
            f"{category}\n"
            f"💰 {amount:,} تومان "
            f"({cnt} مورد)\n\n"
        )

    if daily_rows:

        text += (
            "━━━━━━━━━━━━\n"
            "📅 روند روزانه\n\n"
        )

        for (
            date_text,
            amount,
        ) in daily_rows:

            text += (
                f"{date_text}: "
                f"{amount:,} تومان\n"
            )

    context.user_data.clear()

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


# ============================================================
# حذف / ویرایش
# ============================================================

async def edit_delete_menu(
    update,
    context,
):
    user_id = update.effective_user.id

    if not is_allowed(user_id):
        return

    rows = get_recent_expenses(
        user_id
    )

    if not rows:
        await update.message.reply_text(
            "📋 هنوز هزینه‌ای ثبت نشده.",
            reply_markup=main_keyboard(),
        )
        return

    buttons = []

    for (
        expense_id,
        amount,
        description,
        category,
        created_at,
    ) in rows:

        buttons.append(
            [
                InlineKeyboardButton(
                    f"✏️ ویرایش #{expense_id}",
                    callback_data=f"edit:{expense_id}",
                ),
                InlineKeyboardButton(
                    f"🗑️ حذف #{expense_id}",
                    callback_data=f"delete:{expense_id}",
                ),
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "🔙 بازگشت",
                callback_data="back_menu",
            )
        ]
    )

    await update.message.reply_text(
        "🗑️ حذف / ✏️ ویرایش\n\n"
        "هزینه موردنظر را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def delete_callback(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    if not is_allowed(user_id):
        return

    try:
        expense_id = int(
            query.data.split(":")[1]
        )
    except (ValueError, IndexError):
        await query.edit_message_text(
            "❌ شناسه هزینه نامعتبر است."
        )
        return

    expense = get_expense(
        user_id,
        expense_id,
    )

    if not expense:
        await query.edit_message_text(
            "❌ هزینه پیدا نشد."
        )
        return

    (
        _,
        amount,
        description,
        category,
        _,
    ) = expense

    buttons = [
        [
            InlineKeyboardButton(
                "✅ بله، حذف کن",
                callback_data=(
                    f"confirm_delete:{expense_id}"
                ),
            ),
            InlineKeyboardButton(
                "❌ انصراف",
                callback_data="cancel_delete",
            ),
        ]
    ]

    await query.edit_message_text(
        "⚠️ حذف این هزینه؟\n\n"
        f"{category}\n"
        f"💰 {amount:,} تومان\n"
        f"📝 {description}",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def confirm_delete_callback(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    if not is_allowed(user_id):
        return

    try:
        expense_id = int(
            query.data.split(":")[1]
        )
    except (ValueError, IndexError):
        await query.edit_message_text(
            "❌ شناسه نامعتبر است."
        )
        return

    deleted = delete_expense(
        user_id,
        expense_id,
    )

    if deleted:
        await query.edit_message_text(
            f"✅ هزینه #{expense_id} حذف شد."
        )
    else:
        await query.edit_message_text(
            "❌ هزینه پیدا نشد یا حذف نشد."
        )


async def cancel_delete_callback(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "❌ حذف لغو شد."
    )


async def edit_callback(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    if not is_allowed(user_id):
        return

    try:
        expense_id = int(
            query.data.split(":")[1]
        )
    except (ValueError, IndexError):
        await query.edit_message_text(
            "❌ شناسه نامعتبر است."
        )
        return

    expense = get_expense(
        user_id,
        expense_id,
    )

    if not expense:
        await query.edit_message_text(
            "❌ هزینه پیدا نشد."
        )
        return

    (
        _,
        amount,
        description,
        category,
        _,
    ) = expense

    context.user_data.clear()

    context.user_data[
        "editing_expense"
    ] = expense_id

    context.user_data[
        "editing_category"
    ] = category

    context.user_data[
        "waiting_for_edit"
    ] = True

    await query.edit_message_text(
        f"✏️ ویرایش هزینه #{expense_id}\n\n"
        f"{category}\n"
        f"💰 مبلغ فعلی: {amount:,}\n"
        f"📝 {description}\n\n"
        "مبلغ و توضیح جدید را بفرست.\n\n"
        "مثال:\n"
        "95000 ناهار رستوران"
    )

    await context.bot.send_message(
        chat_id=user_id,
        text="🔙 بازگشت",
        reply_markup=back_keyboard(),
    )


# ============================================================
# تنظیمات
# ============================================================

async def settings(
    update,
    context,
):
    user_id = update.effective_user.id

    if not is_allowed(user_id):
        return

    buttons = [
        [
            InlineKeyboardButton(
                "🏷️ دسته‌بندی‌ها",
                callback_data="manage_categories",
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 بازگشت",
                callback_data="back_menu",
            )
        ],
    ]

    await update.message.reply_text(
        "⚙️ تنظیمات",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def manage_categories(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    if not is_allowed(user_id):
        return

    context.user_data.clear()

    categories = get_categories()

    text = (
        "🏷️ مدیریت دسته‌بندی‌ها\n\n"
    )

    for (
        category_id,
        name,
    ) in categories:

        text += (
            f"• {name}\n"
        )

    buttons = [
        [
            InlineKeyboardButton(
                "➕ افزودن دسته",
                callback_data="category_add",
            )
        ],
        [
            InlineKeyboardButton(
                "✏️ تغییر نام",
                callback_data="category_rename",
            ),
            InlineKeyboardButton(
                "🗑️ حذف",
                callback_data="category_delete",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔙 بازگشت",
                callback_data="settings_menu",
            )
        ],
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def category_add_callback(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    if not is_allowed(user_id):
        return

    context.user_data.clear()

    context.user_data[
        "waiting_category_add"
    ] = True

    await query.edit_message_text(
        "➕ افزودن دسته\n\n"
        "نام دسته جدید را بفرست.\n\n"
        "مثال:\n"
        "☕ کافه\n\n"
        "🔙 برای لغو، بازگشت را بزن."
    )

    await context.bot.send_message(
        chat_id=user_id,
        text="🔙 بازگشت",
        reply_markup=back_keyboard(),
    )


async def category_rename_callback(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    if not is_allowed(user_id):
        return

    categories = get_categories()

    buttons = []

    for (
        category_id,
        name,
    ) in categories:

        if name == "📦 سایر":
            continue

        buttons.append(
            [
                InlineKeyboardButton(
                    name,
                    callback_data=(
                        f"rename_select:{category_id}"
                    ),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "🔙 بازگشت",
                callback_data="manage_categories",
            )
        ]
    )

    await query.edit_message_text(
        "✏️ کدام دسته را می‌خواهی "
        "تغییر نام بدهی؟",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def rename_select_callback(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    if not is_allowed(user_id):
        return

    try:
        category_id = int(
            query.data.split(":")[1]
        )
    except (ValueError, IndexError):
        await query.edit_message_text(
            "❌ شناسه دسته نامعتبر است."
        )
        return

    context.user_data.clear()

    context.user_data[
        "waiting_category_rename"
    ] = True

    context.user_data[
        "rename_category_id"
    ] = category_id

    await query.edit_message_text(
        "✏️ نام جدید دسته را بفرست.\n\n"
        "مثال:\n"
        "☕ کافه"
    )

    await context.bot.send_message(
        chat_id=user_id,
        text="🔙 بازگشت",
        reply_markup=back_keyboard(),
    )


async def category_delete_callback(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    if not is_allowed(user_id):
        return

    categories = get_categories()

    buttons = []

    for (
        category_id,
        name,
    ) in categories:

        if name == "📦 سایر":
            continue

        buttons.append(
            [
                InlineKeyboardButton(
                    f"🗑️ {name}",
                    callback_data=(
                        f"delete_category:{category_id}"
                    ),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "🔙 بازگشت",
                callback_data="manage_categories",
            )
        ]
    )

    await query.edit_message_text(
        "🗑️ کدام دسته را می‌خواهی حذف کنی؟\n\n"
        "⚠️ هزینه‌های آن دسته به "
        "«📦 سایر» منتقل می‌شوند.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def delete_category_callback(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    if not is_allowed(user_id):
        return

    try:
        category_id = int(
            query.data.split(":")[1]
        )
    except (ValueError, IndexError):
        await query.edit_message_text(
            "❌ شناسه دسته نامعتبر است."
        )
        return

    categories = get_categories()

    category_name = None

    for (
        cid,
        name,
    ) in categories:

        if cid == category_id:
            category_name = name
            break

    if not category_name:
        await query.edit_message_text(
            "❌ دسته پیدا نشد."
        )
        return

    if category_name == "📦 سایر":
        await query.edit_message_text(
            "❌ دسته «📦 سایر» قابل حذف نیست."
        )
        return

    buttons = [
        [
            InlineKeyboardButton(
                "✅ بله، حذف کن",
                callback_data=(
                    f"confirm_category_delete:"
                    f"{category_id}"
                ),
            ),
            InlineKeyboardButton(
                "❌ انصراف",
                callback_data="manage_categories",
            ),
        ]
    ]

    await query.edit_message_text(
        f"⚠️ حذف دسته «{category_name}»؟\n\n"
        "هزینه‌های قبلی این دسته به "
        "«📦 سایر» منتقل می‌شوند.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def confirm_category_delete_callback(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    if not is_allowed(user_id):
        return

    try:
        category_id = int(
            query.data.split(":")[1]
        )
    except (ValueError, IndexError):
        await query.edit_message_text(
            "❌ شناسه دسته نامعتبر است."
        )
        return

    deleted = delete_category(
        category_id
    )

    if deleted:
        await query.edit_message_text(
            "✅ دسته حذف شد.\n"
            "هزینه‌های قبلی آن به "
            "«📦 سایر» منتقل شدند."
        )
    else:
        await query.edit_message_text(
            "❌ این دسته قابل حذف نیست "
            "یا حذف انجام نشد."
        )


# ============================================================
# Callback بازگشت
# ============================================================

async def back_callback(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    context.user_data.clear()

    await query.edit_message_text(
        "🏠 برگشتیم به منوی اصلی."
    )

    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="منوی اصلی:",
        reply_markup=main_keyboard(),
    )


async def settings_menu_callback(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    if not is_allowed(user_id):
        return

    buttons = [
        [
            InlineKeyboardButton(
                "🏷️ دسته‌بندی‌ها",
                callback_data="manage_categories",
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 بازگشت",
                callback_data="back_menu",
            )
        ],
    ]

    await query.edit_message_text(
        "⚙️ تنظیمات",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


# ============================================================
# هندلر اصلی پیام‌ها
# ============================================================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return

    user_id = update.effective_user.id

    if not is_allowed(user_id):
        await update.message.reply_text(
            "⛔ شما اجازه استفاده از این ربات را ندارید."
        )
        return

    message = (
        update.message.text.strip()
    )

    # --------------------------------------------------------
    # بازگشت
    # --------------------------------------------------------

    if message == "🔙 بازگشت":
        await go_back(
            update,
            context,
        )
        return

    # --------------------------------------------------------
    # افزودن دسته
    # --------------------------------------------------------

    if context.user_data.get(
        "waiting_category_add"
    ):

        if not message:
            return

        if category_exists(message):
            await update.message.reply_text(
                "❌ این دسته از قبل وجود دارد.",
                reply_markup=back_keyboard(),
            )
            return

        if add_category(message):

            context.user_data.clear()

            await update.message.reply_text(
                f"✅ دسته «{message}» اضافه شد.",
                reply_markup=main_keyboard(),
            )

        else:
            await update.message.reply_text(
                "❌ خطا در افزودن دسته.",
                reply_markup=back_keyboard(),
            )

        return

    # --------------------------------------------------------
    # تغییر نام دسته
    # --------------------------------------------------------

    if context.user_data.get(
        "waiting_category_rename"
    ):

        category_id = context.user_data.get(
            "rename_category_id"
        )

        if category_id is None:
            context.user_data.clear()

            await update.message.reply_text(
                "❌ خطا در انتخاب دسته.",
                reply_markup=main_keyboard(),
            )
            return

        if not message:
            return

        if category_exists(message):
            await update.message.reply_text(
                "❌ این نام قبلاً وجود دارد.",
                reply_markup=back_keyboard(),
            )
            return

        if rename_category(
            category_id,
            message,
        ):

            context.user_data.clear()

            await update.message.reply_text(
                f"✅ دسته به «{message}» تغییر کرد.",
                reply_markup=main_keyboard(),
            )

        else:
            await update.message.reply_text(
                "❌ تغییر نام انجام نشد.",
                reply_markup=back_keyboard(),
            )

        return

    # --------------------------------------------------------
    # تاریخ گزارش
    # --------------------------------------------------------

    if context.user_data.get(
        "waiting_report_date"
    ):

        date_text = normalize_digits(
            message
        )

        if not valid_date(date_text):

            await update.message.reply_text(
                "❌ تاریخ نامعتبر است.\n\n"
                "فرمت درست:\n"
                "2026-08-13",
                reply_markup=back_keyboard(),
            )
            return

        await show_date_report(
            update,
            context,
            date_text,
        )

        return

    # --------------------------------------------------------
    # تاریخ شروع گزارش پیشرفته
    # --------------------------------------------------------

    if context.user_data.get(
        "waiting_advanced_start"
    ):

        start_date = normalize_digits(
            message
        )

        if not valid_date(start_date):

            await update.message.reply_text(
                "❌ تاریخ نامعتبر است.\n\n"
                "فرمت:\n"
                "2026-08-01",
                reply_markup=back_keyboard(),
            )
            return

        context.user_data.clear()

        context.user_data[
            "waiting_advanced_end"
        ] = True

        context.user_data[
            "advanced_start"
        ] = start_date

        await update.message.reply_text(
            "📅 تاریخ پایان را وارد کن:\n\n"
            "مثال:\n"
            "2026-08-13",
            reply_markup=back_keyboard(),
        )

        return

    # --------------------------------------------------------
    # تاریخ پایان گزارش پیشرفته
    # --------------------------------------------------------

    if context.user_data.get(
        "waiting_advanced_end"
    ):

        end_date = normalize_digits(
            message
        )

        if not valid_date(end_date):

            await update.message.reply_text(
                "❌ تاریخ نامعتبر است.\n\n"
                "فرمت:\n"
                "2026-08-13",
                reply_markup=back_keyboard(),
            )
            return

        start_date = context.user_data.get(
            "advanced_start"
        )

        if not start_date:
            context.user_data.clear()

            await update.message.reply_text(
                "❌ تاریخ شروع پیدا نشد. "
                "دوباره تلاش کن.",
                reply_markup=main_keyboard(),
            )
            return

        if end_date < start_date:

            await update.message.reply_text(
                "❌ تاریخ پایان نمی‌تواند "
                "قبل از تاریخ شروع باشد.",
                reply_markup=back_keyboard(),
            )
            return

        await show_advanced_report(
            update,
            context,
            start_date,
            end_date,
        )

        return

    # --------------------------------------------------------
    # منوی اصلی
    # --------------------------------------------------------

    if message == "➕ ثبت هزینه":

        await expense_button(
            update,
            context,
        )
        return

    if message == "📊 گزارش امروز":

        await report(
            update,
            context,
        )
        return

    if message == "📅 گزارش ماه":

        await monthly_report(
            update,
            context,
        )
        return

    if message == "📋 هزینه‌های اخیر":

        await recent(
            update,
            context,
        )
        return

    if message == "📈 گزارش پیشرفته":

        await advanced_report_button(
            update,
            context,
        )
        return

    if message == "📅 گزارش تاریخ":

        await date_report_button(
            update,
            context,
        )
        return

    if message == "🗑️ حذف/ویرایش":

        await edit_delete_menu(
            update,
            context,
        )
        return

    if message == "⚙️ تنظیمات":

        await settings(
            update,
            context,
        )
        return

    # --------------------------------------------------------
    # انتخاب دسته
    # --------------------------------------------------------

    if context.user_data.get(
        "waiting_for_expense"
    ):

        categories = [
            name
            for _, name
            in get_categories()
        ]

        if message in categories:

            await choose_category(
                update,
                context,
                message,
            )

            return

    # --------------------------------------------------------
    # ویرایش هزینه
    # --------------------------------------------------------

    if context.user_data.get(
        "waiting_for_edit"
    ):

        parsed = parse_expense_text(
            message
        )

        if not parsed:

            await update.message.reply_text(
                "❌ فرمت درست نیست.\n\n"
                "مثال:\n"
                "95000 ناهار رستوران",
                reply_markup=back_keyboard(),
            )
            return

        amount, description = parsed

        expense_id = context.user_data.get(
            "editing_expense"
        )

        category = context.user_data.get(
            "editing_category",
            "📦 سایر",
        )

        if expense_id is None:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ هزینه موردنظر پیدا نشد.",
                reply_markup=main_keyboard(),
            )
            return

        updated = update_expense(
            user_id,
            expense_id,
            amount,
            description,
            category,
        )

        context.user_data.clear()

        if updated:

            await update.message.reply_text(
                f"✅ هزینه #{expense_id} "
                "ویرایش شد.\n\n"
                f"{category}\n"
                f"💰 {amount:,} تومان\n"
                f"📝 {description}",
                reply_markup=main_keyboard(),
            )

        else:

            await update.message.reply_text(
                "❌ ویرایش هزینه انجام نشد.",
                reply_markup=main_keyboard(),
            )

        return

    # --------------------------------------------------------
    # ثبت مبلغ بعد از انتخاب دسته
    # --------------------------------------------------------

    if context.user_data.get(
        "waiting_for_amount"
    ):

        parsed = parse_expense_text(
            message
        )

        if not parsed:

            await update.message.reply_text(
                "❌ فرمت درست نیست.\n\n"
                "مثال:\n"
                "85000 ناهار",
                reply_markup=back_keyboard(),
            )
            return

        amount, description = parsed

        category = context.user_data.get(
            "selected_category",
            "📦 سایر",
        )

        saved = add_expense(
            user_id,
            amount,
            description,
            category,
        )

        if not saved:

            await update.message.reply_text(
                "❌ ذخیره هزینه انجام نشد.\n"
                "لطفاً دوباره تلاش کن.",
                reply_markup=back_keyboard(),
            )
            return

        context.user_data.clear()

        await update.message.reply_text(
            "✅ هزینه ثبت شد!\n\n"
            f"{category}\n"
            f"💰 {amount:,} تومان\n"
            f"📝 {description}",
            reply_markup=main_keyboard(),
        )

        return

    # --------------------------------------------------------
    # ثبت سریع
    # --------------------------------------------------------

    parsed = parse_expense_text(
        message
    )

    if parsed:

        amount, description = parsed

        saved = add_expense(
            user_id,
            amount,
            description,
            "📦 سایر",
        )

        if saved:

            await update.message.reply_text(
                "✅ هزینه ثبت شد!\n\n"
                "📦 سایر\n"
                f"💰 {amount:,} تومان\n"
                f"📝 {description}",
                reply_markup=main_keyboard(),
            )

        else:

            await update.message.reply_text(
                "❌ ذخیره هزینه انجام نشد.\n"
                "لطفاً دوباره تلاش کن.",
                reply_markup=main_keyboard(),
            )

        return

    # --------------------------------------------------------
    # پیام ناشناخته
    # --------------------------------------------------------

    await update.message.reply_text(
        "❓ از دکمه‌های منو استفاده کن.\n\n"
        "یا برای ثبت سریع بنویس:\n"
        "85 ناهار",
        reply_markup=main_keyboard(),
    )


# ============================================================
# خطایابی
# ============================================================

async def error_handler(
    update,
    context,
):
    print(
        "❌ ERROR:",
        repr(context.error),
    )


# ============================================================
# اجرای ربات
# ============================================================

def main():

    print("🚀 Starting expense bot...")

    # بررسی دیتابیس
    init_db()

    # تنظیم HTTPX
    request = HTTPXRequest(
        connect_timeout=60,
        read_timeout=60,
        write_timeout=60,
        pool_timeout=60,
    )

    # ساخت Application
    app = (
        Application
        .builder()
        .token(TOKEN)
        .request(request)
        .get_updates_request(request)
        .build()
    )

    # --------------------------------------------------------
    # Command
    # --------------------------------------------------------

    app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    # --------------------------------------------------------
    # Callback ها
    # --------------------------------------------------------

    app.add_handler(
        CallbackQueryHandler(
            delete_callback,
            pattern=r"^delete:\d+$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            confirm_delete_callback,
            pattern=r"^confirm_delete:\d+$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            cancel_delete_callback,
            pattern=r"^cancel_delete$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            edit_callback,
            pattern=r"^edit:\d+$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            manage_categories,
            pattern=r"^manage_categories$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            category_add_callback,
            pattern=r"^category_add$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            category_rename_callback,
            pattern=r"^category_rename$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            rename_select_callback,
            pattern=r"^rename_select:\d+$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            category_delete_callback,
            pattern=r"^category_delete$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            delete_category_callback,
            pattern=r"^delete_category:\d+$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            confirm_category_delete_callback,
            pattern=r"^confirm_category_delete:\d+$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            settings_menu_callback,
            pattern=r"^settings_menu$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            back_callback,
            pattern=r"^back_menu$",
        )
    )

    # --------------------------------------------------------
    # پیام‌های متنی
    # --------------------------------------------------------

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    # --------------------------------------------------------
    # Error handler
    # --------------------------------------------------------

    app.add_error_handler(
        error_handler
    )

    print("✅ ربات اجرا شد!")
    print("🤖 Waiting for Telegram updates...")

    # اجرای polling
    app.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# اجرای مستقیم
# ============================================================

if __name__ == "__main__":
    main()
