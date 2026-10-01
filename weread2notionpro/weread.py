import os
import sys
import traceback

from weread2notionpro.book_summary import ensure_summary_block
from weread2notionpro.notion_helper import NotionHelper
from weread2notionpro.weread_api import WeReadApi, WeReadApiError, WeReadAuthError
from notion_client import errors as notion_errors

from weread2notionpro.utils import (
    get_block,
    get_heading,
    get_number,
    get_number_from_result,
    get_quote,
    get_rich_text_from_result,
    get_table_of_contents,
)



RATING_MAP = {"poor": "\u2b50\ufe0f", "fair": "\u2b50\u2b50\u2b50", "good": "\u2b50\u2b50\u2b50\u2b50\u2b50"}
BOOK_ICON_URL = "https://www.notion.so/icons/book_gray.svg"


def _maybe_number(value):
    """取数字；拿不到（None / 空 / 非数字）返回 None —— 用 None 表示"接口没给"。"""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _resolve_reading_state(book_data):
    """从 book_data 解析 (阅读状态, 阅读进度, 阅读时长, 阅读天数)。

    规则与 progress.py 一致：``markedStatus == 4`` 或 ``finishedDate > 0`` → 已读（进度 100）；
    否则阅读时长 ≥ 60 秒 → 在读；其余 → 想读。
    **拿不到的字段返回 None**，由调用方决定不写，绝不补 0。
    """
    marked_status = _maybe_number(book_data.get("markedStatus"))
    finished_ts = _maybe_number(book_data.get("finishedDate")) or 0
    finished = marked_status == 4 or finished_ts > 0

    reading_time = _maybe_number(book_data.get("readingTime"))
    total_read_day = _maybe_number(book_data.get("totalReadDay"))

    raw_progress = book_data.get("readingProgress")
    progress = None
    if raw_progress is not None and raw_progress != "":
        try:
            progress = 100.0 if finished else round(float(raw_progress) / 100.0, 4)
        except (ValueError, TypeError):
            progress = None

    if finished:
        status = "已读"
    elif reading_time is None:
        status = None
    else:
        status = "在读" if reading_time >= 60 else "想读"
    return status, progress, reading_time, total_read_day
def get_bookmark_list(page_id, bookId):
    """获取我的划线"""
    filter = {
        "and": [
            {"property": "书籍", "relation": {"contains": page_id}},
            {"property": "blockId", "rich_text": {"is_not_empty": True}},
        ]
    }
    results = notion_helper.query_all_by_book(
        notion_helper.bookmark_database_id, filter
    )
    dict1 = {
        get_rich_text_from_result(x, "bookmarkId"): get_rich_text_from_result(
            x, "blockId"
        )
        for x in results
    }
    dict2 = {get_rich_text_from_result(x, "blockId"): x.get("id") for x in results}
    bookmarks = weread_api.get_bookmark_list(bookId)
    for i in bookmarks:
        if i.get("bookmarkId") in dict1:
            i["blockId"] = dict1.pop(i.get("bookmarkId"))
    for blockId in dict1.values():
        notion_helper.delete_block(blockId)
        notion_helper.delete_block(dict2.get(blockId))
    return bookmarks


def get_review_list(page_id,bookId):
    """获取笔记"""
    filter = {
        "and": [
            {"property": "书籍", "relation": {"contains": page_id}},
            {"property": "blockId", "rich_text": {"is_not_empty": True}},
        ]
    }
    results = notion_helper.query_all_by_book(notion_helper.review_database_id, filter)
    dict1 = {
        get_rich_text_from_result(x, "reviewId"): get_rich_text_from_result(
            x, "blockId"
        )
        for x in results
    }
    dict2 = {get_rich_text_from_result(x, "blockId"): x.get("id") for x in results}
    reviews = weread_api.get_review_list(bookId)
    for i in reviews:
        if i.get("reviewId") in dict1:
            i["blockId"] = dict1.pop(i.get("reviewId"))
    for blockId in dict1.values():
        notion_helper.delete_block(blockId)
        notion_helper.delete_block(dict2.get(blockId))
    return reviews


def check(bookId):
    """检查是否已经插入过"""
    filter = {"property": "BookId", "rich_text": {"equals": bookId}}
    response = notion_helper.query(
        database_id=notion_helper.book_database_id, filter=filter
    )
    if len(response["results"]) > 0:
        return response["results"][0]["id"]
    return None


