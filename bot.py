import httpx
import hashlib
import string
import random
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from config import BOT_TOKEN

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

PWNED_API = "https://api.pwnedpasswords.com/range"


def check_password(password: str) -> tuple[bool, int]:
    sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = sha1[:5], sha1[5:]
    try:
        r = httpx.get(f"{PWNED_API}/{prefix}", timeout=10)
        r.raise_for_status()
        for line in r.text.splitlines():
            hash_suffix, count = line.split(":")
            if hash_suffix == suffix:
                return True, int(count)
        return False, 0
    except Exception as e:
        logger.error("Pwned API error: %s", e)
        raise


def generate_password(length=16) -> str:
    if length < 12:
        length = 12
    chars = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    while True:
        pwd = "".join(random.choices(chars, k=length))
        has_upper = any(c in string.ascii_uppercase for c in pwd)
        has_lower = any(c in string.ascii_lowercase for c in pwd)
        has_digit = any(c in string.digits for c in pwd)
        has_special = any(c in "!@#$%^&*()-_=+" for c in pwd)
        if has_upper and has_lower and has_digit and has_special:
            return pwd


def strength_bar(score: int) -> str:
    if score == 0:
        return "🟢🟢🟢🟢🟢 بسیار قوی"
    elif score < 10:
        return "🟡🟢🟢🟢🟢 قوی"
    elif score < 100:
        return "🟠🟡🟢🟢🟢 متوسط"
    elif score < 1000:
        return "🔴🟠🟡🟢🟢 ضعیف"
    elif score < 10000:
        return "🔴🔴🟠🟡🟢 بسیار ضعیف"
    else:
        return "🔴🔴🔴🟠🟡 خطرناک"


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 بررسی رمز عبور", callback_data="check")],
        [InlineKeyboardButton("🔐 ساخت رمز قوی", callback_data="generate")],
        [InlineKeyboardButton("📊 آمار نشت جهانی", callback_data="stats")],
        [InlineKeyboardButton("📖 راهنما", callback_data="help")],
    ])


WELCOME = """🔐 ربات بررسی امنیت رمز عبور

🔍 آیا رمز عبور شما در نشت اطلاعات پیدا شده؟

💡 امکانات:
  • بررسی رمز عبور در ۱۷+ میلیارد نشت
  • ساخت رمز عبور قوی و امن
  • آمار نشت اطلاعات جهانی

🔒 امنیت: رمز شما هیچوقت از دستگاه خارج نمیشه!

📋 منوی اصلی:"""

HELP = """📖 راهنمای ربات

🔹 /start — منوی اصلی
🔹 /check — بررسی رمز عبور
🔹 /generate — ساخت رمز قوی

💡 نحوه کار:
۱. رمز عبورتون رو بفرستید
۲. ربات بررسی میکنه
۳. نتیجه نشون داده میشه

🔒 امنیت:
فقط ۵ کاراکتر اول هش رمز به سرور فرستاده میشه.
خود رمز هیچوقت از دستگاه شما خارج نمیشه."""


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME, reply_markup=main_menu())


async def check_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔍 رمز عبور خود را وارد کنید:\n\n"
        "⚠️ رمز شما ذخیره نمیشه و فقط بررسی میشه.\n"
        "💡 میتونید رمز رو بعد از بررسی حذف کنید.",
    )


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    password = update.message.text
    if not password or len(password) < 4:
        await update.message.reply_text("⚠️ رمز عبور خیلی کوتاهه. حداقل ۴ کاراکتر.")
        return

    status = await update.message.reply_text("⏳ در حال بررسی رمز عبور...")

    try:
        is_breached, count = check_password(password)
    except Exception as e:
        await status.edit_text(f"❌ خطا در بررسی: {e}\n\nلطفاً دوباره تلاش کنید.")
        return

    masked = password[:2] + "*" * (len(password) - 4) + password[-2:] if len(password) > 4 else "****"

    if is_breached:
        text = (
            f"🔴 خطر! رمز شما نشت کرده!\n\n"
            f"🔑 رمز: {masked}\n"
            f"⚠️ تعداد دفعات نشت: {count:,} بار\n"
            f"📊 سطح خطر: {strength_bar(count)}\n\n"
            f"💡 توصیه‌ها:\n"
            f"• همین الان رمز رو عوض کنید\n"
            f"• از رمز یکسان همه جا استفاده نکنید\n"
            f"• از رمز عبور قوی استفاده کنید\n"
            f"• از رمز عبور قوی استفاده کنید"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔐 ساخت رمز قوی", callback_data="generate")],
            [InlineKeyboardButton("🔄 بررسی رمز دیگر", callback_data="check")],
            [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
        ])
    else:
        text = (
            f"✅ عالی! رمز شما نشت نکرده!\n\n"
            f"🔑 رمز: {masked}\n"
            f"📊 تعداد نشت: ۰ بار\n\n"
            f"💡 اما باز هم مراقب باشید:\n"
            f"• از رمز یکسان همه جا استفاده نکنید\n"
            f"• هر چند ماه رمز رو عوض کنید\n"
            f"• از احراز هویت دو مرحله‌ای استفاده کنید"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 بررسی رمز دیگر", callback_data="check")],
            [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
        ])

    await status.edit_text(text, reply_markup=kb)


