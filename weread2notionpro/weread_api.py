import json
import os
import re
import time

import requests
from urllib3.exceptions import HTTPError as Urllib3HTTPError
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
from dotenv import load_dotenv

load_dotenv()

# 网关地址允许用环境变量覆盖：既方便本地自测（指向本地假网关），
# 也方便将来官方换域名时不用改代码，只需改 Secret/变量。
WEREAD_GATEWAY = os.getenv("WEREAD_GATEWAY", "https://i.weread.qq.com/api/agent/gateway")

# 传输层瞬时错误（网关偶发断连、读超时等）——可以重试
TRANSIENT_ERRORS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
    Urllib3HTTPError,
)

# 命中这些关键字就按"API Key 失效/未授权"处理：不重试，直接让整轮 run 变红
AUTH_ERROR_HINTS = (
    "invalid key", "invalidkey", "invalid api key", "apikey", "unauthorized",
    "unauthenticated", "forbidden", "token", "expired", "过期", "失效",
    "未授权", "无效", "重新获取", "重新申请", "重新登录",
)


class WeReadApiError(Exception):
    """微信读书网关返回的业务错误。"""


class WeReadAuthError(WeReadApiError):
    """微信读书 API Key 缺失 / 失效 / 无权限——必须人工重新获取 Key 才能恢复。

    单独分类的目的是：它不该被 main() 的"单本书失败就跳过"逻辑吞掉，
    而应当立刻中止整轮同步并把 workflow 标红。
    """


