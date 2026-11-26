# db.py — لایه‌ی ذخیره‌سازی «گاد استار»
#
# دو حالت دارد (مثل ربات دنبال‌کننده‌ی GodNews24):
#   1) سوپابیس (وقتی SUPABASE_URL و SUPABASE_SERVICE_KEY تنظیم شده باشند):
#      جدول‌های رابطه‌ای واقعی (users, orders, wallet_tx, discount_codes,
#      referrals, support_threads, auto_replies) — نه یک بلاب رمزنگاری‌شده.
#      اسکیما در supabase.sql است. در شروع، همه‌ی ردیف‌ها خوانده و در حافظه
#      نگه داشته می‌شوند؛ در ذخیره، ردیف‌ها upsert می‌شوند.
#      نکته: با سیستم کلید تازه‌ی سوپابیس، هدر apikey باید کلید عمومی
#      (SUPABASE_PUBLISHABLE_KEY) و هدر Authorization باید کلید محرمانه
#      (SUPABASE_SERVICE_KEY) باشد؛ یک کلید برای هر دو جواب نمی‌دهد.
#   2) فایل JSON محلی (godstar_state.json، در gitignore): وقتی متغیرهای
#      سوپابیس نیستند. برای تست و اجرای محلی.
#
# همه‌ی متدها روی dict درون‌حافظه‌ای کار می‌کنند و قابل تست‌اند؛
# هیچ‌کدام مستقیم به تلگرام وصل نمی‌شوند.

import json
import os
import time
from datetime import datetime, timezone

import requests

STATE_FILE = os.environ.get("GODSTAR_STATE_FILE") or "godstar_state.json"


def _now_iso():
    """زمان فعلی به‌صورت ISO 8601 (UTC)؛ سوپابیس برای timestamptz
    عدد خام قبول نمی‌کند، پس created_at هرگز int ذخیره نمی‌شود."""
    return datetime.now(timezone.utc).isoformat()


