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

user_sessions: dict[int, dict] = {}
auto_fetch_jobs: dict[int, bool] = {}


# ─── Helpers ───────────────────────────────────────────

def strip_html(text):
    if not text:
        return ""
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html_mod.unescape(text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def extract_code(text):
    if not text:
        return None
    plain = strip_html(text)
    patterns_kw = [
        r"verification\s+code\s*[:\s]+(\d{4,8})",
        r"your\s+code\s*[:\s]+(\d{4,8})",
        r"code\s+is\s*[:\s]+(\d{4,8})",
        r"code\s*:\s*(\d{4,8})",
        r"enter\s+(?:this\s+)?code\s*[:\s]+(\d{4,8})",
        r"use\s+(?:this\s+)?code\s*[:\s]+(\d{4,8})",
        r"otp\s*[:\s]+(\d{4,8})",
        r"launch\s+code\s*[:\s]+(\d{4,8})",
        r"pin\s*[:\s]+(\d{4,8})",
        r"کد\s+(?:تأ?یید|شما|ورود|فعال)\s*[:\s]+(\d{4,8})",
    ]
    for p in patterns_kw:
        m = re.search(p, plain, re.IGNORECASE)
        if m:
            return m.group(1)
    for p in patterns_kw:
        for line in plain.split("\n"):
            m = re.search(p, line, re.IGNORECASE)
            if m:
                return m.group(1)
    for line in plain.split("\n"):
        line = line.strip()
        m = re.fullmatch(r'\s*(\d{4,8})\s*', line)
        if m:
            return m.group(1)
    m = re.search(r'\b(\d{6})\b', plain)
    if m:
        return m.group(1)
    return None


def extract_verify_link(text):
    if not text:
        return None
    plain = html_mod.unescape(text)
    seen = set()
    verify_kws = ["verify", "confirm", "activation", "activate", "valid", "approve"]
    for line in plain.split("\n"):
        for link in re.findall(r'(https?://[^\s<>"\']+)', line):
            link = link.rstrip('.,;:!?')
            link = re.sub(r'[)}\]]+$', '', link)
            if link in seen:
                continue
            seen.add(link)
            ll = link.lower()
            for kw in verify_kws:
                if kw in ll:
                    return link
    for line in plain.split("\n"):
        for link in re.findall(r'(https?://[^\s<>"\']+)', line):
            link = link.rstrip('.,;:!?')
            link = re.sub(r'[)}\]]+$', '', link)
            if link in seen:
                continue
            seen.add(link)
            return link
    return None


# ─── TempMail.lol API ─────────────────────────────────

TEMPMAIL = "https://api.tempmail.lol"


async def tempmail_create():
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{TEMPMAIL}/generate")
        r.raise_for_status()
        d = r.json()
        return d["address"], d["token"]


async def tempmail_check(token):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{TEMPMAIL}/auth/{token}")
        r.raise_for_status()
        d = r.json()
        emails = d.get("email", [])
        result = []
        for e in emails:
            result.append({
                "id": e.get("_id", ""),
                "sender": e.get("from", ""),
                "subject": e.get("subject", ""),
                "body": e.get("body", ""),
                "html": e.get("html", ""),
                "date": e.get("date", ""),
            })
        return result


# ─── GuerrillaMail API (fallback) ─────────────────────

GUERRILLA = "https://api.guerrillamail.com/ajax.php"


async def guerrilla_create():
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{GUERRILLA}?f=get_email_address&ip=127.0.0.1&agent=Python")
        r.raise_for_status()
        d = r.json()
        return d["email_addr"], d["sid_token"], d.get("seq", 0)


async def guerrilla_check(sid, seq=0):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{GUERRILLA}?f=check_email&ip=127.0.0.1&sid_token={sid}&seq={seq}")
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
            "date": d.get("mail_date", ""),
        }


# ─── Mail.tm API (fallback) ───────────────────────────

