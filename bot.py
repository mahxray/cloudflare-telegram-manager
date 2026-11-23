import asyncio
import base64
import html
import io
import json
import logging
import os
import re
import sqlite3
from contextlib import closing
from contextvars import ContextVar
from functools import wraps
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from i18n import translate_text, LANGUAGES
from telegram import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    InlineKeyboardButton as TelegramInlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"].strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",") if x.strip().isdigit()}
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "bot.db"
FERNET = Fernet(os.environ["ENCRYPTION_KEY"].strip().encode())
CF_API = "https://api.cloudflare.com/client/v4"
PAGE_SIZE = 8
VERSION = "2.8.1"

# A language is selected separately by each authorized Telegram user.
# ContextVar keeps concurrent updates isolated without affecting other admins.
CURRENT_LANGUAGE = ContextVar("cloudflare_ui_language", default="fa")
LANGUAGE_NAMES = {"fa": "🇮🇷 فارسی", "en": "🇬🇧 English", "ru": "🇷🇺 Русский"}


def tr(text):
    return translate_text(text, CURRENT_LANGUAGE.get())


def get_user_language(user_id: int) -> str:
    with closing(db_connect()) as con:
        row = con.execute("SELECT language FROM user_languages WHERE user_id=?", (user_id,)).fetchone()
    return row[0] if row and row[0] in LANGUAGES else "fa"


def save_user_language(user_id: int, language: str):
    if language not in LANGUAGES:
        raise ValueError("Unsupported language")
    with closing(db_connect()) as con:
        con.execute(
            "INSERT INTO user_languages(user_id, language) VALUES(?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET language=excluded.language",
            (user_id, language),
        )
        con.commit()


def localized_handler(handler):
    """Bind the requesting user's persisted language for a single update."""
    @wraps(handler)
    async def wrapped(update, context):
        user = update.effective_user
        token = CURRENT_LANGUAGE.set(get_user_language(user.id) if user else "fa")
        try:
            return await handler(update, context)
        finally:
            CURRENT_LANGUAGE.reset(token)
    return wrapped


async def localized_reply(message, text, **kwargs):
    return await message.reply_text(tr(text), **kwargs)


async def localized_send(chat, text, **kwargs):
    return await chat.send_message(tr(text), **kwargs)


async def localized_answer(query, text=None, **kwargs):
    if text is None:
        return await query.answer(**kwargs)
    return await query.answer(tr(text), **kwargs)


async def localized_document_reply(message, **kwargs):
    if "caption" in kwargs:
        kwargs["caption"] = tr(kwargs["caption"])
    return await message.reply_document(**kwargs)


# Telegram inline keyboard callback_data is limited to 64 UTF-8 bytes.
# Cloudflare zone/record IDs are 32-character hex strings. Reversible binary
# encoding saves 9 bytes per ID (32 -> 23 including a marker) so even buttons
# with both IDs and an action/field remain within Telegram's limit. There is
# no cache or DB migration: callbacks remain valid after a container restart.
_CF_HEX_ID = re.compile(r"(?<=:)[0-9a-fA-F]{32}(?=:|$)")
_SHORT_CF_ID = re.compile(r"^~[A-Za-z0-9_-]{22}$")


def encode_callback_data(data: str) -> str:
    if not isinstance(data, str):
        raise TypeError("callback_data must be a string")

    def encode_id(match):
        raw_id = bytes.fromhex(match.group(0))
        return "~" + base64.urlsafe_b64encode(raw_id).decode("ascii").rstrip("=")

    compact = _CF_HEX_ID.sub(encode_id, data)
    length = len(compact.encode("utf-8"))
    if length > 64 or length < 1:
        # Fail before Telegram API rejects an entire keyboard.
        raise ValueError(f"callback_data length out of range: {length} bytes")
    return compact


def decode_callback_data(data: str) -> str:
    if not isinstance(data, str):
        return ""
    parts = data.split(":")
    for i, item in enumerate(parts):
        if _SHORT_CF_ID.fullmatch(item):
            try:
                raw = base64.urlsafe_b64decode(item[1:] + "==")
                if len(raw) == 16:
                    parts[i] = raw.hex()
            except (ValueError, TypeError):
                pass
    return ":".join(parts)


def InlineKeyboardButton(text, *args, **kwargs):
    """Centralize 64-byte validation for ALL inline buttons in the bot.

    Matches PTB's positional order: (text, url, callback_data, ...).
    """
    if "callback_data" in kwargs and kwargs["callback_data"] is not None:
        kwargs["callback_data"] = encode_callback_data(kwargs["callback_data"])
    elif len(args) >= 2 and args[1] is not None:
        args = list(args)
        args[1] = encode_callback_data(args[1])
    return TelegramInlineKeyboardButton(tr(text), *args, **kwargs)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cfbot")
# HTTPX INFO includes Telegram Bot API credentials in request URLs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Official Cloudflare DNS API record types (2026-10).
DNS_TYPES = [
    "A", "AAAA", "CAA", "CERT", "CNAME", "DNSKEY", "DS", "HTTPS", "LOC",
    "MX", "NAPTR", "NS", "OPENPGPKEY", "PTR", "SMIMEA", "SRV", "SSHFP",
    "SVCB", "TLSA", "TXT", "URI",
]
TYPE_GROUPS = [
    ("⭐ رایج", ["A", "AAAA", "CNAME", "TXT", "MX", "NS", "PTR"]),
    ("🔐 امنیتی", ["CAA", "DS", "DNSKEY", "TLSA", "SSHFP", "SMIMEA", "CERT", "OPENPGPKEY"]),
    ("🧩 پیشرفته", ["SRV", "NAPTR", "SVCB", "HTTPS", "URI", "LOC"]),
]
PROXY_TYPES = {"A", "AAAA", "CNAME"}
PRIORITY_TYPES = {"MX", "URI"}

TYPE_HINTS = {
    "A": "IPv4، مثال: <code>176.65.151.51</code>",
    "AAAA": "IPv6، مثال: <code>2001:db8::1</code>",
    "CNAME": "نام مقصد، مثال: <code>target.example.com</code>",
    "TXT": "متن رکورد، مثال: <code>v=spf1 include:_spf.example.com ~all</code>",
    "MX": "Mail server، مثال: <code>mail.example.com</code> (Priority جدا پرسیده می‌شود)",
    "NS": "Name server، مثال: <code>ns1.example.com</code>",
    "PTR": "نام مقصد Reverse DNS، مثال: <code>host.example.com</code>",
    "CAA": "فرمت CAA، مثال: <code>0 issue letsencrypt.org</code>",
    "CERT": "فرمت CERT طبق RFC، مثال: <code>1 0 5 BASE64...</code>",
    "DNSKEY": "فرمت DNSKEY، مثال: <code>257 3 13 BASE64...</code>",
    "DS": "فرمت DS، مثال: <code>12345 13 2 HEXDIGEST...</code>",
    "HTTPS": "فرمت HTTPS/SVCB، مثال: <code>1 . alpn=\"h2,h3\"</code>",
    "LOC": "فرمت LOC، مثال: <code>37 47 0.0 N 122 24 0.0 W 10m 1m 10000m 10m</code>",
    "NAPTR": "مثال: <code>100 10 \"U\" \"E2U+sip\" \"!^.*$!sip:info@example.com!\" .</code>",
    "OPENPGPKEY": "OpenPGP public key به صورت Base64",
    "SMIMEA": "مثال: <code>3 1 1 HEX...</code>",
    "SRV": "فرمت: <code>priority weight port target</code>، مثال: <code>10 5 443 service.example.com</code>",
    "SSHFP": "فرمت: <code>algorithm fingerprint-type fingerprint</code>، مثال: <code>4 2 ABCDEF...</code>",
    "SVCB": "مثال: <code>1 svc.example.com alpn=\"h2,h3\" port=443</code>",
    "TLSA": "فرمت: <code>usage selector matching-type certificate-data</code>، مثال: <code>3 1 1 HEX...</code>",
    "URI": "URI target، مثال: <code>https://example.com/service</code> (Priority جدا پرسیده می‌شود)",
}


