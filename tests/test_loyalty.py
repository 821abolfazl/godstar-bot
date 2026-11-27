# تست‌های v1.0.11: تخفیف وفاداری هر ۳ سفارش تکمیل‌شده یک بار مصرف می‌شود.
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import Store
import bot as godstar
from pricing import loyalty_eligible


class LoyaltyCycleTest(unittest.TestCase):
    def test_eligible_boundaries(self):
        self.assertFalse(loyalty_eligible(0, 0))
        self.assertFalse(loyalty_eligible(2, 0))
        self.assertTrue(loyalty_eligible(3, 0))
        self.assertTrue(loyalty_eligible(4, 0))
        self.assertFalse(loyalty_eligible(4, 1))
        self.assertFalse(loyalty_eligible(5, 1))
        self.assertTrue(loyalty_eligible(6, 1))
        self.assertFalse(loyalty_eligible(7, 2))
        self.assertTrue(loyalty_eligible(9, 2))

    def test_old_calls_still_work(self):
        # امضای قدیمی با یک آرگومان هم باید درست کار کند.
        self.assertTrue(loyalty_eligible(3))
        self.assertFalse(loyalty_eligible(2))


class LoyaltyEndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(state_file=os.path.join(self.tmp.name, "state.json"))
        self.store.load()
        godstar.ensure_defaults(self.store)
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

    def _place_members(self):
        return godstar.place_order(
            self.store, 111, "members",
            {"count": 1000, "channel_id": "@GodNews24"}, tx_hash="abc")

    def _complete_members(self, n):
        for _ in range(n):
            order = self._place_members()
            godstar.confirm_order(self.store, order["order_no"])

    def test_loyalty_used_once_then_needs_three_more(self):
        # ۳ سفارش تکمیل‌شده → سفارش چهارم با تخفیف وفاداری
        self._complete_members(3)
        order = self._place_members()
        self.assertEqual(order.get("discount_kind"), "loyalty")
        self.assertEqual(order["amount"], "2.70")
        # با تاییدش، تخفیف می‌سوزد؛ دو سفارش بعد بدون تخفیف‌اند
        godstar.confirm_order(self.store, order["order_no"])
        user = self.store.get_user(111)
        self.assertEqual(user.get("loyalty_uses"), 1)
        self.assertEqual(user.get("completed_orders"), 4)
        order2 = self._place_members()
        self.assertFalse(order2.get("discount_kind"))
        self.assertEqual(order2["amount"], "3.00")
        godstar.confirm_order(self.store, order2["order_no"])
        order3 = self._place_members()
        self.assertFalse(order3.get("discount_kind"))
        godstar.confirm_order(self.store, order3["order_no"])
        # حالا ۶ سفارش تکمیل‌شده → تخفیف دوباره فعال است
        order4 = self._place_members()
        self.assertEqual(order4.get("discount_kind"), "loyalty")
        self.assertEqual(order4["amount"], "2.70")

    def test_rejected_loyalty_order_does_not_burn_it(self):
        self._complete_members(3)
        order = self._place_members()
        self.assertEqual(order.get("discount_kind"), "loyalty")
        godstar.reject_order(self.store, order["order_no"], "رسید معتبر نیست")
        self.assertEqual(self.store.get_user(111).get("loyalty_uses") or 0, 0)
        order2 = self._place_members()
        self.assertEqual(order2.get("discount_kind"), "loyalty")


if __name__ == "__main__":
    unittest.main()
