# تست‌های «گاد استار» — فقط stdlib + requests؛ هیچ فراخوانی واقعی
# به تلگرام نمی‌شود (همه‌ی فراخوانی‌های تلگرام monkeypatch می‌شوند).
import os
import sys
import tempfile
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pricing
from pricing import (ads_price, member_price, bot_price, apply_discount,
                     compute_order_price, choose_discount, loyalty_eligible,
                     is_valid_channel_id)
from db import Store
import bot as godstar


class PricingTest(unittest.TestCase):
    def test_ads_pricing(self):
        self.assertEqual(ads_price(10_000, "none"), Decimal("5"))
        self.assertEqual(ads_price(10_000, "photo"), Decimal("6"))
        self.assertEqual(ads_price(10_000, "video"), Decimal("7"))
        self.assertEqual(ads_price(100_000, "photo"), Decimal("60"))
        self.assertEqual(ads_price(1_000_000, "video"), Decimal("700"))

    def test_ads_boundaries_rejected(self):
        for bad in (9_999, 1_000_001, 15_000, 0, -10_000):
            with self.assertRaises(ValueError, msg=str(bad)):
                ads_price(bad, "none")
        with self.assertRaises(ValueError):
            ads_price(10_000, "audio")

    def test_member_pricing(self):
        self.assertEqual(member_price(1_000), Decimal("3"))
        self.assertEqual(member_price(20_000), Decimal("60"))
        self.assertEqual(member_price(5_000), Decimal("15"))

    def test_member_boundaries_rejected(self):
        for bad in (999, 20_001, 1_500, 0):
            with self.assertRaises(ValueError, msg=str(bad)):
                member_price(bad)

    def test_bot_plans(self):
        self.assertEqual(bot_price("1m"), Decimal("10"))
        self.assertEqual(bot_price("3m"), Decimal("28"))
        self.assertEqual(bot_price("6m"), Decimal("54"))
        self.assertEqual(bot_price("12m"), Decimal("100"))
        with self.assertRaises(ValueError):
            bot_price("2m")

    def test_discount_god10(self):
        # مثال مشخصات: 15 با 10٪ می‌شود 13.5؛ و 5 می‌شود 4.5
        self.assertEqual(apply_discount(Decimal("15"), 10),
                         (Decimal("13.50"), Decimal("1.50")))
        self.assertEqual(apply_discount(Decimal("5"), 10),
                         (Decimal("4.50"), Decimal("0.50")))

    def test_choose_discount_priority_and_largest(self):
        # فقط یک تخفیف؛ تساوی 10٪ → اولویت وفاداری > معرفی > کد
        self.assertEqual(choose_discount(loyalty=True, referral=True,
                                         code_percent=10), ("loyalty", 10))
        self.assertEqual(choose_discount(referral=True, code_percent=10),
                         ("referral", 10))
        self.assertEqual(choose_discount(code_percent=10), ("code", 10))
        # درصد بزرگ‌تر می‌برد، حتی اگر اولویتش کمتر باشد
        self.assertEqual(choose_discount(loyalty=True, code_percent=20),
                         ("code", 20))
        self.assertEqual(choose_discount(), (None, 0))

    def test_compute_order_price(self):
        final, disc, kind, pct = compute_order_price(
            Decimal("15"), code_percent=10)
        self.assertEqual((final, disc, kind, pct),
                         (Decimal("13.50"), Decimal("1.50"), "code", 10))
        final, disc, kind, pct = compute_order_price(Decimal("100"))
        self.assertEqual((final, disc, kind), (Decimal("100.00"), Decimal("0"), None))

    def test_loyalty(self):
        self.assertFalse(loyalty_eligible(2))
        self.assertTrue(loyalty_eligible(3))
        self.assertTrue(loyalty_eligible(10))

    def test_channel_id(self):
        self.assertTrue(is_valid_channel_id("@GodNews24"))
        self.assertTrue(is_valid_channel_id("@abc12"))
        self.assertFalse(is_valid_channel_id("GodNews24"))
        self.assertFalse(is_valid_channel_id("@ab"))
        self.assertFalse(is_valid_channel_id("@bad-id"))

    def test_derive_referral_code_is_stable_and_valid(self):
        # کد از روی آیدی ساخته می‌شود؛ برای همان کاربر همیشه همان کد،
        # با فرمت GOD + شش رقم، و برای کاربر دیگر کد دیگر.
        code = pricing.derive_referral_code(278899906)
        self.assertEqual(code, pricing.derive_referral_code(278899906))
        self.assertEqual(code, pricing.derive_referral_code("278899906"))
        self.assertTrue(code.startswith("GOD") and len(code) == 9)
        self.assertTrue(pricing.is_valid_referral_code(code))
        other = pricing.derive_referral_code(111)
        self.assertNotEqual(code, other)
        self.assertTrue(pricing.is_valid_referral_code(other))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(state_file=os.path.join(self.tmp.name, "state.json"))
        self.store.load()

    def tearDown(self):
        self.tmp.cleanup()

    def test_order_numbers_sequential(self):
        o1 = self.store.create_order(111, "order", "members", {"count": 1000}, "3")
        o2 = self.store.create_order(111, "order", "ads", {"views": 10000}, "5")
        self.assertEqual(o1["order_no"], "GS-1001")
        self.assertEqual(o2["order_no"], "GS-1002")
        self.assertEqual(o1["status"], "pending")

    def test_order_state_transitions(self):
        o = self.store.create_order(111, "order", "members", {}, "3")
        self.store.set_order_status(o["order_no"], "confirmed")
        self.assertEqual(self.store.get_order(o["order_no"])["status"], "confirmed")
        # تغییر دوباره‌ی وضعیت رد می‌شود
        with self.assertRaises(ValueError):
            self.store.set_order_status(o["order_no"], "rejected")
        with self.assertRaises(ValueError):
            self.store.set_order_status(o["order_no"], "pending")
        o2 = self.store.create_order(111, "order", "members", {}, "3")
        with self.assertRaises(ValueError):
            self.store.set_order_status(o2["order_no"], "shipped")
        with self.assertRaises(ValueError):
            self.store.set_order_status("GS-9999", "confirmed")

    def test_discount_codes(self):
        row = self.store.add_code("GOD10", 10, 0)
        self.assertEqual(row["percent"], 10)
        self.store.use_code("god10")  # حروف کوچک هم قبول است
        self.assertEqual(self.store.get_code("GOD10")["uses"], 1)
        with self.assertRaises(ValueError):
            self.store.add_code("GOD10", 20)
        limited = self.store.add_code("ONE", 50, 1)
        self.store.use_code("ONE")
        with self.assertRaises(ValueError):
            self.store.use_code("ONE")
        with self.assertRaises(ValueError):
            self.store.use_code("NOPE")

    def test_wallet_credit_debit(self):
        self.assertEqual(self.store.wallet_balance(111), 0.0)
        self.store.credit_wallet(111, 20, note="test")
        self.assertEqual(self.store.wallet_balance(111), 20.0)
        self.store.debit_wallet(111, 5, note="test")
        self.assertEqual(self.store.wallet_balance(111), 15.0)
        with self.assertRaises(ValueError):
            self.store.debit_wallet(111, 100)

    def test_referral_rules(self):
        referrer = self.store.ensure_user(100, "ref", "Ref")
        code = self.store.get_or_create_referral_code(100)
        # کد مشتق‌شده از آیدی: GOD + شش رقم، و برای همان کاربر پایدار
        self.assertEqual(code, pricing.derive_referral_code(100))
        self.assertTrue(code.startswith("GOD") and len(code) == 9)
        self.assertEqual(code, self.store.get_or_create_referral_code(100))
        # کد تصادفی قدیمی به کد مشتق‌شده مهاجرت می‌کند
        self.store.get_user(100)["referral_code"] = "GOD4821"
        self.assertEqual(self.store.get_or_create_referral_code(100), code)
        # خودارجاعی رد می‌شود
        status, _ = self.store.record_referral_use(code, 100)
        self.assertEqual(status, "self")
        # استفاده‌ی اول دوست قبول است
        status, ref = self.store.record_referral_use(code, 200)
        self.assertEqual((status, ref), ("ok", 100))
        # همان کد برای همان نفر فقط یک بار
        status, _ = self.store.record_referral_use(code, 200)
        self.assertEqual(status, "used")
        # کد ناموجود
        status, _ = self.store.record_referral_use("GOD0000", 300)
        self.assertEqual(status, "bad")
        # فعال‌سازی فقط بعد از سفارش دوست
        self.assertIsNone(self.store.activate_referral_for_friend(999))
        self.assertEqual(self.store.activate_referral_for_friend(200), 100)
        self.assertTrue(self.store.get_user(100)["referral_credit"])
        # فعال‌سازی دوم همان دوست چیزی برنمی‌گرداند
        self.assertIsNone(self.store.activate_referral_for_friend(200))

    def test_referral_code_collision_falls_back_to_random(self):
        # اگر کد مشتق‌شده‌ی کاربر را کاربر دیگری گرفته باشد، کد تصادفی
        # یکتا ساخته می‌شود (نه کدِ آن کاربر دیگر).
        derived = pricing.derive_referral_code(100)
        other = self.store.ensure_user(200, "other", "Other")
        other["referral_code"] = derived
        code = self.store.get_or_create_referral_code(100)
        self.assertNotEqual(code, derived)
        self.assertTrue(code.startswith("GOD"))
        self.assertTrue(pricing.is_valid_referral_code(code))

    def test_auto_replies(self):
        self.store.add_auto_reply("سفارشم کی آماده می‌شه؟", "تا ۲۴ ساعت.")
        row = self.store.find_auto_reply("سلام، سفارشم کی آماده می‌شه؟ بگو")
        self.assertIsNotNone(row)
        self.assertEqual(row["answer"], "تا ۲۴ ساعت.")
        self.assertIsNone(self.store.find_auto_reply("سلام چطوری؟"))

    def test_json_persistence(self):
        self.store.create_order(111, "order", "members", {}, "3")
        self.store.save()
        other = Store(state_file=self.store.state_file)
        other.load()
        self.assertEqual(other.get_order("GS-1001")["status"], "pending")

    def test_supabase_headers_split_keys(self):
        # سیستم کلید تازه‌ی سوپابیس: apikey = کلید عمومی، Bearer = کلید محرمانه
        store = Store(state_file=os.path.join(self.tmp.name, "s2.json"),
                      supabase_url="https://example.supabase.co",
                      supabase_key="sb_secret_X",
                      supabase_publishable_key="sb_publishable_Y")
        headers = store._sb_headers()
        self.assertEqual(headers["apikey"], "sb_publishable_Y")
        self.assertEqual(headers["Authorization"], "Bearer sb_secret_X")

    def test_supabase_headers_fallback_without_publishable_key(self):
        # اگر کلید عمومی تنظیم نشده باشد، مثل قبل از کلید سرویس برای هر دو
        # استفاده می‌شود (سازگاری با نسخه‌های قدیمی).
        store = Store(state_file=os.path.join(self.tmp.name, "s3.json"),
                      supabase_url="https://example.supabase.co",
                      supabase_key="sb_secret_X")
        headers = store._sb_headers()
        self.assertEqual(headers["apikey"], "sb_secret_X")
        self.assertEqual(headers["Authorization"], "Bearer sb_secret_X")

    def test_upsert_sends_on_conflict(self):
        # بدون on_conflict سوپابیس upsert را ۴۰۰ می‌کند (علت خطای ذخیره
        # در اجرای ۳۵). هر جدول ستون کلید خودش را می‌فرستد.
        import db as _db
        store = Store(state_file=os.path.join(self.tmp.name, "s4.json"),
                      supabase_url="https://example.supabase.co",
                      supabase_key="k")
        calls = []

        class _Resp:
            status_code = 201
            ok = True
            text = ""

        orig_post = _db.requests.post
        _db.requests.post = lambda url, **kw: calls.append(url) or _Resp()
        try:
            store._sb_upsert_rows("users", [{"chat_id": 1, "username": "u"}])
            store._sb_upsert_rows("order_reminders",
                                  [{"order_no": "GS-1001", "kind": "renewal"}])
            store._sb_upsert_rows("discount_codes",
                                  [{"code": "GOD10", "percent": 10}])
        finally:
            _db.requests.post = orig_post
        self.assertIn("on_conflict=chat_id", calls[0])
        self.assertIn("on_conflict=order_no,kind", calls[1])
        self.assertIn("on_conflict=code", calls[2])

    def test_upsert_normalizes_row_keys(self):
        # رگرسیون (v1.0.4): ردیف خوانده‌شده از دیتابیس ستون confirmed_at
        # را دارد (null) ولی سفارش تازه‌ساخته آن را ندارد؛ upsert دسته‌ای
        # با کلیدهای ناهمگون را PostgREST با PGRST102 رد می‌کند و کل
        # جدول orders ذخیره نمی‌شد (GS-1002 هرگز در دیتابیس نوشته نشد).
        import db as _db
        store = Store(state_file=os.path.join(self.tmp.name, "s6.json"),
                      supabase_url="https://example.supabase.co",
                      supabase_key="k")
        sent = []

        class _Resp:
            status_code = 201
            ok = True
            text = ""

        orig_post = _db.requests.post
        _db.requests.post = lambda url, **kw: sent.append(kw.get("json")) or _Resp()
        try:
            store._sb_upsert_rows("orders", [
                {"order_no": "GS-1001", "status": "rejected",
                 "confirmed_at": None},
                {"order_no": "GS-1002", "status": "pending"},
            ])
        finally:
            _db.requests.post = orig_post
        self.assertEqual(len(sent), 1)
        rows = sent[0]
        self.assertEqual(len(rows), 2)
        self.assertEqual(set(rows[0]), set(rows[1]))
        self.assertIsNone(rows[1]["confirmed_at"])
        self.assertEqual(rows[0]["order_no"], "GS-1001")

    def test_upsert_error_includes_response_body(self):
        # علت دقیق سوپابیس (مثلاً ستون ناشناخته) باید در لاگ دیده شود،
        # نه فقط «400 Bad Request».
        import db as _db
        store = Store(state_file=os.path.join(self.tmp.name, "s5.json"),
                      supabase_url="https://example.supabase.co",
                      supabase_key="k")

        class _Resp:
            status_code = 400
            ok = False
            text = '{"message": "Could not find the \'x\' column"}'

        orig_post = _db.requests.post
        _db.requests.post = lambda url, **kw: _Resp()
        try:
            with self.assertRaises(RuntimeError) as ctx:
                store._sb_upsert_rows("users", [{"chat_id": 1}])
        finally:
            _db.requests.post = orig_post
        self.assertIn("Could not find", str(ctx.exception))

    def test_created_at_is_iso_string(self):
        # timestamptz عدد خام قبول نمی‌کند؛ هر چهار جایی که int می‌گذاشت
        # حالا ISO می‌گذارد (علت پنهان ۴۰۰های بعدی).
        self.store.credit_wallet(111, 7, note="test")
        tx = self.store.state["wallet_tx"][0]
        self.assertIsInstance(tx["created_at"], str)
        self.assertIn("T", tx["created_at"])
        self.store.map_support_message(55, 111)
        thread = self.store.state["support_threads"]["55"]
        self.assertIsInstance(thread["created_at"], str)
        user = self.store.ensure_user(111)
        user["referral_code"] = "GOD4821"
        status, _ = self.store.record_referral_use("GOD4821", 222)
        self.assertEqual(status, "ok")
        self.assertIsInstance(
            self.store.state["referrals"][0]["created_at"], str)