def db_connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with closing(db_connect()) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS cf_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                account_id TEXT NOT NULL,
                token_enc BLOB NOT NULL,
                active INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS user_languages (
                user_id INTEGER PRIMARY KEY,
                language TEXT NOT NULL CHECK(language IN ('fa', 'en', 'ru'))
            )
        """)
        con.commit()
        migrate_legacy_accounts(con)


def migrate_legacy_accounts(con: sqlite3.Connection):
    count = con.execute("SELECT COUNT(*) FROM cf_accounts").fetchone()[0]
    if count:
        return
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ("accounts", "cloudflare_accounts"):
        if table not in tables:
            continue
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        def pick(*names):
            return next((n for n in names if n in cols), None)
        c_label = pick("label", "name", "title")
        c_acc = pick("account_id", "cf_account_id", "account")
        c_tok = pick("token_enc", "api_token", "token")
        c_active = pick("active", "is_active", "selected")
        if not (c_label and c_acc and c_tok):
            continue
        rows = con.execute(f"SELECT * FROM {table}").fetchall()
        for row in rows:
            raw = row[c_tok]
            if raw is None:
                continue
            raw_b = raw if isinstance(raw, bytes) else str(raw).encode()
            try:
                FERNET.decrypt(raw_b)
                enc = raw_b
            except Exception:
                enc = FERNET.encrypt(raw_b)
            con.execute(
                "INSERT INTO cf_accounts(label,account_id,token_enc,active,created_at) VALUES(?,?,?,?,?)",
                (str(row[c_label]), str(row[c_acc]), enc, int(row[c_active]) if c_active else 0, datetime.now(timezone.utc).isoformat()),
            )
        con.commit()
        break


def normalize_cf_token(raw: str) -> str:
    """Remove accidental wrapping (quotes, Bearer, header labels) without guessing secrets."""
    s = raw.strip()
    for _ in range(3):
        before = s
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'", "`"):
            s = s[1:-1].strip()
        if s.lower().startswith("authorization:"):
            s = s.split(":", 1)[1].strip()
        if s.lower().startswith("bearer "):
            s = s[7:].strip()
        if before == s:
            break
    if not s or s.lower() in ("bearer", "authorization:") or len(s) > 1024 or any(not (33 <= ord(c) <= 126) for c in s):
        raise ValueError("فرمت API Token درست نیست؛ فقط خود مقدار Token را کپی کن، بدون Bearer، فاصله یا خط جدید.")
    if s.startswith("cfk_"):
        raise ValueError("Global API Key وارد شده است. از Cloudflare یک API Token بساز (نه Global API Key).")
    return s


def format_cf_errors(data: dict, status: int) -> str:
    parts = []
    def add_errors(errors):
        for err in errors or []:
            if not isinstance(err, dict):
                continue
            code = err.get("code")
            msg = str(err.get("message") or "Unknown error")
            parts.append(f"{msg} ({code})" if code is not None else msg)
            add_errors(err.get("error_chain"))
    add_errors(data.get("errors"))
    return "; ".join(parts[:6]) or f"HTTP {status}"


async def list_zones_with_token(client, token, params):
    r = await client.get(
        CF_API + "/zones",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
    )
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"Cloudflare returned non-JSON HTTP {r.status_code}")
    if r.is_error or data.get("success") is not True:
        msg = format_cf_errors(data, r.status_code)
        if r.status_code == 400 and any(e.get("code") == 6003 for e in data.get("errors",[]) if isinstance(e,dict)):
            msg += ". API Token یا قالب هدر Authorization معتبر نیست؛ فقط متن خام توکن را وارد کن."
        raise RuntimeError(msg)
    return data


async def verify_token_for_account(token: str, account_id: str) -> int:
    """Test the exact account-scoped API call that the bot uses."""
    async with httpx.AsyncClient(timeout=25) as client:
        data = await list_zones_with_token(
            client, normalize_cf_token(token), {"account.id": account_id, "per_page": 1}
        )
    return (data.get("result_info") or {}).get("total_count", len(data.get("result") or []))


async def discover_token_account(token):
    accounts = {}
    page = 1
    async with httpx.AsyncClient(timeout=25) as client:
        while True:
            data = await list_zones_with_token(client, token, {"per_page": 50, "page": page})
            for zone in data.get("result") or []:
                account = zone.get("account") or {}
                account_id = account.get("id") or ""
                if re.fullmatch(r"[0-9a-fA-F]{32}", account_id):
                    accounts[account_id] = (account.get("name") or account_id)[:60]
                if len(accounts) > 1:
                    return None
            if page >= (data.get("result_info") or {}).get("total_pages", 1):
                break
            page += 1
    return next(iter(accounts.items()), None)


def cloudflare_token_template():
    permissions = [
        {"key": "zone", "type": "read"},
        {"key": "dns", "type": "edit"},
        {"key": "zone_settings", "type": "edit"},
        {"key": "cache", "type": "purge"},
    ]
    query = urlencode({
        "permissionGroupKeys": json.dumps(permissions, separators=(",", ":")),
        "accountId": "*",
        "zoneId": "all",
        "name": "Cloudflare Telegram Manager",
    })
    return "https://dash.cloudflare.com/profile/api-tokens?" + query



def encrypt_token(token: str) -> bytes:
    return FERNET.encrypt(normalize_cf_token(token).encode())


def decrypt_token(blob: bytes | str) -> str:
    b = blob if isinstance(blob, bytes) else blob.encode()
    return FERNET.decrypt(b).decode()


def list_accounts():
    with closing(db_connect()) as con:
        return con.execute("SELECT * FROM cf_accounts ORDER BY active DESC, id ASC").fetchall()


def get_account(account_id: int):
    with closing(db_connect()) as con:
        return con.execute("SELECT * FROM cf_accounts WHERE id=?", (account_id,)).fetchone()


def active_account():
    with closing(db_connect()) as con:
        row = con.execute("SELECT * FROM cf_accounts WHERE active=1 ORDER BY id LIMIT 1").fetchone()
        if row:
            return row
        row = con.execute("SELECT * FROM cf_accounts ORDER BY id LIMIT 1").fetchone()
        if row:
            con.execute("UPDATE cf_accounts SET active=CASE WHEN id=? THEN 1 ELSE 0 END", (row["id"],))
            con.commit()
        return row


def is_admin(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id in ADMIN_IDS)


async def safe_edit(q, text: str, **kwargs):
    """Treat repeated Telegram menu edits as a no-op, not an application error."""
    try:
        return await q.edit_message_text(tr(text), **kwargs)
    except BadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return None
        raise


async def deny(update: Update):
    if update.callback_query:
        await update.callback_query.answer("دسترسی ندارید.", show_alert=True)
    elif update.effective_message:
        await localized_reply(update.effective_message, "⛔ دسترسی ندارید.")


async def cf_request(account_row, method: str, path: str, *, params=None, json_body=None, content=None, timeout=30):
    try:
        token = decrypt_token(account_row["token_enc"])
    except InvalidToken as exc:
        raise RuntimeError("کلید رمزنگاری این اکانت با ENCRYPTION_KEY فعلی مطابقت ندارد؛ کلید قبلی را از بکاپ بازیابی کنید. Token را پاک نکنید.") from exc
    # Older builds sometimes saved an entire "Bearer ..." string.
    token = normalize_cf_token(token)
    headers = {"Authorization": f"Bearer {token}"}
    if content is None:
        headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.request(method, CF_API + path, headers=headers, params=params, json=json_body, content=content)
    ctype = r.headers.get("content-type", "")
    if "application/json" in ctype:
        data = r.json()
        if r.is_error or data.get("success") is False:
            errs = data.get("errors") or []
            msg = format_cf_errors(data, r.status_code)
            if "(6111)" in msg:
                msg += "\n🔑 مقدار API Token پذیرفته نشد. از داشبورد Cloudflare یک API Token تازه بساز؛ فقط مقدار خام آن را در «تعویض API» بفرست."
            raise RuntimeError(msg)
        return data
    if r.is_error:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.content


def esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def short(s: str, n=38):
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"




def home_text(acc):
    if not acc:
        return ("☁️ <b>Cloudflare Manager</b>  |  نسخه " + VERSION + "\n"
                "━━━━━━━━━━━━━━━━\n"
                "🔐 <b>حسابی متصل نیست</b>\n\n"
                "مدیریت دامنه‌ها، رکوردهای DNS و تنظیمات Cloudflare، مستقیم از تلگرام.\n\n"
                "برای شروع، «اتصال اولین حساب» را بزنید یا راهنمای اتصال را ببینید.")
    return ("☁️ <b>Cloudflare Manager</b>  |  نسخه " + VERSION + "\n"
            "━━━━━━━━━━━━━━━━\n\n"
            "🌐 از منوی زیر بخش موردنظر را انتخاب کنید. برای بررسی اعتبار توکن، از «حساب‌های Cloudflare ← تست اتصال» استفاده کنید.")


def page_navigation(page, max_page, prefix):
    result=[]
    if page>0:
        result.append(InlineKeyboardButton("◀️ قبلی",callback_data=f"{prefix}:{page-1}"))
    result.append(InlineKeyboardButton(f"{page+1}/{max_page+1}",callback_data="noop"))
    if page<max_page:
        result.append(InlineKeyboardButton("بعدی ▶️",callback_data=f"{prefix}:{page+1}"))
    return result


def is_private_chat(update):
    return getattr(update.effective_chat,"type",None)=="private"


async def account_detail_menu(q, aid, note="", page=0):
    a=get_account(aid)
    if a is None:
        return await accounts_menu(q,"حساب موردنظر پیدا نشد.")
    try:
        data = await cf_request(
            a, "GET", "/zones",
            params={"account.id": a["account_id"], "per_page": 10, "page": page + 1},
        )
        zones = data.get("result") or []
        info = data.get("result_info") or {}
        count = info.get("total_count", len(zones))
        status = "🟢 اتصال برقرار است"
        domain_text = f"🌐 دامنه‌های قابل‌مشاهده: <b>{count}</b>"
        if zones:
            domain_text += "\n" + "\n".join(
                f"• <code>{esc(zone['name'])}</code> · {esc(zone.get('status', '—'))}"
                for zone in zones
            )
        pagination = page_navigation(page, max(0, info.get("total_pages", 1) - 1), f"acc:view:{aid}")
    except (httpx.HTTPError, RuntimeError, ValueError):
        status = "⚠️ دریافت وضعیت اتصال ناموفق بود"
        domain_text = "برای بررسی خطا، تست اتصال را اجرا کنید."
        pagination = []

    kb = [pagination] if len(pagination) > 1 else []
    if a["active"]:
        kb.append([InlineKeyboardButton("🌐 مدیریت دامنه‌ها", callback_data="zones:0")])
    kb += [
        [InlineKeyboardButton("🧪 تست اتصال",callback_data=f"acc:test:{aid}"),
         InlineKeyboardButton("🔑 تعویض API Token",callback_data=f"acc:token:{aid}")],
        [InlineKeyboardButton("🗑 قطع اتصال و حذف",callback_data=f"acc:deleteask:{aid}")],
        [InlineKeyboardButton("↩️ همه حساب‌ها",callback_data="accounts"),
         InlineKeyboardButton("🏠 خانه",callback_data="main")],
    ]
    txt=((f"✅ {esc(note)}\n\n") if note else "")+(
        "👤 <b>جزئیات حساب Cloudflare</b>\n\n"
        f"🏷 نام: <b>{esc(a['label'])}</b>\n"
        f"📡 وضعیت: {status}\n"
        f"🆔 Account ID: <code>{esc(a['account_id'])}</code>\n"
        "🔐 API Token: <b>ذخیره‌شده و رمزنگاری‌شده</b>\n\n"
        f"{domain_text}")
    await safe_edit(q,txt,parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(kb))


async def guide_menu(q, section="start"):
    pages={
      "start": (
        "📖 <b>راهنمای اتصال Cloudflare</b>\n\n"
        "اتصال در ۳ مرحله انجام می‌شود:\n"
        "<b>۱.</b> ساخت API Token با مجوزهای محدود\n"
        "<b>۲.</b> دریافت Account ID\n"
        "<b>۳.</b> ثبت و تست حساب در ربات\n\n"
        "🔐 توکن را فقط در گفت‌وگوی خصوصی با ربات وارد کنید؛ هرگز Global API Key یا رمز عبور نفرستید."
      ),
      "token": (
        "🔑 <b>مرحله ۱ از ۳ | ساخت API Token</b>\n\n"
        "۱. وارد داشبورد Cloudflare شوید.\n"
        "۲. از <b>My Profile → API Tokens → Create Token</b> استفاده کنید.\n"
        "۳. قالب <b>Edit zone DNS</b> یا <b>Custom Token</b> را انتخاب کنید.\n"
        "۴. برای مشاهده Zoneها: <code>Zone → Zone → Read</code> و برای DNS: "
        "<code>Zone → DNS → Edit</code> را بررسی کنید.\n"
        "۵. در <b>Zone Resources</b> تنها دامنه‌های موردنیاز را انتخاب کنید.\n"
        "۶. توکن تولیدشده را یک‌بار کپی کنید.\n\n"
        "امکانات اضافی نیاز به مجوزهای متناسب دارند؛ مانند <b>Zone Settings Edit</b> و <b>Cache Purge</b>."
      ),
      "id": (
        "🆔 <b>مرحله ۲ از ۳ | Account ID</b>\n\n"
        "۱. در داشبورد Cloudflare حسابی که دامنه‌ها داخل آن هستند را باز کنید.\n"
        "۲. شناسه <b>Account ID</b> همان حساب را از صفحه حساب کپی کنید.\n"
        "۳. شناسه شامل ۳۲ کاراکتر هگزادسیمال است و با Zone ID فرق دارد.\n\n"
        "✅ حسابی را انتخاب کنید که دامنه‌های موردنظر واقعاً به آن تعلق دارند."
      ),
      "finish": (
        "✅ <b>مرحله ۳ از ۳ | اتصال در تلگرام</b>\n\n"
        "۱. «اتصال حساب» را بزنید.\n"
        "۲. یک نام دلخواه برای حساب تعیین کنید.\n"
        "۳. Account ID را وارد کنید.\n"
        "۴. مقدار خام API Token را ارسال کنید؛ بدون عبارت <code>Bearer</code>.\n"
        "۵. ربات توکن را با API واقعی Cloudflare بررسی می‌کند و فقط در صورت موفقیت ذخیره می‌کند.\n\n"
        "🧹 پیام حاوی توکن پس از دریافت برای حذف از گفت‌وگو تلاش می‌شود."
      ),
      "errors": (
        "🛠 <b>رفع خطاهای اتصال</b>\n\n"
        "<code>6003 / 6111</code>: هدر Authorization رد شده؛ مقدار خام API Token را دوباره کپی کنید.\n\n"
        "<code>403</code>: دامنه یا مجوز لازم در محدوده توکن نیست؛ Zone Resources و Permissionها را بررسی کنید.\n\n"
        "<code>404</code>: Account ID یا شناسه دامنه اشتباه است، یا امکان مشاهده آن را ندارید.\n\n"
        "<code>Message is not modified</code>: کلیک تکراری؛ منو باید بدون خطای نمایشی باقی بماند.\n\n"
        "⚠️ اگر توکن داخل گروه یا لاگ منتشر شده، از داشبورد Cloudflare آن را باطل و جایگزین کنید."
      ),
    }
    if section not in pages:
        section="start"
    kb=[]
    if section=="start":
        kb += [[InlineKeyboardButton("1️⃣ ساخت API Token",callback_data="guide:token")],
               [InlineKeyboardButton("2️⃣ پیدا کردن Account ID",callback_data="guide:id")],
               [InlineKeyboardButton("3️⃣ ثبت حساب در ربات",callback_data="guide:finish")],
               [InlineKeyboardButton("🛠 عیب‌یابی و خطاها",callback_data="guide:errors")]]
    else:
        kb += [[InlineKeyboardButton("🌐 باز کردن داشبورد Cloudflare",url="https://dash.cloudflare.com/")],
               [InlineKeyboardButton("🔑 صفحه API Tokens",url="https://dash.cloudflare.com/profile/api-tokens")],
               [InlineKeyboardButton("📖 فهرست راهنما",callback_data="guide")]]
    kb += [[InlineKeyboardButton("🔗 اتصال حساب",callback_data="acc:add")],
           [InlineKeyboardButton("🏠 خانه",callback_data="main")]]
    await safe_edit(q,pages[section],parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(kb))


async def fetch_all_zones(account):
    zones=[]
    for page in range(1,41):
        data=await cf_request(account,"GET","/zones",
                              params={"account.id":account["account_id"],"per_page":50,"page":page})
        chunk=data.get("result") or []
        zones.extend(chunk)
        info=data.get("result_info") or {}
        if (not chunk or (info.get("total_pages") and page >= info["total_pages"])
                or (not info.get("total_pages") and len(chunk)<50)):
            break
    return zones


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)
    return await localized_reply(update.effective_message, 
        "📖 <b>راهنمای ربات Cloudflare</b>\n\n"
        "برای مدیریت حساب‌ها و DNS از /start استفاده کنید. "
        "آموزش قدم‌به‌قدم اتصال در دکمه «راهنمای اتصال» موجود است.\n\n"
        "فرمان‌ها:\n"
        "/start — منوی اصلی\n"
        "/accounts — مدیریت حساب‌ها\n"
        "/help — راهنما\n"
        "/cancel — لغو عملیات"
        "\n/language — انتخاب زبان"
        "\n/menu — منوی اصلی",
        parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📖 راهنمای اتصال",callback_data="guide")],
            [InlineKeyboardButton("🏠 منوی اصلی",callback_data="main")]]))


async def accounts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open the exact same account listing as the inline Accounts button."""
    if not is_admin(update):
        return await deny(update)
    context.user_data.clear()
    text, keyboard = accounts_view()
    return await localized_reply(update.effective_message, 
        text, parse_mode=ParseMode.HTML, reply_markup=keyboard
    )


