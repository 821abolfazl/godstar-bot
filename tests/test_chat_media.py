# تست‌های v1.0.13: رسانه‌های چت — رسید و عکس تبلیغ و عکس پشتیبانی.
# بازبینی ۱٫۰٫۱۳ سه سوراخ بی‌صدا در همین مسیر پیدا کرد (هم‌خانواده‌ی
# باگ ویدیوی تبلیغ ۱٫۰٫۱۲ که فقط msg.video را می‌خواند):
# ۱) رسیدِ فرستاده‌شده به‌صورت فایل (document عکسی) نادیده گرفته
#    می‌شد و سفارش برای همیشه در انتظار رسید می‌ماند؛
# ۲) رسیدِ فرستاده‌شده در حالت پشتیبانی به‌جای سفارش، به پشتیبانی
#    می‌رفت؛ ۳) عکس تبلیغِ فرستاده‌شده در چت اصلاً گیرنده‌ای نداشت —
#    ربات به مشتری می‌گفت عکس را در چت بفرست ولی handle_message فقط
#    ویدیو را می‌چسباند (عکس یا به‌اشتباه رسید می‌شد یا گم می‌شد).
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import Store
import bot as godstar


class ChatMediaTest(unittest.TestCase):
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
        user = self.store.ensure_user(111)
        user["username"] = "tester_x"
        user["first_name"] = "Test"

    def tearDown(self):
        godstar.send_message = self._orig_send
        godstar.api = self._orig_api
        godstar.ADMIN_CHAT_ID = self._orig_admin
        self.tmp.cleanup()

    # ---------- سازنده‌های کمکی ----------
    def _members_order_needing_receipt(self, chat_id=111):
        # سفارش وب‌اپی با رسید عکسی: عکس فیش بعداً در چت می‌رسد.
        return godstar.place_order(
            self.store, chat_id, "members",
            {"channel_id": "@GodNews24", "count": 1000},
            receipt_kind="photo")

    def _ads_photo_order(self, chat_id=111, with_receipt=True):
        kw = {"tx_hash": "abc"} if with_receipt else {"receipt_kind": "photo"}
        return godstar.place_order(
            self.store, chat_id, "ads",
            {"views": 10000, "media": "photo", "channel_id": "@GodNews24",
             "ad_text": "t"}, **kw)

    def _msg(self, chat_id=111, **extra):
        msg = {"message_id": 7, "chat": {"id": chat_id}}
        msg.update(extra)
        return msg

    def _support_captions(self):
        out = []
        for method, data in self.api_calls:
            if method in ("sendPhoto", "sendVideo"):
                out.append(data.get("caption") or "")
        return out

    # ---------- رسید ----------
    def test_receipt_photo_as_document_attached(self):
        order = self._members_order_needing_receipt()
        ok = godstar.attach_receipt(
            self.store, 111,
            {"document": {"file_id": "DOC1", "mime_type": "image/jpeg"}})
        self.assertTrue(ok)
        stored = self.store.get_order(order["order_no"])
        self.assertEqual(stored["receipt_file_id"], "DOC1")
        photos = [c for c in self.api_calls if c[0] == "sendPhoto"]
        self.assertEqual(len(photos), 1)
        self.assertEqual(photos[0][1]["photo"], "DOC1")
        self.assertIn(order["order_no"], photos[0][1]["caption"])
        self.assertTrue(any("رسید سفارش" in t and order["order_no"] in t
                            for _, t, _ in self.sent))

    def test_receipt_photo_still_works(self):
        order = self._members_order_needing_receipt()
        ok = godstar.attach_receipt(
            self.store, 111, {"photo": [{"file_id": "SMALL"}, {"file_id": "BIG"}]})
        self.assertTrue(ok)
        self.assertEqual(self.store.get_order(order["order_no"])["receipt_file_id"], "BIG")

    def test_receipt_in_support_mode_reaches_order(self):
        order = self._members_order_needing_receipt()
        self.store.set_support_mode(111, True)
        godstar.handle_message(
            self.store, self._msg(photo=[{"file_id": "P1"}]))
        stored = self.store.get_order(order["order_no"])
        self.assertEqual(stored["receipt_file_id"], "P1")
        self.assertFalse(any("پشتیبانی" in c for c in self._support_captions()))

    def test_receipt_document_in_support_mode_reaches_order(self):
        order = self._members_order_needing_receipt()
        self.store.set_support_mode(111, True)
        godstar.handle_message(
            self.store, self._msg(
                document={"file_id": "DOC9", "mime_type": "image/png"}))
        self.assertEqual(
            self.store.get_order(order["order_no"])["receipt_file_id"], "DOC9")

    # ---------- عکس تبلیغ در چت ----------
    def test_ad_photo_in_chat_attached(self):
        order = self._ads_photo_order()
        ok = godstar.attach_ad_media(
            self.store, 111, {"photo": [{"file_id": "PH1"}]})
        self.assertTrue(ok)
        stored = self.store.get_order(order["order_no"])
        self.assertTrue(stored["details"].get("ad_media_sent"))
        photos = [c for c in self.api_calls if c[0] == "sendPhoto"]
        self.assertEqual(len(photos), 1)
        self.assertEqual(photos[0][1]["photo"], "PH1")
        self.assertIn(order["order_no"], photos[0][1]["caption"])
        self.assertTrue(any("عکس تبلیغ" in t and order["order_no"] in t
                            for _, t, _ in self.sent))

    def test_ad_photo_as_document_attached(self):
        order = self._ads_photo_order()
        ok = godstar.attach_ad_media(
            self.store, 111,
            {"document": {"file_id": "DOCP", "mime_type": "image/jpeg"}})
        self.assertTrue(ok)
        self.assertTrue(
            self.store.get_order(order["order_no"])["details"].get("ad_media_sent"))

    def test_ad_photo_not_stolen_as_receipt(self):
        # سفارش هم رسید می‌خواهد هم عکس تبلیغ؛ عکس تبلیغ باید به
        # رسانه بچسبد، نه به رسید.
        order = self._ads_photo_order(with_receipt=False)
        godstar.handle_message(
            self.store, self._msg(photo=[{"file_id": "PH2"}]))
        stored = self.store.get_order(order["order_no"])
        self.assertTrue(stored["details"].get("ad_media_sent"))
        self.assertEqual(stored["receipt_file_id"], "")
        captions = self._support_captions()
        self.assertTrue(any("عکس تبلیغ" in c for c in captions))
        self.assertFalse(any("رسید سفارش" in c for c in captions))

    def test_ad_media_wrong_kind_ignored(self):
        order = godstar.place_order(
            self.store, 111, "ads",
            {"views": 10000, "media": "video", "channel_id": "@GodNews24",
             "ad_text": "t"}, tx_hash="abc")
        ok = godstar.attach_ad_media(
            self.store, 111, {"photo": [{"file_id": "PH3"}]})
        self.assertFalse(ok)
        self.assertFalse(
            self.store.get_order(order["order_no"])["details"].get("ad_media_sent"))

    # ---------- عکس پشتیبانی ----------
    def test_support_photo_as_document_forwarded(self):
        self.store.set_support_mode(111, True)
        godstar.handle_message(
            self.store, self._msg(
                document={"file_id": "SUP1", "mime_type": "image/png"}))
        photos = [c for c in self.api_calls if c[0] == "sendPhoto"]
        self.assertEqual(len(photos), 1)
        self.assertEqual(photos[0][1]["photo"], "SUP1")
        self.assertIn("پشتیبانی", photos[0][1]["caption"])

    def test_support_photo_without_order_still_support(self):
        self.store.set_support_mode(111, True)
        godstar.handle_message(
            self.store, self._msg(photo=[{"file_id": "SUP2"}]))
        photos = [c for c in self.api_calls if c[0] == "sendPhoto"]
        self.assertEqual(len(photos), 1)
        self.assertEqual(photos[0][1]["photo"], "SUP2")
        self.assertIn("پشتیبانی", photos[0][1]["caption"])


if __name__ == "__main__":
    unittest.main()