class BotOrderTest(unittest.TestCase):
    """تست ثبت سفارش و وب‌اپ؛ فراخوانی‌های تلگرام کاملاً جایگزین می‌شوند."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(state_file=os.path.join(self.tmp.name, "state.json"))
        self.store.load()
        godstar.ensure_defaults(self.store)
        self.sent = []
        self._orig_send = godstar.send_message
        self._orig_api = godstar.api
        godstar.send_message = lambda chat_id, text, reply_markup=None: self.sent.append(
            (chat_id, text, reply_markup)) or {"message_id": 1}
        godstar.api = lambda method, **data: {"message_id": 99}
        # ادمین در تست‌ها: آیدی واقعی ادمین، فقط برای روت پیام‌ها
        self._orig_admin = godstar.ADMIN_CHAT_ID
        godstar.ADMIN_CHAT_ID = "278899906"

    def tearDown(self):
        godstar.send_message = self._orig_send
        godstar.api = self._orig_api
        godstar.ADMIN_CHAT_ID = self._orig_admin
        self.tmp.cleanup()

    def _webapp_msg(self, chat_id, payload):
        import json as _json
        return {"chat": {"id": chat_id}, "from": {"id": chat_id, "username": "u"},
                "web_app_data": {"data": _json.dumps(payload, ensure_ascii=False)}}

    def test_webapp_order_ok_with_god10(self):
        self.store.add_code("GOD10", 10, 0)
        msg = self._webapp_msg(111, {"type": "order", "service": "members",
                                     "details": {"count": 5000, "channel_id": "@GodNews24"},
                                     "discount_code": "GOD10",
                                     "tx_hash": "abc123"})
        godstar.handle_web_app_data(self.store, msg)
        order = self.store.get_order("GS-1001")
        self.assertIsNotNone(order)
        self.assertEqual(order["amount"], "13.50")  # 15 با 10٪
        self.assertEqual(order["discount_kind"], "code")
        self.assertEqual(self.store.get_code("GOD10")["uses"], 1)
        self.assertTrue(any("سفارش شما ثبت شد" in t for _, t, _ in self.sent))

    def test_webapp_bad_price_rejected(self):
        # مشتری قیمت 1 تتر فرستاده؛ سرور دوباره حساب می‌کند (باید 15 شود)
        # و چون رسید هم ندارد، رد می‌شود. مهم: قیمت مشتری نادیده گرفته می‌شود.
        msg = self._webapp_msg(111, {"type": "order", "service": "members",
                                     "details": {"count": 5000, "channel_id": "@GodNews24",
                                                 "price": 1},
                                     "tx_hash": "abc123"})
        godstar.handle_web_app_data(self.store, msg)
        order = self.store.get_order("GS-1001")
        self.assertEqual(order["amount"], "15.00")

    def test_webapp_missing_receipt_rejected(self):
        msg = self._webapp_msg(111, {"type": "order", "service": "ads",
                                     "details": {"views": 10000, "media": "photo",
                                                 "channel_id": "@GodNews24",
                                                 "ad_text": "سلام"}})
        godstar.handle_web_app_data(self.store, msg)
        self.assertIsNone(self.store.get_order("GS-1001"))
        self.assertTrue(any("رسید" in t for _, t, _ in self.sent))

    def test_webapp_missing_channel_rejected(self):
        msg = self._webapp_msg(111, {"type": "order", "service": "members",
                                     "details": {"count": 1000},
                                     "tx_hash": "abc123"})
        godstar.handle_web_app_data(self.store, msg)
        self.assertIsNone(self.store.get_order("GS-1001"))
        self.assertTrue(any("آیدی کانال" in t for _, t, _ in self.sent))

    def test_webapp_bad_views_rejected(self):
        msg = self._webapp_msg(111, {"type": "order", "service": "ads",
                                     "details": {"views": 15000, "media": "none",
                                                 "channel_id": "@GodNews24",
                                                 "ad_text": "x"},
                                     "tx_hash": "abc"})
        godstar.handle_web_app_data(self.store, msg)
        self.assertIsNone(self.store.get_order("GS-1001"))

    # ---- پی‌لود واقعی وب‌اپ (تخت، بدون details) — رگرسیون یکپارچگی ----
    def test_webapp_flat_members_photo_receipt_ok(self):
        # دقیقاً همان چیزی که app.js می‌فرستد: کلیدهای تخت + رسید عکس
        msg = self._webapp_msg(111, {"type": "order", "service": "members",
                                     "title": "خرید ممبر آپلودری",
                                     "channel_id": "@GodNews24",
                                     "quantity": 5000,
                                     "amount_usdt": 15, "payable_usdt": 15,
                                     "pay_from_wallet": False,
                                     "receipt_kind": "photo",
                                     "receipt_file_name": "fish.jpg",
                                     "receipt_file_size": 12345})
        godstar.handle_web_app_data(self.store, msg)
        order = self.store.get_order("GS-1001")
        self.assertIsNotNone(order)
        self.assertEqual(order["amount"], "15.00")
        self.assertEqual(order["details"]["count"], 5000)
        self.assertTrue(any("عکس فیش" in t for _, t, _ in self.sent))

    def test_webapp_flat_ads_hash_receipt_ok(self):
        msg = self._webapp_msg(111, {"type": "order", "service": "ads",
                                     "channel_id": "@GodNews24",
                                     "views": 50000, "media": "photo",
                                     "ad_text": "سلام",
                                     "amount_usdt": 30, "payable_usdt": 30,
                                     "pay_from_wallet": False,
                                     "receipt_kind": "hash",
                                     "receipt_hash": "0xabc123"})
        godstar.handle_web_app_data(self.store, msg)
        order = self.store.get_order("GS-1001")
        self.assertIsNotNone(order)
        self.assertEqual(order["amount"], "30.00")  # 50k با عکس: 6×5
        self.assertEqual(order["tx_hash"], "0xabc123")

    def test_webapp_flat_bot_plan_months_ok(self):
        msg = self._webapp_msg(111, {"type": "order", "service": "bot",
                                     "channel_id": "@GodNews24",
                                     "bot_type": "management",
                                     "plan_months": 3,
                                     "amount_usdt": 28, "payable_usdt": 28,
                                     "pay_from_wallet": False,
                                     "receipt_kind": "hash",
                                     "receipt_hash": "0xdef456"})
        godstar.handle_web_app_data(self.store, msg)
        order = self.store.get_order("GS-1001")
        self.assertIsNotNone(order)
        self.assertEqual(order["amount"], "28.00")
        self.assertEqual(order["details"]["plan"], "3m")

    def test_webapp_flat_wallet_charge_ok(self):
        msg = self._webapp_msg(111, {"type": "wallet_charge",
                                     "amount_usdt": 20,
                                     "receipt_kind": "hash",
                                     "receipt_hash": "0x789"})
        godstar.handle_web_app_data(self.store, msg)
        order = self.store.get_order("GS-1001")
        self.assertIsNotNone(order)
        self.assertEqual(order["type"], "wallet_charge")
        self.assertEqual(order["amount"], "20.00")

    def test_webapp_flat_no_receipt_still_rejected(self):
        msg = self._webapp_msg(111, {"type": "order", "service": "members",
                                     "channel_id": "@GodNews24",
                                     "quantity": 1000,
                                     "receipt_kind": None})
        godstar.handle_web_app_data(self.store, msg)
        self.assertIsNone(self.store.get_order("GS-1001"))
        self.assertTrue(any("رسید" in t for _, t, _ in self.sent))

    def test_webapp_flat_support_photo_flag(self):
        msg = self._webapp_msg(111, {"type": "support_message",
                                     "to": "support",
                                     "text": "سلام، خطا می‌گیرم",
                                     "has_photo": True,
                                     "photo_file_name": "err.png"})
        godstar.handle_web_app_data(self.store, msg)
        # پیام به ادمین می‌رسد و یادداشت «عکس هم دارد» را دارد
        self.assertTrue(any("عکس هم دارد" in t for _, t, _ in self.sent))

    def test_webapp_support_photo_asks_user_to_send_photo_in_chat(self):
        # رگرسیون (v1.0.3): وقتی عکس فقط در وب‌اپ انتخاب شده، تاییدیه‌ی
        # کاربر باید بگوید عکس را در گفتگو با ربات بفرستد تا ادمین ببیند.
        msg = self._webapp_msg(111, {"type": "support_message",
                                     "to": "support",
                                     "text": "سلام، خطا می‌گیرم",
                                     "has_photo": True,
                                     "photo_file_name": "err.png"})
        godstar.handle_web_app_data(self.store, msg)
        user_texts = [t for cid, t, _ in self.sent if cid == 111]
        self.assertTrue(any("عکست را همین‌جا" in t for t in user_texts))

    def test_webapp_support_photo_data_reaches_admin(self):
        # رگرسیون (v1.0.4): عکس پشتیبانی که از مینی‌اپ کوچک‌شده می‌رسد،
        # با بایت واقعی به ادمین فوروارد می‌شود، نه فقط با یادداشت متنی.
        import base64 as _b64
        calls = []
        orig = godstar.send_photo_bytes
        godstar.send_photo_bytes = lambda chat_id, photo_bytes, caption=None, reply_markup=None: calls.append(
            (chat_id, photo_bytes, caption)) or {"message_id": 55}
        try:
            raw = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
            data_url = "data:image/jpeg;base64," + _b64.b64encode(raw).decode()
            msg = self._webapp_msg(111, {"type": "support_message",
                                         "to": "support",
                                         "text": "سلام، خطا می‌گیرم",
                                         "has_photo": True,
                                         "photo_file_name": "err.jpg",
                                         "photo_data": data_url})
            godstar.handle_web_app_data(self.store, msg)
        finally:
            godstar.send_photo_bytes = orig
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], "278899906")
        self.assertEqual(calls[0][1], raw)
        self.assertIn("سلام، خطا می‌گیرم", calls[0][2])
        # (v1.0.5) تاییدیه هم خود عکس را در چت مشتری نشان می‌دهد
        self.assertEqual(calls[1][0], 111)
        self.assertEqual(calls[1][1], raw)
        self.assertIn("عکسی که به پشتیبانی فرستادی", calls[1][2])
        # چون عکس واقعاً رسید، تاییدیه نباید بگوید عکس را در چت بفرست
        user_texts = [t for cid, t, _ in self.sent if cid == 111]
        self.assertFalse(any("عکست را همین‌جا" in t for t in user_texts))

    def test_webapp_support_photo_data_invalid_falls_back(self):
        # photo_data خراب → رفتار قبلی: یادداشت «عکس هم دارد» + درخواست
        # فرستادن عکس در چت؛ چیزی خراب نمی‌شود.
        msg = self._webapp_msg(111, {"type": "support_message",
                                     "to": "support",
                                     "text": "سلام",
                                     "has_photo": True,
                                     "photo_file_name": "err.jpg",
                                     "photo_data": "not-a-data-url!!!"})
        godstar.handle_web_app_data(self.store, msg)
        self.assertTrue(any("عکس هم دارد" in t for _, t, _ in self.sent))
        user_texts = [t for cid, t, _ in self.sent if cid == 111]
        self.assertTrue(any("عکست را همین‌جا" in t for t in user_texts))

    def test_loyalty_discount_auto(self):
        user = self.store.ensure_user(111)
        user["completed_orders"] = 3
        order = godstar.place_order(self.store, 111, "members",
                                    {"count": 5000, "channel_id": "@GodNews24"},
                                    tx_hash="abc")
        self.assertEqual(order["amount"], "13.50")
        self.assertEqual(order["discount_kind"], "loyalty")

    def test_referral_flow_end_to_end(self):
        referrer = self.store.ensure_user(100, "ref", "Ref")
        code = self.store.get_or_create_referral_code(100)
        # دوست با /start کد را می‌زند
        msg = {"chat": {"id": 200}, "from": {"id": 200, "username": "friend"},
               "text": f"/start {code}"}
        godstar.handle_message(self.store, msg)
        self.assertEqual(self.store.get_user(200)["referred_by"], code)
        # دوست اولین سفارش را می‌دهد → 10٪ خودش؛ 10٪ معرف هنوز فعال نیست
        order = godstar.place_order(self.store, 200, "members",
                                    {"count": 5000, "channel_id": "@GodNews24"},
                                    tx_hash="abc")
        self.assertEqual(order["amount"], "13.50")
        self.assertEqual(order["discount_kind"], "referral")
        self.assertFalse(self.store.get_user(100).get("referral_credit"))
        # سفارش دوم دوست دیگر تخفیف معرفی ندارد (اولی در انتظار رزروش کرده)
        order2 = godstar.place_order(self.store, 200, "members",
                                     {"count": 1000, "channel_id": "@GodNews24"},
                                     tx_hash="abc")
        self.assertEqual(order2["amount"], "3.00")
        self.assertEqual(order2["discount_kind"], "")
        # با تایید سفارش اول، 10٪ معرف فعال می‌شود
        godstar.confirm_order(self.store, order["order_no"])
        self.assertTrue(self.store.get_user(100)["referral_credit"])
        self.assertTrue(self.store.get_user(200)["referral_discount_used"])

    def test_discount_code_once_per_user(self):
        # هر نفر از هر کد تخفیف فقط یک بار می‌تواند استفاده کند
        self.store.add_code("GOD10", 10, 0)
        order = godstar.place_order(
            self.store, 111, "members",
            {"count": 1000, "channel_id": "@GodNews24"},
            discount_code="GOD10", tx_hash="abc")
        self.assertEqual(order["discount_kind"], "code")
        self.assertTrue(self.store.has_used_code(111, "GOD10"))
        with self.assertRaises(ValueError) as ctx:
            godstar.place_order(
                self.store, 111, "members",
                {"count": 1000, "channel_id": "@GodNews24"},
                discount_code="GOD10", tx_hash="abc")
        self.assertIn("قبلاً", str(ctx.exception))
        # نفر دیگر هنوز می‌تواند از همان کد استفاده کند
        order2 = godstar.place_order(
            self.store, 222, "members",
            {"count": 1000, "channel_id": "@GodNews24"},
            discount_code="GOD10", tx_hash="abc")
        self.assertEqual(order2["discount_kind"], "code")
        self.assertEqual(self.store.get_code("GOD10")["uses"], 2)

    def test_confirm_wallet_charge_credits_balance(self):
        order = godstar.place_order(self.store, 111, "wallet_charge",
                                    {"amount": 50}, tx_hash="abc")
        self.assertEqual(self.store.wallet_balance(111), 0.0)
        godstar.confirm_order(self.store, order["order_no"])
        self.assertEqual(self.store.wallet_balance(111), 50.0)
        self.assertEqual(self.store.get_order(order["order_no"])["status"], "confirmed")

    def test_reject_refunds_wallet_payment(self):
        self.store.credit_wallet(111, 20)
        order = godstar.place_order(self.store, 111, "members",
                                    {"count": 1000, "channel_id": "@GodNews24"},
                                    paid_by_wallet=True)
        self.assertEqual(self.store.wallet_balance(111), 17.0)
        godstar.reject_order(self.store, order["order_no"])
        self.assertEqual(self.store.wallet_balance(111), 20.0)

    def test_pay_from_wallet_insufficient(self):
        with self.assertRaises(ValueError):
            godstar.place_order(self.store, 111, "members",
                                {"count": 1000, "channel_id": "@GodNews24"},
                                paid_by_wallet=True)

    def test_custom_bot_zero_price(self):
        order = godstar.place_order(self.store, 111, "bot",
                                    {"bot_type": "custom",
                                     "channel_id": "@GodNews24",
                                     "description": "ربات فروشگاه می‌خواهم"},
                                    tx_hash="abc")
        self.assertEqual(order["amount"], "0.00")

    def test_admin_commands_rejected_for_non_admin(self):
        # ADMIN_CHAT_ID در تست تنظیم نشده؛ پس هیچ‌کس ادمین نیست
        msg = {"chat": {"id": 111}, "from": {"id": 111}, "text": "/orders"}
        godstar.handle_message(self.store, msg)
        self.assertTrue(any("فقط برای ادمین" in t for _, t, _ in self.sent))

    def test_support_auto_reply(self):
        godstar.ensure_defaults(self.store)
        msg = {"chat": {"id": 111}, "from": {"id": 111},
               "text": "💬 چت با پشتیبانی"}
        godstar.handle_message(self.store, msg)
        msg2 = {"chat": {"id": 111}, "from": {"id": 111},
                "text": "سلام، سفارشم کی آماده می‌شه؟"}
        godstar.handle_message(self.store, msg2)
        self.assertTrue(any("۲۴ ساعت" in t for _, t, _ in self.sent))


class MenuAndAdminTest(unittest.TestCase):
    """دکمه‌ی منوی مینی‌اپ، نشان ادمین، جواب ادمین با دکمه‌ی فروشگاه،
    و داده‌ی واقعی سفارش/کیف پول در وب‌اپ."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(state_file=os.path.join(self.tmp.name, "state.json"))
        self.store.load()
        godstar.ensure_defaults(self.store)
        self.sent = []
        self.api_calls = []
        self._orig_send = godstar.send_message
        self._orig_api = godstar.api
        self._orig_admin = godstar.ADMIN_CHAT_ID
        self._orig_webapp = godstar.WEBAPP_URL
        godstar.send_message = lambda chat_id, text, reply_markup=None: self.sent.append(
            (chat_id, text, reply_markup)) or {"message_id": 1}
        godstar.api = lambda method, **data: self.api_calls.append(
            (method, data)) or {"message_id": 99}
        godstar.ADMIN_CHAT_ID = "278899906"
        godstar.WEBAPP_URL = "https://example.invalid/app"

    def tearDown(self):
        godstar.send_message = self._orig_send
        godstar.api = self._orig_api
        godstar.ADMIN_CHAT_ID = self._orig_admin
        godstar.WEBAPP_URL = self._orig_webapp
        self.tmp.cleanup()

    def test_welcome_admin_badge_and_version_only_for_admin(self):
        admin_text = godstar.welcome_text(True)
        self.assertIn("👑", admin_text)
        self.assertIn(godstar.BOT_VERSION, admin_text)
        user_text = godstar.welcome_text(False)
        self.assertNotIn("👑", user_text)
        self.assertNotIn(godstar.BOT_VERSION, user_text)

    def test_bot_username_is_shop_bot(self):
        # لینک معرفی و راهنمای رسید باید به ربات اصلی (@GodStarShopBot)
        # برود، نه ربات بازنشسته (@GodStarServiceBot)
        self.assertEqual(godstar.BOT_USERNAME, "GodStarShopBot")

    def test_ensure_defaults_does_not_resurrect_deleted_code(self):
        # کدی که ادمین حذف کرده با شروع ران تازه برنمی‌گردد
        self.store.add_code("GOD10", 10, 0)
        self.store.del_code("GOD10")
        godstar.ensure_defaults(self.store)
        self.assertIsNone(self.store.get_code("GOD10"))

    def test_setup_menu_button_opens_webapp(self):
        self.assertTrue(godstar.setup_menu_button())
        self.assertEqual(len(self.api_calls), 1)
        method, data = self.api_calls[0]
        self.assertEqual(method, "setChatMenuButton")
        import json as _json
        button = _json.loads(data["menu_button"])
        self.assertEqual(button["type"], "web_app")
        self.assertEqual(button["text"], "فروشگاه")
        self.assertEqual(button["web_app"]["url"], "https://example.invalid/app")

    def test_setup_menu_button_skipped_without_url(self):
        godstar.WEBAPP_URL = ""
        self.assertFalse(godstar.setup_menu_button())
        self.assertEqual(self.api_calls, [])

    def test_setup_commands_admin_scope_only(self):
        # فقط یک فراخوانی setMyCommands با scope چت ادمین؛ برای بقیه
        # هیچ دستوری اضافه نمی‌شود (همان چیزی که الان می‌بینند).
        self.assertTrue(godstar.setup_commands())
        self.assertEqual(len(self.api_calls), 1)
        method, data = self.api_calls[0]
        self.assertEqual(method, "setMyCommands")
        import json as _json
        scope = _json.loads(data["scope"])
        self.assertEqual(scope, {"type": "chat", "chat_id": 278899906})
        commands = _json.loads(data["commands"])
        names = [c["command"] for c in commands]
        self.assertEqual(names, ["start", "help", "orders", "approve",
                                 "reject", "codes", "addcode", "delcode",
                                 "broadcast", "stats", "autoreplies",
                                 "addreply", "delreply"])
        for c in commands:
            self.assertFalse(c["command"].startswith("/"))
            self.assertTrue(c["description"].strip())

    def test_setup_commands_failure_is_tolerated(self):
        godstar.api = lambda method, **data: None
        self.assertFalse(godstar.setup_commands())

    def test_setup_commands_skipped_without_admin(self):
        godstar.ADMIN_CHAT_ID = ""
        self.assertFalse(godstar.setup_commands())
        self.assertEqual(self.api_calls, [])

    def test_main_keyboard_has_admin_button_for_admin(self):
        texts = [b["text"] for row in godstar.main_keyboard(True)["keyboard"]
                 for b in row]
        self.assertIn("📋 دستورهای ادمین", texts)

    def test_main_keyboard_has_no_admin_button_for_users(self):
        texts = [b["text"] for row in godstar.main_keyboard()["keyboard"]
                 for b in row]
        self.assertNotIn("📋 دستورهای ادمین", texts)

    def test_admin_commands_button_shows_commands_keyboard(self):
        godstar.handle_message(self.store, self._admin_msg("📋 دستورهای ادمین"))
        self.assertTrue(any("دستورهای ادمین" in t for _, t, _ in self.sent))
        list_text = [t for _, t, _ in self.sent if "دستورهای ادمین" in t][0]
        # روی دکمه‌ها و در لیست، کلمه و کار دستور نوشته شده، نه خود دستور
        self.assertNotIn("/", list_text)
        _, _, markup = self.sent[-1]
        texts = [b["text"] for row in markup["keyboard"] for b in row]
        for label in ("📦 سفارش‌های در انتظار", "✅ تایید سفارش",
                      "❌ رد سفارش", "🎟 کدهای تخفیف",
                      "➕ ساخت کد تخفیف", "🗑 حذف کد تخفیف",
                      "📣 پیام همگانی", "📊 آمار",
                      "💬 پاسخ‌های خودکار", "➕ ساخت پاسخ خودکار",
                      "🗑 حذف پاسخ خودکار"):
            self.assertIn(label, texts)
        self.assertNotIn("/approve", texts)
        self.assertIn("🔙 بازگشت به منو", texts)

    def test_admin_keyboard_label_runs_command(self):
        # زدن برچسب فارسی، همان دستور را می‌فرستد (اینجا: پرسش شماره)
        godstar.handle_message(self.store, self._admin_msg("✅ تایید سفارش"))
        self.assertTrue(any("شماره سفارش" in t for _, t, _ in self.sent))

    def test_start_creates_referral_code(self):
        godstar.handle_message(self.store, self._admin_msg("/start"))
        user = self.store.get_user(278899906)
        self.assertEqual(user["referral_code"],
                         pricing.derive_referral_code(278899906))

    def test_admin_commands_button_ignored_for_users(self):
        msg = {"chat": {"id": 111}, "from": {"id": 111},
               "text": "📋 دستورهای ادمین"}
        godstar.handle_message(self.store, msg)
        self.assertFalse(any("دستورهای ادمین" in t for _, t, _ in self.sent))

    def test_back_button_returns_to_main_keyboard(self):
        godstar.handle_message(self.store, self._admin_msg("📋 دستورهای ادمین"))
        godstar.handle_message(self.store, self._admin_msg("🔙 بازگشت به منو"))
        _, _, markup = self.sent[-1]
        texts = [b["text"] for row in markup["keyboard"] for b in row]
        self.assertIn("📋 دستورهای ادمین", texts)
        self.assertNotIn("/approve", texts)

    def _admin_msg(self, text):
        return {"chat": {"id": 278899906}, "from": {"id": 278899906},
                "text": text}

    def test_bare_approve_asks_for_order_number(self):
        godstar.handle_message(self.store, self._admin_msg("/approve"))
        self.assertTrue(any("شماره سفارش" in t for _, t, _ in self.sent))
        prompt = self.store.state.get("pending_prompt", {}).get("278899906")
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt["cmd"], "/approve")

    def test_prompt_reply_confirms_order(self):
        order = self.store.create_order(111, "order", "members",
                                        {"count": 1000}, "3")
        godstar.handle_message(self.store, self._admin_msg("/approve"))
        godstar.handle_message(self.store, self._admin_msg(order["order_no"]))
        self.assertTrue(any(f"✅ سفارش {order['order_no']} تایید شد." in t
                            for _, t, _ in self.sent))
        self.assertEqual(
            self.store.get_order(order["order_no"])["status"], "confirmed")
        self.assertNotIn("278899906",
                         self.store.state.get("pending_prompt", {}))

    def test_prompt_cancel(self):
        godstar.handle_message(self.store, self._admin_msg("/approve"))
        godstar.handle_message(self.store, self._admin_msg("لغو"))
        self.assertTrue(any("لغو شد" in t for _, t, _ in self.sent))
        self.assertNotIn("278899906",
                         self.store.state.get("pending_prompt", {}))

    def test_prompt_expires_after_ten_minutes(self):
        import time as _time
        godstar.handle_message(self.store, self._admin_msg("/approve"))
        self.store.state["pending_prompt"]["278899906"]["at"] = (
            _time.time() - 700)
        godstar.handle_message(self.store, self._admin_msg("GS-1001"))
        self.assertTrue(any("زمان پاسخ تمام شد" in t for _, t, _ in self.sent))

    def test_new_command_clears_pending_prompt(self):
        self.store.add_code("GOD10", 10, 0)
        godstar.handle_message(self.store, self._admin_msg("/approve"))
        godstar.handle_message(self.store, self._admin_msg("/codes"))
        self.assertNotIn("278899906",
                         self.store.state.get("pending_prompt", {}))
        self.assertTrue(any("کدهای تخفیف" in t for _, t, _ in self.sent))

    def test_keyboard_button_works_during_prompt(self):
        order = self.store.create_order(111, "order", "members",
                                        {"count": 1000}, "3")
        godstar.handle_message(self.store, self._admin_msg("/approve"))
        # ادمین برای پیدا کردن شماره، سفارش‌های من را باز می‌کند؛
        # پرسش باید بماند تا بعد شماره را بفرستد.
        godstar.handle_message(self.store, self._admin_msg("📦 سفارش‌های من"))
        self.assertIn("278899906",
                      self.store.state.get("pending_prompt", {}))
        godstar.handle_message(self.store, self._admin_msg(order["order_no"]))
        self.assertTrue(any("تایید شد" in t for _, t, _ in self.sent))

    def test_addcode_prompt_with_two_args(self):
        godstar.handle_message(self.store, self._admin_msg("/addcode"))
        self.assertTrue(any("کد و درصد" in t for _, t, _ in self.sent))
        godstar.handle_message(self.store, self._admin_msg("GOD20 20"))
        self.assertTrue(any("GOD20" in t and "20٪" in t
                            for _, t, _ in self.sent))
        self.assertIsNotNone(self.store.get_code("GOD20"))

    def test_admin_reply_to_customer_has_shop_button(self):
        self.store.map_support_message(55, 111)
        msg = {"chat": {"id": 278899906}, "from": {"id": 278899906},
               "text": "سلام، درست شد",
               "reply_to_message": {"message_id": 55}}
        godstar.handle_message(self.store, msg)
        to_customer = [s for s in self.sent if s[0] == 111]
        self.assertEqual(len(to_customer), 1)
        self.assertEqual(to_customer[0][1], "سلام، درست شد")
        markup = to_customer[0][2]
        self.assertIsNotNone(markup)
        self.assertEqual(markup["inline_keyboard"][0][0]["web_app"]["url"],
                         "https://example.invalid/app")
        self.assertTrue(any("جواب به مشتری" in t for _, t, _ in self.sent))

    def test_webapp_my_orders_lists_real_orders(self):
        order = self.store.create_order(111, "order", "members",
                                        {"count": 1000}, "3")
        msg = {"chat": {"id": 111}, "from": {"id": 111, "username": "u"},
               "web_app_data": {"data": '{"type": "my_orders"}'}}
        godstar.handle_web_app_data(self.store, msg)
        self.assertTrue(any(order["order_no"] in t for _, t, _ in self.sent))

    def test_webapp_my_orders_empty(self):
        msg = {"chat": {"id": 111}, "from": {"id": 111, "username": "u"},
               "web_app_data": {"data": '{"type": "my_orders"}'}}
        godstar.handle_web_app_data(self.store, msg)
        self.assertTrue(any("هنوز سفارشی" in t for _, t, _ in self.sent))

    def test_webapp_my_wallet_shows_real_balance(self):
        self.store.credit_wallet(111, 7, note="test")
        msg = {"chat": {"id": 111}, "from": {"id": 111, "username": "u"},
               "web_app_data": {"data": '{"type": "my_wallet"}'}}
        godstar.handle_web_app_data(self.store, msg)
        self.assertTrue(any("7" in t and "USDT" in t for _, t, _ in self.sent))


