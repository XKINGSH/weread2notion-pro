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


def _write_step_summary(exit_code):
    """把结果写进 GitHub Actions 的 Job Summary，打开 run 页面就能一眼看到状态。

    旧版本没有这个，失败与否只能靠翻日志；也正是因为没有显式状态，
    "run 绿的但其实少同步了几本书" 这种情况一直没被察觉。
    """
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    server = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    repo = os.getenv("GITHUB_REPOSITORY", "")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    status = "✅ 成功" if exit_code == 0 else "❌ 失败（需要人工处理）"
    lines = [
        "## 微信读书 → Notion 同步结果",
        "",
        f"- 状态：{status}",
        f"- skill_version：`{os.getenv('WEREAD_SKILL_VERSION', '1.0.4')}`",
    ]
    if repo and run_id:
        lines.append(f"- run：[{run_id}]({server}/{repo}/actions/runs/{run_id})")
    lines += [
        "",
        "> 由同步脚本自动生成。状态为失败时，请看上方日志里的 `::error::` 行与"
        "「失败时给出排查指引」步骤。",
        "",
    ]
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
    except OSError:
        pass


if __name__ == "__main__":
    dry_run = ("--dry-run" in sys.argv) or (os.getenv("WEREAD_DRY_RUN") == "1")
    print("开始同步微信读书笔记到 Notion..." + ("（dry-run 模式）" if dry_run else ""))
    exit_code = 0
    try:
        exit_code = main(dry_run=dry_run) or 0
    except Exception as exc:  # 顶层兜底：任何漏网异常都必须变成非 0 退出码
        import traceback
        traceback.print_exc()
        print(f"::error title=同步异常中止::{type(exc).__name__}: {exc}")
        exit_code = 1
    if exit_code == 0:
        print("同步完成！")
    else:
        print("同步未全部完成，详见上方日志。")
    _write_step_summary(exit_code)
    sys.exit(exit_code)
