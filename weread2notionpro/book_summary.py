"""「📖 书籍摘要」块：把一本书的摘要信息与划线汇总写进 Notion 书籍页的正文。

为什么要有它
------------
书籍页正文原来只有划线和想法，看不到"这本书是什么"，也看不到"这本书我一共划了多少"。
2026-10-01 用户明确要求正文里能看到书籍摘要／总结信息，以及划线汇总，所以这里把
微信读书自身返回的数据写成一个 callout 块。

数据来源（全部是接口真实返回的字段，没有自造内容）
--------------------------------------------------
* 书籍元信息：``/book/info`` → ``intro``（简介）、``publisher``、``publishTime``、
  ``newRating``、``isbn``、``author``、``title``；书库里已有的「简介」属性也复用。
* 划线汇总：``/user/notebooks`` 每本书的 ``noteCount``（划线数）、``reviewCount``
  （想法数）、``sort``（该书最近一条笔记的时间戳，用于"最近划线"）。
  同一次同步里实测到的条数（如本轮拉到 27 条划线）优先使用。
* **接口没返回的字段一律写「未提供」**，不用模型生成的内容冒充书籍简介或总结。
  微信读书官方 Agent API 目前**不提供**任何 AI 总结字段（已逐个 tool 核对）。

设计要点
--------
1. **幂等**：页面里已经有摘要块就更新它，没有才插入一块，反复同步也不会越写越多；
2. 块放在目录（table_of_contents）之后、划线正文之前，打开书页第一眼就能看到；
3. 单条 rich_text 上限 2000 字符，这里按 1900 切分。
"""

import time
from datetime import datetime, timedelta, timezone

SUMMARY_MARKER = "📖 书籍摘要"
SUMMARY_ICON = "📖"
SUMMARY_COLOR = "blue_background"
RICH_TEXT_CHUNK = 1900
MISSING = "未提供"
CST = timezone(timedelta(hours=8))


def _clean(value):
    """把各种形态的值转成干净的字符串；拿不到返回 ""。"""
    if value is None:
        return ""
    if isinstance(value, dict):
        value = value.get("title") or value.get("content") or ""
    if isinstance(value, (list, tuple)):
        value = "、".join(_clean(item) for item in value if _clean(item))
    return str(value).strip()


def _has(value):
    """判断一个值是否算"接口给了"（0 也算给了，空串/None 不算）。"""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    return True


def _number(value):
    """尽力取整数；拿不到返回 None。"""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _format_timestamp(value):
    """时间戳（秒）→ 北京时间 YYYY-MM-DD；拿不到返回 ""。"""
    ts = _number(value)
    if ts is None or ts <= 0:
        return ""
    try:
        return datetime.fromtimestamp(ts, tz=CST).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return ""


def notebook_note_stats(api, book_id):
    """从 ``/user/notebooks``（进程内已缓存，不额外打网关）取一本书的笔记统计。

    返回 ``{"划线数": n, "想法数": m, "最近划线": ts}``；某本书不在"有笔记的书"列表里、
    或接口没给对应字段时，对应键不出现 —— 调用方据此写「未提供」，不编数字。
    """
    if not book_id:
        return {}
    try:
        notebooks = api.get_notebooklist()
    except Exception:  # noqa: BLE001 - 统计是附加信息，拿不到就算了
        return {}
    for notebook in notebooks or []:
        if not isinstance(notebook, dict) or notebook.get("bookId") != book_id:
            continue
        stats = {}
        if _has(notebook.get("noteCount")):
            stats["划线数"] = _number(notebook.get("noteCount"))
        if _has(notebook.get("reviewCount")):
            stats["想法数"] = _number(notebook.get("reviewCount"))
        if _has(notebook.get("sort")):
            stats["最近划线"] = _number(notebook.get("sort"))
        return stats
    return {}