class RemindersAndReorderTest(unittest.TestCase):
    """یادآوری تمدید و سفارش در انتظار، سفارش دوباره، چسباندن رسید."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(state_file=os.path.join(self.tmp.name, "state.json"))
        self.store.load()
        godstar.ensure_defaults(self.store)
        self.sent = []
        self.api_calls = []
        self._orig_send = godstar.send_message
        self._orig_api = godstar.api
        self._orig_admin = godstar.ADMIN_CHAT_ID
        self._orig_webapp = godstar.WEBAPP_URL
        godstar.send_message = lambda chat_id, text, reply_markup=None: self.sent.append(
            (chat_id, text, reply_markup)) or {"message_id": 1}
        godstar.api = lambda method, **data: self.api_calls.append(
            (method, data)) or {"message_id": 99}
        godstar.ADMIN_CHAT_ID = "278899906"
        godstar.WEBAPP_URL = "https://example.invalid/app"
        godstar._LAST_REMINDER_CHECK = 0

    def tearDown(self):
        godstar.send_message = self._orig_send
        godstar.api = self._orig_api
        godstar.ADMIN_CHAT_ID = self._orig_admin
        godstar.WEBAPP_URL = self._orig_webapp
        self.tmp.cleanup()

    def _iso_ago(self, seconds):
        import datetime as _dt
        return (_dt.datetime.now(_dt.timezone.utc)
                - _dt.timedelta(seconds=seconds)).isoformat()

    def _reset_throttle(self):
        godstar._LAST_REMINDER_CHECK = 0

    def test_created_at_is_iso_string(self):
        user = self.store.ensure_user(111)
        self.assertIsInstance(user["created_at"], str)
        self.assertIn("T", user["created_at"])
        order = self.store.create_order(111, "order", "members", {}, "3")
        self.assertIsInstance(order["created_at"], str)

    def test_list_orders_sorts_mixed_timestamps(self):
        o1 = self.store.create_order(111, "order", "members", {}, "3")
        o1["created_at"] = 1000  # داده‌ی قدیمی (epoch)
        o2 = self.store.create_order(111, "order", "members", {}, "3")
        o2["created_at"] = self._iso_ago(10)
        order_nos = [o["order_no"] for o in self.store.list_orders(user_id=111)]
        self.assertEqual(order_nos, [o1["order_no"], o2["order_no"]])

    def test_confirm_sets_confirmed_at(self):
        o1 = self.store.create_order(111, "order", "members", {}, "3")
        self.store.set_order_status(o1["order_no"], "confirmed")
        self.assertIn("confirmed_at", o1)
        o2 = self.store.create_order(111, "order", "members", {}, "3")
        self.store.set_order_status(o2["order_no"], "rejected")
        self.assertNotIn("confirmed_at", o2)

    def test_reminder_flags_once(self):
        self.assertTrue(self.store.add_reminder("GS-1001", "renewal"))
        self.assertFalse(self.store.add_reminder("GS-1001", "renewal"))
        self.assertTrue(self.store.has_reminder("GS-1001", "renewal"))
        self.assertFalse(self.store.has_reminder("GS-1001", "pending_admin"))

    def test_pending_reminder_after_two_hours_once(self):
        order = self.store.create_order(111, "order", "members",
                                        {"count": 1000}, "3")
        order["created_at"] = self._iso_ago(3 * 3600)
        godstar.check_reminders(self.store)
        admin_msgs = [t for c, t, _ in self.sent if str(c) == "278899906"]
        self.assertTrue(any("۲ ساعت" in t and order["order_no"] in t
                            for t in admin_msgs))
        self._reset_throttle()
        before = len(self.sent)
        godstar.check_reminders(self.store)
        self.assertEqual(len(self.sent), before)

    def test_pending_reminder_not_before_two_hours(self):
        order = self.store.create_order(111, "order", "members",
                                        {"count": 1000}, "3")
        order["created_at"] = self._iso_ago(3600)
        godstar.check_reminders(self.store)
        self.assertFalse(any(order["order_no"] in t for _, t, _ in self.sent))

    def test_renewal_reminder_three_days_before_expiry(self):
        order = self.store.create_order(
            111, "order", "bot",
            {"bot_type": "management", "plan": "3m",
             "channel_id": "@GodNews24"}, "28")
        self.store.set_order_status(order["order_no"], "confirmed")
        order["confirmed_at"] = self._iso_ago(88 * 86400)  # پایان تا ۲ روز دیگر
        godstar.check_reminders(self.store)
        user_msgs = [t for c, t, _ in self.sent if c == 111]
        self.assertTrue(any("۳ روز" in t and "تمام می‌شود" in t
                            for t in user_msgs))
        self._reset_throttle()
        before = len(self.sent)
        godstar.check_reminders(self.store)
        self.assertEqual(len(self.sent), before)

    def test_renewal_reminder_not_early_or_custom(self):
        o1 = self.store.create_order(
            111, "order", "bot",
            {"bot_type": "management", "plan": "3m",
             "channel_id": "@GodNews24"}, "28")
        self.store.set_order_status(o1["order_no"], "confirmed")
        o1["confirmed_at"] = self._iso_ago(10 * 86400)  # ۸۰ روز مانده
        o2 = self.store.create_order(
            111, "order", "bot",
            {"bot_type": "custom", "plan": "3m",
             "channel_id": "@GodNews24"}, "0")
        self.store.set_order_status(o2["order_no"], "confirmed")
        o2["confirmed_at"] = self._iso_ago(88 * 86400)
        godstar.check_reminders(self.store)
        self.assertFalse(any("تمام می‌شود" in t for _, t, _ in self.sent))

    def test_reorder_from_wallet(self):
        self.store.credit_wallet(111, 100, note="test")
        orig = godstar.place_order(
            self.store, 111, "members",
            {"count": 1000, "channel_id": "@GodNews24"}, paid_by_wallet=True)
        self.assertEqual(self.store.wallet_balance(111), 97.0)
        query = {"data": f"reorder:{orig['order_no']}", "id": "q1",
                 "message": {"chat": {"id": 111}}}
        godstar.handle_callback(self.store, query)
        self.assertEqual(self.store.wallet_balance(111), 94.0)
        self.assertIsNotNone(self.store.get_order("GS-1002"))
        self.assertTrue(any("سفارش دوباره ثبت شد" in t
                            and "از کیف پول" in t for _, t, _ in self.sent))

    def test_reorder_does_not_carry_discount_code(self):
        # سفارش دوباره کد تخفیف را دوباره اعمال نمی‌کند و کد قدیمی را
        # در جزئیات سفارش تازه نمی‌گذارد
        self.store.add_code("GOD10", 10, 0)
        orig = godstar.place_order(
            self.store, 111, "members",
            {"count": 1000, "channel_id": "@GodNews24"},
            discount_code="GOD10", tx_hash="abc")
        self.assertEqual(orig["discount_kind"], "code")
        self.store.credit_wallet(111, 100, note="test")
        query = {"data": f"reorder:{orig['order_no']}", "id": "q1",
                 "message": {"chat": {"id": 111}}}
        godstar.handle_callback(self.store, query)
        new_order = self.store.get_order("GS-1002")
        self.assertIsNotNone(new_order)
        self.assertNotEqual(new_order["discount_kind"], "code")
        self.assertNotIn("discount_code", new_order.get("details") or {})

    def test_reorder_deferred_receipt_without_balance(self):
        orig = godstar.place_order(
            self.store, 222, "members",
            {"count": 1000, "channel_id": "@GodNews24"},
            tx_hash="0x" + "ab" * 32)
        query = {"data": f"reorder:{orig['order_no']}", "id": "q1",
                 "message": {"chat": {"id": 222}}}
        godstar.handle_callback(self.store, query)
        new_order = self.store.get_order("GS-1002")
        self.assertIsNotNone(new_order)
        self.assertEqual(new_order["status"], "pending")
        self.assertTrue(any("رسید را همین‌جا بفرست" in t
                            for _, t, _ in self.sent))

    def test_reorder_other_users_order_rejected(self):
        orig = godstar.place_order(
            self.store, 111, "members",
            {"count": 1000, "channel_id": "@GodNews24"},
            tx_hash="0x" + "ab" * 32)
        query = {"data": f"reorder:{orig['order_no']}", "id": "q1",
                 "message": {"chat": {"id": 333}}}
        godstar.handle_callback(self.store, query)
        self.assertIsNone(self.store.get_order("GS-1002"))
        self.assertTrue(any("مال تو نیست" in (d.get("text") or "")
                            for m, d in self.api_calls
                            if m == "answerCallbackQuery"))

    def test_receipt_attach_photo(self):
        order = godstar.place_order(
            self.store, 111, "members",
            {"count": 1000, "channel_id": "@GodNews24"},
            receipt_kind="photo")
        msg = {"chat": {"id": 111}, "from": {"id": 111},
               "photo": [{"file_id": "small"}, {"file_id": "big"}]}
        godstar.handle_message(self.store, msg)
        self.assertEqual(self.store.get_order(order["order_no"])
                         ["receipt_file_id"], "big")
        self.assertTrue(any("رسید سفارش" in t and "دریافت شد" in t
                            for _, t, _ in self.sent))

    def test_receipt_attach_hash(self):
        order = godstar.place_order(
            self.store, 111, "members",
            {"count": 1000, "channel_id": "@GodNews24"},
            receipt_kind="photo")
        tx = "0x" + "ab" * 32
        msg = {"chat": {"id": 111}, "from": {"id": 111}, "text": tx}
        godstar.handle_message(self.store, msg)
        self.assertEqual(self.store.get_order(order["order_no"])["tx_hash"], tx)

    def test_receipt_ignored_without_awaiting_order(self):
        msg = {"chat": {"id": 111}, "from": {"id": 111},
               "photo": [{"file_id": "big"}]}
        godstar.handle_message(self.store, msg)
        self.assertTrue(any("از منوی پایین" in t for _, t, _ in self.sent))

    def test_load_tolerates_missing_table(self):
        from db import Store as _Store
        orig = _Store._sb_get_all

        def fake(self, table):
            if table == "order_reminders":
                raise RuntimeError("supabase table missing (order_reminders)")
            return []
        _Store._sb_get_all = fake
        try:
            store = _Store(state_file=os.path.join(self.tmp.name, "s2.json"),
                           supabase_url="https://x.supabase.co",
                           supabase_key="k")
            store.load()
        finally:
            _Store._sb_get_all = orig
        self.assertEqual(store.state["order_reminders"], [])
        self.assertEqual(store.state["orders"], {})

    def test_load_normalizes_partial_discount_codes(self):
        # ردیف ناقص (بدون uses/active) کد را «غیرفعال» نشان می‌دهد؛
        # هنگام خواندن، پیش‌فرض‌های اسکیما پر می‌شوند.
        from db import Store as _Store
        orig = _Store._sb_get_all

        def fake(self, table):
            if table == "discount_codes":
                return [{"code": "GOD10", "percent": 10, "max_uses": 0}]
            return [] if table in ("wallet_tx", "referrals", "auto_replies",
                                   "order_reminders") else {}
        _Store._sb_get_all = fake
        try:
            store = _Store(state_file=os.path.join(self.tmp.name, "s3.json"),
                           supabase_url="https://x.supabase.co",
                           supabase_key="k")
            store.load()
        finally:
            _Store._sb_get_all = orig
        row = store.state["discount_codes"]["GOD10"]
        self.assertEqual(row.get("uses"), 0)
        self.assertTrue(row.get("active"))


class InitDataAndInboxTest(unittest.TestCase):
    """اعتبارسنجی initData، صندوق وب‌اپ و دلایل رد سفارش (نسخه‌ی ۱٫۰)."""
    TOKEN = "test-bot-token-123"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(state_file=os.path.join(self.tmp.name, "state.json"))
        self.store.load()
        godstar.ensure_defaults(self.store)
        self.sent = []
        self.api_calls = []
        self.deleted_rows = []
        self._row_n = 0
        self._orig_send = godstar.send_message
        self._orig_api = godstar.api
        self._orig_admin = godstar.ADMIN_CHAT_ID
        self._orig_token = godstar.TOKEN
        self._orig_inbox_check = godstar._LAST_INBOX_CHECK
        godstar.send_message = lambda chat_id, text, reply_markup=None: self.sent.append(
            (chat_id, text, reply_markup)) or {"message_id": 1}
        godstar.api = lambda method, **data: self.api_calls.append(
            (method, data)) or {"message_id": 99}
        godstar.ADMIN_CHAT_ID = "278899906"
        godstar.TOKEN = self.TOKEN
        godstar._LAST_INBOX_CHECK = 0

    def tearDown(self):
        godstar.send_message = self._orig_send
        godstar.api = self._orig_api
        godstar.ADMIN_CHAT_ID = self._orig_admin
        godstar.TOKEN = self._orig_token
        godstar._LAST_INBOX_CHECK = self._orig_inbox_check
        self.tmp.cleanup()

    def _make_init_data(self, user_id, token=None, auth_date=None):
        import hashlib
        import hmac as _hmac
        import json as _json
        import time as _time
        import urllib.parse as _up
        params = {
            "auth_date": str(auth_date if auth_date is not None else int(_time.time())),
            "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
            "user": _json.dumps({"id": user_id, "first_name": "Test",
                                 "username": "t"}, ensure_ascii=False),
        }
        check = "\n".join(f"{k}={params[k]}" for k in sorted(params))
        secret = _hmac.new(b"WebAppData", (token or self.TOKEN).encode(),
                           hashlib.sha256).digest()
        params["hash"] = _hmac.new(secret, check.encode(),
                                   hashlib.sha256).hexdigest()
        return _up.urlencode(params)

    def _inbox_row(self, user_id, payload, init_data=None):
        self._row_n += 1
        return {"id": f"row-{self._row_n}",
                "user_id": user_id,
                "init_data": init_data if init_data is not None
                            else self._make_init_data(user_id),
                "payload": payload,
                "created_at": "2026-11-24T10:00:00+00:00"}

    def _stub_inbox(self, rows):
        fetched = []

        def fetch():
            fetched.append(1)
            return list(rows)

        def delete(row_id):
            self.deleted_rows.append(row_id)
        self.store.fetch_inbox_rows = fetch
        self.store.delete_inbox_row = delete
        return fetched

    def _admin_query(self, data):
        return {"data": data, "id": "q1",
                "message": {"chat": {"id": 278899906}}}

    # ---- validate_init_data ----
    def test_validate_init_data_valid(self):
        user = godstar.validate_init_data(self._make_init_data(111), self.TOKEN)
        self.assertIsNotNone(user)
        self.assertEqual(user["id"], 111)

    def test_validate_init_data_tampered(self):
        init_data = self._make_init_data(111).replace("Test", "Mallory")
        self.assertIsNone(godstar.validate_init_data(init_data, self.TOKEN))

    def test_validate_init_data_expired(self):
        import time as _time
        old = self._make_init_data(111, auth_date=int(_time.time()) - 25 * 3600)
        self.assertIsNone(godstar.validate_init_data(old, self.TOKEN))

    def test_validate_init_data_missing_hash(self):
        import urllib.parse as _up
        params = {"auth_date": "1700000000", "query_id": "x",
                  "user": '{"id": 111}'}
        self.assertIsNone(
            godstar.validate_init_data(_up.urlencode(params), self.TOKEN))

    def test_validate_init_data_wrong_token(self):
        init_data = self._make_init_data(111, token="other-token")
        self.assertIsNone(godstar.validate_init_data(init_data, self.TOKEN))

    def test_validate_init_data_old_sha256_derivation_rejected(self):
        # رگرسیون (v1.0.2): کلید مخفی HMAC-SHA256 توکن با کلید «WebAppData»
        # است، نه SHA256 ساده‌ی توکن؛ داده‌ای که با روش قدیمی امضا شده
        # باید رد شود (همین اشتباه باعث «ثبت نشد» برای همه‌ی initDataهای
        # واقعی شده بود).
        import hashlib as _hashlib
        import hmac as _hmac2
        import json as _json2
        import time as _time2
        import urllib.parse as _up2
        params = {
            "auth_date": str(int(_time2.time())),
            "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
            "user": _json2.dumps({"id": 111, "first_name": "Test",
                                  "username": "t"}, ensure_ascii=False),
        }
        check = "\n".join(f"{k}={params[k]}" for k in sorted(params))
        old_secret = _hashlib.sha256(self.TOKEN.encode()).digest()
        params["hash"] = _hmac2.new(old_secret, check.encode(),
                                    _hashlib.sha256).hexdigest()
        self.assertIsNone(
            godstar.validate_init_data(_up2.urlencode(params), self.TOKEN))

    def test_validate_init_data_empty(self):
        self.assertIsNone(godstar.validate_init_data("", self.TOKEN))
        self.assertIsNone(godstar.validate_init_data(self._make_init_data(111), ""))

    # ---- صندوق وب‌اپ ----
    def test_inbox_order_row_creates_order(self):
        payload = {"type": "order", "service": "members",
                   "details": {"count": 1000, "channel_id": "@GodNews24"},
                   "tx_hash": "abc123"}
        row = self._inbox_row(111, payload)
        self._stub_inbox([row])
        godstar.process_webapp_inbox(self.store)
        order = self.store.get_order("GS-1001")
        self.assertIsNotNone(order)
        self.assertEqual(order["amount"], "3.00")
        self.assertTrue(any("سفارش شما ثبت شد" in t for _, t, _ in self.sent))
        self.assertTrue(any("🆕 سفارش جدید" in t for _, t, _ in self.sent))
        self.assertEqual(self.deleted_rows, [row["id"]])

    def test_inbox_wallet_charge_row(self):
        payload = {"type": "wallet_charge", "amount_usdt": 20,
                   "receipt_kind": "hash", "receipt_hash": "0x789"}
        row = self._inbox_row(111, payload)
        self._stub_inbox([row])
        godstar.process_webapp_inbox(self.store)
        order = self.store.get_order("GS-1001")
        self.assertIsNotNone(order)
        self.assertEqual(order["type"], "wallet_charge")
        self.assertEqual(order["amount"], "20.00")
        self.assertEqual(self.deleted_rows, [row["id"]])

    def test_inbox_invalid_row_deleted_creates_nothing(self):
        row = self._inbox_row(111, {"type": "my_orders"},
                              init_data="query_id=x&user=%7B%22id%22%3A111%7D"
                                        "&auth_date=1700000000")
        self._stub_inbox([row])
        godstar.process_webapp_inbox(self.store)
        self.assertIsNone(self.store.get_order("GS-1001"))
        self.assertEqual(self.sent, [])
        self.assertEqual(self.deleted_rows, [row["id"]])

    def test_inbox_bad_row_does_not_stop_others(self):
        bad = self._inbox_row(111, {"type": "my_orders"}, init_data="garbage")
        good = self._inbox_row(222, {"type": "order", "service": "members",
                                     "details": {"count": 1000,
                                                 "channel_id": "@GodNews24"},
                                     "tx_hash": "abc"})
        self._stub_inbox([bad, good])
        godstar.process_webapp_inbox(self.store)
        order = self.store.get_order("GS-1001")
        self.assertIsNotNone(order)
        self.assertEqual(order["user_id"], 222)
        self.assertEqual(sorted(self.deleted_rows), sorted([bad["id"], good["id"]]))

    def test_inbox_reorder_payload(self):
        orig = godstar.place_order(
            self.store, 111, "members",
            {"count": 1000, "channel_id": "@GodNews24"}, tx_hash="abc")
        self.sent.clear()
        row = self._inbox_row(111, {"type": "reorder",
                                    "order_no": orig["order_no"]})
        self._stub_inbox([row])
        godstar.process_webapp_inbox(self.store)
        self.assertIsNotNone(self.store.get_order("GS-1002"))
        self.assertTrue(any("سفارش دوباره ثبت شد" in t for _, t, _ in self.sent))
        self.assertEqual(self.deleted_rows, [row["id"]])

    def test_inbox_processing_exception_deletes_and_notifies(self):
        row = self._inbox_row(111, {"type": "my_orders"})
        self._stub_inbox([row])

        def boom(store, chat_id, payload):
            raise RuntimeError("boom")
        orig = godstar.process_webapp_payload
        godstar.process_webapp_payload = boom
        try:
            godstar.process_webapp_inbox(self.store)
        finally:
            godstar.process_webapp_payload = orig
        self.assertEqual(self.deleted_rows, [row["id"]])
        self.assertTrue(any("مشکلی در ثبت سفارشت پیش آمد" in t
                            for c, t, _ in self.sent if c == 111))

    def test_inbox_throttle_checks_at_most_once_per_ten_seconds(self):
        row = self._inbox_row(111, {"type": "my_orders"})
        fetched = self._stub_inbox([row])
        godstar.process_webapp_inbox(self.store)
        godstar.process_webapp_inbox(self.store)
        self.assertEqual(len(fetched), 1)
        godstar._LAST_INBOX_CHECK = 0
        godstar.process_webapp_inbox(self.store)
        self.assertEqual(len(fetched), 2)

    # ---- متدهای صندوق در db ----
    def test_fetch_inbox_rows_sorts_by_created_at(self):
        store = Store(state_file=os.path.join(self.tmp.name, "s-inbox.json"),
                      supabase_url="https://example.supabase.co",
                      supabase_key="k")
        store._sb_get_all = lambda table: [
            {"id": "b", "created_at": "2026-11-24T10:02:00+00:00"},
            {"id": "a", "created_at": "2026-11-24T10:01:00+00:00"},
        ] if table == "webapp_inbox" else []
        rows = store.fetch_inbox_rows()
        self.assertEqual([r["id"] for r in rows], ["a", "b"])

    def test_fetch_inbox_rows_empty_without_supabase(self):
        self.assertEqual(self.store.fetch_inbox_rows(), [])
        self.store.delete_inbox_row("whatever")  # no-op, must not raise

    def test_delete_inbox_row_uses_id(self):
        store = Store(state_file=os.path.join(self.tmp.name, "s-inbox2.json"),
                      supabase_url="https://example.supabase.co",
                      supabase_key="k")
        calls = []
        store._sb_delete = lambda table, key_col, key_val: calls.append(
            (table, key_col, key_val))
        store.delete_inbox_row("550e8400-1234-5678-9abc-def012345678")
        self.assertEqual(calls, [("webapp_inbox", "id",
                                  "550e8400-1234-5678-9abc-def012345678")])

    # ---- دلایل رد سفارش ----
    def test_reject_preset_reason_flow(self):
        order = self.store.create_order(111, "order", "members",
                                        {"count": 1000}, "3")
        godstar.handle_callback(self.store, self._admin_query(
            f"reject:{order['order_no']}"))
        # هنوز رد نشده؛ ادمین باید دلیل را انتخاب کند
        self.assertEqual(self.store.get_order(order["order_no"])["status"],
                         "pending")
        picker = [s for s in self.sent if "دلیل رد را انتخاب کن" in s[1]]
        self.assertEqual(len(picker), 1)
        markup = picker[0][2]
        texts = [b["text"] for row in markup["inline_keyboard"] for b in row]
        for reason in godstar.REJECT_REASONS:
            self.assertIn(reason, texts)
        self.assertIn("✏️ تایپ دلیل", texts)
        self.assertIn("لغو", texts)
        # انتخاب اولین دلیل آماده
        self.sent.clear()
        godstar.handle_callback(self.store, self._admin_query(
            f"reject_reason:{order['order_no']}:0"))
        self.assertEqual(self.store.get_order(order["order_no"])["status"],
                         "rejected")
        self.assertEqual(
            self.store.get_order(order["order_no"])["details"]["reject_reason"],
            godstar.REJECT_REASONS[0])
        self.assertTrue(any("دلیل: " + godstar.REJECT_REASONS[0] in t
                            for c, t, _ in self.sent if c == 111))
        self.assertTrue(any("رد شد" in t for c, t, _ in self.sent
                            if str(c) == "278899906"))

    def test_reject_typed_reason_via_prompt(self):
        order = self.store.create_order(111, "order", "members",
                                        {"count": 1000}, "3")
        godstar.handle_callback(self.store, self._admin_query(
            f"reject:{order['order_no']}"))
        godstar.handle_callback(self.store, self._admin_query(
            f"reject_reason_type:{order['order_no']}"))
        prompt = self.store.state.get("pending_prompt", {}).get("278899906")
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt["cmd"], f"/reject_reason {order['order_no']}")
        self.assertTrue(any("دلیل رد سفارش" in t and "تایپ کن" in t
                            for _, t, _ in self.sent))
        self.sent.clear()
        msg = {"chat": {"id": 278899906}, "from": {"id": 278899906},
               "text": "رسید جعلی است"}
        godstar.handle_message(self.store, msg)
        self.assertEqual(self.store.get_order(order["order_no"])["status"],
                         "rejected")
        self.assertEqual(
            self.store.get_order(order["order_no"])["details"]["reject_reason"],
            "رسید جعلی است")
        self.assertTrue(any("دلیل: رسید جعلی است" in t
                            for c, t, _ in self.sent if c == 111))

    def test_reject_cancel_keeps_pending(self):
        order = self.store.create_order(111, "order", "members",
                                        {"count": 1000}, "3")
        godstar.handle_callback(self.store, self._admin_query(
            f"reject:{order['order_no']}"))
        godstar.handle_callback(self.store, self._admin_query(
            f"reject_cancel:{order['order_no']}"))
        self.assertEqual(self.store.get_order(order["order_no"])["status"],
                         "pending")
        self.assertTrue(any("در انتظار می‌ماند" in t for _, t, _ in self.sent))

    def test_reject_command_asks_reason_first(self):
        order = self.store.create_order(111, "order", "members",
                                        {"count": 1000}, "3")
        msg = {"chat": {"id": 278899906}, "from": {"id": 278899906},
               "text": f"/reject {order['order_no']}"}
        godstar.handle_message(self.store, msg)
        self.assertEqual(self.store.get_order(order["order_no"])["status"],
                         "pending")
        self.assertTrue(any("دلیل رد را انتخاب کن" in t for _, t, _ in self.sent))

    def test_reject_with_reason_refunds_wallet_and_stores_reason(self):
        self.store.credit_wallet(111, 20)
        order = godstar.place_order(self.store, 111, "members",
                                    {"count": 1000, "channel_id": "@GodNews24"},
                                    paid_by_wallet=True)
        self.assertEqual(self.store.wallet_balance(111), 17.0)
        godstar.reject_order(self.store, order["order_no"], "رسید معتبر نیست")
        self.assertEqual(self.store.wallet_balance(111), 20.0)
        self.assertEqual(
            self.store.get_order(order["order_no"])["details"]["reject_reason"],
            "رسید معتبر نیست")
        self.assertTrue(any("دلیل: رسید معتبر نیست" in t for _, t, _ in self.sent))

    def test_reject_without_reason_has_no_reason_line(self):
        order = self.store.create_order(111, "order", "members",
                                        {"count": 1000}, "3")
        godstar.reject_order(self.store, order["order_no"])
        self.assertFalse(any("دلیل:" in t for _, t, _ in self.sent))
        self.assertNotIn("reject_reason",
                         self.store.get_order(order["order_no"])["details"])


class SwipeTabsTest(unittest.TestCase):
    """تب‌های کشویی صفحه‌ی اصلی: خدمات / سفارش‌های من / پشتیبانی پنل‌های
    همان صفحه‌اند، نه صفحه‌ی جدا؛ بالای صفحه ثابت می‌ماند."""

    def setUp(self):
        self.root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(self.root, "webapp", "index.html"),
                  encoding="utf-8") as f:
            self.html = f.read()
        with open(os.path.join(self.root, "webapp", "app.js"),
                  encoding="utf-8") as f:
            self.js = f.read()
        with open(os.path.join(self.root, "webapp", "style.css"),
                  encoding="utf-8") as f:
            self.css = f.read()

    def test_three_panels_in_one_swipe_track(self):
        self.assertIn('id="swipe-main"', self.html)
        self.assertIn('id="swipe-track"', self.html)
        for panel in ("home", "orders", "support"):
            self.assertIn(f'data-panel="{panel}"', self.html)

    def test_orders_and_support_are_not_separate_screens(self):
        self.assertNotIn("screen-orders", self.html)
        self.assertNotIn("screen-support", self.html)
        self.assertNotIn("screen-orders", self.js)
        self.assertNotIn("screen-support", self.js)
        # محتوای هر دو پنل هنوز هست و هر آی‌دی فقط یک بار آمده
        for _id in ("orders-list", "chat-box", "faq-list", "support-input"):
            self.assertEqual(self.html.count(f'id="{_id}"'), 1, _id)

    def test_sticky_header_and_swipe_css(self):
        self.assertIn(".main-head", self.css)
        self.assertIn("position: sticky", self.css)
        self.assertIn(".swipe-track", self.css)
        self.assertIn("touch-action: pan-y", self.css)

    def test_js_has_panel_navigation(self):
        self.assertIn('PANELS = ["home", "orders", "support"]', self.js)
        self.assertIn("function setPanel(", self.js)
        self.assertIn("function bindSwipe(", self.js)
        self.assertIn("touchstart", self.js)
        self.assertIn("touchend", self.js)
        # باز کردن پنل سفارش‌ها داده‌ی واقعی را تازه می‌کند و تازه‌سازی
        # خودکار (v1.0.3) را روشن می‌کند؛ بیرون آمدن از پنل آن را قطع می‌کند.
        # فقط وقتی پنل واقعاً عوض شده (v1.0.6): زدن روی صفحه نباید لیست
        # را از نو بسازد، وگرنه دکمه‌ای که کاربر زده زیر انگشتش نابود
        # می‌شود و ضربه‌اش گم می‌شود (دکمه‌ی «دوباره» در ۱٫۰٫۵ همین‌طور
        # بی‌اثر مانده بود).
        self.assertIn('if (changed && PANELS[currentPanel] === "orders") {', self.js)
        self.assertIn("if (!DEMO) loadMyData();", self.js)
        self.assertIn("startOrdersPolling(15000, 0);", self.js)
        self.assertIn("stopOrdersPolling();", self.js)
        # ضربه‌ی ساده (بدون کشیدن) نباید setPanel صدا بزند
        self.assertIn("if (!dragging) { return; }", self.js)
        # تب‌ها هنوز با data-nav مشخص می‌شوند
        self.assertIn('data-nav="orders"', self.html)
        self.assertIn('data-nav="support"', self.html)


class LiveOrdersAndSupportPhotoTest(unittest.TestCase):
    """سفارش‌های زنده (v1.0.3) + پیش‌نمایش عکس پشتیبانی: سفارش تازه همان
    لحظه در «سفارش‌های من» دیده می‌شود و تغییر وضعیت هم با تازه‌سازی
    خودکار همان‌جا می‌نشیند؛ عکس انتخاب‌شده در پشتیبانی پیش‌نمایش دارد
    و عکس فرستاده‌شده در چت، خود عکس است نه جای خالی کوه."""

    def setUp(self):
        self.js = open("webapp/app.js", encoding="utf-8").read()
        self.html = open("webapp/index.html", encoding="utf-8").read()
        self.css = open("webapp/style.css", encoding="utf-8").read()

    def test_pending_order_shows_instantly(self):
        self.assertIn("function addPendingOrder(", self.js)
        self.assertIn("function renderOrdersMerged(", self.js)
        self.assertIn("function pendingMatchesReal(", self.js)
        self.assertIn("در حال ثبت…", self.js)
        # بعد از ثبت سفارش و شارژ کیف پول، تازه‌سازی تند شروع می‌شود
        self.assertEqual(self.js.count("startOrdersPolling(5000, 60000);"), 3)
        self.assertIn("function startOrdersPolling(", self.js)
        self.assertIn("function stopOrdersPolling(", self.js)

    def test_support_photo_preview_and_real_photo_in_chat(self):
        self.assertIn('id="support-photo-preview"', self.html)
        self.assertIn('id="support-photo-remove"', self.html)
        self.assertIn("support-photo-pick", self.html)
        self.assertIn("URL.createObjectURL(f)", self.js)
        self.assertIn("عکس انتخاب شد: ", self.js)
        # عکس فرستاده‌شده با آدرس محلی‌اش در پیام چت می‌ماند
        self.assertIn("photoUrl: sentPhotoUrl", self.js)
        self.assertIn("if (m.photoUrl) {", self.js)
        self.assertIn(".bubble.photo img", self.css)
        self.assertIn("#support-photo-pick", self.css)

    def test_support_photo_data_sent_to_admin(self):
        # v1.0.4: عکس پشتیبانی کوچک می‌شود و با photo_data همراه پیام
        # فرستاده می‌شود تا ادمین خود عکس را ببیند.
        self.assertIn("function downscalePhoto(", self.js)
        self.assertIn("photo_data: dataUrl", self.js)
        self.assertIn('toDataURL("image/jpeg", 0.8)', self.js)

    def test_wallet_checkbox_always_hides_manual(self):
        # v1.0.4: زدن تیک کیف پول، روش‌های دستی را پنهان می‌کند حتی اگر
        # موجودی کم باشد؛ در آن صورت هشدار کمبود موجودی نشان داده می‌شود.
        self.assertIn('$("pay-manual-wrap").classList.toggle("hidden", walletPay);', self.js)
        self.assertIn("کافی نیست", self.js)


class SupportReplyTest(unittest.TestCase):
    """دکمه‌ی «✏️ پاسخ» زیر پیام پشتیبانی در چت ادمین: با زدن آن ادمین
    جوابش را تایپ می‌کند و به همان مشتری فرستاده می‌شود."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(state_file=os.path.join(self.tmp.name, "state.json"))
        self.store.load()
        godstar.ensure_defaults(self.store)
        self.sent = []
        self.api_calls = []
        self._orig_send = godstar.send_message
        self._orig_api = godstar.api
        self._orig_admin = godstar.ADMIN_CHAT_ID
        self._orig_webapp = godstar.WEBAPP_URL
        godstar.send_message = lambda chat_id, text, reply_markup=None: self.sent.append(
            (chat_id, text, reply_markup)) or {"message_id": 1}
        godstar.api = lambda method, **data: self.api_calls.append(
            (method, data)) or {"message_id": 99}
        godstar.ADMIN_CHAT_ID = "278899906"
        godstar.WEBAPP_URL = "https://example.invalid/app"

    def tearDown(self):
        godstar.send_message = self._orig_send
        godstar.api = self._orig_api
        godstar.ADMIN_CHAT_ID = self._orig_admin
        godstar.WEBAPP_URL = self._orig_webapp
        self.tmp.cleanup()

    def _admin_query(self, data):
        return {"data": data, "id": "q1",
                "message": {"chat": {"id": 278899906}}}

    def test_forward_support_attaches_reply_button(self):
        godstar._forward_support_to_admin(self.store, 111, text="سلام")
        self.assertEqual(len(self.sent), 1)
        chat_id, text, markup = self.sent[0]
        self.assertEqual(chat_id, "278899906")
        self.assertIn("سلام", text)
        buttons = markup["inline_keyboard"][0]
        self.assertEqual(buttons[0]["text"], "✏️ پاسخ")
        self.assertEqual(buttons[0]["callback_data"], "support_reply:111")

    def test_forward_support_photo_attaches_reply_button(self):
        godstar._forward_support_to_admin(self.store, 111, text="فیش",
                                          photo_file_id="FILE1")
        photos = [d for m, d in self.api_calls if m == "sendPhoto"]
        self.assertEqual(len(photos), 1)
        import json as _json
        buttons = _json.loads(photos[0]["reply_markup"])["inline_keyboard"][0]
        self.assertEqual(buttons[0]["callback_data"], "support_reply:111")

    def test_support_reply_button_prompts_admin(self):
        godstar.handle_callback(self.store, self._admin_query("support_reply:111"))
        prompt = self.store.state.get("pending_prompt", {}).get("278899906")
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt["cmd"], "/support_reply 111")
        self.assertTrue(any("جوابت به مشتری را تایپ کن" in t
                            for _, t, _ in self.sent))

    def test_support_reply_sends_to_customer(self):
        godstar.handle_callback(self.store, self._admin_query("support_reply:111"))
        msg = {"chat": {"id": 278899906}, "from": {"id": 278899906},
               "text": "ممنون، بررسی می‌کنیم"}
        godstar.handle_message(self.store, msg)
        to_customer = [s for s in self.sent if s[0] == 111]
        self.assertEqual(len(to_customer), 1)
        self.assertEqual(to_customer[0][1], "ممنون، بررسی می‌کنیم")
        # جواب با دکمه‌ی فروشگاه می‌رسد تا مشتری به مینی‌اپ برگردد
        import json as _json
        markup = to_customer[0][2]
        self.assertIn("باز کردن فروشگاه", _json.dumps(markup, ensure_ascii=False))
        self.assertTrue(any("✅ جواب به مشتری فرستاده شد" in t
                            for c, t, _ in self.sent if c == 278899906))

    def test_support_reply_cancel(self):
        godstar.handle_callback(self.store, self._admin_query("support_reply:111"))
        msg = {"chat": {"id": 278899906}, "from": {"id": 278899906},
               "text": "لغو"}
        godstar.handle_message(self.store, msg)
        self.assertEqual([s for s in self.sent if s[0] == 111], [])
        self.assertTrue(any("لغو شد" in t for _, t, _ in self.sent))

    def test_support_reply_non_admin_rejected(self):
        query = {"data": "support_reply:111", "id": "q1",
                 "message": {"chat": {"id": 111}}}
        godstar.handle_callback(self.store, query)
        self.assertNotIn("278899906", self.store.state.get("pending_prompt", {}))
        self.assertTrue(any("فقط ادمین" in (d.get("text") or "")
                            for m, d in self.api_calls
                            if m == "answerCallbackQuery"))