def _ts_value(value):
    """مقدار زمانی (ISO یا epoch) را به ثانیه‌ی epoch تبدیل می‌کند؛
    برای مرتب‌سازی سفارش‌ها (داده‌ی قدیمی int هم پشتیبانی می‌شود)."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return 0.0
    return 0.0

# نگاشت جدول سوپابیس -> کلید حافظه‌ی محلی
_TABLES = {
    "users": "users",                 # dict: str(chat_id) -> row
    "orders": "orders",               # dict: order_no -> row
    "wallet_tx": "wallet_tx",         # list of rows
    "discount_codes": "discount_codes",  # dict: CODE -> row
    "referrals": "referrals",         # list of rows
    "support_threads": "support_threads",  # dict: str(admin_msg_id) -> row
    "auto_replies": "auto_replies",   # list of rows
    "order_reminders": "order_reminders",  # list of rows: {order_no, kind}
}
# ستون کلید اصلی هر جدول (برای upsert)
_TABLE_KEYS = {
    "users": "chat_id",
    "orders": "order_no",
    "wallet_tx": "id",
    "discount_codes": "code",
    "referrals": "id",
    "support_threads": "admin_message_id",
    "auto_replies": "id",
    "order_reminders": "order_no,kind",
}


def _empty_state():
    return {
        "users": {}, "orders": {}, "wallet_tx": [], "discount_codes": {},
        "referrals": [], "support_threads": {}, "auto_replies": [],
        "order_reminders": [],
        "order_seq": 1000, "referral_seq": 0, "wallet_tx_seq": 0,
        "auto_reply_seq": 0, "tg_offset": 0,
        "support_mode": {},   # str(chat_id) -> True (کاربر در حالت پشتیبانی است)
        "pending_prompt": {},  # str(chat_id) -> {"cmd", "at"} (پرسش آرگومان دستور ادمین)
        "pending_reject": {},  # str(admin chat_id) -> {"order_no", "at"} (رد در انتظار دلیل)
    }


class Store:
    def __init__(self, state_file=None, supabase_url=None, supabase_key=None,
                 supabase_publishable_key=None):
        self.state_file = state_file or STATE_FILE
        self.supabase_url = supabase_url if supabase_url is not None else (os.environ.get("SUPABASE_URL") or "")
        self.supabase_key = supabase_key if supabase_key is not None else (os.environ.get("SUPABASE_SERVICE_KEY") or "")
        # کلید عمومی سوپابیس (sb_publishable_...) فقط برای هدر apikey است؛
        # کلید محرمانه (secret/service) فقط برای Authorization: Bearer.
        self.supabase_publishable_key = (
            supabase_publishable_key if supabase_publishable_key is not None
            else (os.environ.get("SUPABASE_PUBLISHABLE_KEY") or ""))
        self.state = _empty_state()
        self.dirty = False

    # ---------- سوپابیس ----------
    def supabase_enabled(self):
        return bool(self.supabase_url and self.supabase_key)

    def _sb_headers(self):
        # با سیستم کلید تازه‌ی سوپابیس، apikey و Bearer باید دو کلید جدا
        # باشند (عمومی و محرمانه). اگر کلید عمومی تنظیم نشده باشد، برای
        # سازگاری با نسخه‌های قدیمی از کلید سرویس برای هر دو استفاده می‌شود.
        apikey = self.supabase_publishable_key or self.supabase_key
        return {"apikey": apikey,
                "Authorization": f"Bearer {self.supabase_key}",
                "Content-Type": "application/json"}

    def _sb_get_all(self, table):
        """همه‌ی ردیف‌های یک جدول را می‌خواند."""
        url = f"{self.supabase_url.rstrip('/')}/rest/v1/{table}?select=*"
        try:
            r = requests.get(url, headers=self._sb_headers(), timeout=15)
        except Exception as ex:
            raise RuntimeError(f"supabase unreachable (network?): {type(ex).__name__}") from ex
        if r.status_code in (401, 403):
            raise RuntimeError("supabase auth failed — SUPABASE_SERVICE_KEY is wrong "
                               "(use the service_role key, not anon)")
        if r.status_code == 404:
            raise RuntimeError(f"supabase table missing ({table}) — run supabase.sql in the SQL Editor")
        r.raise_for_status()
        return r.json()

    def _sb_upsert_rows(self, table, rows):
        if not rows:
            return
        # سوپابیس برای upsert ستون درگیری را می‌خواهد؛ بدون آن ۴۰۰ می‌دهد.
        conflict = _TABLE_KEYS.get(table, "")
        url = f"{self.supabase_url.rstrip('/')}/rest/v1/{table}"
        if conflict:
            url += f"?on_conflict={conflict}"
        # PostgREST در upsert دسته‌ای (PGRST102) می‌خواهد همه‌ی آبجکت‌ها
        # دقیقاً کلیدهای یکسانی داشته باشند. ردیف خوانده‌شده از دیتابیس
        # ستون‌های null (مثل confirmed_at سفارش) را دارد، ولی ردیف
        # تازه‌ساخته آن کلید را ندارد — پس کلیدها را به اجتماع همه‌ی
        # کلیدها یکسان می‌کنیم (کلیدِ نیست = null). بدون این، با آمدن
        # دومین سفارش کل ذخیره‌ی جدول orders می‌افتاد (GS-1002 هرگز
        # در دیتابیس نوشته نشد و «سفارش‌های من» آن را نشان نمی‌داد).
        keys = []
        for row in rows:
            for k in row:
                if k not in keys:
                    keys.append(k)
        normed = [{k: row.get(k) for k in keys} for row in rows]
        try:
            r = requests.post(url,
                              headers={**self._sb_headers(),
                                       "Prefer": "resolution=merge-duplicates"},
                              json=normed, timeout=15)
        except Exception as ex:
            raise RuntimeError(f"supabase unreachable (network?): {type(ex).__name__}") from ex
        if r.status_code in (401, 403):
            raise RuntimeError("supabase auth failed — SUPABASE_SERVICE_KEY is wrong")
        if r.status_code == 404:
            raise RuntimeError(f"supabase table missing ({table}) — run supabase.sql in the SQL Editor")
        if not r.ok:
            # متن پاسخ سوپابیس علت دقیق را می‌گوید؛ در لاگ بماند.
            raise RuntimeError(f"supabase upsert failed ({table}): "
                               f"{r.status_code} {r.text[:300]}")

    def _sb_delete(self, table, key_col, key_val):
        url = (f"{self.supabase_url.rstrip('/')}/rest/v1/{table}"
               f"?{key_col}=eq.{key_val}")
        try:
            r = requests.delete(url, headers=self._sb_headers(), timeout=15)
        except Exception as ex:
            raise RuntimeError(f"supabase unreachable (network?): {type(ex).__name__}") from ex
        r.raise_for_status()

    # ---------- صندوق ورودی مینی‌اپ (نسخه‌ی ۱٫۰) ----------
    def fetch_inbox_rows(self):
        """ردیف‌های صندوق وب‌اپ را می‌خواند (مرتب بر اساس زمان ساخت).

        جدول گذراست و در load/save نمی‌آید؛ فقط Edge Function در آن
        می‌نویسد و ربات می‌خواند و حذف می‌کند. بدون سوپابیس خالی است.
        """
        if not self.supabase_enabled():
            return []
        rows = self._sb_get_all("webapp_inbox")
        rows.sort(key=lambda r: r.get("created_at") or "")
        return rows

    def delete_inbox_row(self, row_id):
        if not self.supabase_enabled():
            return
        self._sb_delete("webapp_inbox", "id", row_id)

    # ---------- بارگذاری / ذخیره ----------
    def load(self):
        """حافظه را می‌خواند؛ سوپابیس اگر تنظیم شده باشد، وگرنه فایل JSON.
        اگر خواندن سوپابیس خطا بدهد، فایل محلی امتحان می‌شود تا داده گم نشود."""
        if not self.supabase_enabled():
            if os.path.exists(self.state_file):
                with open(self.state_file, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    state = _empty_state()
                    state.update(data)
                    self.state = state
            return self.state
        try:
            state = _empty_state()
            for table, key in _TABLES.items():
                try:
                    rows = self._sb_get_all(table)
                except Exception as ex:
                    # یک جدولِ جاافتاده (مثلاً بعد از اضافه شدن جدول تازه،
                    # قبل از اجرای supabase.sql) نباید کل حافظه را بیندازد.
                    print(f"supabase table {table} skipped:", ex)
                    continue
                if isinstance(state[key], dict):
                    pk = _TABLE_KEYS[table]
                    state[key] = {str(row[pk]): row for row in rows}
                else:
                    state[key] = rows
            # ردیف ناقص کد تخفیف (بدون uses/active) کد را «غیرفعال» نشان
            # می‌دهد؛ پیش‌فرض‌های اسکیما را پر کن.
            for row in state["discount_codes"].values():
                if isinstance(row, dict):
                    row.setdefault("uses", 0)
                    row.setdefault("active", True)
                    row.setdefault("max_uses", 0)
            # شمارنده‌ها از بیشترین مقدار موجود بازسازی می‌شوند
            if state["orders"]:
                nums = [int(no.split("-")[1]) for no in state["orders"]
                        if isinstance(no, str) and no.startswith("GS-")
                        and no.split("-")[1].isdigit()]
                if nums:
                    state["order_seq"] = max(nums)
            for seq_key, coll_key in (("referral_seq", "referrals"),
                                      ("wallet_tx_seq", "wallet_tx"),
                                      ("auto_reply_seq", "auto_replies")):
                ids = [int(r.get("id") or 0) for r in state[coll_key]
                       if str(r.get("id") or "").isdigit()]
                if ids:
                    state[seq_key] = max(ids)
            self.state = state
            self.dirty = False
            print("supabase state loaded")
            return state
        except Exception as ex:
            print("supabase load failed:", ex, "— trying local file")
            if os.path.exists(self.state_file):
                with open(self.state_file, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    state = _empty_state()
                    state.update(data)
                    self.state = state
            return self.state

    def save(self):
        """ذخیره می‌کند: همیشه فایل JSON محلی (پشتیبان)، و اگر سوپابیس
        تنظیم شده باشد، upsert همه‌ی ردیف‌ها در جدول‌های رابطه‌ای."""
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False)
        if self.supabase_enabled():
            for table, key in _TABLES.items():
                val = self.state[key]
                rows = list(val.values()) if isinstance(val, dict) else list(val)
                # ردیف‌های JSONB (details) را به dict نگه می‌داریم؛
                # سوپابیس آن‌ها را به‌صورت jsonb می‌پذیرد.
                try:
                    self._sb_upsert_rows(table, rows)
                except Exception as ex:
                    # خطای یک جدول نباید جدول‌های بعدی را نجات‌ندهَد.
                    print(f"supabase save failed ({table}):", ex,
                          "— local file kept")
        self.dirty = False

    def save_if_dirty(self):
        if self.dirty:
            self.save()

    # ---------- کاربران ----------
    def ensure_user(self, chat_id, username=None, first_name=None):
        key = str(chat_id)
        users = self.state["users"]
        user = users.get(key)
        if user is None:
            user = {"chat_id": int(chat_id), "username": username or "",
                    "first_name": first_name or "", "wallet_balance": "0",
                    "completed_orders": 0, "referral_code": "",
                    "referred_by": "", "referral_discount_used": False,
                    "referral_credit": False, "created_at": _now_iso()}
            users[key] = user
            self.dirty = True
        else:
            if username and user.get("username") != username:
                user["username"] = username
                self.dirty = True
            if first_name and user.get("first_name") != first_name:
                user["first_name"] = first_name
                self.dirty = True
        return user

    def get_user(self, chat_id):
        return self.state["users"].get(str(chat_id))

    def list_users(self):
        return list(self.state["users"].values())

    def wallet_balance(self, chat_id):
        user = self.get_user(chat_id)
        return float(user.get("wallet_balance") or 0) if user else 0.0

    def credit_wallet(self, chat_id, amount, note=""):
        user = self.ensure_user(chat_id)
        new_balance = float(user.get("wallet_balance") or 0) + float(amount)
        user["wallet_balance"] = str(round(new_balance, 2))
        self.state["wallet_tx_seq"] += 1
        self.state["wallet_tx"].append({
            "id": self.state["wallet_tx_seq"], "chat_id": int(chat_id),
            "amount": str(amount), "kind": "credit", "note": note,
            "created_at": _now_iso()})
        self.dirty = True
        return new_balance

    def debit_wallet(self, chat_id, amount, note=""):
        user = self.ensure_user(chat_id)
        balance = float(user.get("wallet_balance") or 0)
        if balance + 1e-9 < float(amount):
            raise ValueError("موجودی کیف پول کافی نیست.")
        new_balance = balance - float(amount)
        user["wallet_balance"] = str(round(new_balance, 2))
        self.state["wallet_tx_seq"] += 1
        self.state["wallet_tx"].append({
            "id": self.state["wallet_tx_seq"], "chat_id": int(chat_id),
            "amount": str(-float(amount)), "kind": "debit", "note": note,
            "created_at": _now_iso()})
        self.dirty = True
        return new_balance

    # ---------- سفارش‌ها ----------
    def next_order_no(self):
        self.state["order_seq"] = int(self.state.get("order_seq") or 1000) + 1
        self.dirty = True
        return f"GS-{self.state['order_seq']}"

    def create_order(self, user_id, order_type, service, details, amount,
                     discount_kind=None, discount_percent=0, discount_amount="0",
                     paid_by_wallet=False, tx_hash="", receipt_file_id="",
                     discount_code=None):
        order_no = self.next_order_no()
        details = dict(details or {})
        if discount_code:
            # کد تخفیفِ به‌کاررفته در جزئیات (jsonb) ثبت می‌شود تا
            # مصرفِ هر کاربر از روی سفارش‌هایش خوانده شود.
            details["discount_code"] = str(discount_code).upper()
        order = {"order_no": order_no, "user_id": int(user_id),
                 "type": order_type, "service": service,
                 "details": details, "amount": str(amount),
                 "discount_kind": discount_kind or "",
                 "discount_percent": int(discount_percent or 0),
                 "discount_amount": str(discount_amount),
                 "paid_by_wallet": bool(paid_by_wallet),
                 "status": "pending", "tx_hash": tx_hash or "",
                 "receipt_file_id": receipt_file_id or "",
                 "created_at": _now_iso()}
        self.state["orders"][order_no] = order
        self.dirty = True
        return order

    def get_order(self, order_no):
        return self.state["orders"].get(str(order_no).upper())

    def list_orders(self, status=None, user_id=None):
        orders = list(self.state["orders"].values())
        if status:
            orders = [o for o in orders if o.get("status") == status]
        if user_id is not None:
            orders = [o for o in orders if o.get("user_id") == int(user_id)]
        orders.sort(key=lambda o: _ts_value(o.get("created_at")))
        return orders

    def list_pending_orders(self):
        return self.list_orders(status="pending")

    def set_order_status(self, order_no, status):
        """تغییر وضعیت فقط از pending به confirmed/rejected مجاز است."""
        order = self.get_order(order_no)
        if order is None:
            raise ValueError("سفارشی با این شماره پیدا نشد.")
        if order.get("status") != "pending":
            raise ValueError("این سفارش قبلاً بررسی شده است.")
        if status not in ("confirmed", "rejected"):
            raise ValueError("وضعیت نامعتبر است.")
        order["status"] = status
        if status == "confirmed":
            # شروع اشتراک از لحظه‌ی تایید است، نه ثبت سفارش.
            order["confirmed_at"] = _now_iso()
        self.dirty = True
        return order

    def set_order_receipt(self, order_no, tx_hash="", receipt_file_id=""):
        """رسید (عکس فیش یا هش تراکنش) را به سفارش در انتظار می‌چسباند."""
        order = self.get_order(order_no)
        if order is None:
            raise ValueError("سفارشی با این شماره پیدا نشد.")
        if tx_hash:
            order["tx_hash"] = tx_hash
        if receipt_file_id:
            order["receipt_file_id"] = receipt_file_id
        self.dirty = True
        return order

    def has_reminder(self, order_no, kind):
        return any(r.get("order_no") == str(order_no)
                   and r.get("kind") == kind
                   for r in self.state["order_reminders"])

    def add_reminder(self, order_no, kind):
        """یادآوری ثبت‌شده (هر سفارش + نوع، فقط یک بار)."""
        if self.has_reminder(order_no, kind):
            return False
        self.state["order_reminders"].append(
            {"order_no": str(order_no), "kind": kind})
        self.dirty = True
        return True

    # ---------- کدهای تخفیف ----------
    def get_code(self, code):
        if not code:
            return None
        return self.state["discount_codes"].get(str(code).upper())

    def list_codes(self):
        return list(self.state["discount_codes"].values())

    def add_code(self, code, percent, max_uses=0):
        code = str(code).upper()
        if not code or not code.isalnum():
            raise ValueError("کد تخفیف باید فقط حروف و عدد باشد.")
        if not (0 < int(percent) <= 100):
            raise ValueError("درصد تخفیف باید بین ۱ تا ۱۰۰ باشد.")
        if code in self.state["discount_codes"]:
            raise ValueError("این کد تخفیف از قبل وجود دارد.")
        row = {"code": code, "percent": int(percent),
               "max_uses": int(max_uses or 0), "uses": 0, "active": True}
        self.state["discount_codes"][code] = row
        self.dirty = True
        return row

    def del_code(self, code):
        code = str(code).upper()
        if code not in self.state["discount_codes"]:
            raise ValueError("این کد تخفیف پیدا نشد.")
        del self.state["discount_codes"][code]
        self.dirty = True

    def use_code(self, code):
        """یک بار مصرف کد را ثبت می‌کند؛ اگر غیرفعال یا تمام‌شده باشد خطا می‌دهد."""
        row = self.get_code(code)
        if row is None or not row.get("active"):
            raise ValueError("کد تخفیف معتبر نیست.")
        if row.get("max_uses") and int(row.get("uses") or 0) >= int(row["max_uses"]):
            raise ValueError("این کد تخفیف دیگر قابل استفاده نیست.")
        row["uses"] = int(row.get("uses") or 0) + 1
        self.dirty = True
        return row

    def has_used_code(self, chat_id, code):
        """آیا این کاربر قبلاً از این کد تخفیف استفاده کرده است؟

        مصرف هر کاربر از روی سفارش‌هایش خوانده می‌شود: سفارشی که با
        همان کد ثبت شده باشد. سفارش‌های پیش از نسخه‌ی ۰٫۸ کد را در
        جزئیاتشان ندارند، پس صاحبشان یک بار دیگر می‌تواند از کد
        استفاده کند.
        """
        code = str(code).upper()
        for order in self.state["orders"].values():
            if int(order.get("user_id") or 0) != int(chat_id):
                continue
            if order.get("discount_kind") != "code":
                continue
            if str((order.get("details") or {}).get("discount_code") or "").upper() == code:
                return True
        return False

    # ---------- معرفی ----------
    def get_or_create_referral_code(self, chat_id):
        from pricing import derive_referral_code, make_referral_code
        user = self.ensure_user(chat_id)
        derived = derive_referral_code(chat_id)
        taken_by_other = any(
            (u.get("referral_code") or "").upper() == derived
            and str(u.get("chat_id")) != str(chat_id)
            for u in self.state["users"].values())
        if taken_by_other:
            # در حالت نادرِ تداخل، کد تصادفی یکتا (وب‌اپ کد مشتق‌شده
            # را نشان می‌دهد؛ برای همین این شاخه تقریباً هرگز نمی‌افتد).
            if user.get("referral_code"):
                return user["referral_code"]
            existing = {u.get("referral_code") for u in self.state["users"].values()
                        if u.get("referral_code")}
            code = make_referral_code(existing)
            user["referral_code"] = code
            self.dirty = True
            return code
        if user.get("referral_code") != derived:
            # کد از روی آیدی ساخته می‌شود تا ربات و وب‌اپ همیشه همان
            # کد را نشان بدهند؛ کد تصادفی قدیمی (اگر بود) عوض می‌شود.
            user["referral_code"] = derived
            self.dirty = True
        return derived

    def record_referral_use(self, code, friend_id):
        """ثبت استفاده‌ی دوست از کد معرفی.
        خروجی: ("ok", referrer_id) | ("self", None) | ("used", None) | ("bad", None)
        - self: خود معرف نمی‌تواند کد خودش را بزند
        - used: هر کد برای هر نفر فقط یک بار قابل استفاده است
        - bad: کد وجود ندارد"""
        code = str(code).upper()
        referrer_id = None
        for u in self.state["users"].values():
            if (u.get("referral_code") or "").upper() == code:
                referrer_id = int(u["chat_id"])
                break
        if referrer_id is None:
            return "bad", None
        if referrer_id == int(friend_id):
            return "self", None
        for r in self.state["referrals"]:
            if (r.get("code") == code and int(r.get("friend_id") or 0) == int(friend_id)):
                return "used", None
        self.state["referral_seq"] += 1
        self.state["referrals"].append({
            "id": self.state["referral_seq"], "code": code,
            "referrer_id": referrer_id, "friend_id": int(friend_id),
            "used": True, "activated": False,
            "created_at": _now_iso()})
        user = self.ensure_user(friend_id)
        user["referred_by"] = code
        self.dirty = True
        return "ok", referrer_id

    def activate_referral_for_friend(self, friend_id):
        """وقتی دوست سفارش می‌دهد: ۱۰٪ معرف فعال می‌شود.
        خروجی referrer_id یا None (اگر معرفی فعالی برای این دوست نباشد)."""
        for r in self.state["referrals"]:
            if (int(r.get("friend_id") or 0) == int(friend_id)
                    and not r.get("activated")):
                r["activated"] = True
                referrer = self.get_user(r["referrer_id"])
                if referrer is not None:
                    referrer["referral_credit"] = True
                self.dirty = True
                return int(r["referrer_id"])
        return None

    def list_referrals(self, referrer_id):
        return [r for r in self.state["referrals"]
                if int(r.get("referrer_id") or 0) == int(referrer_id)]

    # ---------- پاسخ‌های خودکار ----------
    def list_auto_replies(self):
        return list(self.state["auto_replies"])

    def find_auto_reply(self, text):
        """تطابق زیررشته‌ای: اگر سؤال مشتری شامل سؤال ذخیره‌شده باشد."""
        if not text:
            return None
        norm = text.strip()
        for row in self.state["auto_replies"]:
            q = (row.get("question") or "").strip()
            if q and q in norm:
                return row
        return None

    def add_auto_reply(self, question, answer):
        question = (question or "").strip()
        answer = (answer or "").strip()
        if not question or not answer:
            raise ValueError("سؤال و جواب هر دو لازم‌اند.")
        for row in self.state["auto_replies"]:
            if row.get("question") == question:
                raise ValueError("این سؤال از قبل ثبت شده است.")
        self.state["auto_reply_seq"] += 1
        row = {"id": self.state["auto_reply_seq"],
               "question": question, "answer": answer}
        self.state["auto_replies"].append(row)
        self.dirty = True
        return row

    def del_auto_reply(self, index):
        rows = self.state["auto_replies"]
        if not (1 <= int(index) <= len(rows)):
            raise ValueError("شماره‌ی پاسخ خودکار نامعتبر است.")
        del rows[int(index) - 1]
        self.dirty = True

    # ---------- پشتیبانی ----------
    def set_support_mode(self, chat_id, on):
        if on:
            self.state["support_mode"][str(chat_id)] = True
        else:
            self.state["support_mode"].pop(str(chat_id), None)
        self.dirty = True

    def in_support_mode(self, chat_id):
        return bool(self.state["support_mode"].get(str(chat_id)))

    def map_support_message(self, admin_message_id, customer_chat_id):
        self.state["support_threads"][str(admin_message_id)] = {
            "admin_message_id": int(admin_message_id),
            "customer_chat_id": int(customer_chat_id),
            "created_at": _now_iso()}
        self.dirty = True

    def support_customer_for(self, admin_message_id):
        row = self.state["support_threads"].get(str(admin_message_id))
        return int(row["customer_chat_id"]) if row else None

    # ---------- آمار ----------
    def stats(self):
        orders = list(self.state["orders"].values())
        confirmed = [o for o in orders if o.get("status") == "confirmed"]
        revenue = sum(float(o.get("amount") or 0) for o in confirmed
                      if o.get("type") != "wallet_charge")
        return {"users": len(self.state["users"]),
                "orders_total": len(orders),
                "orders_pending": len([o for o in orders if o.get("status") == "pending"]),
                "orders_confirmed": len(confirmed),
                "orders_rejected": len([o for o in orders if o.get("status") == "rejected"]),
                "revenue_usdt": round(revenue, 2)}