def language_view(success=False):
    """Language picker with native labels; the selected choice is persisted."""
    lang = CURRENT_LANGUAGE.get()
    titles = {
        "fa": "🌐 <b>زبان ربات</b>\n\nزبان دلخواهت رو انتخاب کن. این تنظیم بعد از ری‌استارت هم حفظ می‌شه.",
        "en": "🌐 <b>Bot language</b>\n\nChoose your preferred language. Your selection is saved across restarts.",
        "ru": "🌐 <b>Язык бота</b>\n\nВыберите язык интерфейса. Настройка сохранится после перезапуска.",
    }
    notice = {
        "fa": "✅ زبان ربات با موفقیت تغییر کرد.\n\n",
        "en": "✅ Bot language updated successfully.\n\n",
        "ru": "✅ Язык бота успешно изменён.\n\n",
    }
    home = {"fa": "🏠 منوی اصلی", "en": "🏠 Main menu", "ru": "🏠 Главное меню"}
    # Keep all three choices on one row with Persian in the middle.
    # Explicit layout order is intentional and independent of LANGUAGES.
    language_buttons = [
        InlineKeyboardButton(("✅ " if lang == code else "") + LANGUAGE_NAMES[code],
                             callback_data="lang:" + code)
        for code in ("en", "fa", "ru")
    ]
    kb = [language_buttons, [InlineKeyboardButton(home[lang], callback_data="main")]]
    return (notice[lang] if success else "") + titles[lang], InlineKeyboardMarkup(kb)


async def show_language_menu(query, success=False):
    text, keyboard = language_view(success)
    return await safe_edit(query, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)
    text, keyboard = language_view()
    return await localized_reply(update.effective_message, text,
                                 parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)
    context.user_data.clear()
    a=active_account()
    return await localized_reply(update.effective_message, 
        "✅ عملیات جاری لغو شد.\n\n"+home_text(a),parse_mode=ParseMode.HTML,
        reply_markup=main_kb(bool(a)))


def main_kb(connected=True):
    """Task-oriented two-column home screen with a clear onboarding path."""
    if not connected:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ اتصال سریع Cloudflare", callback_data="acc:quick")],
            [InlineKeyboardButton("🔗 اتصال اولین حساب", callback_data="acc:add")],
            [InlineKeyboardButton("📖 راهنمای گام‌به‌گام", callback_data="guide")],
            [InlineKeyboardButton("🌐 انتخاب زبان", callback_data="language")],
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 مدیریت دامنه‌ها", callback_data="zones:account"),
         InlineKeyboardButton("🔎 جستجوی DNS", callback_data="gsearch")],
        [InlineKeyboardButton("👤 حساب‌های Cloudflare", callback_data="accounts")],
        [InlineKeyboardButton("📖 راهنمای اتصال", callback_data="guide")],
        [InlineKeyboardButton("🌐 انتخاب زبان", callback_data="language")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)
    context.user_data.clear()
    acc = active_account()
    return await localized_reply(update.effective_message, 
        home_text(acc), parse_mode=ParseMode.HTML, reply_markup=main_kb(bool(acc))
    )


