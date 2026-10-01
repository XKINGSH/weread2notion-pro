import logging
import os
import re
import time

from notion_client import Client
import pendulum
from retrying import retry
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()
from weread2notionpro.utils  import (
    format_date,
    get_date,
    get_first_and_last_day_of_month,
    get_first_and_last_day_of_week,
    get_first_and_last_day_of_year,
    get_icon,
    get_number,
    get_relation,
    get_rich_text,
    get_title,
    timestamp_to_date,
    get_property_value,
)

def _secret_status(env_name):
    """只报告凭据是否已配置，绝不返回凭据本身。

    安全修复：旧代码把 NOTION_TOKEN / WEREAD_API_KEY 的**原文**写进
    Notion「设置」数据库的 NotinToken / WeReadCookie 两列，等于把凭据复制了一份
    到 Notion 里，任何能看这个页面的人都能读到。现在只写状态，不写值。
    """
    return "已配置（出于安全考虑不写入 Notion）" if os.getenv(env_name) else "未配置"


TAG_ICON_URL = "https://www.notion.so/icons/tag_gray.svg"
USER_ICON_URL = "https://www.notion.so/icons/user-circle-filled_gray.svg"
TARGET_ICON_URL = "https://www.notion.so/icons/target_red.svg"
BOOKMARK_ICON_URL = "https://www.notion.so/icons/bookmark_gray.svg"