def get_sort():
    """获取database中的最大Sort值"""
    filter = {"property": "Sort", "number": {"is_not_empty": True}}
    response = notion_helper.query(
        database_id=notion_helper.book_database_id,
        filter=filter,
        page_size=100,
    )
    if len(response.get("results")) > 0:
        return max(
            r.get("properties", {}).get("Sort", {}).get("number") or 0
            for r in response.get("results")
        )
    return 0



def sort_notes(page_id, chapter, bookmark_list):
    """对笔记进行排序"""
    bookmark_list = sorted(
        bookmark_list,
        key=lambda x: (
            x.get("chapterUid", 1),
            0
            if (x.get("range", "") == "" or x.get("range").split("-")[0] == "")
            else int(x.get("range").split("-")[0]),
        ),
    )

    notes = []
    if chapter != None:
        filter = {"property": "书籍", "relation": {"contains": page_id}}
        results = notion_helper.query_all_by_book(
            notion_helper.chapter_database_id, filter
        )
        dict1 = {
            get_number_from_result(x, "chapterUid"): get_rich_text_from_result(
                x, "blockId"
            )
            for x in results
        }
        dict2 = {get_rich_text_from_result(x, "blockId"): x.get("id") for x in results}
        d = {}
        for data in bookmark_list:
            chapterUid = data.get("chapterUid", 1)
            if chapterUid not in d:
                d[chapterUid] = []
            d[chapterUid].append(data)
        for key, value in d.items():
            if key in chapter:
                if key in dict1:
                    chapter.get(key)["blockId"] = dict1.pop(key)
                notes.append(chapter.get(key))
            notes.extend(value)
        for blockId in dict1.values():
            notion_helper.delete_block(blockId)
            notion_helper.delete_block(dict2.get(blockId))
    else:
        notes.extend(bookmark_list)
    return notes


def append_blocks(id, contents):
    print(f"笔记数{len(contents)}")
    before_block_id = None
    block_children = notion_helper.get_block_children(id)
    if len(block_children) > 0 and block_children[0].get("type") == "table_of_contents":
        before_block_id = block_children[0].get("id")
    else:
        # 新页面或没有 TOC 的页面：直接追加，不使用 after
        before_block_id = None
    blocks = []
    sub_contents = []
    l = []
    for content in contents:
        if len(blocks) == 100:
            results = append_blocks_to_notion(id, blocks, before_block_id, sub_contents)
            before_block_id = results[-1].get("blockId")
            l.extend(results)
            blocks.clear()
            sub_contents.clear()
            if not notion_helper.sync_bookmark and content.get("type")==0:
                continue
            blocks.append(content_to_block(content))
            sub_contents.append(content)
        elif "blockId" in content:
            if len(blocks) > 0:
                l.extend(
                    append_blocks_to_notion(id, blocks, before_block_id, sub_contents)
                )
                blocks.clear()
                sub_contents.clear()
            before_block_id = content["blockId"]
        else:
            if not notion_helper.sync_bookmark and content.get("type")==0:
                continue
            blocks.append(content_to_block(content))
            sub_contents.append(content)
    
    if len(blocks) > 0:
        l.extend(append_blocks_to_notion(id, blocks, before_block_id, sub_contents))
    for index, value in enumerate(l):
        print(f"正在插入第{index+1}条笔记，共{len(l)}条")
        if "bookmarkId" in value:
            notion_helper.insert_bookmark(id, value)
        elif "reviewId" in value:
            notion_helper.insert_review(id, value)
        else:
            notion_helper.insert_chapter(id, value)


def content_to_block(content):
    if "bookmarkId" in content:
        return get_block(
            content.get("markText",""),
            notion_helper.block_type,
            notion_helper.show_color,
            content.get("style"),
            content.get("colorStyle"),
            content.get("reviewId"),
        )
    elif "reviewId" in content:
        return get_block(
            content.get("content",""),
            notion_helper.block_type,
            notion_helper.show_color,
            content.get("style"),
            content.get("colorStyle"),
            content.get("reviewId"),
        )
    else:
        return get_heading(content.get("level"), content.get("title"))


