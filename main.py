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

STATUS_ZH = {
    "FT": "完场",
    "HT": "半场",
    "AET": "加时完场",
    "PEN": "点球完场",
    "ABD": "中断",
    "POST": "延期",
    "CANC": "取消",
}

# 中文球队名 → 匹配关键词（Live-Score 返回英文队名，需中英对照）
TEAM_ALIASES = {
    # 中超 2026（官方英文名来自 get_league_fixtures("ChinaCSL")）
    "北京国安": ["beijing guoan"],
    "成都蓉城": ["chengdu rongcheng"],
    "重庆铜梁龙": ["chongqing tongliang long"],
    "大连英博": ["dalian yingbo"],
    "辽宁铁人": ["liaoning shenyang urban", "liaoning"],
    "辽宁沈阳城市": ["liaoning shenyang urban"],
    "青岛海牛": ["qingdao hainiu"],
    "青岛西海岸": ["qingdao west coast"],
    "山东泰山": ["shandong taishan"],
    "上海海港": ["shanghai port"],
    "上海申花": ["shanghai shenhua"],
    "四川九牛": ["sichuan jiuniu"],
    "天津津门虎": ["tianjin jinmen tiger"],
    "武汉三镇": ["wuhan three towns"],
    "云南玉昆": ["yunnan yukun"],
    "浙江队": ["zhejiang"],
    "浙江": ["zhejiang"],
    "河南队": ["henan"],
    "河南": ["henan"],
    # 主流欧洲 / 亚洲豪门
    "曼城": ["manchester city", "man city"],
    "曼联": ["manchester united", "man united"],
    "利物浦": ["liverpool"],
    "阿森纳": ["arsenal"],
    "切尔西": ["chelsea"],
    "热刺": ["tottenham"],
    "皇马": ["real madrid"],
    "皇家马德里": ["real madrid"],
    "巴萨": ["barcelona"],
    "巴塞罗那": ["barcelona"],
    "拜仁": ["bayern"],
    "拜仁慕尼黑": ["bayern munich"],
    "多特": ["dortmund"],
    "巴黎": ["paris saint germain", "paris"],
    "尤文": ["juventus"],
    "国米": ["inter milan", "inter"],
    "国际米兰": ["inter milan"],
    "ac米兰": ["ac milan"],
    "马竞": ["atletico madrid"],
    "那不勒斯": ["napoli"],
    "罗马": ["roma"],
    "本菲卡": ["benfica"],
    "波尔图": ["porto"],
    "阿贾克斯": ["ajax"],
    "费耶诺德": ["feyenoord"],
    "埃因霍温": ["psv"],
    "凯尔特人": ["celtic"],
    "浦和红钻": ["urawa"],
    "横滨水手": ["yokohama"],
    "川崎前锋": ["kawasaki"],
    "全北现代": ["jeonbuk"],
    "蔚山现代": ["ulsan"],
    "利雅得胜利": ["al nassr"],
    "利雅得新月": ["al hilal"],
    "迈阿密国际": ["inter miami"],
}

# 正式结束状态（含加时赛 AET、点球大战 PEN）
FINISH_STATUS = {"FT", "AET", "PEN"}

