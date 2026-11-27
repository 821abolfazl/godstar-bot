# تست‌های v1.0.13: مسیر خرابی ذخیره در سوپابیس.
# باگ ۱٫۰٫۱۲ فقط ستون جاافتاده نبود؛ save() حتی وقتی upsert یک جدول
# می‌افتاد، dirty را پاک می‌کرد — پس تغییرِ ذخیره‌نشده نه دوباره
# امتحان می‌شد و نه با ری‌استارت ربات برمی‌گشت (load از سوپابیسِ
# کهنه می‌خواند و فایل محلی نادیده گرفته می‌شد). حالا: خطا ثبت
# می‌شود، dirty می‌ماند، تلاش دوباره با فاصله‌ی ۶۰ ثانیه‌ای انجام
# می‌شود، و یک بار در هر ران به ادمین خبر داده می‌شود.
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import Store
import bot as godstar


class StoreSaveFailureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(
            state_file=os.path.join(self.tmp.name, "state.json"),
            supabase_url="https://example.supabase.co",
            supabase_key="dummy-key")
        self.store.load()
        self.calls = []
        self.fail_tables = set()
        orig_upsert = self.store._sb_upsert_rows

        def fake_upsert(table, rows):
            self.calls.append(table)
            if table in self.fail_tables:
                raise RuntimeError(
                    f"supabase upsert failed ({table}): 400 PGRST204")
            return None

        self.store._sb_upsert_rows = fake_upsert
        self._orig_send = godstar.send_message
        self._orig_admin = godstar.ADMIN_CHAT_ID
        self.sent = []
        godstar.send_message = lambda chat_id, text, reply_markup=None: self.sent.append(
            (chat_id, text, reply_markup)) or {"message_id": 1}
        godstar.ADMIN_CHAT_ID = "278899906"
        godstar._SAVE_ALERT_SENT = False

    def tearDown(self):
        godstar.send_message = self._orig_send
        godstar.ADMIN_CHAT_ID = self._orig_admin
        godstar._SAVE_ALERT_SENT = False
        self.tmp.cleanup()

    def test_failed_table_keeps_dirty_and_records_errors(self):
        self.fail_tables = {"users"}
        self.store.ensure_user(111)
        errors = self.store.save()
        self.assertIn("users", errors)
        self.assertTrue(self.store.dirty)
        self.assertIn("users", self.store.last_save_errors)
        # جدول‌های دیگر همچنان ذخیره شدند و فایل محلی به‌روز است.
        self.assertIn("orders", self.calls)
        import json
        with open(self.store.state_file, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("111", data["users"])

    def test_backoff_suppresses_immediate_retry_then_retries(self):
        self.fail_tables = {"users"}
        self.store.ensure_user(111)
        self.store.save_if_dirty()
        first_calls = len(self.calls)
        self.assertGreater(first_calls, 0)
        # تلاش فوریِ بعدی نباید دوباره upsert کند.
        self.store.save_if_dirty()
        self.assertEqual(len(self.calls), first_calls)
        self.assertTrue(self.store.dirty)
        # بعد از گذشتن فاصله، دوباره امتحان می‌کند.
        self.store._save_backoff_until = 0.0
        self.fail_tables = set()
        self.store.save_if_dirty()
        self.assertGreater(len(self.calls), first_calls)
        self.assertFalse(self.store.dirty)
        self.assertEqual(self.store.last_save_errors, {})

    def test_success_clears_dirty_and_errors(self):
        self.store.ensure_user(111)
        errors = self.store.save_if_dirty()
        self.assertEqual(errors, {})
        self.assertFalse(self.store.dirty)
        self.assertEqual(self.store.last_save_errors, {})

    def test_save_alert_sent_once_per_run(self):
        self.store.last_save_errors = {"users": "PGRST204 boom"}
        godstar.alert_save_errors_once(self.store)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("users", self.sent[0][1])
        # تا وقتی خطا همان است، دوباره خبر نمی‌دهد.
        godstar.alert_save_errors_once(self.store)
        self.assertEqual(len(self.sent), 1)
        # با درست شدن ذخیره، پرچم برمی‌گردد و خرابی بعدی دوباره خبر می‌دهد.
        self.store.last_save_errors = {}
        godstar.alert_save_errors_once(self.store)
        self.store.last_save_errors = {"orders": "boom"}
        godstar.alert_save_errors_once(self.store)
        self.assertEqual(len(self.sent), 2)
        self.assertIn("orders", self.sent[1][1])


if __name__ == "__main__":
    unittest.main()
