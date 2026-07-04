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
    
    # ========== 兼容原有 Weread.py 调用的方法 ==========
    
    def get_notebooklist(self):
        """获取有笔记的书籍列表（兼容原接口）"""
        return self.get_notebook_list()
    
    def get_bookmark_list(self, bookId):
        """获取某本书的划线列表（兼容原接口）"""
        data = self.get_bookmarks(bookId)
        if isinstance(data, dict):
            return data.get("bookmarks", [])
        return data
    
    def get_review_list(self, bookId):
        """获取某本书的想法/点评列表（兼容原接口）"""
        # 注意：必须使用小写 bookid
        data = self._post("/review/list/mine", bookid=bookId)
        if isinstance(data, dict):
            return data.get("reviews", [])
        return data
    
    def get_chapter_info(self, bookId):
        """获取书籍章节目录（兼容原接口）"""
        data = self._post("/book/chapterinfo", bookId=bookId)
        return data.get("data", [{}])[0].get("updated", []) if data.get("data") else []
    
    # ========== 新增 API Key 专属方法 ==========
    
    def get_notebooks(self, count=100, last_sort=None):
        """获取有笔记的书籍列表（分页）"""
        params = {"count": count}
        if last_sort:
            params["lastSort"] = last_sort
        data = self._post("/user/notebooks", **params)
        return data
    
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
        data = self._post("/book/bookmarklist", bookId=book_id)
        return data
    
    def get_reviews(self, book_id, list_type=11, mine=1, sync_key=0):
        """获取某本书的想法/点评列表"""
        # 注意：使用小写 bookid 参数
        data = self._post("/review/list/mine", bookid=book_id)
        return data
    
    def get_shelf(self):
        """获取书架列表"""
        data = self._post("/shelf/sync")
        return data
    
    def get_book_info(self, book_id):
        """获取书籍基本信息"""
        data = self._post("/book/info", bookId=book_id)
        return data
    
    def get_reading_progress(self, book_id):
        """获取阅读进度"""
        data = self._post("/book/getprogress", bookId=book_id)
        return data


if __name__ == "__main__":
    api = WeReadApi()
    print("测试笔记本书籍列表...")
    notebooks = api.get_notebooklist()
    print(f"  有笔记的书: {len(notebooks)} 本")
    
    if notebooks:
        first_book = notebooks[0]
        book_id = first_book.get("bookId")
        print(f"\n测试第一本书: {first_book.get('book', {}).get('title')}")
        
        print("测试划线列表...")
        bookmarks = api.get_bookmark_list(book_id)
        print(f"  划线数: {len(bookmarks)}")
        
        print("测试点评列表...")
        reviews = api.get_review_list(book_id)
        print(f"  点评数: {len(reviews)}")
        
        print("测试章节目录...")
        chapters = api.get_chapter_info(book_id)
        print(f"  章节数: {len(chapters) if chapters else 0}")
    
    print("\nAPI Key 模式运行正常！")
