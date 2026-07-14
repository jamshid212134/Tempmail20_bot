import httpx
import re
import logging
import html as html_mod
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from config import BOT_TOKEN

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

GUERRILLA_URL = "https://api.guerrillamail.com/ajax.php"
user_sessions: dict[int, dict] = {}
auto_fetch_jobs: dict[int, bool] = {}


def extract_verification_code(text: str) -> str | None:
    if not text:
        return None
    plain = html_mod.unescape(text)
    lines = plain.split("\n")
    keywords = [
        "verification code", "your code", "code is", "code:",
        "enter code", "use code", "otp code", "launch code",
        "کد تایید", "کد تأیید", "کد شما", "کد ورود",
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


async def guerrilla_create() -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{GUERRILLA_URL}?f=get_email_address&ip=127.0.0.1&agent=Python"
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "address": data.get("email_addr", ""),
            "sid_token": data.get("sid_token", ""),
            "seq": data.get("seq", 0),
        }


async def guerrilla_check(sid_token: str, seq: int = 0) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{GUERRILLA_URL}?f=check_email&ip=127.0.0.1&sid_token={sid_token}&seq={seq}",
        )
        resp.raise_for_status()
        data = resp.json()
        emails = data.get("list", [])
        result = []
        for e in emails:
            result.append({
                "id": e.get("mail_id"),
                "sender": e.get("mail_from", ""),
                "subject": e.get("mail_subject", ""),
                "body": e.get("mail_body", ""),
                "date": e.get("mail_date", ""),
            })
        return {"messages": result, "new_seq": data.get("seq", seq)}


async def guerrilla_fetch(sid_token: str, email_id: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{GUERRILLA_URL}?f=fetch_email&ip=127.0.0.1&sid_token={sid_token}&email_id={email_id}",
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "sender": data.get("mail_from", ""),
            "subject": data.get("mail_subject", ""),
            "body": data.get("mail_body", ""),
            "date": data.get("mail_date", ""),
        }


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
            InlineKeyboardButton("📖 راهنما", callback_data="help"),
        ],
    ])


WELCOME_MSG = """🎯 ربات ایمیل موقت حرفه‌ای

✨ با این ربات می‌توانید ایمیل موقت رایگان بسازید
و برای ثبت‌نام در سایت‌ها استفاده کنید.

🔒 ویژگی‌ها:
  • دریافت خودکار ایمیل تأیید
  • نمایش تمام لینک‌های ایمیل
  • انتخاب خودکار دامنه

📋 منوی اصلی:"""

HELP_MSG = """📖 راهنمای ربات

🔹 /start - منوی اصلی
🔹 /newmail - ساخت ایمیل جدید
🔹 /inbox - بررسی صندوق ورودی
🔹 /stop - توقف دریافت خودکار

💡 نکته: بعد از دریافت ایمیل، تمام لینک‌ها
نمایش داده می‌شوند تا خودتان انتخاب کنید."""


def format_new_email(sender: str, subject: str, code: str | None, links: list) -> str:
    safe_sender = html_mod.escape(str(sender))
    safe_subject = html_mod.escape(str(subject))

    msg = (
        "📩 ایمیل جدید دریافت شد!\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 فرستنده: {safe_sender}\n"
        f"📌 موضوع: {safe_subject}\n"
    )

    if code:
        msg += f"\n🔑 کد تأیید: <code>{html_mod.escape(code)}</code>\n"

    if links:
        msg += f"\n🔗 لینک‌ها ({len(links)}):\n"
        for i, (desc, link) in enumerate(links, 1):
            msg += f"{i}. {desc}\n"

    msg += "\n━━━━━━━━━━━━━━━━━━━━\n💡 لینک مورد نظر را انتخاب کنید:"
    return msg


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
    await create_new_email(update.message, chat_id)


