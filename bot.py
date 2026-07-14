import random
import string
import re
import logging
import html as html_mod
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from config import BOT_TOKEN
import mailtm_client as mail

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BASE_URL = "https://api.mail.tm"
user_sessions: dict[int, dict] = {}
auto_fetch_jobs: dict[int, bool] = {}


def generate_random_username(length: int = 10) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def extract_verification_code(text: str) -> str | None:
    if not text:
        return None
    plain = html_mod.unescape(text)
    lines = plain.split("\n")
    keywords = [
        "verification code", "your code", "code is", "code:",
        "enter code", "use code", "otp code", "verification pin",
        "your pin", "pin is", "pin code",
        "کد تایید", "کد تأیید", "کد شما", "کد ورود", "کد فعال‌سازی",
    ]
    for line in lines:
        line = line.strip()
        if not line:
            continue
        lower_line = line.lower()
        for kw in keywords:
            if kw in lower_line:
                match = re.search(r"[:\s]+(\d{4,8})\s*$", line)
                if match:
                    return match.group(1)
                match = re.search(r"[:\s]+(\d{4,8})\s", line)
                if match:
                    return match.group(1)
    for line in lines:
        line = line.strip()
        if not line:
            continue
        match = re.fullmatch(r"\s*(\d{4,8})\s*", line)
        if match:
            return match.group(1)
    return None


def extract_link_context(text: str) -> list[tuple[str, str]]:
    if not text:
        return []
    plain = html_mod.unescape(text)
    lines = plain.split("\n")
    results = []
    seen = set()
    for line in lines:
        links_in_line = re.findall(r'(https?://[^\s<>"\']+)', line)
        for link in links_in_line:
            link = link.rstrip('.,;:!?')
            link = re.sub(r'[)}\]]+$', '', link)
            if link in seen:
                continue
            seen.add(link)
            desc = _clean_link_desc(line, link)
            results.append((desc, link))
    return results


def _clean_link_desc(line: str, link: str) -> str:
    desc = line.replace(link, "").strip()
    desc = re.sub(r'^[:\s\-–—]+', '', desc)
    desc = re.sub(r'[.:;\s]+$', '', desc)
    if len(desc) > 60:
        desc = desc[:57] + "..."
    if not desc:
        ll = link.lower()
        if "delete" in ll:
            return "🗑️ لینک حذف"
        elif "verify" in ll or "confirm" in ll:
            return "✅ لینک تأیید"
        elif "login" in ll or "log" in ll:
            return "🔑 لینک ورود"
        return "🔗 لینک"
    return desc


def get_session(chat_id: int) -> dict | None:
    return user_sessions.get(chat_id)


def set_session(chat_id: int, data: dict):
    user_sessions[chat_id] = data


def clear_session(chat_id: int):
    user_sessions.pop(chat_id, None)


def build_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📧 ساخت ایمیل جدید", callback_data="newmail"),
            InlineKeyboardButton("📬 صندوق ورودی", callback_data="inbox"),
        ],
        [
            InlineKeyboardButton("🔑 رمز عبور", callback_data="password"),
            InlineKeyboardButton("🗑️ حذف ایمیل", callback_data="delete"),
        ],
        [
            InlineKeyboardButton("🌐 دامنه‌ها", callback_data="domains"),
            InlineKeyboardButton("📖 راهنما", callback_data="help"),
        ],
    ])


WELCOME_MSG = """🎯 ربات ایمیل موقت حرفه‌ای

✨ با این ربات می‌توانید ایمیل موقت رایگان بسازید
و برای ثبت‌نام در سایت‌ها استفاده کنید.

🔒 ویژگی‌ها:
  • دریافت خودکار ایمیل تأیید
  • نمایش تمام لینک‌های ایمیل
  • دامنه‌های متنوع
  • تشخیص خودکار کد تأیید

📋 منوی اصلی:"""

