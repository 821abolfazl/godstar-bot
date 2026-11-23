# ربات فروش خدمات «گاد استار» (God Star) — نسخه‌ی ۱
#
# خدمات: تبلیغات تلگرام، ممبر آپلودری، ربات تلگرام (مدیریت + اختصاصی).
# پرداخت فقط تتر (USDT) روی شبکه‌ی BEP20.
#
# راه‌اندازی:
#   1) ربات در BotFather ساخته شده: @GodStarServiceBot
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

import json
import os
import sys
import time

import requests

from db import Store
import pricing
from pricing import (WALLET_ADDRESS, ads_price, member_price, bot_price,
                     compute_order_price, loyalty_eligible,
                     is_valid_channel_id, is_valid_referral_code)

BOT_VERSION = "2026-11-23-0.3"   # فقط توی لاگ و برای ادمین در /start نوشته می‌شه
BOT_USERNAME = "GodStarServiceBot"
WEBAPP_URL = os.environ.get("WEBAPP_URL") or "https://821abolfazl.github.io/godstar/"

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


# ---------- کیبوردها ----------
def main_keyboard():
    return {"keyboard": [
        [{"text": "🛍 خدمات", "web_app": {"url": WEBAPP_URL}},
         {"text": "📦 سفارش‌های من"}],
        [{"text": "👛 کیف پول من"}, {"text": "💬 پشتیبانی"}],
        [{"text": "🎁 کد معرفی من"}],
        [{"text": "🛍 باز کردن فروشگاه", "web_app": {"url": WEBAPP_URL}}],
    ], "resize_keyboard": True}


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


# ---------- متن‌ها ----------
def welcome_text(is_admin_user):
    text = ("سلام! 👋 به «گاد استار» خوش اومدی ⭐\n\n"
            "اینجا می‌تونی تبلیغات تلگرام بگیری، ممبر بخری یا ربات تلگرام "
            "برای کانالت سفارش بدی.\n\n"
            f"{PAYMENT_NOTE} به آدرس کیف پول ما.\n\n"
            f"{SUPPORT_LINE}")
    if is_admin_user:
        text += f"\n\nنسخه‌ی ربات: {BOT_VERSION}"
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
    """پاسخ‌های خودکار پیش‌فرض و کد GOD10 اگر هنوز ثبت نشده باشند."""
    if not store.list_auto_replies():
        store.add_auto_reply(
            "سفارشم کی آماده می‌شه؟",
            "سفارش‌های ممبر و تبلیغات معمولاً تا ۲۴ ساعت بعد از تایید پرداخت "
            "انجام می‌شوند و نتیجه‌اش را با پیام بهت خبر می‌دهیم.")
        store.add_auto_reply(
            "چطور پرداخت کنم؟",
            "فقط تتر (USDT) روی شبکه‌ی BEP20 به آدرس کیف پول ما بفرست و "
            "رسید یا کد هش تراکنش را ارسال کن.")
    if store.get_code("GOD10") is None:
        store.add_code("GOD10", 10, 0)
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
        code_percent = int(code_row["percent"])
    return loyalty, referral, code_percent, code_row