async def show_main(q, text=None):
    acc = active_account()
    return await safe_edit(q, text or home_text(acc),
                           parse_mode=ParseMode.HTML, reply_markup=main_kb(bool(acc)))


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return await deny(update)
    q = update.callback_query
    d = decode_callback_data(q.data or "")
    if (d in {"acc:add", "acc:quick"} or d.startswith("acc:token:")) and not is_private_chat(update):
        return await localized_answer(q, "🔒 اتصال حساب و ورود توکن فقط در گفت‌وگوی خصوصی مجاز است.",show_alert=True)
    if not d.startswith("lang:"):
        await localized_answer(q)
    if d != "noop":
        context.user_data.clear()
    try:
        if d == "noop": return
        if d in {"main", "about"}: return await show_main(q)
        if d == "language": return await show_language_menu(q)
        if d.startswith("lang:"):
            if not is_private_chat(update):
                return await localized_answer(q, "🔒 زبان را در گفت‌وگوی خصوصی تغییر دهید.", show_alert=True)
            language = d.split(":", 1)[1]
            if language not in LANGUAGES:
                return await localized_answer(q, "❌ زبان انتخاب‌شده معتبر نیست.", show_alert=True)
            await localized_answer(q)
            save_user_language(update.effective_user.id, language)
            CURRENT_LANGUAGE.set(language)
            context.user_data.clear()
            await publish_user_commands(context.bot, update.effective_chat.id, language)
            return await show_language_menu(q, success=True)

        if d == "guide": return await guide_menu(q)
        if d.startswith("guide:"): return await guide_menu(q,d.split(":",1)[1])
        if d.startswith("acc:view:"):
            parts = d.split(":")
            aid = int(parts[2])
            page = int(parts[3]) if len(parts) == 4 else 0
            return await account_detail_menu(q, aid, page=page)
        if d == "accounts": return await accounts_menu(q)
        if d == "acc:quick":
            context.user_data["state"] = "acc_quick_token"
            return await safe_edit(
                q,
                "⚡ <b>اتصال سریع Cloudflare</b>\n\n"
                "فرم ساخت توکن را باز کنید، حساب و دامنه‌های موردنیاز را انتخاب کنید و ساخت توکن را تأیید کنید.\n"
                "توکن را در همین گفت‌وگوی خصوصی بفرستید تا ربات حساب را پیدا کند.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔑 باز کردن فرم ساخت توکن", url=cloudflare_token_template())],
                    [InlineKeyboardButton("🔗 اتصال دستی", callback_data="acc:add")],
                    [InlineKeyboardButton("❌ لغو", callback_data="main")],
                ]),
            )
        if d == "acc:add":
            if not is_private_chat(update):
                return await localized_answer(q, "اتصال حساب فقط در گفت‌وگوی خصوصی مجاز است.",show_alert=True)
            context.user_data["state"] = "acc_label"
            return await safe_edit(q,
                "🔗 <b>اتصال حساب | مرحله ۱ از ۳</b>\n\n"
                "یک نام دلخواه برای شناسایی حساب وارد کنید.\n"
                "مثال: <code>حساب اصلی</code> یا <code>سایت‌های کاری</code>\n\n"
                "🔐 اطلاعات حساب فقط در همین گفت‌وگوی خصوصی دریافت می‌شود.",
                parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📖 راهنمای اتصال",callback_data="guide")],
                    [InlineKeyboardButton("❌ لغو",callback_data="accounts")]]))
        if d == "zones:account" or d.startswith("acc:select:"):
            rows = list_accounts()
            if not rows:
                return await zones_menu(q)
            keyboard = [
                [InlineKeyboardButton(short(row["label"], 35), callback_data=f"zones:account:{row['id']}")]
                for row in rows
            ]
            keyboard.append([InlineKeyboardButton("🏠 منوی اصلی", callback_data="main")])
            return await safe_edit(
                q, "👤 برای مدیریت دامنه‌ها، حساب Cloudflare را انتخاب کنید.",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        if d.startswith("zones:account:"):
            aid = int(d.rsplit(":", 1)[1])
            selected = get_account(aid)
            if selected is None:
                return await accounts_menu(q, "حساب موردنظر پیدا نشد.")
            if not selected["active"]:
                with closing(db_connect()) as con:
                    con.execute("UPDATE cf_accounts SET active=CASE WHEN id=? THEN 1 ELSE 0 END", (aid,))
                    con.commit()
            return await zones_menu(q)
        if d.startswith("acc:test:"):
            aid=int(d.rsplit(":",1)[1]); a=get_account(aid)
            if a is None: raise ValueError("اکانت پیدا نشد")
            try:
                token=decrypt_token(a["token_enc"])
                count=await verify_token_for_account(token,a["account_id"])
            except (InvalidToken, ValueError, RuntimeError) as exc:
                kb=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔑 تعویض API",callback_data=f"acc:token:{aid}")],
                    [InlineKeyboardButton("↩️ بازگشت به حساب",callback_data=f"acc:view:{aid}")],
                ])
                return await safe_edit(q,
                    "❌ <b>تست اتصال Cloudflare ناموفق بود</b>\n\n"
                    f"<code>{esc(exc)}</code>\n\n"
                    "اکانت و رکوردها پاک نشده‌اند.",
                    parse_mode=ParseMode.HTML,reply_markup=kb)
            return await safe_edit(q,
                "✅ <b>Cloudflare وصل است</b>\n\n"
                "درخواست خواندنی لیست دامنه‌ها موفق بود.\n"
                f"تعداد دامنه‌های قابل‌مشاهده: {count}",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ بازگشت به حساب",callback_data=f"acc:view:{aid}")]]))
        if d.startswith("acc:token:"):
            aid=int(d.rsplit(":",1)[1]); a=get_account(aid)
            if a is None: raise ValueError("اکانت پیدا نشد")
            context.user_data.clear()
            if not is_private_chat(update):
                return await localized_answer(q, "تعویض توکن فقط در گفت‌وگوی خصوصی مجاز است.",show_alert=True)
            context.user_data.update(state="acc_replace_token", edit_account_id=aid)
            return await safe_edit(q, 
                f"🔐 <b>تعویض API Token</b>\n\nاکانت: <b>{esc(a['label'])}</b>\n\n"
                "مقدار خام API Token جدید را بفرست؛ بدون Bearer.\n"
                "قبل از ذخیره، اتصال با Cloudflare آزمایش می‌شود.\n\n"
                "🔒 پیام حاوی توکن برای حذف از چت تلاش می‌شود.",
                parse_mode=ParseMode.HTML,reply_markup=cancel_kb(f"acc:view:{aid}"))
        if d.startswith("acc:deleteask:"):
            aid=int(d.rsplit(":",1)[1]); a=get_account(aid)
            if a is None: raise ValueError("حساب پیدا نشد")
            kb=InlineKeyboardMarkup([[InlineKeyboardButton("🗑 تأیید قطع اتصال",callback_data=f"acc:delete:{aid}")],[InlineKeyboardButton("↩️ لغو و بازگشت",callback_data=f"acc:view:{aid}")]])
            return await safe_edit(q, f"⚠️ <b>تأیید قطع اتصال حساب</b>\n\nحساب: <b>{esc(a['label'])}</b>\n\nتوکن ذخیره‌شده از ربات حذف می‌شود؛ خود حساب و دامنه‌های Cloudflare دست‌نخورده می‌مانند.",parse_mode=ParseMode.HTML,reply_markup=kb)
        if d.startswith("acc:delete:"):
            aid=int(d.rsplit(":",1)[1])
            with closing(db_connect()) as con:
                con.execute("DELETE FROM cf_accounts WHERE id=?",(aid,)); con.commit()
            active_account()
            return await accounts_menu(q,"✅ اطلاعات اکانت از ربات حذف شد.")
        if d.startswith("zones:"):
            return await zones_menu(q, int(d.split(":")[1]))
        if d.startswith("zone:") and d.count(":")==1:
            zid=d.split(":")[1]; return await zone_menu(q,zid)
        if d.startswith("dns:"):
            _,zid,page=d.split(":",2); return await dns_menu(q,zid,int(page))
        if d.startswith("rtypepage:"):
            _,zid,gidx=d.split(":"); return await record_type_menu(q,zid,int(gidx))
        if d.startswith("rtype:"):
            _,zid,rtype=d.split(":",2)
            context.user_data.clear(); context.user_data.update(state="add_name",zone_id=zid,record_type=rtype)
            return await safe_edit(q, f"➕ <b>رکورد جدید | مرحله ۱ از ۲</b>\n\n"
                f"نوع رکورد: <b>{rtype}</b>\n\n"
                "نام میزبان را بفرستید.\n"
                "مثال: <code>@</code> برای دامنه اصلی، <code>api</code> برای زیردامنه، یا نام کامل.",parse_mode=ParseMode.HTML,reply_markup=cancel_kb(f"dns:{zid}:0"))
        if d.startswith("rec:"):
            _,zid,rid=d.split(":",2); return await record_menu(q,zid,rid)
        if d.startswith("rproxy:"):
            _,zid,rid=d.split(":",2); return await toggle_proxy(q,zid,rid)
        if d.startswith("redit:"):
            _,field,zid,rid=d.split(":",3)
            context.user_data.clear(); context.user_data.update(state=f"edit_{field}",zone_id=zid,record_id=rid)
            prompts={"name":"نام جدید رکورد را بفرست.","content":"مقدار/Content جدید را بفرست.","ttl":"TTL را بفرست: <code>1</code> برای Auto یا 60 تا 86400.","comment":"Comment جدید را بفرست؛ برای پاک‌کردن <code>-</code> بفرست."}
            return await safe_edit(q, "✏️ "+prompts[field],parse_mode=ParseMode.HTML,reply_markup=cancel_kb(f"rec:{zid}:{rid}"))
        if d.startswith("rdeleteask:"):
            _,zid,rid=d.split(":",2)
            kb=InlineKeyboardMarkup([[InlineKeyboardButton("🗑 حذف قطعی",callback_data=f"rdelete:{zid}:{rid}")],[InlineKeyboardButton("⬅️ برگشت",callback_data=f"rec:{zid}:{rid}")]])
            return await safe_edit(q, "⚠️ <b>تأیید حذف رکورد DNS</b>\n\nاین عملیات غیرقابل‌بازگشت است. برای ادامه «حذف قطعی» را انتخاب کنید.",reply_markup=kb)
        if d.startswith("rdelete:"):
            _,zid,rid=d.split(":",2); a=active_account(); await cf_request(a,"DELETE",f"/zones/{zid}/dns_records/{rid}")
            return await dns_menu(q,zid,0,"✅ رکورد حذف شد.")
        if d.startswith("search:"):
            zid=d.split(":",1)[1]; context.user_data.clear(); context.user_data.update(state="zone_search",zone_id=zid)
            return await safe_edit(q, "🔎 بخشی از نام، IP/Content یا نوع رکورد را بفرست.",reply_markup=cancel_kb(f"dns:{zid}:0"))
        if d == "gsearch":
            context.user_data.clear(); context.user_data["state"]="global_search"
            return await safe_edit(q, "🔎 <b>جستجوی سراسری DNS</b>\n\nنام رکورد، IP یا نوع آن را بفرستید. جستجو در دامنه‌های قابل‌دسترسی حساب فعال انجام می‌شود.",parse_mode=ParseMode.HTML,reply_markup=cancel_kb("main"))
        if d.startswith("settings:"):
            zid=d.split(":",1)[1]; return await settings_menu(q,zid)
        if d.startswith("settoggle:"):
            _,zid,key=d.split(":",2); return await toggle_setting(q,zid,key)
        if d.startswith("cache:"):
            zid=d.split(":",1)[1]; return await cache_menu(q,zid)
        if d.startswith("purgeall:"):
            zid=d.split(":",1)[1]
            return await safe_edit(q,
                "⚠️ <b>تأیید پاک‌سازی کامل کش</b>\n\nهمه محتوای Cache دامنه حذف می‌شود و ممکن است بار سرور اصلی افزایش پیدا کند. ادامه می‌دهید؟",
                parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔥 بله، پاک‌سازی کامل",callback_data=f"purgeconfirm:{zid}")],
                    [InlineKeyboardButton("↩️ انصراف",callback_data=f"cache:{zid}")]]))
        if d.startswith("purgeconfirm:"):
            zid=d.split(":",1)[1]; a=active_account()
            await cf_request(a,"POST",f"/zones/{zid}/purge_cache",json_body={"purge_everything":True})
            return await safe_edit(q,"✅ کش دامنه با موفقیت پاک شد.",reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ مدیریت کش",callback_data=f"cache:{zid}")]]))
        if d.startswith("purgeurl:"):
            zid=d.split(":",1)[1]; context.user_data.clear(); context.user_data.update(state="purge_url",zone_id=zid)
            return await safe_edit(q, "🧹 <b>پاک‌سازی یک URL</b>\n\nآدرس کامل را همراه با <code>https://</code> ارسال کنید.",parse_mode=ParseMode.HTML,reply_markup=cancel_kb(f"cache:{zid}"))
        if d.startswith("dnssec:"):
            zid=d.split(":",1)[1]; return await dnssec_menu(q,zid)
        if d.startswith("dnssectoggle:"):
            zid=d.split(":",1)[1]
            return await safe_edit(q,
                "⚠️ <b>تأیید تغییر DNSSEC</b>\n\nتغییر DNSSEC بدون هماهنگی رکورد DS در رجیسترار می‌تواند باعث اختلال در دسترسی دامنه شود. ادامه می‌دهید؟",
                parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ تأیید تغییر وضعیت",callback_data=f"dnssecapply:{zid}")],
                    [InlineKeyboardButton("↩️ انصراف",callback_data=f"dnssec:{zid}")]]))
        if d.startswith("dnssecapply:"):
            zid=d.split(":",1)[1]; return await dnssec_toggle(q,zid)
        if d.startswith("export:"):
            zid=d.split(":",1)[1]; return await export_dns(q,zid)
        if d.startswith("import:"):
            zid=d.split(":",1)[1]; context.user_data.clear(); context.user_data.update(state="import_bind",zone_id=zid)
            return await safe_edit(q, "📥 <b>ورود رکوردهای DNS</b>\n\nفایل BIND معتبر را به‌صورت Document ارسال کنید.\n⚠️ ورود فایل می‌تواند رکوردهای جدید بسازد؛ فایل را پیش از ارسال بازبینی کنید.",parse_mode=ParseMode.HTML,reply_markup=cancel_kb(f"zone:{zid}"))
        if d.startswith("zonepause:"):
            zid=d.split(":",1)[1]
            z=await get_zone(zid)
            label="فعال‌سازی مجدد" if z.get("paused") else "توقف موقت"
            return await safe_edit(q,
                f"⚠️ <b>تأیید {label} دامنه</b>\n\nدامنه: <b>{esc(z['name'])}</b>\n"
                "این عملیات تنظیمات ترافیک دامنه را تغییر می‌دهد. ادامه می‌دهید؟",
                parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ بله، اعمال تغییر",callback_data=f"zonepauseapply:{zid}")],
                    [InlineKeyboardButton("↩️ انصراف",callback_data=f"zone:{zid}")]]))
        if d.startswith("zonepauseapply:"):
            zid=d.split(":",1)[1]; return await toggle_zone_pause(q,zid)
        if d.startswith("zoneinfo:"):
            zid=d.split(":",1)[1]; return await zone_info(q,zid)
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            return
        log.exception("Telegram callback failed")
        try:
            await safe_edit(q, f"❌ <b>Telegram Error</b>\n<code>{esc(e)}</code>",
                parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ اکانت‌ها",callback_data="accounts")],
                    [InlineKeyboardButton("🏠 منوی اصلی",callback_data="main")]]))
        except Exception: pass
    except Exception as e:
        log.exception("callback failed")
        a=active_account()
        kb=[]
        if a and ("6111" in str(e) or "6003" in str(e)):
            kb.append([InlineKeyboardButton("🔑 تعویض API اکانت فعال",callback_data=f"acc:token:{a['id']}")])
        kb.append([InlineKeyboardButton("👤 اکانت‌ها",callback_data="accounts")])
        kb.append([InlineKeyboardButton("🏠 منوی اصلی",callback_data="main")])
        try:
            await safe_edit(q, f"❌ <b>خطای اتصال/عملیات</b>\n<code>{esc(e)}</code>",
                parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(kb))
        except Exception: pass


