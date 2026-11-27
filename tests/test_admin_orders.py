# تست‌های v1.0.9: آیدی مشتری در اعلان سفارش + دستور /allorders.
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import Store
import bot as godstar


class AdminOrdersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(state_file=os.path.join(self.tmp.name, "state.json"))
        self.store.load()
        godstar.ensure_defaults(self.store)
        self.sent = []
        self._orig_send = godstar.send_message
        self._orig_admin = godstar.ADMIN_CHAT_ID
        godstar.send_message = lambda chat_id, text, reply_markup=None: self.sent.append(
            (chat_id, text, reply_markup)) or {"message_id": 1}
        godstar.ADMIN_CHAT_ID = "278899906"
        user = self.store.ensure_user(111)
        user["username"] = "tester_x"
        user["first_name"] = "Test"

    def tearDown(self):
        godstar.send_message = self._orig_send
        godstar.ADMIN_CHAT_ID = self._orig_admin
        self.tmp.cleanup()

    def _order(self, service="members", tx_hash="abc"):
        return godstar.place_order(
            self.store, 111, service,
            {"count": 1000, "channel_id": "@GodNews24"}, tx_hash=tx_hash)

    def test_customer_line_includes_username(self):
        order = self._order()
        text = godstar.order_summary_text(
            order, self.store.get_user(111))
        self.assertIn("@tester_x", text)
        self.assertIn("111", text)
        self.assertIn("🆔", text)

    def test_customer_line_without_username(self):
        user = self.store.ensure_user(222)
        user["first_name"] = "NoUser"
        user["username"] = None
        order = godstar.place_order(
            self.store, 222, "members",
            {"count": 1000, "channel_id": "@GodNews24"}, tx_hash="abc")
        text = godstar.order_summary_text(
            order, self.store.get_user(222))
        self.assertIn("مشتری: NoUser — 🆔 222", text)
        self.assertNotIn("(@", text)

    def test_allorders_lists_all_newest_first(self):
        o1 = self._order()
        godstar.confirm_order(self.store, o1["order_no"])
        o2 = self._order()
        godstar.reject_order(self.store, o2["order_no"], "رسید معتبر نیست")
        o3 = self._order()
        godstar.handle_admin_command(self.store, 278899906, "/allorders")
        # پیام /allorders آخرین پیام فرستاده‌شده است (پیام‌های تایید/رد
        # پیش از آن آمده‌اند و شماره‌ی همان سفارش را هم دارند).
        text = self.sent[-1][1]
        self.assertIn("3 سفارش", text)
        self.assertIn("GS-1001", text)
        self.assertIn("GS-1002", text)
        self.assertIn("GS-1003", text)
        # تازه‌ترین اول: GS-1003 پیش از GS-1001 می‌آید
        self.assertLess(text.index("GS-1003"), text.index("GS-1001"))
        self.assertIn("تایید شده", text)
        self.assertIn("رد شد", text)
        self.assertIn("در انتظار تایید", text)
        self.assertIn("@tester_x", text)

    def test_allorders_shows_stage(self):
        order = self._order()
        godstar.confirm_order(self.store, order["order_no"])
        godstar.advance_order_progress(self.store, order["order_no"])
        godstar.handle_admin_command(self.store, 278899906, "/allorders")
        text = "\n".join(t for _, t, _ in self.sent)
        self.assertIn("در حال انجام", text)

    def test_allorders_empty(self):
        godstar.handle_admin_command(self.store, 278899906, "/allorders")
        self.assertTrue(any("هنوز سفارشی ثبت نشده است" in t
                            for _, t, _ in self.sent))

    def test_allorders_is_admin_only(self):
        self.assertIn("/allorders", godstar.ADMIN_COMMANDS)
        self.assertEqual(godstar.ADMIN_KEYBOARD_COMMANDS["📋 همه‌ی سفارش‌ها"],
                         "/allorders")


if __name__ == "__main__":
    unittest.main()
