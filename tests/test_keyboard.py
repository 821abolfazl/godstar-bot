# تست‌های v1.0.12: منوی اصلی هیچ دکمه‌ی وب‌اپی ندارد؛ مینی‌اپ فقط از
# دکمه‌ی آبی «فروشگاه» باز می‌شود.
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import Store
import bot as godstar


class MainKeyboardTest(unittest.TestCase):
    def test_no_webapp_button_in_main_keyboard(self):
        for is_admin in (False, True):
            keyboard = godstar.main_keyboard(is_admin)["keyboard"]
            for row in keyboard:
                for button in row:
                    self.assertNotIn("web_app", button)

    def test_no_open_shop_button_in_main_keyboard(self):
        texts = [b["text"] for row in godstar.main_keyboard()["keyboard"]
                 for b in row]
        self.assertNotIn("🛍 باز کردن فروشگاه", texts)
        self.assertIn("🛍 خدمات", texts)


class ServicesButtonTest(unittest.TestCase):
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

    def test_services_button_shows_services_and_points_to_blue_button(self):
        msg = {"chat": {"id": 111},
               "from": {"id": 111, "username": "tester_x",
                        "first_name": "Test"},
               "text": "🛍 خدمات"}
        godstar.handle_message(self.store, msg)
        self.assertEqual(len(self.sent), 1)
        text = self.sent[0][1]
        self.assertIn("تبلیغات تلگرام", text)
        self.assertIn("دکمه‌ی آبی «فروشگاه»", text)


if __name__ == "__main__":
    unittest.main()
