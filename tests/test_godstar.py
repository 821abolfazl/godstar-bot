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
        self.assertTrue(code.startswith("GOD") and len(code) == 7)
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
        # دوست اولین سفارش را می‌دهد → 10٪ خودش + فعال شدن 10٪ معرف
        order = godstar.place_order(self.store, 200, "members",
                                    {"count": 5000, "channel_id": "@GodNews24"},
                                    tx_hash="abc")
        self.assertEqual(order["amount"], "13.50")
        self.assertEqual(order["discount_kind"], "referral")
        self.assertTrue(self.store.get_user(100)["referral_credit"])
        # سفارش دوم دوست دیگر تخفیف معرفی ندارد
        order2 = godstar.place_order(self.store, 200, "members",
                                     {"count": 1000, "channel_id": "@GodNews24"},
                                     tx_hash="abc")
        self.assertEqual(order2["amount"], "3.00")
        self.assertEqual(order2["discount_kind"], "")

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

    def test_setup_menu_button_opens_webapp(self):
        self.assertTrue(godstar.setup_menu_button())
        self.assertEqual(len(self.api_calls), 1)
        method, data = self.api_calls[0]
        self.assertEqual(method, "setChatMenuButton")
        import json as _json
        button = _json.loads(data["menu_button"])
        self.assertEqual(button["type"], "web_app")
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