def place_order(store, user_id, service, details, discount_code=None,
                paid_by_wallet=False, tx_hash="", receipt_file_id="",
                receipt_kind=""):
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
        else:
            _require_receipt({"tx_hash": tx_hash, "receipt_file_id": receipt_file_id})

    order = store.create_order(
        user_id, order_type, service, details, str(final),
        discount_kind=kind, discount_percent=percent,
        discount_amount=str(discount_amount),
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


def reject_order(store, order_no):
    """رد سفارش توسط ادمین: وضعیت + پیام خودکار + برگشت پول کیف پول."""
    order = store.set_order_status(order_no, "rejected")
    user_id = order["user_id"]
    if order.get("paid_by_wallet") and float(order.get("amount") or 0) > 0:
        store.credit_wallet(user_id, float(order["amount"]),
                            note=f"برگشت وجه سفارش ردشده {order_no}")
    send_message(user_id,
                 f"❌ متأسفانه سفارشت رد شد.\n\n"
                 f"شماره سفارش: {order_no}\n"
                 "اگر پول از کیف پولت کم شده بود، برگشت داده شد.\n"
                 f"{SUPPORT_LINE}")
    return order


# ---------- وب‌اپ (web_app_data) ----------
def handle_web_app_data(store, msg):
    """پیام web_app_data را اعتبارسنجی و پردازش می‌کند.
    قیمت هرگز از مشتری گرفته نمی‌شود؛ سرور دوباره حساب می‌کند."""
    chat_id = msg["chat"]["id"]
    user = store.ensure_user(chat_id, (msg.get("from") or {}).get("username"),
                             (msg.get("from") or {}).get("first_name"))
    try:
        payload = json.loads(msg["web_app_data"]["data"])
    except Exception:
        send_message(chat_id, "داده‌ی دریافتی از فروشگاه معتبر نیست؛ دوباره تلاش کن.")
        return
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
            if payload.get("has_photo") and not photo:
                # عکس در وب‌اپ فقط انتخاب شده؛ خود عکس بعداً در چت می‌رسد
                text = (text + "\n(📷 عکس هم دارد — مشتری در چت می‌فرستد.)").strip()
            if not text and not photo:
                raise ValueError("برای ارسال پیام به پشتیبانی، متن یا عکس بفرست.")
            _forward_support_to_admin(store, chat_id, text=text, photo_file_id=photo,
                                      label="💬 پشتیبانی (از فروشگاه)")
            send_message(chat_id, "پیامت به پشتیبانی رسید؛ به‌زودی جواب می‌دهیم. 🙏")
        elif ptype == "referral_share":
            code = store.get_or_create_referral_code(chat_id)
            send_message(chat_id,
                         f"کد معرفی تو: {code}\n"
                         f"https://t.me/{BOT_USERNAME}?start={code}\n\n"
                         + REFERRAL_LINE)
        else:
            send_message(chat_id, "دستور دریافتی از فروشگاه شناخته نشد.")
    except ValueError as ex:
        send_message(chat_id, f"⚠️ {ex}")
    store.save_if_dirty()


def _forward_support_to_admin(store, chat_id, text="", photo_file_id="", label="💬 پشتیبانی"):
    """پیام پشتیبانی مشتری را به ادمین می‌رساند و آیدی پیام را
    برای برگرداندن جواب ادمین ذخیره می‌کند."""
    if not ADMIN_CHAT_ID:
        send_message(chat_id, "پشتیبانی در حال حاضر در دسترس نیست؛ بعداً تلاش کن.")
        return None
    user = store.get_user(chat_id) or {}
    name = user.get("first_name") or user.get("username") or str(chat_id)
    caption = f"{label}\nاز: {name} ({chat_id})"
    if photo_file_id:
        res = api("sendPhoto", chat_id=ADMIN_CHAT_ID, photo=photo_file_id,
                  caption=caption + (f"\n{text}" if text else ""))
    else:
        res = send_message(ADMIN_CHAT_ID, caption + f"\n\n{text}")
    if res and res.get("message_id"):
        store.map_support_message(res["message_id"], chat_id)
    return res


# ---------- دستورهای ادمین ----------
def _admin_only(chat_id):
    if not is_admin(chat_id):
        send_message(chat_id, "این دستور فقط برای ادمین است.")
        return False
    return True


def handle_admin_command(store, chat_id, text):
    parts = text.split()
    cmd = parts[0].lower()
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
            order = reject_order(store, parts[1])
            send_message(chat_id, f"❌ سفارش {order['order_no']} رد شد.")
        except ValueError as ex:
            send_message(chat_id, f"⚠️ {ex}")
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
        send_message(chat_id,
                     "دستورهای ادمین:\n"
                     "/orders — سفارش‌های در انتظار\n"
                     "/approve <شماره> — تایید سفارش\n"
                     "/reject <شماره> — رد سفارش\n"
                     "/codes — لیست کدهای تخفیف\n"
                     "/addcode <CODE> <درصد> [حداکثر مصرف]\n"
                     "/delcode <CODE>\n"
                     "/broadcast <متن>\n"
                     "/stats — آمار\n"
                     "/autoreplies — پاسخ‌های خودکار\n"
                     "/addreply <سؤال> | <جواب>\n"
                     "/delreply <شماره>")


ADMIN_COMMANDS = {"/orders", "/approve", "/reject", "/codes", "/addcode",
                  "/delcode", "/broadcast", "/stats", "/autoreplies",
                  "/addreply", "/delreply"}


# ---------- پردازش پیام ----------
def handle_message(store, msg):
    chat_id = msg["chat"]["id"]
    frm = msg.get("from") or {}
    text = (msg.get("text") or "").strip()
    user = store.ensure_user(chat_id, frm.get("username"), frm.get("first_name"))

    if "web_app_data" in msg:
        handle_web_app_data(store, msg)
        return

    # جواب ادمین به پیام فورواردشده‌ی پشتیبانی → برگرداندن به مشتری
    if is_admin(chat_id) and msg.get("reply_to_message"):
        replied_id = msg["reply_to_message"].get("message_id")
        customer_id = store.support_customer_for(replied_id) if replied_id else None
        if customer_id:
            if msg.get("photo"):
                file_id = msg["photo"][-1]["file_id"]
                api("sendPhoto", chat_id=customer_id, photo=file_id,
                    caption=msg.get("caption") or "")
            else:
                send_message(customer_id, msg.get("text") or "")
            send_message(chat_id, "✅ جواب به مشتری فرستاده شد.")
            store.save_if_dirty()
            return

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
                     reply_markup=main_keyboard())
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
                         reply_markup=main_keyboard())
            return
        send_message(chat_id, "دستور شناخته نشد. از منوی پایین استفاده کن 🙂",
                     reply_markup=main_keyboard())
        return

    # منوی اصلی
    if text == "🛍 خدمات":
        send_message(chat_id, SERVICES_TEXT, reply_markup=main_keyboard())
        return
    if text == "🛍 باز کردن فروشگاه":
        send_message(chat_id, "فروشگاه را از دکمه‌ی وب‌اپ باز کن 👆",
                     reply_markup=main_keyboard())
        return
    if text == "📦 سفارش‌های من":
        orders = store.list_orders(user_id=chat_id)
        if not orders:
            send_message(chat_id, "هنوز سفارشی ثبت نکرده‌ای 🙂\n" + ORDERS_NOTICE)
            return
        lines = ["📦 سفارش‌های تو:\n", ORDERS_NOTICE + "\n"]
        for o in orders:
            lines.append(order_summary_text(o))
            lines.append("—")
        send_message(chat_id, "\n".join(lines))
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
        send_message(chat_id, "به منوی اصلی برگشتی 🙂", reply_markup=main_keyboard())
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

    # پیام متفرقه: پاسخ خودکار (اگر تطابق داشت) وگرنه راهنمایی به منو
    if text:
        reply = store.find_auto_reply(text)
        if reply:
            send_message(chat_id, reply["answer"])
            return
    send_message(chat_id, "از منوی پایین انتخاب کن 🙂", reply_markup=main_keyboard())


def handle_callback(store, query):
    data = query.get("data") or ""
    chat_id = (query.get("message") or {}).get("chat", {}).get("id")
    query_id = query.get("id")
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
    elif data.startswith("reject:"):
        order_no = data[len("reject:"):]
        try:
            reject_order(store, order_no)
            answer_callback(query_id, f"سفارش {order_no} رد شد ❌")
        except ValueError as ex:
            answer_callback(query_id, str(ex))
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