MAILTM = "https://api.mail.tm"


def _gen_user(length=10):
    import random, string
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _gen_pass(length=14):
    import random, string
    chars = string.ascii_uppercase + string.ascii_lowercase + string.digits + "!@#$%"
    return "".join(random.choices(chars, k=length))


async def mailtm_create(username=None):
    if not username:
        username = _gen_user()
    password = _gen_pass()
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{MAILTM}/domains")
        r.raise_for_status()
        domains = [d["domain"] for d in r.json().get("hydra:member", [])]
        if not domains:
            raise ValueError("No domains")
        import random
        domain = random.choice(domains)
        addr = f"{username}@{domain}"
        await c.post(f"{MAILTM}/accounts", json={"address": addr, "password": password})
        r2 = await c.post(f"{MAILTM}/token", json={"address": addr, "password": password})
        r2.raise_for_status()
        return addr, password, r2.json()["token"]


async def mailtm_check(token):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{MAILTM}/messages", headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        return r.json().get("hydra:member", [])


async def mailtm_fetch(token, mid):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{MAILTM}/messages/{mid}", headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        d = r.json()
        return {
            "sender": d.get("from", {}).get("address", ""),
            "subject": d.get("subject", ""),
            "body": d.get("text") or d.get("html") or "",
            "date": d.get("createdAt", ""),
        }


# ─── Session ───────────────────────────────────────────

def get_s(cid):
    return user_sessions.get(cid)


def set_s(cid, data):
    user_sessions[cid] = data


def clear_s(cid):
    user_sessions.pop(cid, None)


# ─── Create email with fallback ───────────────────────

async def create_email():
    """Try tempmail.lol -> guerrillamail -> mail.tm"""
    try:
        addr, token = await tempmail_create()
        return {"backend": "tempmail", "address": addr, "token": token, "count": 0}
    except Exception as e:
        logger.warning("tempmail.lol failed: %s", e)

    try:
        addr, sid, seq = await guerrilla_create()
        return {"backend": "guerrillamail", "address": addr, "sid": sid, "seq": seq, "count": 0}
    except Exception as e:
        logger.warning("guerrillamail failed: %s", e)

    try:
        addr, pw, token = await mailtm_create()
        return {"backend": "mailtm", "address": addr, "password": pw, "token": token, "count": 0}
    except Exception as e:
        logger.warning("mailtm failed: %s", e)

    raise Exception("All email services failed!")


async def check_inbox(session):
    """Check inbox based on backend"""
    backend = session["backend"]
    if backend == "tempmail":
        return await tempmail_check(session["token"])
    elif backend == "guerrillamail":
        msgs, new_seq = await guerrilla_check(session["sid"], session.get("seq", 0))
        session["seq"] = new_seq
        return msgs
    elif backend == "mailtm":
        return await mailtm_check(session["token"])
    return []


async def fetch_email(session, mid):
    """Fetch single email based on backend"""
    backend = session["backend"]
    if backend == "tempmail":
        # tempmail already returns full body in list
        return mid
    elif backend == "guerrillamail":
        return await guerrilla_fetch(session["sid"], mid)
    elif backend == "mailtm":
        return await mailtm_fetch(session["token"], mid)
    return {}


def get_msg_id(session, msg):
    """Get message ID from list item"""
    backend = session["backend"]
    if backend == "tempmail":
        return msg  # full message object
    elif backend == "guerrillamail":
        return msg.get("mail_id")
    elif backend == "mailtm":
        return msg.get("id")
    return None


def get_msg_fields(session, msg):
    """Get sender, subject, date from list item"""
    backend = session["backend"]
    if backend == "tempmail":
        return msg.get("from", ""), msg.get("subject", ""), msg.get("date", "")[:10]
    elif backend == "guerrillamail":
        return msg.get("mail_from", ""), msg.get("mail_subject", ""), msg.get("mail_date", "")[:10]
    elif backend == "mailtm":
        sender = msg.get("from", {})
        if isinstance(sender, dict):
            sender = sender.get("address", "")
        return sender, msg.get("subject", ""), msg.get("createdAt", "")[:10]
    return "", "", ""


