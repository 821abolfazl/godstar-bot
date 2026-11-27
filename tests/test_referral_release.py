# تست‌های v1.0.12: تخفیف معرفی هم فقط با تایید سفارش می‌سوزد، نه با رد شدن.
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import Store
import bot as godstar


class ReferralReleaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(state_file=os.path.join(self.tmp.name, "state.json"))
        self.store.load()
        godstar.ensure_defaults(self.store)
        self.store.ensure_user(100, "ref", "Ref")
        code = self.store.get_or_create_referral_code(100)
        status, _ = self.store.record_referral_use(code, 200)
        self.assertEqual(status, "ok")
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

    def _place_members(self, discount_code=None):
        return godstar.place_order(
            self.store, 200, "members",
            {"count": 1000, "channel_id": "@GodNews24"},
            discount_code=discount_code, tx_hash="abc")

    def test_rejected_order_does_not_burn_referral(self):
        order = self._place_members()
        self.assertEqual(order.get("discount_kind"), "referral")
        self.assertEqual(order["amount"], "2.70")
        godstar.reject_order(self.store, order["order_no"], "رسید معتبر نیست")
        self.assertFalse(self.store.get_user(200).get("referral_discount_used"))
        self.assertFalse(self.store.get_user(100).get("referral_credit"))
        self.assertFalse(any(c == 100 for c, _, _ in self.sent))
        # دوست دوباره می‌تواند از تخفیف معرفی استفاده کند
        order2 = self._place_members()
        self.assertEqual(order2.get("discount_kind"), "referral")

    def test_confirmed_order_burns_referral_and_notifies_referrer(self):
        order = self._place_members()
        godstar.confirm_order(self.store, order["order_no"])
        self.assertTrue(self.store.get_user(200).get("referral_discount_used"))
        self.assertTrue(self.store.get_user(100).get("referral_credit"))
        self.assertTrue(any(c == 100 and "دوستت با کد معرفیت" in t
                            for c, t, _ in self.sent))
        order2 = self._place_members()
        self.assertFalse(order2.get("discount_kind"))
        self.assertEqual(order2["amount"], "3.00")

    def test_two_pending_orders_cannot_both_use_referral(self):
        order = self._place_members()
        self.assertEqual(order.get("discount_kind"), "referral")
        order2 = self._place_members()
        self.assertFalse(order2.get("discount_kind"))
        self.assertEqual(order2["amount"], "3.00")

    def test_referral_not_burned_when_code_wins(self):
        self.store.add_code("GOD20", 20)
        order = self._place_members(discount_code="GOD20")
        self.assertEqual(order.get("discount_kind"), "code")
        godstar.confirm_order(self.store, order["order_no"])
        # تخفیف معرفی استفاده نشده، پس نمی‌سوزد
        self.assertFalse(self.store.get_user(200).get("referral_discount_used"))
        order2 = self._place_members()
        self.assertEqual(order2.get("discount_kind"), "referral")


if __name__ == "__main__":
    unittest.main()
