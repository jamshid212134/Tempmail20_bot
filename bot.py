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
from email_core import (
    create_email,
    extract_verification_code,
    extract_link_context,
    extract_all_links,
    generate_random_username,
    generate_random_password,
    BACKENDS,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

user_sessions: dict[int, dict] = {}
auto_fetch_jobs: dict[int, bool] = {}


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
            InlineKeyboardButton("🌐 سرویس‌ها", callback_data="domains"),
            InlineKeyboardButton("📖 راهنما", callback_data="help"),
        ],
    ])


WELCOME_MSG = """🎯 ربات ایمیل موقت حرفه‌ای

✨ با این ربات می‌توانید ایمیل موقت رایگان بسازید
و برای ثبت‌نام در سایت‌ها استفاده کنید.

🔒 ویژگی‌ها:
  • دریافت خودکار ایمیل تأیید
  • نمایش تمام لینک‌های ایمیل
  • چند سرویس ایمیل موقت
  • انتخاب خودکار بهترین سرویس

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

💡 نکته: ربات خودکار بهترین سرویس
ایمیل موقت را برای شما انتخاب می‌کند."""


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
    await show_creation_status(update, context, chat_id)


async def show_creation_status(update_or_msg, context, chat_id):
    if hasattr(update_or_msg, "message"):
        status_msg = await update_or_msg.message.reply_text("⏳ در حال ساخت ایمیل موقت...")
    else:
        status_msg = update_or_msg
    try:
        backend, address = await create_email()
        password = backend.password
        domain = address.split("@")[-1]
        set_session(chat_id, {
            "address": address,
            "password": password,
            "backend_name": backend.name,
            "backend": backend,
            "domain": domain,
            "message_count": 0,
        })
        auto_fetch_jobs[chat_id] = True
        success_msg = (
            "✅ ایمیل موقت شما ساخته شد!\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📧 آدرس:\n<code>{address}</code>\n\n"
            f"🔑 رمز:\n<code>{password}</code>\n\n"
            f"🌐 سرویس: {backend.name}\n"
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
        backend, address = await create_email(custom_username)
        password = backend.password
        domain = address.split("@")[-1]
        set_session(chat_id, {
            "address": address, "password": password,
            "backend_name": backend.name, "backend": backend,
            "domain": domain, "message_count": 0,
        })
        auto_fetch_jobs[chat_id] = True
        success_msg = (
            "✅ ایمیل موقت شما ساخته شد!\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📧 آدرس:\n<code>{address}</code>\n\n"
            f"🔑 رمز:\n<code>{password}</code>\n\n"
            f"🌐 سرویس: {backend.name}\n"
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
        backend = session.get("backend")
        if not backend:
            await msg.edit_text("⚠️ سرویس ایمیل یافت نشد. /newmail", reply_markup=build_main_menu_keyboard())
            return
        messages = await backend.get_messages()
        if not messages:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")],
                [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
            ])
            await msg.edit_text(
                "📭 صندوق خالی است.\n\n"
                f"📧 {session['address']}\n"
                f"🌐 {session['backend_name']}\n\n"
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
            sender = backend.extract_sender(m)
            subject = backend.extract_subject(m)
            created = backend.extract_date(m)
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
        backend = session.get("backend")
        if not backend:
            await update.message.reply_text("⚠️ سرویس ایمیل یافت نشد.")
            return
        msg_index = int(context.args[0]) - 1
        messages = await backend.get_messages()
        if not messages:
            await update.message.reply_text("📭 صندوق خالی است.")
            return
        if msg_index < 0 or msg_index >= len(messages):
            await update.message.reply_text(f"❌ شماره ۱ تا {len(messages)}")
            return
        msg_id = messages[msg_index]["id"]
        detail = await backend.get_message_detail(msg_id)
        await show_email_detail(update, detail, backend)
    except ValueError:
        await update.message.reply_text("❌ شماره نامعتبر.")
    except Exception as e:
        await update.message.reply_text(f"❌ خطا: {str(e)}")


async def show_email_detail(update_or_query, detail, backend=None):
    if backend:
        sender = backend.extract_sender(detail)
        subject = backend.extract_subject(detail)
        text = backend.extract_text(detail)
        created = backend.extract_date(detail)
    else:
        sender = detail.get("from", {}).get("address", "ناشناس") if isinstance(detail.get("from"), dict) else str(detail.get("from", "ناشناس"))
        subject = detail.get("subject", "(بدون موضوع)")
        text = detail.get("text") or detail.get("html") or ""
        created = detail.get("createdAt", "")[:10]

    code = extract_verification_code(text)
    links = extract_link_context(text)

    safe_sender = html_mod.escape(str(sender))
    safe_subject = html_mod.escape(str(subject))
    safe_text = html_mod.escape(str(text[:2000]))

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
        f"🌐 سرویس: {session.get('backend_name', 'نامشخص')}\n"
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
    msg = "🌐 سرویس‌های ایمیل موقت\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for i, b in enumerate(BACKENDS, 1):
        msg += f"  {i}. {b.name}\n"
    msg += f"\n━━━━━━━━━━━━━━━━━━━━\n"
    msg += "💡 ربات خودکار بهترین سرویس را انتخاب می‌کند."
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📧 ساخت ایمیل", callback_data="newmail")],
        [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
    ])
    await update.message.reply_text(msg, reply_markup=keyboard)


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
        backend = session.get("backend")
        if not backend:
            auto_fetch_jobs.pop(chat_id, None)
            continue
        try:
            messages = await backend.get_messages()
            current_count = len(messages)
            last_count = session.get("message_count", 0)
            if current_count > last_count:
                new_messages = messages[:current_count - last_count]
                session["message_count"] = current_count
                for m in new_messages:
                    sender = backend.extract_sender(m)
                    subject = backend.extract_subject(m)
                    msg_id = m["id"]
                    detail = await backend.get_message_detail(msg_id)
                    text = backend.extract_text(detail)
                    code = extract_verification_code(text)
                    links = extract_link_context(text)

                    safe_sender = html_mod.escape(str(sender))
                    safe_subject = html_mod.escape(str(subject))

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
        await show_creation_status_from_callback(query, context, chat_id)

    elif data == "delete_and_new":
        session = get_session(chat_id)
        if session:
            clear_session(chat_id)
            auto_fetch_jobs.pop(chat_id, None)
        await show_creation_status_from_callback(query, context, chat_id)

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
            backend = session.get("backend")
            if not backend:
                await query.edit_message_text("⚠️ سرویس ایمیل یافت نشد.")
                return
            messages = await backend.get_messages()
            if not messages:
                await query.edit_message_text("📭 خالی.")
                return
            detail = await backend.get_message_detail(messages[0]["id"])
            await show_email_detail_from_callback(query, detail, backend)
        else:
            msg_index = int(data.split("_")[1])
            session = get_session(chat_id)
            if not session:
                await query.edit_message_text("⚠️ ایمیل فعالی ندارید.")
                return
            backend = session.get("backend")
            if not backend:
                await query.edit_message_text("⚠️ سرویس ایمیل یافت نشد.")
                return
            try:
                messages = await backend.get_messages()
                if msg_index >= len(messages):
                    await query.edit_message_text("❌ یافت نشد.")
                    return
                detail = await backend.get_message_detail(messages[msg_index]["id"])
                await show_email_detail_from_callback(query, detail, backend)
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
        msg = "🌐 سرویس‌های ایمیل موقت\n━━━━━━━━━━━━━━━━━━━━\n\n"
        for i, b in enumerate(BACKENDS, 1):
            msg += f"  {i}. {b.name}\n"
        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n"
        msg += "💡 ربات خودکار بهترین سرویس را انتخاب می‌کند."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📧 ساخت ایمیل", callback_data="newmail")],
            [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
        ])
        await query.edit_message_text(msg, reply_markup=keyboard)

    elif data == "stop":
        auto_fetch_jobs[chat_id] = False
        await query.edit_message_text("✅ متوقف شد.", reply_markup=build_main_menu_keyboard())