# ─── UI ────────────────────────────────────────────────

def main_menu():
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


WELCOME = """🎯 ربات ایمیل موقت حرفه‌ای

✨ ایمیل موقت رایگان بسازید و برای ثبت‌نام در سایت‌ها استفاده کنید.

🔒 امکانات:
  • چند سرویس ایمیل موقت
  • دریافت خودکار ایمیل تأیید
  • استخراج خودکار کد تأیید
  • نمایش لینک‌ها به صورت دکمه

📋 منوی اصلی:"""

HELP = """📖 راهنمای ربات

🔹 /start — منوی اصلی
🔹 /newmail — ساخت ایمیل جدید
🔹 /inbox — بررسی صندوق ورودی
🔹 /stop — توقف دریافت خودکار

💡 ایمیل‌ها خودکار هر ۱۰ ثانیه بررسی می‌شوند."""


def fmt_new_email(sender, subject, code, verify_link):
    s = html_mod.escape(str(sender))
    j = html_mod.escape(str(subject))
    msg = "📩 ایمیل جدید!\n"
    msg += f"👤 {s}\n"
    msg += f"📌 {j}\n\n"
    if code:
        msg += f"🔑 کد تأیید: {code}\n\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        msg += "👆 کد بالا رو کپی کنید\n"
    if verify_link:
        msg += "\n👆 دکمه زیر رو بزنید تا لینک باز بشه"
    if not code and not verify_link:
        msg += "❌ کد یا لینکی پیدا نشد"
    return msg


def fmt_detail(detail):
    sender = html_mod.escape(str(detail.get("sender", "ناشناس")))
    subject = html_mod.escape(str(detail.get("subject", "(بدونوضوع)")))
    body = detail.get("body", "")
    date = detail.get("date", "")
    code = extract_code(body)
    verify_link = extract_verify_link(body)
    plain_body = strip_html(body)[:2000]

    msg = "📩 ایمیل دریافتی\n"
    msg += f"👤 {sender}\n"
    msg += f"📌 {subject}\n"
    msg += f"📅 {date}\n\n"
    if code:
        msg += f"🔑 کد تأیید: {code}\n"
    if verify_link:
        msg += "🔗 لینک تأیید:\n"
        msg += f"{verify_link}\n"
    msg += f"\n📄 متن ایمیل:\n{plain_body}"
    buttons = []
    if code:
        buttons.append([InlineKeyboardButton(f"📋 کپی کد: {code}", callback_data=f"copy_{code}")])
    if verify_link:
        buttons.append([InlineKeyboardButton("🔗 باز کردن لینک تأیید", url=verify_link)])
    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_inbox")])
    return msg, InlineKeyboardMarkup(buttons)


# ─── Handlers ──────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME, reply_markup=main_menu())


async def stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    auto_fetch_jobs[cid] = False
    await update.message.reply_text("✅ دریافت خودکار متوقف شد.", reply_markup=main_menu())


