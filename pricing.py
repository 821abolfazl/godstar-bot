# pricing.py — منطق خالص قیمت‌گذاری «گاد استار» (بدون هیچ فراخوانی تلگرام)
#
# همه‌ی قیمت‌ها به تتر (USDT) و همه‌ی محاسبه‌ها با Decimal انجام می‌شود
# تا خطای اعشار float پیش نیاید (مثلاً 15 با 10٪ تخفیف دقیقاً 13.5 می‌شود).

import re
from decimal import Decimal, ROUND_HALF_UP

WALLET_ADDRESS = "0x747f4B72a967fC504a77Ba38a681939dDeB6F793"

# ---------- تبلیغات تلگرام ----------
ADS_MIN_VIEWS = 10_000
ADS_MAX_VIEWS = 1_000_000
ADS_STEP_VIEWS = 10_000
# قیمت به ازای هر ۱۰ هزار بازدید، بر اساس نوع رسانه
ADS_PRICE_PER_10K = {"none": Decimal("5"), "photo": Decimal("6"), "video": Decimal("7")}
ADS_MEDIA_LABELS = {"none": "بدون رسانه", "photo": "عکس", "video": "ویدیو"}

# ---------- ممبر آپلودری ----------
MEMBER_MIN = 1_000
MEMBER_MAX = 20_000
MEMBER_STEP = 1_000
MEMBER_PRICE_PER_1K = Decimal("3")

# ---------- ربات تلگرام (مدیریت کانال) ----------
# قیمت درج‌شده همان قیمت نهایی است؛ تخفیف نسبت به قیمت ماهانه حساب شده:
# 1 ماه 10 (بدون تخفیف)، 3 ماه 28 به‌جای 30 (2 تخفیف)،
# 6 ماه 54 به‌جای 60 (6 تخفیف)، 12 ماه 100 به‌جای 120 (20 تخفیف)
BOT_PLANS = {
    "1m": {"months": 1, "price": Decimal("10"), "list_price": Decimal("10"), "discount": Decimal("0")},
    "3m": {"months": 3, "price": Decimal("28"), "list_price": Decimal("30"), "discount": Decimal("2")},
    "6m": {"months": 6, "price": Decimal("54"), "list_price": Decimal("60"), "discount": Decimal("6")},
    "12m": {"months": 12, "price": Decimal("100"), "list_price": Decimal("120"), "discount": Decimal("20")},
}
BOT_PLAN_LABELS = {"1m": "۱ ماهه", "3m": "۳ ماهه", "6m": "۶ ماهه", "12m": "۱۲ ماهه"}

# ---------- شارژ کیف پول ----------
WALLET_CHARGE_AMOUNTS = [Decimal("10"), Decimal("20"), Decimal("50"), Decimal("100")]

# ---------- تخفیف‌ها ----------
REFERRAL_PERCENT = 10        # تخفیف معرفی (دوست و معرف هر دو ۱۰٪)
LOYALTY_PERCENT = 10         # تخفیف وفاداری بعد از ۳ سفارش تکمیل‌شده
LOYALTY_AFTER_ORDERS = 3