def append_blocks_to_notion(id, blocks, after, contents):
    if not after:
        # 新页面：直接追加到页面底部
        response = notion_helper.append_blocks(
            block_id=id, children=blocks
        )
    else:
        response = notion_helper.append_blocks_after(
            block_id=id, children=blocks, after=after
        )
    results = response.get("results")
    l = []
    for index, content in enumerate(contents):
        result = results[index]
        if content.get("abstract") != None and content.get("abstract") != "":
            notion_helper.append_blocks(
                block_id=result.get("id"), children=[get_quote(content.get("abstract"))]
            )
        content["blockId"] = result.get("id")
        l.append(content)
    return l


# dry-run 时完全不构造 Notion 客户端：既保证"只读不写"，也让预检不依赖 NOTION_* 凭据。
DRY_RUN = ("--dry-run" in sys.argv) or (os.getenv("WEREAD_DRY_RUN") == "1")

weread_api = WeReadApi()
notion_helper = None if DRY_RUN else NotionHelper()

def insert_book_to_notion(book_data, cover, page_id, bookId, title, sort):
    """插入/更新书籍信息到Notion（对齐原版 book.py 的 insert_book_to_notion 效果）"""
    properties = {}
    
    # 书名
    book_title = book_data.get("title") or title or ""
    if isinstance(book_title, dict):
        book_title = book_title.get("title", "")
    if book_title:
        properties["书名"] = {"title": [{"text": {"content": str(book_title)}}]}
    
    # BookId
    if bookId:
        properties["BookId"] = {"rich_text": [{"text": {"content": str(bookId)}}]}
    
    # Sort
    try:
        properties["Sort"] = {"number": int(sort) if sort else 0}
    except (ValueError, TypeError):
        properties["Sort"] = {"number": 0}
    
    # 阅读状态：拿不到就整条不写（旧实现默认"想读"，会把已读完的书改错）
    status = book_data.get("阅读状态")
    if status:
        if not isinstance(status, str):
            status = str(status)
        properties["阅读状态"] = {"status": {"name": status}}
    
    # 阅读时长
    rt = book_data.get("阅读时长")
    if rt is not None:
        try:
            properties["阅读时长"] = {"number": int(rt)}
        except (ValueError, TypeError):
            pass
    
    # 阅读天数
    rtd = book_data.get("阅读天数")
    if rtd is not None:
        try:
            properties["阅读天数"] = {"number": int(rtd)}
        except (ValueError, TypeError):
            pass
    
    # 阅读进度
    rp = book_data.get("阅读进度")
    if rp is not None:
        try:
            properties["阅读进度"] = {"number": float(rp)}
        except (ValueError, TypeError):
            pass
    
    # 封面
    if cover and isinstance(cover, str) and cover.startswith("http"):
        properties["封面"] = {"files": [{"type": "external", "name": "Cover", "external": {"url": cover}}]}
    
    # 作者
    author_ids = book_data.get("作者", [])
    if author_ids and isinstance(author_ids, list):
        valid_ids = [str(aid) for aid in author_ids if aid]
        if valid_ids:
            properties["作者"] = {"relation": [{"id": aid} for aid in valid_ids]}
    
    # 分类
    cat_ids = book_data.get("分类", [])
    if cat_ids and isinstance(cat_ids, list):
        valid_cats = [str(cid) for cid in cat_ids if cid]
        if valid_cats:
            properties["分类"] = {"relation": [{"id": cid} for cid in valid_cats]}
    
    # 我的评分
    my_rating = book_data.get("我的评分", "")
    if my_rating and isinstance(my_rating, str) and my_rating.strip():
        properties["我的评分"] = {"select": {"name": my_rating.strip()}}
    
    # 评分
    rating = book_data.get("评分")
    if rating is not None:
        try:
            properties["评分"] = {"number": int(rating)}
        except (ValueError, TypeError):
            pass
    
    # 简介
    intro = book_data.get("简介") or book_data.get("intro") or ""
    if intro and isinstance(intro, str) and intro.strip():
        properties["简介"] = {"rich_text": [{"text": {"content": intro.strip()}}]}
    
    # ISBN
    isbn = book_data.get("ISBN") or book_data.get("isbn") or ""
    if isbn and isinstance(isbn, str) and isbn.strip():
        properties["ISBN"] = {"rich_text": [{"text": {"content": isbn.strip()}}]}
    
    # 链接
    weread_url = book_data.get("链接") or book_data.get("url") or ""
    if not weread_url and bookId:
        weread_url = "https://weread.qq.com/web/reader/" + str(bookId)
    if weread_url and isinstance(weread_url, str):
        properties["链接"] = {"url": weread_url.strip()}
    
    # 时间（完成/最后阅读时间）- 用于年/月/周/日关系
    time_str = book_data.get("时间", "")
    if time_str:
        try:
            ts = int(time_str)
            from datetime import datetime, timezone, timedelta
            dt = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8)))
            # Only set date if it's a reasonable year (1970-2100)
            if 1970 <= dt.year <= 2100:
                properties["时间"] = {"date": {"start": dt.strftime("%Y-%m-%d")}}
                notion_helper.get_date_relation(properties, dt)
            else:
                print(f"  [WARN] Sort timestamp {ts} yields year {dt.year}, skipping date relation")
        except (ValueError, TypeError, OSError) as e:
            print(f"  [WARN] Could not parse time_str={repr(time_str)}: {e}")
    
    # 开始阅读时间
    begin_date = book_data.get("开始阅读时间", "")
    if begin_date:
        try:
            ts = int(begin_date)
            from datetime import datetime, timezone, timedelta
            bd = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8)))
            if 1970 <= bd.year <= 2100:
                properties["开始阅读时间"] = {"date": {"start": bd.strftime("%Y-%m-%d")}}
        except (ValueError, TypeError, OSError):
            pass
    
    # 最后阅读时间
    last_date = book_data.get("最后阅读时间", "")
    if last_date:
        try:
            ts = int(last_date)
            from datetime import datetime, timezone, timedelta
            ld = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8)))
            if 1970 <= ld.year <= 2100:
                properties["最后阅读时间"] = {"date": {"start": ld.strftime("%Y-%m-%d")}}
        except (ValueError, TypeError, OSError):
            pass
    
    print(f"  Properties to update: {list(properties.keys())}")
    
    # 更新页面属性，并把封面与图标合并到同一次请求里。
    #
    # 历史坑：这里曾写成 notion_helper.client.pages.patch(...)，但 notion-client 的
    # PagesEndpoint 只有 create / retrieve / update，**根本没有 patch 方法**，于是每本书、
    # 每一轮都抛 AttributeError: 'PagesEndpoint' object has no attribute 'patch'，
    # 被 except 吞成一行 [WARN]，封面和图标从未真正写进 Notion。
    # 2026-10-01 手动跑的那一轮仍然如此：8 本书 → 8 次 cover + 8 次 icon 报错，run 却是绿的。
    # 正确做法：pages.update 本身就支持 cover / icon，一次请求即可。
    cover_object = None
    if isinstance(cover, str) and cover.startswith("http"):
        cover_object = {"type": "external", "external": {"url": cover}}
    return notion_helper.update_page(
        page_id=page_id,
        properties=properties,
        cover=cover_object,
        icon=cover_object,
    )



