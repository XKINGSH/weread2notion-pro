from datetime import datetime
from datetime import timedelta
import os

import pendulum

from weread2notionpro.weread_api import WeReadApi
from weread2notionpro.notion_helper import NotionHelper
from weread2notionpro.utils import (
    format_date,
    get_date,
    get_icon,
    get_number,
    get_relation,
    get_title,
)


def insert_to_notion(page_id, timestamp, duration):
    parent = {"database_id": notion_helper.day_database_id, "type": "database_id"}
    properties = {
        "标题": get_title(
            format_date(
                datetime.utcfromtimestamp(timestamp) + timedelta(hours=8),
                "%Y年%m月%d日",
            )
        ),
        "日期": get_date(
            start=format_date(datetime.utcfromtimestamp(timestamp) + timedelta(hours=8))
        ),
        "时长": get_number(duration),
        "时间戳": get_number(timestamp),
        "年": get_relation(
            [
                notion_helper.get_year_relation_id(
                    datetime.utcfromtimestamp(timestamp) + timedelta(hours=8)
                ),
            ]
        ),
        "月": get_relation(
            [
                notion_helper.get_month_relation_id(
                    datetime.utcfromtimestamp(timestamp) + timedelta(hours=8)
                ),
            ]
        ),
        "周": get_relation(
            [
                notion_helper.get_week_relation_id(
                    datetime.utcfromtimestamp(timestamp) + timedelta(hours=8)
                ),
            ]
        ),
    }
    if page_id != None:
        notion_helper.client.pages.update(page_id=page_id, properties=properties)
    else:
        notion_helper.client.pages.create(
            parent=parent,
            icon=get_icon("https://www.notion.so/icons/target_red.svg"),
            properties=properties,
        )


notion_helper = NotionHelper()
weread_api = WeReadApi()


def main():
    print("开始同步阅读时长...")
    api_data = weread_api.get_api_data()
    read_times = api_data.get("readTimes", {})
    read_times = {int(k): v for k, v in read_times.items()}

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
            timestamp = ts_prop.get("number")
            duration = dur_prop.get("number")
            rid = result.get("id")
            if timestamp in read_times:
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
