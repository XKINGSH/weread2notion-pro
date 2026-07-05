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


def get_number_safe(prop_obj):
    if prop_obj is None:
        return None
    if isinstance(prop_obj, (int, float)):
        return int(prop_obj)
    if isinstance(prop_obj, dict):
        num = prop_obj.get('number')
        if num is not None and isinstance(num, (int, float)):
            return int(num)
        for key, val in prop_obj.items():
            if isinstance(val, (int, float)):
                return int(val)
    return None


def insert_to_notion(page_id=None, timestamp=None, duration=None, book_database_id=None):
    properties = {
        '标题': get_title(pendulum.from_timestamp(timestamp, tz='Asia/Shanghai').to_date_string()),
        '日期': get_date(start=pendulum.from_timestamp(timestamp, tz='Asia/Shanghai').format('YYYY-MM-DD HH:mm:ss')),
        '时长': get_number(duration),
        '时间戳': get_number(timestamp),
        '书架': get_relation([book_database_id]) if book_database_id else None,
    }
    properties = {k: v for k, v in properties.items() if v is not None}
    if page_id is not None:
        notion_helper.client.pages.update(page_id=page_id, properties=properties)
    else:
        parent = {'database_id': notion_helper.day_database_id, 'type': 'database_id'}
        notion_helper.client.pages.create(
            parent=parent,
            icon=get_icon('https://www.notion.so/icons/target_red.svg'),
            properties=properties,
        )


def main():
    print('Start syncing read time...')
    api_data = weread_api.get_api_data()
    read_times = api_data.get('readTimes', {})
    read_times = {int(k): int(v) for k, v in read_times.items()}
    if not read_times:
        print('Warning: No read time data from API')
        return
    now = pendulum.now('Asia/Shanghai').start_of('day')
    today_ts = now.int_timestamp
    if today_ts not in read_times:
        read_times[today_ts] = 0
    read_times = dict(sorted(read_times.items()))
    results = notion_helper.query_all(database_id=notion_helper.day_database_id)
    print(f'Notion has {len(results)} read records')
    updated = 0
    created = 0
    for result in results:
        ts_prop = result.get('properties', {}).get('时间戳')
        dur_prop = result.get('properties', {}).get('时长')
        if ts_prop and dur_prop:
            timestamp = get_number_safe(ts_prop)
            duration = get_number_safe(dur_prop)
            rid = result.get('id')
            if timestamp is not None and timestamp in read_times:
                value = read_times.pop(timestamp)
                if value != duration:
                    insert_to_notion(page_id=rid, timestamp=timestamp, duration=value)
                    updated += 1
                    ts_date = format_date(datetime.utcfromtimestamp(timestamp) + timedelta(hours=8), '%Y年%m月%d日')
                    print(f'  Updated: {ts_date} {duration} -> {value}')
    for key, value in read_times.items():
        if value > 0:
            insert_to_notion(None, int(key), value)
            created += 1
            ts_date = format_date(datetime.utcfromtimestamp(key) + timedelta(hours=8), '%Y年%m月%d日')
            print(f'  Created: {ts_date} duration {value}')
    print(f'Done: updated {updated}, created {created}')


if __name__ == '__main__':
    main()
