"""
微信读书笔记同步到 Notion 的主程序入口
"""
import os
import sys
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from weread2notionpro.weread import main

if __name__ == "__main__":
    print("开始同步微信读书笔记到 Notion...")
    main()
    print("同步完成！")