class PayFromWalletTest(unittest.TestCase):
    """پرداخت از کیف پول: با تیک خوردن، روش‌های دستی (آدرس + رسید)
    پنهان می‌شوند و ثبت بدون رسید انجام می‌شود."""

    def setUp(self):
        self.root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(self.root, "webapp", "index.html"),
                  encoding="utf-8") as f:
            self.html = f.read()
        with open(os.path.join(self.root, "webapp", "app.js"),
                  encoding="utf-8") as f:
            self.js = f.read()

    def test_manual_payment_wrapped_and_before_submit(self):
        self.assertIn('id="pay-manual-wrap"', self.html)
        segment = self.html[self.html.index('id="pay-manual-wrap"'):
                            self.html.index('id="pay-submit"')]
        for needle in ("آدرس کیف پول", "ارسال رسید", 'id="pay-receipt"',
                       'id="pay-upload-wrap"', 'id="pay-hash-wrap"'):
            self.assertIn(needle, segment)

    def test_render_payment_hides_manual_when_wallet_checked(self):
        # v1.0.4: زدن تیک کیف پول، روش‌های دستی را پنهان می‌کند حتی اگر
        # موجودی کم باشد؛ در آن صورت هشدار کمبود موجودی نشان داده می‌شود.
        self.assertIn(
            '$("pay-manual-wrap").classList.toggle("hidden", walletPay);',
            self.js)
        self.assertIn("کافی نیست", self.js)
        self.assertNotIn("walletPay && walletOk)", self.js)

    def test_wallet_pay_submits_without_receipt(self):
        self.assertIn("receipt_kind: walletPay ? null", self.js)


