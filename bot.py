# ربات فروش خدمات «گاد استار» (God Star) — نسخه‌ی ۱
#
# خدمات: تبلیغات تلگرام، ممبر آپلودری، ربات تلگرام (مدیریت + اختصاصی).
# پرداخت فقط تتر (USDT) روی شبکه‌ی BEP20.
#
# راه‌اندازی:
#   1) ربات در BotFather ساخته شده: @GodStarShopBot
#   2) توکن را به‌عنوان Secret با نام GODSTAR_BOT_TOKEN بگذار
#      (هیچ‌وقت توی چت یا فایل نفرست)؛
#   3) ADMIN_CHAT_ID را هم به‌عنوان Secret بگذار (ادمین: 278899906)؛
#   4) برای ذخیره‌ی دائمی، SUPABASE_URL و SUPABASE_SERVICE_KEY و
#      SUPABASE_PUBLISHABLE_KEY را بگذار و supabase.sql را در SQL Editor
#      سوپابیس اجرا کن؛ بدون آن‌ها حافظه در godstar_state.json می‌ماند
#      (gitignore). کلید عمومی فقط برای apikey و کلید محرمانه فقط برای
#      Authorization است (سیستم کلید تازه‌ی سوپابیس).
#
# الگو از ربات دنبال‌کننده‌ی GodNews24 گرفته شده: فراخوانی خام Bot API
# با requests، long-poll، مدیریت 429، و خطای شبکه که حلقه را نمی‌شکند.

import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone

import requests

from db import Store, _ts_value
import pricing
from pricing import (WALLET_ADDRESS, ads_price, member_price, bot_price,
                     compute_order_price, loyalty_eligible,
                     is_valid_channel_id, is_valid_referral_code)

BOT_VERSION = "2026-11-26-1.0.4"   # فقط توی لاگ و برای ادمین در /start نوشته می‌شه
BOT_USERNAME = "GodStarShopBot"  # ربات اصلی؛ هرگز GodStarServiceBot (بازنشسته) نیست
WEBAPP_URL = os.environ.get("WEBAPP_URL") or "https://821abolfazl.github.io/godstar-bot/"

TOKEN = os.environ.get("GODSTAR_BOT_TOKEN") or ""
# ادمین فقط از متغیر محیطی خوانده می‌شود؛ اگر تنظیم نشده باشد،
# هیچ اقدام ادمینی انجام نمی‌شود (مقدار پیش‌فرض در کد نیست).
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID") or ""
LOOP_MINUTES = int(os.environ.get("LOOP_MINUTES") or 0)

POLL_SECONDS = 2        # مکث کوتاه بین دو دور؛ انتظار واقعی را long polling انجام می‌دهد
POLL_TIMEOUT = 10       # مهلت long-poll در هر درخواست getUpdates (ثانیه)

SUPPORT_LINE = "درصورت نیاز به راهنمایی و مشاوره با پشتیبانی در ارتباط باشید"
ORDERS_NOTICE = "هر تغییری در وضعیت سفارشت، با پیام بهت خبر داده می‌شود."
LOYALTY_LINE = "بعد از ۳ سفارش، ۱۰٪ تخفیف می‌گیری"
REFERRAL_LINE = "وقتی دوستت با کدت سفارش بدهد، هر دو ۱۰٪ تخفیف می‌گیرید."
REFERRAL_ONCE_LINE = "هر کد دعوت برای هر نفر فقط یک بار قابل استفاده است."
PAYMENT_NOTE = "پرداخت فقط با تتر (BEP20)"

# ---------- تلگرام (الگوی ربات دنبال‌کننده) ----------
_API_ERROR = {}


def api(method, **data):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    try:
        r = requests.post(url, data=data, timeout=30)
        if r.status_code == 429:   # محدودیت سرعت تلگرام: صبر و یک بار تلاش دوباره
            try:
                wait = int(r.json().get("parameters", {}).get("retry_after", 5))
            except Exception:
                wait = 5
            time.sleep(min(wait, 30))
            r = requests.post(url, data=data, timeout=30)
    except requests.RequestException as ex:   # قطعی/کندی شبکه نباید حلقه را خراب کند
        print("telegram network error:", method, type(ex).__name__)
        _API_ERROR["last"] = {"status": "network"}
        return None
    if not r.ok:
        print("telegram error:", method, r.status_code, r.text[:150])
        _API_ERROR["last"] = {"status": r.status_code}
        return None
    return r.json().get("result")


def send_photo_bytes(chat_id, photo_bytes, caption=None, reply_markup=None):
    """عکس را با بایت‌های واقعی (multipart) به تلگرام می‌فرستد؛ برای
    عکس پشتیبانی که از مینی‌اپ کوچک‌شده می‌رسد. خروجی مثل api است:
    نتیجه‌ی sendPhoto یا None. محدودیت ۴۲۹ مثل api یک بار تلاش دوباره."""
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    if reply_markup:
        data["reply_markup"] = reply_markup
    files = {"photo": ("photo.jpg", photo_bytes, "image/jpeg")}
    try:
        r = requests.post(url, data=data, files=files, timeout=60)
        if r.status_code == 429:   # محدودیت سرعت تلگرام: صبر و یک بار تلاش دوباره
            try:
                wait = int(r.json().get("parameters", {}).get("retry_after", 5))
            except Exception:
                wait = 5
            time.sleep(min(wait, 30))
            r = requests.post(url, data=data, files=files, timeout=60)
    except requests.RequestException as ex:   # قطعی/کندی شبکه نباید حلقه را خراب کند
        print("telegram network error:", "sendPhoto", type(ex).__name__)
        _API_ERROR["last"] = {"status": "network"}
        return None
    if not r.ok:
        print("telegram error:", "sendPhoto", r.status_code, r.text[:150])
        _API_ERROR["last"] = {"status": r.status_code}
        return None
    return r.json().get("result")


def api_get_updates(offset):
    try:
        r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                         params={"offset": offset, "timeout": POLL_TIMEOUT,
                                 "allowed_updates": json.dumps(
                                     ["message", "callback_query"])},
                         timeout=POLL_TIMEOUT + 10)
        data = r.json()
    except Exception as ex:
        print("getUpdates error:", type(ex).__name__)
        return []
    if not data.get("ok"):
        print("getUpdates failed:", str(data)[:120])
        if data.get("error_code") == 429:
            # تلگرام می‌گوید صبر کن؛ اگر بی‌اعتنا هر ۲ ثانیه دوباره بزنیم،
            # پنجره‌ی جریمه تازه می‌شود و 429 ادامه پیدا می‌کند.
            try:
                wait = int(data.get("parameters", {}).get("retry_after", 5))
            except Exception:
                wait = 5
            time.sleep(min(max(wait, 1), 60))
        return []
    return data.get("result", [])


def send_message(chat_id, text, reply_markup=None):
    data = {"chat_id": chat_id, "text": text}
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    return api("sendMessage", **data)


def forward_message_to(chat_id, from_chat_id, message_id):
    return api("forwardMessage", chat_id=chat_id, from_chat_id=from_chat_id,
               message_id=message_id)


def answer_callback(callback_query_id, text=""):
    return api("answerCallbackQuery", callback_query_id=callback_query_id,
               text=text)


def is_admin(chat_id):
    return bool(ADMIN_CHAT_ID) and str(chat_id) == str(ADMIN_CHAT_ID)


def validate_init_data(init_data, token, max_age_sec=86400, now=None):
    """initData تلگرام را دقیقاً مثل مستندات تلگرام اعتبارسنجی می‌کند.

    رشته‌ی بررسی از همه‌ی پارامترها به‌جز hash ساخته می‌شود (مرتب‌شده
    بر اساس کلید، «key=value» با «\n»): کلید مخفی = HMAC-SHA256 توکن
    با کلید ثابت «WebAppData» (مستندات تلگرام — نه SHA256 ساده‌ی توکن)
    و HMAC-SHA256 آن باید با hash برابر باشد؛ auth_date هم نباید بیش از
    ۲۴ ساعت قدیمی باشد. خروجی: dict کاربر (user.id) یا None.
    Edge Function همین الگوریتم را در TypeScript پیاده کرده است.
    """
    if not init_data or not token:
        return None
    try:
        pairs = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    try:
        auth_date = int(pairs.get("auth_date") or 0)
    except (TypeError, ValueError):
        return None
    now = time.time() if now is None else now
    if auth_date <= 0 or now - auth_date > max_age_sec:
        return None
    check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed, received_hash.lower()):
        return None
    try:
        user = json.loads(pairs.get("user") or "{}")
    except Exception:
        return None
    if not isinstance(user, dict) or not user.get("id"):
        return None
    return user


# ---------- دکمه‌ی منوی پایین تلگرام (باز کردن مینی‌اپ) ----------
def shop_button_markup():
    """دکمه‌ی شیشه‌ای «باز کردن فروشگاه» برای پیام‌های ربات."""
    if not WEBAPP_URL:
        return None
    return {"inline_keyboard": [[
        {"text": "🛍 باز کردن فروشگاه", "web_app": {"url": WEBAPP_URL}}]]}


def setup_menu_button():
    """دکمه‌ی «منو» پایینِ چپ تلگرام را روی باز کردن مینی‌اپ می‌گذارد.

    این تنظیم سمت تلگرام ذخیره می‌شود؛ یک بار اجرا کافی است و با هر
    استارت دوباره تکرار می‌شود تا اگر در BotFather عوض شده باشد برگردد.
    خطا در آن هرگز نباید استارت ربات را خراب کند.
    """
    if not WEBAPP_URL:
        print("WEBAPP_URL تنظیم نشده — دکمه‌ی منو تنظیم نمی‌شود")
        return False
    res = api("setChatMenuButton", menu_button=json.dumps({
        "type": "web_app", "text": "فروشگاه",
        "web_app": {"url": WEBAPP_URL}}, ensure_ascii=False))
    if res is None:
        print("تنظیم دکمه‌ی منو ناموفق بود (در لاگ بالا ببین)")
        return False
    print("دکمه‌ی منوی مینی‌اپ تنظیم شد")
    return True