async def newmail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    if get_s(cid):
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🗑️ حذف و ساخت جدید", callback_data="delete_and_new"),
                InlineKeyboardButton("❌ انصراف", callback_data="cancel"),
            ]
        ])
        await update.message.reply_text(
            f"⚠️ ایمیل فعال دارید:\n📧 {get_s(cid)['address']}\n\nحذف و جدید بسازید؟",
            reply_markup=kb,
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


async def do_create(msg, cid):
    status = await msg.reply_text("⏳ در حال ساخت ایمیل موقت...")
    try:
        s = await create_email()
        set_s(cid, s)
        auto_fetch_jobs[cid] = True
        backend_name = {"tempmail": "TempMail.lol", "guerrillamail": "GuerrillaMail", "mailtm": "Mail.tm"}.get(s["backend"], "")
        text = (
            "✅ ایمیل موقت شما ساخته شد!\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📧 آدرس:\n<code>{s['address']}</code>\n\n"
            f"🌐 سرویس: {backend_name}\n"
            "🔄 دریافت خودکار: فعال\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📌 در سایت ثبت‌نام کنید.\n"
            "ایمیل‌ها خودکار بررسی می‌شوند."
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📋 کپی آدرس", callback_data="copy_email"),
                InlineKeyboardButton("📬 صندوق ورودی", callback_data="inbox"),
            ],
        ])
        await status.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error("Create error: %s", e)
        await status.edit_text(f"❌ خطا: {e}", reply_markup=main_menu())


