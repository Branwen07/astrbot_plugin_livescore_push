import asyncio
import json
import os
import re
from datetime import datetime, timedelta

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, register

try:
    from astrbot.core.star.filter.command import GreedyStr
except ImportError:  # 兼容旧版本
    GreedyStr = str

MCP_SERVER = "Live-Score"

# 常见中文联赛名 → 匹配关键词（匹配 leagueKey / leaguename，小写）
LEAGUE_ALIASES = {
    "英超": ["premier league", "englandpremierleague"],
    "西甲": ["la liga", "laliga", "spainlaliga", "primera division"],
    "德甲": ["bundesliga", "germanybundesliga"],
    "意甲": ["serie a", "italyseriea"],
    "法甲": ["ligue 1", "franceligue1"],
    "欧冠": ["champions league", "europeuefachampionsleague"],
    "欧联": ["europa league", "europeuefaeuropaleague"],
    "中超": ["super league", "chinasuperleague"],
    "日职": ["j1 league", "japanj1"],
    "韩职": ["k league", "koreakleague"],
}

STATUS_ZH = {
    "FT": "完场",
    "HT": "半场",
    "AET": "加时完场",
    "PEN": "点球完场",
    "ABD": "中断",
    "POST": "延期",
    "CANC": "取消",
}


@register(
    "astrbot_plugin_livescore_push",
    "Branwen",
    "基于 Live-Score MCP 的足球比赛实时推送插件：订阅联赛/球队，进球、开赛、完场即时推送到群/私聊，并支持每日赛程与开赛前提醒。",
    "1.0.0",
    "https://github.com/",
)
class LiveScorePush(Star):
    def __init__(self, context: Context, config: AstrBotConfig | dict | None = None):
        super().__init__(context)
        self.config = config or {}
        base = os.path.dirname(os.path.abspath(__file__))
        self.data_dir = os.path.join(base, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        self.subs_file = os.path.join(self.data_dir, "subscriptions.json")
        self.state_file = os.path.join(self.data_dir, "state.json")
        self._subs = self._load_json(self.subs_file, [])
        self._state = self._load_json(self.state_file, {})
        self.session_meta_file = os.path.join(self.data_dir, "session_meta.json")
        self._session_meta = self._load_json(self.session_meta_file, {})
        self._cron_ok = False
        # 面板页面 API：订阅概览（pages/subscriptions 页面调用）
        try:
            self.context.register_web_api(
                "/astrbot_plugin_livescore_push/subs",
                self.web_subs_overview,
                ["GET"],
                "订阅概览：查看各会话订阅的联赛/球队",
            )
            self.context.register_web_api(
                "/astrbot_plugin_livescore_push/subs/add",
                self.web_subs_add,
                ["POST"],
                "为指定会话添加订阅（body: session/type/name）",
            )
            self.context.register_web_api(
                "/astrbot_plugin_livescore_push/subs/remove",
                self.web_subs_remove,
                ["POST"],
                "移除指定会话的订阅（body: session/type/name）",
            )
        except Exception:
            pass
        # 延迟注册定时任务（幂等）
        asyncio.create_task(self._ensure_crons())

    # ==================== 基础工具 ====================

    def _load_json(self, path: str, default):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    def _save_json(self, path: str, data) -> None:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"数据保存失败 {path}: {e}")

    # ==================== MCP 调用 ====================

    async def _mcp(self, tool: str, **kwargs):
        """调用 Live-Score MCP 工具并解析出 JSON 数据。"""
        try:
            mgr = self.context.get_llm_tool_manager()
            client = mgr.mcp_client_dict.get(MCP_SERVER)
            if not client:
                logger.warning("未找到 Live-Score MCP 客户端，请确认 mcp_server.json 中已启用。")
                return None
            result = await client.call_tool_with_reconnect(
                tool_name=tool,
                arguments=kwargs or {},
                read_timeout_seconds=timedelta(seconds=30),
            )
            texts = []
            for c in (result.content or []):
                text = getattr(c, "text", None)
                if text:
                    texts.append(text)
            return self._extract_json("\n".join(texts))
        except Exception as e:
            logger.error(f"Live-Score MCP 调用失败 {tool}: {e}")
            return None

    @staticmethod
    def _extract_json(raw: str):
        if not raw:
            return None
        for ch in ("[", "{"):
            i = raw.find(ch)
            if i >= 0:
                try:
                    return json.loads(raw[i:])
                except Exception:
                    continue
        return None

    # ==================== 订阅匹配 ====================

    @classmethod
    def _expand_name(cls, name: str) -> list[str]:
        name = name.strip().lower()
        kws = [name]
        if name in LEAGUE_ALIASES:
            kws += LEAGUE_ALIASES[name]
        return [k for k in kws if k]

    def _league_hit(self, match: dict, league: dict, sub_name: str) -> bool:
        key = (match.get("leagueKey") or league.get("key") or "").lower()
        name = (match.get("leaguename") or league.get("league") or "").lower()
        return any(kw in key or kw in name for kw in self._expand_name(sub_name))

    def _team_hit(self, match: dict, sub_name: str) -> bool:
        kw = sub_name.strip().lower()
        if not kw:
            return False
        local = (match.get("localteam") or "").lower()
        visitor = (match.get("visitorteam") or "").lower()
        return kw in local or kw in visitor

    def _watched_subs(self, match: dict, league: dict) -> list[dict]:
        hits = []
        for s in self._subs:
            try:
                if s["type"] == "league" and self._league_hit(match, league, s["name"]):
                    hits.append(s)
                elif s["type"] == "team" and self._team_hit(match, s["name"]):
                    hits.append(s)
            except Exception:
                continue
        return hits

    # ==================== 推送 ====================

    async def _push(self, text: str, subs: list[dict]) -> None:
        sessions: set[str] = set()
        for s in subs:
            sessions.update(s.get("sessions", []))
        for sess in sessions:
            try:
                await self.context.send_message(sess, MessageChain([Plain(text)]))
            except Exception as e:
                logger.error(f"消息推送失败 {sess}: {e}")

    # ==================== 定时任务 ====================

    async def _ensure_crons(self) -> None:
        if self._cron_ok:
            return
        try:
            cm = self.context.cron_manager
            jobs = await cm.list_jobs()
            names = {getattr(j, "name", "") for j in jobs}
            if "livescore_poll" not in names:
                await cm.add_basic_job(
                    name="livescore_poll",
                    cron_expression="*/1 * * * *",
                    handler=self.poll_live,
                    description="足球实时比分轮询（进球/开赛/完场推送）",
                )
            if self.config.get("daily_schedule", True) and "livescore_daily" not in names:
                await cm.add_basic_job(
                    name="livescore_daily",
                    cron_expression="0 8 * * *",
                    handler=self.daily_schedule,
                    description="每日 8 点推送关注赛程",
                )
            remind = int(self.config.get("remind_minutes", 0) or 0)
            if remind > 0 and "livescore_remind" not in names:
                await cm.add_basic_job(
                    name="livescore_remind",
                    cron_expression="*/5 * * * *",
                    handler=self.remind_check,
                    description="开赛前提醒",
                )
            self._cron_ok = True
            logger.info("Live-Score 推送定时任务已注册。")
        except Exception as e:
            logger.error(f"定时任务注册失败: {e}")

    # ---- 核心轮询：每分钟一次 ----
    async def poll_live(self) -> None:
        if not self._subs:
            return
        data = await self._mcp("get_live_scores")
        if not data:
            return
        live_ids: set[str] = set()
        for country in data:
            for lg in country.get("leagues", []):
                for m in lg.get("matches", []):
                    mid = str(m.get("id", ""))
                    if not mid:
                        continue
                    live_ids.add(mid)
                    subs = self._watched_subs(m, lg)
                    if subs:
                        await self._process_live_match(m, lg, subs)
        # 完场检测：关注比赛从直播列表消失 → 拉详情确认 FT
        for mid, st in list(self._state.items()):
            if mid in live_ids or not st.get("score") or st.get("pushed_finish"):
                continue
            detail = await self._mcp("get_match", id=mid, h2h=0)
            if detail and str(detail.get("status", "")) == "FT":
                st["pushed_finish"] = True
                self._save_json(self.state_file, self._state)
                if not self.config.get("push_finish", True):
                    continue
                subs = self._watched_subs(detail, {})
                if subs:
                    league = detail.get("leaguename") or ""
                    score = detail.get("scoretime") or ""
                    await self._push(
                        f"🏁 [{league}] {detail.get('localteam','')} {score} {detail.get('visitorteam','')} 全场结束",
                        subs,
                    )

    async def _process_live_match(self, m: dict, lg: dict, subs: list[dict]) -> None:
        mid = str(m["id"])
        score = str(m.get("scoretime") or "")
        status = str(m.get("status") or "")
        is_live = status.isdigit()  # 数字 = 比赛进行中（分钟数）
        league = lg.get("leaguename") or m.get("leaguename") or ""
        local, visitor = m.get("localteam", ""), m.get("visitorteam", "")
        st = self._state.get(mid)

        if st is None:
            st = {"events": {}, "score": score, "pushed_finish": False}
            self._state[mid] = st
            if is_live and self.config.get("push_start", True):
                await self._push(f"🔔 [{league}] {local} vs {visitor} 开赛！", subs)
        else:
            st["score"] = score

        # 比分变化（或首次出现）才拉详情，控制 MCP 调用量
        if is_live and self.config.get("push_goal", True):
            if not st.get("detail_fetched") or st.get("last_score") != score:
                detail = await self._mcp("get_match", id=mid, h2h=0)
                st["detail_fetched"] = True
                if detail:
                    for e in (detail.get("events") or []):
                        eid = str(e.get("id") or "")
                        if not eid:
                            continue
                        if eid in st["events"]:
                            continue
                        st["events"][eid] = True
                        etype = e.get("type", "")
                        minute = e.get("minute") or ""
                        if etype == "goal":
                            await self._push(
                                f"⚽ [{league}] {local} {score} {visitor} · {minute}'", subs
                            )
                        elif etype == "redcard":
                            await self._push(
                                f"🟥 [{league}] {local} {score} {visitor} · 红牌 {minute}'", subs
                            )
        st["last_score"] = score
        self._save_json(self.state_file, self._state)

    # ---- 每日 8 点：今日关注赛程 ----
    async def daily_schedule(self) -> None:
        if not self._subs:
            return
        today = datetime.now().strftime("%d/%m/%Y")
        data = await self._mcp("get_day_fixtures", date=today, language="en", tzoffset=480)
        if not data:
            return
        lines, hit_subs = [], set()
        for country in data:
            for lg in country.get("leagues", []):
                for m in lg.get("matches", []):
                    subs = self._watched_subs(m, lg)
                    if not subs:
                        continue
                    hit_subs.update(id(s) for s in subs)
                    t = m.get("time") or ""
                    status = str(m.get("status") or "")
                    if re.fullmatch(r"\d{1,2}:\d{2}", status):
                        lines.append(f"· {t} {m['localteam']} vs {m['visitorteam']}")
                    elif status:
                        lines.append(f"· {t} {m['localteam']} {m.get('scoretime','')} {m['visitorteam']}（{STATUS_ZH.get(status, status)}）")
        if lines:
            text = "📅 今日关注赛程：\n" + "\n".join(lines[:25])
            await self._push(text, [s for s in self._subs if id(s) in hit_subs])

    # ---- 开赛前提醒（每 5 分钟） ----
    async def remind_check(self) -> None:
        mins = int(self.config.get("remind_minutes", 0) or 0)
        if mins <= 0 or not self._subs:
            return
        now = datetime.now()
        today = now.strftime("%d/%m/%Y")
        data = await self._mcp("get_day_fixtures", date=today, language="en", tzoffset=480)
        if not data:
            return
        changed = False
        for country in data:
            for lg in country.get("leagues", []):
                for m in lg.get("matches", []):
                    status = str(m.get("status") or "")
                    if not re.fullmatch(r"\d{1,2}:\d{2}", status):
                        continue  # 未开赛的比赛 status 为开赛时间
                    try:
                        kickoff = datetime.strptime(
                            f"{now.strftime('%Y-%m-%d')} {status}", "%Y-%m-%d %H:%M"
                        )
                    except Exception:
                        continue
                    delta = (kickoff - now).total_seconds() / 60
                    if not (0 < delta <= mins):
                        continue
                    mid = str(m.get("id", ""))
                    key = f"remind_{mid}"
                    if self._state.get(key):
                        continue
                    subs = self._watched_subs(m, lg)
                    if subs:
                        self._state[key] = True
                        changed = True
                        await self._push(
                            f"⏰ [{lg.get('leaguename','')}] {m['localteam']} vs {m['visitorteam']} "
                            f"{status} 开球，约 {int(delta)} 分钟后！",
                            subs,
                        )
        if changed:
            self._save_json(self.state_file, self._state)

    # ==================== 指令 ====================

    @filter.command("球", alias={"football", "足球"}, desc="足球比分推送：订阅/关注/取消/列表/现在/今日/帮助")
    async def cmd(self, event: AstrMessageEvent, arg: GreedyStr) -> None:
        await self._ensure_crons()
        arg = (arg or "").strip()
        session = event.unified_msg_origin
        self._record_session_meta(event, session)
        if not arg or arg in ("help", "帮助"):
            yield event.plain_result(self._usage())
            return
        parts = arg.split(None, 1)
        action, rest = parts[0], (parts[1] if len(parts) > 1 else "").strip()

        if action in ("订阅", "sub", "subscribe"):
            if not rest:
                yield event.plain_result("用法：/球 订阅 <联赛名>，如：/球 订阅 英超")
                return
            yield event.plain_result(self._add_sub("league", rest, session))
        elif action in ("关注", "follow"):
            if not rest:
                yield event.plain_result("用法：/球 关注 <球队名>，如：/球 关注 曼城")
                return
            yield event.plain_result(self._add_sub("team", rest, session))
        elif action in ("取消", "unsub"):
            if not rest:
                yield event.plain_result("用法：/球 取消 <关键词>，如：/球 取消 英超")
                return
            yield event.plain_result(self._remove_sub(rest, session))
        elif action in ("列表", "list"):
            yield event.plain_result(self._list_subs())
        elif action in ("现在", "live"):
            yield event.plain_result(await self._query_live())
        elif action in ("今日", "today"):
            yield event.plain_result(await self._query_today())
        else:
            yield event.plain_result(self._usage())

    # ---- 面板 API / 会话记录 ----
    def _record_session_meta(self, event: AstrMessageEvent, session: str) -> None:
        """记录会话的可读信息（平台、发送者昵称、最近活跃时间），供面板页面展示。"""
        try:
            meta = dict(self._session_meta.get(session) or {})
            meta.setdefault("platform", "")
            try:
                platform = event.get_platform_name()
                if platform:
                    meta["platform"] = platform
            except Exception:
                pass
            try:
                sender = event.message_obj.sender
                nick = getattr(sender, "nickname", None) or getattr(sender, "card", None)
                if not nick:
                    uid = getattr(sender, "user_id", None) or getattr(sender, "qq", None)
                    nick = f"用户{uid}" if uid else ""
                if nick:
                    meta["sender"] = str(nick)
            except Exception:
                pass
            meta["last_seen"] = datetime.now().strftime("%m-%d %H:%M")
            self._session_meta[session] = meta
            self._save_json(self.session_meta_file, self._session_meta)
        except Exception:
            pass

    async def web_subs_overview(self):
        """面板页面 API：返回全部订阅与会话元信息。"""
        return {
            "subs": self._subs,
            "session_meta": self._session_meta,
        }

    async def web_subs_add(self):
        """面板页面 API：为指定会话添加订阅。"""
        from astrbot.api.web import request

        body = await request.json() or {}
        session = str(body.get("session") or "").strip()
        name = str(body.get("name") or "").strip()
        stype = str(body.get("type") or "").strip()
        if not session or not name:
            return {"ok": False, "message": "缺少参数：session / name"}
        if stype not in ("league", "team"):
            return {"ok": False, "message": "type 必须是 league 或 team"}
        msg = self._add_sub(stype, name, session)
        ok = not msg.startswith(("订阅失败", "失败"))
        return {"ok": ok, "message": msg}

    async def web_subs_remove(self):
        """面板页面 API：移除指定会话的订阅（精确匹配）。"""
        from astrbot.api.web import request

        body = await request.json() or {}
        session = str(body.get("session") or "").strip()
        name = str(body.get("name") or "").strip()
        stype = str(body.get("type") or "").strip()
        if not session or not name:
            return {"ok": False, "message": "缺少参数：session / name"}
        removed = False
        keep = []
        for s in self._subs:
            if s["type"] == stype and s["name"] == name and session in s["sessions"]:
                s["sessions"] = [x for x in s["sessions"] if x != session]
                removed = True
            if s["sessions"]:
                keep.append(s)
        if removed:
            self._subs = keep
            self._save_json(self.subs_file, self._subs)
            return {"ok": True, "message": f"已取消「{name}」在本会话的订阅"}
        return {"ok": False, "message": "未找到该订阅"}

    # ---- 订阅管理 ----
    def _add_sub(self, stype: str, name: str, session: str) -> str:
        name = name.strip()
        if stype == "league":
            kws = self._expand_name(name)
            if not kws:
                return "订阅失败：无效的联赛名称"
        for s in self._subs:
            if s["type"] == stype and s["name"] == name:
                if session not in s["sessions"]:
                    s["sessions"].append(session)
                    self._save_json(self.subs_file, self._subs)
                return f"已订阅「{name}」（本会话已加入推送）"
        self._subs.append({"type": stype, "name": name, "sessions": [session]})
        self._save_json(self.subs_file, self._subs)
        tip = "联赛" if stype == "league" else "球队"
        return f"✅ 已{('订阅' if stype=='league' else '关注')}「{name}」{tip}，有比赛时将推送至此会话"

    def _remove_sub(self, kw: str, session: str) -> str:
        kw = kw.lower()
        removed = 0
        keep = []
        for s in self._subs:
            if kw in s["name"].lower():
                s["sessions"] = [x for x in s["sessions"] if x != session]
                if s["sessions"]:
                    keep.append(s)
                removed += 1
            else:
                keep.append(s)
        self._subs = keep
        self._save_json(self.subs_file, self._subs)
        if removed:
            return f"已取消 {removed} 个匹配订阅（仅本会话）"
        return "未找到匹配的订阅"

    def _list_subs(self) -> str:
        if not self._subs:
            return "暂无订阅。用 /球 订阅 英超 或 /球 关注 曼城 开始吧"
        lines = []
        for i, s in enumerate(self._subs, 1):
            tag = "联赛" if s["type"] == "league" else "球队"
            lines.append(f"{i}. [{tag}] {s['name']}（{len(s['sessions'])} 个会话）")
        return "📋 当前订阅：\n" + "\n".join(lines)

    # ---- 查询 ----
    async def _query_live(self) -> str:
        data = await self._mcp("get_live_scores")
        if not data:
            return "无法获取直播数据（MCP 未连接或暂无进行中比赛）"
        lines = []
        for country in data:
            for lg in country.get("leagues", []):
                for m in lg.get("matches", []):
                    subs = self._watched_subs(m, lg)
                    mark = "★" if subs else " "
                    status = str(m.get("status") or "")
                    if status.isdigit():
                        status = f"{status}'"
                    lines.append(
                        f"{mark} [{lg.get('leaguename','')}] {m['localteam']} {m.get('scoretime','')} {m['visitorteam']}（{status}）"
                    )
        if not lines:
            return "当前没有进行中的比赛"
        return "🔴 进行中比赛（★=已订阅）：\n" + "\n".join(lines[:25])

    async def _query_today(self) -> str:
        today = datetime.now().strftime("%d/%m/%Y")
        data = await self._mcp("get_day_fixtures", date=today, language="en", tzoffset=480)
        if not data:
            return "无法获取今日赛程"
        lines = []
        for country in data:
            for lg in country.get("leagues", []):
                for m in lg.get("matches", []):
                    subs = self._watched_subs(m, lg)
                    if not subs:
                        continue
                    mark = "★"
                    t = m.get("time") or ""
                    status = str(m.get("status") or "")
                    if re.fullmatch(r"\d{1,2}:\d{2}", status):
                        lines.append(f"{mark} {t} {m['localteam']} vs {m['visitorteam']}")
                    elif status:
                        lines.append(f"{mark} {t} {m['localteam']} {m.get('scoretime','')} {m['visitorteam']}（{STATUS_ZH.get(status, status)}）")
        if not lines:
            return "今日没有订阅相关的比赛"
        return "📅 今日关注赛程（★=已订阅）：\n" + "\n".join(lines[:25])

    @staticmethod
    def _usage() -> str:
        return (
            "⚽ 足球比分推送（基于 Live-Score MCP）\n"
            "/球 订阅 <联赛> —— 订阅联赛，如：/球 订阅 英超\n"
            "/球 关注 <球队> —— 关注球队，如：/球 关注 曼城\n"
            "/球 取消 <关键词> —— 取消订阅\n"
            "/球 列表 —— 查看订阅\n"
            "/球 现在 —— 查看进行中的关注比赛\n"
            "/球 今日 —— 查看今日关注赛程\n"
            "/球 帮助 —— 本帮助\n"
            "推送功能：进球 ⚽ / 开赛 🔔 / 完场 🏁 / 红牌 🟥 / 每日赛程 📅 / 开赛提醒 ⏰"
        )
