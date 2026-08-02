# ⚽ 足球比分推送（astrbot_plugin_livescore_push）

基于 AstrBot 已启用的 **Live-Score MCP** 服务（`livescoremcp.com`）实现的足球比赛实时推送插件。
无需额外 API Key，直接复用 `mcp_server.json` 中的 Live-Score 连接。

## ✨ 功能

| 功能 | 说明 |
| --- | --- |
| ⚽ 进球推送 | 订阅的比赛进球立即推送（含比分与进球分钟） |
| 🟥 红牌推送 | 红牌事件推送 |
| 🔔 开赛推送 | 关注比赛开赛时通知 |
| 🏁 完场推送 | 比赛结束（FT）时推送赛果 |
| 📅 每日赛程 | 每天 8:00 推送当日关注赛程 |
| ⏰ 开赛提醒 | 可选：开赛前 N 分钟提醒（默认关闭） |

## 📦 安装

1. 将本目录放入 AstrBot `data/plugins/` 下
2. 确认 `mcp_server.json` 中 `Live-Score` 服务 `active: true`
3. 在 AstrBot 面板启用插件，重启或热重载

## 🎮 指令

> 💡 群聊中请以 `/` 开头或 @机器人；私聊可直接发送 `球 关注 xx`。

```
/球 订阅 英超   —— 订阅联赛（支持中文名：英超/西甲/德甲/意甲/法甲/欧冠/欧联/中超/日职/韩职，或英文 leagueKey）
/球 关注 曼城   —— 关注球队（模糊匹配队名）
/球 取消 英超   —— 取消订阅（仅当前会话）
/球 列表        —— 查看当前订阅
/球 现在        —— 查看进行中的关注比赛（★=已订阅）
/球 今日        —— 查看今日关注赛程
/球 帮助        —— 查看帮助
```

## ⚙️ 配置（面板中可调）

- `push_goal`：进球推送，默认开
- `push_start`：开赛推送，默认开
- `push_finish`：完场推送，默认开
- `daily_schedule`：每日 8 点赛程推送，默认开
- `remind_minutes`：开赛前提醒分钟数，0=关闭

## 🗂 数据文件（插件目录 data/）

- `subscriptions.json`：订阅列表（类型、名称、推送会话）
- `state.json`：推送状态缓存（事件去重、完场标记、提醒标记）

## 🔧 技术要点

- **MCP 调用**：`context.get_llm_tool_manager().mcp_client_dict["Live-Score"]` → `call_tool_with_reconnect()`
- **定时任务**：AstrBot 内置 `cron_manager.add_basic_job()`（每分钟轮询 / 每日 8 点 / 每 5 分钟提醒），幂等注册防重复
- **去重**：以 `get_match` 返回的 events[].id 为唯一键，进球/红牌只推一次
- **流量控制**：仅当比分变化时才拉取比赛详情，避免高频调用 MCP
- **完场检测**：关注比赛从直播列表消失后拉详情确认 FT 再推送
