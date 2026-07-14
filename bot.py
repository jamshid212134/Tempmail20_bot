import httpx
import random
import string
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

def generate_username(length=10):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def generate_password(length=14):
    chars = string.ascii_uppercase + string.ascii_lowercase + string.digits + "!@#$%"
    return "".join(random.choices(chars, k=length))


def extract_code(text):
    if not text:
        return None
    plain = html_mod.unescape(text)
    keywords = [
        "verification code", "your code", "code is", "code:",
        "enter code", "use code", "otp", "launch code", "pin",
        "کد تایید", "کد تأیید", "کد شما", "کد ورود",
    ]
    for line in plain.split("\n"):
        line = line.strip()
        low = line.lower()
        for kw in keywords:
            if kw in low:
                m = re.search(r"[:\s]+(\d{4,8})\b", line)
                if m:
                    return m.group(1)
    for line in plain.split("\n"):
        m = re.fullmatch(r"\s*(\d{4,8})\s*", line.strip())
        if m:
            return m.group(1)
    return None


def extract_links(text):
    if not text:
        return []
    plain = html_mod.unescape(text)
    seen = set()
    results = []
    for line in plain.split("\n"):
        for link in re.findall(r'(https?://[^\s<>"\']+)', line):
            link = link.rstrip('.,;:!?')
            link = re.sub(r'[)}\]]+$', '', link)
            if link in seen:
                continue
            seen.add(link)
            ll = link.lower()
            if "verify" in ll or "confirm" in ll:
                desc = "✅ لینک تأیید"
            elif "delete" in ll:
                desc = "🗑️ لینک حذف"
            elif "login" in ll or "log" in ll or "auth" in ll:
                desc = "🔑 لینک ورود"
            else:
                desc = "🔗 لینک"
            results.append((desc, link))
    return results


# ─── GuerrillaMail API ────────────────────────────────

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


# ─── Mail.tm API ──────────────────────────────────────

MAILTM = "https://api.mail.tm"


async def mailtm_create(username=None):
    if not username:
        username = generate_username()
    password = generate_password()
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{MAILTM}/domains")
        r.raise_for_status()
        domains = [d["domain"] for d in r.json().get("hydra:member", [])]
        if not domains:
            raise ValueError("No domains")
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

def get_session(cid):
    return user_sessions.get(cid)


def set_session(cid, data):
    user_sessions[cid] = data


def clear_session(cid):
    user_sessions.pop(cid, None)


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
            InlineKeyboardButton("🌐 دامنه‌ها", callback_data="domains"),
            InlineKeyboardButton("📖 راهنما", callback_data="help"),
        ],
    ])


def back_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu")]
    ])


WELCOME = """🎯 ربات ایمیل موقت حرفه‌ای

✨ ایمیل موقت رایگان بسازید و برای ثبت‌نام در سایت‌ها استفاده کنید.

🔒 امکانات:
  • ساخت ایمیل با چند سرویس مختلف
  • دریافت خودکار ایمیل تأیید
  • استخراج خودکار کد تأیید
  • نمایش لینک‌ها به صورت دکمه
  • رمز عبور هر ایمیل

📋 منوی اصلی:"""

HELP = """📖 راهنمای ربات

🔹 /start — منوی اصلی
🔹 /newmail — ساخت ایمیل جدید
🔹 /inbox — بررسی صندوق ورودی
🔹 /stop — توقف دریافت خودکار

💡 نکته: ایمیل‌ها خودکار هر ۱۰ ثانیه بررسی می‌شوند."""


# ─── Message Formatters ────────────────────────────────

def fmt_new_email(sender, subject, code, links):
    s = html_mod.escape(str(sender))
    j = html_mod.escape(str(subject))
    msg = (
        "📩 ایمیل جدید دریافت شد!\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 فرستنده: {s}\n"
        f"📌 موضوع: {j}\n"
    )
    if code:
        msg += f"\n🔑 کد تأیید: <code>{html_mod.escape(code)}</code>\n"
    if links:
        msg += f"\n🔗 لینک‌ها ({len(links)}):\n"
        for i, (desc, _) in enumerate(links, 1):
            msg += f"{i}. {desc}\n"
    msg += "\n━━━━━━━━━━━━━━━━━━━━\n💡 لینک مورد نظر را انتخاب کنید:"
    return msg