# 英文队名 → 中文（推送内容中文化，官方英文名来自 Live-Score MCP）
TEAM_CN = {
    # 中超 2026
    "Beijing Guoan": "北京国安",
    "Chengdu Rongcheng": "成都蓉城",
    "Chongqing Tongliang Long": "重庆铜梁龙",
    "Dalian Zhixing": "大连英博",
    "Henan": "河南队",
    "Liaoning Shenyang Urban": "辽宁铁人",
    "Qingdao Hainiu": "青岛海牛",
    "Qingdao West Coast": "青岛西海岸",
    "Shandong Taishan": "山东泰山",
    "Shanghai Port": "上海海港",
    "Shanghai Shenhua": "上海申花",
    "Sichuan Jiuniu": "四川九牛",
    "Tianjin Jinmen Tiger": "天津津门虎",
    "Wuhan Three Towns": "武汉三镇",
    "Yunnan Yukun": "云南玉昆",
    "Zhejiang": "浙江队",
    # 主流豪门
    "Manchester City": "曼城",
    "Man City": "曼城",
    "Manchester United": "曼联",
    "Man United": "曼联",
    "Liverpool": "利物浦",
    "Arsenal": "阿森纳",
    "Chelsea": "切尔西",
    "Tottenham": "热刺",
    "Tottenham Hotspur": "热刺",
    "Real Madrid": "皇家马德里",
    "Barcelona": "巴塞罗那",
    "Bayern Munich": "拜仁慕尼黑",
    "Borussia Dortmund": "多特蒙德",
    "Paris Saint Germain": "巴黎圣日耳曼",
    "PSG": "巴黎圣日耳曼",
    "Juventus": "尤文图斯",
    "Inter": "国际米兰",
    "Inter Milan": "国际米兰",
    "AC Milan": "AC米兰",
    "Atletico Madrid": "马德里竞技",
    "Napoli": "那不勒斯",
    "Roma": "罗马",
    "Benfica": "本菲卡",
    "Porto": "波尔图",
    "Ajax": "阿贾克斯",
    "Feyenoord": "费耶诺德",
    "PSV": "埃因霍温",
    "Celtic": "凯尔特人",
    "Rangers": "流浪者",
    "Urawa Red Diamonds": "浦和红钻",
    "Yokohama F. Marinos": "横滨水手",
    "Kawasaki Frontale": "川崎前锋",
    "Jeonbuk": "全北现代",
    "Jeonbuk Hyundai Motors": "全北现代",
    "Ulsan": "蔚山现代",
    "Ulsan Hyundai": "蔚山现代",
    "Al Nassr": "利雅得胜利",
    "Al Hilal": "利雅得新月",
    "Inter Miami": "迈阿密国际",
    # 荷甲 / 荷乙 / 友谊赛常见对手
    "Volendam": "福伦丹",
    "AZ Alkmaar": "阿尔克马尔",
    "FC Utrecht": "乌德勒支",
    "FC Twente": "特温特",
    "PSV Eindhoven": "埃因霍温",
    "SC Heerenveen": "海伦芬",
    "Vitesse": "维特斯",
    "FC Groningen": "格罗宁根",
    "NEC Nijmegen": "奈梅亨",
    "Sparta Rotterdam": "鹿特丹斯巴达",
    "Go Ahead Eagles": "前进之鹰",
    "PEC Zwolle": "兹沃勒",
    "Heracles Almelo": "赫拉克勒斯",
    "RKC Waalwijk": "瓦尔韦克",
    "Almere City": "阿尔梅勒城",
    "FC Emmen": "埃门",
    "NAC Breda": "布雷达",
    "Willem II": "威廉二世",
    "Fortuna Sittard": "锡塔德幸运",
    "ADO Den Haag": "海牙",
    "Excelsior": "精英",
    "Cambuur": "坎布尔",
    "Roda JC": "罗达JC",
    "FC Dordrecht": "多德雷赫特",
    "VVV-Venlo": "芬洛",
    "FC Eindhoven": "埃因霍温FC",
    "Telstar": "特尔斯达",
    "MVV Maastricht": "马斯特里赫特",
    "FC Den Bosch": "登博斯",
    "Helmond Sport": "赫尔蒙德",
    "TOP Oss": "奥斯",
    "De Graafschap": "格拉夫夏普",
    "Jong Ajax": "阿贾克斯青年队",
    "Jong PSV": "埃因霍温青年队",
    "Jong AZ": "阿尔克马尔青年队",
}

# 队名前缀/后缀剥离规则（归一化匹配用，如 "FC Volendam" → "Volendam"）
_TEAM_NAME_PREFIXES = (
    "fc ", "afc ", "sc ", "sv ", "fk ", "ss ", "cf ", "cd ", "ac ", "as ",
    "rc ", "rs ", "sk ", "bk ", "tsv ", "vfl ", "sbv ", "deportivo ",
    "atletico ", "athletic ", "club ", "royal ",
)
_TEAM_NAME_SUFFIXES = (" fc", " afc", " sc", " cf", " fk", " sk", " sv")


