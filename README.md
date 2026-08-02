# ⚽ 足球比分推送（astrbot_plugin_livescore_push）

基于 AstrBot 已启用的 **Live-Score MCP** 服务（`livescoremcp.com`）实现的足球比赛实时推送插件。
无需额外 API Key，直接复用 `mcp_server.json` 中的 Live-Score 连接。

## ✨ 功能

| 功能 | 说明 |
| --- | --- |
| 📅 每日赛程 | 每天 8:00 查询关注球队当日赛程并推送（无比赛也会提示） |
| ⏰ 开赛提醒 | 开赛前 15 分钟提醒（精确到分钟，仅一次） |
| 🔔 开赛推送 | 关注比赛开赛时通知 |
| ⚽ 进球推送 | 比赛进行中每分钟轮询，进球立即推送（含比分与进球分钟） |
| 🟥 红牌推送 | 红牌事件推送 |
| 🏁 完场推送 | 比赛结束（FT）时推送赛果 |

## 📦 安装

1. 将本目录放入 AstrBot `data/plugins/` 下
2. 确认 `mcp_server.json` 中 `Live-Score` 服务 `active: true`
3. 在 AstrBot 面板启用插件，重启或热重载

## 🎮 指令

> 💡 群聊中请以 `/` 开头或 @机器人；私聊可直接发送 `球 关注 xx`。

```
/球 关注 曼城   —— 关注球队（模糊匹配队名）
/球 订阅 曼城   —— 同上（兼容写法）
/球 取消 曼城   —— 取消关注（仅当前会话）
/球 列表        —— 查看当前关注
/球 现在        —— 查看进行中的关注比赛
/球 今日        —— 查看今日关注赛程
/球 帮助        —— 查看帮助
```

## ⚙️ 配置（面板中可调）

- `push_chinese`：推送内容中文化（队名/联赛名），默认开
  - 匹配机制：精确匹配 → 剥离 FC/AFC/SC 等前后缀归一化匹配 → 长关键词词边界兜底（如 `FC Volendam` → 福伦丹、`AFC Ajax` → 阿贾克斯）
  - 联赛名同样支持子串匹配（如 `Club Friendlies` → 俱乐部友谊赛）；未收录的队名/联赛保持原文
- `tz_offset`：时区修正（小时），MCP 返回 UTC 时间，默认 8（北京时间）
- `daily_schedule`：每日 8 点赛程推送，默认开
- `remind_minutes`：开赛前提醒分钟数，默认 15，0=关闭
- `push_goal`：进球推送，默认开
- `push_start`：开赛推送，默认开
- `push_finish`：完场推送，默认开

## 🗂 数据文件（插件目录 data/）

- `subscriptions.json`：关注球队列表（名称、推送会话）
- `state.json`：推送状态缓存（今日赛程缓存、事件去重、完场标记、提醒标记）

## 🔧 技术要点

- **MCP 调用**：`context.get_llm_tool_manager().mcp_client_dict["Live-Score"]` → `call_tool_with_reconnect()`
- **动态任务链**（AstrBot 内置 `cron_manager.add_basic_job()`，支持 payload 传参、一次性任务自动删除）：
  - `livescore_daily`（唯一固定任务，每天 8:00）：查询今日赛程 → 缓存到 state → 推送
  - 发现关注比赛后，为每场未开赛比赛动态创建**一次性** `livescore_remind_<比赛ID>`（开赛前 `remind_minutes` 分钟触发，执行后自动删除）
  - `livescore_remind_*` 触发时：推送开赛提醒 → 为该比赛创建 `livescore_poll_<比赛ID>`（每分钟轮询）；任务描述中写明开球时间与推送时间，可在面板「定时任务」页查看
  - `livescore_poll_*`：开赛前 5 分钟开始生效，推送开赛 🔔 / 进球 ⚽ / 红牌 🟥，比赛**正式结束（含加时 AET、点球 PEN）**后推送完场 🏁 并删除自身任务
  - 插件重载后自动从 state 缓存恢复动态任务
- **去重**：以 `get_match` 返回的 events[].id 为唯一键，进球/红牌只推一次
- **流量控制**：轮询仅当比分变化时才拉取比赛详情；完场检测从直播列表消失后拉一次详情确认
- **时区**：MCP 返回 UTC 时间，插件按面板 `tz_offset` 配置修正为本地时间
