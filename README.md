# 将微信读书划线和笔记同步到Notion


本项目通过Github Action每天定时同步微信读书划线到Notion。

预览效果：[https://malinkang.notion.site/malinkang/534a7684b30e4a879269313f437f2185](https://malinkang.notion.site/9a311b7413b74c8788752249edd0b256?pvs=25)


## 使用

> [!IMPORTANT]  
> 关注公众号获取教程，后续有更新也会第一时间在公众号里同步。

![扫码_搜索联合传播样式-标准色版](https://github.com/malinkang/weread2notion/assets/3365208/191900c6-958e-4f9b-908d-a40a54889b5e)


## 群
> [!IMPORTANT]  
> 欢迎加入群讨论。可以讨论使用中遇到的任何问题，也可以讨论Notion使用，后续我也会在群中分享更多Notion自动化工具。微信群失效的话可以添加我的微信malinkang，我拉你入群。

| 微信群 | QQ群 |
| --- | --- |
| <div align="center"><img src="https://images.malinkang.com/2024/10/2dfe1f98391e7bd2ee03879e788e5649.jpg" ></div> | <div align="center"><img src="https://images.malinkang.com/2024/10/f6d61b082e78f001cff8ab0a21344fe9.jpeg" width="50%"></div> |


## 捐赠

如果你觉得本项目帮助了你，请作者喝一杯咖啡，你的支持是作者最大的动力。本项目会持续更新。

| 支付宝支付 | 微信支付 |
| --- | --- |
| <div align="center"><img src="https://images.malinkang.com/2024/03/7fd0feb1145f19fab3821ff1d4631f85.jpg" width="50%"></div> | <div align="center"><img src="https://images.malinkang.com/2024/03/d34f577490a32d4440c8a22f57af41da.jpg" width="50%"></div> |

## 其他项目
* [WeRead2Notion-Pro](https://github.com/malinkang/weread2notion-pro)
* [WeRead2Notion](https://github.com/malinkang/weread2notion)
* [Podcast2Notion](https://github.com/malinkang/podcast2notion)
* [Douban2Notion](https://github.com/malinkang/douban2notion)
* [Keep2Notion](https://github.com/malinkang/keep2notion)

---

# 维护说明（XKINGSH fork 专属，非上游内容）

> 这一段是为了让"下次坏了能自己修"而写的，记录了 2026-09-29 这次排查的结论。
> 上游 README 的原文保留在上面。

## 1. 当前形态

- 本 fork 已从「网页 Cookie」方案整体切换到**微信读书官方 Agent API Key**（`wrk-` 开头），
  网关地址 `https://i.weread.qq.com/api/agent/gateway`。
- 依赖的仓库 Secret（**只有这三个是必需的**）：
  | Secret | 用途 | 最近一次更新 |
  | --- | --- | --- |
  | `NOTION_TOKEN` | Notion integration token | 2024-02-19 |
  | `NOTION_PAGE` | 同步落点的 Notion 页面 URL | 2024-02-19 |
  | `WEREAD_API_KEY` | 微信读书官方 API Key | 2026-07-04 |
- 仓库里还留着两个**当前代码已经不使用**的旧 Secret：`WEREAD_COOKIE`（2025-03-30，Cookie 时代遗留）
  和一个名字就叫 `NAME` 的（2024-05-01）。确认无用后可以删掉，减少混淆。

## 2. 关于「登录一次能维持多久」——不要对外承诺「永久有效」

- 官方取 Key 的入口是 <https://weread.qq.com/r/weread-skills>，**扫码一次**即可拿到 `wrk-` Key，
  之后脚本靠这个 Key 请求，不再需要网页登录。
- 但截至 2026-09 检索，**官方与公开资料都没有给出该 Key 的明确有效期数字**，
  也**没有提供 refresh / renew 之类的续期接口**。
  所以：以前 `.env.example` 和 `weread_api.py` 里写的
  「永久有效 / 不存在过期问题 / 无需手动更新」是**没有依据的**，已从代码注释中删除。
- 实际结论：**不能承诺永久有效**。能保证的是「失效会被立刻发现，而不是悄悄停摆」：
  脚本遇到鉴权失败会抛 `WeReadAuthError` → workflow 变红 → GitHub 发失败邮件 →
  Job Summary 里直接给出重新获取 Key 的步骤。
- 续期动作（人工，约 2 分钟）：打开 <https://weread.qq.com/r/weread-skills> → 扫码
  → 复制新的 `wrk-` Key → 仓库 Settings > Secrets and variables > Actions → 更新
  `WEREAD_API_KEY`。

## 3. 为什么定时任务会「自己没了」

GitHub 会在**仓库连续 60 天没有任何提交**时，自动把所有 scheduled workflow 置为
`disabled_inactivity`，并且不会单独提醒。

本项目最后一次提交是 2026-07-06，两个 workflow（`Auto Sync WeRead Notes to Notion`、
`read time sync`）就在 2026-09-05 被自动停用，同步从那天起完全停止。

现在已加入 `.github/workflows/keepalive.yml`：每月制造一次极小提交，让仓库始终"有活动"。
如果将来它也被停用，恢复方式二选一：

1. 往仓库随便推一次提交；或
2. 打开 Actions 页面，选中被停用的 workflow，点 **Enable workflow**。

## 4. 怎么确认它还活着

- **不写 Notion 的干跑**（推荐，随时可用）：
  仓库 Actions → `Auto Sync WeRead Notes to Notion` → Run workflow → 勾选
  `dry_run` → 运行。它只会校验 `WEREAD_API_KEY` 并打印书架书目，不读写 Notion。
- 本地干跑：`python -m weread2notionpro --dry-run`（需要先有 `.env`）。
- 每次运行都会在 run 页面底部的 **Job Summary** 里写明成功/失败，
  并附上实际解析到的依赖版本。

## 5. 已修掉的两个具体坑（避免以后重复踩）

1. **`pages.patch` 根本不存在**
   `notion-client` 的 `PagesEndpoint` 只有 `create` / `retrieve` / `update`。
   旧代码调用 `client.pages.patch(...)`，每本书每次都抛
   `AttributeError: 'PagesEndpoint' object has no attribute 'patch'`，
   被 `except` 吞成一行 `[WARN]`，于是**封面和图标从来没写进过 Notion**。
   现在改为 `pages.update(page_id=..., cover=..., icon=...)`。
2. **失败被静默吞掉**
   旧 `main()` 里 `except Exception: print(...); continue`，所以整轮同步哪怕一本书都没成功，
   run 也照样是绿的。2026-09-05 的 run `33942853283` 就是最后一本书失败、run 仍然 success。
   现在改为：失败计数 → 结束时有失败就 `return 1` → run 变红 + `::error::` 注解 + Job Summary。

## 6. 定时策略

| Workflow | cron | 含义 |
| --- | --- | --- |
| `weread.yml` | `0 */6 * * *` | 每 6 小时（北京时间 8:00 / 14:00 / 20:00 / 次日 2:00） |
| `read_time.yml` | `0 0 * * *` | 每天一次（阅读时长本身就是按天聚合的数据，没必要加密） |
| `keepalive.yml` | `17 3 1 * *` | 每月 1 日，只为了保住上面两个定时任务不被自动停用 |

## 7. 同步范围与 Notion 库结构

一次运行（`python -m weread2notionpro`）会做四件事，全部以「微信读书侧的唯一 ID」为键，
所以**重复跑不会灌重复数据**：

| 内容 | 微信读书来源 | 落到 Notion 哪里 | 去重键 |
| --- | --- | --- | --- |
| 书籍元信息 | `/book/info` + `/book/getprogress` | 「书架」库（书名/BookId/ISBN/链接/封面/图标/作者/分类/评分/简介） | `BookId` |
| **读书进度** | `/shelf/sync`（全量书架）+ `/book/getprogress` | 「书架」库的 `阅读进度` / `阅读时长` / `阅读天数` / `阅读状态` / `最后阅读时间` | `BookId` |
| **划线** | `/book/bookmarklist` | 「划线」库一条记录 + 书籍页正文里的一个块 | `bookmarkId` ↔ `blockId` |
| **想法 / 点评** | `/review/list/mine` | 「笔记」库一条记录 + 书籍页正文里的一个块 | `reviewId` ↔ `blockId` |
| 阅读时长（按天） | `/readdata/detail` | 「日」库（也挂在「年/月/周」关系上） | `时间戳` |

- 进度是**增量写入**：先算出目标值，再和 Notion 现有值逐项比较，只有真的变了才 PATCH，
  同一轮内不会重复处理同一本书。
- 划线 / 想法是**对账写入**：先在 Notion 按 `bookmarkId` / `reviewId` 找到已有块，
  命中就原地更新、没命中才新增；微信读书里已删除的划线，对应块会被清掉。
- `--dry-run` 只校验 Key 与数据可达性，不写任何 Notion 数据。
