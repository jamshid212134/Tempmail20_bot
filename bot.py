import random
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
import email_core as ec

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
            InlineKeyboardButton("📖 راهنما", callback_data="help"),
        ],
    ])


WELCOME_MSG = """🎯 ربات ایمیل موقت حرفه‌ای

✨ ایمیل موقت رایگان با چند سرویس مختلف
برای ثبت‌نام در سایت‌ها و دریافت کد تأیید.

🔒 ویژگی‌ها:
  • چند سرویس ایمیل (خودکار انتخاب بهترین)
  • دریافت خودکار ایمیل تأیید
  • تشخیص خودکار کد و لینک
  • نمایش تمام لینک‌های ایمیل

📋 منوی اصلی:"""

HELP_MSG = """📖 راهنمای ربات

🔹 /start - منوی اصلی
🔹 /newmail - ساخت ایمیل تصادفی
🔹 /inbox - بررسی صندوق ورودی
🔹 /read <شماره> - خواندن ایمیل خاص
🔹 /stop - توقف دریافت خودکار

💡 نکته: بعد از ساخت ایمیل، آدرس رو
توی سایت مورد نظرت وارد کن. ایمیل‌ها
خودکار بررسی می‌شن."""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_MSG, reply_markup=build_main_menu_keyboard())


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
            "⚠️ ایمیل فعال دارید.\n"
            f"📧 {get_session(chat_id)['address']}\n\n"
            "حذف و جدید بسازید؟",
            reply_markup=keyboard,
        )
        return
    await _create_email(update, chat_id)


async def _create_email(update_or_query, chat_id):
    if hasattr(update_or_query, "message"):
        status_msg = await update_or_query.message.reply_text("⏳ در حال ساخت ایمیل...")
    else:
        status_msg = await update_or_query.edit_message_text("⏳ در حال ساخت ایمیل...")
    try:
        backend, address = await ec.create_email()
        set_session(chat_id, {
            "address": address,
            "password": backend.password,
            "backend": backend,
            "message_count": 0,
        })
        auto_fetch_jobs[chat_id] = True
        success_msg = (
            f"✅ ایمیل ساخته شد! ({backend.name})\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📧 آدرس:\n<code>{address}</code>\n\n"
            f"🔑 رمز:\n<code>{backend.password}</code>\n\n"
            "🔄 دریافت خودکار: فعال\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📌 آدرس رو توی سایت وارد کن.\n"
            "کد تأیید خودکار ارسال می‌شه."
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
        logger.error(f"Error creating email: {e}")
        await status_msg.edit_text(f"❌ خطا: {str(e)}", reply_markup=build_main_menu_keyboard())


async def inbox(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    if not session:
        await update.message.reply_text(
            "⚠️ ایمیل فعالی ندارید. /newmail",
            reply_markup=build_main_menu_keyboard(),
        )
        return
    await _show_inbox(update, chat_id, session)


async def _show_inbox(update_or_query, chat_id, session):
    if hasattr(update_or_query, "message"):
        msg = await update_or_query.message.reply_text("⏳ بررسی صندوق...")
    else:
        msg = await update_or_query.edit_message_text("⏳ بررسی صندوق...")
    try:
        backend = session["backend"]
        messages = await backend.get_messages()
        if not messages:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")],
                [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
            ])
            await msg.edit_text(
                f"📭 صندوق خالی است.\n\n📧 {session['address']}\n\n"
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
            buttons.append([InlineKeyboardButton(
                f"📩 {subject[:35]}", callback_data=f"read_{i-1}"
            )])
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
        backend = session["backend"]
        messages = await backend.get_messages()
        if not messages:
            await update.message.reply_text("📭 صندوق خالی است.")
            return
        if msg_index < 0 or msg_index >= len(messages):
            await update.message.reply_text(f"❌ شماره ۱ تا {len(messages)}")
            return
        detail = await backend.get_message_detail(messages[msg_index]["id"])
        await _show_email_detail(update, detail, backend)
    except ValueError:
        await update.message.reply_text("❌ شماره نامعتبر.")
    except Exception as e:
        await update.message.reply_text(f"❌ خطا: {str(e)}")


async def _show_email_detail(update_or_query, detail, backend):
    sender = backend.extract_sender(detail)
    subject = backend.extract_subject(detail)
    text = backend.extract_text(detail)
    created = backend.extract_date(detail)

    code = ec.extract_verification_code(text)
    links = ec.extract_link_context(text)

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
        await update.message.reply_text(
            "⚠️ ایمیل فعالی ندارید. /newmail",
            reply_markup=build_main_menu_keyboard(),
        )
        return
    msg = (
        "🔑 رمز ایمیل شما\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📧 آدرس:\n<code>{session['address']}</code>\n\n"
        f"🔑 رمز:\n<code>{session['password']}</code>\n\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(
        msg, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 منو", callback_data="main_menu")]
        ]),
    )


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    if not session:
        await update.message.reply_text(
            "⚠️ ایمیلی برای حذف نیست.",
            reply_markup=build_main_menu_keyboard(),
        )
        return
    await update.message.reply_text(
        f"⚠️ حذف ایمیل؟\n\n📧 {session['address']}",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ بله", callback_data="confirm_delete"),
                InlineKeyboardButton("❌ انصراف", callback_data="cancel"),
            ]
        ]),
    )


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    auto_fetch_jobs[chat_id] = False
    await update.message.reply_text(
        "✅ دریافت خودکار متوقف شد.",
        reply_markup=build_main_menu_keyboard(),
    )