HELP_MSG = """📖 راهنمای ربات

🔹 /start - منوی اصلی
🔹 /newmail - ساخت ایمیل تصادفی
🔹 /custom <نام> - ساخت با نام دلخواه
🔹 /inbox - بررسی صندوق ورودی
🔹 /read <شماره> - خواندن ایمیل خاص
🔹 /password - نمایش رمز عبور
🔹 /delete - حذف ایمیل فعلی
🔹 /stop - توقف دریافت خودکار

💡 نکته: ایمیل‌ها خودکار بررسی می‌شوند.
کد تأیید و لینک‌ها خودکار شناسایی می‌شوند."""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        WELCOME_MSG, reply_markup=build_main_menu_keyboard()
    )


async def newmail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if get_session(chat_id):
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🗑️ حذف و ساخت جدید", callback_data="delete_and_new"),
                InlineKeyboardButton("❌ انصراف", callback_data="cancel"),
            ]
        ])
        await update.message.reply_text(
            "⚠️ شما قبلاً یک ایمیل فعال دارید.\n\n"
            f"📧 {get_session(chat_id)['address']}\n\n"
            "آیا می‌خواهید آن را حذف و ایمیل جدید بسازید؟",
            reply_markup=keyboard,
        )
        return
    await show_creation_status(update, chat_id)


