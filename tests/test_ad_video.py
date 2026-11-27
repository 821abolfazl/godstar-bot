# تست‌های v1.0.10: فیلم تبلیغ در چت به سفارش Ads می‌چسبد و به ادمین می‌رسد.
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import Store
import bot as godstar


class AdVideoTest(unittest.TestCase):
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

    def _video_ads_order(self, chat_id=111):
        return godstar.place_order(
            self.store, chat_id, "ads",
            {"views": 100000, "media": "video", "channel_id": "@GodNews24",
             "ad_text": "t"},
            tx_hash="abc")

    def test_video_attached_to_pending_ads_order(self):
        order = self._video_ads_order()
        ok = godstar.attach_ad_video(
            self.store, 111, {"video": {"file_id": "VID1"}})
        self.assertTrue(ok)
        stored = self.store.get_order(order["order_no"])
        self.assertTrue(stored["details"].get("ad_media_sent"))
        self.assertNotIn("ad_media_file_id", stored["details"])
        videos = [c for c in self.api_calls if c[0] == "sendVideo"]
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0][1]["video"], "VID1")
        self.assertEqual(videos[0][1]["chat_id"], "278899906")
        self.assertIn(order["order_no"], videos[0][1]["caption"])
        self.assertIn("@tester_x", videos[0][1]["caption"])
        self.assertTrue(any("فیلم تبلیغ" in t and order["order_no"] in t
                            for _, t, _ in self.sent))

    def test_video_without_pending_order(self):
        ok = godstar.attach_ad_video(
            self.store, 111, {"video": {"file_id": "VID1"}})
        self.assertFalse(ok)
        self.assertEqual(self.api_calls, [])

    def test_video_ignored_when_media_already_sent(self):
        order = self._video_ads_order()
        order["details"]["ad_media_sent"] = True
        ok = godstar.attach_ad_video(
            self.store, 111, {"video": {"file_id": "VID1"}})
        self.assertFalse(ok)
        self.assertEqual(self.api_calls, [])

    def test_video_ignored_for_photo_order(self):
        godstar.place_order(
            self.store, 111, "ads",
            {"views": 100000, "media": "photo", "channel_id": "@GodNews24",
             "ad_text": "t"},
            tx_hash="abc")
        ok = godstar.attach_ad_video(
            self.store, 111, {"video": {"file_id": "VID1"}})
        self.assertFalse(ok)

    def test_video_ignored_for_non_ads_order(self):
        godstar.place_order(
            self.store, 111, "members",
            {"count": 1000, "channel_id": "@GodNews24"}, tx_hash="abc")
        ok = godstar.attach_ad_video(
            self.store, 111, {"video": {"file_id": "VID1"}})
        self.assertFalse(ok)

    def test_newest_order_gets_video(self):
        old = self._video_ads_order()
        new = self._video_ads_order()
        godstar.attach_ad_video(
            self.store, 111, {"video": {"file_id": "VID1"}})
        self.assertTrue(
            self.store.get_order(new["order_no"])["details"].get("ad_media_sent"))
        self.assertNotIn("ad_media_sent",
                         self.store.get_order(old["order_no"])["details"])

    def test_handle_message_routes_video(self):
        order = self._video_ads_order()
        msg = {"chat": {"id": 111},
               "from": {"id": 111, "username": "tester_x",
                        "first_name": "Test"},
               "video": {"file_id": "VID2"}}
        godstar.handle_message(self.store, msg)
        self.assertTrue(
            self.store.get_order(order["order_no"])["details"].get("ad_media_sent"))
        self.assertTrue(any(c[0] == "sendVideo" for c in self.api_calls))

    def test_video_sent_as_file_is_attached(self):
        # فیلمی که به‌صورت فایل فرستاده می‌شود (document با mime ویدیویی)
        # هم باید به سفارش بچسبد، نه اینکه گم شود.
        order = self._video_ads_order()
        ok = godstar.attach_ad_video(
            self.store, 111,
            {"document": {"file_id": "DOC1", "mime_type": "video/mp4"}})
        self.assertTrue(ok)
        self.assertTrue(
            self.store.get_order(order["order_no"])["details"].get("ad_media_sent"))
        videos = [c for c in self.api_calls if c[0] == "sendVideo"]
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0][1]["video"], "DOC1")

    def test_non_video_document_is_ignored(self):
        self._video_ads_order()
        ok = godstar.attach_ad_video(
            self.store, 111,
            {"document": {"file_id": "DOC2", "mime_type": "application/pdf"}})
        self.assertFalse(ok)
        self.assertEqual(self.api_calls, [])

    def test_video_in_support_mode_still_attached(self):
        # مشتری در حالت پشتیبانی فیلم را می‌فرستد: فیلم نباید در چت
        # پشتیبانی گم شود؛ اول به سفارش می‌چسبد.
        order = self._video_ads_order()
        self.store.set_support_mode(111, True)
        msg = {"chat": {"id": 111},
               "from": {"id": 111, "username": "tester_x",
                        "first_name": "Test"},
               "video": {"file_id": "VID3"}}
        godstar.handle_message(self.store, msg)
        self.assertTrue(
            self.store.get_order(order["order_no"])["details"].get("ad_media_sent"))
        self.assertTrue(any(c[0] == "sendVideo" for c in self.api_calls))


if __name__ == "__main__":
    unittest.main()