class ReceiptPhotoAndSupportContextTest(unittest.TestCase):
    """v1.0.5: عکس رسید از مینی‌اپ با بایت واقعی به ادمین می‌رسد؛ جواب
    پشتیبانی با زمینه‌ی پیام اصلی (متن/عکس) به مشتری می‌رسد؛ سفارش
    دوباره از راه پی‌لود وب‌اپ هم سفارش تازه می‌سازد."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(state_file=os.path.join(self.tmp.name, "state.json"))
        self.store.load()
        godstar.ensure_defaults(self.store)
        self.sent = []
        self.api_calls = []
        self.photo_calls = []
        self._orig_send = godstar.send_message
        self._orig_api = godstar.api
        self._orig_photo = godstar.send_photo_bytes
        self._orig_admin = godstar.ADMIN_CHAT_ID
        godstar.send_message = lambda chat_id, text, reply_markup=None: self.sent.append(
            (chat_id, text, reply_markup)) or {"message_id": 1}
        godstar.api = lambda method, **data: self.api_calls.append(
            (method, data)) or {"message_id": 99}
        godstar.send_photo_bytes = lambda chat_id, photo_bytes, caption=None, reply_markup=None: self.photo_calls.append(
            (chat_id, photo_bytes, caption)) or {"message_id": 55,
                                                 "photo": [{"file_id": "SMALL"},
                                                           {"file_id": "BIGFILE"}]}
        godstar.ADMIN_CHAT_ID = "278899906"

    def tearDown(self):
        godstar.send_message = self._orig_send
        godstar.api = self._orig_api
        godstar.send_photo_bytes = self._orig_photo
        godstar.ADMIN_CHAT_ID = self._orig_admin
        self.tmp.cleanup()

    def _webapp_msg(self, chat_id, payload):
        import json as _json
        return {"chat": {"id": chat_id}, "from": {"id": chat_id, "username": "u"},
                "web_app_data": {"data": _json.dumps(payload, ensure_ascii=False)}}

    def _data_url(self, raw):
        import base64 as _b64
        return "data:image/jpeg;base64," + _b64.b64encode(raw).decode()

    def test_receipt_photo_data_uploaded_to_admin(self):
        # عکس رسید کوچک‌شده با بایت واقعی به ادمین می‌رسد و file_id آن
        # روی سفارش می‌نشیند؛ تاییدیه از مشتری عکس در چت نمی‌خواهد و
        # اعلان ادمین عکس را دوباره نمی‌فرستد.
        raw = b"\xff\xd8\xff\xe0fake-receipt-jpeg"
        msg = self._webapp_msg(111, {"type": "order", "service": "members",
                                     "details": {"count": 5000, "channel_id": "@GodNews24"},
                                     "receipt_kind": "photo",
                                     "receipt_file_name": "r.jpg",
                                     "receipt_photo_data": self._data_url(raw)})
        godstar.handle_web_app_data(self.store, msg)
        order = self.store.get_order("GS-1001")
        self.assertIsNotNone(order)
        self.assertEqual(order["receipt_file_id"], "BIGFILE")
        self.assertEqual(len(self.photo_calls), 1)
        self.assertEqual(self.photo_calls[0][0], "278899906")
        self.assertEqual(self.photo_calls[0][1], raw)
        self.assertIn("GS-1001", self.photo_calls[0][2])
        self.assertFalse(any(m == "sendPhoto" for m, _ in self.api_calls))
        user_texts = [t for cid, t, _ in self.sent if cid == 111]
        self.assertFalse(any("عکس فیش را همین‌جا" in t for t in user_texts))

    def test_receipt_photo_data_invalid_falls_back(self):
        # receipt_photo_data خراب → رفتار قبلی: تاییدیه عکس را در چت
        # می‌خواهد؛ چیزی خراب نمی‌شود.
        msg = self._webapp_msg(111, {"type": "order", "service": "members",
                                     "details": {"count": 5000, "channel_id": "@GodNews24"},
                                     "receipt_kind": "photo",
                                     "receipt_file_name": "r.jpg",
                                     "receipt_photo_data": "not-a-data-url!!!"})
        godstar.handle_web_app_data(self.store, msg)
        self.assertIsNotNone(self.store.get_order("GS-1001"))
        self.assertEqual(self.photo_calls, [])
        user_texts = [t for cid, t, _ in self.sent if cid == 111]
        self.assertTrue(any("عکس فیش را همین‌جا" in t for t in user_texts))

    def test_support_reply_with_photo_context(self):
        # پیام پشتیبانی با عکس → جواب ادمین با همان عکس به مشتری برمی‌گردد
        # تا بداند جواب، جوابِ کدام عکس است.
        raw = b"\xff\xd8\xff\xe0fake-support-jpeg"
        msg = self._webapp_msg(111, {"type": "support_message", "to": "support",
                                     "text": "این خطا را می‌گیرم",
                                     "has_photo": True,
                                     "photo_file_name": "err.jpg",
                                     "photo_data": self._data_url(raw)})
        godstar.handle_web_app_data(self.store, msg)
        thread = self.store.support_thread_for(55)
        self.assertIsNotNone(thread)
        self.assertEqual(thread["orig_photo_file_id"], "BIGFILE")
        query = {"data": "support_reply:111", "id": "q1",
                 "message": {"chat": {"id": 278899906}, "message_id": 55}}
        godstar.handle_callback(self.store, query)
        prompt = self.store.state.get("pending_prompt", {}).get("278899906")
        self.assertEqual(prompt["cmd"], "/support_reply 111 55")
        admin_msg = {"chat": {"id": 278899906}, "from": {"id": 278899906},
                     "text": "ممنون، بررسی می‌کنیم"}
        godstar.handle_message(self.store, admin_msg)
        photos = [d for m, d in self.api_calls if m == "sendPhoto" and d.get("chat_id") == 111]
        self.assertEqual(len(photos), 1)
        self.assertEqual(photos[0]["photo"], "BIGFILE")
        self.assertIn("ممنون، بررسی می‌کنیم", photos[0]["caption"])
        self.assertIn("در جواب عکسی که فرستادی", photos[0]["caption"])

    def test_support_reply_with_text_context(self):
        # پیام متنی → جواب با نقل‌قول متن اصلی می‌رسد.
        godstar._forward_support_to_admin(self.store, 111, text="سلام، خطا می‌گیرم")
        query = {"data": "support_reply:111", "id": "q1",
                 "message": {"chat": {"id": 278899906}, "message_id": 1}}
        godstar.handle_callback(self.store, query)
        admin_msg = {"chat": {"id": 278899906}, "from": {"id": 278899906},
                     "text": "درستش می‌کنیم"}
        godstar.handle_message(self.store, admin_msg)
        to_customer = [s for s in self.sent if s[0] == 111]
        self.assertEqual(len(to_customer), 1)
        self.assertIn("«سلام، خطا می‌گیرم»", to_customer[0][1])
        self.assertIn("درستش می‌کنیم", to_customer[0][1])

    def test_support_reply_without_context(self):
        # بدون نخ (مثلاً بعد از ری‌استارت) جواب همان متن ساده است.
        godstar.handle_admin_command(self.store, 278899906,
                                     "/support_reply 111 ممنون، بررسی می‌کنیم")
        to_customer = [s for s in self.sent if s[0] == 111]
        self.assertEqual(len(to_customer), 1)
        self.assertEqual(to_customer[0][1], "ممنون، بررسی می‌کنیم")

    def test_support_confirmation_echoes_photo(self):
        # تایید پیام پشتیبانی، خود عکس فرستاده‌شده را در چت مشتری نشان
        # می‌دهد تا بداند چه عکسی فرستاده است.
        raw = b"\xff\xd8\xff\xe0fake-support-jpeg"
        msg = self._webapp_msg(111, {"type": "support_message", "to": "support",
                                     "text": "این خطا",
                                     "has_photo": True,
                                     "photo_file_name": "err.jpg",
                                     "photo_data": self._data_url(raw)})
        godstar.handle_web_app_data(self.store, msg)
        # یک بار به ادمین، یک بار برگشت به خود مشتری در تاییدیه
        self.assertEqual(len(self.photo_calls), 2)
        self.assertEqual(self.photo_calls[1][0], 111)
        self.assertEqual(self.photo_calls[1][1], raw)
        self.assertIn("عکسی که به پشتیبانی فرستادی", self.photo_calls[1][2])

    def test_largest_photo_file_id(self):
        self.assertEqual(godstar._largest_photo_file_id(None), "")
        self.assertEqual(godstar._largest_photo_file_id({}), "")
        res = {"photo": [{"file_id": "A"}, {"file_id": "B"}]}
        self.assertEqual(godstar._largest_photo_file_id(res), "B")

    def test_reorder_via_webapp_payload_creates_order(self):
        # سفارش دوباره از راه پی‌لود وب‌اپ (همان مسیر دکمه‌ی «دوباره»
        # در مینی‌اپ) سفارش تازه می‌سازد.
        msg = self._webapp_msg(111, {"type": "order", "service": "members",
                                     "details": {"count": 8000, "channel_id": "@GodNews24"},
                                     "tx_hash": "abc123"})
        godstar.handle_web_app_data(self.store, msg)
        godstar.reject_order(self.store, "GS-1001", "تست")
        msg2 = self._webapp_msg(111, {"type": "reorder", "order_no": "GS-1001"})
        godstar.handle_web_app_data(self.store, msg2)
        new = self.store.get_order("GS-1002")
        self.assertIsNotNone(new)
        self.assertEqual(new["service"], "members")
        self.assertEqual(new["status"], "pending")
        self.assertTrue(any("سفارش دوباره ثبت شد" in t for _, t, _ in self.sent))


class AdMediaAndReorderLogTest(unittest.TestCase):
    """v1.0.6: عکس/فیلم تبلیغ سفارش Ads با بایت واقعی به ادمین می‌رسد؛
    خط «مشتری در چت می‌فرستد» وقتی رسید رسیده پنهان می‌شود؛ سفارش
    دوباره در لاگ ردی برمی‌دارد (درخواست + نتیجه + شماره سفارش در خطا)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(state_file=os.path.join(self.tmp.name, "state.json"))
        self.store.load()
        godstar.ensure_defaults(self.store)
        self.sent = []
        self.photo_calls = []
        self.video_calls = []
        self._orig_send = godstar.send_message
        self._orig_api = godstar.api
        self._orig_photo = godstar.send_photo_bytes
        self._orig_video = godstar.send_video_bytes
        self._orig_admin = godstar.ADMIN_CHAT_ID
        godstar.send_message = lambda chat_id, text, reply_markup=None: self.sent.append(
            (chat_id, text, reply_markup)) or {"message_id": 1}
        godstar.api = lambda method, **data: {"message_id": 99}
        godstar.send_photo_bytes = lambda chat_id, photo_bytes, caption=None, reply_markup=None: self.photo_calls.append(
            (chat_id, photo_bytes, caption)) or {"message_id": 55,
                                                 "photo": [{"file_id": "SMALL"},
                                                           {"file_id": "BIGFILE"}]}
        godstar.send_video_bytes = lambda chat_id, video_bytes, caption=None, reply_markup=None: self.video_calls.append(
            (chat_id, video_bytes, caption)) or {"message_id": 56,
                                                 "video": {"file_id": "VIDFILE"}}
        godstar.ADMIN_CHAT_ID = "278899906"

    def tearDown(self):
        godstar.send_message = self._orig_send
        godstar.api = self._orig_api
        godstar.send_photo_bytes = self._orig_photo
        godstar.send_video_bytes = self._orig_video
        godstar.ADMIN_CHAT_ID = self._orig_admin
        self.tmp.cleanup()

    def _webapp_msg(self, chat_id, payload):
        import json as _json
        return {"chat": {"id": chat_id}, "from": {"id": chat_id, "username": "u"},
                "web_app_data": {"data": _json.dumps(payload, ensure_ascii=False)}}

    def _data_url(self, raw, mime="image/jpeg"):
        import base64 as _b64
        return "data:" + mime + ";base64," + _b64.b64encode(raw).decode()

    def _admin_texts(self):
        return [t for c, t, _ in self.sent if c == "278899906"]

    def _ads_order(self, media, media_data=None):
        payload = {"type": "order", "service": "ads", "views": 100000,
                   "media": media, "ad_text": "تبلیغ تست",
                   "channel_id": "@GodNews24", "tx_hash": "abc123"}
        if media_data is not None:
            payload["ad_media_data"] = media_data
            payload["ad_media_kind"] = media
        godstar.handle_web_app_data(
            self.store, self._webapp_msg(111, payload))
        return self.store.get_order("GS-1001")

    def test_ad_photo_reaches_admin(self):
        raw = b"\xff\xd8\xff\xe0fake-ad-photo"
        order = self._ads_order("photo", self._data_url(raw))
        self.assertIsNotNone(order)
        self.assertEqual(len(self.photo_calls), 1)
        self.assertEqual(self.photo_calls[0][0], "278899906")
        self.assertEqual(self.photo_calls[0][1], raw)
        self.assertIn("GS-1001", self.photo_calls[0][2])
        self.assertTrue(order["details"].get("ad_media_sent"))
        self.assertNotIn("ad_media_file_id", order["details"])
        self.assertTrue(any("رسانه‌ی تبلیغ: عکس ارسال شد" in t
                            for t in self._admin_texts()))

    def test_ad_video_reaches_admin(self):
        raw = b"\x00\x00\x00\x18ftypmp42fake-ad-video"
        order = self._ads_order(
            "video", self._data_url(raw, mime="video/mp4"))
        self.assertIsNotNone(order)
        self.assertEqual(len(self.video_calls), 1)
        self.assertEqual(self.video_calls[0][0], "278899906")
        self.assertEqual(self.video_calls[0][1], raw)
        self.assertIn("GS-1001", self.video_calls[0][2])
        self.assertTrue(order["details"].get("ad_media_sent"))
        self.assertNotIn("ad_media_file_id", order["details"])
        self.assertTrue(any("رسانه‌ی تبلیغ: ویدیو ارسال شد" in t
                            for t in self._admin_texts()))

    def test_ad_media_missing_falls_back_to_chat_note(self):
        order = self._ads_order("photo")
        self.assertIsNotNone(order)
        self.assertEqual(self.photo_calls, [])
        self.assertEqual(self.video_calls, [])
        self.assertTrue(any("عکس تبلیغ را همین‌جا" in t
                            for _, t, _ in self.sent))
        self.assertTrue(any("رسانه‌ی تبلیغ: عکس — مشتری در چت می‌فرستد" in t
                            for t in self._admin_texts()))

    def test_receipt_chat_note_hidden_when_photo_delivered(self):
        raw = b"\xff\xd8\xff\xe0fake-receipt-jpeg"
        msg = self._webapp_msg(111, {"type": "order", "service": "members",
                                     "details": {"count": 5000, "channel_id": "@GodNews24"},
                                     "receipt_kind": "photo",
                                     "receipt_file_name": "r.jpg",
                                     "receipt_photo_data": self._data_url(raw)})
        godstar.handle_web_app_data(self.store, msg)
        self.assertTrue(any("رسید: عکس ارسال شده است" in t
                            for t in self._admin_texts()))
        self.assertFalse(any("مشتری در چت می‌فرستد" in t
                             for t in self._admin_texts()))

    def test_reorder_logs_request_and_result(self):
        msg = self._webapp_msg(111, {"type": "order", "service": "members",
                                     "details": {"count": 8000, "channel_id": "@GodNews24"},
                                     "tx_hash": "abc123"})
        godstar.handle_web_app_data(self.store, msg)
        godstar.reject_order(self.store, "GS-1001", "تست")
        import io as _io
        from contextlib import redirect_stdout as _redirect
        buf = _io.StringIO()
        with _redirect(buf):
            godstar.handle_web_app_data(
                self.store,
                self._webapp_msg(111, {"type": "reorder", "order_no": "GS-1001"}))
        out = buf.getvalue()
        self.assertIn("درخواست GS-1001", out)
        self.assertIn("GS-1001 → GS-1002", out)
        self.assertIsNotNone(self.store.get_order("GS-1002"))

    def test_reorder_error_log_names_order(self):
        import io as _io
        from contextlib import redirect_stdout as _redirect
        buf = _io.StringIO()
        with _redirect(buf):
            godstar.handle_web_app_data(
                self.store,
                self._webapp_msg(111, {"type": "reorder", "order_no": "GS-9999"}))
        out = buf.getvalue()
        self.assertIn("type=reorder order_no=GS-9999", out)
        self.assertTrue(any("⚠️" in t for _, t, _ in self.sent))

    def test_decode_data_url(self):
        self.assertEqual(godstar._decode_data_url(
            self._data_url(b"hello")), b"hello")
        self.assertIsNone(godstar._decode_data_url(None))
        self.assertIsNone(godstar._decode_data_url("not-base64!!!"))
        self.assertIsNone(godstar._decode_data_url(
            self._data_url(b"x" * (6 * 1024 * 1024))))
        # _decode_photo_data هنوز همان رفتار قبلی را دارد
        self.assertEqual(godstar._decode_photo_data(
            self._data_url(b"\xff\xd8\xff\xe0x")), b"\xff\xd8\xff\xe0x")