def cancel_kb(target="main"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو عملیات", callback_data=target)],
                                 [InlineKeyboardButton("🏠 منوی اصلی",callback_data="main")]])


def accounts_view(note=""):
    """Build a single account screen shared by /accounts and inline buttons."""
    rows = list_accounts()
    kb = [[InlineKeyboardButton("☁️ " + short(a["label"], 29),
                                callback_data=f"acc:view:{a['id']}")] for a in rows]
    kb.append([InlineKeyboardButton("⚡ اتصال سریع Cloudflare", callback_data="acc:quick")])
    kb.append([InlineKeyboardButton("➕ افزودن حساب جدید", callback_data="acc:add")])
    kb.append([InlineKeyboardButton("📖 راهنمای اتصال حساب", callback_data="guide")])
    kb.append([InlineKeyboardButton("🏠 منوی اصلی", callback_data="main")])
    desc = ("برای مدیریت یا تست اتصال، یک حساب را انتخاب کنید."
            if rows else "هنوز حسابی متصل نشده است. با دکمه زیر شروع کنید.")
    text = (f"{esc(note)}\n\n" if note else "") + (
        "👤 <b>حساب‌های Cloudflare</b>\n\n"
        f"📂 تعداد حساب‌ها: <b>{len(rows)}</b>\n\n"
        f"{desc}"
    )
    return text, InlineKeyboardMarkup(kb)


