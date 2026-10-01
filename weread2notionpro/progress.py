"""读书进度同步：阅读进度 / 阅读时长 / 阅读天数 / 阅读状态 / 最后阅读时间。

为什么单独一个模块
------------------
``weread.py`` 的主循环只遍历 ``/user/notebooks``（"有笔记的书"），所以
「在读但还没有划线」的书，阅读进度永远不会回写 Notion —— 这正是需求里
"读书进度要能同步" 的实际缺口。本模块以**书架全量**为输入，只更新**已有**书籍页的
属性，不新建页面，因此天然幂等、可以反复执行。

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
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))

STATUS_FINISHED = "已读"
STATUS_READING = "在读"
STATUS_WANT = "想读"

# 与 notion_helper 里的属性类型保持一致
NUMBER_FIELDS = ("阅读进度", "阅读时长", "阅读天数")


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

    ``entry`` 至少要有 ``bookId``；``readingTime`` / ``totalReadDay`` /
    ``readingProgress`` / ``markedStatus`` / ``lastReadingDate`` 缺失时按 0 处理，
    不会抛异常 —— 上游接口字段一旦改名，这里退化为"不更新"而不是"整轮崩溃"。

    状态判定与 ``weread.py`` / ``book.py`` 保持一致：
    ``markedStatus == 4`` → 已读（进度 100）；否则阅读时长 ≥ 60 秒 → 在读；其余 → 想读。
    """
    if not isinstance(entry, dict):
        raise TypeError("entry 必须是 dict")

    marked_status = _as_int(entry.get("markedStatus") or 1)
    reading_time = _as_int(entry.get("readingTime"))
    total_read_day = _as_int(entry.get("totalReadDay"))
    raw_progress = _as_float(entry.get("readingProgress"))

    if marked_status == 4:
        status = STATUS_FINISHED
        notion_progress = 100.0
    elif reading_time >= 60:
        status = STATUS_READING
        notion_progress = raw_progress / 100.0
    else:
        status = STATUS_WANT
        notion_progress = raw_progress / 100.0

    state = {
        "阅读状态": status,
        "阅读进度": round(notion_progress, 4),
        "阅读时长": reading_time,
        "阅读天数": total_read_day,
    }
    last_date = timestamp_to_date_string(entry.get("lastReadingDate"))
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


def load_entries(api):
    """取书架全量进度；书架接口不可用时退回"有笔记的书"，保证不比修复前更差。"""
    entries = []
    try:
        entries = api.get_shelf_progress()
    except Exception as err:  # noqa: BLE001 - 见下方说明
        # 鉴权类错误必须继续往上抛：那要靠换 Key 解决，不能被当成"接口不可用"吞掉。
        # 这里按类名判断，是为了不把 weread_api 的 import 提到模块顶层（单测要免依赖）。
        if type(err).__name__ in ("WeReadAuthError", "WeReadApiError"):
            raise
        print("  [WARN] 书架进度接口不可用（%s: %s），退回 /user/notebooks 口径" % (type(err).__name__, err))
        entries = []

    if entries:
        print("  进度数据来源：/shelf/sync（书架全量 %d 本）" % len(entries))
        return entries

    entries = []
    for notebook in api.get_notebooklist() or []:
        if not isinstance(notebook, dict):
            continue
        book = notebook.get("book") if isinstance(notebook.get("book"), dict) else {}
        merged = dict(book)
        if notebook.get("bookId"):
            merged["bookId"] = notebook.get("bookId")
        if merged.get("bookId"):
            entries.append(merged)
    print("  进度数据来源：/user/notebooks 回退（%d 本）" % len(entries))
    return entries


def sync_progress(api=None, helper=None, apply=True, limit=None):
    """把书架进度写回 Notion「书架」库，返回统计 dict。

    :param apply: False 时只打印将要发生的改动，不写任何 Notion 数据（dry-run）。
    :param limit: 只处理前 N 本，便于小范围试跑。
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
                continue
            helper.update_page(
                page_id=page["pageId"],
                properties=to_notion_properties(changed),
            )
            summary["updated"] += 1
            print("  进度更新：%s → %s" % (title, sorted(changed.keys())))
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
    for title, err in summary["failures"]:
        print("::error title=读书进度同步失败::%s: %s" % (title, err))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())