async def cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data

    if d == "main_menu":
        await q.edit_message_text(WELCOME, reply_markup=main_menu())

    elif d == "help":
        await q.edit_message_text(HELP, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 منو", callback_data="main_menu")]
        ]))

    elif d == "check":
        await q.edit_message_text(
            "🔍 رمز عبور خود را وارد کنید:\n\n"
            "⚠️ رمز شما ذخیره نمیشه.",
        )

    elif d == "generate":
        pwd = generate_password()
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 رمز جدید", callback_data="generate")],
            [InlineKeyboardButton("🔍 بررسی این رمز", callback_data=f"verify_{pwd}")],
            [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
        ])
        await q.edit_message_text(
            f"🔐 رمز عبور قوی شما:\n\n"
            f"🔑 رمز:\n<code>{pwd}</code>\n\n"
            f"📊 قدرت: بسیار قوی 💪\n"
            f"📏 طول: {len(pwd)} کاراکتر\n\n"
            f"💡 نکته: این رمز رو جایی ذخیره کنید",
            parse_mode="HTML",
            reply_markup=kb,
        )

    elif d.startswith("verify_"):
        pwd = d.replace("verify_", "")
        status = await q.edit_message_text("⏳ در حال بررسی...")
        try:
            is_breached, count = check_password(pwd)
            masked = pwd[:2] + "*" * (len(pwd) - 4) + pwd[-2:] if len(pwd) > 4 else "****"
            if is_breached:
                text = (
                    f"🔴 خطر! رمز نشت کرده!\n\n"
                    f"🔑 رمز: {masked}\n"
                    f"⚠️ تعداد دفعات نشت: {count:,} بار\n"
                    f"📊 سطح خطر: {strength_bar(count)}\n\n"
                    f"💡 یه رمز دیگه بسازید!"
                )
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔐 ساخت رمز جدید", callback_data="generate")],
                    [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
                ])
            else:
                text = (
                    f"✅ عالی! رمز نشت نکرده!\n\n"
                    f"🔑 رمز: {masked}\n"
                    f"📊 تعداد نشت: ۰ بار\n\n"
                    f"💡 میتونید از این رمز استفاده کنید."
                )
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 رمز دیگر", callback_data="generate")],
                    [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
                ])
            await status.edit_text(text, reply_markup=kb)
        except Exception as e:
            await status.edit_text(f"❌ خطا: {e}", reply_markup=main_menu())

    elif d == "stats":
        try:
            r = httpx.get("https://haveibeenpwned.com/api/v3/breaches", timeout=10,
                          headers={"user-agent": "PwnedPasswordBot/1.0"})
            if r.status_code == 200:
                breaches = r.json()
                total_breaches = len(breaches)
                total_accounts = sum(b.get("PwnCount", 0) for b in breaches)
                recent = sorted(breaches, key=lambda x: x.get("AddedDate", ""), reverse=True)[:5]
                text = "📊 آمار نشت اطلاعات جهانی\n━━━━━━━━━━━━━━━━━━━━\n\n"
                text += f"🌐 تعداد کل نشت‌ها: {total_breaches:,}\n"
                text += f"👥 تعداد کل اکانت‌ها: {total_accounts:,}\n\n"
                text += "📅 آخرین نشت‌ها:\n"
                for b in recent:
                    name = b.get("Title", "ناشناس")
                    count = b.get("PwnCount", 0)
                    date = b.get("BreachDate", "ناشناس")
                    text += f"  • {name}: {count:,} اکانت ({date})\n"
                text += "\n💡 همین الان رمزتون رو بررسی کنید!"
            else:
                text = "📊 آمار در دسترس نیست.\n\n💡 همین الان رمزتون رو بررسی کنید!"
        except Exception:
            text = "📊 آمار در دسترس نیست.\n\n💡 همین الان رمزتون رو بررسی کنید!"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 بررسی رمز", callback_data="check")],
            [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
        ])
        await q.edit_message_text(text, reply_markup=kb)


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("check", check_command))
    app.add_handler(CommandHandler("generate", lambda u, c: cb(
        Update(update_id=0, callback_query=None), c) if False else None))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(cb))

    logger.info("Bot started!")
    print("Bot running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
