"""读书进度同步与书籍摘要块的纯逻辑单测：不联网、不读写 Notion、不需要第三方依赖。

跑法（仓库根目录）：
    python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from weread2notionpro import book_summary, progress  # noqa: E402


class FakeApi(object):
    """假的微信读书客户端：书架 + 逐本进度 + 有笔记书目 + 书籍信息。"""

    def __init__(self, shelf_books, progresses=None, notebooks=None,
                 shelf_raises=None, progress_raises=None, book_info=None):
        self._shelf_books = shelf_books
        self._progresses = progresses or {}
        self._notebooks = notebooks or []
        self._shelf_raises = shelf_raises
        self._progress_raises = progress_raises or {}
        self._book_info = book_info or {}
        self.progress_calls = []

    def get_shelf_progress(self):
        if self._shelf_raises is not None:
            raise self._shelf_raises
        return list(self._shelf_books)

    def get_book_progress(self, book_id):
        self.progress_calls.append(book_id)
        if book_id in self._progress_raises:
            raise self._progress_raises[book_id]
        return dict(self._progresses.get(book_id, {}))

    def get_notebooklist(self):
        return list(self._notebooks)

    def get_book_info(self, book_id):
        return dict(self._book_info.get(book_id, {}))


class FakeHelper(object):
    """假的 Notion 客户端：记录写入，不做真实请求。"""

    def __init__(self, books, raise_on=None, children=None):
        self._books = books
        self._raise_on = raise_on or set()
        self._children = children or {}
        self.update_calls = []
        self.block_updates = []
        self.appended_after = []
        self.appended = []

    def get_all_book(self):
        return self._books

    def update_page(self, page_id, properties, cover=None, icon=None):
        if page_id in self._raise_on:
            raise RuntimeError("boom")
        self.update_calls.append((page_id, properties))
        return {"id": page_id}

    def get_block_children(self, page_id):
        return list(self._children.get(page_id, []))

    def update_block(self, block_id, payload):
        self.block_updates.append((block_id, payload))
        return {"id": block_id}

    def append_blocks(self, page_id, children):
        self.appended.append((page_id, children))
        return {"results": children}

    def append_blocks_after(self, page_id, children, after):
        self.appended_after.append((page_id, children, after))
        return {"results": children}


class TestComputeNotionState(unittest.TestCase):
    def test_finished_book_is_completed_and_100_percent(self):
        state = progress.compute_notion_state(
            {
                "bookId": "b1",
                "readingTime": 19326,
                "progress": 99,
                "finishTime": 1740563549,
                "startReadingTime": 1714639347,
                "updateTime": 1740563561,
            }
        )
        self.assertEqual(state["阅读状态"], "已读")
        self.assertEqual(state["阅读进度"], 100.0)
        self.assertEqual(state["阅读时长"], 19326)
        self.assertEqual(state["最后阅读时间"], "2025-02-26")
        self.assertNotIn("阅读天数", state)

    def test_finished_via_marked_status(self):
        state = progress.compute_notion_state({"bookId": "b", "markedStatus": 4})
        self.assertEqual(state["阅读状态"], "已读")
        self.assertEqual(state["阅读进度"], 100.0)

    def test_finished_via_shelf_finish_reading(self):
        state = progress.compute_notion_state(
            {"bookId": "b", "finishReading": 1, "progress": 99, "readingTime": 60}
        )
        self.assertEqual(state["阅读状态"], "已读")
        self.assertEqual(state["阅读进度"], 100.0)

    def test_reading_book_progress_is_normalised(self):
        state = progress.compute_notion_state(
            {"bookId": "b2", "readingTime": 3600, "progress": 37, "updateTime": 1700000000}
        )
        self.assertEqual(state["阅读状态"], "在读")
        self.assertEqual(state["阅读进度"], 0.37)
        self.assertEqual(state["阅读时长"], 3600)
        self.assertEqual(state["最后阅读时间"], "2023-11-15")

    def test_started_but_short_is_want_to_read(self):
        state = progress.compute_notion_state(
            {"bookId": "b3", "readingTime": 0, "progress": 0, "markedStatus": 1}
        )
        self.assertEqual(state["阅读状态"], "想读")
        self.assertEqual(state["阅读进度"], 0.0)
        self.assertEqual(state["阅读时长"], 0)

    def test_legacy_alias_field_names_still_work(self):
        state = progress.compute_notion_state(
            {"bookId": "b4", "readingTime": 120, "readingProgress": 50,
             "lastReadingDate": 1700000000}
        )
        self.assertEqual(state["阅读进度"], 0.5)
        self.assertEqual(state["最后阅读时间"], "2023-11-15")

    def test_missing_fields_are_not_written_as_zero(self):
        """这是 2026-10-01 那次事故的回归测试：字段缺失时不能写成 0。"""
        state = progress.compute_notion_state({"bookId": "b5"})
        self.assertEqual(state, {})

    def test_missing_reading_time_keeps_status_absent(self):
        state = progress.compute_notion_state({"bookId": "b6", "progress": 20})
        self.assertEqual(state["阅读进度"], 0.2)
        self.assertNotIn("阅读状态", state)
        self.assertNotIn("阅读时长", state)

    def test_non_dict_raises(self):
        with self.assertRaises(TypeError):
            progress.compute_notion_state(None)


class TestDiffAndProperties(unittest.TestCase):
    def test_no_change_yields_empty_diff(self):
        target = {"阅读状态": "在读", "阅读进度": 0.37, "阅读时长": 3600}
        current = {"阅读状态": "在读", "阅读进度": 0.37, "阅读时长": 3600}
        self.assertEqual(progress.diff_state(target, current), {})

    def test_changed_fields_are_reported(self):
        target = {"阅读状态": "已读", "阅读进度": 100.0, "阅读时长": 3600}
        current = {"阅读状态": "在读", "阅读进度": 0.37, "阅读时长": 3600}
        changed = progress.diff_state(target, current)
        self.assertEqual(sorted(changed.keys()), ["阅读状态", "阅读进度"])

    def test_properties_mapping(self):
        props = progress.to_notion_properties(
            {"阅读状态": "在读", "阅读进度": 0.5, "阅读时长": 60,
             "最后阅读时间": "2025-02-26"}
        )
        self.assertEqual(props["阅读状态"], {"status": {"name": "在读"}})
        self.assertEqual(props["阅读进度"], {"number": 0.5})
        self.assertEqual(props["阅读时长"], {"number": 60})
        self.assertEqual(props["最后阅读时间"], {"date": {"start": "2025-02-26"}})

    def test_timestamp_conversion_rejects_bad_values(self):
        self.assertIsNone(progress.timestamp_to_date_string(0))
        self.assertIsNone(progress.timestamp_to_date_string("oops"))


class TestLoadEntries(unittest.TestCase):
    def test_shelf_books_are_enriched_with_real_progress(self):
        api = FakeApi(
            shelf_books=[{"bookId": "b1", "title": "甲", "finishReading": 0}],
            progresses={"b1": {"bookId": "b1", "readingTime": 19326, "progress": 99,
                               "finishTime": 1740563549}},
        )
        entries = progress.load_entries(api)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "甲")
        self.assertEqual(entries[0]["readingTime"], 19326)
        self.assertEqual(entries[0]["progress"], 99)

    def test_shelf_failure_falls_back_to_notebooks(self):
        api = FakeApi(
            shelf_books=[],
            shelf_raises=RuntimeError("http 500"),
            notebooks=[{"bookId": "n1", "markedStatus": 4, "readingProgress": 99,
                        "book": {"bookId": "n1", "title": "乙"}}],
        )
        entries = progress.load_entries(api)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["bookId"], "n1")
        self.assertEqual(entries[0]["markedStatus"], 4)

    def test_auth_error_propagates(self):
        class WeReadAuthError(Exception):
            pass

        api = FakeApi(shelf_books=[], shelf_raises=WeReadAuthError("key 失效"))
        with self.assertRaises(WeReadAuthError):
            progress.load_entries(api)

    def test_single_book_progress_failure_does_not_abort(self):
        api = FakeApi(
            shelf_books=[{"bookId": "b1", "title": "甲"}, {"bookId": "b2", "title": "乙"}],
            progresses={"b2": {"bookId": "b2", "readingTime": 60, "progress": 10}},
            progress_raises={"b1": RuntimeError("gateway down")},
        )
        entries = progress.load_entries(api)
        self.assertEqual(len(entries), 2)
        self.assertNotIn("readingTime", entries[0])
        self.assertEqual(entries[1]["readingTime"], 60)


class TestSyncProgress(unittest.TestCase):
    def _entries(self):
        return [
            {"bookId": "b1", "title": "甲", "readingTime": 19326, "progress": 99,
             "finishTime": 1740563549},
            {"bookId": "b2", "title": "乙", "readingTime": 60, "progress": 10},
        ]

    def test_updates_only_changed_books(self):
        entries = self._entries()
        api = FakeApi(shelf_books=entries, progresses={e["bookId"]: e for e in entries})
        helper = FakeHelper(
            books={
                "b1": {"pageId": "p1", "status": "想读", "阅读进度": 0,
                       "readingTime": 0, "阅读天数": 0, "最后阅读时间": None},
                "b2": {"pageId": "p2", "status": "在读", "阅读进度": 0.1,
                       "readingTime": 60, "阅读天数": 0, "最后阅读时间": None},
            }
        )
        summary = progress.sync_progress(api=api, helper=helper, summaries=False)
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(summary["unchanged"], 1)
        self.assertEqual(summary["failed"], 0)
        page_id, props = helper.update_calls[0]
        self.assertEqual(page_id, "p1")
        self.assertEqual(props["阅读状态"], {"status": {"name": "已读"}})
        self.assertEqual(props["阅读进度"], {"number": 100.0})
        self.assertEqual(props["阅读时长"], {"number": 19326})

    def test_missing_page_is_counted(self):
        entries = self._entries()
        api = FakeApi(shelf_books=entries)
        helper = FakeHelper(books={})
        summary = progress.sync_progress(api=api, helper=helper, summaries=False)
        self.assertEqual(summary["missing"], 2)
        self.assertEqual(summary["updated"], 0)

    def test_page_failure_is_counted_not_raised(self):
        entries = self._entries()
        api = FakeApi(shelf_books=entries)
        helper = FakeHelper(
            books={"b1": {"pageId": "p1"}, "b2": {"pageId": "p2"}},
            raise_on={"p1"},
        )
        summary = progress.sync_progress(api=api, helper=helper, summaries=False)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(len(summary["failures"]), 1)

    def test_dry_run_writes_nothing(self):
        entries = self._entries()
        api = FakeApi(shelf_books=entries)
        helper = FakeHelper(books={"b1": {"pageId": "p1"}})
        summary = progress.sync_progress(api=api, helper=helper, apply=False)
        self.assertEqual(helper.update_calls, [])
        self.assertEqual(summary["updated"], 0)
        self.assertEqual(summary["total"], 2)

    def test_summary_backfill_adds_block_once(self):
        entries = self._entries()
        api = FakeApi(
            shelf_books=entries,
            progresses={e["bookId"]: e for e in entries},
            book_info={},
        )
        helper = FakeHelper(
            books={
                "b1": {"pageId": "p1", "status": "已读", "阅读进度": 100.0,
                       "readingTime": 19326, "最后阅读时间": "2025-02-26",
                       "简介": "甲书的简介"},
                "b2": {"pageId": "p2", "status": "在读", "阅读进度": 0.1,
                       "readingTime": 60, "最后阅读时间": "2025-01-05",
                       "简介": "乙书的简介"},
            },
            children={"p1": [{"id": "toc", "type": "table_of_contents"}],
                      "p2": [{"id": "toc", "type": "table_of_contents"}]},
        )
        summary = progress.sync_progress(api=api, helper=helper, summaries=True)
        self.assertEqual(summary["summaries"], 2)
        self.assertEqual(len(helper.appended_after), 2)
        page_id, children, after = helper.appended_after[0]
        self.assertEqual(after, "toc")
        text = book_summary.block_plain_text(children[0])
        self.assertIn("书籍摘要", text)
        self.assertIn("甲书的简介", text)


class TestBookSummary(unittest.TestCase):
    def test_body_contains_meta_and_intro(self):
        body = book_summary.build_summary_body(
            {"title": "职场晋升101", "author": "崔璀", "publisher": "中信出版社",
             "publishTime": "2022-02-01 00:00:00", "newRating": 84, "isbn": "9787",
             "intro": "本书讲晋升。"}
        )
        self.assertIn("《职场晋升101》", body)
        self.assertIn("作者：崔璀", body)
        self.assertIn("出版社：中信出版社", body)
        self.assertIn("出版时间：2022-02-01", body)
        self.assertIn("微信读书评分：84", body)
        self.assertIn("ISBN：9787", body)
        self.assertIn("简介：本书讲晋升。", body)

    def test_missing_fields_are_marked_wei_ti_gong(self):
        """接口没给的字段写「未提供」，不省略、不编造（2026-10-01 用户口径）。"""
        body = book_summary.build_summary_body({"title": "只有书名"})
        self.assertIn("《只有书名》", body)
        self.assertIn("作者：未提供", body)
        self.assertIn("出版社：未提供", body)
        self.assertIn("微信读书评分：未提供", body)
        self.assertIn("ISBN：未提供", body)
        self.assertIn("简介：未提供", body)
        self.assertIn("划线汇总：未提供", body)
        self.assertIsNone(book_summary.build_summary_body({}))

    def test_note_summary_line_uses_real_counts(self):
        body = book_summary.build_summary_body(
            {"title": "职场晋升101", "划线数": 27, "想法数": 0, "最近划线": 1736069754,
             "intro": "本书讲晋升。"}
        )
        self.assertIn("划线汇总：划线 27 条 · 想法 0 条 · 最近划线：2025-01-05", body)

    def test_note_summary_falls_back_to_notebook_stats(self):
        body = book_summary.build_summary_body(
            {"title": "华为数据之道", "noteCount": 1, "reviewCount": 1, "sort": 1790821470}
        )
        self.assertIn("划线汇总：划线 1 条 · 想法 1 条 · 最近划线：2026-10-01", body)

    def test_notebook_note_stats_reads_gateway_list(self):
        class Api:
            def get_notebooklist(self):
                return [{"bookId": "b1", "noteCount": 3, "reviewCount": 1, "sort": 1736069754}]
        self.assertEqual(
            book_summary.notebook_note_stats(Api(), "b1"),
            {"划线数": 3, "想法数": 1, "最近划线": 1736069754},
        )
        self.assertEqual(book_summary.notebook_note_stats(Api(), "nope"), {})

        class Broken:
            def get_notebooklist(self):
                raise RuntimeError("gateway down")
        self.assertEqual(book_summary.notebook_note_stats(Broken(), "b1"), {})

    def test_ensure_inserts_after_toc_when_missing(self):
        helper = FakeHelper(
            books={},
            children={"p1": [{"id": "toc", "type": "table_of_contents"},
                             {"id": "n1", "type": "callout"}]},
        )
        changed = book_summary.ensure_summary_block(
            helper, "p1", {"title": "甲", "intro": "简介内容"}
        )
        self.assertTrue(changed)
        self.assertEqual(helper.appended_after[0][2], "toc")
        self.assertEqual(helper.appended, [])

    def test_ensure_is_idempotent(self):
        text = book_summary.build_summary_text({"title": "甲", "intro": "简介内容"})
        existing = {
            "id": "sum1",
            "type": "callout",
            "callout": {"rich_text": [{"type": "text", "text": {"content": text},
                                       "plain_text": text}]},
        }
        helper = FakeHelper(books={}, children={"p1": [existing]})
        changed = book_summary.ensure_summary_block(
            helper, "p1", {"title": "甲", "intro": "简介内容"}
        )
        self.assertFalse(changed)
        self.assertEqual(helper.block_updates, [])
        self.assertEqual(helper.appended_after, [])

    def test_ensure_updates_when_content_changed(self):
        existing = {
            "id": "sum1",
            "type": "callout",
            "callout": {"rich_text": [{"type": "text", "plain_text": "📖 书籍摘要\n旧内容"}]},
        }
        helper = FakeHelper(books={}, children={"p1": [existing]})
        changed = book_summary.ensure_summary_block(
            helper, "p1", {"title": "甲", "intro": "新内容"}
        )
        self.assertTrue(changed)
        self.assertEqual(helper.block_updates[0][0], "sum1")
        self.assertIn("新内容", helper.block_updates[0][1]["callout"]["rich_text"][0]["text"]["content"])

    def test_update_false_skips_existing_block(self):
        existing = {
            "id": "sum1",
            "type": "callout",
            "callout": {"rich_text": [{"type": "text", "plain_text": "📖 书籍摘要\n旧内容"}]},
        }
        helper = FakeHelper(books={}, children={"p1": [existing]})
        changed = book_summary.ensure_summary_block(
            helper, "p1", {"title": "甲", "intro": "新内容"}, update=False
        )
        self.assertFalse(changed)
        self.assertEqual(helper.block_updates, [])


if __name__ == "__main__":
    unittest.main()
