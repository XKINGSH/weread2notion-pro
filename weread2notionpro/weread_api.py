import json
import os
import re
import requests
from dotenv import load_dotenv

load_dotenv()

WEREAD_GATEWAY = "https://i.weread.qq.com/api/agent/gateway"


class WeReadApi:
    """
    微信读书 API 客户端 — 使用腾讯官方 API Key 鉴权
    
    优势：
    - 不需要 Cookie，不存在过期问题
    - API Key 长期有效
    - 提供完整的书架、笔记、划线、书评接口
    """
    
    def __init__(self):
        self.api_key = os.getenv("WEREAD_API_KEY")
        if not self.api_key:
            raise Exception("未设置 WEREAD_API_KEY，请在 .env 或 GitHub Secrets 中配置")
        self.skill_version = "1.0.4"
        self.session = requests.Session()
        
    def _post(self, api_name, **params):
        """统一 POST 请求接口"""
        params["api_name"] = api_name
        params["skill_version"] = self.skill_version
        
        resp = self.session.post(
            WEREAD_GATEWAY,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            },
            json=params,
            timeout=30
        )
        data = resp.json()
        
        if data.get("errcode") not in (0, None):
            raise Exception(f"API 错误 [{data.get('errcode')}]: {data.get('errmsg', data)}")
        
        return data
    
    # ========== 兼容原有 Weread.py / read_time.py 调用的方法 ==========
    
    def get_notebooklist(self):
        """获取有笔记的书籍列表（兼容原接口，带防死循环保护）"""
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
        return all_books
    
    def get_bookmark_list(self, bookId):
        """获取某本书的划线列表（兼容原接口）"""
        data = self._post("/book/bookmarklist", bookId=bookId)
        return data.get("updated", [])
    
    def get_review_list(self, bookId):
        """获取某本书的想法/点评列表（兼容原接口）"""
        data = self._post("/review/list/mine", bookid=bookId)
        reviews_raw = data.get("reviews", [])
        reviews = list(map(lambda x: x.get("review"), reviews_raw)) if reviews_raw else []
        reviews = [
            {"chapterUid": 1000000, **x} if x.get("type") == 4 else x
            for x in reviews
        ]
        return reviews
    
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