def _norm_team_name(name: str) -> str:
    """剥离常见俱乐部前后缀，返回小写规范化名（如 FC Volendam → volendam）。"""
    n = (name or "").strip().lower()
    for p in _TEAM_NAME_PREFIXES:
        if n.startswith(p):
            n = n[len(p):]
            break
    for sfx in _TEAM_NAME_SUFFIXES:
        if n.endswith(sfx):
            n = n[:-len(sfx)]
            break
    return n.strip()


# 规范化名 → 中文（构建一次）
NORM_TEAM_CN = {_norm_team_name(k): v for k, v in TEAM_CN.items() if _norm_team_name(k)}

# 联赛名/Key → 中文
LEAGUE_CN = {
    "Chinese Super League": "中超", "ChinaCSL": "中超",
    "China League One": "中甲", "ChinaLeagueOne": "中甲",
    "China League Two": "中乙", "ChinaLeagueTwo": "中乙",
    "Premier League": "英超",
    "La Liga": "西甲",
    "Serie A": "意甲",
    "Bundesliga": "德甲",
    "Ligue 1": "法甲",
    "Champions League": "欧冠",
    "Europa League": "欧联",
    "FA Cup": "足总杯",
    "Copa del Rey": "国王杯",
    "Coppa Italia": "意大利杯",
    "DFB Pokal": "德国杯",
    "Coupe de France": "法国杯",
    "J.League": "J联赛",
    "K League": "K联赛",
    # 友谊赛 / 杯赛
    "Club Friendlies": "俱乐部友谊赛",
    "Club Friendlies 1": "俱乐部友谊赛",
    "ClubFriendlies": "俱乐部友谊赛",
    "ClubFriendlies1": "俱乐部友谊赛",
    "Club Friendlies 2": "俱乐部友谊赛",
    "ClubFriendlies 2": "俱乐部友谊赛",
    "International Club Friendlies": "国际俱乐部友谊赛",
    "International Friendly": "国际友谊赛",
    "InternationalFriendly": "国际友谊赛",
    "Int. Friendly": "国际友谊赛",
    "Int Friendly": "国际友谊赛",
    "Friendlies": "友谊赛",
    "FA Cup (China)": "足协杯",
    "Super Cup": "超级杯",
    "Community Shield": "社区盾",
}

# 动态任务命名（按比赛 ID 区分）
JOB_DAILY = "livescore_daily"
JOB_REMIND_PREFIX = "livescore_remind_"
JOB_POLL_PREFIX = "livescore_poll_"

# 轮询任务提前开始时间（分钟）：开赛前 5 分钟即开始轮询，确保推送"开赛"
POLL_LEAD_MINUTES = 5