async def do_inbox(msg, cid, s):
    status = await msg.reply_text("⏳ در حال بررسی صندوق...")
    try:
        msgs = await check_inbox(s)
        if not msgs:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")],
                [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
            ])
            await status.edit_text(
                "📭 صندوق خالی است.\n\n"
                f"📧 {s['address']}\n\n"
                "🔄 دریافت خودکار فعال است.",
                reply_markup=kb,
            )
            return

        new_count = len(msgs) - s.get("count", 0)
        s["count"] = len(msgs)
        lines = [f"📬 {len(msgs)} ایمیل:\n"]
        if new_count > 0:
            lines.insert(0, f"🔔 {new_count} ایمیل جدید!\n")

        buttons = []
        for i, m in enumerate(msgs[:10], 1):
            fr, subj, dt = get_msg_fields(s, m)
            lines.append(f"{i}. 📩 {subj}\n   👤 {fr} | 📅 {dt}")
            buttons.append([InlineKeyboardButton(f"📩 {subj[:35]}", callback_data=f"read_{i-1}")])

        buttons.append([InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")])
        buttons.append([InlineKeyboardButton("🏠 منو", callback_data="main_menu")])
        await status.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        logger.error("Inbox error: %s", e)
        await status.edit_text(f"❌ خطا: {e}", reply_markup=main_menu())


# ─── Auto Check ────────────────────────────────────────

async def auto_check(ctx: ContextTypes.DEFAULT_TYPE):
    for cid, active in list(auto_fetch_jobs.items()):
        if not active:
            continue
        s = get_s(cid)
        if not s:
            auto_fetch_jobs.pop(cid, None)
            continue
        try:
            msgs = await check_inbox(s)
            last = s.get("count", 0)
            if len(msgs) > last:
                new_msgs = msgs[: len(msgs) - last]
                s["count"] = len(msgs)
                for m in new_msgs:
                    if s["backend"] == "tempmail":
                        detail = m
                    elif s["backend"] == "guerrillamail":
                        detail = await guerrilla_fetch(s["sid"], m.get("mail_id"))
                    elif s["backend"] == "mailtm":
                        detail = await mailtm_fetch(s["token"], m.get("id"))
                    else:
                        continue

                    code = extract_code(detail.get("body", ""))
                    verify_link = extract_verify_link(detail.get("body", ""))

                    buttons = []
                    if code:
                        buttons.append([InlineKeyboardButton(f"📋 کپی کد: {code}", callback_data=f"copy_{code}")])
                    if verify_link:
                        buttons.append([InlineKeyboardButton("🔗 باز کردن لینک تأیید", url=verify_link)])
                    buttons.append([InlineKeyboardButton("📩 مشاهده ایمیل کامل", callback_data="read_latest")])

                    sender = detail.get("sender", "")
                    subject = detail.get("subject", "")
                    notify = f"📩 ایمیل جدید!\n👤 {sender}\n📌 {subject}\n\n"
                    if code:
                        notify += f"🔑 کد تأیید: {code}\n"
                    if verify_link:
                        notify += "👇 لینک تأیید رو باز کنید"
                    if not code and not verify_link:
                        notify += "❌ کد یا لینکی پیدا نشد\n📩 ایمیل کامل رو ببینید"
                    await ctx.bot.send_message(
                        chat_id=cid,
                        text=notify,
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(buttons),
                    )
        except Exception as e:
            logger.error("Auto-check error: %s", e)


# ─── Callbacks ─────────────────────────────────────────

async def cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    cid = update.effective_chat.id

    if d == "main_menu":
        await q.edit_message_text(WELCOME, reply_markup=main_menu())

    elif d.startswith("copy_"):
        code = d.replace("copy_", "")
        await q.answer(f"✅ کد {code} کپی شد!\nحالا در سایت Paste کنید (Ctrl+V)", show_alert=True)

    elif d == "help":
        await q.edit_message_text(HELP, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 منو", callback_data="main_menu")]
        ]))

    elif d == "newmail":
        s = get_s(cid)
        if s:
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🗑️ حذف و ساخت جدید", callback_data="delete_and_new"),
                    InlineKeyboardButton("❌ انصراف", callback_data="cancel"),
                ]
            ])
            await q.edit_message_text(
                f"⚠️ ایمیل فعال دارید:\n📧 {s['address']}\n\nحذف و جدید بسازید؟",
                reply_markup=kb,
            )
            return
        status = await q.edit_message_text("⏳ در حال ساخت ایمیل...")
        try:
            s = await create_email()
            set_s(cid, s)
            auto_fetch_jobs[cid] = True
            backend_name = {"tempmail": "TempMail.lol", "guerrillamail": "GuerrillaMail", "mailtm": "Mail.tm"}.get(s["backend"], "")
            text = (
                "✅ ایمیل موقت شما ساخته شد!\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📧 آدرس:\n<code>{s['address']}</code>\n\n"
                f"🌐 سرویس: {backend_name}\n"
                "🔄 دریافت خودکار: فعال\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "📌 در سایت ثبت‌نام کنید.\n"
                "ایمیل‌ها خودکار بررسی می‌شوند."
            )
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📋 کپی آدرس", callback_data="copy_email"),
                    InlineKeyboardButton("📬 صندوق ورودی", callback_data="inbox"),
                ],
            ])
            await status.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            await status.edit_text(f"❌ خطا: {e}", reply_markup=main_menu())

    elif d == "delete_and_new":
        clear_s(cid)
        auto_fetch_jobs.pop(cid, None)
        status = await q.edit_message_text("⏳ در حال ساخت ایمیل...")
        try:
            s = await create_email()
            set_s(cid, s)
            auto_fetch_jobs[cid] = True
            backend_name = {"tempmail": "TempMail.lol", "guerrillamail": "GuerrillaMail", "mailtm": "Mail.tm"}.get(s["backend"], "")
            text = (
                "✅ ایمیل موقت شما ساخته شد!\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📧 آدرس:\n<code>{s['address']}</code>\n\n"
                f"🌐 سرویس: {backend_name}\n"
                "🔄 دریافت خودکار: فعال\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "📌 در سایت ثبت‌نام کنید.\n"
                "ایمیل‌ها خودکار بررسی می‌شوند."
            )
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📋 کپی آدرس", callback_data="copy_email"),
                    InlineKeyboardButton("📬 صندوق ورودی", callback_data="inbox"),
                ],
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
            await q.edit_message_text("⚠️ ایمیلی برای حذف نیست.", reply_markup=main_menu())
            return
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ بله", callback_data="confirm_delete"),
                InlineKeyboardButton("❌ انصراف", callback_data="cancel"),
            ]
        ])
        await q.edit_message_text(f"⚠️ حذف ایمیل؟\n\n📧 {s['address']}", reply_markup=kb)

    elif d == "confirm_delete":
        clear_s(cid)
        auto_fetch_jobs.pop(cid, None)
        await q.edit_message_text("✅ حذف شد.", reply_markup=main_menu())

    elif d == "password":
        s = get_s(cid)
        if not s:
            await q.edit_message_text("⚠️ ایمیل فعالی ندارید. /newmail", reply_markup=main_menu())
            return
        pw = s.get("password", "ندارد (GuerrillaMail)")
        backend_name = {"tempmail": "TempMail.lol", "guerrillamail": "GuerrillaMail", "mailtm": "Mail.tm"}.get(s["backend"], "")
        text = (
            "🔑 رمز ایمیل شما\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📧 آدرس:\n<code>{s['address']}</code>\n\n"
            f"🔑 رمز:\n<code>{pw}</code>\n\n"
            f"🌐 سرویس: {backend_name}\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 منو", callback_data="main_menu")]
        ]))

    elif d == "inbox":
        s = get_s(cid)
        if not s:
            await q.edit_message_text("⚠️ ایمیل فعالی ندارید.", reply_markup=main_menu())
            return
        await do_inbox_cb(q, cid, s)

    elif d.startswith("read_"):
        s = get_s(cid)
        if not s:
            await q.edit_message_text("⚠️ ایمیل فعالی ندارید.")
            return
        try:
            msgs = await check_inbox(s)
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

            if s["backend"] == "tempmail":
                detail = m
            elif s["backend"] == "guerrillamail":
                detail = await guerrilla_fetch(s["sid"], m.get("mail_id"))
            elif s["backend"] == "mailtm":
                detail = await mailtm_fetch(s["token"], m.get("id"))
            else:
                await q.edit_message_text("❌ خطا")
                return

            text, kb = fmt_detail(detail)
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            await q.edit_message_text(f"❌ خطا: {e}")

    elif d == "back_inbox":
        s = get_s(cid)
        if not s:
            await q.edit_message_text("⚠️ ایمیل فعالی ندارید.")
            return
        await do_inbox_cb(q, cid, s)

    elif d == "refresh_inbox":
        s = get_s(cid)
        if not s:
            await q.edit_message_text("⚠️ ایمیل فعالی ندارید.")
            return
        await do_inbox_cb(q, cid, s)

    elif d == "stop":
        auto_fetch_jobs[cid] = False
        await q.edit_message_text("✅ متوقف شد.", reply_markup=main_menu())