def setup_commands():
    """دستورها را در منوی تلگرام می‌گذارد — فقط برای ادمین.

    با scope چت ادمین، فقط او دستورهای ادمین را در منو می‌بیند
    (با تایپ «/»)؛ برای بقیه هیچ دستوری اضافه نمی‌شود — همان چیزی
    که الان می‌بینند. خطا در آن هرگز نباید استارت ربات را خراب کند.
    """
    if not ADMIN_CHAT_ID:
        return False
    commands = [
        {"command": "start", "description": "شروع و فروشگاه"},
        {"command": "help", "description": "راهنما"},
        {"command": "orders", "description": "سفارش‌های در انتظار"},
        {"command": "approve", "description": "تایید سفارش"},
        {"command": "reject", "description": "رد سفارش"},
        {"command": "codes", "description": "لیست کدهای تخفیف"},
        {"command": "addcode", "description": "ساخت کد تخفیف"},
        {"command": "delcode", "description": "حذف کد تخفیف"},
        {"command": "broadcast", "description": "پیام همگانی"},
        {"command": "stats", "description": "آمار فروش"},
        {"command": "autoreplies", "description": "پاسخ‌های خودکار"},
        {"command": "addreply", "description": "افزودن پاسخ خودکار"},
        {"command": "delreply", "description": "حذف پاسخ خودکار"},
    ]
    res = api("setMyCommands",
              commands=json.dumps(commands, ensure_ascii=False),
              scope=json.dumps({"type": "chat",
                                "chat_id": int(ADMIN_CHAT_ID)}))
    if res is None:
        print("تنظیم دستورهای ادمین در منو ناموفق بود (در لاگ بالا ببین)")
        return False
    print("دستورهای ادمین در منو تنظیم شد")
    return True


# ---------- کیبوردها ----------
def main_keyboard(is_admin=False):
    keyboard = [
        [{"text": "🛍 خدمات", "web_app": {"url": WEBAPP_URL}},
         {"text": "📦 سفارش‌های من"}],
        [{"text": "👛 کیف پول من"}, {"text": "💬 پشتیبانی"}],
        [{"text": "🎁 کد معرفی من"}],
        [{"text": "🛍 باز کردن فروشگاه", "web_app": {"url": WEBAPP_URL}}],
    ]
    if is_admin:
        keyboard.append([{"text": "📋 دستورهای ادمین"}])
    return {"keyboard": keyboard, "resize_keyboard": True}


def admin_keyboard():
    """منوی دستورهای ادمین — با زدن دکمه‌ی «📋 دستورهای ادمین» در منوی
    اصلی باز می‌شود؛ روی هر دکمه کلمه و کاری که دستور انجام می‌دهد
    نوشته شده (نه خود دستور)، و زدن آن همان دستور را می‌فرستد."""
    return {"keyboard": [
        [{"text": "📦 سفارش‌های در انتظار"}, {"text": "✅ تایید سفارش"}],
        [{"text": "❌ رد سفارش"}, {"text": "🎟 کدهای تخفیف"}],
        [{"text": "➕ ساخت کد تخفیف"}, {"text": "🗑 حذف کد تخفیف"}],
        [{"text": "📣 پیام همگانی"}, {"text": "📊 آمار"}],
        [{"text": "💬 پاسخ‌های خودکار"}, {"text": "➕ ساخت پاسخ خودکار"}],
        [{"text": "🗑 حذف پاسخ خودکار"}],
        [{"text": "🔙 بازگشت به منو"}],
    ], "resize_keyboard": True}


# برچسب دکمه‌های منوی دستورهای ادمین → دستوری که می‌فرستند
ADMIN_KEYBOARD_COMMANDS = {
    "📦 سفارش‌های در انتظار": "/orders",
    "✅ تایید سفارش": "/approve",
    "❌ رد سفارش": "/reject",
    "🎟 کدهای تخفیف": "/codes",
    "➕ ساخت کد تخفیف": "/addcode",
    "🗑 حذف کد تخفیف": "/delcode",
    "📣 پیام همگانی": "/broadcast",
    "📊 آمار": "/stats",
    "💬 پاسخ‌های خودکار": "/autoreplies",
    "➕ ساخت پاسخ خودکار": "/addreply",
    "🗑 حذف پاسخ خودکار": "/delreply",
}


def support_keyboard():
    return {"keyboard": [
        [{"text": "💬 چت با پشتیبانی"}],
        [{"text": "👤 ارتباط با مدیریت"}],
        [{"text": "🔙 بازگشت به منو"}],
    ], "resize_keyboard": True}


def order_admin_keyboard(order_no):
    return {"inline_keyboard": [[
        {"text": "✅ تایید", "callback_data": f"approve:{order_no}"},
        {"text": "❌ رد", "callback_data": f"reject:{order_no}"},
    ]]}


def support_reply_keyboard(customer_id):
    """دکمه‌ی «✏️ پاسخ» زیر پیام پشتیبانی در چت ادمین — با زدن آن
    ادمین جوابش را تایپ می‌کند و به همان مشتری فرستاده می‌شود."""
    return {"inline_keyboard": [[
        {"text": "✏️ پاسخ", "callback_data": f"support_reply:{customer_id}"},
    ]]}


# ---------- متن‌ها ----------
def welcome_text(is_admin_user):
    text = ("سلام! 👋 به «گاد استار» خوش اومدی ⭐\n\n"
            "اینجا می‌تونی تبلیغات تلگرام بگیری، ممبر بخری یا ربات تلگرام "
            "برای کانالت سفارش بدی.\n\n"
            f"{PAYMENT_NOTE} به آدرس کیف پول ما.\n\n"
            f"{SUPPORT_LINE}")
    if is_admin_user:
        text += (f"\n\n👑 شما ادمین هستید\n"
                 f"نسخه‌ی ربات: {BOT_VERSION}\n"
                 "دستورهای ادمین: /orders ،/approve ،/reject ،/stats ،/broadcast")
    return text


SERVICES_TEXT = (
    "🛍 خدمات «گاد استار»\n\n"
    "۱️⃣ تبلیغات تلگرام 📢\n"
    "بازدید ۱۰٬۰۰۰ تا ۱٬۰۰۰٬۰۰۰؛ هر ۱۰ هزار بازدید: "
    "بدون رسانه ۵، عکس ۶، ویدیو ۷ تتر.\n"
    "چرا تبلیغات ما؟ هدف‌گیری دقیق، بازدید از کاربران واقعی "
    "(پلتفرم رسمی تلگرام)، گزارش نتیجه، بدون دردسر.\n\n"
    "۲️⃣ ممبر آپلودری 👥\n"
    "۱٬۰۰۰ تا ۲۰٬۰۰۰ ممبر؛ هر ۱٬۰۰۰ ممبر ۳ تتر.\n"
    "برای کانال‌های غیراخلاقی سفارش انجام نمی‌شود.\n"
    "ممبرها کاملاً از منابع اخلاقی تأمین می‌شوند.\n"
    "چرا ممبر ما؟ تحویل سریع، قیمت شفاف، هر تعدادی که بخواهی "
    "(۱ تا ۲۰ هزار)، پشتیبانی تا پایان سفارش.\n\n"
    "۳️⃣ ربات تلگرام 🤖\n"
    "ربات مدیریت کانال: ۱ ماهه ۱۰، ۳ ماهه ۲۸، ۶ ماهه ۵۴، ۱۲ ماهه ۱۰۰ تتر.\n"
    "ربات اختصاصی تو: قیمت توافقی بعد از بررسی.\n"
    "چرا ربات ما؟ مخصوص کانال تو ساخته می‌شود، مدیریت کامل کانال، "
    "پشتیبانی ۲۴ ساعته، تحویل سریع.\n\n"
    "برای ثبت سفارش، فروشگاه را باز کن 👇\n"
    f"{PAYMENT_NOTE}."
)


def order_summary_text(order, user=None):
    lines = [f"🧾 سفارش {order['order_no']}",
             f"خدمت: {service_label(order.get('service'))}",
             f"مبلغ: {order['amount']} USDT"]
    if order.get("discount_kind"):
        kind_label = {"loyalty": "وفاداری", "referral": "معرفی",
                      "code": "کد تخفیف"}.get(order["discount_kind"], "")
        lines.append(f"تخفیف ({kind_label}): {order.get('discount_percent')}٪ "
                     f"(-{order.get('discount_amount')} USDT)")
    if order.get("paid_by_wallet"):
        lines.append("پرداخت: از کیف پول")
    if order.get("tx_hash"):
        lines.append(f"هش تراکنش: {order['tx_hash']}")
    if order.get("receipt_file_id"):
        lines.append("رسید: عکس ارسال شده است 📎")
    details = order.get("details") or {}
    if details.get("channel_id"):
        lines.append(f"کانال: {details['channel_id']}")
    if details.get("views"):
        lines.append(f"بازدید: {details['views']:,}")
    if details.get("count"):
        lines.append(f"تعداد: {details['count']:,}")
    if details.get("plan"):
        lines.append(f"پلن: {details['plan']}")
    if details.get("ad_text"):
        lines.append(f"متن تبلیغ: {details['ad_text'][:200]}")
    if details.get("receipt_kind") == "photo":
        fname = details.get("receipt_file_name") or ""
        lines.append("رسید: عکس فیش — مشتری در چت می‌فرستد 📷"
                     + (f" ({fname})" if fname else ""))
    if details.get("description"):
        lines.append(f"توضیحات: {details['description'][:200]}")
    if user:
        name = user.get("first_name") or user.get("username") or ""
        lines.append(f"مشتری: {name} ({order['user_id']})")
    lines.append(f"وضعیت: {status_label(order.get('status'))}")
    return "\n".join(lines)