async def accounts_menu(q, note=""):
    text, keyboard = accounts_view(note)
    return await safe_edit(q, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def zones_menu(q, page=0, note=""):
    a = active_account()
    if not a:
        return await safe_edit(q, "🌐 <b>مدیریت دامنه‌ها</b>\n\nابتدا یک حساب Cloudflare متصل کنید.",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 اتصال حساب", callback_data="acc:add")],
                [InlineKeyboardButton("📖 راهنما", callback_data="guide")],
                [InlineKeyboardButton("🏠 منوی اصلی", callback_data="main")]]))
    zones = await fetch_all_zones(a)
    max_page = max(0, (len(zones)-1)//PAGE_SIZE)
    page = min(max(0, page), max_page)
    chunk = zones[page*PAGE_SIZE:(page+1)*PAGE_SIZE]
    kb = [[InlineKeyboardButton(("🟢 " if z.get("status")=="active" else "🟡 ")+short(z['name'], 35),
                                callback_data=f"zone:{z['id']}")] for z in chunk]
    if max_page:
        kb.append(page_navigation(page, max_page, "zones"))
    kb.extend([[InlineKeyboardButton("🔄 تازه‌سازی", callback_data=f"zones:{page}"),
                InlineKeyboardButton("👤 حساب‌ها", callback_data="accounts")],
               [InlineKeyboardButton("🏠 منوی اصلی", callback_data="main")]])
    text = ((f"✅ {esc(note)}\n\n") if note else "") + (
        "🌐 <b>مدیریت دامنه‌ها</b>\n\n"
        f"👤 حساب: <b>{esc(a['label'])}</b>\n"
        f"📂 دامنه‌ها: <b>{len(zones)}</b>\n"
        f"📄 صفحه: <b>{page+1} از {max_page+1}</b>\n\n"
        + ("برای مدیریت DNS و تنظیمات، یک دامنه را انتخاب کنید."
           if zones else "دامنه‌ای در محدوده دسترسی این توکن پیدا نشد. مجوز Zone Read را بررسی کنید.")
    )
    await safe_edit(q, text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


async def get_zone(zid):
    a=active_account(); return (await cf_request(a,"GET",f"/zones/{zid}"))["result"]


async def zone_menu(q, zid):
    z = await get_zone(zid)
    state = "🟢 فعال" if z.get('status') == 'active' else f"🟡 {esc(z.get('status', 'نامشخص'))}"
    paused = "▶️ فعال‌سازی مجدد دامنه" if z.get("paused") else "⏸ توقف موقت دامنه"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧩 رکوردهای DNS", callback_data=f"dns:{zid}:0")],
        [InlineKeyboardButton("🔎 جستجوی رکورد", callback_data=f"search:{zid}"),
         InlineKeyboardButton("🛡️ تنظیمات امنیتی و SSL", callback_data=f"settings:{zid}")],
        [InlineKeyboardButton("🧹 مدیریت کش", callback_data=f"cache:{zid}"),
         InlineKeyboardButton("🔐 DNSSEC", callback_data=f"dnssec:{zid}")],
        [InlineKeyboardButton("📤 خروجی DNS", callback_data=f"export:{zid}"),
         InlineKeyboardButton("📥 ورود رکوردها", callback_data=f"import:{zid}")],
        [InlineKeyboardButton("ℹ️ جزئیات دامنه", callback_data=f"zoneinfo:{zid}")],
        [InlineKeyboardButton(paused, callback_data=f"zonepause:{zid}")],
        [InlineKeyboardButton("↩️ فهرست دامنه‌ها", callback_data="zones:0"),
         InlineKeyboardButton("🏠 خانه", callback_data="main")],
    ])
    return await safe_edit(q,
        f"🌐 <b>{esc(z['name'])}</b>\n\n"
        f"📡 وضعیت: {state}\n"
        f"💎 پلن: <code>{esc((z.get('plan') or {}).get('name','-'))}</code>\n"
        f"⏸ توقف موقت: {'بله' if z.get('paused') else 'خیر'}\n\n"
        "یکی از بخش‌های مدیریت دامنه را انتخاب کنید.",
        parse_mode=ParseMode.HTML, reply_markup=kb)


async def list_records(zid):
    a = active_account()
    records = []
    for page in range(1, 41):
        data = await cf_request(a, "GET", f"/zones/{zid}/dns_records",
                                params={"per_page": 500, "page": page})
        batch = data.get("result") or []
        records.extend(batch)
        info = data.get("result_info") or {}
        if (not batch or (info.get("total_pages") and page >= info["total_pages"])
                or (not info.get("total_pages") and len(batch) < 500)):
            break
    return records


def record_label(r):
    cloud="🟠" if r.get("proxied") else "⚪"
    return f"{cloud} {r.get('type')} {short(r.get('name',''),22)} → {short(r.get('content',''),24)}"


async def dns_menu(q,zid,page=0,note=""):
    recs=await list_records(zid)
    max_page=max(0,(len(recs)-1)//PAGE_SIZE)
    page=max(0,min(page,max_page))
    chunk=recs[page*PAGE_SIZE:(page+1)*PAGE_SIZE]
    kb=[[InlineKeyboardButton(record_label(r),callback_data=f"rec:{zid}:{r['id']}")] for r in chunk]
    if max_page:
        kb.append(page_navigation(page,max_page,f"dns:{zid}"))
    kb += [
        [InlineKeyboardButton("➕ ساخت رکورد",callback_data=f"rtypepage:{zid}:0"),
         InlineKeyboardButton("🔎 جستجو",callback_data=f"search:{zid}")],
        [InlineKeyboardButton("🔄 تازه‌سازی",callback_data=f"dns:{zid}:{page}")],
        [InlineKeyboardButton("↩️ بازگشت به دامنه",callback_data=f"zone:{zid}"),
         InlineKeyboardButton("🏠 خانه",callback_data="main")]]
    txt=((f"✅ {esc(note)}\n\n") if note else "")+(
        "🧩 <b>مدیریت رکوردهای DNS</b>\n\n"
        f"📋 تعداد رکوردها: <b>{len(recs)}</b>\n"
        f"📄 صفحه: <b>{page+1} از {max_page+1}</b>\n\n"
        + ("🟠 = پروکسی Cloudflare  |  ⚪ = فقط DNS\nبرای ویرایش، رکورد را انتخاب کنید."
           if recs else "هنوز رکوردی وجود ندارد؛ از «ساخت رکورد» استفاده کنید."))
    await safe_edit(q,txt,parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(kb))


async def record_type_menu(q,zid,gidx=0):
    gidx = max(0, min(gidx, len(TYPE_GROUPS)-1))
    title,types=TYPE_GROUPS[gidx]
    kb=[]
    for i in range(0,len(types),3):
        kb.append([InlineKeyboardButton(t,callback_data=f"rtype:{zid}:{t}") for t in types[i:i+3]])
    kb.append([InlineKeyboardButton(("✅ " if i==gidx else "▫️ ")+TYPE_GROUPS[i][0],callback_data=f"rtypepage:{zid}:{i}") for i in range(len(TYPE_GROUPS))])
    kb.append([InlineKeyboardButton("↩️ رکوردها",callback_data=f"dns:{zid}:0")])
    await safe_edit(q,
        f"➕ <b>ساخت رکورد DNS</b>\n\n"
        f"دسته: <b>{esc(title)}</b>\n"
        "نوع رکورد را انتخاب کنید. سپس نام و مقدار آن را در مراحل بعد وارد می‌کنید.",
        parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(kb))


async def get_record(zid,rid):
    a=active_account(); return (await cf_request(a,"GET",f"/zones/{zid}/dns_records/{rid}"))["result"]


async def record_menu(q,zid,rid):
    r=await get_record(zid,rid)
    prox="🟠 پروکسی فعال" if r.get("proxied") else "⚪ فقط DNS"
    kb=[
        [InlineKeyboardButton("✏️ ویرایش نام",callback_data=f"redit:name:{zid}:{rid}"),
         InlineKeyboardButton("🎯 ویرایش مقدار",callback_data=f"redit:content:{zid}:{rid}")],
        [InlineKeyboardButton("⏱ تغییر TTL",callback_data=f"redit:ttl:{zid}:{rid}"),
         InlineKeyboardButton("💬 توضیحات",callback_data=f"redit:comment:{zid}:{rid}")],
    ]
    if r.get("proxiable") or r.get("type") in PROXY_TYPES:
        kb.append([InlineKeyboardButton(f"🔄 تغییر وضعیت پروکسی | {prox}",callback_data=f"rproxy:{zid}:{rid}")])
    kb += [[InlineKeyboardButton("🗑 حذف رکورد",callback_data=f"rdeleteask:{zid}:{rid}")],
           [InlineKeyboardButton("↩️ فهرست رکوردها",callback_data=f"dns:{zid}:0")]]
    txt=(f"🧩 <b>جزئیات رکورد {esc(r.get('type'))}</b>\n\n"
         f"📛 نام: <code>{esc(r.get('name'))}</code>\n"
         f"🎯 مقدار: <code>{esc(short(r.get('content') or '', 1100))}</code>\n"
         f"⏱ TTL: <code>{esc(r.get('ttl'))}</code>\n"
         f"☁️ وضعیت: {prox}\n"
         f"💬 توضیحات: <code>{esc(r.get('comment') or '-')}</code>\n"
         f"🆔 شناسه: <code>{esc(r.get('id'))}</code>")
    await safe_edit(q,txt,parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(kb))


async def toggle_proxy(q,zid,rid):
    a=active_account(); r=await get_record(zid,rid)
    await cf_request(a,"PATCH",f"/zones/{zid}/dns_records/{rid}",json_body={"proxied":not bool(r.get("proxied"))})
    await record_menu(q,zid,rid)


async def settings_menu(q,zid):
    a=active_account()
    keys=["always_use_https","automatic_https_rewrites","tls_1_3","brotli","http3","websockets","ipv6"]
    vals={}
    for k in keys:
        try:
            vals[k]=(await cf_request(a,"GET",f"/zones/{zid}/settings/{k}"))["result"]
        except Exception:
            vals[k]={"id":k,"value":None}
    def b(k,label):
        v=vals[k].get("value")
        state="🟢" if v=="on" else ("⚪" if v=="off" else "🔒")
        return InlineKeyboardButton(f"{state} {label}",callback_data=f"settoggle:{zid}:{k}")
    kb=InlineKeyboardMarkup([
        [b("always_use_https","اجبار HTTPS"),b("automatic_https_rewrites","بازنویسی HTTPS")],
        [b("tls_1_3","TLS 1.3"),b("brotli","فشرده‌سازی Brotli")],
        [b("http3","HTTP/3"),b("websockets","WebSocket")],
        [b("ipv6","IPv6")],
        [InlineKeyboardButton("↩️ بازگشت به دامنه",callback_data=f"zone:{zid}")]])
    await safe_edit(q,
        "🛡️ <b>تنظیمات امنیت و شبکه</b>\n\n"
        "🟢 روشن  |  ⚪ خاموش  |  🔒 غیرقابل‌دسترسی\n\n"
        "با انتخاب هر گزینه، وضعیت آن تغییر می‌کند. برخی قابلیت‌ها به سطح دسترسی API یا پلن شما وابسته‌اند.",
        parse_mode=ParseMode.HTML,reply_markup=kb)


async def toggle_setting(q,zid,key):
    a=active_account(); cur=(await cf_request(a,"GET",f"/zones/{zid}/settings/{key}"))["result"].get("value")
    if cur not in ("on","off"): return await localized_answer(q, f"این گزینه Toggle ساده نیست: {cur}",show_alert=True)
    await cf_request(a,"PATCH",f"/zones/{zid}/settings/{key}",json_body={"value":"off" if cur=="on" else "on"})
    await settings_menu(q,zid)


async def cache_menu(q,zid):
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 پاک‌سازی یک آدرس (URL)",callback_data=f"purgeurl:{zid}")],
        [InlineKeyboardButton("🔥 پاک‌سازی تمام کش",callback_data=f"purgeall:{zid}")],
        [InlineKeyboardButton("↩️ بازگشت به دامنه",callback_data=f"zone:{zid}")]])
    await safe_edit(q,
        "🧹 <b>مدیریت کش Cloudflare</b>\n\n"
        "می‌توانید محتوای ذخیره‌شده برای یک URL مشخص یا کل دامنه را پاک کنید.\n\n"
        "⚠️ پاک‌سازی کامل ممکن است موقتاً بار سرور اصلی را بیشتر کند.",
        parse_mode=ParseMode.HTML,reply_markup=kb)


