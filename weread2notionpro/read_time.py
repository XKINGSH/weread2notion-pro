import os
import sys
import pendulum
from datetime import datetime, timedelta
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from weread2notionpro.weread_api import WeReadApi
from weread2notionpro.notion_helper import NotionHelper
from weread2notionpro.utils import format_date, get_number, get_relation, get_title, get_date, get_icon

weread_api = WeReadApi()
notion_helper = NotionHelper()


def insert_to_notion(page_id=None, timestamp=None, duration=None, book_database_id=None):
    """向 Notion 插入或更新一条阅读时长记录"""
    properties = {
        "标题": get_title(pendulum.from_timestamp(timestamp, tz="Asia/Shanghai").to_date_string()),
        "日期": get_date(start=pendulum.from_timestamp(timestamp, tz="Asia/Shanghai").format("YYYY-MM-DD HH:mm:ss")),
        "时长": get_number(duration),
        "时间戳": get_number(timestamp),
        "书架": get_relation([book_database_id]) if book_database_id else None,
    }
    properties = {k: v for k, v in properties.items() if v is not None}
    if page_id is not None:
        notion_helper.client.pages.update(page_id=page_id, properties=properties)
    else:
        parent = {"database_id": notion_helper.day_database_id, "type": "database_id"}
        notion_helper.client.pages.create(
            parent=parent,
            icon=get_icon("https://www.notion.so/icons/target_red.svg"),
            properties=properties,
        )


def main():
    print("开始同步阅读时长...")
    
    api_data = weread_api.get_api_data()
    read_times = api_data.get("readTimes", {})
    # 确保 readTimes 的 key 转为 int
    read_times = {int(k): int(v) for k, v in read_times.items()}

    if not read_times:
        print("警告: 微信读书 API 未返回阅读时长数据")
        return

    now = pendulum.now("Asia/Shanghai").start_of("day")
    today_ts = now.int_timestamp
    if today_ts not in read_times:
        read_times[today_ts] = 0
    read_times = dict(sorted(read_times.items()))

    results = notion_helper.query_all(database_id=notion_helper.day_database_id)
    print(f"Notion 中已有 {len(results)} 条阅读记录")

    updated = 0
    created = 0
    for result in results:
        ts_prop = result.get("properties", {}).get("时间戳")
        dur_prop = result.get("properties", {}).get("时长")
        if ts_prop and dur_prop:
            # 安全获取 number 值，处理可能的 dict 包装
            timestamp_raw = ts_prop.get("number") if isinstance(ts_prop, dict) else ts_prop
            duration_raw = dur_prop.get("number") if isinstance(dur_prop, dict) else dur_prop
            
            # 确保是数字类型
            timestamp = int(float(timestamp_raw)) if timestamp_raw is not None else None
            duration = int(float(duration_raw)) if duration_raw is not None else None
            rid = result.get("id")
            
            if timestamp is not None and timestamp in read_times:
                value = read_times.pop(timestamp)
                if value != duration:
                    insert_to_notion(page_id=rid, timestamp=timestamp, duration=value)
                    updated += 1
                    ts_date = format_date(
                        datetime.utcfromtimestamp(timestamp) + timedelta(hours=8),
                        "%Y年%m月%d日",
                    )
                    print(f"  更新: {ts_date} 时长 {duration} -> {value}")

    for key, value in read_times.items():
        if value > 0:
            insert_to_notion(None, int(key), value)
            created += 1
            ts_date = format_date(
                datetime.utcfromtimestamp(key) + timedelta(hours=8), "%Y年%m月%d日"
            )
            print(f"  新建: {ts_date} 时长 {value}")

    print(f"同步完成: 更新 {updated} 条, 新建 {created} 条")


if __name__ == "__main__":
    main()