async def show_creation_status(update, chat_id):
    status_msg = await update.message.reply_text("⏳ در حال ساخت ایمیل موقت...")
    try:
        username = generate_random_username()
        password = mail.generate_random_password()
        domains = await mail.get_available_domains()
        if not domains:
            await status_msg.edit_text(
                "❌ خطا: دامنه‌ای موجود نیست.",
                reply_markup=build_main_menu_keyboard(),
            )
            return
        domain = random.choice(domains)
        address = f"{username}@{domain}"
        await mail.create_account(address, password)
        token = await mail.get_token(address, password)
        set_session(chat_id, {
            "address": address,
            "password": password,
            "token": token,
            "username": username,
            "domain": domain,
            "message_count": 0,
        })
        auto_fetch_jobs[chat_id] = True
        success_msg = (
            "✅ ایمیل موقت شما ساخته شد!\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📧 آدرس:\n<code>{address}</code>\n\n"
            f"🔑 رمز:\n<code>{password}</code>\n\n"
            "⏰ اعتبار: ۷ روز\n"
            "🔄 دریافت خودکار: فعال\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📌 در سایت ثبت‌نام کنید.\n"
            "ایمیل‌ها خودکار بررسی می‌شوند."
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📋 کپی آدرس", callback_data="copy_email"),
                InlineKeyboardButton("📋 کپی رمز", callback_data="copy_pass"),
            ],
            [
                InlineKeyboardButton("📬 صندوق ورودی", callback_data="inbox"),
                InlineKeyboardButton("🗑️ حذف", callback_data="delete"),
            ],
        ])
        await status_msg.edit_text(success_msg, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error: {e}")
        await status_msg.edit_text(f"❌ خطا: {str(e)}", reply_markup=build_main_menu_keyboard())


async def custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if get_session(chat_id):
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🗑️ حذف و ساخت جدید", callback_data="delete_and_new"),
                InlineKeyboardButton("❌ انصراف", callback_data="cancel"),
            ]
        ])
        await update.message.reply_text(
            "⚠️ شما قبلاً یک ایمیل فعال دارید.",
            reply_markup=keyboard,
        )
        return
    if not context.args:
        await update.message.reply_text(
            "📝 نام دلخواه: /custom myname",
            reply_markup=build_main_menu_keyboard(),
        )
        return
    custom_username = context.args[0].lower().strip()
    if not all(c.isalnum() or c in "-_." for c in custom_username):
        await update.message.reply_text("❌ نام نامعتبر است.", reply_markup=build_main_menu_keyboard())
        return
    status_msg = await update.message.reply_text("⏳ در حال ساخت ایمیل...")
    try:
        password = mail.generate_random_password()
        domains = await mail.get_available_domains()
        if not domains:
            await status_msg.edit_text("❌ خطا: دامنه‌ای موجود نیست.", reply_markup=build_main_menu_keyboard())
            return
        domain = random.choice(domains)
        address = f"{custom_username}@{domain}"
        await mail.create_account(address, password)
        token = await mail.get_token(address, password)
        set_session(chat_id, {
            "address": address, "password": password, "token": token,
            "username": custom_username, "domain": domain, "message_count": 0,
        })
        auto_fetch_jobs[chat_id] = True
        success_msg = (
            "✅ ایمیل موقت شما ساخته شد!\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📧 آدرس:\n<code>{address}</code>\n\n"
            f"🔑 رمز:\n<code>{password}</code>\n\n"
            "⏰ اعتبار: ۷ روز\n"
            "🔄 دریافت خودکار: فعال\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📌 در سایت ثبت‌نام کنید.\n"
            "ایمیل‌ها خودکار بررسی می‌شوند."
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📋 کپی آدرس", callback_data="copy_email"),
                InlineKeyboardButton("📋 کپی رمز", callback_data="copy_pass"),
            ],
            [
                InlineKeyboardButton("📬 صندوق ورودی", callback_data="inbox"),
                InlineKeyboardButton("🗑️ حذف", callback_data="delete"),
            ],
        ])
        await status_msg.edit_text(success_msg, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error: {e}")
        await status_msg.edit_text(f"❌ خطا: {str(e)}", reply_markup=build_main_menu_keyboard())


async def inbox(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    if not session:
        await update.message.reply_text("⚠️ ایمیل فعالی ندارید. /newmail", reply_markup=build_main_menu_keyboard())
        return
    await show_inbox(update, chat_id, session)


async def show_inbox(update_or_query, chat_id, session):
    if hasattr(update_or_query, "message"):
        msg = await update_or_query.message.reply_text("⏳ در حال بررسی صندوق...")
    else:
        msg = await update_or_query.edit_message_text("⏳ در حال بررسی صندوق...")
    try:
        messages = await mail.get_messages(session["token"])
        if not messages:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")],
                [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
            ])
            await msg.edit_text(
                "📭 صندوق خالی است.\n\n"
                f"📧 {session['address']}\n\n"
                "🔄 دریافت خودکار فعال است.",
                reply_markup=keyboard,
            )
            return
        new_count = len(messages) - session.get("message_count", 0)
        session["message_count"] = len(messages)
        lines = [f"📬 {len(messages)} ایمیل:\n"]
        if new_count > 0:
            lines.insert(0, f"🔔 {new_count} ایمیل جدید!\n")
        buttons = []
        for i, m in enumerate(messages[:10], 1):
            sender = m.get("from", {}).get("address", "ناشناس")
            subject = m.get("subject", "(بدون موضوع)")
            created = m.get("createdAt", "")[:10]
            lines.append(f"{i}. 📩 {subject}\n   👤 {sender} | 📅 {created}")
            buttons.append([InlineKeyboardButton(f"📩 {subject[:35]}", callback_data=f"read_{i-1}")])
        buttons.append([InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")])
        buttons.append([InlineKeyboardButton("🏠 منو", callback_data="main_menu")])
        await msg.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        logger.error(f"Inbox error: {e}")
        await msg.edit_text(f"❌ خطا: {str(e)}", reply_markup=build_main_menu_keyboard())


async def read(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    if not session:
        await update.message.reply_text("⚠️ ایمیل فعالی ندارید. /newmail")
        return
    if not context.args:
        await update.message.reply_text("📝 شماره: /read 1")
        return
    try:
        msg_index = int(context.args[0]) - 1
        messages = await mail.get_messages(session["token"])
        if not messages:
            await update.message.reply_text("📭 صندوق خالی است.")
            return
        if msg_index < 0 or msg_index >= len(messages):
            await update.message.reply_text(f"❌ شماره ۱ تا {len(messages)}")
            return
        msg_id = messages[msg_index]["id"]
        detail = await mail.get_message_detail(session["token"], msg_id)
        await show_email_detail(update, detail)
    except ValueError:
        await update.message.reply_text("❌ شماره نامعتبر.")
    except Exception as e:
        await update.message.reply_text(f"❌ خطا: {str(e)}")


async def show_email_detail(update_or_query, detail):
    sender = detail.get("from", {}).get("address", "ناشناس")
    subject = detail.get("subject", "(بدون موضوع)")
    text = detail.get("text") or detail.get("html") or ""
    created = detail.get("createdAt", "")[:16].replace("T", " ")

    code = extract_verification_code(text)
    links = extract_link_context(text)

    safe_sender = html_mod.escape(sender)
    safe_subject = html_mod.escape(subject)
    safe_text = html_mod.escape(text[:2000])

    response = (
        "📩 ایمیل دریافتی\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 فرستنده: {safe_sender}\n"
        f"📌 موضوع: {safe_subject}\n"
        f"📅 تاریخ: {created}\n"
    )

    if code:
        response += f"\n🔑 کد تأیید: <code>{html_mod.escape(code)}</code>\n"

    if links:
        response += f"\n🔗 لینک‌های موجود ({len(links)}):\n"
        for i, (desc, link) in enumerate(links, 1):
            response += f"\n{i}. {html_mod.escape(desc)}\n"

    response += (
        "\n📄 متن ایمیل:\n"
        "─────────────\n"
        f"{safe_text}\n"
        "─────────────"
    )

    buttons = []
    for i, (desc, link) in enumerate(links, 1):
        buttons.append([InlineKeyboardButton(f"🔗 {i}. {desc[:40]}", url=link)])
    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_inbox")])

    if hasattr(update_or_query, "message"):
        await update_or_query.message.reply_text(
            response, reply_markup=InlineKeyboardMarkup(buttons)
        )
    else:
        await update_or_query.edit_message_text(
            response, reply_markup=InlineKeyboardMarkup(buttons)
        )


async def password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    if not session:
        await update.message.reply_text("⚠️ ایمیل فعالی ندارید. /newmail", reply_markup=build_main_menu_keyboard())
        return
    msg = (
        "🔑 رمز ایمیل شما\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📧 آدرس:\n<code>{session['address']}</code>\n\n"
        f"🔑 رمز:\n<code>{session['password']}</code>\n\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
    ])
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=keyboard)


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    if not session:
        await update.message.reply_text("⚠️ ایمیلی برای حذف نیست.", reply_markup=build_main_menu_keyboard())
        return
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ بله", callback_data="confirm_delete"),
            InlineKeyboardButton("❌ انصراف", callback_data="cancel"),
        ]
    ])
    await update.message.reply_text(
        f"⚠️ حذف ایمیل؟\n\n📧 {session['address']}",
        reply_markup=keyboard,
    )