def service_label(service):
    return {"ads": "تبلیغات تلگرام", "members": "ممبر آپلودری",
            "bot": "ربات تلگرام", "wallet_charge": "شارژ کیف پول"}.get(
                service or "", service or "")


def status_label(status):
    return {"pending": "در انتظار تایید", "confirmed": "تایید شده ✅",
            "rejected": "رد شده ❌"}.get(status or "", status or "")


def ensure_defaults(store):
    """پاسخ‌های خودکار پیش‌فرض اگر هنوز ثبت نشده باشند.

    کد تخفیف اینجا ساخته نمی‌شود: کدی که ادمین با /delcode حذف کرده
    نباید با شروع ران تازه برگردد (قبلاً GOD10 همین‌طور برمی‌گشت).
    کد را ادمین با /addcode می‌سازد.
    """
    if not store.list_auto_replies():
        store.add_auto_reply(
            "سفارشم کی آماده می‌شه؟",
            "سفارش‌های ممبر و تبلیغات معمولاً تا ۲۴ ساعت بعد از تایید پرداخت "
            "انجام می‌شوند و نتیجه‌اش را با پیام بهت خبر می‌دهیم.")
        store.add_auto_reply(
            "چطور پرداخت کنم؟",
            "فقط تتر (USDT) روی شبکه‌ی BEP20 به آدرس کیف پول ما بفرست و "
            "رسید یا کد هش تراکنش را ارسال کن.")
    store.save_if_dirty()