async def dnssec_menu(q,zid):
    a=active_account()
    data=(await cf_request(a,"GET",f"/zones/{zid}/dnssec"))["result"]
    status=data.get("status","disabled")
    txt=(f"🔐 <b>تنظیمات DNSSEC</b>\n\n"
         f"📡 وضعیت: <code>{esc(status)}</code>\n"
         f"🔢 الگوریتم: <code>{esc(data.get('algorithm','-'))}</code>\n"
         f"🔎 Digest: <code>{esc(data.get('digest','-'))}</code>\n"
         f"🧾 DS Record: <code>{esc(data.get('ds','-'))}</code>\n\n"
         "⚠️ برای فعال‌سازی کامل، ممکن است ثبت DS در رجیسترار دامنه لازم باشد.")
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تغییر وضعیت DNSSEC",callback_data=f"dnssectoggle:{zid}")],
        [InlineKeyboardButton("↩️ بازگشت به دامنه",callback_data=f"zone:{zid}")]])
    await safe_edit(q,txt,parse_mode=ParseMode.HTML,reply_markup=kb)


async def dnssec_toggle(q,zid):
    """Cloudflare DNSSEC enable/disable is PATCH with a status body."""
    a=active_account()
    data=(await cf_request(a,"GET",f"/zones/{zid}/dnssec"))["result"]
    new_status=("disabled" if data.get("status") in ("active","pending") else "active")
    await cf_request(a,"PATCH",f"/zones/{zid}/dnssec",json_body={"status":new_status})
    await dnssec_menu(q,zid)


async def export_dns(q,zid):
    a=active_account(); raw=await cf_request(a,"GET",f"/zones/{zid}/dns_records/export")
    z=await get_zone(zid); bio=io.BytesIO(raw); bio.name=f"{z['name']}.bind"
    await localized_document_reply(q.message, document=bio,filename=bio.name,caption=f"📤 DNS export — {z['name']}")
    await localized_answer(q, "Export آماده شد")


async def toggle_zone_pause(q,zid):
    a=active_account(); z=await get_zone(zid); await cf_request(a,"PATCH",f"/zones/{zid}",json_body={"paused":not bool(z.get("paused"))}); await zone_menu(q,zid)


