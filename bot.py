import httpx
import re
import logging
import html as html_mod
import time
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

user_sessions: dict[int, dict] = {}
auto_fetch_jobs: dict[int, bool] = {}

GUERRILLA = "https://api.guerrillamail.com/ajax.php"


def strip_html(text):
    if not text:
        return ""
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<p[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html_mod.unescape(text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def extract_code(text):
    if not text:
        return None
    plain = strip_html(text)
    patterns = [
        r"(?:verification|confirm|activate|launch|otp|pin)\s*(?:code|number)?\s*[:\s]+(\d{4,8})",
        r"(?:your|the)\s+code\s+is\s*[:\s]+(\d{4,8})",
        r"code\s*:\s*(\d{4,8})",
        r"کد\s+(?:تأ?یید|شما|ورود|فعال)\s*[:\s]+(\d{4,8})",
        r">\s*(\d{4,8})\s*<",
    ]
    for p in patterns:
        m = re.search(p, plain, re.IGNORECASE)
        if m:
            return m.group(1)
    for line in plain.split("\n"):
        m = re.fullmatch(r'\s*(\d{4,8})\s*', line.strip())
        if m:
            return m.group(1)
    m = re.search(r'\b(\d{6})\b', plain)
    if m:
        return m.group(1)
    return None


def extract_verify_link(text):
    if not text:
        return None
    plain = strip_html(text)
    seen = set()
    for line in plain.split("\n"):
        for link in re.findall(r'(https?://[^\s<>"\']+)', line):
            link = link.rstrip('.,;:!?')
            link = re.sub(r'[)}\]]+$', '', link)
            if link in seen:
                continue
            seen.add(link)
            ll = link.lower()
            if any(kw in ll for kw in ["verify", "confirm", "activation", "activate", "valid", "approve", "token"]):
                return link
    for line in plain.split("\n"):
        for link in re.findall(r'(https?://[^\s<>"\']+)', line):
            link = link.rstrip('.,;:!?')
            link = re.sub(r'[)}\]]+$', '', link)
            if link not in seen:
                return link
    return None


async def guerrilla_create():
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{GUERRILLA}?f=get_email_address&ip=127.0.0.1&agent=Python")
        r.raise_for_status()
        d = r.json()
        return d["email_addr"], d["sid_token"], 0


async def guerrilla_check(sid, seq=0):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{GUERRILLA}?f=check_email&ip=127.0.0.1&sid_token={sid}&seq={seq}")
        if r.status_code == 429:
            logger.warning("GuerrillaMail 429")
            return [], seq
        r.raise_for_status()
        d = r.json()
        return d.get("list", []), d.get("seq", seq)


async def guerrilla_fetch(sid, eid):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{GUERRILLA}?f=fetch_email&ip=127.0.0.1&sid_token={sid}&email_id={eid}")
        r.raise_for_status()
        d = r.json()
        return {
            "sender": d.get("mail_from", ""),
            "subject": d.get("mail_subject", ""),
            "body": d.get("mail_body", ""),
            "html": d.get("mail_body", ""),
            "date": d.get("mail_date", ""),
        }


async def guerrilla_set_email(sid, addr):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{GUERRILLA}?f=set_email_user&ip=127.0.0.1&sid_token={sid}&email_user={addr}")
        r.raise_for_status()
        return r.json()


def get_s(cid):
    return user_sessions.get(cid)


def set_s(cid, data):
    user_sessions[cid] = data


def clear_s(cid):
    user_sessions.pop(cid, None)


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📧 ساخت ایمیل جدید", callback_data="newmail")],
        [InlineKeyboardButton("📬 صندوق ورودی", callback_data="inbox"),
         InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")],
        [InlineKeyboardButton("🗑️ حذف ایمیل", callback_data="delete")],
    ])


WELCOME = """🎯 ربات ایمیل موقت

📧 ایمیل موقت بسازید و برای ثبت‌نام استفاده کنید.
🔄 کد تأیید و لینک فعال‌سازی خودکار دریافت میشه.

💡 منوی اصلی:"""

HELP = """📖 راهنما

🔹 /start — منوی اصلی
🔹 /newmail — ساخت ایمیل جدید
🔹 /inbox — بررسی صندوق ورودی

💡 هر ۳۰ ثانیه صندوق چک میشه."""


def fmt_detail(detail):
    body = detail.get("body", "")
    html = detail.get("html", "")
    content = body if body.strip() else html
    code = extract_code(content)
    link = extract_verify_link(content)
    plain = strip_html(content)[:2000]

    sender = html_mod.escape(str(detail.get("sender", "ناشناس")))
    subject = html_mod.escape(str(detail.get("subject", "")))
    date = detail.get("date", "")

    msg = f"📩 {subject}\n👤 {sender}\n📅 {date}\n\n"
    if code:
        msg += f"🔑 کد تأیید: {code}\n"
    if link:
        msg += f"🔗 لینک تأیید:\n{link}\n"
    msg += f"\n📄 متن:\n{plain}"

    buttons = []
    if code:
        buttons.append([InlineKeyboardButton(f"📋 کپی کد: {code}", callback_data=f"copy_{code}")])
    if link:
        buttons.append([InlineKeyboardButton("🔗 باز کردن لینک", url=link)])
    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_inbox")])
    return msg, InlineKeyboardMarkup(buttons)


async def do_create(msg, cid):
    status = await msg.reply_text("⏳ در حال ساخت ایمیل...")
    try:
        addr, sid, seq = await guerrilla_create()
        set_s(cid, {
            "backend": "guerrillamail",
            "address": addr,
            "sid": sid,
            "seq": seq,
            "count": 0,
        })
        auto_fetch_jobs[cid] = True
        text = (
            f"✅ ایمیل موقت ساخته شد!\n\n"
            f"📧 آدرس ایمیل:\n"
            f"<code>{addr}</code>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 مراحل کار:\n\n"
            f"۱. آدرس بالا رو کپی کنید\n"
            f"۲. در سایت مورد نظر ثبت‌نام کنید\n"
            f"۳. ایمیل تأیید ارسال میشه\n"
            f"۴. ربات خودکار کد/لینک رو میفرسته\n\n"
            f"⏱️ اعتبار: ۱ ساعت\n"
            f"🔄 دریافت خودکار: هر ۳۰ ثانیه"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 کپی آدرس ایمیل", callback_data="copy_email")],
            [InlineKeyboardButton("📬 صندوق ورودی", callback_data="inbox")],
        ])
        await status.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error("Create error: %s", e)
        await status.edit_text(f"❌ خطا: {e}", reply_markup=main_menu())


