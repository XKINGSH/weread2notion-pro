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

def ensure_book_in_notion(book):
    """如果书不在 Notion 书架中，自动创建（含完整书籍信息）"""
    bookId = book.get("bookId")
    existing = check(bookId)
    if existing:
        return existing
    
    book_info = weread_api.get_book_info(bookId)
    book_data = book_info if book_info else book.get("book", {})
    
    title = book_data.get("title", "")
    author_name = book_data.get("author", "")
    cover = book_data.get("cover", "")
    
    properties = {
        "书名": {"title": [{"text": {"content": title}}]},
        "BookId": {"rich_text": [{"text": {"content": bookId}}]},
        "Sort": {"number": book.get("sort", 0)},
    }
    
    # 封面
    if cover:
        properties["封面"] = {"files": [{"name": "Cover", "type": "external", "external": {"url": cover}}]}
    
    # 作者（尝试查找或创建）
    if author_name:
        author_id = notion_helper.get_author_relation_id(author_name)
        if author_id:
            properties["作者"] = {"relation": [{"id": author_id}]}
    
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
    if books != None:
        for index, book in enumerate(books):
            bookId = book.get("bookId")
            title = book.get("book").get("title")
            sort = book.get("sort")
            
            # 如果书不在 Notion 中，自动创建
            if bookId not in notion_books:
                print(f"书籍《{title}》不在 Notion 书架中，自动创建...")
                page_id = ensure_book_in_notion(book)
            else:
                page_id = notion_books.get(bookId).get("pageId")
                # 检查是否有新笔记需要同步
                if sort == notion_books.get(bookId).get("Sort"):
                    continue
            
            print(f"正在同步《{title}》,一共{len(books)}本，当前是第{index+1}本。")
            try:
                chapter = weread_api.get_chapter_info(bookId)
                bookmark_list = get_bookmark_list(page_id, bookId)
                reviews = get_review_list(page_id, bookId)
                bookmark_list.extend(reviews)
                chapter_content = sort_notes(page_id, chapter, bookmark_list)
                append_blocks(page_id, chapter_content)
                
                # 更新书籍信息
                book_data = book.get("book", {})
                author_name = book_data.get("author", "")
                cover = book_data.get("cover", "")
                
                update_props = {"Sort": get_number(sort)}
                
                if cover:
                    update_props["封面"] = {"files": [{"name": "Cover", "type": "external", "external": {"url": cover}}]}
                
                if author_name:
                    author_id = notion_helper.get_author_relation_id(author_name)
                    if author_id:
                        update_props["作者"] = {"relation": [{"id": author_id}]}
                
                update_props["阅读状态"] = {"select": {"name": "在读"}}
                
                notion_helper.update_book_page(page_id=page_id, properties=update_props)
                print(f"  ✅ 《{title}》同步完成")
            except notion_errors.APIResponseError as e:
                err_msg = str(e)
                if "archived ancestor" in err_msg.lower() or "can'" in err_msg or "Can'" in err_msg:
                    print(f"  ⚠️  《{title}》页面已被归档，跳过（请在Notion中恢复或删除该页面）")
                else:
                    print(f"  ❌ 《{title}》同步失败: {err_msg}")
                continue
            except Exception as e:
                print(f"  ❌ 《{title}》同步异常: {e}")
                continue

if __name__ == "__main__":
    main()