class NotionHelper:
    database_name_dict = {
        "BOOK_DATABASE_NAME": "书架",
        "REVIEW_DATABASE_NAME": "笔记",
        "BOOKMARK_DATABASE_NAME": "划线",
        "DAY_DATABASE_NAME": "日",
        "WEEK_DATABASE_NAME": "周",
        "MONTH_DATABASE_NAME": "月",
        "YEAR_DATABASE_NAME": "年",
        "CATEGORY_DATABASE_NAME": "分类",
        "AUTHOR_DATABASE_NAME": "作者",
        "CHAPTER_DATABASE_NAME": "章节",
        "READ_DATABASE_NAME": "阅读记录",
        "SETTING_DATABASE_NAME": "设置",
    }
    database_id_dict = {}
    heatmap_block_id = None
    show_color = True
    block_type = "callout"
    sync_bookmark = True
    def __init__(self):
        self.client = Client(auth=os.getenv("NOTION_TOKEN"), log_level=logging.ERROR)
        self.__cache = {}
        self.page_id = self.extract_page_id(os.getenv("NOTION_PAGE"))
        self.search_database(self.page_id)
        for key in self.database_name_dict.keys():
            if os.getenv(key) != None and os.getenv(key) != "":
                self.database_name_dict[key] = os.getenv(key)
        self.book_database_id = self.database_id_dict.get(
            self.database_name_dict.get("BOOK_DATABASE_NAME")
        )
        self.review_database_id = self.database_id_dict.get(
            self.database_name_dict.get("REVIEW_DATABASE_NAME")
        )
        self.bookmark_database_id = self.database_id_dict.get(
            self.database_name_dict.get("BOOKMARK_DATABASE_NAME")
        )
        self.day_database_id = self.database_id_dict.get(
            self.database_name_dict.get("DAY_DATABASE_NAME")
        )
        self.week_database_id = self.database_id_dict.get(
            self.database_name_dict.get("WEEK_DATABASE_NAME")
        )
        self.month_database_id = self.database_id_dict.get(
            self.database_name_dict.get("MONTH_DATABASE_NAME")
        )
        self.year_database_id = self.database_id_dict.get(
            self.database_name_dict.get("YEAR_DATABASE_NAME")
        )
        self.category_database_id = self.database_id_dict.get(
            self.database_name_dict.get("CATEGORY_DATABASE_NAME")
        )
        self.author_database_id = self.database_id_dict.get(
            self.database_name_dict.get("AUTHOR_DATABASE_NAME")
        )
        self.chapter_database_id = self.database_id_dict.get(
            self.database_name_dict.get("CHAPTER_DATABASE_NAME")
        )
        self.read_database_id = self.database_id_dict.get(
            self.database_name_dict.get("READ_DATABASE_NAME")
        )  
        self.setting_database_id = self.database_id_dict.get(
            self.database_name_dict.get("SETTING_DATABASE_NAME")
        )
        self.update_book_database()
        if self.read_database_id is None:
            self.create_database()
        if self.setting_database_id is None:
            self.create_setting_database()
        if self.setting_database_id:
            self.insert_to_setting_database()

    def extract_page_id(self, notion_url):
        # 正则表达式匹配 32 个字符的 Notion page_id
        match = re.search(
            r"([a-f0-9]{32}|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})",
            notion_url,
        )
        if match:
            return match.group(0)
        else:
            raise Exception(f"获取NotionID失败，请检查输入的Url是否正确")

    def search_database(self, block_id):
        children = self.client.blocks.children.list(block_id=block_id)["results"]
        # 遍历子块
        for child in children:
            # 检查子块的类型
            if child["type"] == "child_database":
                self.database_id_dict[child.get("child_database").get("title")] = (
                    child.get("id")
                )
            elif child["type"] == "embed" and child.get("embed").get("url"):
                if child.get("embed").get("url").startswith("https://heatmap.malinkang.com/"):
                    self.heatmap_block_id = child.get("id")
            # 如果子块有子块，递归调用函数
            if "has_children" in child and child["has_children"]:
                self.search_database(child["id"])

    def update_book_database(self):
        """更新数据库"""
        response = self.client.databases.retrieve(database_id=self.book_database_id)
        id = response.get("id")
        properties = response.get("properties") or {}
        update_properties = {}
        prop_checks = [
            ("阅读时长", "number"),
            ("书架分类", "select"),
            ("豆瓣链接", "url"),
            ("我的评分", "select"),
            ("豆瓣短评", "rich_text"),
        ]
        for prop_name, expected_type in prop_checks:
            prop = properties.get(prop_name)
            if prop is None or prop.get("type") != expected_type:
                update_properties[prop_name] = {expected_type: {}}
        if len(update_properties) > 0:
            self.client.databases.update(database_id=id, properties=update_properties)
    def create_database(self):
        title = [
            {
                "type": "text",
                "text": {
                    "content": self.database_name_dict.get("READ_DATABASE_NAME"),
                },
            },
        ]
        properties = {
            "标题": {"title": {}},
            "时长": {"number": {}},
            "时间戳": {"number": {}},
            "日期": {"date": {}},
            "书架": {
                "relation": {
                    "database_id": self.book_database_id,
                    "single_property": {},
                }
            },
        }
        parent = parent = {"page_id": self.page_id, "type": "page_id"}
        self.read_database_id = self.client.databases.create(
            parent=parent,
            title=title,
            icon=get_icon("https://www.notion.so/icons/target_gray.svg"),
            properties=properties,
        ).get("id")    
        
    def create_setting_database(self):
        title = [
            {
                "type": "text",
                "text": {
                    "content": self.database_name_dict.get("SETTING_DATABASE_NAME"),
                },
            },
        ]
        properties = {
            "标题": {"title": {}},
            "NotinToken": {"rich_text": {}},
            "NotinPage": {"rich_text": {}},
            "WeReadCookie": {"rich_text": {}},
            "根据划线颜色设置文字颜色": {"checkbox": {}},
            "同步书签": {"checkbox": {}},
            # "Cookie状态": {
            #     "select": {
            #         "options": [
            #             {"name": "可用", "color": "green"},
            #             {"name": "过期", "color": "red"},
            #         ]
            #     }
            # },
            "样式": {
                "select": {
                    "options": [
                        {"name": "callout", "color": "blue"},
                        {"name": "quote", "color": "green"},
                        {"name": "paragraph", "color": "purple"},
                        {"name": "bulleted_list_item", "color": "yellow"},
                        {"name": "numbered_list_item", "color": "pink"},
                    ]
                }
            },
            "最后同步时间": {"date": {}},
        }
        parent = parent = {"page_id": self.page_id, "type": "page_id"}
        self.setting_database_id = self.client.databases.create(
            parent=parent,
            title=title,
            icon=get_icon("https://www.notion.so/icons/gear_gray.svg"),
            properties=properties,
        ).get("id")

    def insert_to_setting_database(self):
        existing_pages = self.query(database_id=self.setting_database_id, filter={"property": "标题", "title": {"equals": "设置"}}).get("results")
        properties = {
            "标题": {"title": [{"type": "text", "text": {"content": "设置"}}]},
            "最后同步时间": {"date": {"start": pendulum.now("Asia/Shanghai").isoformat()}},
            # 安全修复：不再把凭据原文写进 Notion，只写"是否已配置"。
            "NotinToken": {"rich_text": [{"type": "text", "text": {"content": _secret_status("NOTION_TOKEN")}}]},
            "NotinPage": {"rich_text": [{"type": "text", "text": {"content": os.getenv("NOTION_PAGE")}}]},
            "WeReadCookie": {"rich_text": [{"type": "text", "text": {"content": _secret_status("WEREAD_API_KEY")}}]},
        }
        if existing_pages:
            remote_properties = existing_pages[0].get("properties")
            self.show_color = get_property_value(remote_properties.get("根据划线颜色设置文字颜色"))
            self.sync_bookmark = get_property_value(remote_properties.get("同步书签"))
            self.block_type = get_property_value(remote_properties.get("样式"))
            page_id = existing_pages[0].get("id")
            self.client.pages.update(page_id=page_id, properties=properties)
        else:
            properties["根据划线颜色设置文字颜色"] = {"checkbox": True}
            properties["同步书签"] = {"checkbox": True}
            properties["样式"] = {"select": {"name": "callout"}}
            self.client.pages.create(
                parent={"database_id": self.setting_database_id},
                properties=properties,
            )
  
        

    _last_write_ts = 0.0

    def _throttle(self):
        """给 Notion 写入之间留出最小间隔，避免触发 3 req/s 的官方限流。

        旧实现在书多、划线多的时候是"能多快就多快"地连打 API，
        一旦被限流，重试 3 次 × 5 秒之后仍失败就把整本书丢掉。
        """
        interval = float(os.getenv("NOTION_MIN_INTERVAL", "0.35"))
        wait = self._last_write_ts + interval - time.time()
        if wait > 0:
            time.sleep(wait)
        NotionHelper._last_write_ts = time.time()

    def update_heatmap(self, block_id, url):
        # 更新 image block 的链接
        return self.client.blocks.update(block_id=block_id, embed={"url": url})

    def get_week_relation_id(self, date):
        year = date.isocalendar().year
        week = date.isocalendar().week
        week = f"{year}年第{week}周"
        start, end = get_first_and_last_day_of_week(date)
        properties = {"日期": get_date(format_date(start), format_date(end))}
        return self.get_relation_id(
            week, self.week_database_id, TARGET_ICON_URL, properties
        )

    def get_month_relation_id(self, date):
        month = date.strftime("%Y年%-m月")
        start, end = get_first_and_last_day_of_month(date)
        properties = {"日期": get_date(format_date(start), format_date(end))}
        return self.get_relation_id(
            month, self.month_database_id, TARGET_ICON_URL, properties
        )

    def get_year_relation_id(self, date):
        year = date.strftime("%Y")
        start, end = get_first_and_last_day_of_year(date)
        properties = {"日期": get_date(format_date(start), format_date(end))}
        return self.get_relation_id(
            year, self.year_database_id, TARGET_ICON_URL, properties
        )

    def get_day_relation_id(self, date):
        new_date = date.replace(hour=0, minute=0, second=0, microsecond=0)
        timestamp = (new_date - timedelta(hours=8)).timestamp()
        day = new_date.strftime("%Y年%m月%d日")
        properties = {
            "日期": get_date(format_date(date)),
            "时间戳": get_number(timestamp),
        }
        properties["年"] = get_relation(
            [
                self.get_year_relation_id(new_date),
            ]
        )
        properties["月"] = get_relation(
            [
                self.get_month_relation_id(new_date),
            ]
        )
        properties["周"] = get_relation(
            [
                self.get_week_relation_id(new_date),
            ]
        )
        return self.get_relation_id(
            day, self.day_database_id, TARGET_ICON_URL, properties
        )

    def get_relation_id(self, name, id, icon, properties={}):
        key = f"{id}{name}"
        if key in self.__cache:
            return self.__cache.get(key)
        filter = {"property": "标题", "title": {"equals": name}}
        response = self.query(database_id=id, filter=filter)
        if len(response.get("results")) == 0:
            parent = {"database_id": id, "type": "database_id"}
            properties["标题"] = get_title(name)
            page_id = self.client.pages.create(
                parent=parent, properties=properties, icon=get_icon(icon)
            ).get("id")
        else:
            page_id = response.get("results")[0].get("id")
        self.__cache[key] = page_id
        return page_id

    def get_author_relation_id(self, name):
        """查找或创建作者，返回作者页面ID"""
        if not name or not self.author_database_id:
            return None
        key = f"author_{self.author_database_id}_{name}"
        if key in self._NotionHelper__cache:
            return self._NotionHelper__cache.get(key)
        filter = {"property": "标题", "title": {"equals": name}}
        response = self.query(database_id=self.author_database_id, filter=filter)
        if len(response.get("results")) == 0:
            parent = {"database_id": self.author_database_id, "type": "database_id"}
            properties = {"标题": get_title(name)}
            page_id = self.client.pages.create(
                parent=parent, properties=properties, icon=get_icon(USER_ICON_URL)
            ).get("id")
        else:
            page_id = response.get("results")[0].get("id")
        self._NotionHelper__cache[key] = page_id
        return page_id


    def get_category_relation_id(self, name):
        """查找或创建分类，返回分类页面ID"""
        if not name or not self.category_database_id:
            return None
        key = f"category_{self.category_database_id}_{name}"
        if key in self._NotionHelper__cache:
            return self._NotionHelper__cache.get(key)
        filter = {"property": "标题", "title": {"equals": name}}
        response = self.query(database_id=self.category_database_id, filter=filter)
        if len(response.get("results")) == 0:
            parent = {"database_id": self.category_database_id, "type": "database_id"}
            properties = {"标题": get_title(name)}
            page_id = self.client.pages.create(
                parent=parent, properties=properties, icon=get_icon(TAG_ICON_URL)
            ).get("id")
        else:
            page_id = response.get("results")[0].get("id")
        self._NotionHelper__cache[key] = page_id
        return page_id
    def insert_bookmark(self, id, bookmark):
        icon = get_icon(BOOKMARK_ICON_URL)
        properties = {
            "Name": get_title(bookmark.get("markText", "")),
            "bookId": get_rich_text(bookmark.get("bookId")),
            "range": get_rich_text(bookmark.get("range")),
            "bookmarkId": get_rich_text(bookmark.get("bookmarkId")),
            "blockId": get_rich_text(bookmark.get("blockId")),
            "chapterUid": get_number(bookmark.get("chapterUid")),
            "bookVersion": get_number(bookmark.get("bookVersion")),
            "colorStyle": get_number(bookmark.get("colorStyle")),
            "type": get_number(bookmark.get("type")),
            "style": get_number(bookmark.get("style")),
            "书籍": get_relation([id]),
        }
        if "createTime" in bookmark:
            create_time = timestamp_to_date(int(bookmark.get("createTime")))
            properties["Date"] = get_date(create_time.strftime("%Y-%m-%d %H:%M:%S"))
            self.get_date_relation(properties, create_time)
        parent = {"database_id": self.bookmark_database_id, "type": "database_id"}
        self.create_page(parent, properties, icon)

    def insert_review(self, id, review):
        time.sleep(0.1)
        icon = get_icon(TAG_ICON_URL)
        properties = {
            "Name": get_title(review.get("content", "")),
            "bookId": get_rich_text(review.get("bookId")),
            "reviewId": get_rich_text(review.get("reviewId")),
            "blockId": get_rich_text(review.get("blockId")),
            "chapterUid": get_number(review.get("chapterUid")),
            "bookVersion": get_number(review.get("bookVersion")),
            "type": get_number(review.get("type")),
            "书籍": get_relation([id]),
        }
        if "range" in review:
            properties["range"] = get_rich_text(review.get("range"))
        if "star" in review:
            properties["star"] = get_number(review.get("star"))
        if "abstract" in review:
            properties["abstract"] = get_rich_text(review.get("abstract"))
        if "createTime" in review:
            create_time = timestamp_to_date(int(review.get("createTime")))
            properties["Date"] = get_date(create_time.strftime("%Y-%m-%d %H:%M:%S"))
            self.get_date_relation(properties, create_time)
        parent = {"database_id": self.review_database_id, "type": "database_id"}
        self.create_page(parent, properties, icon)

    def insert_chapter(self, id, chapter):
        time.sleep(0.1)
        icon = {"type": "external", "external": {"url": TAG_ICON_URL}}
        properties = {
            "Name": get_title(chapter.get("title")),
            "blockId": get_rich_text(chapter.get("blockId")),
            "chapterUid": {"number": chapter.get("chapterUid")},
            "chapterIdx": {"number": chapter.get("chapterIdx")},
            "readAhead": {"number": chapter.get("readAhead")},
            "updateTime": {"number": chapter.get("updateTime")},
            "level": {"number": chapter.get("level")},
            "书籍": {"relation": [{"id": id}]},
        }
        parent = {"database_id": self.chapter_database_id, "type": "database_id"}
        self.create_page(parent, properties, icon)

    @retry(stop_max_attempt_number=3, wait_fixed=5000)
    def update_book_page(self, page_id, properties):
        return self.client.pages.update(page_id=page_id, properties=properties)

    @retry(stop_max_attempt_number=3, wait_fixed=5000)
    def update_page(self, page_id, properties, cover=None, icon=None):
        """更新页面属性，可选同时更新封面与图标。

        notion-client 的 pages.update 本身就支持 cover / icon，
        不需要（也没有）pages.patch。只传非 None 的字段，避免把 null 发上去。
        """
        self._throttle()
        payload = {"page_id": page_id, "properties": properties}
        if cover is not None:
            payload["cover"] = cover
        if icon is not None:
            payload["icon"] = icon
        return self.client.pages.update(**payload)


    @retry(stop_max_attempt_number=3, wait_fixed=5000)
    def create_page(self, parent, properties, icon):
        self._throttle()
        return self.client.pages.create(parent=parent, properties=properties, icon=icon)

    @retry(stop_max_attempt_number=3, wait_fixed=5000)
    def create_book_page(self, parent, properties, icon):
        return self.client.pages.create(
            parent=parent, properties=properties, icon=icon, cover=icon
        )

    @retry(stop_max_attempt_number=3, wait_fixed=5000)
    def query(self, **kwargs):
        """查询数据库页面（兼容 notion-client 3.x，支持分页）"""
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        if not kwargs:
            return {"results": [], "has_more": False}
        if "database_id" in kwargs:
            db_id = kwargs.pop("database_id")
            filter_cond = kwargs.pop("filter", None)
            page_size = kwargs.pop("page_size", 100)
            start_cursor = kwargs.pop("start_cursor", None)
            server_side = getattr(self.client.databases, "query", None)
            if callable(server_side):
                return self._query_server_side(
                    server_side, db_id, filter_cond, page_size, start_cursor, kwargs
                )
            # —— 回退路径 ——
            # 当前 notion-client 版本没有 databases.query 时，才退回下面这套
            # "全库 search + 客户端过滤" 的老做法。
            all_results = []
            while True:
                search_kwargs = dict(kwargs)
                search_kwargs["page_size"] = min(page_size, 100)
                if start_cursor:
                    search_kwargs["start_cursor"] = start_cursor
                resp = self.client.search(**search_kwargs)
                # Filter by database_id
                filtered = []
                for r in resp.get("results", []):
                    parent = r.get("parent", {})
                    if parent.get("database_id") != db_id:
                        continue
                    if filter_cond:
                        if not self._matches_filter(r, filter_cond):
                            continue
                    filtered.append(r)
                all_results.extend(filtered)
                start_cursor = resp.get("next_cursor")
                if not resp.get("has_more") or not start_cursor:
                    break
                # Safety: prevent infinite loop
                if len(all_results) > 10000:
                    break
            return {"results": all_results, "has_more": False}
        return self.client.search(**kwargs) if kwargs else self.client.search()


    def _query_server_side(self, query_fn, db_id, filter_cond, page_size, start_cursor, extra):
        """走服务端 databases.query：筛选与分页都在 Notion 侧完成。

        旧实现（下面 _matches_filter 那条回退路径）用 client.search 拉全库再在本地筛，
        有两个后果：一是 search 有索引延迟、且会漏掉刚建的页面，于是同一本书可能被
        重复创建；二是把整库结果都拖回来，书一多就非常慢。
        """
        results = []
        while True:
            call_kwargs = dict(extra)
            if filter_cond is not None:
                call_kwargs["filter"] = filter_cond
            call_kwargs["page_size"] = min(int(page_size or 100), 100)
            if start_cursor:
                call_kwargs["start_cursor"] = start_cursor
            response = query_fn(database_id=db_id, **call_kwargs)
            results.extend(response.get("results", []))
            start_cursor = response.get("next_cursor")
            if not response.get("has_more") or not start_cursor:
                break
        return {"results": results, "has_more": False}

    def _matches_filter(self, page, filter_cond):
        """完整 filter 匹配，支持 title/rich_text/number/relation/date/checkbox/select"""
        if not isinstance(filter_cond, dict):
            return True
        if "and" in filter_cond:
            return all(self._matches_filter(page, f) for f in filter_cond["and"])
        if "or" in filter_cond:
            return any(self._matches_filter(page, f) for f in filter_cond["or"])
        prop_name = filter_cond.get("property")
        if not prop_name:
            return True
        props = page.get("properties", {})
        prop_val = props.get(prop_name)
        if not prop_val:
            return False
        prop_type = prop_val.get("type", "")

        # title filter
        if "title" in filter_cond:
            if prop_type != "title":
                return False
            titles = prop_val.get("title", [])
            if not titles:
                return False
            return titles[0].get("plain_text", "") == filter_cond["title"].get("equals", "")

        # rich_text filter
        if "rich_text" in filter_cond:
            if prop_type != "rich_text":
                return False
            if filter_cond["rich_text"].get("is_not_empty"):
                return len(prop_val.get("rich_text", [])) > 0
            texts = prop_val.get("rich_text", [])
            if not texts:
                return False
            return texts[0].get("plain_text", "") == filter_cond["rich_text"].get("equals", "")

        # number filter
        if "number" in filter_cond:
            if prop_type != "number":
                return False
            if filter_cond["number"].get("is_not_empty"):
                return prop_val.get("number") is not None
            return prop_val.get("number") == filter_cond["number"].get("equals")

        # relation filter (contains)
        if "relation" in filter_cond:
            if prop_type != "relation":
                return False
            rel_ids = [r.get("id") for r in (prop_val.get("relation") or []) if isinstance(r, dict)]
            if filter_cond["relation"].get("contains"):
                return filter_cond["relation"]["contains"] in rel_ids
            return bool(rel_ids)

        # date filter
        if "date" in filter_cond:
            if prop_type != "date":
                return False
            d = prop_val.get("date")
            if filter_cond["date"].get("is_not_empty"):
                return d is not None
            if d and filter_cond["date"].get("equals"):
                return d.get("start") == filter_cond["date"]["equals"]["start"]
            return True

        # checkbox filter
        if "checkbox" in filter_cond:
            if prop_type != "checkbox":
                return False
            return prop_val.get("checkbox") == filter_cond["checkbox"].get("equals", False)

        # select filter
        if "select" in filter_cond:
            if prop_type != "select":
                return False
            sel = prop_val.get("select", {})
            if filter_cond["select"].get("equals"):
                return sel.get("name") == filter_cond["select"]["equals"]
            if filter_cond["select"].get("is_not_empty"):
                return bool(sel)
            return True

        return True



    @retry(stop_max_attempt_number=3, wait_fixed=5000)
    def get_block_children(self, id):
        """列出页面的子块（带分页）。

        旧实现只取第一页（最多 100 个块），一本书划线一多，
        后面对不上的块就会走 append 而不是 update，造成重复插入。
        """
        results = []
        cursor = None
        while True:
            call_kwargs = {"block_id": id, "page_size": 100}
            if cursor:
                call_kwargs["start_cursor"] = cursor
            response = self.client.blocks.children.list(**call_kwargs)
            results.extend(response.get("results", []))
            cursor = response.get("next_cursor")
            if not response.get("has_more") or not cursor:
                break
        return results

    @retry(stop_max_attempt_number=3, wait_fixed=5000)
    def append_blocks(self, block_id, children):
        self._throttle()
        return self.client.blocks.children.append(block_id=block_id, children=children)

    @retry(stop_max_attempt_number=3, wait_fixed=5000)
    def append_blocks_after(self, block_id, children, after):
        #奇怪不知道为什么会多插入一个children，没找到问题，先暂时这么解决，搜索是否有parent
        parent = self.client.blocks.retrieve(after).get("parent")
        if(parent.get("type")=="block_id"):
            after = parent.get("block_id")
        return self.client.blocks.children.append(
            block_id=block_id, children=children, after=after
        )

    @retry(stop_max_attempt_number=3, wait_fixed=5000)
    def delete_block(self, block_id):
        try:
            return self.client.blocks.delete(block_id=block_id)
        except Exception as e:
            # 忽略已归档页面的删除错误
            if "archived" in str(e).lower():
                print(f"  跳过删除已归档块: {block_id}")
                return None
            raise

    @retry(stop_max_attempt_number=3, wait_fixed=5000)
    def get_all_book(self):
        """从Notion中获取所有的书籍"""
        results = self.query_all(self.book_database_id)
        books_dict = {}
        for result in results:
            book_prop = result.get("properties", {}).get("BookId")
            bookId = get_property_value(book_prop) if book_prop else None
            # Skip entries with empty bookId to prevent duplicate creation
            if not bookId:
                continue
            books_dict[bookId] = {
                "pageId": result.get("id"),
                "readingTime": get_property_value(
                    result.get("properties", {}).get("阅读时长")
                ),
                "category": get_property_value(
                    result.get("properties", {}).get("书架分类")
                ),
                "Sort": get_property_value(result.get("properties", {}).get("Sort")) if result.get("properties", {}).get("Sort") else 0,
                "douban_url": get_property_value(
                    result.get("properties", {}).get("豆瓣链接")
                ),
                "cover": result.get("cover"),
                "myRating": get_property_value(
                    result.get("properties", {}).get("我的评分")
                ),
                "comment": get_property_value(result.get("properties", {}).get("豆瓣短评")),
                "status": get_property_value(result.get("properties", {}).get("阅读状态")),
                # 以下三个字段供 progress.py 做"只有变了才写"的去重比较，
                # 顺带把属性读全，不额外产生 Notion 请求。
                "阅读进度": get_property_value(
                    result.get("properties", {}).get("阅读进度")
                ),
                "阅读天数": get_property_value(
                    result.get("properties", {}).get("阅读天数")
                ),
                "最后阅读时间": get_property_value(
                    result.get("properties", {}).get("最后阅读时间")
                ),
            }
        return books_dict

    @retry(stop_max_attempt_number=3, wait_fixed=5000)
    def query_all_by_book(self, database_id, filter):
        results = []
        has_more = True
        start_cursor = None
        while has_more:
            response = self.query(
                database_id=database_id,
                filter=filter,
                start_cursor=start_cursor,
                page_size=100,
            )
            start_cursor = response.get("next_cursor")
            has_more = response.get("has_more")
            results.extend(response.get("results"))
        return results

    @retry(stop_max_attempt_number=3, wait_fixed=5000)
    def query_all(self, database_id):
        """获取database中所有的数据"""
        results = []
        has_more = True
        start_cursor = None
        while has_more:
            response = self.query(
                database_id=database_id,
                start_cursor=start_cursor,
                page_size=100,
            )
            start_cursor = response.get("next_cursor")
            has_more = response.get("has_more")
            results.extend(response.get("results"))
        return results

    def get_date_relation(self, properties, date):
        properties["年"] = get_relation(
            [
                self.get_year_relation_id(date),
            ]
        )
        properties["月"] = get_relation(
            [
                self.get_month_relation_id(date),
            ]
        )
        properties["周"] = get_relation(
            [
                self.get_week_relation_id(date),
            ]
        )
        properties["日"] = get_relation(
            [
                self.get_day_relation_id(date),
            ]
        )