def _as_int(value):
    """عدد صحیح از پی‌لود JSON؛ float صحیح هم قبول می‌شود."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _webapp_details(service, payload):
    """ساخت details از پی‌لود تخت وب‌اپ (کلیدهای تخت)؛ اگر details آماده
    آمده بود همان را برمی‌گرداند (سازگاری با فراخوانی داخلی/تست‌ها).

    وب‌اپ این‌طوری می‌فرستد (تخت، بدون details):
      ads: views, media, ad_text, channel_id, target_channels
      members: quantity, channel_id
      bot: bot_type, plan_months (عدد 1/3/6/12), notes, channel_id
      رسید: receipt_kind ("photo"|"hash"), receipt_hash, receipt_file_name
    """
    details = dict(payload.get("details") or {})
    if details:
        return details
    if service == "ads":
        details = {"views": _as_int(payload.get("views")),
                   "media": payload.get("media") or "none",
                   "channel_id": payload.get("channel_id"),
                   "ad_text": payload.get("ad_text"),
                   "target_channels": payload.get("target_channels"),
                   "media_file_name": payload.get("media_file_name")}
    elif service == "members":
        details = {"count": _as_int(payload.get("quantity")),
                   "channel_id": payload.get("channel_id")}
    elif service == "bot":
        months = _as_int(payload.get("plan_months"))
        plan = {1: "1m", 3: "3m", 6: "6m", 12: "12m"}.get(months)
        details = {"bot_type": payload.get("bot_type") or "management",
                   "plan": plan,
                   "channel_id": payload.get("channel_id"),
                   "description": payload.get("notes")}
    if payload.get("receipt_kind") == "photo":
        details["receipt_kind"] = "photo"
        if payload.get("receipt_file_name"):
            details["receipt_file_name"] = payload.get("receipt_file_name")
    return {k: v for k, v in details.items() if v is not None}


# ---------- ثبت سفارش (منطق مشترک وب‌اپ و ربات) ----------
def _require_receipt(payload):
    if not (payload.get("tx_hash") or "").strip() and not payload.get("receipt_file_id"):
        raise ValueError("برای ثبت سفارش، رسید پرداخت یا کد هش تراکنش را ارسال کن.")


def _discount_context(store, user, discount_code):
    """تعیین تخفیف قابل اعمال برای این سفارش (قانون تک‌تخفیف).
    خروجی: (loyalty, referral, code_percent, code_row)"""
    loyalty = loyalty_eligible(user.get("completed_orders") or 0)
    referral = bool(user.get("referred_by")) and not user.get("referral_discount_used")
    code_row = None
    code_percent = None
    if discount_code:
        code_row = store.get_code(discount_code)
        if code_row is None or not code_row.get("active"):
            raise ValueError("کد تخفیف معتبر نیست.")
        if code_row.get("max_uses") and int(code_row.get("uses") or 0) >= int(code_row["max_uses"]):
            raise ValueError("این کد تخفیف دیگر قابل استفاده نیست.")
        if store.has_used_code(user.get("chat_id"), discount_code):
            raise ValueError("شما قبلاً از این کد تخفیف استفاده کرده‌اید.")
        code_percent = int(code_row["percent"])
    return loyalty, referral, code_percent, code_row


def place_order(store, user_id, service, details, discount_code=None,
                paid_by_wallet=False, tx_hash="", receipt_file_id="",
                receipt_kind="", defer_receipt=False):
    """سفارش را با قیمت‌گذاری دوباره (هرگز به قیمت مشتری اعتماد نمی‌شود)
    ثبت می‌کند و برمی‌گرداند. خطا = ValueError با پیام فارسی."""
    user = store.ensure_user(user_id)
    details = dict(details or {})

    if service == "ads":
        views = details.get("views")
        if not isinstance(views, int) or isinstance(views, bool):
            raise ValueError("تعداد بازدید نامعتبر است.")
        base = ads_price(views, details.get("media") or "none")
        channel_id = (details.get("channel_id") or "").strip()
        if not is_valid_channel_id(channel_id):
            raise ValueError("برای ثبت سفارش، آیدی کانال را درست وارد کن (مثلاً @GodNews24).")
        if not (details.get("ad_text") or "").strip():
            raise ValueError("برای ثبت سفارش، متن تبلیغ را وارد کن.")
        order_type = "order"
    elif service == "members":
        count = details.get("count")
        if not isinstance(count, int) or isinstance(count, bool):
            raise ValueError("تعداد ممبر نامعتبر است.")
        base = member_price(count)
        channel_id = (details.get("channel_id") or "").strip()
        if not is_valid_channel_id(channel_id):
            raise ValueError("برای ثبت سفارش، آیدی کانال را درست وارد کن (مثلاً @GodNews24).")
        order_type = "order"
    elif service == "bot":
        bot_type = details.get("bot_type") or "management"
        if bot_type == "custom":
            # ربات اختصاصی: قیمت توافقی بعد از بررسی؛ مبلغ صفر ثبت می‌شود
            base = pricing.Decimal("0")
        else:
            plan = details.get("plan")
            if not plan:
                raise ValueError("برای ثبت سفارش، پلن ربات را انتخاب کن.")
            base = bot_price(plan)
        channel_id = (details.get("channel_id") or "").strip()
        if not is_valid_channel_id(channel_id):
            raise ValueError("برای ثبت سفارش، آیدی کانال را درست وارد کن (مثلاً @GodNews24).")
        order_type = "order"
    elif service == "wallet_charge":
        amount = details.get("amount")
        try:
            base = pricing.Decimal(str(amount))
        except Exception:
            raise ValueError("مبلغ شارژ نامعتبر است.")
        if base <= 0:
            raise ValueError("مبلغ شارژ باید بیشتر از صفر باشد.")
        base = pricing._money(base)
        order_type = "wallet_charge"
    else:
        raise ValueError("خدمت نامعتبر است.")

    # تخفیف فقط برای سفارش‌های خدمت است، نه شارژ کیف پول
    if order_type == "wallet_charge":
        final, discount_amount, kind, percent = base, pricing.Decimal("0"), None, 0
        code_row = None
    else:
        loyalty, referral, code_percent, code_row = _discount_context(
            store, user, discount_code)
        final, discount_amount, kind, percent = compute_order_price(
            base, loyalty=loyalty, referral=referral, code_percent=code_percent)

    # پرداخت: یا از کیف پول، یا با رسید/هش. رسید عکس در وب‌اپ فقط به‌صورت
    # متادیتا می‌رسد (نام فایل) و خود عکس بعداً در چت فرستاده می‌شود؛
    # پس receipt_kind == "photo" هم رسید معتبر است.
    if float(final) > 0:
        if paid_by_wallet:
            if store.wallet_balance(user_id) + 1e-9 < float(final):
                raise ValueError("موجودی کیف پول کافی نیست؛ اول کیف پول را شارژ کن.")
        elif receipt_kind == "photo":
            pass  # عکس فیش بعداً در چت می‌رسد؛ در details ثبت شده است
        elif defer_receipt:
            pass  # سفارش دوباره: رسید بعداً در چت می‌رسد (attach_receipt)
        else:
            _require_receipt({"tx_hash": tx_hash, "receipt_file_id": receipt_file_id})

    order = store.create_order(
        user_id, order_type, service, details, str(final),
        discount_kind=kind, discount_percent=percent,
        discount_amount=str(discount_amount),
        discount_code=(code_row["code"] if kind == "code" and code_row else None),
        paid_by_wallet=bool(paid_by_wallet),
        tx_hash=(tx_hash or "").strip(),
        receipt_file_id=receipt_file_id or "")
    if order_type == "wallet_charge":
        pass  # شارژ فقط بعد از تایید ادمین به کیف پول اضافه می‌شود
    else:
        if paid_by_wallet and float(final) > 0:
            store.debit_wallet(user_id, float(final), note=f"سفارش {order['order_no']}")
        if kind == "code" and code_row is not None:
            store.use_code(code_row["code"])
        if user.get("referred_by"):
            # اولین سفارش دوست: ۱۰٪ معرف فعال می‌شود. تخفیف ۱۰٪ دوست هم
            # به همین سفارش تعلق داشت (اگر تخفیف دیگری بزرگ‌تر نبود، همان
            # انتخاب شد)؛ پس با ثبت این سفارش، حق استفاده‌اش تمام می‌شود.
            user["referral_discount_used"] = True
            store.dirty = True
            referrer_id = store.activate_referral_for_friend(user_id)
            if referrer_id:
                send_message(referrer_id,
                             "🎉 دوستت با کد معرفیت سفارش داد!\n"
                             "۱۰٪ تخفیف معرف برای سفارش بعدیت فعال شد. ✅")
    return order


def notify_admin_new_order(store, order):
    """اعلان فوری سفارش جدید به ادمین + دکمه‌های تایید/رد + رسید."""
    if not ADMIN_CHAT_ID:
        return
    user = store.get_user(order["user_id"])
    text = "🆕 سفارش جدید\n\n" + order_summary_text(order, user)
    send_message(ADMIN_CHAT_ID, text, reply_markup=order_admin_keyboard(order["order_no"]))
    if order.get("receipt_file_id"):
        api("sendPhoto", chat_id=ADMIN_CHAT_ID,
            photo=order["receipt_file_id"],
            caption=f"🧾 رسید سفارش {order['order_no']}")


def confirm_order(store, order_no):
    """تایید سفارش توسط ادمین: وضعیت + پیام خودکار به مشتری + اثرات جانبی.
    خروجی سفارش تاییدشده."""
    order = store.set_order_status(order_no, "confirmed")
    user_id = order["user_id"]
    if order.get("type") == "wallet_charge":
        store.credit_wallet(user_id, float(order["amount"]),
                            note=f"شارژ کیف پول ({order_no})")
    else:
        user = store.ensure_user(user_id)
        user["completed_orders"] = int(user.get("completed_orders") or 0) + 1
        store.dirty = True
    send_message(user_id,
                 f"✅ سفارشت تایید شد!\n\n"
                 f"شماره سفارش: {order_no}\n"
                 f"مبلغ: {order['amount']} USDT\n"
                 "به‌زودی انجامش می‌دهیم و نتیجه را خبر می‌دهیم. 🙏")
    return order


# دلایل آماده‌ی رد سفارش — ادمین یکی را انتخاب می‌کند یا دلیل خودش را می‌نویسد
REJECT_REASONS = ["رسید معتبر نیست", "مبلغ با سفارش هم‌خوانی ندارد",
                  "اطلاعات سفارش ناقص است", "سرویس در حال حاضر در دسترس نیست"]


def reject_order(store, order_no, reason=None):
    """رد سفارش توسط ادمین: وضعیت + دلیل رد + پیام خودکار + برگشت پول کیف پول.

    دلیل (اگر داده شده باشد) در جزئیات سفارش ذخیره می‌شود تا هم در پیام
    رد به مشتری برسد و هم در «سفارش‌های من» مینی‌اپ دیده شود.
    """
    order = store.set_order_status(order_no, "rejected")
    user_id = order["user_id"]
    if reason:
        details = dict(order.get("details") or {})
        details["reject_reason"] = reason
        order["details"] = details
        store.dirty = True
    if order.get("paid_by_wallet") and float(order.get("amount") or 0) > 0:
        store.credit_wallet(user_id, float(order["amount"]),
                            note=f"برگشت وجه سفارش ردشده {order_no}")
    text = (f"❌ متأسفانه سفارشت رد شد.\n\n"
            f"شماره سفارش: {order_no}\n")
    if reason:
        text += f"دلیل: {reason}\n"
    text += ("اگر پول از کیف پولت کم شده بود، برگشت داده شد.\n"
             f"{SUPPORT_LINE}")
    send_message(user_id, text)
    return order


def ask_reject_reason(store, chat_id, order_no):
    """پیش از رد سفارش، دلیل را از ادمین می‌پرسد: ۴ دلیل آماده + تایپ
    دلیل + لغو. رد واقعی فقط بعد از انتخاب/تایپ دلیل انجام می‌شود."""
    order = store.get_order(order_no)
    if order is None:
        raise ValueError("سفارشی با این شماره پیدا نشد.")
    if order.get("status") != "pending":
        raise ValueError("این سفارش قبلاً بررسی شده است.")
    store.state.setdefault("pending_reject", {})[str(chat_id)] = {
        "order_no": order_no, "at": time.time()}
    store.dirty = True
    rows = [[{"text": reason,
              "callback_data": f"reject_reason:{order_no}:{i}"}]
            for i, reason in enumerate(REJECT_REASONS)]
    rows.append([{"text": "✏️ تایپ دلیل",
                  "callback_data": f"reject_reason_type:{order_no}"}])
    rows.append([{"text": "لغو", "callback_data": f"reject_cancel:{order_no}"}])
    send_message(chat_id, f"❌ رد سفارش {order_no}\n\nدلیل رد را انتخاب کن:",
                 reply_markup={"inline_keyboard": rows})


# ---------- سفارش دوباره + رسید + یادآوری‌ها (v0.4) ----------
PLAN_DAYS = {"1m": 30, "3m": 90, "6m": 180, "12m": 365}
PENDING_REMIND_AFTER_SEC = 2 * 3600      # سفارش بیش از ۲ ساعت در انتظار
RENEWAL_REMIND_BEFORE_SEC = 3 * 86400    # ۳ روز مانده به پایان اشتراک
RECEIPT_WINDOW_SEC = 24 * 3600           # رسید فقط برای سفارش ۲۴ ساعت اخیر
REMINDER_CHECK_EVERY_SEC = 15 * 60       # یادآوری‌ها هر ۱۵ دقیقه چک می‌شوند
_LAST_REMINDER_CHECK = 0

_TX_HASH_RE = re.compile(r"^(0x[0-9a-fA-F]{16,64}|[0-9a-fA-F]{64})$")


def _looks_like_tx_hash(text):
    return bool(text) and bool(_TX_HASH_RE.match(text.strip()))


def reorder_order(store, chat_id, order_no):
    """سفارش دوباره با یک ضربه: همان خدمت با همان مشخصات، قیمت تازه.

    اول از کیف پول پرداخت می‌شود (اگر موجودی کافی باشد)؛ وگرنه سفارش
    در انتظار می‌ماند و رسید بعداً در چت می‌رسد (attach_receipt).
    """
    order = store.get_order(order_no)
    if order is None:
        raise ValueError("سفارشی با این شماره پیدا نشد.")
    if int(order.get("user_id") or 0) != int(chat_id):
        raise ValueError("این سفارش مال تو نیست.")
    service = order.get("service")
    if service not in ("ads", "members", "bot") or order.get("type") != "order":
        raise ValueError("این سفارش را نمی‌شود دوباره ثبت کرد.")
    details = dict(order.get("details") or {})
    details.pop("receipt_file_name", None)
    details.pop("receipt_file_size", None)
    details.pop("discount_code", None)  # سفارش دوباره کد را دوباره اعمال نمی‌کند
    user_id = int(chat_id)
    paid_from_wallet = False
    try:
        new_order = place_order(store, user_id, service, details,
                                paid_by_wallet=True)
        paid_from_wallet = True
    except ValueError as ex:
        if "موجودی کیف پول کافی نیست" not in str(ex):
            raise
        new_order = place_order(store, user_id, service, details,
                                paid_by_wallet=False, defer_receipt=True)
    notify_admin_new_order(store, new_order)
    if paid_from_wallet:
        pay_note = "پرداخت: از کیف پول ✅"
    else:
        pay_note = ("پرداخت: رسید را همین‌جا بفرست (عکس فیش یا هش تراکنش) "
                    "تا سفارشت تایید شود. 📷")
    send_message(chat_id,
                 f"سفارش دوباره ثبت شد ✅\n\n"
                 f"شماره سفارش تازه: {new_order['order_no']}\n"
                 f"مبلغ: {new_order['amount']} USDT\n"
                 f"{pay_note}\n\n" + ORDERS_NOTICE)
    return new_order


def reorder_keyboard(orders):
    """دکمه‌های «دوباره» برای تازه‌ترین سفارش‌های خدمتی کاربر."""
    rows = []
    for o in orders:
        if o.get("type") != "order" or o.get("service") not in ("ads", "members", "bot"):
            continue
        rows.append([{"text": f"🔁 {o['order_no']} دوباره",
                      "callback_data": f"reorder:{o['order_no']}"}])
    if not rows:
        return None
    return {"inline_keyboard": rows[-5:]}


def send_my_orders(store, chat_id):
    """سفارش‌های واقعی مشتری + دکمه‌ی سفارش دوباره (چت و وب‌اپ)."""
    orders = store.list_orders(user_id=chat_id)
    if not orders:
        send_message(chat_id, "📦 هنوز سفارشی ثبت نکردی 🙂\n" + ORDERS_NOTICE)
        return
    lines = ["📦 سفارش‌های تو:\n", ORDERS_NOTICE + "\n"]
    for o in orders:
        lines.append(order_summary_text(o))
        lines.append("—")
    send_message(chat_id, "\n".join(lines),
                 reply_markup=reorder_keyboard(orders))


def _awaiting_receipt_order(store, chat_id):
    """تازه‌ترین سفارش در انتظارِ بدون رسید (۲۴ ساعت اخیر)."""
    now = time.time()
    for o in reversed(store.list_orders(user_id=chat_id, status="pending")):
        if o.get("paid_by_wallet") or o.get("tx_hash") or o.get("receipt_file_id"):
            continue
        created = _ts_value(o.get("created_at"))
        if created and now - created <= RECEIPT_WINDOW_SEC:
            return o
    return None


def attach_receipt(store, chat_id, msg):
    """عکس فیش یا هش تراکنش را به سفارش در انتظار می‌چسباند.

    همین مسیر رسیدِ عکسِ سفارش‌های وب‌اپ را هم کامل می‌کند: وب‌اپ فقط
    نام فایل را می‌فرستد و خود عکس اینجا در چت می‌رسد.
    """
    text = (msg.get("text") or "").strip()
    photo_file_id = ""
    if msg.get("photo"):
        photo_file_id = msg["photo"][-1]["file_id"]
    if not photo_file_id and not _looks_like_tx_hash(text):
        return False
    order = _awaiting_receipt_order(store, chat_id)
    if order is None:
        return False
    store.set_order_receipt(order["order_no"],
                            tx_hash=text if _looks_like_tx_hash(text) else "",
                            receipt_file_id=photo_file_id)
    send_message(chat_id,
                 f"✅ رسید سفارش {order['order_no']} دریافت شد؛ "
                 "در انتظار تایید است. 🙏")
    if ADMIN_CHAT_ID:
        if photo_file_id:
            api("sendPhoto", chat_id=ADMIN_CHAT_ID, photo=photo_file_id,
                caption=f"📷 رسید سفارش {order['order_no']} رسید.")
        else:
            send_message(ADMIN_CHAT_ID,
                         f"📎 هش تراکنش سفارش {order['order_no']} رسید:\n{text}")
    store.save_if_dirty()
    return True


def check_reminders(store):
    """یادآوری تمدید اشتراک (۳ روز مانده) و سفارش در انتظار (بیش از ۲ ساعت).

    هر ۱۵ دقیقه یک بار اجرا می‌شود و هر یادآوری برای هر سفارش فقط یک بار
    فرستاده می‌شود (order_reminders). خطا در آن هرگز حلقه را نمی‌شکند.
    """
    global _LAST_REMINDER_CHECK
    now = time.time()
    if now - _LAST_REMINDER_CHECK < REMINDER_CHECK_EVERY_SEC:
        return
    _LAST_REMINDER_CHECK = now
    try:
        # ۱) سفارش در انتظار بیش از ۲ ساعت → یادآوری به ادمین
        if ADMIN_CHAT_ID:
            for o in store.list_orders(status="pending"):
                created = _ts_value(o.get("created_at"))
                if not created or now - created < PENDING_REMIND_AFTER_SEC:
                    continue
                if store.has_reminder(o["order_no"], "pending_admin"):
                    continue
                user = store.get_user(o["user_id"])
                send_message(ADMIN_CHAT_ID,
                             f"⏰ سفارش {o['order_no']} بیش از ۲ ساعت است "
                             "در انتظار تایید است.\n\n" +
                             order_summary_text(o, user),
                             reply_markup=order_admin_keyboard(o["order_no"]))
                store.add_reminder(o["order_no"], "pending_admin")
        # ۲) تمدید اشتراک ربات مدیریت، ۳ روز مانده به پایان
        for o in store.list_orders(status="confirmed"):
            if o.get("service") != "bot" or o.get("type") != "order":
                continue
            details = o.get("details") or {}
            if (details.get("bot_type") or "management") == "custom":
                continue
            days = PLAN_DAYS.get(details.get("plan") or "")
            if not days:
                continue
            start = _ts_value(o.get("confirmed_at")) or _ts_value(o.get("created_at"))
            if not start:
                continue
            expiry = start + days * 86400
            if expiry - RENEWAL_REMIND_BEFORE_SEC <= now < expiry:
                if store.has_reminder(o["order_no"], "renewal"):
                    continue
                send_message(o["user_id"],
                             "⏰ اشتراک ربات تلگرام (مدیریت کانال) تو ۳ روز "
                             "دیگر تمام می‌شود.\n\n"
                             "برای اینکه کانالت بی‌ربات نماند، از فروشگاه "
                             "تمدیدش کن. 👇",
                             reply_markup=shop_button_markup())
                store.add_reminder(o["order_no"], "renewal")
    except Exception as ex:
        print("خطا در یادآوری‌ها:", repr(ex))


# ---------- وب‌اپ (web_app_data + صندوق سوپابیس) ----------
def process_webapp_payload(store, chat_id, payload):
    """پی‌لود وب‌اپ را پردازش می‌کند؛ هم برای web_app_data تلگرام و هم
    برای ردیف‌های صندوق وب‌اپ (Edge Function) یک مسیر است.
    قیمت هرگز از مشتری گرفته نمی‌شود؛ سرور دوباره حساب می‌کند."""
    if not isinstance(payload, dict):
        send_message(chat_id, "داده‌ی دریافتی از فروشگاه معتبر نیست؛ دوباره تلاش کن.")
        return
    ptype = payload.get("type")
    try:
        if ptype == "order":
            service = payload.get("service")
            details = _webapp_details(service, payload)
            receipt_kind = payload.get("receipt_kind") or ""
            order = place_order(
                store, chat_id, service, details,
                discount_code=payload.get("discount_code"),
                paid_by_wallet=bool(payload.get("paid_by_wallet")),
                tx_hash=payload.get("tx_hash") or payload.get("receipt_hash") or "",
                receipt_file_id=payload.get("receipt_file_id") or "",
                receipt_kind=receipt_kind)
            confirm_text = ("سفارش شما ثبت شد ✅\n\n"
                            f"شماره سفارش: {order['order_no']}\n"
                            f"مبلغ: {order['amount']} USDT\n"
                            "در انتظار تایید\n\n" + ORDERS_NOTICE)
            if receipt_kind == "photo" and not order.get("paid_by_wallet"):
                confirm_text += ("\n\n📷 لطفاً عکس فیش را همین‌جا در گفتگو "
                                 "با ربات بفرست تا سفارشت تایید شود.")
            send_message(chat_id, confirm_text)
            notify_admin_new_order(store, order)
        elif ptype == "wallet_charge":
            receipt_kind = payload.get("receipt_kind") or ""
            order = place_order(
                store, chat_id, "wallet_charge",
                {"amount": payload.get("amount_usdt") if payload.get("amount_usdt") is not None else payload.get("amount")},
                paid_by_wallet=False,
                tx_hash=payload.get("tx_hash") or payload.get("receipt_hash") or "",
                receipt_file_id=payload.get("receipt_file_id") or "",
                receipt_kind=receipt_kind)
            confirm_text = ("سفارش شما ثبت شد ✅\n\n"
                            f"شماره سفارش: {order['order_no']}\n"
                            f"مبلغ شارژ: {order['amount']} USDT\n"
                            "در انتظار تایید\n\n"
                            "بعد از تایید، مبلغ به کیف پولت اضافه می‌شود.")
            if receipt_kind == "photo":
                confirm_text += ("\n\n📷 لطفاً عکس فیش را همین‌جا در گفتگو "
                                 "با ربات بفرست تا شارژ تایید شود.")
            send_message(chat_id, confirm_text)
            notify_admin_new_order(store, order)
        elif ptype == "support_message":
            text = (payload.get("text") or "").strip()
            photo = payload.get("photo_file_id") or ""
            photo_bytes = _decode_photo_data(payload.get("photo_data"))
            if payload.get("has_photo") and not photo and not photo_bytes:
                # عکس در وب‌اپ فقط انتخاب شده؛ خود عکس بعداً در چت می‌رسد
                text = (text + "\n(📷 عکس هم دارد — مشتری در چت می‌فرستد.)").strip()
            if not text and not photo and not photo_bytes:
                raise ValueError("برای ارسال پیام به پشتیبانی، متن یا عکس بفرست.")
            _forward_support_to_admin(store, chat_id, text=text, photo_file_id=photo,
                                      photo_bytes=photo_bytes,
                                      label="💬 پشتیبانی (از فروشگاه)")
            confirm = "پیامت به پشتیبانی رسید؛ به‌زودی جواب می‌دهیم. 🙏"
            if payload.get("has_photo") and not photo and not photo_bytes:
                # عکس در وب‌اپ فقط انتخاب شده؛ خودش را در چت می‌خواهد تا
                # ادمین آن را ببیند — در تایید پیام هم گفته می‌شود.
                confirm += "\n📷 عکست را همین‌جا در گفتگو با ربات بفرست تا به پشتیبانی برسد."
            send_message(chat_id, confirm)
        elif ptype == "referral_share":
            code = store.get_or_create_referral_code(chat_id)
            send_message(chat_id,
                         f"کد معرفی تو: {code}\n"
                         f"https://t.me/{BOT_USERNAME}?start={code}\n\n"
                         + REFERRAL_LINE)
        elif ptype == "my_orders":
            send_my_orders(store, chat_id)
        elif ptype == "reorder":
            reorder_order(store, chat_id, payload.get("order_no"))
        elif ptype == "my_wallet":
            balance = store.wallet_balance(chat_id)
            send_message(chat_id,
                         f"👛 کیف پول من\n\nموجودی: {balance} USDT\n\n"
                         "برای شارژ، فروشگاه را باز کن و «شارژ کیف پول» را بزن.")
        else:
            send_message(chat_id, "دستور دریافتی از فروشگاه شناخته نشد.")
    except ValueError as ex:
        send_message(chat_id, f"⚠️ {ex}")
    store.save_if_dirty()


def handle_web_app_data(store, msg):
    """پیام web_app_data را اعتبارسنجی و پردازش می‌کند (باز شدن از
    دکمه‌ی کیبورد؛ باز شدن از دکمه‌ی منو از راه صندوق وب‌اپ می‌رسد)."""
    chat_id = msg["chat"]["id"]
    user = store.ensure_user(chat_id, (msg.get("from") or {}).get("username"),
                             (msg.get("from") or {}).get("first_name"))
    store.get_or_create_referral_code(chat_id)
    try:
        payload = json.loads(msg["web_app_data"]["data"])
    except Exception:
        send_message(chat_id, "داده‌ی دریافتی از فروشگاه معتبر نیست؛ دوباره تلاش کن.")
        return
    process_webapp_payload(store, chat_id, payload)


INBOX_CHECK_EVERY_SEC = 10  # صندوق وب‌اپ حداکثر هر ۱۰ ثانیه چک می‌شود
_LAST_INBOX_CHECK = 0


def process_webapp_inbox(store):
    """ردیف‌های صندوق وب‌اپ (webapp_inbox) را می‌خواند و پردازش می‌کند.

    مینی‌اپ سفارش/شارژ/پیام را از راه Edge Function اینجا می‌گذارد تا از
    هر جای باز شدن (دکمه‌ی آبی منو یا کیبورد) ثبت شود. هر ردیف: initData
    با توکن ربات اعتبارسنجی می‌شود؛ اگر معتبر بود پی‌لودش مثل web_app_data
    پردازش می‌شود؛ اگر نبود فقط لاگ می‌شود. ردیف در هر دو حالت حذف
    می‌شود؛ اگر پردازش خطا بدهد، ردیف حذف می‌شود و به کاربر پیام خطا
    می‌رسد. یک ردیف بد بقیه را متوقف نمی‌کند.
    """
    global _LAST_INBOX_CHECK
    now = time.time()
    if now - _LAST_INBOX_CHECK < INBOX_CHECK_EVERY_SEC:
        return
    _LAST_INBOX_CHECK = now
    try:
        rows = store.fetch_inbox_rows()
    except Exception as ex:
        print("خطا در خواندن صندوق وب‌اپ:", repr(ex))
        return
    for row in rows:
        row_id = row.get("id")
        user_id = row.get("user_id")
        try:
            user = validate_init_data(row.get("init_data") or "", TOKEN)
            if user is None:
                print("ردیف صندوق وب‌اپ initData معتبر ندارد؛ حذف می‌شود:", row_id)
            else:
                chat_id = int(user["id"])
                store.ensure_user(chat_id)
                store.get_or_create_referral_code(chat_id)
                payload = row.get("payload")
                if isinstance(payload, str):
                    payload = json.loads(payload)
                process_webapp_payload(store, chat_id, payload)
        except Exception as ex:
            print("خطا در پردازش ردیف صندوق وب‌اپ:", repr(ex))
            if user_id:
                try:
                    send_message(int(user_id),
                                 "مشکلی در ثبت سفارشت پیش آمد؛ لطفاً دوباره تلاش کن. 🙏")
                except Exception:
                    pass
        finally:
            try:
                store.delete_inbox_row(row_id)
            except Exception as ex:
                print("خطا در حذف ردیف صندوق وب‌اپ:", repr(ex))
    store.save_if_dirty()


def _decode_photo_data(photo_data):
    """data URL عکس (data:image/jpeg;base64,...) را به بایت تبدیل
    می‌کند؛ در هر خطایی None (رفتار قبلی: فقط نام عکس). سقف ۵ مگابایت."""
    if not photo_data or not isinstance(photo_data, str):
        return None
    try:
        if "," in photo_data:
            photo_data = photo_data.split(",", 1)[1]
        raw = base64.b64decode(photo_data, validate=True)
        if not raw or len(raw) > 5 * 1024 * 1024:
            return None
        return raw
    except Exception:
        return None


def _forward_support_to_admin(store, chat_id, text="", photo_file_id="", photo_bytes=None, label="💬 پشتیبانی"):
    """پیام پشتیبانی مشتری را به ادمین می‌رساند و آیدی پیام را
    برای برگرداندن جواب ادمین ذخیره می‌کند."""
    if not ADMIN_CHAT_ID:
        send_message(chat_id, "پشتیبانی در حال حاضر در دسترس نیست؛ بعداً تلاش کن.")
        return None
    user = store.get_user(chat_id) or {}
    name = user.get("first_name") or user.get("username") or str(chat_id)
    caption = f"{label}\nاز: {name} ({chat_id})"
    kb = support_reply_keyboard(chat_id)
    if photo_bytes:
        res = send_photo_bytes(ADMIN_CHAT_ID, photo_bytes,
                               caption=caption + (f"\n{text}" if text else ""),
                               reply_markup=json.dumps(kb, ensure_ascii=False))
    elif photo_file_id:
        res = api("sendPhoto", chat_id=ADMIN_CHAT_ID, photo=photo_file_id,
                  caption=caption + (f"\n{text}" if text else ""),
                  reply_markup=json.dumps(kb, ensure_ascii=False))
    else:
        res = send_message(ADMIN_CHAT_ID, caption + f"\n\n{text}", reply_markup=kb)
    if res and res.get("message_id"):
        store.map_support_message(res["message_id"], chat_id)
    return res


# ---------- دستورهای ادمین ----------
def _admin_only(chat_id):
    if not is_admin(chat_id):
        send_message(chat_id, "این دستور فقط برای ادمین است.")
        return False
    return True


PROMPT_TIMEOUT_SEC = 600  # ۱۰ دقیقه برای جواب به پرسش آرگومان

# دستورهایی که بدون آرگومان، ربات آرگومان را می‌پرسد
PROMPT_FOR = {
    "/approve": "شماره سفارش را بفرست (مثلاً GS-1001).",
    "/reject": "شماره سفارش را بفرست (مثلاً GS-1001).",
    "/delcode": "کد تخفیف را بفرست (مثلاً GOD20).",
    "/broadcast": "متن پیام همگانی را بفرست.",
    "/addcode": "کد و درصد را بفرست (مثلاً: GOD20 20). برای حداکثر مصرف، عدد سوم را هم بفرست (مثلاً: GOD20 20 50).",
    "/addreply": "سؤال و جواب را با | از هم جدا کن (مثلاً: سفارشم کی می‌رسه؟ | تا ۲۴ ساعت بعد از تایید).",
    "/delreply": "شماره پاسخ خودکار را بفرست.",
}


# متن لیست دستورهای ادمین — هم در جواب دستور ناشناخته، هم در منوی
# دستورهای ادمین (دکمه‌ی «📋 دستورهای ادمین») استفاده می‌شود.
ADMIN_COMMANDS_TEXT = (
    "دستورهای ادمین:\n"
    "📦 سفارش‌های در انتظار\n"
    "✅ تایید سفارش — با شماره سفارش\n"
    "❌ رد سفارش — با شماره سفارش\n"
    "🎟 کدهای تخفیف\n"
    "➕ ساخت کد تخفیف — با کد و درصد\n"
    "🗑 حذف کد تخفیف — با کد\n"
    "📣 پیام همگانی — با متن پیام\n"
    "📊 آمار\n"
    "💬 پاسخ‌های خودکار\n"
    "➕ ساخت پاسخ خودکار — با سؤال و جواب\n"
    "🗑 حذف پاسخ خودکار — با شماره")


def handle_admin_command(store, chat_id, text):
    parts = text.split()
    cmd = parts[0].lower()
    # دستور بدون آرگومان: به‌جای نشان دادن دوباره‌ی لیست، آرگومان را
    # می‌پرسیم؛ جواب در پیام بعدی می‌رسد (۱۰ دقیقه فرصت، «لغو» کنسل).
    needs = {"/approve": 2, "/reject": 2, "/delcode": 2, "/broadcast": 2,
             "/addcode": 3, "/addreply": 2, "/delreply": 2}
    if cmd in needs and len(parts) < needs[cmd]:
        store.state.setdefault("pending_prompt", {})[str(chat_id)] = {
            "cmd": cmd, "at": time.time()}
        store.dirty = True
        send_message(chat_id, PROMPT_FOR[cmd] + "\nبرای لغو «لغو» را بفرست.")
        return
    if cmd == "/orders":
        pending = store.list_pending_orders()
        if not pending:
            send_message(chat_id, "سفارش در انتظاری نیست. ✅")
            return
        lines = ["📋 سفارش‌های در انتظار تایید:\n"]
        for o in pending:
            lines.append(order_summary_text(o, store.get_user(o["user_id"])))
            lines.append("—")
        send_message(chat_id, "\n".join(lines))
    elif cmd == "/approve" and len(parts) >= 2:
        try:
            order = confirm_order(store, parts[1])
            send_message(chat_id, f"✅ سفارش {order['order_no']} تایید شد.")
        except ValueError as ex:
            send_message(chat_id, f"⚠️ {ex}")
    elif cmd == "/reject" and len(parts) >= 2:
        try:
            ask_reject_reason(store, chat_id, parts[1])
        except ValueError as ex:
            send_message(chat_id, f"⚠️ {ex}")
    elif cmd == "/reject_reason" and len(parts) >= 3:
        # جواب تایپ‌شده‌ی ادمین به پرسش دلیل رد:
        # "/reject_reason GS-1001 <دلیل>" — فقط از راه pending_prompt می‌رسد.
        order_no = parts[1]
        reason = text.split(None, 2)[2].strip()
        try:
            order = reject_order(store, order_no, reason)
            store.state.get("pending_reject", {}).pop(str(chat_id), None)
            store.dirty = True
            send_message(chat_id, f"❌ سفارش {order['order_no']} رد شد.\nدلیل: {reason}")
        except ValueError as ex:
            send_message(chat_id, f"⚠️ {ex}")
    elif cmd == "/support_reply" and len(parts) >= 3:
        # جواب تایپ‌شده‌ی ادمین به پیام پشتیبانی (از راه دکمه‌ی «✏️ پاسخ»):
        # "/support_reply <chat_id> <متن جواب>"
        try:
            customer_id = int(parts[1])
        except ValueError:
            send_message(chat_id, "⚠️ آیدی مشتری نامعتبر است.")
            return
        reply_text = text.split(None, 2)[2].strip()
        send_message(customer_id, reply_text, reply_markup=shop_button_markup())
        send_message(chat_id, "✅ جواب به مشتری فرستاده شد.")
    elif cmd == "/codes":
        codes = store.list_codes()
        if not codes:
            send_message(chat_id, "هنوز کد تخفیفی ثبت نشده است.")
            return
        lines = ["🎟 کدهای تخفیف:\n"]
        for c in codes:
            uses = f"{c.get('uses')}/{c.get('max_uses') or '∞'}"
            lines.append(f"{c['code']} — {c['percent']}٪ — مصرف: {uses} — "
                         f"{'فعال ✅' if c.get('active') else 'غیرفعال'}")
        send_message(chat_id, "\n".join(lines))
    elif cmd == "/addcode" and len(parts) >= 3:
        max_uses = int(parts[3]) if len(parts) >= 4 and parts[3].isdigit() else 0
        try:
            row = store.add_code(parts[1], int(parts[2]), max_uses)
            send_message(chat_id, f"✅ کد {row['code']} با {row['percent']}٪ تخفیف ساخته شد.")
        except ValueError as ex:
            send_message(chat_id, f"⚠️ {ex}")
    elif cmd == "/delcode" and len(parts) >= 2:
        try:
            store.del_code(parts[1])
            send_message(chat_id, f"🗑 کد {parts[1].upper()} حذف شد.")
        except ValueError as ex:
            send_message(chat_id, f"⚠️ {ex}")
    elif cmd == "/broadcast" and len(parts) >= 2:
        msg_text = text[len("/broadcast"):].strip()
        sent = 0
        for u in store.list_users():
            if send_message(u["chat_id"], msg_text):
                sent += 1
        send_message(chat_id, f"📣 پیام به {sent} کاربر فرستاده شد.")
    elif cmd == "/stats":
        s = store.stats()
        send_message(chat_id,
                     "📊 آمار «گاد استار»\n\n"
                     f"کاربران: {s['users']}\n"
                     f"سفارش‌ها: {s['orders_total']} (در انتظار: {s['orders_pending']}، "
                     f"تاییدشده: {s['orders_confirmed']}، ردشده: {s['orders_rejected']})\n"
                     f"درآمد (تاییدشده، بدون شارژ کیف پول): {s['revenue_usdt']} USDT")
    elif cmd == "/autoreplies":
        rows = store.list_auto_replies()
        if not rows:
            send_message(chat_id, "هنوز پاسخ خودکاری ثبت نشده است.")
            return
        lines = ["🤖 پاسخ‌های خودکار:\n"]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. «{r['question']}»\n   ← {r['answer']}")
        lines.append("\nحذف: /delreply <شماره>")
        send_message(chat_id, "\n".join(lines))
    elif cmd == "/addreply":
        rest = text[len("/addreply"):].strip()
        if "|" not in rest:
            send_message(chat_id, "فرمت: /addreply <سؤال> | <جواب>")
            return
        q, a = rest.split("|", 1)
        try:
            store.add_auto_reply(q, a)
            send_message(chat_id, "✅ پاسخ خودکار اضافه شد.")
        except ValueError as ex:
            send_message(chat_id, f"⚠️ {ex}")
    elif cmd == "/delreply" and len(parts) >= 2:
        try:
            store.del_auto_reply(parts[1])
            send_message(chat_id, "🗑 پاسخ خودکار حذف شد.")
        except ValueError as ex:
            send_message(chat_id, f"⚠️ {ex}")
    else:
        send_message(chat_id, ADMIN_COMMANDS_TEXT)


ADMIN_COMMANDS = {"/orders", "/approve", "/reject", "/codes", "/addcode",
                  "/delcode", "/broadcast", "/stats", "/autoreplies",
                  "/addreply", "/delreply"}


# ---------- پردازش پیام ----------
def handle_message(store, msg):
    chat_id = msg["chat"]["id"]
    frm = msg.get("from") or {}
    text = (msg.get("text") or "").strip()
    user = store.ensure_user(chat_id, frm.get("username"), frm.get("first_name"))
    # کد معرفی هر کاربر از همان اولین پیام ساخته می‌شود تا وقتی به
    # «کد معرفی من» می‌رسد، کد آماده باشد (بدون «دریافت کد»).
    store.get_or_create_referral_code(chat_id)

    if "web_app_data" in msg:
        handle_web_app_data(store, msg)
        return

    # جواب ادمین به پیام فورواردشده‌ی پشتیبانی → برگرداندن به مشتری
    if is_admin(chat_id) and msg.get("reply_to_message"):
        replied_id = msg["reply_to_message"].get("message_id")
        customer_id = store.support_customer_for(replied_id) if replied_id else None
        if customer_id:
            # جواب ادمین به مشتری می‌رسد (پیام تلگرام)؛ دکمه‌ی فروشگاه هم
            # همراهش است تا مشتری بتواند به مینی‌اپ برگردد. دیدن جواب
            # «داخل» مینی‌اپ بدون سرور ممکن نیست؛ اعلان تلگرام راه آن است.
            shop = shop_button_markup()
            if msg.get("photo"):
                file_id = msg["photo"][-1]["file_id"]
                photo_kw = {"chat_id": customer_id, "photo": file_id,
                            "caption": msg.get("caption") or ""}
                if shop:
                    photo_kw["reply_markup"] = json.dumps(shop, ensure_ascii=False)
                api("sendPhoto", **photo_kw)
            else:
                send_message(customer_id, msg.get("text") or "",
                             reply_markup=shop)
            send_message(chat_id, "✅ جواب به مشتری فرستاده شد.")
            store.save_if_dirty()
            return

    if text.startswith("/"):
        # دستور تازه، پرسش آرگومان قبلی را باطل می‌کند
        if (store.state.get("pending_prompt") or {}).pop(str(chat_id), None) is not None:
            store.dirty = True
    if text.startswith("/start"):
        param = text[len("/start"):].strip()
        if param and is_valid_referral_code(param):
            status, _ref = store.record_referral_use(param, chat_id)
            if status == "self":
                send_message(chat_id, "کد معرفی خودت را نمی‌توانی استفاده کنی 🙂")
            elif status == "used":
                send_message(chat_id, "این کد را قبلاً استفاده کرده‌ای.")
            elif status == "ok":
                send_message(chat_id,
                             "🎉 کد معرفی ثبت شد!\n" + REFERRAL_LINE + "\n" +
                             "۱۰٪ تخفیف روی اولین سفارشت اعمال می‌شود.")
        send_message(chat_id, welcome_text(is_admin(chat_id)),
                     reply_markup=main_keyboard(is_admin(chat_id)))
        store.save_if_dirty()
        return

    if text.startswith("/"):
        cmd = text.split()[0].lower()
        if cmd in ADMIN_COMMANDS:
            if _admin_only(chat_id):
                handle_admin_command(store, chat_id, text)
            store.save_if_dirty()
            return
        if cmd == "/help":
            send_message(chat_id, welcome_text(is_admin(chat_id)),
                         reply_markup=main_keyboard(is_admin(chat_id)))
            return
        send_message(chat_id, "دستور شناخته نشد. از منوی پایین استفاده کن 🙂",
                     reply_markup=main_keyboard(is_admin(chat_id)))
        return

    # منوی اصلی
    if text == "🛍 خدمات":
        send_message(chat_id, SERVICES_TEXT, reply_markup=main_keyboard(is_admin(chat_id)))
        return
    if text == "🛍 باز کردن فروشگاه":
        send_message(chat_id, "فروشگاه را از دکمه‌ی وب‌اپ باز کن 👆",
                     reply_markup=main_keyboard(is_admin(chat_id)))
        return
    if text == "📦 سفارش‌های من":
        send_my_orders(store, chat_id)
        return
    if text == "👛 کیف پول من":
        balance = store.wallet_balance(chat_id)
        completed = int(user.get("completed_orders") or 0)
        remaining = max(0, pricing.LOYALTY_AFTER_ORDERS - completed)
        loyalty_note = ("🎉 تخفیف وفاداری ۱۰٪ برای سفارش بعدیت فعال است!"
                        if remaining == 0 else
                        f"🎁 {LOYALTY_LINE} — {remaining} سفارش دیگر تا فعال شدن.")
        send_message(chat_id,
                     f"👛 کیف پول من\n\nموجودی: {balance} USDT\n\n"
                     f"{loyalty_note}\n\n"
                     "برای شارژ، فروشگاه را باز کن و «شارژ کیف پول» را بزن.\n"
                     f"{PAYMENT_NOTE}.")
        return
    if text == "💬 پشتیبانی":
        send_message(chat_id,
                     f"💬 پشتیبانی «گاد استار»\n\n{SUPPORT_LINE}\n\n"
                     "یکی را انتخاب کن:",
                     reply_markup=support_keyboard())
        return
    if text in ("💬 چت با پشتیبانی", "👤 ارتباط با مدیریت"):
        label = "💬 پشتیبانی" if text.startswith("💬") else "👤 ارتباط با مدیریت"
        store.set_support_mode(chat_id, True)
        store.state.setdefault("support_label", {})[str(chat_id)] = label
        send_message(chat_id,
                     f"{label}\n\nپیامت (متن یا عکس) را بفرست؛ مستقیم به پشتیبانی می‌رسد.\n"
                     "برای خروج، «🔙 بازگشت به منو» را بزن.")
        store.save_if_dirty()
        return
    if text == "🔙 بازگشت به منو":
        store.set_support_mode(chat_id, False)
        send_message(chat_id, "به منوی اصلی برگشتی 🙂", reply_markup=main_keyboard(is_admin(chat_id)))
        store.save_if_dirty()
        return
    if text == "🎁 کد معرفی من":
        code = store.get_or_create_referral_code(chat_id)
        invited = store.list_referrals(chat_id)
        lines = ["🎁 کد معرفی من\n",
                 f"کد تو: {code}",
                 f"لینک دعوت: https://t.me/{BOT_USERNAME}?start={code}\n",
                 REFERRAL_LINE,
                 REFERRAL_ONCE_LINE + "\n"]
        if invited:
            lines.append("دوستان دعوت‌شده:")
            for r in invited:
                friend = store.get_user(r["friend_id"]) or {}
                name = friend.get("first_name") or friend.get("username") or str(r["friend_id"])
                chip = "✅ فعال شد" if r.get("activated") else "⏳ در انتظار سفارش"
                lines.append(f"• {name} — {chip}")
        else:
            lines.append("هنوز کسی با کدت سفارش نداده")
        if user.get("referral_credit"):
            lines.append("\n🎉 ۱۰٪ تخفیف معرف برای سفارش بعدیت فعال است!")
        send_message(chat_id, "\n".join(lines))
        store.save_if_dirty()
        return

    # منوی دستورهای ادمین — فقط ادمین دکمه‌اش را می‌بیند؛ زدن هر دستور
    # در آن منو، همان دستور را می‌فرستد (دستور بدون آرگومان، آرگومان
    # را می‌پرسد).
    if text == "📋 دستورهای ادمین" and is_admin(chat_id):
        send_message(chat_id, ADMIN_COMMANDS_TEXT,
                     reply_markup=admin_keyboard())
        return

    # دکمه‌های منوی دستورهای ادمین — برچسب فارسی، همان دستور را می‌فرستد.
    if text in ADMIN_KEYBOARD_COMMANDS and is_admin(chat_id):
        handle_admin_command(store, chat_id, ADMIN_KEYBOARD_COMMANDS[text])
        store.save_if_dirty()
        return

    # جواب به پرسش آرگومان دستور ادمین (مثلاً شماره سفارش برای /approve)؛
    # دکمه‌های منو بالاتر بررسی شده‌اند، پس زدن دکمه وسط پرسش، پرسش را
    # از بین نمی‌برد (ادمین برای پیدا کردن شماره به آن‌ها نیاز دارد).
    if is_admin(chat_id) and text and not text.startswith("/"):
        prompt = (store.state.get("pending_prompt") or {}).get(str(chat_id))
        if prompt:
            if text == "لغو":
                store.state["pending_prompt"].pop(str(chat_id), None)
                store.dirty = True
                send_message(chat_id, "لغو شد.")
                store.save_if_dirty()
                return
            if time.time() - float(prompt.get("at") or 0) > PROMPT_TIMEOUT_SEC:
                store.state["pending_prompt"].pop(str(chat_id), None)
                store.dirty = True
                send_message(chat_id, "زمان پاسخ تمام شد؛ دستور را دوباره بفرست.")
                store.save_if_dirty()
                return
            cmd = prompt["cmd"]
            store.state["pending_prompt"].pop(str(chat_id), None)
            store.dirty = True
            handle_admin_command(store, chat_id, f"{cmd} {text}")
            store.save_if_dirty()
            return

    # حالت پشتیبانی: متن/عکس مشتری → اول پاسخ خودکار، بعد فوروارد به ادمین
    if store.in_support_mode(chat_id):
        if text:
            reply = store.find_auto_reply(text)
            if reply:
                send_message(chat_id, reply["answer"])
                return
        photo_file_id = ""
        if msg.get("photo"):
            photo_file_id = msg["photo"][-1]["file_id"]
        label = (store.state.get("support_label") or {}).get(str(chat_id), "💬 پشتیبانی")
        _forward_support_to_admin(store, chat_id, text=text,
                                  photo_file_id=photo_file_id, label=label)
        if not ADMIN_CHAT_ID:
            return
        send_message(chat_id, "پیامت رسید؛ به‌زودی جواب می‌دهیم. 🙏")
        store.save_if_dirty()
        return

    # رسید: عکس فیش یا هش تراکنش برای سفارش در انتظار (از وب‌اپ یا
    # سفارش دوباره) — قبل از پاسخ متفرقه بررسی می‌شود.
    if attach_receipt(store, chat_id, msg):
        store.save_if_dirty()
        return

    # پیام متفرقه: پاسخ خودکار (اگر تطابق داشت) وگرنه راهنمایی به منو
    if text:
        reply = store.find_auto_reply(text)
        if reply:
            send_message(chat_id, reply["answer"])
            return
    send_message(chat_id, "از منوی پایین انتخاب کن 🙂", reply_markup=main_keyboard(is_admin(chat_id)))


def handle_callback(store, query):
    data = query.get("data") or ""
    chat_id = (query.get("message") or {}).get("chat", {}).get("id")
    query_id = query.get("id")
    # سفارش دوباره مال مشتری است، نه ادمین؛ قبل از چک ادمین بررسی می‌شود.
    if data.startswith("reorder:"):
        order_no = data[len("reorder:"):]
        try:
            new_order = reorder_order(store, chat_id, order_no)
            answer_callback(query_id, f"سفارش تازه {new_order['order_no']} ثبت شد ✅")
        except ValueError as ex:
            answer_callback(query_id, str(ex))
        store.save_if_dirty()
        return
    if not is_admin(chat_id):
        answer_callback(query_id, "فقط ادمین می‌تواند سفارش را بررسی کند.")
        return
    if data.startswith("approve:"):
        order_no = data[len("approve:"):]
        try:
            confirm_order(store, order_no)
            answer_callback(query_id, f"سفارش {order_no} تایید شد ✅")
        except ValueError as ex:
            answer_callback(query_id, str(ex))
    elif data.startswith("reject_reason_type:"):
        order_no = data[len("reject_reason_type:"):]
        order = store.get_order(order_no)
        if order is None or order.get("status") != "pending":
            answer_callback(query_id, "این سفارش در انتظار نیست.")
        else:
            store.state.setdefault("pending_prompt", {})[str(chat_id)] = {
                "cmd": f"/reject_reason {order_no}", "at": time.time()}
            store.state.get("pending_reject", {}).pop(str(chat_id), None)
            store.dirty = True
            send_message(chat_id,
                         f"دلیل رد سفارش {order_no} را تایپ کن.\n"
                         "برای لغو «لغو» را بفرست.")
            answer_callback(query_id, "")
    elif data.startswith("reject_reason:"):
        rest = data[len("reject_reason:"):]
        order_no, _, idx = rest.rpartition(":")
        try:
            reason = REJECT_REASONS[int(idx)]
        except (ValueError, IndexError):
            answer_callback(query_id, "دلیل نامعتبر است.")
            return
        try:
            order = reject_order(store, order_no, reason)
            store.state.get("pending_reject", {}).pop(str(chat_id), None)
            store.dirty = True
            answer_callback(query_id, f"سفارش {order_no} رد شد ❌")
            send_message(chat_id, f"❌ سفارش {order['order_no']} رد شد.\nدلیل: {reason}")
        except ValueError as ex:
            answer_callback(query_id, str(ex))
    elif data.startswith("reject_cancel:"):
        order_no = data[len("reject_cancel:"):]
        store.state.get("pending_reject", {}).pop(str(chat_id), None)
        store.dirty = True
        answer_callback(query_id, "لغو شد.")
        send_message(chat_id, f"رد سفارش {order_no} لغو شد؛ سفارش در انتظار می‌ماند.")
    elif data.startswith("reject:"):
        order_no = data[len("reject:"):]
        try:
            ask_reject_reason(store, chat_id, order_no)
            answer_callback(query_id, "دلیل رد را انتخاب کن.")
        except ValueError as ex:
            answer_callback(query_id, str(ex))
    elif data.startswith("support_reply:"):
        customer_id = data[len("support_reply:"):]
        if not customer_id.isdigit():
            answer_callback(query_id, "مشتری نامعتبر است.")
            return
        store.state.setdefault("pending_prompt", {})[str(chat_id)] = {
            "cmd": f"/support_reply {customer_id}", "at": time.time()}
        store.dirty = True
        send_message(chat_id,
                     "جوابت به مشتری را تایپ کن.\nبرای لغو «لغو» را بفرست.")
        answer_callback(query_id, "")
    else:
        answer_callback(query_id, "")
    store.save_if_dirty()


def run_once(store):
    updates = api_get_updates(store.state.get("tg_offset", 0))
    for u in updates:
        store.state["tg_offset"] = u["update_id"] + 1
        try:
            if "message" in u:
                handle_message(store, u["message"])
            elif "callback_query" in u:
                handle_callback(store, u["callback_query"])
        except Exception as ex:
            print("خطا در پردازش آپدیت:", repr(ex))
    try:
        check_reminders(store)
    except Exception as ex:
        print("خطا در یادآوری‌ها:", repr(ex))
    try:
        process_webapp_inbox(store)
    except Exception as ex:
        print("خطا در صندوق وب‌اپ:", repr(ex))
    store.save_if_dirty()


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    print(f"نسخه‌ی ربات گاد استار: {BOT_VERSION}")
    if not TOKEN:
        print("GODSTAR_BOT_TOKEN is not set — refusing to run")
        raise SystemExit(1)
    if not ADMIN_CHAT_ID:
        print("هشدار: ADMIN_CHAT_ID تنظیم نشده — اقدام‌های ادمین غیرفعال‌اند")
    else:
        print("ادمین تنظیم شده — /start برای ادمین نسخه و دستورها را نشان می‌دهد")
    setup_menu_button()
    setup_commands()
    store = Store()
    print("state backend:", "supabase" if store.supabase_enabled() else "file")
    store.load()
    ensure_defaults(store)
    loop_minutes = LOOP_MINUTES
    end = time.time() + loop_minutes * 60 if loop_minutes > 0 else 0
    while True:
        try:
            run_once(store)
        except SystemExit:
            raise
        except Exception as ex:
            print("خطا در این دور:", repr(ex))
        if loop_minutes <= 0:
            break
        if time.time() >= end:
            break
        time.sleep(POLL_SECONDS)
    store.save_if_dirty()


if __name__ == "__main__":
    main()