class WeReadApi:
    """
    微信读书 API 客户端 — 使用腾讯官方 API Key 鉴权

    关于"要不要重新登录"（这里以前写的是错话，已更正）：
    - 本项目**不再使用网页 Cookie**，改用官方 Agent API Key（wrk- 开头），
      获取入口：https://weread.qq.com/r/weread-skills ，扫码一次拿到 Key。
    - 官方与公开资料到目前为止**没有给出该 Key 的明确有效期数字**，也**没有提供任何
      refresh / renew 接口**。所以以前这里写的"不存在过期问题 / API Key 长期有效"
      属于无依据的承诺，已删除，不再对外承诺"永久有效"。
    - 能保证的是可运维性，而不是永久有效：
        1) 网关瞬时断连自动重试（见 _post），避免偶发抖动导致整本书同步失败；
        2) Key 一旦失效立刻抛 WeReadAuthError，让 workflow 明确变红并给出处置提示，
           而不是像 2026-07-04~09-05 那样"每天都显示成功、实际早就没同步全"；
        3) 提供 check_credentials() 预检与 --dry-run，方便随时确认 Key 还活着。
    """

    def __init__(self):
        self.api_key = os.getenv("WEREAD_API_KEY")
        if not self.api_key:
            raise WeReadAuthError(
                "未设置 WEREAD_API_KEY。请在 GitHub 仓库 Settings > Secrets and variables > "
                "Actions 中添加 WEREAD_API_KEY（Key 从 https://weread.qq.com/r/weread-skills 扫码获取）"
            )
        self.skill_version = os.getenv("WEREAD_SKILL_VERSION", "1.0.4")
        self.max_attempts = int(os.getenv("WEREAD_MAX_ATTEMPTS", "4"))
        self.backoff_base = float(os.getenv("WEREAD_BACKOFF_BASE", "2"))
        self.http_timeout = float(os.getenv("WEREAD_HTTP_TIMEOUT", "30"))
        self._notebooks_cache = None

        # 传输层重试：只在连接/读/5xx/429 上重试，绝不重试 401/403（那是鉴权问题，重试没用）
        self.session = requests.Session()
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    @staticmethod
    def _looks_like_auth_error(text):
        lowered = str(text).lower()
        return any(hint in lowered for hint in AUTH_ERROR_HINTS)
        
    def _post(self, api_name, **params):
        """统一 POST 请求接口：瞬时错误自动重试，鉴权错误与业务错误分开抛出。

        历史问题：原实现只有一次裸请求，网关偶发 RemoteDisconnected 就直接冒泡，
        在 main() 里被当成"单本书失败"静默跳过（2026-09-05 run 33942853283 的
        《深层认知》就是这样丢的），而整个 run 仍然显示 success。
        """
        params["api_name"] = api_name
        params["skill_version"] = self.skill_version

        last_error = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = self.session.post(
                    WEREAD_GATEWAY,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=params,
                    timeout=self.http_timeout,
                )
            except TRANSIENT_ERRORS as err:
                last_error = err
                if attempt >= self.max_attempts:
                    break
                wait = self.backoff_base ** attempt
                print(f"  [WARN] 微信读书网关瞬时错误（第 {attempt}/{self.max_attempts} 次，"
                      f"{wait:.0f}s 后重试）: {err}")
                time.sleep(wait)
                continue

            if resp.status_code in (401, 403):
                raise WeReadAuthError(
                    f"微信读书网关返回 HTTP {resp.status_code}，API Key 可能已失效。"
                    "请到 https://weread.qq.com/r/weread-skills 重新扫码获取 Key，"
                    "并更新仓库 Secret WEREAD_API_KEY。"
                )
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = WeReadApiError(f"HTTP {resp.status_code}")
                if attempt >= self.max_attempts:
                    break
                wait = self.backoff_base ** attempt
                print(f"  [WARN] 微信读书网关 HTTP {resp.status_code}"
                      f"（第 {attempt}/{self.max_attempts} 次，{wait:.0f}s 后重试）")
                time.sleep(wait)
                continue

            try:
                data = resp.json()
            except ValueError:
                raise WeReadApiError(
                    f"微信读书网关返回非 JSON 响应（HTTP {resp.status_code}）：{resp.text[:200]!r}"
                )

            errcode = data.get("errcode")
            errmsg = data.get("errmsg", "")
            if errcode not in (0, None):
                if self._looks_like_auth_error(errmsg) or self._looks_like_auth_error(errcode):
                    raise WeReadAuthError(
                        f"微信读书 API Key 可能已失效 [{errcode}]：{errmsg}。"
                        "请到 https://weread.qq.com/r/weread-skills 重新扫码获取 Key，"
                        "并更新仓库 Secret WEREAD_API_KEY。"
                    )
                raise WeReadApiError(f"API 错误 [{errcode}]: {errmsg or data}")

            return data

        raise WeReadApiError(
            f"微信读书网关连续 {self.max_attempts} 次请求失败（{api_name}），最后一次错误：{last_error}"
        )
    
    # ========== 兼容原有 Weread.py / read_time.py 调用的方法 ==========
    
    def invalidate_notebooks_cache(self):
        """清空书籍列表缓存（正常情况下不需要调用）。"""
        self._notebooks_cache = None

    def get_notebooklist(self):
        """获取有笔记的书籍列表（兼容原接口，带防死循环保护；结果按进程缓存）。

        历史问题：get_read_info() 内部对每一本书都会重新调用一次本方法，
        8 本书就要多打 8 轮全量分页请求——既拖长单次运行（2026-09-05 那轮跑了
        8 分 28 秒），也更容易撞上网关限流/断连。这里加一层进程内缓存。
        """
        if self._notebooks_cache is not None:
            return self._notebooks_cache
        all_books = []
        last_sort = None
        page = 0
        max_pages = 50
        while page < max_pages:
            page += 1
            data = self._post("/user/notebooks", count=100, lastSort=last_sort)
            books = data.get("books", [])
            if not books:
                break
            all_books.extend(books)
            if not data.get("hasMore"):
                break
            new_sort = books[-1].get("sort")
            if new_sort == last_sort:
                print(f"  分页异常：第{page}页 sort 未变化，停止分页")
                break
            last_sort = new_sort
        print(f"  获取到 {len(all_books)} 本书")
        self._notebooks_cache = all_books
        return all_books
    
    def get_bookmark_list(self, bookId):
        """获取某本书的划线列表（兼容原接口）"""
        data = self._post("/book/bookmarklist", bookId=bookId)
        return data.get("updated", [])
    
    def get_review_list(self, bookId):
        """获取某本书的想法/点评列表（兼容原接口）"""
        data = self._post("/review/list/mine", bookid=bookId)
        reviews_raw = data.get("reviews", []) or []
        # 兼容两种返回：外层直接是 review，或包一层 {"review": {...}}。
        # 并显式丢掉 None —— 旧实现遇到没有 review 字段的条目会往列表里塞 None，
        # 下游 i.get("reviewId") 直接抛 AttributeError，整本书的同步就断了。
        reviews = []
        for item in reviews_raw:
            if not isinstance(item, dict):
                continue
            review = item.get("review") if isinstance(item.get("review"), dict) else item
            if not isinstance(review, dict) or not review:
                continue
            reviews.append(review)
        return [
            {"chapterUid": 1000000, **x} if x.get("type") == 4 else x
            for x in reviews
        ]
    
    def get_chapter_info(self, bookId):
        """获取书籍章节目录（兼容原接口）"""
        data = self._post("/book/chapterinfo", bookId=bookId)
        chapters = data.get("chapters", [])
        chapter_dict = {item["chapterUid"]: item for item in chapters}
        chapter_dict[1000000] = {
            "chapterUid": 1000000,
            "chapterIdx": 1000000,
            "updateTime": 1683825006,
            "readAhead": 0,
            "title": "点评",
            "level": 1,
        }
        return chapter_dict
    
    def get_api_data(self):
        """获取阅读时长数据（兼容 read_time.py）
        原返回: {"readTimes": {timestamp: duration, ...}}
        """
        data = self._post("/readdata/detail", mode="overall")
        return data

    def check_credentials(self):
        """预检 API Key 当前是否可用。

        返回 (ok: bool, message: str)。刻意不抛异常，方便 --dry-run 与预检步骤直接展示结论。
        """
        try:
            data = self._post("/user/notebooks", count=1)
        except WeReadAuthError as err:
            return False, str(err)
        except Exception as err:  # noqa: BLE001 - 预检要给出可读结论而不是堆栈
            return False, f"{type(err).__name__}: {err}"
        books = data.get("books", []) or []
        return True, (f"WEREAD_API_KEY 校验通过（skill_version={self.skill_version}，"
                      f"本次探测返回 {len(books)} 本书）")
    
    # ========== 新增 API Key 专属方法 ==========
    
    def get_notebooks(self, count=100, last_sort=None):
        """获取有笔记的书籍列表（分页，单次）"""
        params = {"count": count}
        if last_sort:
            params["lastSort"] = last_sort
        return self._post("/user/notebooks", **params)
    
    def get_notebook_list(self):
        """分页获取所有有笔记的书籍"""
        all_books = []
        last_sort = None
        while True:
            data = self.get_notebooks(count=100, last_sort=last_sort)
            all_books.extend(data.get("books", []))
            if not data.get("hasMore"):
                break
            last_sort = data["books"][-1].get("sort") if data["books"] else None
        return all_books
    
    def get_bookmarks(self, book_id):
        """获取某本书的划线列表"""
        return self._post("/book/bookmarklist", bookId=book_id)
    
    def get_reviews(self, book_id, list_type=11, mine=1, sync_key=0):
        """获取某本书的想法/点评列表"""
        return self._post("/review/list/mine", bookid=book_id)
    
    def get_shelf(self):
        """获取书架列表"""
        return self._post("/shelf/sync")
    
    def get_book_info(self, book_id):
        """获取书籍基本信息"""
        return self._post("/book/info", bookId=book_id)
    
    def get_reading_progress(self, book_id):
        """获取阅读进度"""
        return self._post("/book/getprogress", bookId=book_id)


    # ========== 兼容原版 weread_api.py 的方法名 ==========
    
    def get_bookinfo(self, bookId):
        """获取书的详情（兼容原版方法名）"""
        return self.get_book_info(bookId)
    
    def get_read_info(self, bookId):
        """获取阅读详情（兼容原版方法名）
        通过 /book/getprogress 获取进度数据
        同时尝试从 /user/notebooks 获取日期信息
        """
        data = self._post("/book/getprogress", bookId=bookId)
        result = {
            "readingTime": data.get("readingTime", 0),
            "totalReadDay": data.get("totalReadDay", 0),
            "readingProgress": data.get("readingProgress", 0),
            "markedStatus": data.get("markedStatus", 1),
            "beginReadingDate": data.get("beginReadingDate", ""),
            "lastReadingDate": data.get("lastReadingDate", ""),
            "finishedDate": data.get("finishedDate", ""),
            "readDetail": data.get("readDetail", {}),
            "bookInfo": data.get("bookInfo", {}),
        }
        # 如果日期字段为空，尝试从 /user/notebooks 获取
        if not result["beginReadingDate"] or not result["lastReadingDate"]:
            notebooks = self.get_notebooklist()
            for nb in notebooks:
                if nb.get("bookId") == bookId:
                    book_obj = nb.get("book", {})
                    if not result["beginReadingDate"] and book_obj.get("beginReadingDate"):
                        result["beginReadingDate"] = book_obj["beginReadingDate"]
                    if not result["lastReadingDate"] and book_obj.get("lastReadingDate"):
                        result["lastReadingDate"] = book_obj["lastReadingDate"]
                    if not result["finishedDate"] and book_obj.get("finishedDate"):
                        result["finishedDate"] = book_obj["finishedDate"]
                    break
        # 如果还是没有，尝试从 /shelf/sync 获取
        if not result["beginReadingDate"] or not result["lastReadingDate"]:
            shelf = self.get_shelf()
            for bp in shelf.get("bookProgress", []):
                if bp.get("bookId") == bookId:
                    if not result["beginReadingDate"] and bp.get("beginReadingDate"):
                        result["beginReadingDate"] = bp["beginReadingDate"]
                    if not result["lastReadingDate"] and bp.get("lastReadingDate"):
                        result["lastReadingDate"] = bp["lastReadingDate"]
                    if not result["finishedDate"] and bp.get("finishedDate"):
                        result["finishedDate"] = bp["finishedDate"]
                    break
        return result
    
    def get_url(self, book_id):
        """生成微信读书阅读链接"""
        return f"https://weread.qq.com/web/reader/{book_id}"
    
    def get_shelf_progress(self):
        """获取「书架里每一本书」的阅读进度（不限于有笔记的书）。

        旧缺口：get_bookshelf() 是从 /user/notebooks（只有笔记的书）派生出来的，
        所以"在读但还没划线"的书根本进不了同步范围，阅读进度永远停在 Notion 里不动。

        说明：本方法按"防御式解析"写——/shelf/sync 的字段名在不同版本间有过差异，
        这里同时兼容 bookProgress / books / bookprogress 三种键，并把内层 book
        对象展开合并。任何一条解析不出来就跳过该条，不会让整轮失败。
        """
        data = self._post("/shelf/sync")
        if not isinstance(data, dict):
            return []
        raw = []
        for key in ("bookProgress", "books", "bookprogress"):
            value = data.get(key)
            if isinstance(value, list) and value:
                raw.extend(value)
        merged_by_id = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            book = item.get("book") if isinstance(item.get("book"), dict) else {}
            book_id = item.get("bookId") or book.get("bookId")
            if not book_id:
                continue
            merged = dict(book)
            for k, v in item.items():
                if k == "book" or v is None or v == "":
                    continue
                merged[k] = v
            merged["bookId"] = book_id
            old = merged_by_id.get(book_id)
            if old is None:
                merged_by_id[book_id] = merged
            else:
                # 同一本书在两处出现时，取字段更全的那份
                for k, v in merged.items():
                    if old.get(k) in (None, "", 0) and v not in (None, ""):
                        old[k] = v
        return list(merged_by_id.values())

    def get_bookshelf(self):
        """获取书架（兼容原版 book.py 调用）
        返回 {bookProgress: [...], archive: [...]} 格式

        现在以 /shelf/sync 的真实书架为准；接口不可用时退回旧的有笔记书籍口径，
        保证不会比修复前更差。
        """
        try:
            book_progress = self.get_shelf_progress()
        except WeReadApiError as err:
            print(f"  [WARN] /shelf/sync 不可用（{err}），退回 /user/notebooks 口径")
            book_progress = []
        if book_progress:
            return {"bookProgress": book_progress, "archive": []}
        notebooks = self.get_notebooklist()
        archive = []
        for nb in notebooks:
            bd = nb.get("book", {})
            entry = {
                "bookId": nb.get("bookId"),
                "readingTime": bd.get("readingTime", 0),
                "totalReadDay": bd.get("totalReadDay", 0),
                "readingProgress": bd.get("readingProgress", 0),
                "markedStatus": bd.get("markedStatus", 1),
                "beginReadingDate": bd.get("beginReadingDate", ""),
                "lastReadingDate": bd.get("lastReadingDate", ""),
                "finishedDate": bd.get("finishedDate", ""),
                "title": bd.get("title", ""),
                "author": bd.get("author", ""),
                "cover": bd.get("cover", ""),
                "sort": nb.get("sort", 0),
            }
            book_progress.append(entry)
        return {"bookProgress": book_progress, "archive": archive}

if __name__ == "__main__":
    api = WeReadApi()
    print("测试 get_notebooklist...")
    notebooks = api.get_notebooklist()
    print(f"  有笔记的书: {len(notebooks)} 本")
    
    if notebooks:
        first_book = notebooks[0]
        book_id = first_book.get("bookId")
        print(f"\n第一本书: {first_book.get('book', {}).get('title')}")
        
        print("测试 get_bookmark_list...")
        bookmarks = api.get_bookmark_list(book_id)
        print(f"  划线数: {len(bookmarks)}")
        
        print("测试 get_review_list...")
        reviews = api.get_review_list(book_id)
        print(f"  点评数: {len(reviews)}")
        
        print("测试 get_chapter_info...")
        chapters = api.get_chapter_info(book_id)
        print(f"  章节数: {len(chapters)}")
        
        print("测试 get_api_data...")
        api_data = api.get_api_data()
        print(f"  readTimes: {api_data.get('readTimes')}")
    
    print("\n全部测试通过！")