def _money(value):
    """گرد کردن به دو رقم اعشار (نیم به بالا)، خروجی Decimal."""
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def ads_price(views, media="none"):
    """قیمت تبلیغات: views باید بین 10,000 تا 1,000,000 و مضرب 10,000 باشد."""
    if media not in ADS_PRICE_PER_10K:
        raise ValueError("نوع رسانه نامعتبر است (بدون رسانه / عکس / ویدیو).")
    if not isinstance(views, int) or isinstance(views, bool):
        raise ValueError("تعداد بازدید باید عدد صحیح باشد.")
    if views < ADS_MIN_VIEWS or views > ADS_MAX_VIEWS:
        raise ValueError("تعداد بازدید باید بین ۱۰٬۰۰۰ تا ۱٬۰۰۰٬۰۰۰ باشد.")
    if views % ADS_STEP_VIEWS:
        raise ValueError("تعداد بازدید باید مضرب ۱۰٬۰۰۰ باشد.")
    return _money(ADS_PRICE_PER_10K[media] * (views // ADS_STEP_VIEWS))


def member_price(count):
    """قیمت ممبر: count باید بین 1,000 تا 20,000 و مضرب 1,000 باشد."""
    if not isinstance(count, int) or isinstance(count, bool):
        raise ValueError("تعداد ممبر باید عدد صحیح باشد.")
    if count < MEMBER_MIN or count > MEMBER_MAX:
        raise ValueError("تعداد ممبر باید بین ۱٬۰۰۰ تا ۲۰٬۰۰۰ باشد.")
    if count % MEMBER_STEP:
        raise ValueError("تعداد ممبر باید مضرب ۱٬۰۰۰ باشد.")
    return _money(MEMBER_PRICE_PER_1K * (count // MEMBER_STEP))


def bot_price(plan):
    """قیمت اشتراک ربات مدیریت کانال برای پلن داده‌شده."""
    if plan not in BOT_PLANS:
        raise ValueError("پلن نامعتبر است (۱ / ۳ / ۶ / ۱۲ ماهه).")
    return BOT_PLANS[plan]["price"]


def apply_discount(amount, percent):
    """اعمال درصد تخفیف روی مبلغ؛ خروجی (مبلغ نهایی، مقدار تخفیف)."""
    amount = Decimal(amount)
    percent = Decimal(percent)
    if percent < 0 or percent > 100:
        raise ValueError("درصد تخفیف باید بین ۰ تا ۱۰۰ باشد.")
    discount = _money(amount * percent / Decimal("100"))
    return _money(amount - discount), discount


def choose_discount(*, loyalty=False, referral=False, code_percent=None):
    """قانون ترکیب تخفیف (تک‌تخفیف در هر سفارش):
    فقط یک تخفیف اعمال می‌شود — بزرگ‌ترین درصد؛ در صورت تساوی،
    اولویت: وفاداری > معرفی > کد تخفیف.
    خروجی: (kind, percent) که kind یکی از
    "loyalty" / "referral" / "code" / None است."""
    candidates = []
    if loyalty:
        candidates.append(("loyalty", LOYALTY_PERCENT))
    if referral:
        candidates.append(("referral", REFERRAL_PERCENT))
    if code_percent:
        candidates.append(("code", int(code_percent)))
    if not candidates:
        return None, 0
    priority = {"loyalty": 0, "referral": 1, "code": 2}
    # بزرگ‌ترین درصد اول، بعد کم‌ترین عدد اولویت (وفاداری اول)
    best = sorted(candidates, key=lambda c: (-c[1], priority[c[0]]))[0]
    return best


def compute_order_price(base_amount, *, loyalty=False, referral=False, code_percent=None):
    """قیمت نهایی سفارش با قانون تک‌تخفیف.
    خروجی: (final, discount_amount, kind, percent)."""
    kind, percent = choose_discount(loyalty=loyalty, referral=referral,
                                    code_percent=code_percent)
    if kind is None:
        return _money(base_amount), Decimal("0"), None, 0
    final, discount = apply_discount(base_amount, percent)
    return final, discount, kind, percent


def loyalty_eligible(completed_orders):
    """وفاداری: بعد از ۳ سفارش تکمیل‌شده، ۱۰٪ تخفیف خودکار."""
    return int(completed_orders or 0) >= LOYALTY_AFTER_ORDERS


_CHANNEL_RE = re.compile(r"^@[A-Za-z0-9_]{4,}$")


def is_valid_channel_id(channel_id):
    """آیدی کانال باید با @ شروع شود و فقط حروف/عدد/آندرلاین داشته باشد."""
    return isinstance(channel_id, str) and bool(_CHANNEL_RE.match(channel_id.strip()))


_REFERRAL_RE = re.compile(r"^[A-Za-z0-9]+$")


def is_valid_referral_code(code):
    """کد معرفی: فقط حروف و عدد، بدون خط تیره یا علامت (مثلاً GOD4821)."""
    return isinstance(code, str) and 4 <= len(code) <= 12 and bool(_REFERRAL_RE.match(code))


def derive_referral_code(chat_id):
    """کد معرفی یکتا و پایدار برای هر کاربر، از روی آیدی تلگرامش.

    وب‌اپ (ایستا، بدون سرور) کد واقعی را از جایی نمی‌تواند بگیرد؛ پس
    کد از روی آیدی ساخته می‌شود تا ربات و وب‌اپ بدون هیچ دریافتی همان
    کد را نشان بدهند. FNV-1a سی‌ودوبیتی روی رشته‌ی آیدی، بعد GOD +
    شش رقم — همان تابع در webapp/app.js هم هست و باید همان خروجی را
    بدهد. اگر کدِ مشتق‌شده را کاربر دیگری گرفته باشد، ربات به‌جایش
    یک کد تصادفی یکتا می‌سازد (در آن حالت نادر، وب‌اپ کد مشتق‌شده را
    نشان می‌دهد و لینک اشتراک‌گذاری به معرف درست نمی‌رسد).
    """
    h = 2166136261
    for ch in str(chat_id):
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return f"GOD{100000 + (h % 900000)}"


def make_referral_code(existing_codes, _randint=None):
    """ساخت کد معرفی تازه و یکتا به شکل GOD + چهار رقم (مثلاً GOD4821)."""
    import random
    randint = _randint or random.randint
    for _ in range(100):
        code = f"GOD{randint(1000, 9999)}"
        if code not in existing_codes:
            return code
    raise ValueError("نتوانستم کد معرفی یکتا بسازم؛ دوباره تلاش کن.")