async def domains(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("⏳ دریافت دامنه‌ها...")
    try:
        domain_list = await mail.get_available_domains()
        if not domain_list:
            await status_msg.edit_text("❌ دامنه‌ای نیست.", reply_markup=build_main_menu_keyboard())
            return
        msg = "🌐 دامنه‌ها\n━━━━━━━━━━━━━━━━━━━━\n\n"
        for i, d in enumerate(domain_list, 1):
            msg += f"  {i}. {d}\n"
        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n📊 مجموع: {len(domain_list)}"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📧 ساخت ایمیل", callback_data="newmail")],
            [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
        ])
        await status_msg.edit_text(msg, reply_markup=keyboard)
    except Exception as e:
        await status_msg.edit_text(f"❌ خطا: {str(e)}", reply_markup=build_main_menu_keyboard())


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    auto_fetch_jobs[chat_id] = False
    await update.message.reply_text("✅ دریافت خودکار متوقف شد.", reply_markup=build_main_menu_keyboard())


async def auto_check_inbox(context: ContextTypes.DEFAULT_TYPE):
    for chat_id, is_active in list(auto_fetch_jobs.items()):
        if not is_active:
            continue
        session = get_session(chat_id)
        if not session:
            auto_fetch_jobs.pop(chat_id, None)
            continue
        try:
            messages = await mail.get_messages(session["token"])
            current_count = len(messages)
            last_count = session.get("message_count", 0)

            if last_count == 0 and current_count > 0:
                session["message_count"] = current_count
                continue

            if current_count > last_count:
                new_messages = messages[:current_count - last_count]
                session["message_count"] = current_count
                for m in new_messages:
                    sender = m.get("from", {}).get("address", "ناشناس")
                    subject = m.get("subject", "(بدون موضوع)")
                    detail = await mail.get_message_detail(session["token"], m["id"])
                    text = detail.get("text") or detail.get("html") or ""
                    code = extract_verification_code(text)
                    links = extract_link_context(text)

                    safe_sender = html_mod.escape(sender)
                    safe_subject = html_mod.escape(subject)

                    notify = (
                        "📩 ایمیل جدید دریافت شد!\n"
                        "━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"👤 فرستنده: {safe_sender}\n"
                        f"📌 موضوع: {safe_subject}\n"
                    )

                    if code:
                        notify += f"\n🔑 کد تأیید: <code>{html_mod.escape(code)}</code>\n"

                    if links:
                        notify += f"\n🔗 لینک‌ها ({len(links)}):\n"
                        for i, (desc, link) in enumerate(links, 1):
                            notify += f"{i}. {desc}\n"

                    notify += "\n━━━━━━━━━━━━━━━━━━━━\n💡 لینک مورد نظر را انتخاب کنید:"

                    buttons = []
                    for i, (desc, link) in enumerate(links, 1):
                        buttons.append([InlineKeyboardButton(f"🔗 {i}. {desc[:40]}", url=link)])
                    buttons.append([InlineKeyboardButton("📩 خواندن ایمیل", callback_data="read_latest")])

                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=notify,
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(buttons),
                    )
        except Exception as e:
            logger.error(f"Auto-check error: {e}")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id

    if data == "main_menu":
        await query.edit_message_text(WELCOME_MSG, reply_markup=build_main_menu_keyboard())

    elif data == "help":
        await query.edit_message_text(HELP_MSG, reply_markup=build_main_menu_keyboard())

    elif data == "newmail":
        session = get_session(chat_id)
        if session:
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🗑️ حذف و ساخت جدید", callback_data="delete_and_new"),
                    InlineKeyboardButton("❌ انصراف", callback_data="cancel"),
                ]
            ])
            await query.edit_message_text(
                f"⚠️ ایمیل فعال دارید:\n📧 {session['address']}\n\nحذف و جدید بسازید؟",
                reply_markup=keyboard,
            )
            return
        await show_creation_status_from_callback(query, chat_id)

    elif data == "delete_and_new":
        session = get_session(chat_id)
        if session:
            try:
                await mail.delete_account(session["token"])
            except Exception:
                pass
            clear_session(chat_id)
            auto_fetch_jobs.pop(chat_id, None)
        await show_creation_status_from_callback(query, chat_id)

    elif data == "copy_email":
        session = get_session(chat_id)
        if session:
            await query.answer(f"📧 {session['address']}", show_alert=True)
        else:
            await query.answer("⚠️ ایمیلی نیست", show_alert=True)

    elif data == "copy_pass":
        session = get_session(chat_id)
        if session:
            await query.answer(f"🔑 {session['password']}", show_alert=True)
        else:
            await query.answer("⚠️ ایمیلی نیست", show_alert=True)

    elif data == "cancel":
        await query.edit_message_text("✅ لغو شد.", reply_markup=build_main_menu_keyboard())

    elif data == "confirm_delete":
        session = get_session(chat_id)
        if session:
            try:
                await mail.delete_account(session["token"])
            except Exception:
                pass
            clear_session(chat_id)
            auto_fetch_jobs.pop(chat_id, None)
            await query.edit_message_text("✅ حذف شد.", reply_markup=build_main_menu_keyboard())
        else:
            await query.edit_message_text("⚠️ ایمیلی نبود.", reply_markup=build_main_menu_keyboard())

    elif data == "inbox":
        session = get_session(chat_id)
        if not session:
            await query.edit_message_text("⚠️ ایمیل فعالی ندارید.", reply_markup=build_main_menu_keyboard())
            return
        await show_inbox_from_callback(query, chat_id, session)

    elif data.startswith("read_"):
        if data == "read_latest":
            session = get_session(chat_id)
            if not session:
                await query.edit_message_text("⚠️ ایمیل فعالی ندارید.")
                return
            messages = await mail.get_messages(session["token"])
            if not messages:
                await query.edit_message_text("📭 خالی.")
                return
            detail = await mail.get_message_detail(session["token"], messages[0]["id"])
            await show_email_detail_from_callback(query, detail)
        else:
            msg_index = int(data.split("_")[1])
            session = get_session(chat_id)
            if not session:
                await query.edit_message_text("⚠️ ایمیل فعالی ندارید.")
                return
            try:
                messages = await mail.get_messages(session["token"])
                if msg_index >= len(messages):
                    await query.edit_message_text("❌ یافت نشد.")
                    return
                detail = await mail.get_message_detail(session["token"], messages[msg_index]["id"])
                await show_email_detail_from_callback(query, detail)
            except Exception as e:
                await query.edit_message_text(f"❌ خطا: {str(e)}")

    elif data == "back_inbox":
        session = get_session(chat_id)
        if not session:
            await query.edit_message_text("⚠️ ایمیل فعالی ندارید.")
            return
        await show_inbox_from_callback(query, chat_id, session)

    elif data == "refresh_inbox":
        session = get_session(chat_id)
        if not session:
            await query.edit_message_text("⚠️ ایمیل فعالی ندارید.")
            return
        await show_inbox_from_callback(query, chat_id, session)

    elif data == "domains":
        try:
            domain_list = await mail.get_available_domains()
            if not domain_list:
                await query.edit_message_text("❌ دامنه‌ای نیست.", reply_markup=build_main_menu_keyboard())
                return
            msg = "🌐 دامنه‌ها\n━━━━━━━━━━━━━━━━━━━━\n\n"
            for i, d in enumerate(domain_list, 1):
                msg += f"  {i}. {d}\n"
            msg += f"\n━━━━━━━━━━━━━━━━━━━━\n📊 مجموع: {len(domain_list)}"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📧 ساخت ایمیل", callback_data="newmail")],
                [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
            ])
            await query.edit_message_text(msg, reply_markup=keyboard)
        except Exception as e:
            await query.edit_message_text(f"❌ خطا: {str(e)}", reply_markup=build_main_menu_keyboard())

    elif data == "stop":
        auto_fetch_jobs[chat_id] = False
        await query.edit_message_text("✅ متوقف شد.", reply_markup=build_main_menu_keyboard())