def build_summary_body(book_data):
    """生成摘要块的正文；连书名都没有时返回 None。"""
    book_data = book_data or {}

    title = _clean(book_data.get("title") or book_data.get("书名"))
    author = _clean(book_data.get("author") or book_data.get("作者"))
    publisher = _clean(book_data.get("publisher")) or MISSING
    publish_time = _clean(book_data.get("publishTime"))[:10] or MISSING

    rating = book_data.get("newRating")
    rating_text = MISSING
    if _has(rating):
        try:
            rating_text = str(rating) if float(rating) > 0 else MISSING
        except (TypeError, ValueError):
            rating_text = str(rating)

    isbn = _clean(book_data.get("isbn")) or MISSING

    intro = _clean(book_data.get("intro") or book_data.get("简介"))
    intro_text = intro if intro else MISSING + "（微信读书接口未返回该书简介）"

    # 划线汇总：优先用本轮同步实测到的条数，其次用 /user/notebooks 的统计
    bookmark_count = _number(book_data.get("划线数"))
    if bookmark_count is None:
        bookmark_count = _number(book_data.get("bookmarkCount"))
    if bookmark_count is None:
        bookmark_count = _number(book_data.get("noteCount"))

    review_count = _number(book_data.get("想法数"))
    if review_count is None:
        review_count = _number(book_data.get("reviewCount"))

    last_note = _format_timestamp(
        book_data.get("最近划线") or book_data.get("lastNoteTime") or book_data.get("sort")
    )

    if bookmark_count is None and review_count is None:
        notes_text = MISSING + "（微信读书接口未返回该书的笔记统计）"
    else:
        notes_text = "划线 %d 条 · 想法 %d 条 · 最近划线：%s" % (
            bookmark_count if bookmark_count is not None else 0,
            review_count if review_count is not None else 0,
            last_note or MISSING,
        )

    if not title and not author:
        return None

    lines = []
    if title:
        lines.append("《%s》" % title)
    lines.append(
        "作者：%s ｜ 出版社：%s ｜ 出版时间：%s ｜ 微信读书评分：%s ｜ ISBN：%s"
        % (author or MISSING, publisher, publish_time, rating_text, isbn)
    )
    lines.append("划线汇总：" + notes_text)
    lines.append("简介：" + intro_text)
    return "\n".join(lines)


def build_summary_text(book_data):
    """摘要块的完整正文（含 marker），没有可写内容时返回 None。"""
    body = build_summary_body(book_data)
    if not body:
        return None
    return SUMMARY_MARKER + "\n" + body


def rich_text_objects(text):
    """把一段文本切成 Notion 允许的 rich_text 片段（单条上限 2000 字符）。"""
    chunks = []
    remaining = text or ""
    while remaining:
        chunks.append({"type": "text", "text": {"content": remaining[:RICH_TEXT_CHUNK]}})
        remaining = remaining[RICH_TEXT_CHUNK:]
    if not chunks:
        chunks = [{"type": "text", "text": {"content": ""}}]
    return chunks


def block_plain_text(block):
    """取出一个块的纯文本（判断摘要块是否已存在、内容是否需要更新）。"""
    if not isinstance(block, dict):
        return ""
    data = block.get(block.get("type"), {}) or {}
    if not isinstance(data, dict):
        return ""
    parts = []
    for item in data.get("rich_text", []) or []:
        parts.append(
            item.get("plain_text") or (item.get("text") or {}).get("content") or ""
        )
    return "".join(parts)


def make_callout(body):
    return {
        "type": "callout",
        "callout": {
            "rich_text": rich_text_objects(body),
            "icon": {"type": "emoji", "emoji": SUMMARY_ICON},
            "color": SUMMARY_COLOR,
        },
    }


def find_summary_block(children):
    """在页面子块里找摘要块，返回 (块, 目录块 id)。"""
    target = None
    after = None
    for child in children or []:
        if not isinstance(child, dict):
            continue
        if after is None and child.get("type") == "table_of_contents":
            after = child.get("id")
        if target is None and child.get("type") == "callout":
            if SUMMARY_MARKER in block_plain_text(child):
                target = child
    return target, after


def ensure_summary_block(helper, page_id, book_data, update=True):
    """确保书籍页正文里有摘要块；返回 True 表示这次真的写入/更新了。

    :param update: True 时内容变了就更新；False 只在缺失时补一块
                   （用于书架全量回填，避免每轮为每本书都重写一次块）。
    """
    text = build_summary_text(book_data)
    if not text:
        return False

    children = helper.get_block_children(page_id) or []
    target, after = find_summary_block(children)
    payload = make_callout(text)

    if target is not None:
        if not update:
            return False
        if block_plain_text(target).strip() == text.strip():
            return False
        helper.update_block(target["id"], {"callout": payload["callout"]})
        return True

    if after:
        helper.append_blocks_after(page_id, [payload], after)
    else:
        helper.append_blocks(page_id, [payload])
    return True
