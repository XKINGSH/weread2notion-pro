"""读书进度同步的纯逻辑单测：不联网、不读写 Notion、不需要第三方依赖。

跑法（仓库根目录）：
    python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from weread2notionpro import progress  # noqa: E402


class FakeApi(object):
    """假的微信读书客户端：只提供书架进度与有笔记书目两个入口。"""

    def __init__(self, shelf_entries, notebooks=None, shelf_raises=None):
        self._shelf_entries = shelf_entries
        self._notebooks = notebooks or []
        self._shelf_raises = shelf_raises

    def get_shelf_progress(self):
        if self._shelf_raises is not None:
            raise self._shelf_raises
        return list(self._shelf_entries)

    def get_notebooklist(self):
        return list(self._notebooks)


class FakeHelper(object):
    """假的 Notion 客户端：记录被调用过哪些写入，不做真实请求。"""

    def __init__(self, books, raise_on=None):
        self._books = books
        self._raise_on = raise_on or set()
        self.update_calls = []

    def get_all_book(self):
        return self._books

    def update_page(self, page_id, properties, cover=None, icon=None):
        if page_id in self._raise_on:
            raise RuntimeError("boom")
        self.update_calls.append((page_id, properties))
        return {"id": page_id}


class TestComputeNotionState(unittest.TestCase):
    def test_finished_book_is_100_percent(self):
        state = progress.compute_notion_state(
            {
                "bookId": "b1",
                "markedStatus": 4,
                "readingTime": 3600,
                "totalReadDay": 9,
                "readingProgress": 37,
                "lastReadingDate": 1700000000,
            }
        )
        self.assertEqual(state["阅读状态"], "已读")
        self.assertEqual(state["阅读进度"], 100.0)
        self.assertEqual(state["阅读时长"], 3600)
        self.assertEqual(state["阅读天数"], 9)
        self.assertEqual(state["最后阅读时间"], "2023-11-15")

    def test_reading_book_progress_is_normalised(self):
        state = progress.compute_notion_state(
            {"bookId": "b2", "markedStatus": 1, "readingTime": 600, "readingProgress": 37}
        )
        self.assertEqual(state["阅读状态"], "在读")
        self.assertAlmostEqual(state["阅读进度"], 0.37)

    def test_short_reading_time_is_want_to_read(self):
        state = progress.compute_notion_state(
            {"bookId": "b3", "markedStatus": 1, "readingTime": 5, "readingProgress": 0}
        )
        self.assertEqual(state["阅读状态"], "想读")
        self.assertEqual(state["阅读进度"], 0.0)

    def test_missing_fields_do_not_raise(self):
        state = progress.compute_notion_state({"bookId": "b4"})
        self.assertEqual(state["阅读状态"], "想读")
        self.assertEqual(state["阅读时长"], 0)
        self.assertNotIn("最后阅读时间", state)

    def test_garbage_numbers_do_not_raise(self):
        state = progress.compute_notion_state(
            {"bookId": "b5", "readingTime": "abc", "readingProgress": None}
        )
        self.assertEqual(state["阅读时长"], 0)

    def test_non_dict_rejected(self):
        with self.assertRaises(TypeError):
            progress.compute_notion_state("not-a-dict")


class TestTimestamp(unittest.TestCase):
    def test_invalid_values(self):
        for value in (None, 0, "", -5, "not-a-number"):
            self.assertIsNone(progress.timestamp_to_date_string(value))

    def test_valid_value(self):
        self.assertEqual(progress.timestamp_to_date_string(1700000000), "2023-11-15")


class TestDiffState(unittest.TestCase):
    def test_identical_state_is_not_rewritten(self):
        target = {"阅读状态": "在读", "阅读进度": 0.1, "阅读时长": 100, "阅读天数": 2}
        self.assertEqual(progress.diff_state(target, dict(target)), {})

    def test_float_noise_is_ignored(self):
        target = {"阅读进度": 0.37}
        current = {"阅读进度": 0.37000000001}
        self.assertEqual(progress.diff_state(target, current), {})

    def test_changed_field_detected(self):
        changed = progress.diff_state({"阅读进度": 0.5}, {"阅读进度": 0.1})
        self.assertEqual(changed, {"阅读进度": 0.5})

    def test_name_change_detected(self):
        changed = progress.diff_state({"阅读状态": "已读"}, {"阅读状态": "在读"})
        self.assertEqual(changed, {"阅读状态": "已读"})

    def test_none_current_is_treated_as_change(self):
        changed = progress.diff_state({"阅读天数": 3}, {"阅读天数": None})
        self.assertEqual(changed, {"阅读天数": 3})


class TestToNotionProperties(unittest.TestCase):
    def test_shapes(self):
        properties = progress.to_notion_properties(
            {"阅读状态": "在读", "阅读进度": 0.5, "最后阅读时间": "2026-10-01"}
        )
        self.assertEqual(properties["阅读状态"], {"status": {"name": "在读"}})
        self.assertEqual(properties["阅读进度"], {"number": 0.5})
        self.assertEqual(properties["最后阅读时间"], {"date": {"start": "2026-10-01"}})

    def test_empty(self):
        self.assertEqual(progress.to_notion_properties({}), {})


class TestSyncProgress(unittest.TestCase):
    def setUp(self):
        self.entries = [
            {"bookId": "b1", "title": "书一", "readingTime": 100, "totalReadDay": 2, "readingProgress": 10},
            {"bookId": "b2", "title": "书二", "readingTime": 200, "totalReadDay": 3, "readingProgress": 50},
            {"bookId": "b3", "title": "书三", "readingTime": 300, "totalReadDay": 4, "readingProgress": 60},
        ]
        self.books = {
            "b1": {"pageId": "p1", "status": "在读", "阅读进度": 0.1, "readingTime": 100, "阅读天数": 2,
                   "最后阅读时间": 0},
            "b2": {"pageId": "p2", "status": "在读", "阅读进度": 0.1, "readingTime": 200, "阅读天数": 3,
                   "最后阅读时间": 0},
        }

    def test_counts_and_single_write(self):
        helper = FakeHelper(self.books)
        summary = progress.sync_progress(api=FakeApi(self.entries), helper=helper)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(summary["unchanged"], 1)
        self.assertEqual(summary["missing"], 1)
        self.assertEqual(summary["failed"], 0)
        # 只对真正变化的那一本写了一次，且只写了变化的字段
        self.assertEqual(len(helper.update_calls), 1)
        page_id, properties = helper.update_calls[0]
        self.assertEqual(page_id, "p2")
        self.assertEqual(properties, {"阅读进度": {"number": 0.5}})

    def test_duplicate_book_ids_processed_once(self):
        entries = list(self.entries) + [dict(self.entries[0])]
        helper = FakeHelper(self.books)
        summary = progress.sync_progress(api=FakeApi(entries), helper=helper)
        self.assertEqual(summary["total"], 3)

    def test_rerun_writes_nothing_the_second_time(self):
        helper = FakeHelper(self.books)
        progress.sync_progress(api=FakeApi(self.entries), helper=helper)
        # 把 Notion 侧更新成最新状态后重跑，应当零写入（幂等）
        self.books["b2"]["阅读进度"] = 0.5
        helper2 = FakeHelper(self.books)
        summary = progress.sync_progress(api=FakeApi(self.entries), helper=helper2)
        self.assertEqual(summary["updated"], 0)
        self.assertEqual(helper2.update_calls, [])

    def test_single_book_failure_does_not_abort_run(self):
        helper = FakeHelper(self.books, raise_on={"p2"})
        summary = progress.sync_progress(api=FakeApi(self.entries), helper=helper)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["unchanged"], 1)
        self.assertEqual(summary["failures"][0][0], "书二")

    def test_falls_back_to_notebooks_when_shelf_unavailable(self):
        notebooks = [{"bookId": "b9", "book": {"title": "回退书", "readingTime": 120, "readingProgress": 30}}]
        helper = FakeHelper(self.books)
        summary = progress.sync_progress(
            api=FakeApi([], notebooks=notebooks, shelf_raises=RuntimeError("接口挂了")),
            helper=helper,
        )
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["missing"], 1)

    def test_auth_error_is_not_swallowed(self):
        class WeReadAuthError(RuntimeError):
            pass

        helper = FakeHelper(self.books)
        with self.assertRaises(WeReadAuthError):
            progress.sync_progress(
                api=FakeApi([], shelf_raises=WeReadAuthError("key 失效")), helper=helper
            )

    def test_dry_run_writes_nothing(self):
        helper = FakeHelper(self.books)
        summary = progress.sync_progress(api=FakeApi(self.entries), helper=helper, apply=False)
        self.assertEqual(helper.update_calls, [])
        self.assertEqual(summary["updated"], 0)

    def test_limit(self):
        helper = FakeHelper(self.books)
        summary = progress.sync_progress(api=FakeApi(self.entries), helper=helper, limit=1)
        self.assertEqual(summary["total"], 1)


if __name__ == "__main__":
    unittest.main()