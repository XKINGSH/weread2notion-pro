from weread2notionpro.notion_helper import NotionHelper
from weread2notionpro.weread_api import WeReadApi
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


weread_api = WeReadApi()
notion_helper = NotionHelper()

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
    
    # 阅读状态
    status = book_data.get("阅读状态", "想读")
    if not isinstance(status, str):
        status = str(status) if status else "想读"
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
    intro = book_data.get("简介", "")
    if intro and isinstance(intro, str) and intro.strip():
        properties["简介"] = {"rich_text": [{"text": {"content": intro.strip()}}]}
    
    # ISBN
    isbn = book_data.get("ISBN", "")
    if isbn and isinstance(isbn, str) and isbn.strip():
        properties["ISBN"] = {"rich_text": [{"text": {"content": isbn.strip()}}]}
    
    # 链接
    weread_url = book_data.get("链接", "") or ""
    if not weread_url and bookId:
        weread_url = "https://weread.qq.com/web/reader/" + str(bookId)
    if weread_url and isinstance(weread_url, str):
        properties["链接"] = {"url": weread_url.strip()}
    
    # 时间（完成/最后阅读时间）- 用于年/月/周/日关系
    time_str = book_data.get("时间", "")
    print(f"  [DEBUG] time_str={repr(time_str)}, finishedDate={repr(book_data.get('finishedDate'))}, lastReadingDate={repr(book_data.get('lastReadingDate'))}")
    if time_str:
        try:
            ts = int(time_str)
            from datetime import datetime, timezone, timedelta
            dt = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8)))
            properties["时间"] = {"date": {"start": dt.strftime("%Y-%m-%d"), "time_zone": "Asia/Shanghai"}}
            notion_helper.get_date_relation(properties, dt)
        except (ValueError, TypeError, OSError):
            pass
    
    # 开始阅读时间
    begin_date = book_data.get("开始阅读时间", "")
    print(f"  [DEBUG] begin_date={repr(begin_date)}, beginReadingDate={repr(book_data.get('beginReadingDate'))}")
    if begin_date:
        try:
            ts = int(begin_date)
            from datetime import datetime, timezone, timedelta
            bd = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8)))
            properties["开始阅读时间"] = {"date": {"start": bd.strftime("%Y-%m-%d"), "time_zone": "Asia/Shanghai"}}
        except (ValueError, TypeError, OSError):
            pass
    
    # 最后阅读时间
    last_date = book_data.get("最后阅读时间", "")
    if last_date:
        try:
            ts = int(last_date)
            from datetime import datetime, timezone, timedelta
            ld = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8)))
            properties["最后阅读时间"] = {"date": {"start": ld.strftime("%Y-%m-%d"), "time_zone": "Asia/Shanghai"}}
        except (ValueError, TypeError, OSError):
            pass
    
    print(f"  Properties to update: {list(properties.keys())}")
    
    # 更新页面
    icon = {"type": "external", "external": {"url": cover}} if cover else None
    result = notion_helper.update_page(page_id=page_id, properties=properties, cover=icon)
    return result



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
    
    title = book_data.get("title", note_data.get("title", ""))
    author_name = book_data.get("author", note_data.get("author", ""))
    cover = book_data.get("cover", note_data.get("cover", ""))
    intro = book_data.get("intro", "")
    isbn = book_data.get("isbn", "")
    categories = book_data.get("categories", [])
    begin_date = book_data.get("beginReadingDate", "")
    last_date = book_data.get("lastReadingDate", "")
    finished_date = book_data.get("finishedDate", "")
    reading_time = book_data.get("readingTime", 0)
    total_read_day = book_data.get("totalReadDay", 0)
    new_rating = book_data.get("newRating", "")
    rating_detail = book_data.get("newRatingDetail", {})
    marked_status = book_data.get("markedStatus", 1)
    reading_progress = book_data.get("readingProgress", 0)
    
    # 计算阅读状态
    status = "想读"
    if marked_status == 4:
        status = "已读"
    elif reading_time >= 60:
        status = "在读"
    
    # 计算阅读进度
    try:
        read_progress = 100 if marked_status == 4 else float(reading_progress or 0) / 100
    except (ValueError, TypeError):
        read_progress = 0
    
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
        "阅读状态": {"status": {"name": status}},
        "阅读时长": {"number": reading_time},
        "阅读天数": {"number": total_read_day},
        "阅读进度": {"number": read_progress},
    }
    
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
    return page_id


def main():
    notion_books = notion_helper.get_all_book()
    books = weread_api.get_notebooklist()
    if books is None:
        print("没有获取到书籍列表")
        return
    
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
            if book_info:
                book_data.update(book_info)
            if read_info:
                book_data.update(read_info.get("readDetail", {}))
                book_data.update(read_info.get("bookInfo", {}))

            try:
                ms = book_data.get("markedStatus", 1)
                book_data["阅读进度"] = (
                    100 if ms == 4 else float(book_data.get("readingProgress", 0) or 0) / 100
                )
            except (ValueError, TypeError):
                book_data["阅读进度"] = 0

            if ms == 4:
                book_data["阅读状态"] = "已读"
            elif book_data.get("readingTime", 0) >= 60:
                book_data["阅读状态"] = "在读"
            else:
                book_data["阅读状态"] = "想读"

            book_data["阅读时长"] = book_data.get("readingTime", 0)
            book_data["阅读天数"] = book_data.get("totalReadDay", 0)
            book_data["评分"] = book_data.get("newRating", "")
            rd = book_data.get("newRatingDetail") or {}
            mrk = rd.get("myRating", "")
            book_data["我的评分"] = RATING_MAP.get(mrk, "") if mrk else ""
            if book_data["阅读状态"] == "已读" and not book_data["我的评分"]:
                book_data["我的评分"] = "未评分"

            book_data["时间"] = (
                book_data.get("finishedDate")
                or book_data.get("lastReadingDate")
                or book_data.get("readingBookDate")
            )
            book_data["开始阅读时间"] = book_data.get("beginReadingDate")
            book_data["最后阅读时间"] = book_data.get("lastReadingDate")

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

            insert_book_to_notion(book_data, cover, page_id, bookId, title, sort)

            print(f"  Done syncing: {title}")
        except notion_errors.APIResponseError as e:
            err_msg = str(e)
            if "archived ancestor" in err_msg.lower() or "can'" in err_msg or "Can'" in err_msg:
                print(f"  [WARN] Page archived, skipping: {title}")
            else:
                print(f"  [ERROR] Sync failed for {title}: {err_msg}")
            continue
        except Exception as e:
            import traceback
            print(f"  [ERROR] Unexpected error for {title}: {e}")
            traceback.print_exc()
            continue

if __name__ == "__main__":
    main()