async def show_creation_status_from_callback(query, context, chat_id):
    status_msg = await query.edit_message_text("⏳ در حال ساخت ایمیل...")
    try:
        backend, address = await create_email()
        password = backend.password
        domain = address.split("@")[-1]
        set_session(chat_id, {
            "address": address, "password": password,
            "backend_name": backend.name, "backend": backend,
            "domain": domain, "message_count": 0,
        })
        auto_fetch_jobs[chat_id] = True
        success_msg = (
            "✅ ایمیل موقت شما ساخته شد!\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📧 آدرس:\n<code>{address}</code>\n\n"
            f"🔑 رمز:\n<code>{password}</code>\n\n"
            f"🌐 سرویس: {backend.name}\n"
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
        backend = session.get("backend")
        if not backend:
            await query.edit_message_text("⚠️ سرویس ایمیل یافت نشد. /newmail", reply_markup=build_main_menu_keyboard())
            return
        messages = await backend.get_messages()
        if not messages:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")],
                [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
            ])
            await query.edit_message_text(
                f"📭 خالی.\n📧 {session['address']}\n🌐 {session['backend_name']}",
                reply_markup=keyboard,
            )
            return
        new_count = len(messages) - session.get("message_count", 0)
        session["message_count"] = len(messages)
        lines = [f"📬 {len(messages)} ایمیل:\n"]
        if new_count > 0:
            lines.insert(0, f"🔔 {new_count} جدید!\n")
        buttons = []
        for i, m in enumerate(messages[:10], 1):
            sender = backend.extract_sender(m)
            subject = backend.extract_subject(m)
            created = backend.extract_date(m)
            lines.append(f"{i}. 📩 {subject}\n   👤 {sender} | 📅 {created}")
            buttons.append([InlineKeyboardButton(f"📩 {subject[:35]}", callback_data=f"read_{i-1}")])
        buttons.append([InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")])
        buttons.append([InlineKeyboardButton("🏠 منو", callback_data="main_menu")])
        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        await query.edit_message_text(f"❌ خطا: {str(e)}", reply_markup=build_main_menu_keyboard())


async def show_email_detail_from_callback(query, detail, backend=None):
    if backend:
        sender = backend.extract_sender(detail)
        subject = backend.extract_subject(detail)
        text = backend.extract_text(detail)
        created = backend.extract_date(detail)
    else:
        sender = detail.get("from", {}).get("address", "ناشناس") if isinstance(detail.get("from"), dict) else str(detail.get("from", "ناشناس"))
        subject = detail.get("subject", "(بدون موضوع)")
        text = detail.get("text") or detail.get("html") or ""
        created = detail.get("createdAt", "")[:10]

    code = extract_verification_code(text)
    links = extract_link_context(text)

    safe_sender = html_mod.escape(str(sender))
    safe_subject = html_mod.escape(str(subject))
    safe_text = html_mod.escape(str(text[:2000]))

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