def ensure_book_in_notion(book):
    """如果书不在 Notion 书架中，自动创建（含完整书籍信息，对齐原版 book.py 效果）"""
    bookId = book.get("bookId")
    existing = check(bookId)
    if existing:
        return existing
    
    # 获取完整书籍信息
    book_info = weread_api.get_book_info(bookId)
    book_data = book_info if book_info else book.get("book", {})
    note_data = book.get("book", {})

    # /book/info 只有书籍元信息（书名/作者/封面/简介/评分…），**没有**阅读数字；
    # 阅读时长与进度必须另外取 /book/getprogress，否则新建页面的这些属性只能填 0。
    read_info = weread_api.get_read_info(bookId) if hasattr(weread_api, "get_read_info") else {}
    if isinstance(read_info, dict):
        for key, value in read_info.items():
            if value is not None and value != "":
                book_data[key] = value
    
    title = book_data.get("title", note_data.get("title", ""))
    author_name = book_data.get("author", note_data.get("author", ""))
    cover = book_data.get("cover", note_data.get("cover", ""))
    intro = book_data.get("intro", "")
    isbn = book_data.get("isbn", "")
    categories = book_data.get("categories", [])
    begin_date = book_data.get("beginReadingDate", "")
    last_date = book_data.get("lastReadingDate", "")
    finished_date = book_data.get("finishedDate", "")
    reading_time = _maybe_number(book_data.get("readingTime"))
    total_read_day = _maybe_number(book_data.get("totalReadDay"))
    new_rating = book_data.get("newRating", "")
    rating_detail = book_data.get("newRatingDetail", {})
    marked_status = _maybe_number(book_data.get("markedStatus"))
    finished_ts = _maybe_number(book_data.get("finishedDate")) or 0
    reading_progress = _maybe_number(book_data.get("readingProgress"))
    finished = marked_status == 4 or finished_ts > 0

    # 计算阅读状态：拿不到阅读数字时不写这个属性（宁可不写，也不改成"想读"）
    status = None
    if finished:
        status = "已读"
    elif reading_time is not None:
        status = "在读" if reading_time >= 60 else "想读"

    # 计算阅读进度
    read_progress = None
    if reading_progress is not None:
        read_progress = 100.0 if finished else round(reading_progress / 100.0, 4)
    
    # 评分映射
    rating_map = {"poor": "⭐️", "fair": "⭐️⭐️⭐️", "good": "⭐️⭐️⭐️⭐️⭐️"}
    my_rating = ""
    if rating_detail and rating_detail.get("myRating"):
        my_rating = rating_map.get(rating_detail.get("myRating"), "")
    elif status == "已读":
        my_rating = "未评分"
    
    # 时间
    time_str = finished_date or last_date or begin_date
    
    properties = {
        "书名": {"title": [{"text": {"content": title}}]},
        "BookId": {"rich_text": [{"text": {"content": bookId}}]},
        "Sort": {"number": book.get("sort", 0)},
    }
    # 进度类属性只在真的拿到数据时才写（旧实现写 0，会把已有的好数据覆盖掉）
    if status:
        properties["阅读状态"] = {"status": {"name": status}}
    if reading_time is not None:
        properties["阅读时长"] = {"number": reading_time}
    if total_read_day is not None:
        properties["阅读天数"] = {"number": total_read_day}
    if read_progress is not None:
        properties["阅读进度"] = {"number": read_progress}
    
    # 封面（替换 /s_ 为 /t7_）
    if cover:
        cover = cover.replace("/s_", "/t7_")
        if cover and cover.strip() and cover.startswith("http"):
            properties["封面"] = {"files": [{"name": "Cover", "type": "external", "external": {"url": cover}}]}
    
    # 作者（关联作者库）
    if author_name:
        author_ids = []
        for name in author_name.split(" "):
            aid = notion_helper.get_author_relation_id(name)
            if aid:
                author_ids.append(aid)
        if author_ids:
            properties["作者"] = {"relation": [{"id": aid} for aid in author_ids]}
    
    # 分类（关联分类库）
    if categories:
        cat_ids = []
        for cat in categories:
            cid = notion_helper.get_category_relation_id(cat.get("title", ""))
            if cid:
                cat_ids.append(cid)
        if cat_ids:
            properties["分类"] = {"relation": [{"id": cid} for cid in cat_ids]}
    
    # 豆瓣链接
    douban_url = book_data.get("douban_url", "")
    if douban_url:
        properties["豆瓣链接"] = {"url": douban_url}
    
    # 我的评分
    if my_rating:
        properties["我的评分"] = {"select": {"name": my_rating}}
    
    # 简介
    if intro:
        properties["简介"] = {"rich_text": [{"text": {"content": intro}}]}
    
    # ISBN
    if isbn:
        properties["ISBN"] = {"rich_text": [{"text": {"content": isbn}}]}
    
    # 链接
    weread_url = book_data.get("url", "") or ("https://weread.qq.com/web/book/" + bookId)
    properties["链接"] = {"url": weread_url}
    
    # 开始/最后/完成阅读时间
    if begin_date:
        try:
            from datetime import datetime
            bd = datetime.fromtimestamp(int(begin_date)).strftime("%Y-%m-%d")
            properties["开始阅读时间"] = {"date": {"start": bd}}
        except (ValueError, TypeError):
            pass
    if last_date:
        try:
            from datetime import datetime
            ld = datetime.fromtimestamp(int(last_date)).strftime("%Y-%m-%d")
            properties["最后阅读时间"] = {"date": {"start": ld}}
        except (ValueError, TypeError):
            pass
    if finished_date:
        try:
            from datetime import datetime, timezone, timedelta
            fd = datetime.fromtimestamp(int(finished_date), tz=timezone(timedelta(hours=8)))
            properties["时间"] = {"date": {"start": fd.strftime("%Y-%m-%d")}}
            # 年/月/周/日关系
            notion_helper.get_date_relation(properties, fd)
        except (ValueError, TypeError):
            pass
    elif time_str:
        try:
            from datetime import datetime, timezone, timedelta
            td = datetime.fromtimestamp(int(time_str), tz=timezone(timedelta(hours=8)))
            properties["时间"] = {"date": {"start": td.strftime("%Y-%m-%d")}}
            notion_helper.get_date_relation(properties, td)
        except (ValueError, TypeError):
            pass
    
    response = notion_helper.client.pages.create(
        parent={"database_id": notion_helper.book_database_id},
        properties=properties,
    )
    page_id = response.get("id")
    print(f"  自动创建书籍页面: {title} (ID: {page_id})")
    try:
        from weread2notionpro.book_summary import notebook_note_stats

        for stat_key, stat_value in notebook_note_stats(weread_api, bookId).items():
            book_data.setdefault(stat_key, stat_value)
    except Exception as exc:  # 统计拿不到不影响建页
        print(f"  [WARN] 读取笔记统计失败：{type(exc).__name__}: {exc}")
    try:
        ensure_summary_block(notion_helper, page_id, book_data)
    except Exception as exc:  # 摘要只是附加信息，不能把建页流程带崩
        print(f"  [WARN] 写入书籍摘要失败：{type(exc).__name__}: {exc}")
    return page_id


