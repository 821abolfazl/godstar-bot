# تست‌های مراحل انجام سفارش (v1.0.8) — فایل جدا چون test_godstar.py
# به سقف ۱۳۱٬۰۷۲ بایتی آرگومان ابزار گیت‌هاب رسیده بود.
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import Store
import bot as godstar


class OrderProgressTest(unittest.TestCase):
    """v1.0.8: مراحل انجام سفارش — تایید «تایید شده» می‌گذارد، ادمین
    با دکمه‌ی «مرحله‌ی بعد» جلو می‌برد، مشتری در «سفارش‌های من» می‌بیند."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self.tmp.name, "state.json")
        self.store = Store(state_file=self.state_path)
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

    def _admin_query(self, data, message_id):
        return {"data": data, "id": "q1",
                "message": {"message_id": message_id,
                            "chat": {"id": 278899906}}}

    def _reply_markups(self):
        import json as _json
        out = []
        for m, d in self.api_calls:
            if m == "editMessageReplyMarkup":
                out.append(_json.loads(d["reply_markup"]))
        return out

    def _pending_order(self):
        return godstar.place_order(
            self.store, 111, "members",
            {"count": 1000, "channel_id": "@GodNews24"}, tx_hash="abc")

    def test_confirm_sets_progress_confirmed(self):
        order = self._pending_order()
        godstar.confirm_order(self.store, order["order_no"])
        self.assertEqual(
            self.store.get_order("GS-1001")["details"]["progress"], "confirmed")
        self.assertTrue(any("مراحل انجام" in t for _, t, _ in self.sent))

    def test_confirm_wallet_charge_sets_progress_completed(self):
        order = godstar.place_order(
            self.store, 111, "wallet_charge", {"amount": 20}, tx_hash="abc")
        godstar.confirm_order(self.store, order["order_no"])
        self.assertEqual(
            self.store.get_order("GS-1001")["details"]["progress"], "completed")
        self.assertEqual(self.store.wallet_balance(111), 20.0)

    def test_advance_via_callback_full_flow(self):
        self._pending_order()
        # تایید: دکمه‌ی «مرحله‌ی بعد: در حال انجام» می‌آید
        godstar.handle_callback(
            self.store, self._admin_query("approve:GS-1001", 55))
        markups = self._reply_markups()
        self.assertEqual(len(markups), 1)
        btn = markups[0]["inline_keyboard"][0][0]
        self.assertEqual(btn["callback_data"], "progress:GS-1001")
        self.assertIn("در حال انجام", btn["text"])
        # مرحله‌ی اول: در حال انجام
        godstar.handle_callback(
            self.store, self._admin_query("progress:GS-1001", 55))
        self.assertEqual(
            self.store.get_order("GS-1001")["details"]["progress"], "in_progress")
        self.assertTrue(any("به مرحله‌ی «در حال انجام» رسید" in t
                            for _, t, _ in self.sent))
        markups = self._reply_markups()
        btn = markups[-1]["inline_keyboard"][0][0]
        self.assertIn("تکمیل شده", btn["text"])
        # مرحله‌ی آخر: تکمیل شده — دکمه‌ها برداشته می‌شوند
        godstar.handle_callback(
            self.store, self._admin_query("progress:GS-1001", 55))
        self.assertEqual(
            self.store.get_order("GS-1001")["details"]["progress"], "completed")
        self.assertTrue(any("تکمیل شد" in t for _, t, _ in self.sent))
        self.assertEqual(self._reply_markups()[-1], {"inline_keyboard": []})

    def test_progress_on_pending_order_rejected(self):
        self._pending_order()
        godstar.handle_callback(
            self.store, self._admin_query("progress:GS-1001", 55))
        self.assertTrue(any("فقط سفارش تاییدشده" in (d.get("text") or "")
                            for m, d in self.api_calls
                            if m == "answerCallbackQuery"))

    def test_progress_non_admin_rejected(self):
        self._pending_order()
        query = {"data": "progress:GS-1001", "id": "q1",
                 "message": {"message_id": 55, "chat": {"id": 111}}}
        godstar.handle_callback(self.store, query)
        self.assertTrue(any("فقط ادمین" in (d.get("text") or "")
                            for m, d in self.api_calls
                            if m == "answerCallbackQuery"))

    def test_progress_command(self):
        self._pending_order()
        godstar.confirm_order(self.store, "GS-1001")
        godstar.handle_admin_command(self.store, 278899906, "/progress GS-1001")
        self.assertEqual(
            self.store.get_order("GS-1001")["details"]["progress"], "in_progress")
        self.assertTrue(any("به مرحله‌ی «در حال انجام» رسید" in t
                            and "مرحله‌ی بعد: تکمیل شده" in t
                            for _, t, _ in self.sent))

    def test_progress_persists_after_save_load(self):
        self._pending_order()
        godstar.confirm_order(self.store, "GS-1001")
        godstar.advance_order_progress(self.store, "GS-1001")
        self.store.save()
        store2 = Store(state_file=self.state_path)
        store2.load()
        self.assertEqual(
            store2.get_order("GS-1001")["details"]["progress"], "in_progress")