def fmt_email_detail(detail):
    sender = html_mod.escape(str(detail.get("sender", "ناشناس")))
    subject = html_mod.escape(str(detail.get("subject", "(بدون موضوع)")))
    text = detail.get("body", "")
    date = detail.get("date", "")
    code = extract_code(text)
    links = extract_links(text)
    body = html_mod.escape(text[:2000])

    msg = (
        "📩 ایمیل دریافتی\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 فرستنده: {sender}\n"
        f"📌 موضوع: {subject}\n"
        f"📅 تاریخ: {date}\n"
    )
    if code:
        msg += f"\n🔑 کد تأیید: <code>{html_mod.escape(code)}</code>\n"
    if links:
        msg += f"\n🔗 لینک‌ها ({len(links)}):\n"
        for i, (desc, _) in enumerate(links, 1):
            msg += f"{i}. {desc}\n"
    msg += (
        "\n📄 متن ایمیل:\n"
        "─────────────\n"
        f"{body}\n"
        "─────────────"
    )

    buttons = []
    for i, (desc, link) in enumerate(links, 1):
        buttons.append([InlineKeyboardButton(f"🔗 {i}. {desc[:40]}", url=link)])
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
    if get_session(cid):
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🗑️ حذف و ساخت جدید", callback_data="delete_and_new"),
                InlineKeyboardButton("❌ انصراف", callback_data="cancel"),
            ]
        ])
        await update.message.reply_text(
            f"⚠️ ایمیل فعال دارید:\n📧 {get_session(cid)['address']}\n\nحذف و جدید بسازید؟",
            reply_markup=kb,
        )
        return
    await do_create(update.message, cid)