def _dry_run():
    """只校验微信读书侧凭据与数据可达性，不读写 Notion。"""
    print("[dry-run] 校验 WEREAD_API_KEY ...")
    ok, message = weread_api.check_credentials()
    print(f"[dry-run] {message}")
    if not ok:
        print(f"::error title=WEREAD_API_KEY 校验失败::{message}")
        return 1
    books = weread_api.get_notebooklist()
    print(f"[dry-run] 有笔记的书共 {len(books)} 本")
    for book in books[:20]:
        title = book.get("book", {}).get("title") or book.get("title", "未知书籍")
        print(f"[dry-run]   - {title}")
    api_data = weread_api.get_api_data()
    read_times = api_data.get("readTimes", {}) or {}
    print(f"[dry-run] 阅读时长记录 {len(read_times)} 天")
    print("[dry-run] 全部通过，未写入任何 Notion 数据")
    return 0


def main(dry_run=False):
    """同步微信读书笔记到 Notion。

    返回值就是进程退出码：0 = 全部成功；1 = 有书籍同步失败，需要人工介入。

    重要：这里刻意不再"吞掉"失败。旧实现在循环里 except Exception 打印一行就 continue，
    于是 2026-09-05 那次 run（33942853283）最后一本《深层认知》因网关断连失败，
    日志只有一行 [ERROR]，run 依然显示 success —— 同步内容悄悄变少，直到 60 天后
    workflow 被 GitHub 自动停用才被发现。
    """
    if dry_run or DRY_RUN:
        return _dry_run()

    notion_books = notion_helper.get_all_book()
    books = weread_api.get_notebooklist()
    if books is None:
        print("::error title=书籍列表为空::微信读书没有返回任何书籍")
        return 1

    synced_count = 0
    skipped_archived = []
    failed_books = []

    for index, book in enumerate(books):
        bookId = book.get("bookId")
        title = book.get("book", {}).get("title") or book.get("title", "未知书籍")
        sort = book.get("sort")
        
        if bookId not in notion_books:
            print(f"书籍《{title}》不在 Notion 书架中，自动创建...")
            page_id = ensure_book_in_notion(book)
            if not page_id:
                print(f"  创建失败，跳过")
                continue
            # Trust the page_id returned from ensure_book_in_notion
            # and add to cache to avoid re-creation
            notion_books[bookId] = {"pageId": page_id}
            print(f"  已缓存 pageId: {page_id}")
        else:
            page_id = notion_books.get(bookId).get("pageId")
            if not page_id:
                print(f"  pageId 为空，跳过")
                continue
        
        print(f"正在同步《{title}》,一共{len(books)}本，当前是第{index+1}本。")
        try:
            # 同步划线和笔记
            chapter = weread_api.get_chapter_info(bookId)
            bookmark_list = get_bookmark_list(page_id, bookId)
            reviews = get_review_list(page_id, bookId)
            print(f"  拉取到 划线 {len(bookmark_list)} 条 / 想法点评 {len(reviews)} 条")
            # 「📖 书籍摘要」块里的"划线汇总"用本轮真的拉到的条数，以及全部笔记里
            # 最新一条的时间；取不到就不写这一项（宁可写「未提供」，也不编数字）。
            note_stats = {"划线数": len(bookmark_list), "想法数": len(reviews)}
            note_times = [
                int(item["createTime"])
                for item in list(bookmark_list) + list(reviews)
                if str(item.get("createTime") or "").strip().isdigit()
            ]
            note_stats["最近划线"] = max(note_times) if note_times else None
            if len(bookmark_list) == 0 and len(reviews) == 0:
                print("  [WARN] 本书既无划线也无想法；若微信读书里确实有笔记，请检查 WEREAD_API_KEY 的权限范围")
            bookmark_list.extend(reviews)
            chapter_content = sort_notes(page_id, chapter, bookmark_list)
            append_blocks(page_id, chapter_content)

            # 始终更新书籍元信息（对齐原版 book.py 逻辑）
            # 合并三层数据来源：notebook + bookinfo + readinfo
            note_data = book.get("book", {})
            if not isinstance(note_data, dict):
                note_data = {}
            book_info = weread_api.get_bookinfo(bookId) if hasattr(weread_api, "get_bookinfo") else {}
            if not isinstance(book_info, dict):
                book_info = {}
            read_info = weread_api.get_read_info(bookId) if hasattr(weread_api, "get_read_info") else {}
            if not isinstance(read_info, dict):
                read_info = {}

            book_data = {}
            book_data.update(note_data)
            if book_info and isinstance(book_info, dict):
                # Only update with non-empty values from book_info
                for k, v in book_info.items():
                    if v and not isinstance(v, (list, dict)) or isinstance(v, (list, dict)):
                        book_data[k] = v
            if read_info and isinstance(read_info, dict):
                # Only update with non-empty values (don't let empty strings overwrite valid dates)
                for k, v in read_info.items():
                    if v is not None and v != "" and v != {}:
                        book_data[k] = v
                # Also merge readDetail and bookInfo selectively
                rd = read_info.get("readDetail", {})
                bi = read_info.get("bookInfo", {})
                if rd and isinstance(rd, dict) and rd:
                    for k, v in rd.items():
                        if v is not None and v != "":
                            book_data[k] = v
                if bi and isinstance(bi, dict) and bi:
                    for k, v in bi.items():
                        if v is not None and v != "":
                            book_data[k] = v

            # 进度类属性只在**真的拿到数据**时才写。
            # 旧实现给缺失字段补 0，把「书架」库里 50+ 本书的 阅读时长/阅读进度/阅读天数
            # 全部改写成了 0，连读完的书都被标成"想读"（2026-10-01 实测发现）。
            status, progress, reading_time, total_read_day = _resolve_reading_state(book_data)
            for attr, value in (
                ("阅读状态", status),
                ("阅读进度", progress),
                ("阅读时长", reading_time),
                ("阅读天数", total_read_day),
            ):
                if value is None:
                    book_data.pop(attr, None)
                else:
                    book_data[attr] = value
            book_data["评分"] = book_data.get("newRating", "")
            rd = book_data.get("newRatingDetail") or {}
            mrk = rd.get("myRating", "")
            book_data["我的评分"] = RATING_MAP.get(mrk, "") if mrk else ""
            if book_data.get("阅读状态") == "已读" and not book_data["我的评分"]:
                book_data["我的评分"] = "未评分"

            book_data["时间"] = (
                book_data.get("finishedDate")
                or book_data.get("lastReadingDate")
                or book_data.get("readingBookDate")
            )
            book_data["开始阅读时间"] = book_data.get("beginReadingDate")
            book_data["最后阅读时间"] = book_data.get("lastReadingDate")
            # Fallback: if dates are empty, use sort timestamp as last reading date
            if not book_data.get("时间") and sort:
                try:
                    ts = int(sort)
                    # Validate: sort should be a reasonable timestamp (after year 2000)
                    if 946684800 <= ts <= 4102444800:
                        book_data["时间"] = ts
                        book_data["最后阅读时间"] = ts
                except (ValueError, TypeError):
                    pass

            cover = (book_data.get("cover", "") or "").replace("/s_", "/t7_")
            if not cover or not cover.strip() or not cover.startswith("http"):
                cover = BOOK_ICON_URL

            author_name = book_data.get("author", "")
            if author_name and isinstance(author_name, str):
                book_data["作者"] = [
                    aid for aid in [notion_helper.get_author_relation_id(x) for x in author_name.split(" ")]
                    if aid
                ]
            else:
                book_data["作者"] = []

            categories = book_data.get("categories", [])
            if categories and isinstance(categories, list):
                book_data["分类"] = [
                    cid for cid in [notion_helper.get_category_relation_id(c.get("title", "") if isinstance(c, dict) else c) for c in categories]
                    if cid
                ]
            else:
                book_data["分类"] = []

            book_data["Sort"] = sort

            # Map API field names to Notion property names
            # intro -> 简介, isbn -> ISBN, url -> 链接
            if book_data.get("intro"):
                book_data["简介"] = book_data["intro"]
            if book_data.get("isbn"):
                book_data["ISBN"] = book_data["isbn"]
            if book_data.get("url"):
                book_data["链接"] = book_data["url"]
            # categories -> 分类 (already handled above as relation IDs)
            
            for stat_key, stat_value in note_stats.items():
                if stat_value is not None:
                    book_data[stat_key] = stat_value
            insert_book_to_notion(book_data, cover, page_id, bookId, title, sort)
            try:
                ensure_summary_block(notion_helper, page_id, book_data)
            except Exception as exc:  # 摘要只是附加信息，不能把整本书的同步带崩
                print(f"  [WARN] 写入书籍摘要失败：{type(exc).__name__}: {exc}")

            print(f"  Done syncing: {title}")
            synced_count += 1
        except WeReadAuthError as e:
            # 鉴权失败和"某一本书"无关，不能跳过：立刻中止整轮，让 run 明确变红。
            print(f"  [FATAL] 微信读书 API Key 失效，中止本轮同步（已处理 {synced_count} 本）")
            print(f"::error title=WEREAD_API_KEY 失效::{e}")
            raise
        except notion_errors.APIResponseError as e:
            err_msg = str(e)
            if "archived ancestor" in err_msg.lower() or "can'" in err_msg or "Can'" in err_msg:
                print(f"  [WARN] Page archived, skipping: {title}")
                skipped_archived.append(title)
            else:
                print(f"  [ERROR] Sync failed for {title}: {err_msg}")
                print(f"::error title=Notion 接口错误::{title}: {err_msg}")
                failed_books.append((title, err_msg))
            continue
        except Exception as e:
            print(f"  [ERROR] Unexpected error for {title}: {e}")
            traceback.print_exc()
            print(f"::error title=同步失败::{title}: {e}")
            failed_books.append((title, str(e)))
            continue

    # —— 读书进度同步 ——
    # 上面的循环只覆盖 /user/notebooks（"有笔记的书"）。"在读但还没划线"的书
    # 阅读进度永远不动，这是需求里"读书进度要能同步"的实际缺口。
    # 这里用书架全量再跑一遍，只更新已有书籍页的属性，不新建页面 → 幂等、可重复执行。
    try:
        from weread2notionpro.progress import sync_progress

        progress_summary = sync_progress(api=weread_api, helper=notion_helper)
        print(
            f"  读书进度：更新 {progress_summary['updated']} 本 / "
            f"无变化 {progress_summary['unchanged']} 本 / "
            f"Notion 缺页 {progress_summary['missing']} 本 / "
            f"失败 {len(progress_summary['failures'])} 本 / "
            f"书籍摘要块新增 {progress_summary.get('summaries', 0)} 个"
        )
        for progress_title, progress_err in progress_summary["failures"]:
            print(f"::error title=读书进度同步失败::{progress_title}: {progress_err}")
            failed_books.append((progress_title, "读书进度: %s" % progress_err))
    except WeReadAuthError:
        raise
    except Exception as exc:  # noqa: BLE001 - 进度是增量能力，不能让它把整轮同步带崩，但必须显性记录
        print(f"::error title=读书进度同步异常::{type(exc).__name__}: {exc}")
        failed_books.append(("-读书进度同步-", "%s: %s" % (type(exc).__name__, exc)))

    print(
        f"同步结束：成功 {synced_count} 本，归档跳过 {len(skipped_archived)} 本，"
        f"失败 {len(failed_books)} 本（本轮共 {len(books)} 本）"
    )
    if failed_books:
        # 有失败就把退出码置为 1 —— 这是"不再静默失败"的关键一步。
        print("以下书籍本轮同步失败，请查看日志中的 ::error:: 提示：")
        for failed_title, failed_err in failed_books:
            print(f"  - {failed_title} | {failed_err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