class AdminUsersListTest(unittest.TestCase):
    """v1.0.6: دستور /users لیست کاربران را با آیدی نشان می‌دهد؛ رسیدی
    که مشتری در چت می‌فرستد هم نام و آیدی فرستنده را به ادمین می‌گوید."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(state_file=os.path.join(self.tmp.name, "state.json"))
        self.store.load()
        godstar.ensure_defaults(self.store)
        self.sent = []
        self.api_calls = []
        self._orig_send = godstar.send_message
        self._orig_api = godstar.api
        self._orig_admin = godstar.ADMIN_CHAT_ID
        godstar.send_message = lambda chat_id, text, reply_markup=None: self.sent.append(
            (chat_id, text, reply_markup)) or {"message_id": 1}
        godstar.api = lambda method, **data: self.api_calls.append(
            (method, data)) or {"message_id": 99}
        godstar.ADMIN_CHAT_ID = "278899906"

    def tearDown(self):
        godstar.send_message = self._orig_send
        godstar.api = self._orig_api
        godstar.ADMIN_CHAT_ID = self._orig_admin
        self.tmp.cleanup()

    def test_users_command_lists_users_with_ids(self):
        alice = self.store.ensure_user(111, "alice", "آلیس")
        alice["completed_orders"] = 3
        alice["wallet_balance"] = "5"
        self.store.ensure_user(222, "bob", "باب")
        godstar.handle_admin_command(self.store, 278899906, "/users")
        to_admin = [t for c, t, _ in self.sent if c == 278899906]
        self.assertEqual(len(to_admin), 1)
        text = to_admin[0]
        self.assertIn("2 نفر", text)
        self.assertIn("آلیس", text)
        self.assertIn("111", text)
        self.assertIn("باب", text)
        self.assertIn("222", text)
        self.assertIn("3 سفارش", text)

    def test_users_command_empty(self):
        godstar.handle_admin_command(self.store, 278899906, "/users")
        self.assertTrue(any("هنوز کسی" in t for _, t, _ in self.sent))

    def test_list_users_newest_first(self):
        old = self.store.ensure_user(111, "old", "قدیمی")
        old["created_at"] = "2026-11-25T10:00:00"
        new = self.store.ensure_user(222, "new", "تازه")
        new["created_at"] = "2026-11-26T10:00:00"
        users = self.store.list_users()
        self.assertEqual([u["chat_id"] for u in users], [222, 111])

    def test_receipt_photo_forward_includes_sender_id(self):
        self.store.ensure_user(111, "testuser", "تست")
        godstar.place_order(self.store, 111, "members",
                            {"count": 1000, "channel_id": "@GodNews24"},
                            defer_receipt=True)
        ok = godstar.attach_receipt(
            self.store, 111,
            {"photo": [{"file_id": "SMALL"}, {"file_id": "PHOTOFILE"}],
             "text": ""})
        self.assertTrue(ok)
        photos = [d for m, d in self.api_calls if m == "sendPhoto"]
        self.assertEqual(len(photos), 1)
        self.assertEqual(photos[0]["photo"], "PHOTOFILE")
        self.assertIn("(111)", photos[0]["caption"])
        self.assertIn("تست", photos[0]["caption"])

    def test_receipt_hash_forward_includes_sender_id(self):
        self.store.ensure_user(111, "testuser", "تست")
        godstar.place_order(self.store, 111, "members",
                            {"count": 1000, "channel_id": "@GodNews24"},
                            defer_receipt=True)
        ok = godstar.attach_receipt(
            self.store, 111, {"text": "0x" + "ab" * 32})
        self.assertTrue(ok)
        to_admin = [t for c, t, _ in self.sent if c == "278899906"]
        self.assertTrue(any("(111)" in t and "تست" in t for t in to_admin))

    def test_users_command_wired_into_menu_and_help(self):
        self.assertIn("/users", godstar.ADMIN_COMMANDS)
        self.assertEqual(godstar.ADMIN_KEYBOARD_COMMANDS.get("👥 کاربران"), "/users")
        self.assertIn("👥 کاربران", godstar.ADMIN_COMMANDS_TEXT)
        self.assertIn("/users", godstar.welcome_text(True))
        self.assertNotIn("/users", godstar.welcome_text(False))


class WalletPayAndKeyboardTest(unittest.TestCase):
    """v1.0.7: پرداخت از کیف پول (کلید پی‌لود)، ثبت مستقیم ربات
    اختصاصی، و برداشته شدن دکمه‌های تایید/رد بعد از تصمیم ادمین."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(state_file=os.path.join(self.tmp.name, "state.json"))
        self.store.load()
        godstar.ensure_defaults(self.store)
        self.sent = []
        self.api_calls = []
        self._orig_send = godstar.send_message
        self._orig_api = godstar.api
        self._orig_admin = godstar.ADMIN_CHAT_ID
        godstar.send_message = lambda chat_id, text, reply_markup=None: self.sent.append(
            (chat_id, text, reply_markup)) or {"message_id": 1}
        godstar.api = lambda method, **data: self.api_calls.append(
            (method, data)) or {"message_id": 99}
        godstar.ADMIN_CHAT_ID = "278899906"

    def tearDown(self):
        godstar.send_message = self._orig_send
        godstar.api = self._orig_api
        godstar.ADMIN_CHAT_ID = self._orig_admin
        self.tmp.cleanup()

    def _webapp_msg(self, chat_id, payload):
        import json as _json
        return {"chat": {"id": chat_id}, "from": {"id": chat_id, "username": "u"},
                "web_app_data": {"data": _json.dumps(payload, ensure_ascii=False)}}

    def _wallet_order_payload(self, **extra):
        payload = {"type": "order", "service": "members",
                   "title": "خرید ممبر آپلودری",
                   "channel_id": "@GodNews24",
                   "quantity": 1000,
                   "amount_usdt": 3, "payable_usdt": 3}
        payload.update(extra)
        return payload

    def test_wallet_order_via_paid_by_wallet_key(self):
        # دقیقاً همان پی‌لودی که مینی‌اپ ۱٫۰٫۷ می‌فرستد
        self.store.credit_wallet(111, 100, note="test")
        godstar.handle_web_app_data(
            self.store, self._webapp_msg(111, self._wallet_order_payload(
                paid_by_wallet=True)))
        order = self.store.get_order("GS-1001")
        self.assertIsNotNone(order)
        self.assertTrue(order["paid_by_wallet"])
        self.assertEqual(self.store.wallet_balance(111), 97.0)
        self.assertFalse(any("رسید" in t for _, t, _ in self.sent))

    def test_wallet_order_via_legacy_pay_from_wallet_key(self):
        # نسخه‌های قدیمیِ کش‌شده‌ی مینی‌اپ کلید pay_from_wallet
        # می‌فرستادند و ربات آن را نمی‌دید — رگرسیون خطای «رسید لازم است»
        self.store.credit_wallet(111, 100, note="test")
        godstar.handle_web_app_data(
            self.store, self._webapp_msg(111, self._wallet_order_payload(
                pay_from_wallet=True)))
        order = self.store.get_order("GS-1001")
        self.assertIsNotNone(order)
        self.assertTrue(order["paid_by_wallet"])
        self.assertEqual(self.store.wallet_balance(111), 97.0)

    def test_wallet_order_without_receipt_still_rejected(self):
        godstar.handle_web_app_data(
            self.store, self._webapp_msg(111, self._wallet_order_payload()))
        self.assertIsNone(self.store.get_order("GS-1001"))
        self.assertTrue(any("رسید" in t for _, t, _ in self.sent))

    def test_custom_bot_order_via_payload_no_receipt(self):
        # ربات اختصاصی مبلغ صفر دارد؛ مستقیم ثبت می‌شود، بدون رسید
        godstar.handle_web_app_data(
            self.store, self._webapp_msg(111, {
                "type": "order", "service": "bot",
                "title": "ربات تلگرام",
                "sub": "ربات اختصاصی — @GodNews24",
                "channel_id": "@GodNews24",
                "amount_usdt": 0, "payable_usdt": 0,
                "paid_by_wallet": False,
                "bot_type": "custom", "plan_months": None,
                "plan_price_usdt": None, "notes": "ربات فروشگاه می‌خواهم"}))
        order = self.store.get_order("GS-1001")
        self.assertIsNotNone(order)
        self.assertEqual(order["amount"], "0.00")
        self.assertEqual(order["status"], "pending")
        self.assertTrue(any("سفارش شما ثبت شد" in t for _, t, _ in self.sent))

    def _pending_order(self):
        return godstar.place_order(
            self.store, 111, "members",
            {"count": 1000, "channel_id": "@GodNews24"}, tx_hash="abc")

    def _admin_query(self, data, message_id):
        return {"data": data, "id": "q1",
                "message": {"message_id": message_id,
                            "chat": {"id": 278899906}}}

    def _keyboard_clears(self):
        return [d for m, d in self.api_calls
                if m == "editMessageReplyMarkup"]

    def test_approve_callback_sets_progress_keyboard(self):
        # (v1.0.8) بعد از تایید سفارش خدمتی، به‌جای برداشتن دکمه‌ها،
        # دکمه‌ی «مرحله‌ی بعد» می‌آید.
        self._pending_order()
        godstar.handle_callback(
            self.store, self._admin_query("approve:GS-1001", 55))
        self.assertEqual(self.store.get_order("GS-1001")["status"], "confirmed")
        clears = self._keyboard_clears()
        self.assertEqual(len(clears), 1)
        self.assertEqual(clears[0]["message_id"], 55)
        import json as _json
        kb = _json.loads(clears[0]["reply_markup"])
        btn = kb["inline_keyboard"][0][0]
        self.assertEqual(btn["callback_data"], "progress:GS-1001")
        self.assertIn("در حال انجام", btn["text"])

    def test_approve_wallet_charge_clears_keyboard(self):
        # شارژ کیف پول با تایید تکمیل است؛ مرحله ندارد، پس دکمه‌ها
        # برداشته می‌شوند.
        godstar.place_order(
            self.store, 111, "wallet_charge", {"amount": 20}, tx_hash="abc")
        godstar.handle_callback(
            self.store, self._admin_query("approve:GS-1001", 55))
        clears = self._keyboard_clears()
        self.assertEqual(len(clears), 1)
        self.assertEqual(clears[0]["reply_markup"],
                         '{"inline_keyboard": []}')

    def test_reject_callback_clears_notification_keyboard(self):
        self._pending_order()
        godstar.handle_callback(
            self.store, self._admin_query("reject:GS-1001", 55))
        # پیام انتخاب دلیل فرستاده شد و دکمه‌های اعلان اصلی برداشته شدند
        self.assertTrue(any("دلیل رد را انتخاب کن" in t for _, t, _ in self.sent))
        clears = self._keyboard_clears()
        self.assertEqual(len(clears), 1)
        self.assertEqual(clears[0]["message_id"], 55)

    def test_reject_reason_callback_clears_picker_keyboard(self):
        self._pending_order()
        godstar.handle_callback(
            self.store, self._admin_query("reject_reason:GS-1001:1", 77))
        self.assertEqual(self.store.get_order("GS-1001")["status"], "rejected")
        clears = self._keyboard_clears()
        self.assertEqual(len(clears), 1)
        self.assertEqual(clears[0]["message_id"], 77)

    def test_reject_cancel_clears_picker_keyboard(self):
        self._pending_order()
        godstar.handle_callback(
            self.store, self._admin_query("reject_cancel:GS-1001", 88))
        clears = self._keyboard_clears()
        self.assertEqual(len(clears), 1)
        self.assertEqual(clears[0]["message_id"], 88)

    def test_typed_reject_reason_clears_picker_keyboard(self):
        self._pending_order()
        godstar.handle_callback(
            self.store, self._admin_query("reject_reason_type:GS-1001", 66))
        admin_msg = {"chat": {"id": 278899906},
                     "from": {"id": 278899906}, "text": "دلیل تستی"}
        godstar.handle_message(self.store, admin_msg)
        self.assertEqual(self.store.get_order("GS-1001")["status"], "rejected")
        clears = self._keyboard_clears()
        self.assertEqual(len(clears), 1)
        self.assertEqual(clears[0]["message_id"], 66)