async def _auto_check(context: ContextTypes.DEFAULT_TYPE):
    for chat_id, is_active in list(auto_fetch_jobs.items()):
        if not is_active:
            continue
        session = get_session(chat_id)
        if not session:
            auto_fetch_jobs.pop(chat_id, None)
            continue
        try:
            backend = session["backend"]
            messages = await backend.get_messages()
            current_count = len(messages)
            last_count = session.get("message_count", 0)

            if last_count == 0 and current_count > 0:
                session["message_count"] = current_count
                continue

            if current_count > last_count:
                new_messages = messages[:current_count - last_count]
                session["message_count"] = current_count
                for m in new_messages:
                    sender = backend.extract_sender(m)
                    subject = backend.extract_subject(m)
                    detail = await backend.get_message_detail(m["id"])
                    text = backend.extract_text(detail)
                    code = ec.extract_verification_code(text)
                    links = ec.extract_link_context(text)

                    safe_sender = html_mod.escape(sender)
                    safe_subject = html_mod.escape(subject)

                    notify = (
                        "📩 ایمیل جدید!\n"
                        "━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"👤 فرستنده: {safe_sender}\n"
                        f"📌 موضوع: {safe_subject}\n"
                    )

                    if code:
                        notify += f"\n🔑 کد تأیید: <code>{html_mod.escape(code)}</code>\n"

                    if links:
                        notify += "\n🔗 لینک‌ها:\n"
                        for i, (desc, link) in enumerate(links, 1):
                            notify += f"{i}. {desc}\n"

                    notify += "\n━━━━━━━━━━━━━━━━━━━━"

                    buttons = []
                    for i, (desc, link) in enumerate(links, 1):
                        buttons.append([InlineKeyboardButton(
                            f"🔗 {i}. {desc[:40]}", url=link
                        )])
                    buttons.append([InlineKeyboardButton(
                        "📩 خواندن ایمیل", callback_data="read_latest"
                    )])

                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=notify,
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(buttons),
                    )
        except Exception as e:
            logger.error(f"Auto-check error for chat {chat_id}: {e}")


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
            await query.edit_message_text(
                f"⚠️ ایمیل فعال دارید:\n📧 {session['address']}\n\nحذف و جدید بسازید؟",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🗑️ حذف و ساخت جدید", callback_data="delete_and_new"),
                        InlineKeyboardButton("❌ انصراف", callback_data="cancel"),
                    ]
                ]),
            )
            return
        await _create_email(query, chat_id)

    elif data == "delete_and_new":
        clear_session(chat_id)
        auto_fetch_jobs.pop(chat_id, None)
        await _create_email(query, chat_id)

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
        await _show_inbox_from_callback(query, chat_id, session)

    elif data.startswith("read_"):
        if data == "read_latest":
            session = get_session(chat_id)
            if not session:
                await query.edit_message_text("⚠️ ایمیل فعالی ندارید.")
                return
            backend = session["backend"]
            messages = await backend.get_messages()
            if not messages:
                await query.edit_message_text("📭 خالی.")
                return
            detail = await backend.get_message_detail(messages[0]["id"])
            await _show_email_detail_from_callback(query, detail, backend)
        else:
            msg_index = int(data.split("_")[1])
            session = get_session(chat_id)
            if not session:
                await query.edit_message_text("⚠️ ایمیل فعالی ندارید.")
                return
            try:
                backend = session["backend"]
                messages = await backend.get_messages()
                if msg_index >= len(messages):
                    await query.edit_message_text("❌ یافت نشد.")
                    return
                detail = await backend.get_message_detail(messages[msg_index]["id"])
                await _show_email_detail_from_callback(query, detail, backend)
            except Exception as e:
                await query.edit_message_text(f"❌ خطا: {str(e)}")

    elif data == "back_inbox":
        session = get_session(chat_id)
        if not session:
            await query.edit_message_text("⚠️ ایمیل فعالی ندارید.")
            return
        await _show_inbox_from_callback(query, chat_id, session)

    elif data == "refresh_inbox":
        session = get_session(chat_id)
        if not session:
            await query.edit_message_text("⚠️ ایمیل فعالی ندارید.")
            return
        await _show_inbox_from_callback(query, chat_id, session)

    elif data == "stop":
        auto_fetch_jobs[chat_id] = False
        await query.edit_message_text("✅ متوقف شد.", reply_markup=build_main_menu_keyboard())


async def _show_inbox_from_callback(query, chat_id, session):
    await query.edit_message_text("⏳ بررسی صندوق...")
    try:
        backend = session["backend"]
        messages = await backend.get_messages()
        if not messages:
            await query.edit_message_text(
                f"📭 خالی.\n📧 {session['address']}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")],
                    [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
                ]),
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


async def _show_email_detail_from_callback(query, detail, backend):
    sender = backend.extract_sender(detail)
    subject = backend.extract_subject(detail)
    text = backend.extract_text(detail)
    created = backend.extract_date(detail)

    code = ec.extract_verification_code(text)
    links = ec.extract_link_context(text)

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
    app.add_handler(CommandHandler("inbox", inbox))
    app.add_handler(CommandHandler("read", read))
    app.add_handler(CommandHandler("password", password))
    app.add_handler(CommandHandler("delete", delete))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CallbackQueryHandler(callback_handler))

    job_queue = app.job_queue
    job_queue.run_repeating(_auto_check, interval=10, first=5)

    logger.info("🚀 ربات شروع به کار کرد!")
    print("✅ ربات در حال اجراست...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