async def inbox(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    s = get_session(cid)
    if not s:
        await update.message.reply_text("⚠️ ایمیل فعالی ندارید. /newmail", reply_markup=main_menu())
        return
    await do_inbox(update.message, cid, s)


async def do_create(msg, cid):
    status = await msg.reply_text("⏳ در حال ساخت ایمیل موقت...")
    try:
        addr, sid, seq = await guerrilla_create()
        set_session(cid, {
            "backend": "guerrillamail",
            "address": addr,
            "sid": sid,
            "seq": seq,
            "count": 0,
        })
        auto_fetch_jobs[cid] = True
        text = (
            "✅ ایمیل موقت شما ساخته شد!\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📧 آدرس:\n<code>{addr}</code>\n\n"
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
        if s["backend"] == "guerrillamail":
            msgs, new_seq = await guerrilla_check(s["sid"], s.get("seq", 0))
            s["seq"] = new_seq
        else:
            msgs = await mailtm_check(s["token"])

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
            if s["backend"] == "guerrillamail":
                fr = m.get("mail_from", "ناشناس")
                subj = m.get("mail_subject", "(بدون موضوع)")
                dt = m.get("mail_date", "")[:10]
            else:
                fr = m.get("from", {}).get("address", "ناشناس")
                subj = m.get("subject", "(بدونوضوع)")
                dt = m.get("createdAt", "")[:10]
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
        s = get_session(cid)
        if not s:
            auto_fetch_jobs.pop(cid, None)
            continue
        try:
            if s["backend"] == "guerrillamail":
                msgs, new_seq = await guerrilla_check(s["sid"], s.get("seq", 0))
                s["seq"] = new_seq
            else:
                msgs = await mailtm_check(s["token"])

            last = s.get("count", 0)
            if len(msgs) > last:
                new_msgs = msgs[: len(msgs) - last]
                s["count"] = len(msgs)
                for m in new_msgs:
                    if s["backend"] == "guerrillamail":
                        detail = await guerrilla_fetch(s["sid"], m.get("mail_id"))
                    else:
                        detail = await mailtm_fetch(s["token"], m.get("id"))

                    code = extract_code(detail.get("body", ""))
                    links = extract_links(detail.get("body", ""))
                    notify = fmt_new_email(
                        detail.get("sender", ""),
                        detail.get("subject", ""),
                        code,
                        links,
                    )
                    buttons = []
                    for i, (desc, link) in enumerate(links, 1):
                        buttons.append([InlineKeyboardButton(f"🔗 {i}. {desc[:40]}", url=link)])
                    buttons.append([InlineKeyboardButton("📩 خواندن ایمیل", callback_data="read_latest")])
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

    elif d == "help":
        await q.edit_message_text(HELP, reply_markup=back_menu())

    elif d == "newmail":
        s = get_session(cid)
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
            addr, sid, seq = await guerrilla_create()
            set_session(cid, {
                "backend": "guerrillamail",
                "address": addr,
                "sid": sid,
                "seq": seq,
                "count": 0,
            })
            auto_fetch_jobs[cid] = True
            text = (
                "✅ ایمیل موقت شما ساخته شد!\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📧 آدرس:\n<code>{addr}</code>\n\n"
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
        clear_session(cid)
        auto_fetch_jobs.pop(cid, None)
        status = await q.edit_message_text("⏳ در حال ساخت ایمیل...")
        try:
            addr, sid, seq = await guerrilla_create()
            set_session(cid, {
                "backend": "guerrillamail",
                "address": addr,
                "sid": sid,
                "seq": seq,
                "count": 0,
            })
            auto_fetch_jobs[cid] = True
            text = (
                "✅ ایمیل موقت شما ساخته شد!\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📧 آدرس:\n<code>{addr}</code>\n\n"
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
        s = get_session(cid)
        if s:
            await q.answer(f"📧 {s['address']}", show_alert=True)
        else:
            await q.answer("⚠️ ایمیلی نیست", show_alert=True)

    elif d == "cancel":
        await q.edit_message_text("✅ لغو شد.", reply_markup=main_menu())

    elif d == "delete":
        s = get_session(cid)
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
        clear_session(cid)
        auto_fetch_jobs.pop(cid, None)
        await q.edit_message_text("✅ حذف شد.", reply_markup=main_menu())

    elif d == "password":
        s = get_session(cid)
        if not s:
            await q.edit_message_text("⚠️ ایمیل فعالی ندارید. /newmail", reply_markup=main_menu())
            return
        pw = s.get("password", "ندارد")
        text = (
            "🔑 رمز ایمیل شما\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📧 آدرس:\n<code>{s['address']}</code>\n\n"
            f"🔑 رمز:\n<code>{pw}</code>\n\n"
            f"🌐 سرویس: {s.get('backend', 'نامشخص')}\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=back_menu())

    elif d == "domains":
        text = (
            "🌐 سرویس‌های ایمیل موقت\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "  1. GuerrillaMail\n"
            "  2. Mail.tm\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "💡 ربات خودکار بهترین سرویس را انتخاب می‌کند."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📧 ساخت ایمیل", callback_data="newmail")],
            [InlineKeyboardButton("🏠 منو", callback_data="main_menu")],
        ])
        await q.edit_message_text(text, reply_markup=kb)

    elif d == "inbox":
        s = get_session(cid)
        if not s:
            await q.edit_message_text("⚠️ ایمیل فعالی ندارید.", reply_markup=main_menu())
            return
        await do_inbox_cb(q, cid, s)

    elif d.startswith("read_"):
        s = get_session(cid)
        if not s:
            await q.edit_message_text("⚠️ ایمیل فعالی ندارید.")
            return
        try:
            if s["backend"] == "guerrillamail":
                msgs, _ = await guerrilla_check(s["sid"], s.get("seq", 0))
            else:
                msgs = await mailtm_check(s["token"])
            if d == "read_latest":
                if not msgs:
                    await q.edit_message_text("📭 خالی.")
                    return
                mid = msgs[0].get("mail_id") if s["backend"] == "guerrillamail" else msgs[0].get("id")
            else:
                idx = int(d.split("_")[1])
                if idx >= len(msgs):
                    await q.edit_message_text("❌ یافت نشد.")
                    return
                mid = msgs[idx].get("mail_id") if s["backend"] == "guerrillamail" else msgs[idx].get("id")

            if s["backend"] == "guerrillamail":
                detail = await guerrilla_fetch(s["sid"], mid)
            else:
                detail = await mailtm_fetch(s["token"], mid)

            text, kb = fmt_email_detail(detail)
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            await q.edit_message_text(f"❌ خطا: {e}")

    elif d == "back_inbox":
        s = get_session(cid)
        if not s:
            await q.edit_message_text("⚠️ ایمیل فعالی ندارید.")
            return
        await do_inbox_cb(q, cid, s)

    elif d == "refresh_inbox":
        s = get_session(cid)
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
        if s["backend"] == "guerrillamail":
            msgs, new_seq = await guerrilla_check(s["sid"], s.get("seq", 0))
            s["seq"] = new_seq
        else:
            msgs = await mailtm_check(s["token"])

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
            if s["backend"] == "guerrillamail":
                fr = m.get("mail_from", "ناشناس")
                subj = m.get("mail_subject", "(بدون موضوع)")
                dt = m.get("mail_date", "")[:10]
            else:
                fr = m.get("from", {}).get("address", "ناشناس")
                subj = m.get("subject", "(بدونوضوع)")
                dt = m.get("createdAt", "")[:10]
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