async def do_inbox(msg, cid, s):
    status = await msg.reply_text("⏳ در حال بررسی صندوق...")
    try:
        msgs, _ = await guerrilla_check(s["sid"], s.get("seq", 0))
        if not msgs:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")],
                [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
            ])
            await status.edit_text(
                f"📭 خالی است\n\n📧 {s['address']}\n\n"
                "🔄 دریافت خودکار فعال است.\n"
                "ایمیل تأیید به این آدرس ارسال بشه، خودکار دریافت میشه.",
                reply_markup=kb,
            )
            return

        new_count = len(msgs) - s.get("count", 0)
        s["count"] = len(msgs)
        lines = [f"📬 {len(msgs)} ایمیل:\n"]
        if new_count > 0:
            lines.insert(0, f"🔔 {new_count} جدید!\n")

        buttons = []
        for i, m in enumerate(msgs[:10], 1):
            fr = m.get("mail_from", "")
            subj = m.get("mail_subject", "")
            dt = m.get("mail_date", "")
            lines.append(f"{i}. 📩 {subj}\n   👤 {fr}")
            buttons.append([InlineKeyboardButton(f"📩 {subj[:35]}", callback_data=f"read_{i-1}")])

        buttons.append([InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")])
        buttons.append([InlineKeyboardButton("🏠 منو", callback_data="main_menu")])
        await status.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        logger.error("Inbox error: %s", e)
        await status.edit_text(f"❌ خطا: {e}", reply_markup=main_menu())


async def auto_check(ctx: ContextTypes.DEFAULT_TYPE):
    for cid, active in list(auto_fetch_jobs.items()):
        if not active:
            continue
        s = get_s(cid)
        if not s:
            auto_fetch_jobs.pop(cid, None)
            continue
        try:
            msgs, new_seq = await guerrilla_check(s["sid"], s.get("seq", 0))
            s["seq"] = new_seq
            last = s.get("count", 0)
            logger.info("Auto-check cid=%d emails=%d prev=%d", cid, len(msgs), last)
            if len(msgs) > last:
                new_msgs = msgs[: len(msgs) - last]
                s["count"] = len(msgs)
                for m in new_msgs:
                    eid = m.get("mail_id")
                    if not eid:
                        continue
                    detail = await guerrilla_fetch(s["sid"], eid)
                    content = detail.get("body", "") or detail.get("html", "")
                    code = extract_code(content)
                    link = extract_verify_link(content)

                    sender = detail.get("sender", "")
                    subject = detail.get("subject", "")

                    buttons = []
                    if code:
                        buttons.append([InlineKeyboardButton(f"📋 کپی کد: {code}", callback_data=f"copy_{code}")])
                    if link:
                        buttons.append([InlineKeyboardButton("🔗 باز کردن لینک تأیید", url=link)])
                    buttons.append([InlineKeyboardButton("📩 مشاهده ایمیل کامل", callback_data="read_latest")])

                    notify = f"📩 ایمیل جدید!\n👤 {sender}\n📌 {subject}\n\n"
                    if code:
                        notify += f"🔑 کد تأیید: {code}\n"
                    if link:
                        notify += "🔗 لینک تأیید:\n"
                        notify += f"{link}\n"
                    if not code and not link:
                        notify += "📩 ایمیل کامل رو ببینید"
                    await ctx.bot.send_message(
                        chat_id=cid,
                        text=notify,
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
                    )
        except Exception as e:
            logger.error("Auto-check error: %s", e)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME, reply_markup=main_menu())


async def newmail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    s = get_s(cid)
    if s:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ حذف و ساخت جدید", callback_data="delete_and_new"),
             InlineKeyboardButton("❌ انصراف", callback_data="cancel")],
        ])
        await update.message.reply_text(
            f"⚠️ ایمیل فعال دارید:\n📧 {s['address']}\n\nحذف و جدید بسازید؟", reply_markup=kb,
        )
        return
    await do_create(update.message, cid)


