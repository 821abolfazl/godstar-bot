# تست‌های v1.0.13: ستون‌های supabase.sql با فیلدهایی که کد در ردیف‌ها
# می‌گذارد هم‌خوان باشند. نبودِ ستون loyalty_uses در جدول users زنده
# (نسخه‌ی ۱٫۰٫۱۲) ذخیره‌ی همه‌ی کاربرها را با PGRST204 می‌انداخت و
# موجودی کیف پول در مینی‌اپ هیچ‌وقت تازه نمی‌شد — در حالی که ربات
# در حافظه‌اش موجودی درست را داشت و فقط فایل محلی نگه داشته می‌شد.
# این تست با ساختن ردیف‌های واقعی با متدهای خود Store و مقایسه‌ی
# کلیدهایشان با ستون‌های supabase.sql، چنین دریفتی را پیش از پوش
# می‌گیرد؛ هر فیلد تازه‌ای باید هم در db.py و هم در supabase.sql
# (CREATE TABLE یا ALTER TABLE … ADD COLUMN) بیاید.
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import Store, _TABLES
import bot as godstar

SQL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "supabase.sql")

_SKIP_FIRST = {"primary", "unique", "constraint", "check", "foreign", "key"}


def _sql_columns(path):
    """ستون‌های هر جدول در supabase.sql: از CREATE TABLEها + ALTERهای
    ADD COLUMN IF NOT EXISTS (مهاجرت‌های پایین فایل)."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    tables = {}
    for m in re.finditer(
            r"create table if not exists\s+(?:public\.)?(\w+)\s*\((.*?)\);",
            text, re.S):
        cols = set()
        for line in m.group(2).splitlines():
            line = line.strip()
            if not line or line.startswith("--"):
                continue
            first = line.split()[0].strip('",')
            if first.lower() in _SKIP_FIRST:
                continue
            cols.add(first)
        tables[m.group(1)] = cols
    for m in re.finditer(
            r"alter table\s+(?:public\.)?(\w+)\s+add column if not exists\s+(\w+)",
            text, re.I):
        tables.setdefault(m.group(1), set()).add(m.group(2))
    return tables


class SchemaDriftTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(state_file=os.path.join(self.tmp.name, "state.json"))
        self.store.load()
        godstar.ensure_defaults(self.store)
        self.columns = _sql_columns(SQL_PATH)

    def tearDown(self):
        self.tmp.cleanup()

    def _assert_row_keys(self, table, row):
        missing = sorted(set(row) - self.columns.get(table, set()))
        self.assertEqual(
            missing, [],
            f"ستون‌های جاافتاده در جدول {table}: {missing} — "
            f"کلیدهای ردیف: {sorted(row)}")

    def test_every_store_table_has_sql_definition(self):
        for table in _TABLES:
            self.assertIn(
                table, self.columns,
                f"جدول {table} در supabase.sql تعریف نشده است")

    def test_user_row_keys_match_users_table(self):
        # رگرسیون باگ کیف پول ۱٫۰٫۱۲: loyalty_uses در ردیف بود ولی
        # ستونش در جدول زنده نبود و ذخیره‌ی کاربرها می‌افتاد.
        user = self.store.ensure_user(111, username="t", first_name="T")
        self.store.credit_wallet(111, 20, note="شارژ آزمایشی")
        self.store.debit_wallet(111, 5, note="سفارش آزمایشی")
        self.assertIn("loyalty_uses", user)
        self._assert_row_keys("users", user)

    def test_order_row_keys_match_orders_table(self):
        order = self.store.create_order(
            111, "order", "members", {"channel_id": "@x"}, "30")
        self._assert_row_keys("orders", order)
        self.store.set_order_status(order["order_no"], "confirmed")
        # سفارش تاییدشده confirmed_at هم می‌گیرد و آن هم ستون می‌خواهد.
        self.assertIn("confirmed_at", order)
        self._assert_row_keys("orders", order)

    def test_wallet_tx_row_keys_match_table(self):
        self.store.credit_wallet(111, 20)
        row = self.store.state["wallet_tx"][-1]
        self._assert_row_keys("wallet_tx", row)

    def test_discount_code_row_keys_match_table(self):
        self.store.add_code("GOD10", 10, max_uses=2)
        self._assert_row_keys(
            "discount_codes", self.store.state["discount_codes"]["GOD10"])

    def test_referral_row_keys_match_table(self):
        self.store.ensure_user(111, username="ref")
        self.store.state["users"]["111"]["referral_code"] = "GOD111111"
        status, _ = self.store.record_referral_use("GOD111111", 222)
        self.assertEqual(status, "ok")
        self._assert_row_keys("referrals", self.store.state["referrals"][-1])

    def test_support_thread_auto_reply_reminder_rows_match_tables(self):
        self.store.map_support_message(
            999, 111, orig_text="سلام", orig_photo_file_id="fid")
        self._assert_row_keys(
            "support_threads", self.store.state["support_threads"]["999"])
        row = self.store.add_auto_reply("سؤال؟", "جواب.")
        self._assert_row_keys("auto_replies", row)
        self.store.add_reminder("GS-1001", "pending_admin")
        self._assert_row_keys(
            "order_reminders", self.store.state["order_reminders"][-1])


if __name__ == "__main__":
    unittest.main()
