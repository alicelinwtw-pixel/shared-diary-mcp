import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shared_diary import DiaryError, DiaryStore, PermissionDenied
from shared_diary import DiaryTools


class DiaryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = DiaryStore()
        self.human = self.store.create_participant("Human", "human", participant_id="human")
        self.assistant = self.store.create_participant("Aster", "ai", participant_id="assistant")
        self.companion = self.store.create_participant("Juniper", "ai", participant_id="companion")

    def tearDown(self) -> None:
        self.store.close()

    def test_shared_entry_is_visible_to_every_participant(self) -> None:
        entry = self.store.write_entry("assistant", "今天完成了第一次共同写作。")
        self.assertEqual(self.store.read_entry(entry["id"], "human")["body"], entry["body"])
        self.assertEqual(self.store.read_entry(entry["id"], "companion")["body"], entry["body"])

    def test_only_author_can_edit_entry(self) -> None:
        entry = self.store.write_entry("human", "写错了一个字")
        edited = self.store.edit_entry(entry["id"], "human", "已经改好了")
        self.assertEqual(edited["body"], "已经改好了")
        self.assertEqual(self.store.read_entry(entry["id"], "assistant")["body"], "已经改好了")
        with self.assertRaises(PermissionDenied):
            self.store.edit_entry(entry["id"], "assistant", "偷偷篡改")
        with self.assertRaises(DiaryError):
            self.store.edit_entry(entry["id"], "human", "   ")

    def test_only_author_can_delete_entry_and_replies(self) -> None:
        entry = self.store.write_entry("human", "这篇可以删")
        self.store.reply_to_entry(entry["id"], "assistant", "回应也会一起消失")
        with self.assertRaises(PermissionDenied):
            self.store.delete_entry(entry["id"], "assistant")
        self.assertEqual(len(self.store.list_replies(entry["id"], "human")), 1)
        self.store.delete_entry(entry["id"], "human")
        self.assertEqual(self.store.list_timeline("human"), [])

    def test_private_entry_is_only_visible_to_author(self) -> None:
        entry = self.store.write_entry("assistant", "这是一篇私人草稿。", visibility="private")
        self.assertEqual(self.store.read_entry(entry["id"], "assistant")["body"], entry["body"])
        with self.assertRaises(PermissionDenied):
            self.store.read_entry(entry["id"], "human")

    def test_selected_entry_is_visible_only_to_selected_people(self) -> None:
        entry = self.store.write_entry(
            "human",
            "这一篇只给 Aster。",
            visibility="selected",
            audience=["assistant"],
        )
        self.assertEqual(self.store.read_entry(entry["id"], "assistant")["body"], entry["body"])
        with self.assertRaises(PermissionDenied):
            self.store.read_entry(entry["id"], "companion")

    def test_challenge_entry_unlocks_with_any_equivalent_answer(self) -> None:
        entry = self.store.write_entry(
            "assistant",
            "答案正确后显示正文。",
            visibility="challenge",
            audience=["human"],
            challenge_question="第一次测试是哪一天？",
            challenge_answers=["1月2日", "一月二日", "2026-01-02"],
            challenge_hint="一月。",
            preview="等待正确答案。",
        )
        locked = self.store.read_entry(entry["id"], "human")
        self.assertTrue(locked["locked"])
        self.assertIsNone(locked["body"])

        wrong = self.store.read_entry(entry["id"], "human", answer="1月3日")
        self.assertTrue(wrong["locked"])

        unlocked = self.store.read_entry(entry["id"], "human", answer="  一月二日  ")
        self.assertFalse(unlocked["locked"])
        self.assertTrue(unlocked["just_unlocked"])
        self.assertIn("答案正确", unlocked["body"])

        still_unlocked = self.store.read_entry(entry["id"], "human")
        self.assertFalse(still_unlocked["locked"])

    def test_challenge_is_hidden_from_unselected_people_and_locked_replies(self) -> None:
        entry = self.store.write_entry(
            "assistant",
            "只给特定参与者的正文。",
            visibility="challenge",
            audience=["human"],
            challenge_question="暗号？",
            challenge_answers=["starlight"],
        )
        with self.assertRaises(PermissionDenied):
            self.store.read_entry(entry["id"], "companion")
        with self.assertRaises(PermissionDenied):
            self.store.reply_to_entry(entry["id"], "human", "还没解锁。")
        self.store.read_entry(entry["id"], "human", answer="starlight")
        reply = self.store.reply_to_entry(entry["id"], "human", "现在可以回应。")
        self.assertEqual(reply["body"], "现在可以回应。")

    def test_replies_are_bidirectional_and_have_unread_state(self) -> None:
        entry = self.store.write_entry("assistant", "今天适合记录一次测试。")
        reply = self.store.reply_to_entry(entry["id"], "human", "同意。")
        self.store.reply_to_entry(entry["id"], "assistant", "已经记下来了。")

        replies = self.store.list_replies(entry["id"], "human")
        self.assertEqual([item["body"] for item in replies], ["同意。", "已经记下来了。"]) 

        unread = self.store.get_unread_replies("assistant")
        self.assertEqual([item["id"] for item in unread], [reply["id"]])
        self.assertEqual(self.store.mark_replies_read("assistant", [reply["id"]]), 1)
        self.assertEqual(self.store.get_unread_replies("assistant"), [])

    def test_other_participants_entries_have_persistent_unread_state(self) -> None:
        entry = self.store.write_entry("assistant", "这是一篇尚未阅读的新日记。")
        self.assertTrue(self.store.read_entry(entry["id"], "human")["unread"])
        self.assertFalse(self.store.read_entry(entry["id"], "assistant")["unread"])
        self.store.mark_entry_read(entry["id"], "human")
        self.assertFalse(self.store.read_entry(entry["id"], "human")["unread"])

    def test_selected_and_challenge_require_audience(self) -> None:
        with self.assertRaises(DiaryError):
            self.store.write_entry("human", "缺少对象", visibility="selected")
        with self.assertRaises(DiaryError):
            self.store.write_entry(
                "human",
                "缺少答案",
                visibility="challenge",
                audience=["assistant"],
                challenge_question="问题",
            )

    def test_access_key_maps_to_one_stable_participant(self) -> None:
        key = self.store.issue_access_key("assistant")
        self.assertEqual(self.store.authenticate(key), "assistant")
        self.assertIsNone(self.store.authenticate("wrong-key"))
        rotated = self.store.issue_access_key("assistant")
        self.assertIsNone(self.store.authenticate(key))
        self.assertEqual(self.store.authenticate(rotated), "assistant")

    def test_tools_support_silent_autonomous_writes_and_recent_pages(self) -> None:
        tools = DiaryTools(self.store)
        result = tools.write_entry(
            "assistant",
            "开始记录。",
            occurred_at="2026-07-16T12:00:00+00:00",
        )
        self.assertTrue(result["saved"])
        self.assertFalse(result["announce"])
        self.assertIn("Do not announce", result["instruction"])

        recent = tools.read_recent("assistant", days=1)
        self.assertEqual(list(recent["days"]), ["2026-07-16"])
        self.assertEqual(recent["days"]["2026-07-16"][0]["body"], "开始记录。")

    def test_mixed_utc_and_offset_times_use_real_order_and_shanghai_day(self) -> None:
        earlier = self.store.write_entry(
            "companion",
            "较早的 02:51",
            occurred_at="2026-07-17T02:51:00+08:00",
        )
        later = self.store.write_entry(
            "human",
            "较晚的 03:21",
            occurred_at="2026-07-16T19:21:00.000Z",
        )

        timeline = self.store.list_timeline("human")
        self.assertEqual([item["id"] for item in timeline], [later["id"], earlier["id"]])
        self.assertEqual([item["local_day"] for item in timeline], ["2026-07-17"] * 2)
        self.assertEqual(
            [item["id"] for item in self.store.list_day("human", "2026-07-17")],
            [earlier["id"], later["id"]],
        )

    def test_reopening_database_repairs_old_mixed_offset_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "diary.sqlite3"
            store = DiaryStore(path)
            store.create_participant("Human", "human", participant_id="human")
            entry = store.write_entry("human", "旧数据")
            with store.db:
                store.db.execute(
                    "UPDATE entries SET occurred_at = ? WHERE id = ?",
                    ("2026-07-17T03:21:00+08:00", entry["id"]),
                )
            store.close()

            reopened = DiaryStore(path)
            repaired = reopened.read_entry(entry["id"], "human")
            self.assertEqual(repaired["occurred_at"], "2026-07-16T19:21:00.000000+00:00")
            self.assertEqual(repaired["local_day"], "2026-07-17")
            reopened.close()

    def test_invalid_event_time_is_rejected(self) -> None:
        with self.assertRaises(DiaryError):
            self.store.write_entry("human", "坏时间", occurred_at="不是时间")

    def test_export_contains_only_visible_entries_and_their_replies(self) -> None:
        visible = self.store.write_entry("human", "共同页")
        self.store.reply_to_entry(visible["id"], "assistant", "共同回应")
        self.store.write_entry("assistant", "私人页", visibility="private")
        locked = self.store.write_entry(
            "assistant",
            "锁后正文",
            visibility="challenge",
            audience=["human"],
            challenge_question="暗号？",
            challenge_answers=["starlight"],
            preview="门缝里的一句话",
        )

        backup = self.store.export_visible_diary("human")
        bodies = [entry["body"] for entry in backup["entries"]]
        self.assertIn("共同页", bodies)
        self.assertNotIn("私人页", bodies)
        exported_visible = next(
            entry for entry in backup["entries"] if entry["id"] == visible["id"]
        )
        self.assertEqual(exported_visible["replies"][0]["body"], "共同回应")
        exported_locked = next(
            entry for entry in backup["entries"] if entry["id"] == locked["id"]
        )
        self.assertTrue(exported_locked["locked"])
        self.assertIsNone(exported_locked["body"])
        self.assertEqual(exported_locked["replies"], [])


if __name__ == "__main__":
    unittest.main()