class GetUpdatesTest(unittest.TestCase):
    def setUp(self):
        self._orig_get = godstar.requests.get
        self._orig_sleep = godstar.time.sleep
        self.sleeps = []
        godstar.time.sleep = lambda s: self.sleeps.append(s)

    def tearDown(self):
        godstar.requests.get = self._orig_get
        godstar.time.sleep = self._orig_sleep

    def _fake_error(self, error_code, retry_after=None):
        params = {} if retry_after is None else {"retry_after": retry_after}

        class Resp:
            def json(self):
                return {"ok": False, "error_code": error_code,
                        "description": "Too Many Requests",
                        "parameters": params}

        godstar.requests.get = lambda *a, **k: Resp()

    def test_429_sleeps_retry_after(self):
        self._fake_error(429, 32)
        self.assertEqual(godstar.api_get_updates(0), [])
        self.assertEqual(self.sleeps, [32])

    def test_429_default_wait_without_retry_after(self):
        self._fake_error(429, None)
        self.assertEqual(godstar.api_get_updates(0), [])
        self.assertEqual(self.sleeps, [5])

    def test_other_error_does_not_sleep(self):
        self._fake_error(403, None)
        self.assertEqual(godstar.api_get_updates(0), [])
        self.assertEqual(self.sleeps, [])


if __name__ == "__main__":
    unittest.main()