@register(
    "astrbot_plugin_livescore_push",
    "Branwen",
    "基于 Live-Score MCP 的足球比赛实时推送插件：关注球队，进球、开赛、完场即时推送到群/私聊，并支持每日赛程与开赛前提醒。",
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
        # 仅保留球队订阅（历史联赛订阅数据已弃用，启动时清理）
        _old_subs = self._load_json(self.subs_file, [])
        self._subs = [s for s in _old_subs if s.get("type") == "team" and s.get("name")]
        if self._subs != _old_subs:
            self._save_json(self.subs_file, self._subs)
        self._state = self._load_json(self.state_file, {})
        self._poll_lock = asyncio.Lock()
        self.session_meta_file = os.path.join(self.data_dir, "session_meta.json")
        self._session_meta = self._load_json(self.session_meta_file, {})
        self._cron_ok = False
        # 面板页面 API：订阅概览（pages/subscriptions 页面调用）
        try:
            self.context.register_web_api(
                "/astrbot_plugin_livescore_push/subs",
                self.web_subs_overview,
                ["GET"],
                "订阅概览：查看各会话关注的球队",
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

    # ==================== 中文显示 ====================

    def _zh(self) -> bool:
        """是否开启中文推送（默认开）。"""
        try:
            return bool(self.config.get("push_chinese", True))
        except Exception:
            return True

    def _team_cn(self, name_en: str) -> str:
        """英文队名 → 中文：精确匹配 → 剥离前后缀归一化匹配 → 长关键词词边界兜底。"""
        if not self._zh() or not name_en:
            return name_en or ""
        if name_en in TEAM_CN:
            return TEAM_CN[name_en]
        norm = _norm_team_name(name_en)
        if norm and norm in NORM_TEAM_CN:
            return NORM_TEAM_CN[norm]
        # 长关键词（≥6 字符）词边界子串匹配，覆盖 FC Volendam II 这类变体
        for k in sorted(NORM_TEAM_CN, key=len, reverse=True):
            if len(k) < 6:
                continue
            if re.search(rf"\b{re.escape(k)}\b", name_en, re.IGNORECASE):
                return NORM_TEAM_CN[k]
        return name_en

    def _league_cn(self, league: str, key: str = "") -> str:
        """联赛名/Key → 中文：精确 → 长关键词子串（如 Club Friendlies → 俱乐部友谊赛）。"""
        if not self._zh():
            return league or ""
        for k in sorted(LEAGUE_CN, key=len, reverse=True):
            if not k:
                continue
            if k == key or k == league or (len(k) >= 4 and league and k in league):
                return LEAGUE_CN[k]
        return league or ""

    # ==================== 订阅匹配 ====================

    def _team_hit(self, match: dict, sub_name: str) -> bool:
        kw = sub_name.strip().lower()
        if not kw:
            return False
        kws = [kw] + TEAM_ALIASES.get(kw, [])
        local = (match.get("localteam") or "").lower()
        visitor = (match.get("visitorteam") or "").lower()
        return any(k in local or k in visitor for k in kws)

    def _watched_subs(self, match: dict, league: dict) -> list[dict]:
        hits = []
        for s in self._subs:
            try:
                if s["type"] == "team" and self._team_hit(match, s["name"]):
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
            # 删除本插件全部旧任务（固定 daily + 动态 remind_/poll_），重建。
            # 旧任务记录残留在 DB（persistent=False 不参与重启调度），
            # 若仅按 name 去重会跳过注册，导致 handler 注册表缺失、
            # 定时任务不再执行、面板"立即执行"报 handler not found。
            for j in jobs:
                name = getattr(j, "name", "")
                if name == JOB_DAILY or name.startswith((JOB_REMIND_PREFIX, JOB_POLL_PREFIX)):
                    try:
                        await cm.delete_job(j.job_id)
                    except Exception as e:
                        logger.warning(f"清理旧任务 {name} 失败: {e}")
            # 固定任务：每日 8 点，发现关注比赛后动态调度提醒/轮询任务
            await cm.add_basic_job(
                name=JOB_DAILY,
                cron_expression="0 8 * * *",
                handler=self.daily_schedule,
                description="每日 8 点查询订阅球队当日赛程并调度开赛提醒/轮询任务",
            )
            # 恢复动态任务（插件重载后 remind_/poll_ 已随上面清理）
            await self._schedule_today_tasks()
            self._cron_ok = True
            logger.info("Live-Score 定时任务已注册（daily + 动态恢复）。")
        except Exception as e:
            logger.error(f"定时任务注册失败: {e}")

    # ---- 动态任务管理 ----
    async def _job_exists(self, name: str) -> bool:
        try:
            cm = self.context.cron_manager
            jobs = await cm.list_jobs("basic")
            return any(getattr(j, "name", "") == name for j in jobs)
        except Exception:
            return False

    async def _remove_job_by_name(self, name: str) -> None:
        try:
            cm = self.context.cron_manager
            jobs = await cm.list_jobs("basic")
            for j in jobs:
                if getattr(j, "name", "") == name:
                    await cm.delete_job(j.job_id)
        except Exception as e:
            logger.warning(f"删除任务 {name} 失败: {e}")

    async def _add_remind_job(self, m: dict, remind_at, kickoff=None) -> None:
        """注册一次性开赛提醒任务（到点执行后自动删除）。

        description 中写明推送时间，便于可视化面板查看；
        先以 enabled=False 创建再启用，避免占位 cron 产生"每天 0 点"的幽灵任务。
        """
        mid = m.get("mid", "")
        name = f"{JOB_REMIND_PREFIX}{mid}"
        if await self._job_exists(name):
            return
        cm = self.context.cron_manager
        local, visitor = self._team_cn(m.get("local", "")), self._team_cn(m.get("visitor", ""))
        t_push = remind_at.strftime("%m-%d %H:%M")
        t_ko = kickoff.strftime("%H:%M") if kickoff else "?"
        job = await cm.add_basic_job(
            name=name,
            cron_expression=remind_at.isoformat(),
            handler=self.remind_check,
            payload={"mid": mid},
            description=f"开赛提醒：{local} vs {visitor}（{t_ko} 开球，{t_push} 推送）",
            persistent=False,
            enabled=False,
        )
        await cm.update_job(
            job.job_id, enabled=True, run_once=True, cron_expression=remind_at.isoformat()
        )

    async def _add_poll_job(self, m: dict) -> None:
        """注册每分钟轮询任务（开赛前 POLL_LEAD_MINUTES 分钟开始生效，完场后删除）。"""
        mid = m.get("mid", "")
        name = f"{JOB_POLL_PREFIX}{mid}"
        if await self._job_exists(name):
            return
        cm = self.context.cron_manager
        local, visitor = self._team_cn(m.get("local", "")), self._team_cn(m.get("visitor", ""))
        await cm.add_basic_job(
            name=name,
            cron_expression="*/1 * * * *",
            handler=self.poll_match,
            payload={"mid": mid},
            description=f"比赛轮询：{local} vs {visitor}（每分钟，完场自动移除）",
            persistent=False,
        )

    async def _schedule_today_tasks(self) -> None:
        """为今日每场关注比赛安排动态任务：未开赛→一次性提醒，已开赛→轮询。"""
        cached = self._state.get("today") or {}
        matches = cached.get("matches", [])
        now = datetime.now()
        today_key = now.strftime("%Y-%m-%d")
        mins = int(self.config.get("remind_minutes", 0) or 0)
        for m in matches:
            if m.get("finished") or not m.get("mid"):
                continue
            kickoff = self._kickoff_local(m, today_key)
            if kickoff and kickoff > now and not m.get("remind_done"):
                # 未开赛：注册一次性提醒（开赛前 mins 分钟）
                if mins > 0:
                    remind_at = kickoff - timedelta(minutes=mins)
                    if remind_at > now:
                        await self._add_remind_job(m, remind_at, kickoff)
                        continue
            # 提醒时刻已过 / 已提醒过 / 已开赛：直接安排轮询（提前 5 分钟生效）
            await self._add_poll_job(m)

    # ---- 每场轮询：每分钟一次（开赛前 5 分钟开始生效） ----
    async def poll_match(self, mid: str = None) -> None:
        if not self._subs or not mid:
            return
        if self._poll_lock.locked():
            logger.info("poll_match: 上一次执行未完成，跳过本轮")
            return
        async with self._poll_lock:
            cached = self._state.get("today") or {}
            if cached.get("date") != datetime.now().strftime("%Y-%m-%d"):
                return  # 缓存过期（跨天），等 daily 刷新
            m = next((x for x in cached.get("matches", []) if x.get("mid") == mid), None)
            if not m or m.get("finished"):
                await self._remove_job_by_name(f"{JOB_POLL_PREFIX}{mid}")
                return
            now = datetime.now()
            kickoff = self._kickoff_local(m, now.strftime("%Y-%m-%d"))
            if kickoff and now < kickoff - timedelta(minutes=POLL_LEAD_MINUTES):
                return  # 未到轮询开始时间（提前 5 分钟）
            data = await self._mcp("get_live_scores")
            if data is None:
                return  # 获取失败；空列表表示无进行中比赛，继续完场检查
            live_match = None
            for country in data:
                for lg in country.get("leagues", []):
                    for mm in lg.get("matches", []):
                        if str(mm.get("id", "")) == mid:
                            live_match = mm
                            break
                    if live_match:
                        break
                if live_match:
                    break
            subs = [s for s in self._subs if s["name"] in m.get("subs", [])]
            if live_match:
                await self._process_live_match(live_match, subs, m.get("league", ""))
            else:
                await self._check_finish(m)

    async def _check_finish(self, m: dict) -> bool:
        """比赛不在直播列表时确认是否已结束（含加时 AET / 点球 PEN）。结束则推送并清理任务。"""
        mid = m["mid"]
        detail = await self._mcp("get_match", id=mid, h2h=0)
        if not detail:
            return False
        status = str(detail.get("status") or "")
        if status not in FINISH_STATUS and status not in ("ABD", "POST", "CANC"):
            return False  # 仍在进行（数据延迟）或未开赛，继续等
        # 正式结束 / 中断 / 延期 / 取消
        st = self._state.get(mid)
        league = self._league_cn(m.get("league", ""), m.get("league_key", ""))
        local, visitor = self._team_cn(m.get("local", "")), self._team_cn(m.get("visitor", ""))
        if status in FINISH_STATUS:
            if st and not st.get("pushed_finish") and self.config.get("push_finish", True):
                st["pushed_finish"] = True
                subs = [s for s in self._subs if s["name"] in m.get("subs", [])]
                if subs:
                    score = detail.get("scoretime") or st.get("score") or ""
                    zh = {"FT": "全场结束", "AET": "加时赛结束", "PEN": "点球大战结束"}.get(status, "比赛结束")
                    await self._push(
                        f"🏁 [{league}] {local} {score} {visitor} {zh}",
                        subs,
                    )
        elif st and not st.get("pushed_finish"):
            st["pushed_finish"] = True
            subs = [s for s in self._subs if s["name"] in m.get("subs", [])]
            if subs:
                zh = {"ABD": "比赛中断", "POST": "比赛延期", "CANC": "比赛取消"}.get(status, "比赛未进行")
                await self._push(f"⚠️ [{league}] {local} vs {visitor} {zh}", subs)
        m["finished"] = True
        if st:
            self._state.pop(mid, None)
        self._save_json(self.state_file, self._state)
        await self._remove_job_by_name(f"{JOB_POLL_PREFIX}{mid}")
        await self._remove_job_by_name(f"{JOB_REMIND_PREFIX}{mid}")
        return True

    async def _process_live_match(self, m: dict, subs: list[dict], league: str = "") -> None:
        mid = str(m["id"])
        score = str(m.get("scoretime") or "")
        status = str(m.get("status") or "")
        is_live = status.isdigit()  # 数字 = 比赛进行中（分钟数）
        league = self._league_cn(league or m.get("leaguename") or "", m.get("leagueKey") or "")
        local, visitor = self._team_cn(m.get("localteam", "")), self._team_cn(m.get("visitorteam", ""))
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
        logger.info("daily_schedule: 开始拉取今日赛程")
        data = await self._refresh_today_cache()
        if data is None:
            logger.info("daily_schedule: 未获取到赛程数据（MCP 无返回）")
            return
        matches = self._state.get("today", {}).get("matches", [])
        if not matches:
            await self._push("📅 今日订阅球队暂无比赛", self._subs)
            return
        lines = []
        today_key = datetime.now().strftime("%Y-%m-%d")
        for m in matches:
            league = self._league_cn(m["league"], m.get("league_key", ""))
            local, visitor = self._team_cn(m["local"]), self._team_cn(m["visitor"])
            if m.get("finished"):
                lines.append(f"· [{league}] {local} vs {visitor}（已结束）")
            elif m.get("kickoff"):
                ko = self._kickoff_local(m, today_key)
                t = ko.strftime("%H:%M") if ko else m["kickoff"]
                lines.append(f"· {t} [{league}] {local} vs {visitor}")
            elif m.get("status"):
                lines.append(f"· [{league}] {local} vs {visitor}（{STATUS_ZH.get(m['status'], m['status'])}）")
        if lines:
            await self._push("📅 今日关注赛程：\n" + "\n".join(lines[:25]), self._subs)
        # 动态调度：每场未开赛比赛 → 一次性开赛提醒；已开赛 → 轮询任务
        await self._schedule_today_tasks()

    def _tz_offset_minutes(self) -> int:
        """时区修正（分钟）：MCP 返回 UTC 时间，按配置的小时数偏移到本地。"""
        try:
            v = self.config.get("tz_offset")
            if v is None:
                v = 8
            return int(v) * 60
        except Exception:
            return 8 * 60

    def _kickoff_local(self, m: dict, today_key: str):
        """解析开球时间（本地）：MCP 的 status HH:MM 为 UTC，按配置时区偏移。返回 datetime 或 None。"""
        ko = m.get("kickoff") or ""
        if not ko:
            return None
        try:
            kickoff = datetime.strptime(f"{today_key} {ko}", "%Y-%m-%d %H:%M")
            kickoff += timedelta(minutes=self._tz_offset_minutes())
            return kickoff
        except Exception:
            return None

    def _collect_today_matches(self, data: list) -> list[dict]:
        """从 get_day_fixtures 结果中提取订阅球队的比赛（含命中球队名，供精确推送）。"""
        out = []
        for country in data:
            for lg in country.get("leagues", []):
                for m in lg.get("matches", []):
                    subs = self._watched_subs(m, lg)
                    if not subs:
                        continue
                    status = str(m.get("status") or "")
                    out.append({
                        "mid": str(m.get("id", "")),
                        "league": lg.get("leaguename") or m.get("leaguename") or "",
                        "league_key": m.get("leagueKey") or lg.get("key") or "",
                        "local": m.get("localteam", ""),
                        "visitor": m.get("visitorteam", ""),
                        "kickoff": status if re.fullmatch(r"\d{1,2}:\d{2}", status) else "",
                        "status": status,
                        "subs": [s["name"] for s in subs],
                    })
        return out

    async def _refresh_today_cache(self) -> list | None:
        """拉取今日赛程并缓存到 state["today"]，返回原始数据（失败返回 None）。"""
        today = datetime.now().strftime("%d/%m/%Y")
        data = await self._mcp(
            "get_day_fixtures", date=today, language="en", tzoffset=self._tz_offset_minutes()
        )
        if not data:
            return None
        self._state["today"] = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "matches": self._collect_today_matches(data),
        }
        self._save_json(self.state_file, self._state)
        return data

    # ---- 开赛提醒（一次性任务，按比赛触发） ----
    async def remind_check(self, mid: str = None) -> None:
        mins = int(self.config.get("remind_minutes", 0) or 0)
        if mins <= 0 or not self._subs or not mid:
            return
        now = datetime.now()
        today_key = now.strftime("%Y-%m-%d")
        cached = self._state.get("today") or {}
        if cached.get("date") != today_key:
            # 缓存过期/缺失（如插件重载后）：补拉一次今日赛程
            await self._refresh_today_cache()
            cached = self._state.get("today") or {}
        m = next((x for x in cached.get("matches", []) if x.get("mid") == mid), None)
        if not m or m.get("finished") or m.get("remind_done"):
            return
        kickoff = self._kickoff_local(m, today_key)
        if kickoff is None:
            return
        delta = (kickoff - now).total_seconds() / 60
        if not (0 < delta <= mins + 2):
            # 任务延迟到开球后：不再提醒，直接进入轮询阶段
            await self._add_poll_job(m)
            return
        m["remind_done"] = True
        self._save_json(self.state_file, self._state)
        subs = [s for s in self._subs if s["name"] in m.get("subs", [])]
        league = self._league_cn(m["league"], m.get("league_key", ""))
        local, visitor = self._team_cn(m["local"]), self._team_cn(m["visitor"])
        await self._push(
            f"⏰ [{league}] {local} vs {visitor} "
            f"{kickoff.strftime('%H:%M')} 开球，约 {int(delta)} 分钟后！",
            subs,
        )
        # 提醒后为该比赛注册轮询任务（开赛前 5 分钟开始生效）
        await self._add_poll_job(m)

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
                yield event.plain_result("用法：/球 订阅 <球队名>，如：/球 订阅 曼城")
                return
            yield event.plain_result(self._add_sub("team", rest, session))
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
        if stype == "league":
            stype = "team"  # 兼容旧参数，统一按球队处理
        if stype != "team":
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
        for s in self._subs:
            if s["type"] == stype and s["name"] == name:
                if session not in s["sessions"]:
                    s["sessions"].append(session)
                    self._save_json(self.subs_file, self._subs)
                return f"已关注「{name}」（本会话已加入推送）"
        self._subs.append({"type": stype, "name": name, "sessions": [session]})
        self._save_json(self.subs_file, self._subs)
        return f"✅ 已关注「{name}」球队，有比赛时将推送至此会话"

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
            tag = "球队"
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
                    league = self._league_cn(
                        lg.get("leaguename") or m.get("leaguename") or "",
                        m.get("leagueKey") or lg.get("key") or "",
                    )
                    local, visitor = self._team_cn(m["localteam"]), self._team_cn(m["visitorteam"])
                    lines.append(
                        f"{mark} [{league}] {local} {m.get('scoretime','')} {visitor}（{status}）"
                    )
        if not lines:
            return "当前没有进行中的比赛"
        return "🔴 进行中比赛（★=已订阅）：\n" + "\n".join(lines[:25])

    async def _query_today(self) -> str:
        today = datetime.now().strftime("%d/%m/%Y")
        data = await self._mcp(
            "get_day_fixtures", date=today, language="en", tzoffset=self._tz_offset_minutes()
        )
        if not data:
            return "无法获取今日赛程"
        lines = []
        tz_min = self._tz_offset_minutes()
        for country in data:
            for lg in country.get("leagues", []):
                for m in lg.get("matches", []):
                    subs = self._watched_subs(m, lg)
                    if not subs:
                        continue
                    mark = "★"
                    status = str(m.get("status") or "")
                    league = self._league_cn(
                        lg.get("leaguename") or m.get("leaguename") or "",
                        m.get("leagueKey") or lg.get("key") or "",
                    )
                    local, visitor = self._team_cn(m["localteam"]), self._team_cn(m["visitorteam"])
                    if re.fullmatch(r"\d{1,2}:\d{2}", status):
                        # MCP 时间按 UTC，修正到本地显示
                        t = datetime.strptime(f"{today} {status}", "%d/%m/%Y %H:%M") + timedelta(minutes=tz_min)
                        lines.append(f"{mark} {t.strftime('%H:%M')} [{league}] {local} vs {visitor}")
                    elif status:
                        lines.append(f"{mark} [{league}] {local} {m.get('scoretime','')} {visitor}（{STATUS_ZH.get(status, status)}）")
        if not lines:
            return "今日没有订阅相关的比赛"
        return "📅 今日关注赛程（★=已订阅）：\n" + "\n".join(lines[:25])

    @staticmethod
    def _usage() -> str:
        return (
            "⚽ 足球比分推送（基于 Live-Score MCP）\n"
            "/球 关注 <球队> —— 关注球队，如：/球 关注 曼城（每日 8 点推送赛程，赛前 15 分钟提醒）\n"
            "/球 订阅 <球队> —— 同上（兼容写法）\n"
            "/球 取消 <关键词> —— 取消关注\n"
            "/球 列表 —— 查看关注\n"
            "/球 现在 —— 查看进行中的关注比赛\n"
            "/球 今日 —— 查看今日关注赛程\n"
            "/球 帮助 —— 本帮助\n"
            "推送功能：进球 ⚽ / 开赛 🔔 / 完场 🏁 / 红牌 🟥 / 每日赛程 📅 / 赛前提醒 ⏰"
        )