async def zone_info(q,zid):
    z=await get_zone(zid)
    ns="\n".join(z.get("name_servers") or [])
    txt=(f"ℹ️ <b>اطلاعات دامنه {esc(z['name'])}</b>\n\n"
         f"📡 وضعیت: <code>{esc(z.get('status'))}</code>\n"
         f"⏸ توقف موقت: <code>{'بله' if z.get('paused') else 'خیر'}</code>\n"
         f"📂 نوع: <code>{esc(z.get('type'))}</code>\n"
         f"💎 پلن: <code>{esc((z.get('plan') or {}).get('name','-'))}</code>\n"
         f"🆔 Zone ID: <code>{esc(zid)}</code>\n\n"
         f"🌐 <b>Name Servers</b>\n<code>{esc(ns)}</code>")
    await safe_edit(q,txt,parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("↩️ بازگشت به دامنه",callback_data=f"zone:{zid}")]]))


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return await deny(update)
    state=context.user_data.get("state")
    if not state: return
    msg=update.effective_message; text=(msg.text or "").strip()
    try:
        if state in {"acc_label","acc_id","acc_token","acc_replace_token","acc_quick_token"} and not is_private_chat(update):
            context.user_data.clear()
            try: await msg.delete()
            except Exception: pass
            return await localized_send(update.effective_chat, "🔒 ورود اطلاعات اتصال فقط در گفت‌وگوی خصوصی مجاز است. /start")
        if state=="acc_label":
            if not (1 <= len(text) <= 60):
                raise ValueError("نام حساب باید بین ۱ تا ۶۰ کاراکتر باشد.")
            context.user_data.update(state="acc_id",label=text)
            return await localized_reply(msg, 
                "🆔 <b>اتصال حساب | مرحله ۲ از ۳</b>\n\n"
                "Account ID مربوط به همین حساب را ارسال کنید.\n"
                "این شناسه ۳۲ کاراکتر دارد و با Zone ID متفاوت است.\n\n"
                "راهنما: Cloudflare Dashboard → انتخاب حساب → Account ID",
                parse_mode=ParseMode.HTML,reply_markup=cancel_kb("accounts"))
        if state=="acc_id":
            if not re.fullmatch(r"[0-9a-fA-F]{32}",text):
                raise ValueError("Account ID باید دقیقاً ۳۲ کاراکتر هگزادسیمال باشد. لطفاً شناسه حساب را دوباره وارد کنید.")
            context.user_data.update(state="acc_token",account_id=text.lower())
            return await localized_reply(msg, 
                "🔑 <b>اتصال حساب | مرحله ۳ از ۳</b>\n\n"
                "API Token ساخته‌شده را ارسال کنید؛ فقط مقدار خام توکن، بدون Bearer.\n"
                "توکن باید به دامنه‌های این حساب و دست‌کم مجوز مشاهده Zone دسترسی داشته باشد.\n\n"
                "🔒 بعد از دریافت، پیام حاوی توکن را حذف می‌کنم و اتصال را می‌آزمایم.",
                parse_mode=ParseMode.HTML,reply_markup=cancel_kb("accounts"))
        if state in ("acc_token", "acc_replace_token", "acc_quick_token"):
            # Private-chat messages can be deleted by bots; failures are nonfatal.
            try: await msg.delete()
            except Exception: pass
            token=normalize_cf_token(text)
            if state=="acc_replace_token":
                aid=context.user_data["edit_account_id"]
                account=get_account(aid)
                if account is None: raise ValueError("اکانت پیدا نشد")
                await verify_token_for_account(token, account["account_id"])
                with closing(db_connect()) as con:
                    con.execute("UPDATE cf_accounts SET token_enc=? WHERE id=?",(encrypt_token(token),aid))
                    con.commit()
                context.user_data.clear()
                return await localized_send(update.effective_chat, 
                    "✅ <b>API Token با موفقیت تعویض شد.</b>\n\nتوکن جدید آزمایش و به‌صورت رمزنگاری‌شده ذخیره شد.",
                    parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([
                      [InlineKeyboardButton("👤 مشاهده حساب",callback_data=f"acc:view:{aid}")],
                      [InlineKeyboardButton("🏠 منوی اصلی",callback_data="main")]]))
            if state == "acc_quick_token":
                discovered = await discover_token_account(token)
                if discovered is None:
                    return await localized_send(
                        update.effective_chat,
                        "شناسه حساب یکتا پیدا نشد. توکن را به دامنه‌های یک حساب محدود کنید یا از اتصال دستی استفاده کنید.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔗 اتصال دستی", callback_data="acc:add")],
                            [InlineKeyboardButton("❌ لغو", callback_data="main")],
                        ]),
                    )
                accid, label = discovered
            else:
                label = context.user_data["label"]
                accid = context.user_data["account_id"]
            await verify_token_for_account(token, accid)
            enc=encrypt_token(token)
            with closing(db_connect()) as con:
                exists=con.execute("SELECT COUNT(*) FROM cf_accounts").fetchone()[0]
                cursor = con.execute(
                    "INSERT INTO cf_accounts(label,account_id,token_enc,active,created_at) VALUES(?,?,?,?,?)",
                    (label, accid, enc, 0 if exists else 1, datetime.now(timezone.utc).isoformat()),
                )
                aid = cursor.lastrowid
                con.commit()
            context.user_data.clear()
            return await localized_send(update.effective_chat, 
                "✅ <b>حساب Cloudflare متصل شد!</b>\n\n"
                f"👤 نام: <b>{esc(label)}</b>\n"
                "🔐 توکن اعتبارسنجی و رمزنگاری شد. اکنون می‌توانید دامنه‌ها را مدیریت کنید.",
                parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👤 مشاهده حساب", callback_data=f"acc:view:{aid}")],
                    [InlineKeyboardButton("👤 حساب‌های من",callback_data="accounts")],
                    [InlineKeyboardButton("🏠 منوی اصلی",callback_data="main")]]))
        if state=="add_name":
            zid=context.user_data["zone_id"]; rtype=context.user_data["record_type"]
            z=await get_zone(zid); name=text
            if name=="@": name=z["name"]
            elif "." not in name: name=f"{name}.{z['name']}"
            context.user_data.update(state="add_content",record_name=name)
            hint=TYPE_HINTS.get(rtype,"مقدار رکورد را بفرست.")
            return await localized_reply(msg, f"🎯 مقدار <b>{rtype}</b> را بفرست.\n\n{hint}",parse_mode=ParseMode.HTML,reply_markup=cancel_kb(f"dns:{zid}:0"))
        if state=="add_content":
            rtype=context.user_data["record_type"]; context.user_data["content"]=text
            if rtype=="A":
                import ipaddress
                if ipaddress.ip_address(text).version != 4: raise ValueError("برای رکورد A باید IPv4 وارد شود.")
            if rtype=="AAAA":
                import ipaddress
                if ipaddress.ip_address(text).version != 6: raise ValueError("برای رکورد AAAA باید IPv6 وارد شود.")
            if rtype in PRIORITY_TYPES:
                context.user_data["state"]="add_priority"
                return await localized_reply(msg, "⏱ اولویت (Priority) را با عدد ۰ تا ۶۵۵۳۵ ارسال کنید.",reply_markup=cancel_kb(f"dns:{context.user_data['zone_id']}:0"))
            return await create_pending_record(msg,context)
        if state=="add_priority":
            p=int(text)
            if not 0<=p<=65535: raise ValueError("Priority باید بین ۰ و ۶۵۵۳۵ باشد.")
            context.user_data["priority"]=p; return await create_pending_record(msg,context)
        if state.startswith("edit_"):
            field=state[5:]; zid=context.user_data["zone_id"]; rid=context.user_data["record_id"]; body={}
            if field=="ttl":
                v=int(text)
                if not (v==1 or 60<=v<=86400): raise ValueError("TTL باید ۱ (خودکار) یا عدد ۶۰ تا ۸۶۴۰۰ باشد.")
                body["ttl"]=v
            elif field=="comment": body["comment"]=None if text=="-" else text
            else:
                if field in ("content", "name") and not text: raise ValueError("مقدار جدید نمی‌تواند خالی باشد.")
                body[field]=text
            a=active_account(); await cf_request(a,"PATCH",f"/zones/{zid}/dns_records/{rid}",json_body=body); context.user_data.clear()
            return await localized_reply(msg, "✅ رکورد با موفقیت ویرایش شد.",reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ بازگشت به رکورد",callback_data=f"rec:{zid}:{rid}")]]))
        if state=="zone_search":
            zid=context.user_data["zone_id"]; recs=await list_records(zid); term=text.lower(); found=[r for r in recs if term in str(r.get("name","")).lower() or term in str(r.get("content","")).lower() or term==str(r.get("type","")).lower()]
            kb=[[InlineKeyboardButton(record_label(r),callback_data=f"rec:{zid}:{r['id']}")] for r in found[:30]]; kb.append([InlineKeyboardButton("⬅️ رکوردها",callback_data=f"dns:{zid}:0")]); context.user_data.clear()
            return await localized_reply(msg, f"🔎 <b>نتایج جستجوی DNS</b>\n\nتعداد نتیجه: {len(found)}",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(kb))
        if state=="global_search":
            a=active_account(); zones=await fetch_all_zones(a); term=text.lower(); matches=[]
            for z in zones:
                try:
                    recs=await list_records(z["id"])
                    for r in recs:
                        if term in str(r.get("name","")).lower() or term in str(r.get("content","")).lower() or term==str(r.get("type","")).lower(): matches.append((z,r))
                except Exception: continue
            kb=[[InlineKeyboardButton(f"{short(z['name'],14)} | {record_label(r)}",callback_data=f"rec:{z['id']}:{r['id']}")] for z,r in matches[:30]]; kb.append([InlineKeyboardButton("⬅️ منوی اصلی",callback_data="main")]); context.user_data.clear()
            return await localized_reply(msg, f"🔎 <b>نتایج جستجوی سراسری</b>\n\nتعداد نتیجه: {len(matches)}",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(kb))
        if state=="purge_url":
            zid=context.user_data["zone_id"]
            if not re.match(r"^https?://[^\s/]+(?:/|$)",text,re.I):
                raise ValueError("یک URL کامل و معتبر با https:// یا http:// وارد کنید.")
            a=active_account(); await cf_request(a,"POST",f"/zones/{zid}/purge_cache",json_body={"files":[text]})
            context.user_data.clear()
            return await localized_reply(msg, "✅ کش آدرس موردنظر پاک شد.",reply_markup=InlineKeyboardMarkup([
              [InlineKeyboardButton("↩️ مدیریت کش",callback_data=f"cache:{zid}")]]))
    except Exception as e:
        # Auth inputs are deleted before being validated and secrets are never logged.
        log.error("text flow failed: %s", type(e).__name__)
        await localized_send(update.effective_chat, f"❌ خطا: <code>{esc(e)}</code>\n\n"
                                                 "اگر در حال واردکردن API Token هستی، یک مقدار معتبر دیگر بفرست. "
                                                 "توکن نامعتبر ذخیره نشده است.",parse_mode=ParseMode.HTML)


async def create_pending_record(msg,context):
    zid=context.user_data["zone_id"]; rtype=context.user_data["record_type"]
    body={"type":rtype,"name":context.user_data["record_name"],"content":context.user_data["content"],"ttl":1}
    if rtype in PRIORITY_TYPES: body["priority"]=context.user_data.get("priority",0)
    if rtype in PROXY_TYPES: body["proxied"]=False
    a=active_account(); result=(await cf_request(a,"POST",f"/zones/{zid}/dns_records",json_body=body))["result"]
    context.user_data.clear()
    await localized_reply(msg, f"✅ <b>رکورد DNS ساخته شد.</b>\n\n"
        f"🧩 نوع: <b>{rtype}</b>\n"
        f"📛 نام: <code>{esc(result.get('name'))}</code>\n"
        f"🎯 مقدار: <code>{esc(result.get('content'))}</code>",
        parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ فهرست رکوردها",callback_data=f"dns:{zid}:0")]]))


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return await deny(update)
    if context.user_data.get("state")!="import_bind": return
    zid=context.user_data["zone_id"]; doc=update.effective_message.document
    if not doc: return
    if not is_private_chat(update):
        return await localized_reply(update.effective_message, "🔒 ارسال فایل رکوردها فقط در گفت‌وگوی خصوصی مجاز است.")
    if doc.file_size and doc.file_size>2*1024*1024:
        return await localized_reply(update.effective_message, "⚠️ حجم فایل BIND نباید بیشتر از ۲ مگابایت باشد.")
    f=await doc.get_file(); data=await f.download_as_bytearray(); a=active_account()
    if len(data)>2*1024*1024:
        return await localized_reply(update.effective_message, "⚠️ حجم فایل بیشتر از حد مجاز است.")
    try:
        token=decrypt_token(a["token_enc"])
        headers={"Authorization":f"Bearer {token}"}
        files={"file":(doc.file_name or "records.bind",bytes(data),"text/plain")}
        async with httpx.AsyncClient(timeout=60) as client: r=await client.post(CF_API+f"/zones/{zid}/dns_records/import",headers=headers,files=files)
        j=r.json()
        if r.is_error or j.get("success") is False: raise RuntimeError("; ".join(e.get("message",str(e)) for e in j.get("errors",[])))
        context.user_data.clear(); res=j.get("result",{})
        await localized_reply(update.effective_message, f"✅ Import انجام شد.\nRecords: {res.get('recs_added',res)}\n/start")
    except Exception as e:
        await localized_reply(update.effective_message, f"❌ Import failed: <code>{esc(e)}</code>",parse_mode=ParseMode.HTML)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Unhandled exception", exc_info=context.error)


# Keep the command menu in Telegram aligned with CommandHandler registrations.
# Old /id entries configured in BotFather are replaced on the next successful startup.
BOT_COMMAND_MENU = (
    ("start", "🏠 منوی اصلی"),
    ("menu", "📋 نمایش منوی اصلی"),
    ("accounts", "👤 مدیریت حساب‌های Cloudflare"),
    ("help", "📖 راهنمای استفاده از ربات"),
    ("cancel", "❌ لغو عملیات جاری"),
    ("language", "🌐 انتخاب زبان"),
)


def command_list(language="fa"):
    return [BotCommand(command=name, description=translate_text(description, language))
            for name, description in BOT_COMMAND_MENU]


async def publish_user_commands(bot, chat_id: int, language: str):
    try:
        await bot.set_my_commands(command_list(language), scope=BotCommandScopeChat(chat_id))
    except Exception as exc:
        log.warning("Unable to publish chat language commands: %s", type(exc).__name__)


async def publish_bot_commands(application: Application):
    """Publish slash commands in the default and private-chat Telegram menus."""
    scopes = [
        (None, None, "fa"),
        (BotCommandScopeAllPrivateChats(), None, "fa"),
        (BotCommandScopeAllPrivateChats(), "en", "en"),
        (BotCommandScopeAllPrivateChats(), "ru", "ru"),
    ]
    for scope, locale_code, language in scopes:
        try:
            kwargs = {"scope": scope}
            if locale_code:
                kwargs["language_code"] = locale_code
            await application.bot.set_my_commands(command_list(language), **kwargs)
        except Exception as exc:
            # Telegram API being unavailable must not stop the bot.
            log.warning("Unable to refresh Telegram commands for %s/%s: %s",
                        type(scope).__name__, locale_code, type(exc).__name__)


def main():
    if not ADMIN_IDS: raise SystemExit("TELEGRAM_ADMIN_IDS is empty")
    init_db()
    app=Application.builder().token(BOT_TOKEN).post_init(publish_bot_commands).build()
    app.add_handler(CommandHandler("start",localized_handler(start)))
    app.add_handler(CommandHandler("menu",localized_handler(start)))
    app.add_handler(CommandHandler("accounts",localized_handler(accounts_command)))
    app.add_handler(CommandHandler("help",localized_handler(help_command)))
    app.add_handler(CommandHandler("cancel",localized_handler(cancel_command)))
    app.add_handler(CommandHandler("language", localized_handler(language_command)))
    app.add_handler(CallbackQueryHandler(localized_handler(callback)))
    app.add_handler(MessageHandler(filters.Document.ALL,localized_handler(handle_document)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,localized_handler(handle_text)))
    app.add_error_handler(error_handler)
    app.run_polling(drop_pending_updates=True)


if __name__=="__main__":
    main()