async def inbox(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    s = get_s(cid)
    if not s:
        await update.message.reply_text("⚠️ ایمیل فعالی ندارید. /newmail", reply_markup=main_menu())
        return
    await do_inbox(update.message, cid, s)


async def cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    cid = update.effective_chat.id

    if d == "main_menu":
        await q.edit_message_text(WELCOME, reply_markup=main_menu())

    elif d.startswith("copy_"):
        code = d.replace("copy_", "")
        await q.answer(f"✅ کد {code} کپی شد!\nحالا در سایت Paste کنید", show_alert=True)

    elif d == "help":
        await q.edit_message_text(HELP, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 منو", callback_data="main_menu")]
        ]))

    elif d == "newmail":
        s = get_s(cid)
        if s:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑️ حذف و ساخت جدید", callback_data="delete_and_new"),
                 InlineKeyboardButton("❌ انصراف", callback_data="cancel")],
            ])
            await q.edit_message_text(
                f"⚠️ ایمیل فعال دارید:\n📧 {s['address']}\n\nحذف و جدید بسازید؟", reply_markup=kb,
            )
            return
        status = await q.edit_message_text("⏳ در حال ساخت ایمیل...")
        try:
            addr, sid, seq = await guerrilla_create()
            set_s(cid, {
                "backend": "guerrillamail",
                "address": addr,
                "sid": sid,
                "seq": seq,
                "count": 0,
            })
            auto_fetch_jobs[cid] = True
            text = (
                f"✅ ایمیل موقت ساخته شد!\n\n"
                f"📧 آدرس ایمیل:\n"
                f"<code>{addr}</code>\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 مراحل کار:\n\n"
                f"۱. آدرس بالا رو کپی کنید\n"
                f"۲. در سایت مورد نظر ثبت‌نام کنید\n"
                f"۳. ایمیل تأیید ارسال میشه\n"
                f"۴. ربات خودکار کد/لینک رو میفرسته\n\n"
                f"⏱️ اعتبار: ۱ ساعت\n"
                f"🔄 دریافت خودکار: هر ۳۰ ثانیه"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 کپی آدرس ایمیل", callback_data="copy_email")],
                [InlineKeyboardButton("📬 صندوق ورودی", callback_data="inbox")],
            ])
            await status.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            await status.edit_text(f"❌ خطا: {e}", reply_markup=main_menu())

    elif d == "delete_and_new":
        clear_s(cid)
        auto_fetch_jobs.pop(cid, None)
        status = await q.edit_message_text("⏳ در حال ساخت ایمیل...")
        try:
            addr, sid, seq = await guerrilla_create()
            set_s(cid, {
                "backend": "guerrillamail",
                "address": addr,
                "sid": sid,
                "seq": seq,
                "count": 0,
            })
            auto_fetch_jobs[cid] = True
            text = (
                f"✅ ایمیل موقت ساخته شد!\n\n"
                f"📧 آدرس ایمیل:\n"
                f"<code>{addr}</code>\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 مراحل کار:\n\n"
                f"۱. آدرس بالا رو کپی کنید\n"
                f"۲. در سایت مورد نظر ثبت‌نام کنید\n"
                f"۳. ایمیل تأیید ارسال میشه\n"
                f"۴. ربات خودکار کد/لینک رو میفرسته\n\n"
                f"⏱️ اعتبار: ۱ ساعت\n"
                f"🔄 دریافت خودکار: هر ۳۰ ثانیه"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 کپی آدرس ایمیل", callback_data="copy_email")],
                [InlineKeyboardButton("📬 صندوق ورودی", callback_data="inbox")],
            ])
            await status.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            await status.edit_text(f"❌ خطا: {e}", reply_markup=main_menu())

    elif d == "copy_email":
        s = get_s(cid)
        if s:
            await q.answer(f"📧 {s['address']}", show_alert=True)
        else:
            await q.answer("⚠️ ایمیلی نیست", show_alert=True)

    elif d == "cancel":
        await q.edit_message_text("✅ لغو شد.", reply_markup=main_menu())

    elif d == "delete":
        s = get_s(cid)
        if not s:
            await q.edit_message_text("⚠️ ایمیلی نیست.", reply_markup=main_menu())
            return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ بله", callback_data="confirm_delete"),
             InlineKeyboardButton("❌ انصراف", callback_data="cancel")],
        ])
        await q.edit_message_text(f"⚠️ حذف ایمیل؟\n\n📧 {s['address']}", reply_markup=kb)

    elif d == "confirm_delete":
        clear_s(cid)
        auto_fetch_jobs.pop(cid, None)
        await q.edit_message_text("✅ حذف شد.", reply_markup=main_menu())

    elif d == "inbox":
        s = get_s(cid)
        if not s:
            await q.edit_message_text("⚠️ ایمیل فعالی ندارید.")
            return
        status = await q.edit_message_text("⏳ در حال بررسی صندوق...")
        try:
            msgs, _ = await guerrilla_check(s["sid"], s.get("seq", 0))
            if not msgs:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")],
                    [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
                ])
                await status.edit_text(
                    f"📭 خالی است\n\n📧 {s['address']}\n\n"
                    "🔄 دریافت خودکار فعال است.\n"
                    "ایمیل تأیید به این آدرس ارسال بشه، خودکار دریافت میشه.",
                    reply_markup=kb,
                )
                return

            new_count = len(msgs) - s.get("count", 0)
            s["count"] = len(msgs)
            lines = [f"📬 {len(msgs)} ایمیل:\n"]
            if new_count > 0:
                lines.insert(0, f"🔔 {new_count} جدید!\n")

            buttons = []
            for i, m in enumerate(msgs[:10], 1):
                fr = m.get("mail_from", "")
                subj = m.get("mail_subject", "")
                lines.append(f"{i}. 📩 {subj}\n   👤 {fr}")
                buttons.append([InlineKeyboardButton(f"📩 {subj[:35]}", callback_data=f"read_{i-1}")])

            buttons.append([InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")])
            buttons.append([InlineKeyboardButton("🏠 منو", callback_data="main_menu")])
            await status.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as e:
            await status.edit_text(f"❌ خطا: {e}", reply_markup=main_menu())

    elif d.startswith("read_"):
        s = get_s(cid)
        if not s:
            await q.edit_message_text("⚠️ ایمیلی نیست.")
            return
        try:
            msgs, _ = await guerrilla_check(s["sid"], s.get("seq", 0))
            if d == "read_latest":
                if not msgs:
                    await q.edit_message_text("📭 خالی.")
                    return
                m = msgs[0]
            else:
                idx = int(d.split("_")[1])
                if idx >= len(msgs):
                    await q.edit_message_text("❌ یافت نشد.")
                    return
                m = msgs[idx]

            eid = m.get("mail_id")
            detail = await guerrilla_fetch(s["sid"], eid)
            text, kb = fmt_detail(detail)
            await q.edit_message_text(text, reply_markup=kb)
        except Exception as e:
            await q.edit_message_text(f"❌ خطا: {e}")

    elif d == "back_inbox":
        s = get_s(cid)
        if not s:
            await q.edit_message_text("⚠️ ایمیلی نیست.")
            return
        status = await q.edit_message_text("⏳ در حال بررسی...")
        try:
            msgs, _ = await guerrilla_check(s["sid"], s.get("seq", 0))
            if not msgs:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")],
                    [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
                ])
                await status.edit_text(
                    f"📭 خالی است\n\n📧 {s['address']}", reply_markup=kb,
                )
                return

            lines = [f"📬 {len(msgs)} ایمیل:\n"]
            buttons = []
            for i, m in enumerate(msgs[:10], 1):
                fr = m.get("mail_from", "")
                subj = m.get("mail_subject", "")
                lines.append(f"{i}. 📩 {subj}\n   👤 {fr}")
                buttons.append([InlineKeyboardButton(f"📩 {subj[:35]}", callback_data=f"read_{i-1}")])

            buttons.append([InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")])
            buttons.append([InlineKeyboardButton("🏠 منو", callback_data="main_menu")])
            await status.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as e:
            await status.edit_text(f"❌ خطا: {e}", reply_markup=main_menu())

    elif d == "refresh_inbox":
        s = get_s(cid)
        if not s:
            await q.edit_message_text("⚠️ ایمیلی نیست.")
            return
        status = await q.edit_message_text("⏳ در حال بررسی...")
        try:
            msgs, _ = await guerrilla_check(s["sid"], s.get("seq", 0))
            if not msgs:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")],
                    [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
                ])
                await status.edit_text(
                    f"📭 خالی است\n\n📧 {s['address']}\n\n🔄 دریافت خودکار فعال است.", reply_markup=kb,
                )
                return

            new_count = len(msgs) - s.get("count", 0)
            s["count"] = len(msgs)
            lines = [f"📬 {len(msgs)} ایمیل:\n"]
            if new_count > 0:
                lines.insert(0, f"🔔 {new_count} جدید!\n")

            buttons = []
            for i, m in enumerate(msgs[:10], 1):
                fr = m.get("mail_from", "")
                subj = m.get("mail_subject", "")
                lines.append(f"{i}. 📩 {subj}\n   👤 {fr}")
                buttons.append([InlineKeyboardButton(f"📩 {subj[:35]}", callback_data=f"read_{i-1}")])

            buttons.append([InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")])
            buttons.append([InlineKeyboardButton("🏠 منو", callback_data="main_menu")])
            await status.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as e:
            await status.edit_text(f"❌ خطا: {e}", reply_markup=main_menu())


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("newmail", newmail))
    app.add_handler(CommandHandler("inbox", inbox))
    app.add_handler(CallbackQueryHandler(cb))

    app.job_queue.run_repeating(auto_check, interval=30, first=5)

    logger.info("Bot started!")
    print("Bot running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