async def do_inbox_cb(q, cid, s):
    await q.edit_message_text("⏳ در حال بررسی...")
    try:
        msgs = await check_inbox(s)
        if not msgs:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")],
                [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
            ])
            await q.edit_message_text(
                f"📭 خالی.\n📧 {s['address']}", reply_markup=kb,
            )
            return

        new_count = len(msgs) - s.get("count", 0)
        s["count"] = len(msgs)
        lines = [f"📬 {len(msgs)} ایمیل:\n"]
        if new_count > 0:
            lines.insert(0, f"🔔 {new_count} جدید!\n")

        buttons = []
        for i, m in enumerate(msgs[:10], 1):
            fr, subj, dt = get_msg_fields(s, m)
            lines.append(f"{i}. 📩 {subj}\n   👤 {fr} | 📅 {dt}")
            buttons.append([InlineKeyboardButton(f"📩 {subj[:35]}", callback_data=f"read_{i-1}")])

        buttons.append([InlineKeyboardButton("🔄 بروزرسانی", callback_data="refresh_inbox")])
        buttons.append([InlineKeyboardButton("🏠 منو", callback_data="main_menu")])
        await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        await q.edit_message_text(f"❌ خطا: {e}", reply_markup=main_menu())


# ─── Main ──────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("newmail", newmail))
    app.add_handler(CommandHandler("inbox", inbox))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CallbackQueryHandler(cb))

    app.job_queue.run_repeating(auto_check, interval=10, first=5)

    logger.info("🚀 ربات شروع به کار کرد!")
    print("✅ ربات در حال اجراست...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