async def create_new_email(msg, chat_id):
    status_msg = await msg.reply_text("⏳ در حال ساخت ایمیل موقت...")
    try:
        data = await guerrilla_create()
        address = data["address"]
        sid_token = data["sid_token"]
        seq = data.get("seq", 0)
        set_session(chat_id, {
            "address": address,
            "sid_token": sid_token,
            "seq": seq,
            "message_count": 0,
        })
        auto_fetch_jobs[chat_id] = True
        success_msg = (
            "✅ ایمیل موقت شما ساخته شد!\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📧 آدرس:\n<code>{address}</code>\n\n"
            "🔄 دریافت خودکار: فعال\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📌 در سایت ثبت‌نام کنید.\n"
            "ایمیل‌ها خودکار بررسی می‌شوند."
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📋 کپی آدرس", callback_data="copy_email"),
                InlineKeyboardButton("📬 صندوق ورودی", callback_data="inbox"),
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
    await show_inbox(update.message, chat_id, session)


async def show_inbox(msg, chat_id, session):
    status_msg = await msg.reply_text("⏳ در حال بررسی صندوق...")
    try:
        result = await guerrilla_check(session["sid_token"], session.get("seq", 0))
        messages = result["messages"]
        session["seq"] = result["new_seq"]
        if not messages:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")],
                [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
            ])
            await status_msg.edit_text(
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
            sender = m.get("sender", "ناشناس")
            subject = m.get("subject", "(بدون موضوع)")
            created = m.get("date", "")[:10]
            lines.append(f"{i}. 📩 {subject}\n   👤 {sender} | 📅 {created}")
            buttons.append([InlineKeyboardButton(f"📩 {subject[:35]}", callback_data=f"read_{i-1}")])
        buttons.append([InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")])
        buttons.append([InlineKeyboardButton("🏠 منو", callback_data="main_menu")])
        await status_msg.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        logger.error(f"Inbox error: {e}")
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
            result = await guerrilla_check(session["sid_token"], session.get("seq", 0))
            messages = result["messages"]
            session["seq"] = result["new_seq"]
            current_count = len(messages)
            last_count = session.get("message_count", 0)
            if current_count > last_count:
                new_messages = messages[:current_count - last_count]
                session["message_count"] = current_count
                for m in new_messages:
                    detail = await guerrilla_fetch(session["sid_token"], m["id"])
                    sender = detail.get("sender", "ناشناس")
                    subject = detail.get("subject", "(بدون موضوع)")
                    text = detail.get("body", "")
                    code = extract_verification_code(text)
                    links = extract_link_context(text)

                    notify = format_new_email(sender, subject, code, links)

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
        await create_new_email_callback(query, chat_id)

    elif data == "delete_and_new":
        clear_session(chat_id)
        auto_fetch_jobs.pop(chat_id, None)
        await create_new_email_callback(query, chat_id)

    elif data == "copy_email":
        session = get_session(chat_id)
        if session:
            await query.answer(f"📧 {session['address']}", show_alert=True)
        else:
            await query.answer("⚠️ ایمیلی نیست", show_alert=True)

    elif data == "cancel":
        await query.edit_message_text("✅ لغو شد.", reply_markup=build_main_menu_keyboard())

    elif data == "inbox":
        session = get_session(chat_id)
        if not session:
            await query.edit_message_text("⚠️ ایمیل فعالی ندارید.", reply_markup=build_main_menu_keyboard())
            return
        await show_inbox_callback(query, chat_id, session)

    elif data.startswith("read_"):
        session = get_session(chat_id)
        if not session:
            await query.edit_message_text("⚠️ ایمیل فعالی ندارید.")
            return
        try:
            result = await guerrilla_check(session["sid_token"], session.get("seq", 0))
            messages = result["messages"]
            session["seq"] = result["new_seq"]
            if data == "read_latest":
                if not messages:
                    await query.edit_message_text("📭 خالی.")
                    return
                detail = await guerrilla_fetch(session["sid_token"], messages[0]["id"])
            else:
                msg_index = int(data.split("_")[1])
                if msg_index >= len(messages):
                    await query.edit_message_text("❌ یافت نشد.")
                    return
                detail = await guerrilla_fetch(session["sid_token"], messages[msg_index]["id"])
            await show_email_detail_callback(query, detail)
        except Exception as e:
            await query.edit_message_text(f"❌ خطا: {str(e)}")

    elif data == "back_inbox":
        session = get_session(chat_id)
        if not session:
            await query.edit_message_text("⚠️ ایمیل فعالی ندارید.")
            return
        await show_inbox_callback(query, chat_id, session)

    elif data == "refresh_inbox":
        session = get_session(chat_id)
        if not session:
            await query.edit_message_text("⚠️ ایمیل فعالی ندارید.")
            return
        await show_inbox_callback(query, chat_id, session)

    elif data == "stop":
        auto_fetch_jobs[chat_id] = False
        await query.edit_message_text("✅ متوقف شد.", reply_markup=build_main_menu_keyboard())


async def create_new_email_callback(query, chat_id):
    status_msg = await query.edit_message_text("⏳ در حال ساخت ایمیل...")
    try:
        data = await guerrilla_create()
        address = data["address"]
        sid_token = data["sid_token"]
        seq = data.get("seq", 0)
        set_session(chat_id, {
            "address": address,
            "sid_token": sid_token,
            "seq": seq,
            "message_count": 0,
        })
        auto_fetch_jobs[chat_id] = True
        success_msg = (
            "✅ ایمیل موقت شما ساخته شد!\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📧 آدرس:\n<code>{address}</code>\n\n"
            "🔄 دریافت خودکار: فعال\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📌 در سایت ثبت‌نام کنید.\n"
            "ایمیل‌ها خودکار بررسی می‌شوند."
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📋 کپی آدرس", callback_data="copy_email"),
                InlineKeyboardButton("📬 صندوق ورودی", callback_data="inbox"),
            ],
        ])
        await status_msg.edit_text(success_msg, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        await status_msg.edit_text(f"❌ خطا: {str(e)}", reply_markup=build_main_menu_keyboard())


async def show_inbox_callback(query, chat_id, session):
    await query.edit_message_text("⏳ در حال بررسی...")
    try:
        result = await guerrilla_check(session["sid_token"], session.get("seq", 0))
        messages = result["messages"]
        session["seq"] = result["new_seq"]
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
            sender = m.get("sender", "ناشناس")
            subject = m.get("subject", "(بدون موضوع)")
            created = m.get("date", "")[:10]
            lines.append(f"{i}. 📩 {subject}\n   👤 {sender} | 📅 {created}")
            buttons.append([InlineKeyboardButton(f"📩 {subject[:35]}", callback_data=f"read_{i-1}")])
        buttons.append([InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")])
        buttons.append([InlineKeyboardButton("🏠 منو", callback_data="main_menu")])
        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        await query.edit_message_text(f"❌ خطا: {str(e)}", reply_markup=build_main_menu_keyboard())


async def show_email_detail_callback(query, detail):
    sender = detail.get("sender", "ناشناس")
    subject = detail.get("subject", "(بدون موضوع)")
    text = detail.get("body", "")
    created = detail.get("date", "")

    code = extract_verification_code(text)
    links = extract_link_context(text)

    safe_sender = html_mod.escape(str(sender))
    safe_subject = html_mod.escape(str(subject))
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
    app.add_handler(CommandHandler("inbox", inbox))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CallbackQueryHandler(callback_handler))

    job_queue = app.job_queue
    job_queue.run_repeating(auto_check_inbox, interval=10, first=5)

    logger.info("🚀 ربات شروع به کار کرد!")
    print("✅ ربات در حال اجراست...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