async def show_creation_status_from_callback(query, chat_id):
    status_msg = await query.edit_message_text("⏳ در حال ساخت ایمیل...")
    try:
        username = generate_random_username()
        password = mail.generate_random_password()
        domains = await mail.get_available_domains()
        if not domains:
            await status_msg.edit_text("❌ دامنه‌ای نیست.", reply_markup=build_main_menu_keyboard())
            return
        domain = random.choice(domains)
        address = f"{username}@{domain}"
        await mail.create_account(address, password)
        token = await mail.get_token(address, password)
        set_session(chat_id, {
            "address": address, "password": password, "token": token,
            "username": username, "domain": domain, "message_count": 0,
        })
        auto_fetch_jobs[chat_id] = True
        success_msg = (
            "✅ ایمیل موقت شما ساخته شد!\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📧 آدرس:\n<code>{address}</code>\n\n"
            f"🔑 رمز:\n<code>{password}</code>\n\n"
            "⏰ اعتبار: ۷ روز\n"
            "🔄 دریافت خودکار: فعال\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📌 در سایت ثبت‌نام کنید.\n"
            "ایمیل‌ها خودکار بررسی می‌شوند."
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📋 کپی آدرس", callback_data="copy_email"),
                InlineKeyboardButton("📋 کپی رمز", callback_data="copy_pass"),
            ],
            [
                InlineKeyboardButton("📬 صندوق ورودی", callback_data="inbox"),
                InlineKeyboardButton("🗑️ حذف", callback_data="delete"),
            ],
        ])
        await status_msg.edit_text(success_msg, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        await status_msg.edit_text(f"❌ خطا: {str(e)}", reply_markup=build_main_menu_keyboard())


async def show_inbox_from_callback(query, chat_id, session):
    await query.edit_message_text("⏳ در حال بررسی...")
    try:
        messages = await mail.get_messages(session["token"])
        if not messages:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")],
                [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
            ])
            await query.edit_message_text(
                f"📭 خالی.\n📧 {session['address']}", reply_markup=keyboard,
            )
            return
        new_count = len(messages) - session.get("message_count", 0)
        session["message_count"] = len(messages)
        lines = [f"📬 {len(messages)} ایمیل:\n"]
        if new_count > 0:
            lines.insert(0, f"🔔 {new_count} جدید!\n")
        buttons = []
        for i, m in enumerate(messages[:10], 1):
            sender = m.get("from", {}).get("address", "ناشناس")
            subject = m.get("subject", "(بدون موضوع)")
            created = m.get("createdAt", "")[:10]
            lines.append(f"{i}. 📩 {subject}\n   👤 {sender} | 📅 {created}")
            buttons.append([InlineKeyboardButton(f"📩 {subject[:35]}", callback_data=f"read_{i-1}")])
        buttons.append([InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")])
        buttons.append([InlineKeyboardButton("🏠 منو", callback_data="main_menu")])
        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        await query.edit_message_text(f"❌ خطا: {str(e)}", reply_markup=build_main_menu_keyboard())


async def show_email_detail_from_callback(query, detail):
    sender = detail.get("from", {}).get("address", "ناشناس")
    subject = detail.get("subject", "(بدون موضوع)")
    text = detail.get("text") or detail.get("html") or ""
    created = detail.get("createdAt", "")[:16].replace("T", " ")

    code = extract_verification_code(text)
    links = extract_link_context(text)

    safe_sender = html_mod.escape(sender)
    safe_subject = html_mod.escape(subject)
    safe_text = html_mod.escape(text[:2000])

    response = (
        "📩 ایمیل دریافتی\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 فرستنده: {safe_sender}\n"
        f"📌 موضوع: {safe_subject}\n"
        f"📅 تاریخ: {created}\n"
    )

    if code:
        response += f"\n🔑 کد تأیید: <code>{html_mod.escape(code)}</code>\n"

    if links:
        response += f"\n🔗 لینک‌ها ({len(links)}):\n"
        for i, (desc, link) in enumerate(links, 1):
            response += f"\n{i}. {html_mod.escape(desc)}\n"

    response += (
        "\n📄 متن ایمیل:\n"
        "─────────────\n"
        f"{safe_text}\n"
        "─────────────"
    )

    buttons = []
    for i, (desc, link) in enumerate(links, 1):
        buttons.append([InlineKeyboardButton(f"🔗 {i}. {desc[:40]}", url=link)])
    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_inbox")])

    await query.edit_message_text(response, reply_markup=InlineKeyboardMarkup(buttons))


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("newmail", newmail))
    app.add_handler(CommandHandler("custom", custom))
    app.add_handler(CommandHandler("inbox", inbox))
    app.add_handler(CommandHandler("read", read))
    app.add_handler(CommandHandler("password", password))
    app.add_handler(CommandHandler("delete", delete))
    app.add_handler(CommandHandler("domains", domains))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CallbackQueryHandler(callback_handler))

    job_queue = app.job_queue
    job_queue.run_repeating(auto_check_inbox, interval=10, first=5)

    logger.info("🚀 ربات شروع به کار کرد!")
    print("✅ ربات در حال اجراست...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
