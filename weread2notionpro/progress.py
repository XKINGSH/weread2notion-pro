"""读书进度同步：阅读进度 / 阅读时长 / 阅读状态 / 最后阅读时间（+ 书籍摘要块补齐）。

为什么单独一个模块
------------------
``weread.py`` 的主循环只遍历 ``/user/notebooks``（"有笔记的书"），所以
「在读但还没有划线」的书，阅读进度永远不会回写 Notion —— 这正是需求里
"读书进度要能同步" 的实际缺口。本模块以**书架全量**为输入，只更新**已有**书籍页的
属性，不新建页面，因此天然幂等、可以反复执行。

数据从哪里来（2026-10-01 实测修正）
----------------------------------
``/shelf/sync`` 只返回书籍**元信息**：bookId / title / author / cover /
finishReading / readUpdateTime，**不含** readingTime、readingProgress 这类数字。
真正的每本进度在 ``/book/getprogress``：``progress``(0-100) / ``readingTime``(秒) /
``finishTime`` / ``startReadingTime`` / ``updateTime``。

旧实现把 /shelf/sync 的字段当成进度，缺的补 0，结果把「书架」库里 52 本书的
阅读时长 / 阅读进度 / 阅读天数全部改写成了 0，连读完的书都被标成"想读"。
现在的规则是：**接口没给出的字段就不写**，宁可不动，也不覆盖成 0。

幂等与去重（不重复灌数据）
--------------------------
1. 唯一键是书架库的 ``BookId``；
2. 先算出目标值，再与 Notion 现值逐项比较，**只有真的变了才 PATCH**；
3. 同一轮内同一个 ``bookId`` 只处理一次；
4. Notion 里没有对应页面时只计数跳过（建页由 ``weread.py`` 负责），不会重复建页。

依赖说明
--------
本模块顶层的 import 只有标准库；``WeReadApi`` / ``NotionHelper`` 都在函数内部延迟导入。
这样 ``tests/test_progress.py`` 这种纯逻辑单测不需要安装第三方依赖也能跑。
"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))

STATUS_FINISHED = "已读"
STATUS_READING = "在读"
STATUS_WANT = "想读"

# 与 notion_helper 里的属性类型保持一致
NUMBER_FIELDS = ("阅读进度", "阅读时长", "阅读天数")

# 逐本调用 /book/getprogress 之间的轻微间隔，避免对网关造成压力（秒）。
PROGRESS_SLEEP_SECONDS = 0.2


def _as_int(value):
    try:
        if value is None or value == "":
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _as_float(value):
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _maybe_int(value):
    """取整数；拿不到（None / 空 / 非数字）返回 None —— 用 None 表示"接口没给"。"""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _maybe_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def timestamp_to_date_string(timestamp):
    """微信读书的时间戳（秒）→ 北京时间的 ``YYYY-MM-DD``；无效值返回 None。"""
    ts = _as_int(timestamp)
    if ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=CST).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


def compute_notion_state(entry):
    """把一条微信读书进度数据换算成 Notion「书架」库的属性目标值。

    ``entry`` 是书架条目与 ``/book/getprogress`` 的合并结果，字段可能不全。
    **只输出接口确实给出的字段**：拿不到的字段不出现在返回值里，这样
    ``diff_state`` 就不会把 Notion 里的现有值改写成 0。

    状态判定与 ``weread.py`` 保持一致：
    ``markedStatus == 4`` 或 ``finishTime > 0`` 或书架 ``finishReading == 1`` → 已读（进度 100）；
    否则阅读时长 ≥ 60 秒 → 在读；其余 → 想读。

    注意：``阅读天数``（totalReadDay）微信读书网关并不提供，因此这里不会写它。
    """
    if not isinstance(entry, dict):
        raise TypeError("entry 必须是 dict")

    state = {}

    reading_time = _maybe_int(entry.get("readingTime"))
    if reading_time is not None:
        state["阅读时长"] = reading_time

    marked_status = _maybe_int(entry.get("markedStatus"))
    finish_time = _maybe_int(entry.get("finishTime")) or 0
    finish_reading = entry.get("finishReading")
    finished = (
        marked_status == 4
        or finish_time > 0
        or finish_reading in (1, "1", True)
    )

    raw_progress = _maybe_float(entry.get("progress"))
    if raw_progress is None:
        raw_progress = _maybe_float(entry.get("readingProgress"))
    if finished:
        # 已读完就是 100%，这里不依赖接口有没有给 progress
        state["阅读进度"] = 100.0
    elif raw_progress is not None:
        state["阅读进度"] = round(raw_progress / 100.0, 4)

    if finished:
        state["阅读状态"] = STATUS_FINISHED
    elif reading_time is not None:
        state["阅读状态"] = STATUS_READING if reading_time >= 60 else STATUS_WANT

    total_read_day = _maybe_int(entry.get("totalReadDay"))
    if total_read_day is not None:
        state["阅读天数"] = total_read_day

    last_ts = max(
        _maybe_int(entry.get("updateTime")) or 0,
        finish_time,
        _maybe_int(entry.get("readUpdateTime")) or 0,
        _maybe_int(entry.get("lastReadingDate")) or 0,
    )
    last_date = timestamp_to_date_string(last_ts)
    if last_date:
        state["最后阅读时间"] = last_date
    return state


def extract_state(page_summary):
    """从 ``NotionHelper.get_all_book()`` 的一条摘要里取出当前的进度类属性值。

    ``get_all_book()`` 已经把属性都取回来了，所以这里不需要额外请求 Notion。
    """
    page_summary = page_summary or {}
    return {
        "阅读状态": page_summary.get("status"),
        "阅读进度": page_summary.get("阅读进度"),
        "阅读时长": page_summary.get("readingTime"),
        "阅读天数": page_summary.get("阅读天数"),
        "最后阅读时间": timestamp_to_date_string(page_summary.get("最后阅读时间")),
    }


def diff_state(target, current):
    """返回**需要更新**的字段；没有变化时返回空 dict —— 这是"不重复写"的关键。"""
    changed = {}
    for key, want in (target or {}).items():
        have = (current or {}).get(key)
        if key == "阅读进度":
            if have is None or abs(_as_float(have) - _as_float(want)) > 1e-6:
                changed[key] = want
        elif key in NUMBER_FIELDS:
            if have is None or _as_int(have) != _as_int(want):
                changed[key] = want
        else:
            if have != want:
                changed[key] = want
    return changed


def to_notion_properties(changed):
    """把 diff 结果转成 Notion API 的 ``properties`` 结构。"""
    properties = {}
    if "阅读状态" in changed:
        properties["阅读状态"] = {"status": {"name": changed["阅读状态"]}}
    for name in NUMBER_FIELDS:
        if name in changed:
            properties[name] = {"number": changed[name]}
    if "最后阅读时间" in changed:
        properties["最后阅读时间"] = {"date": {"start": changed["最后阅读时间"]}}
    return properties


def _merge_progress(api, book):
    """把书架条目与 /book/getprogress 的真实进度合并成一条 entry。"""
    entry = dict(book)
    book_id = entry.get("bookId")
    if not book_id:
        return entry
    try:
        progress = api.get_book_progress(book_id)
    except Exception as err:  # noqa: BLE001
        # 鉴权类错误继续往上抛：那要靠换 Key 解决，不能被当成"这本读不到"吞掉。
        if type(err).__name__ in ("WeReadAuthError", "WeReadApiError"):
            raise
        print("  [WARN] %s 进度读取失败（%s: %s）" % (book_id, type(err).__name__, err))
        return entry
    if isinstance(progress, dict):
        for key, value in progress.items():
            if value is not None:
                entry[key] = value
    if PROGRESS_SLEEP_SECONDS:
        time.sleep(PROGRESS_SLEEP_SECONDS)
    return entry


def load_entries(api):
    """取书架全量，并逐本补上 /book/getprogress 的真实阅读进度。

    书架接口不可用时退回"有笔记的书"口径，保证不比修复前更差。
    """
    books = []
    try:
        books = api.get_shelf_progress()
    except Exception as err:  # noqa: BLE001 - 见下方说明
        if type(err).__name__ in ("WeReadAuthError", "WeReadApiError"):
            raise
        print("  [WARN] 书架接口不可用（%s: %s），退回 /user/notebooks 口径" % (type(err).__name__, err))
        books = []

    if not books:
        entries = []
        for notebook in api.get_notebooklist() or []:
            if not isinstance(notebook, dict):
                continue
            book = notebook.get("book") if isinstance(notebook.get("book"), dict) else {}
            merged = dict(book)
            for key in ("bookId", "markedStatus", "readingProgress", "finishReading", "sort"):
                if notebook.get(key) is not None:
                    merged[key] = notebook.get(key)
            if merged.get("bookId"):
                entries.append(merged)
        print("  进度数据来源：/user/notebooks 回退（%d 本）" % len(entries))
        return entries

    enriched = [_merge_progress(api, book) for book in books if isinstance(book, dict)]
    with_numbers = sum(
        1
        for item in enriched
        if item.get("readingTime") is not None or item.get("progress") is not None
    )
    print(
        "  进度数据来源：/shelf/sync 书架 %d 本 + /book/getprogress（%d 本取到阅读数字）"
        % (len(enriched), with_numbers)
    )
    return enriched


def build_summary_source(entry, page, api):
    """拼出写「📖 书籍摘要」所需的信息。

    优先用「书架」库里已有的简介；没有才去问网关（避免每轮为每本书多打一次接口）。
    """
    entry = entry or {}
    page = page or {}
    data = {
        "title": entry.get("title") or page.get("书名"),
        "author": entry.get("author"),
    }
    intro = (page.get("简介") or "").strip()
    if not intro:
        book_id = entry.get("bookId")
        info = {}
        if book_id and hasattr(api, "get_book_info"):
            try:
                info = api.get_book_info(book_id) or {}
            except Exception as err:  # noqa: BLE001
                print("  [WARN] %s 书籍信息读取失败（%s: %s）" % (book_id, type(err).__name__, err))
                info = {}
        if isinstance(info, dict):
            intro = (info.get("intro") or "").strip()
            for key in ("publisher", "publishTime", "newRating", "isbn"):
                if info.get(key):
                    data[key] = info[key]
    if intro:
        data["intro"] = intro
    # 划线汇总：用 /user/notebooks（已缓存）里该书的 noteCount / reviewCount / sort
    try:
        from weread2notionpro.book_summary import notebook_note_stats

        data.update(notebook_note_stats(api, entry.get("bookId")))
    except Exception as err:  # noqa: BLE001 - 统计拿不到不影响摘要块其余内容
        print("  [WARN] 笔记统计读取失败（%s: %s）" % (type(err).__name__, err))
    return data


def sync_progress(api=None, helper=None, apply=True, limit=None, summaries=True):
    """把书架进度写回 Notion「书架」库，返回统计 dict。

    :param apply: False 时只打印将要发生的改动，不写任何 Notion 数据（dry-run）。
    :param limit: 只处理前 N 本，便于小范围试跑。
    :param summaries: 顺带给还没有摘要块的书籍页补一块「📖 书籍摘要」。
    """
    if api is None:
        from weread2notionpro.weread_api import WeReadApi

        api = WeReadApi()

    entries = load_entries(api)

    # 同一轮内按 bookId 去重，避免同一本书被处理两遍
    deduped = {}
    for entry in entries:
        book_id = (entry or {}).get("bookId")
        if book_id:
            deduped[str(book_id)] = entry
    entries = list(deduped.values())
    if limit:
        entries = entries[: int(limit)]

    summary = {
        "total": len(entries),
        "updated": 0,
        "unchanged": 0,
        "missing": 0,
        "failed": 0,
        "failures": [],
        "summaries": 0,
        "summary_failed": 0,
    }

    if not apply:
        for entry in entries:
            print("  [dry-run] %s -> %s" % (entry.get("bookId"), compute_notion_state(entry)))
        return summary

    if helper is None:
        from weread2notionpro.notion_helper import NotionHelper

        helper = NotionHelper()

    notion_books = helper.get_all_book()
    for entry in entries:
        book_id = str(entry.get("bookId"))
        title = entry.get("title") or book_id
        try:
            page = notion_books.get(book_id)
            if not page or not page.get("pageId"):
                summary["missing"] += 1
                continue
            changed = diff_state(compute_notion_state(entry), extract_state(page))
            if not changed:
                summary["unchanged"] += 1
            else:
                helper.update_page(
                    page_id=page["pageId"],
                    properties=to_notion_properties(changed),
                )
                summary["updated"] += 1
                print("  进度更新：%s → %s" % (title, sorted(changed.keys())))

            if summaries:
                try:
                    from weread2notionpro.book_summary import ensure_summary_block

                    book_data = build_summary_source(entry, page, api)
                    if ensure_summary_block(helper, page["pageId"], book_data, update=False):
                        summary["summaries"] += 1
                except Exception as err:  # noqa: BLE001 - 摘要失败不影响进度写入
                    summary["summary_failed"] += 1
                    print("  [WARN] 书籍摘要写入失败（%s）：%s: %s" % (title, type(err).__name__, err))
        except Exception as err:  # noqa: BLE001 - 单本失败不能中断整轮，但必须计数上报
            summary["failed"] += 1
            summary["failures"].append((title, "%s: %s" % (type(err).__name__, err)))
    return summary


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    dry_run = ("--dry-run" in args) or os.getenv("WEREAD_DRY_RUN") == "1"
    limit = None
    for index, arg in enumerate(args):
        if arg == "--limit" and index + 1 < len(args):
            limit = args[index + 1]

    print("开始同步读书进度..." + ("（dry-run 模式）" if dry_run else ""))
    try:
        summary = sync_progress(apply=not dry_run, limit=limit)
    except Exception as exc:  # noqa: BLE001 - 顶层兜底，保证退出码非 0
        import traceback

        traceback.print_exc()
        print("::error title=读书进度同步失败::%s: %s" % (type(exc).__name__, exc))
        return 1

    print(
        "读书进度同步结束：更新 %d 本 / 无变化 %d 本 / Notion 缺页 %d 本 / 失败 %d 本（共 %d 本）"
        % (
            summary["updated"],
            summary["unchanged"],
            summary["missing"],
            summary["failed"],
            summary["total"],
        )
    )
    print(
        "书籍摘要块：新增 %d 个 / 写入失败 %d 个"
        % (summary.get("summaries", 0), summary.get("summary_failed", 0))
    )
    for title, err in summary["failures"]:
        print("::error title=读书进度同步失败::%s: %s" % (title, err))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())