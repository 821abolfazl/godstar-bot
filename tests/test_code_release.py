# تست‌های v1.0.12: کد تخفیف با رد شدن سفارش نمی‌سوزد؛ فقط با تایید می‌سوزد.
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import Store
import bot as godstar


class CodeReleaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(state_file=os.path.join(self.tmp.name, "state.json"))
        self.store.load()
        godstar.ensure_defaults(self.store)
        self.store.add_code("GOD10", 10, max_uses=2)
        self.sent = []
        self._orig_send = godstar.send_message
        self._orig_api = godstar.api
        self._orig_admin = godstar.ADMIN_CHAT_ID
        godstar.send_message = lambda chat_id, text, reply_markup=None: self.sent.append(
            (chat_id, text, reply_markup)) or {"message_id": 1}
        godstar.api = lambda method, **data: {"message_id": 99}
        godstar.ADMIN_CHAT_ID = "278899906"

    def tearDown(self):
        godstar.send_message = self._orig_send
        godstar.api = self._orig_api
        godstar.ADMIN_CHAT_ID = self._orig_admin
        self.tmp.cleanup()

    def _place_with_code(self):
        return godstar.place_order(
            self.store, 111, "members",
            {"count": 1000, "channel_id": "@GodNews24"},
            discount_code="GOD10", tx_hash="abc")

    def test_rejected_order_releases_code(self):
        order = self._place_with_code()
        self.assertEqual(order.get("discount_kind"), "code")
        self.assertEqual(self.store.get_code("GOD10")["uses"], 1)
        self.assertTrue(self.store.has_used_code(111, "GOD10"))
        godstar.reject_order(self.store, order["order_no"], "رسید معتبر نیست")
        self.assertEqual(self.store.get_code("GOD10")["uses"], 0)
        self.assertFalse(self.store.has_used_code(111, "GOD10"))
        # پیام رد به مشتری می‌گوید کد آزاد شده است
        reject_texts = [t for c, t, _ in self.sent if c == 111]
        self.assertTrue(any("آزاد شد" in t for t in reject_texts))
        # مشتری دوباره می‌تواند از همان کد استفاده کند
        order2 = self._place_with_code()
        self.assertEqual(order2.get("discount_kind"), "code")
        self.assertEqual(order2["amount"], "2.70")

    def test_confirmed_order_burns_code(self):
        order = self._place_with_code()
        godstar.confirm_order(self.store, order["order_no"])
        self.assertEqual(self.store.get_code("GOD10")["uses"], 1)
        self.assertTrue(self.store.has_used_code(111, "GOD10"))
        with self.assertRaises(ValueError):
            self._place_with_code()

    def test_release_deleted_code_is_noop(self):
        order = self._place_with_code()
        self.store.del_code("GOD10")
        # رد سفارش نباید با کد حذف‌شده خطا بدهد
        godstar.reject_order(self.store, order["order_no"], "رسید معتبر نیست")
        self.assertFalse(self.store.has_used_code(111, "GOD10"))


if __name__ == "__main__":
    unittest.main()
