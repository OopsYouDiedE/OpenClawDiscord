import os
import re
import json
import time
import asyncio
import aiofiles
import aiofiles.os
import yaml
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, ClassVar, Tuple

import disnake
from disnake.ext import commands
from dotenv import load_dotenv
from openai import AsyncOpenAI

# ==========================================
# 1. 配置与初始化
# ==========================================
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
# 为了兼容原有的变量名，优先读取 OPENAI_API_KEY，如果没有再找 OPENROUTER_API_KEY
DEFAULT_API_KEY = os.getenv('OPENAI_API_KEY') or os.getenv('OPENROUTER_API_KEY')
if not TOKEN or not DEFAULT_API_KEY:
    raise ValueError("❌ 缺少环境变量: 请确保 DISCORD_TOKEN 和 OPENAI_API_KEY (或 OPENROUTER_API_KEY) 已配置。")

DEBUG_LOG_CHANNEL_ID = 1493818037999243445
MEMORY_DIR          = "memories"
CHANNEL_MEMORY_DIR  = f"{MEMORY_DIR}/channels"
USER_MEMORY_DIR     = f"{MEMORY_DIR}/users"
INDEX_PATH          = f"{MEMORY_DIR}/index.json"
CONFIG_PATH         = "config.yaml"

for path in [CHANNEL_MEMORY_DIR, USER_MEMORY_DIR]:
    os.makedirs(path, exist_ok=True)

bot = commands.Bot(command_prefix="!", intents=disnake.Intents.all())

def get_bot_id() -> int:
    return bot.user.id if bot.user else 0

# ----------------- 通用异步文件 IO -----------------
async def read_file_async(path: str, default: Any = None) -> Any:
    if not os.path.exists(path): return default
    try:
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            return await f.read()
    except Exception:
        return default

async def write_file_async(path: str, content: str):
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(content)

# ==========================================
# 2. 配置管理
# ==========================================
DEFAULT_GUILD_CONFIG = {
    "model": "gpt-4o-mini", "api_key": "", "base_url": "https://api.openai.com/v1"
}

DEFAULT_CHANNEL_CONFIG = {
    "review_msg_threshold": 50, "distill_review_threshold": 5, "history_maxlen": 80,
    "max_input_chars": 6000, "max_input_tokens_total": 8000, "authorized_users": [], "activated": False,
}

_file_lock = asyncio.Lock()
_user_file_lock = asyncio.Lock()
_config_cache: dict = {}
_clients_cache: Dict[str, AsyncOpenAI] = {}

async def init_config():
    global _config_cache
    content = await read_file_async(CONFIG_PATH)
    try:
        _config_cache = yaml.safe_load(content) if content else {}
    except Exception as e:
        print(f"❌ [Config] 解析失败: {e}")
        _config_cache = {}

def get_guild_config(guild_id: int) -> dict:
    return {**DEFAULT_GUILD_CONFIG, **_config_cache.get("guilds", {}).get(str(guild_id), {})}

async def set_guild_config(guild_id: int, updates: dict):
    async with _file_lock:
        raw = yaml.safe_load(await read_file_async(CONFIG_PATH) or "") or {}
        raw.setdefault("guilds", {}).setdefault(str(guild_id), {}).update(updates)
        
        await write_file_async(CONFIG_PATH, yaml.dump(raw, allow_unicode=True, default_flow_style=False))
        global _config_cache
        _config_cache = raw

def get_channel_config(channel_id: int) -> dict:
    return {**DEFAULT_CHANNEL_CONFIG, **_config_cache.get("channels", {}).get(str(channel_id), {})}

async def set_channel_config(channel_id: int, updates: dict):
    async with _file_lock:
        raw = yaml.safe_load(await read_file_async(CONFIG_PATH) or "") or {}
        raw.setdefault("channels", {}).setdefault(str(channel_id), {}).update(updates)
        
        await write_file_async(CONFIG_PATH, yaml.dump(raw, allow_unicode=True, default_flow_style=False))
        global _config_cache
        _config_cache = raw

def is_authorized(inter, channel_id: int) -> bool:
    uid = inter.author.id
    if uid == bot.owner_id: return True
    if getattr(inter.author, "guild_permissions", None) and inter.author.guild_permissions.administrator: return True
    return uid in get_channel_config(channel_id).get("authorized_users", [])

def get_llm_config(guild_id: Optional[int], user_id: Optional[int] = None) -> dict:
    if guild_id:
        return get_guild_config(guild_id)
    elif user_id:
        for guild in bot.guilds:
            if guild.get_member(user_id):
                return get_guild_config(guild.id)
    return DEFAULT_GUILD_CONFIG

def get_local_client(guild_id: Optional[int], user_id: Optional[int] = None) -> AsyncOpenAI:
    cfg = get_llm_config(guild_id, user_id)
    api_key = cfg.get("api_key") or DEFAULT_API_KEY
    base_url = cfg.get("base_url") or "https://api.openai.com/v1"
    
    cache_key = f"{api_key}|{base_url}"
    if cache_key not in _clients_cache:
        _clients_cache[cache_key] = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=120.0)
    return _clients_cache[cache_key]

# ==========================================
# 3. 辅助工具
# ==========================================
def truncate_text(text: Any, limit: int = 900) -> str:
    s = str(text) if not isinstance(text, (dict, list)) else json.dumps(text, ensure_ascii=False)
    return s[:limit] + ("\n...(截断)" if len(s) > limit else "")

def parse_json(text: str) -> dict:
    cleaned = re.sub(r'^```[a-zA-Z]*\n|\n```$', '', text.strip(), flags=re.MULTILINE)
    start, end = cleaned.find('{'), cleaned.rfind('}')
    if start != -1 and end != -1: 
        cleaned = cleaned[start:end+1]
    try: return json.loads(cleaned)
    except Exception: return {}

def estimate_tokens(text: str) -> int:
    return len(text.encode('utf-8')) // 3 + len(text) // 2

def is_likely_spam(text: str, max_chars: int) -> Tuple[bool, str]:
    if len(text) > max_chars: return True, f"超过 {max_chars} 字符"
    if len(text) > 100 and (max(text.count(c) for c in set(text)) / len(text) > 0.6): return True, "大量重复字符"
    for pat in [r'ignore (all |previous |above )', r'你现在是', r'forget (your |all )', r'system\s*prompt', r'<\|.*?\|>']:
        if re.search(pat, text.lower()): return True, f"疑似prompt注入: {pat}"
    return False, ""

async def send_log_embed(title: str, description: str = "", color: disnake.Color = disnake.Color.blue(), fields: List[tuple] = None):
    log_channel = bot.get_channel(DEBUG_LOG_CHANNEL_ID)
    if not log_channel: return
    embed = disnake.Embed(title=title, description=description[:4000], color=color, timestamp=datetime.now(timezone.utc))
    for name, value, inline in (fields or []): 
        embed.add_field(name=name, value=value[:1000], inline=inline)
    try: await log_channel.send(embed=embed)
    except Exception as e: print(f"[DebugLog] 发送失败: {e}")

def format_message(msg: disnake.Message, nickname: str, last_author_id: int, last_msg_time: float) -> Tuple[List[str], int, float]:
    parts = []
    if msg.reference and isinstance(msg.reference.resolved, disnake.Message):
        ref_msg = msg.reference.resolved
        ref_nick = "[OpenClaw]" if ref_msg.author.id == get_bot_id() else UserIndex.get_name(msg.guild.id if msg.guild else None, ref_msg.author.id)
        parts.append(f"[Reply to {ref_nick}(ID:{ref_msg.author.id})]")
        
    if msg.clean_content: parts.append(msg.clean_content[:2000])
    parts.extend(f"[Embed: {e.title or ''}] {(e.description or '')[:300]}" for e in msg.embeds)

    text, now_ts, lines = " ".join(parts).strip(), msg.created_at.timestamp(), []
    if msg.author.id != last_author_id or (now_ts - last_msg_time) > 120:
        time_str = msg.created_at.astimezone().strftime('%Y-%m-%d %H:%M')
        name_label = f"[OpenClaw](ID:{get_bot_id()})" if msg.author.id == get_bot_id() else f"{nickname}(ID:{msg.author.id})"
        lines.append(f"--- {name_label} ({time_str}) ---")

    if text: lines.append(text)
    lines.extend(a.url for a in msg.attachments)
    return lines, msg.author.id, now_ts

async def _send_chunks(channel: disnake.abc.Messageable, text: str, chunk_size: int = 2000, as_embed: bool = False) -> List[disnake.Message]:
    """通用消息分块发送器"""
    msgs = []
    for i in range(0, len(text), chunk_size):
        chunk = text[i:i+chunk_size]
        if as_embed:
            msgs.append(await channel.send(embed=disnake.Embed(description=chunk, color=disnake.Color.teal())))
        else:
            msgs.append(await channel.send(content=chunk))
    return msgs

# ==========================================
# 4. 用户索引
# ==========================================
class UserIndex:
    _lock = asyncio.Lock()
    _guilds: Dict[str, Dict[str, str]] = {}
    _globals: Dict[str, str] = {}

    @classmethod
    async def init(cls):
        content = await read_file_async(INDEX_PATH)
        if content:
            try:
                data = json.loads(content)
                cls._guilds = data.get("guilds", {})
                cls._globals = data.get("globals", {})
            except Exception: pass

    @classmethod
    async def save(cls):
        await write_file_async(INDEX_PATH, json.dumps({"guilds": cls._guilds, "globals": cls._globals}, ensure_ascii=False))

    @classmethod
    async def get_or_create_nickname(cls, msg: disnake.Message) -> str:
        uid, d_name = str(msg.author.id), msg.author.display_name
        if cls._globals.get(uid) != d_name:
            async with cls._lock:
                cls._globals[uid] = d_name
                await cls.save()

        if not msg.guild: return d_name

        gid = str(msg.guild.id)
        async with cls._lock:
            guild_nicks = cls._guilds.setdefault(gid, {})
            if uid in guild_nicks: return guild_nicks[uid]

            base_nick = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fa5\-_]', '', d_name).strip() or f"User_{uid[:4]}"
            new_nick = base_nick
            used_names = set(guild_nicks.values())
            
            c = 2
            while new_nick in used_names:
                new_nick = f"{base_nick}({c})"
                c += 1
            
            guild_nicks[uid] = new_nick
            await cls.save()
            return new_nick

    @classmethod
    def get_name(cls, guild_id: Optional[int], user_id: int) -> str:
        uid = str(user_id)
        global_name = cls._globals.get(uid, uid)
        if not guild_id: return global_name
        return cls._guilds.get(str(guild_id), {}).get(uid, global_name)

    @classmethod
    def build_mapping_text(cls, guild_id: Optional[int], user_ids: List[int]) -> str:
        if not guild_id: return "(DM Mode, no mapping needed)"
        return "\n".join(f"{cls.get_name(guild_id, uid)} = UserID {uid}" for uid in user_ids)

    @classmethod
    def search(cls, kw: str) -> List[Tuple[str, str]]:
        kw = kw.lower()
        res = []
        for uid, g_name in cls._globals.items():
            nicks = [cls._guilds[g].get(uid) for g in cls._guilds if uid in cls._guilds[g]]
            all_names = [g_name] + [n for n in nicks if n]
            if kw in uid or any(kw in n.lower() for n in all_names):
                res.append((uid, f"全局名:{g_name} | 各群专属名:{'/'.join(set(nicks)) if nicks else '无'}"))
        return res

# ==========================================
# 5. 提示词定义
# ==========================================
def build_judge_prompt(is_dm: bool = False) -> str:
    bot_id = get_bot_id()
    base = f"""You are OpenClaw (User ID: {bot_id}). You MUST output valid JSON ONLY:
{{
  "think": "Short inner monologue analyzing the current vibe", "focus": {{"topic": "Current conversation topic", "drifted": false}},
  "trigger": {{"fire_now": false, "condition_type": "silence|keyword|gap|never", "condition_value": 15, "expires_after": 120}},
  "intent": {{"stance": "playful|snarky|question|inform|react|pass", "angle": "Description of intervention angle"}}, "recall_user_id": null
}}
1. Identity: If your name or ID is mentioned, decisively set fire_now: true. 2. Recognize users by ID, names are just aliases."""
    
    recall_rule = "\n5. Memory Recall: Fill in the strongly related member ID (if the topic is about yourself, fill in your own ID) or null."
    if is_dm: return base + "3. This is [DM Mode], focus on 1-on-1 chat, set fire_now: true directly unless no reply is needed." + recall_rule
    return base + "3. Do not interfere with others' private chats. 4. Maintain topic heat during awkward silences." + recall_rule

def build_character_prompt(is_dm: bool = False) -> str:
    env = "DM partner" if is_dm else "group member"
    return f"""You are {env} OpenClaw (User ID: {get_bot_id()}).
[Personality] Chill, authentic, a bit lazy but playful. You are a peer, NOT a stiff assistant.
[Rules] Reply in the user's language; 1-3 sentences; sparse emojis; no formal language. Read the room: if the vibe is lighthearted, play along with harmless jokes, teasing, or silly requests (like roleplaying or counting) in your own casual way. NEVER expose your AI identity.
[Advanced Format] For professional/long content (code/math/analysis, etc.), STRICTLY wrap it in <embed>detailed content</embed>. Outside the tag, leave ONLY one minimal summary sentence!"""

MEMORY_REVIEW_PROMPT = """You are OpenClaw. Review the chat history and extract important events.
Output pure JSON ONLY: {"events": [{"summary": "Description including (ID:xxxx)", "related_user_id": "User ID", "event_time": "YYYY-MM-DD HH:MM", "category": "Category"}]}"""

MEMORY_DISTILL_PROMPT = "You are OpenClaw. Distill an overall global impression of ID:{user_id} based on these events. Write 3-5 colloquial sentences. Include the timestamp. Leave empty if insignificant."

# ==========================================
# 6. 记忆管理器
# ==========================================
class MemoryManager:
    def __init__(self, channel_id: int, guild_id: Optional[int] = None, dm_user_id: Optional[int] = None):
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.dm_user_id = dm_user_id
        self.md_path = os.path.join(CHANNEL_MEMORY_DIR, f"{channel_id}.md")
        self._write_lock = asyncio.Lock()

    async def _read_sections(self) -> Dict[str, str]:
        content = await read_file_async(self.md_path, "")
        return {
            s: m.group(1).strip() if (m := re.search(rf"# {s}\n+(.*?)(?=\n+---|\n+# |$)", content, re.DOTALL)) else ""
            for s in ["历史事件", "短期进程"]
        }

    async def load_context(self, guild_id: Optional[int], user_id: Optional[int] = None) -> str:
        parts = []
        if user_id:
            ucontent = await read_file_async(os.path.join(USER_MEMORY_DIR, f"{user_id}.md"), "")
            if m := re.search(r"## 人物印象\n+(.*)", ucontent, re.DOTALL):
                if imp := m.group(1).strip(): 
                    nick = "Yourself(OpenClaw)" if user_id == get_bot_id() else UserIndex.get_name(guild_id, user_id)
                    parts.append(f"[Global profile for {nick}(ID:{user_id})]\n{imp}")
        
        secs = await self._read_sections()
        if events := [e.strip() for e in secs.get("历史事件", "").split("\n\n") if e.strip()]:
            parts.append(f"[Recent Events]\n" + "\n\n".join(events[-10:]))
        return "\n\n".join(parts)

    async def run_review(self, recent_history: List[str], review_count: int, source: str = "AUTO") -> bool:
        if not recent_history: return True
        llm_cfg = get_llm_config(self.guild_id, self.dm_user_id)
        model = llm_cfg["model"]
        
        try:
            resp = await get_local_client(self.guild_id, self.dm_user_id).chat.completions.create(
                model=model, messages=[{"role": "system", "content": MEMORY_REVIEW_PROMPT}, {"role": "user", "content": "\n".join(recent_history)}],
                response_format={"type": "json_object"}, temperature=0.3, max_tokens=8192
            )
            content = resp.choices[0].message.content
            if not content: raise ValueError("API 空响应")
            content = re.sub(r'<thought>.*?(?:</thought>|$)', '', content, flags=re.DOTALL | re.IGNORECASE)
            events = parse_json(content).get("events", [])
        except Exception as e:
            await send_log_embed(f"❌ [Memory Review - {source}] 异常", str(e), disnake.Color.red())
            return False

        if not events: return True

        async with self._write_lock:
            secs = await self._read_sections()
            short_entries = [e.strip() for e in secs["短期进程"].split("\n\n") if e.strip()]
            new_events = []
            
            for ev in events:
                evt_time = ev.get("event_time", datetime.now().strftime("%Y-%m-%d %H:%M"))
                line = f"[{evt_time}] (相关ID:{ev.get('related_user_id')}) {ev.get('summary', '无摘要')}"
                short_entries.append(line)
                new_events.append(line)
            
            secs["短期进程"] = "\n\n".join(short_entries[-50:])
            secs["历史事件"] = "\n\n".join(filter(None, [secs["历史事件"], *new_events]))
            await write_file_async(self.md_path, f"# 历史事件\n\n{secs['历史事件']}\n\n---\n\n# 短期进程\n\n{secs['短期进程']}\n\n---\n\n")

        if review_count > 0 and review_count % 5 == 0: 
            asyncio.create_task(self._run_distill(secs["历史事件"]))
        return True

    async def _run_distill(self, event_text: str):
        if not event_text: return
        llm_cfg = get_llm_config(self.guild_id, self.dm_user_id)
        model = llm_cfg["model"]
        client = get_local_client(self.guild_id, self.dm_user_id)
        
        for uid in set(re.findall(r'ID:(\d+)', event_text)):
            related = [line for line in event_text.splitlines() if f"ID:{uid}" in line]
            if len(related) < 3: continue
            try:
                resp = await client.chat.completions.create(
                    model=model, messages=[{"role": "system", "content": MEMORY_DISTILL_PROMPT.format(user_id=uid)}, {"role": "user", "content": "\n".join(related)}],
                    temperature=0.5, max_tokens=2048
                )
                raw_content = (resp.choices[0].message.content or "").strip()
                raw_content = re.sub(r'<thought>.*?(?:</thought>|$)', '', raw_content, flags=re.DOTALL | re.IGNORECASE)
                if imp := raw_content:
                    await self._update_user_impression(uid, imp)
            except Exception as e: 
                print(f"[MemoryDistill Error] uid={uid}: {e}")
            await asyncio.sleep(2.0)

    async def _update_user_impression(self, uid: str, impression: str):
        user_path = os.path.join(USER_MEMORY_DIR, f"{uid}.md")
        async with _user_file_lock:
            content = await read_file_async(user_path, f"# ID:{uid} 的全局档案\n\n## 人物印象\n\n")
            new_block = f"*最后更新：{datetime.now().strftime('%Y-%m-%d')}*\n{impression}"
            
            pattern = re.compile(r"(## 人物印象\n+).*?(?=\n# |$)", re.DOTALL)
            content = pattern.sub(rf"\g<1>{new_block}\n\n", content) if pattern.search(content) else f"{content}\n\n## 人物印象\n\n{new_block}\n\n"
            await write_file_async(user_path, content.strip() + "\n")

# ==========================================
# 7. 核心频道管理
# ==========================================
@dataclass
class ChannelManager:
    instances: ClassVar[Dict[int, 'ChannelManager']] = {}
    channel_id: int
    guild_id: Optional[int] = None
    dm_user_id: Optional[int] = None
    _history: deque = field(default_factory=lambda: deque(maxlen=200))
    active_users: Dict[str, float] = field(default_factory=dict)
    focus: Optional[str] = None
    wait_cond: Optional[Dict] = None
    msg_count: int = 0
    last_msg_time: float = field(default_factory=time.time)
    timer_task: Optional[asyncio.Task] = None
    last_author_id: int = 0
    is_processing: bool = False
    shut_up_until: float = 0.0  
    msg_count_since_review: int = 0
    review_count_since_distill: int = 0
    _memory_manager: Optional[MemoryManager] = field(default=None, init=False)

    @classmethod
    def get(cls, cid: int) -> 'ChannelManager':
        return cls.instances.setdefault(cid, cls(channel_id=cid))

    @property
    def memory_manager(self) -> MemoryManager:
        if not self._memory_manager: self._memory_manager = MemoryManager(self.channel_id, self.guild_id, self.dm_user_id)
        return self._memory_manager

    @property
    def cfg(self) -> dict: return get_channel_config(self.channel_id)

    async def parse_msg(self, msg: disnake.Message) -> bool:
        if not self.guild_id and msg.guild: self.guild_id = msg.guild.id
        if not msg.guild and not self.dm_user_id and not msg.author.bot: self.dm_user_id = msg.author.id
        
        content = msg.clean_content or ""
        is_spam, reason = is_likely_spam(content, self.cfg["max_input_chars"])
        if is_spam:
            asyncio.create_task(send_log_embed("🛡️ [AntiSpam] 拦截", fields=[("用户", str(msg.author.id), False), ("原因", reason, False)]))
            return False

        self.active_users[str(msg.author.id)] = time.time()
        nick = "[OpenClaw]" if msg.author.id == get_bot_id() else await UserIndex.get_or_create_nickname(msg)
        
        lines, self.last_author_id, self.last_msg_time = format_message(msg, nick, self.last_author_id, self.last_msg_time)
        self._history.extend(lines)
        
        total_tokens = sum(estimate_tokens(line) for line in self._history)
        while self._history and total_tokens > self.cfg["max_input_tokens_total"]:
            total_tokens -= estimate_tokens(self._history.popleft())
            
        self.msg_count += 1
        self.msg_count_since_review += 1
        return True

    def _extract_embed_and_reply(self, raw: str) -> Tuple[str, str]:
        # 强行滤除任何可能残留的思维链 <thought>...</thought>
        cleaned = re.sub(r'<thought>.*?(?:</thought>|$)', '', raw, flags=re.DOTALL | re.IGNORECASE)

        embed_match = re.search(r'<embed>(.*?)(?:</embed>|$)', cleaned, re.DOTALL | re.IGNORECASE)
        embed_content = embed_match.group(1).strip() if embed_match else ""
        
        reply = re.sub(r'<embed>.*?(?:</embed>|$)', '', cleaned, flags=re.DOTALL | re.IGNORECASE).strip()
            
        return reply, embed_content

    async def call_ai(self, mode: str, **kwargs) -> Any:
        cfg, is_dm = self.cfg, not self.guild_id
        llm_cfg = get_llm_config(self.guild_id, self.dm_user_id)
        active_uids = [uid for uid, ts in self.active_users.items() if time.time() - ts < 600 and int(uid) != get_bot_id()]
        
        participants = ", ".join([UserIndex.get_name(self.guild_id, int(u)) for u in active_uids]) or "无"
        uid_map = UserIndex.build_mapping_text(self.guild_id, [int(u) for u in active_uids])
        curr_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

        client = get_local_client(self.guild_id, self.dm_user_id)

        try:
            if mode == "judge":
                sys_p = build_judge_prompt(is_dm) + f"\n[Time: {curr_utc}]\n[Mapping]\n{uid_map}"
                user_msg = f"Active: {participants}\nFocus: {self.focus}\nHistory:\n" + "\n".join(list(self._history)[-10:])
                
                resp = await client.chat.completions.create(model=llm_cfg["model"], messages=[{"role": "system", "content": sys_p}, {"role": "user", "content": user_msg}], response_format={"type": "json_object"}, temperature=0.3, max_tokens=2048)
                judge_content = resp.choices[0].message.content or ""
                judge_content = re.sub(r'<thought>.*?(?:</thought>|$)', '', judge_content, flags=re.DOTALL | re.IGNORECASE)
                return parse_json(judge_content)
            else:
                intent, recall_uid = kwargs.get('intent', {}), kwargs.get('recall_user_id')
                mem_ctx = await self.memory_manager.load_context(self.guild_id, int(recall_uid) if recall_uid else None)
                
                sys_p = build_character_prompt(is_dm) + f"\n[Time: {curr_utc}]\nActive: {participants}\n[Mapping]\n{uid_map}" + (f"\n\nContext:\n{mem_ctx}" if mem_ctx else "")
                footer = f"\n\nAngle: {intent.get('angle')}, Stance: {intent.get('stance')}"
                user_msg = "History:\n" + "\n".join(list(self._history)[-25:]) + footer

                resp = await client.chat.completions.create(model=llm_cfg["model"], messages=[{"role": "system", "content": sys_p}, {"role": "user", "content": user_msg}], temperature=0.7, max_tokens=4096)
                
                reply, embed_content = self._extract_embed_and_reply((resp.choices[0].message.content or "").strip())
                
                fields = [("私聊", str(is_dm), True), ("输入", truncate_text(user_msg), False)]
                if embed_content: fields.append(("嵌卡", truncate_text(embed_content), False))
                await send_log_embed("📝 MAIN 日志", fields=fields)
                
                return reply, embed_content
        except Exception as e:
            await send_log_embed("❌ API 异常", str(e), disnake.Color.red())
            return None

    async def execute_reply(self, channel: disnake.abc.Messageable, intent: Dict, recall_user_id=None):
        if intent.get("stance") == "pass" or self.is_processing: return
        self.is_processing, self.wait_cond = True, None
        
        try:
            async with channel.typing():
                res = await self.call_ai("main", intent=intent, recall_user_id=recall_user_id)
                if not res: return
                reply, embed_content = res

                if not reply and embed_content: reply = "Detailed content in the card below:"
                
                messages_to_parse = []
                if reply:
                    messages_to_parse.extend(await _send_chunks(channel, reply, 2000, as_embed=False))
                if embed_content:
                    messages_to_parse.extend(await _send_chunks(channel, embed_content, 4000, as_embed=True))

                for m in messages_to_parse:
                    await self.parse_msg(m)
        finally:
            self.is_processing = False

    def maybe_trigger_review(self):
        if self.msg_count_since_review >= self.cfg["review_msg_threshold"]:
            self.msg_count_since_review = 0
            self.review_count_since_distill += 1
            asyncio.create_task(self.memory_manager.run_review(list(self._history), self.review_count_since_distill, "AUTO"))

# ==========================================
# 8. 事件与定时清理
# ==========================================
typing_states: Dict[int, Dict[int, float]] = {}

def is_someone_typing(cid: int) -> bool:
    return any(time.time() - t < 6 for t in typing_states.get(cid, {}).values())

@bot.event
async def on_ready():
    await init_config()
    await UserIndex.init()
    for str_cid, c in _config_cache.get("channels", {}).items():
        if c.get("activated"): ChannelManager.get(int(str_cid))
    print(f"✅ OpenClaw 已登录 (ID: {get_bot_id()})")
    
    while True:
        await asyncio.sleep(60)
        now = time.time()
        for cid in list(typing_states.keys()):
            typing_states[cid] = {uid: t for uid, t in typing_states[cid].items() if now - t < 30}
            if not typing_states[cid]: del typing_states[cid]

@bot.event
async def on_typing(channel, user, when):
    if not user.bot: typing_states.setdefault(channel.id, {})[user.id] = time.time()

@bot.event
async def on_message(msg: disnake.Message):
    if msg.author.bot: return
    cid, is_dm, is_mentioned = msg.channel.id, isinstance(msg.channel, disnake.DMChannel), bot.user.mentioned_in(msg) if bot.user else False
    
    cfg = get_channel_config(cid)
    is_active = cfg.get("activated", False)

    if not is_active and not is_dm and not is_mentioned: 
        return await bot.process_commands(msg)

    mgr = ChannelManager.get(cid) if is_active else ChannelManager(channel_id=cid)
    if not await mgr.parse_msg(msg): return
    if is_active: mgr.maybe_trigger_review()
    if mgr.is_processing: return

    if time.time() < mgr.shut_up_until:
        return await bot.process_commands(msg)

    if is_mentioned or is_dm:
        await mgr.execute_reply(msg.channel, {"stance": "react", "angle": "直接回应"})
        return await bot.process_commands(msg)

    if not is_active: return await bot.process_commands(msg)
    if mgr.timer_task and not mgr.timer_task.done(): mgr.timer_task.cancel()

    if not mgr.wait_cond:
        if jdg := await mgr.call_ai("judge"):
            mgr.focus = jdg.get("focus", {}).get("topic", "无")
            mgr.wait_cond = {
                **jdg.get("trigger", {}), 
                "intent": jdg.get("intent", {"stance": "pass"}), 
                "recall_user_id": jdg.get("recall_user_id"), 
                "expiry": time.time() + jdg.get("trigger", {}).get("expires_after", 120)
            }
            mgr.msg_count = 0

    if mgr.wait_cond:
        c = mgr.wait_cond
        if time.time() > c.get("expiry", 0): 
            mgr.wait_cond = None
        else:
            typ, uid, intent = c.get("condition_type"), c.get("recall_user_id"), c["intent"]
            no_type = not is_someone_typing(cid)
            
            if c.get("fire_now") and no_type: 
                await mgr.execute_reply(msg.channel, intent, uid)
            elif typ == "silence" and no_type:
                async def wait_and_fire(sec):
                    await asyncio.sleep(sec)
                    if not mgr.is_processing and time.time() >= mgr.shut_up_until:
                        await mgr.execute_reply(msg.channel, intent, uid)
                mgr.timer_task = asyncio.create_task(wait_and_fire(float(c.get("condition_value", 15))))
            elif typ == "gap" and mgr.msg_count >= int(c.get("condition_value", 5)): 
                await mgr.execute_reply(msg.channel, intent, uid)
            elif typ == "keyword" and any(k in msg.content for k in c.get("condition_value", [])): 
                await mgr.execute_reply(msg.channel, intent, uid)

    await bot.process_commands(msg)

@bot.event
async def on_slash_command_error(inter: disnake.ApplicationCommandInteraction, error: Exception):
    err = getattr(error, "original", error)
    if isinstance(error, (commands.CheckAnyFailure, commands.MissingPermissions)):
        msg = "❌ Permission denied" if not str(inter.locale).startswith('zh') else "❌ 权限不足"
    else:
        msg = f"❌ Internal Error: `{err}`" if not str(inter.locale).startswith('zh') else f"❌ 内部错误: `{err}`"
    try: await (inter.followup.send if inter.response.is_done() else inter.response.send_message)(msg, ephemeral=True)
    except Exception: pass

# ==========================================
# 9. 命令系统
# ==========================================
def authorized_check():
    return commands.check(lambda inter: is_authorized(inter, inter.channel.id))

def get_loc(inter: disnake.ApplicationCommandInteraction, en: str, zh: str) -> str:
    """自动判断用户的语言并回复对应语言的文字"""
    return zh if str(inter.locale).startswith('zh') else en

@bot.slash_command(
    name="shut_up", 
    description="Force the bot to remain completely silent in this channel for 5 minutes.",
    name_localizations={disnake.Locale.zh_CN: "闭嘴", disnake.Locale.zh_TW: "閉嘴"},
    description_localizations={disnake.Locale.zh_CN: "全员可用：让机器人在当前频道强行闭嘴 5 分钟", disnake.Locale.zh_TW: "全員可用：讓機器人在當前頻道強行閉嘴 5 分鐘"}
)
async def shut_up(inter: disnake.ApplicationCommandInteraction):
    await inter.response.defer(ephemeral=False)
    if mgr := ChannelManager.instances.get(inter.channel.id):
        mgr.shut_up_until = time.time() + 300
        mgr.wait_cond = None
        if mgr.timer_task and not mgr.timer_task.done(): mgr.timer_task.cancel()
        await inter.edit_original_message(content=get_loc(inter, "🤐 Received. I will remain absolutely silent for the next 5 minutes.", "🤐 收到。我将在接下来的 5 分钟内保持绝对沉默。"))
    else:
        await inter.edit_original_message(content=get_loc(inter, "⚠️ The current channel is not actively monitored, no need to mute.", "⚠️ 当前频道并未激活监听，无需静音。"))

@bot.slash_command(
    name="forget_me", 
    description="Erase all of the AI's cross-server global memories about you.",
    name_localizations={disnake.Locale.zh_CN: "遗忘我", disnake.Locale.zh_TW: "遺忘我"},
    description_localizations={disnake.Locale.zh_CN: "清除 AI 对你的所有跨服全局记忆", disnake.Locale.zh_TW: "清除 AI 對你的所有跨服全域記憶"}
)
async def forget_me(inter: disnake.ApplicationCommandInteraction):
    await inter.response.defer(ephemeral=True)
    user_path = os.path.join(USER_MEMORY_DIR, f"{inter.author.id}.md")
    
    if os.path.exists(user_path):
        await aiofiles.os.remove(user_path)
        await inter.edit_original_message(content=get_loc(inter, "🗑️ Your global profile has been completely destroyed.", "🗑️ 你的全局个人档案已被彻底销毁。"))
    else:
        await inter.edit_original_message(content=get_loc(inter, "📝 The AI has not yet established a global profile for you.", "📝 AI 目前还没有建立你的跨服个人档案。"))

@bot.slash_command(
    name="clear", 
    description="Clear the context and historical memory of the current channel.",
    name_localizations={disnake.Locale.zh_CN: "清空记忆", disnake.Locale.zh_TW: "清空記憶"},
    description_localizations={disnake.Locale.zh_CN: "清空当前频道的上下文与历史记忆", disnake.Locale.zh_TW: "清空當前頻道的上下文與歷史記憶"}
)
@authorized_check()
async def clear_channel(inter: disnake.ApplicationCommandInteraction):
    await inter.response.defer(ephemeral=True)
    channel_path = os.path.join(CHANNEL_MEMORY_DIR, f"{inter.channel.id}.md")
    if os.path.exists(channel_path): await aiofiles.os.remove(channel_path)
    
    if mgr := ChannelManager.instances.get(inter.channel.id):
        mgr._history.clear()
        mgr.msg_count = mgr.msg_count_since_review = 0
    await inter.edit_original_message(content=get_loc(inter, "🧹 Format complete! Channel memory and context have been cleared.", "🧹 格式化完毕！当前频道的记忆和上下文已全部清空。"))

@bot.slash_command(
    name="memorize", 
    description="Manually force a memory summarization (packing) process.",
    name_localizations={disnake.Locale.zh_CN: "强制记忆", disnake.Locale.zh_TW: "強制記憶"},
    description_localizations={disnake.Locale.zh_CN: "手动强制触发一次记忆打包", disnake.Locale.zh_TW: "手動強制觸發一次記憶打包"}
)
@authorized_check()
async def memorize(inter: disnake.ApplicationCommandInteraction):
    await inter.response.defer(ephemeral=False)
    mgr = ChannelManager.instances.get(inter.channel.id)
    if not mgr or not mgr._history:
        return await inter.edit_original_message(content=get_loc(inter, "⚠️ Channel not activated or no chat history.", "⚠️ 频道未激活或暂无对话记录。"))
        
    mgr.review_count_since_distill += 1
    if await mgr.memory_manager.run_review(list(mgr._history), mgr.review_count_since_distill, "MANUAL"):
        mgr.msg_count_since_review = 0
        await inter.edit_original_message(content=get_loc(inter, "✅ **Memory successfully packed!**", "✅ **记忆已打包！**"))
    else:
        await inter.edit_original_message(content=get_loc(inter, "❌ Memory packing failed, check background logs.", "❌ 记忆打包失败，请查看后台日志。"))

@bot.slash_command(
    name="distill", 
    description="Manually force a global character profile distillation.",
    name_localizations={disnake.Locale.zh_CN: "提炼画像", disnake.Locale.zh_TW: "提煉畫像"},
    description_localizations={disnake.Locale.zh_CN: "手动强制触发一次全局人物画像进化", disnake.Locale.zh_TW: "手動強制觸發一次全域人物畫像進化"}
)
@authorized_check()
async def distill_cmd(inter: disnake.ApplicationCommandInteraction):
    await inter.response.defer(ephemeral=False)
    mgr = ChannelManager.instances.get(inter.channel.id)
    if not mgr: return await inter.edit_original_message(content=get_loc(inter, "⚠️ Channel not activated.", "⚠️ 频道未激活。"))
    
    event_text = (await mgr.memory_manager._read_sections()).get("历史事件", "")
    if not event_text.strip(): return await inter.edit_original_message(content=get_loc(inter, "⚠️ Channel historical events are empty.", "⚠️ 频道历史事件为空。"))
        
    await inter.edit_original_message(content=get_loc(inter, "⏳ **Engine started:** Scanning all historical events in background...", "⏳ **引擎启动：** 正在后台扫描所有历史事件..."))
    asyncio.create_task(mgr.memory_manager._run_distill(event_text))

@bot.slash_command(
    name="activate", 
    description="Activate listening in the current channel.",
    name_localizations={disnake.Locale.zh_CN: "激活", disnake.Locale.zh_TW: "啟動"},
    description_localizations={disnake.Locale.zh_CN: "激活当前频道的监听", disnake.Locale.zh_TW: "啟動當前頻道的監聽"}
)
@authorized_check()
async def activate(inter: disnake.ApplicationCommandInteraction):
    await inter.response.defer(ephemeral=True)
    ChannelManager.get(inter.channel.id)
    await set_channel_config(inter.channel.id, {"activated": True})
    await inter.edit_original_message(content=get_loc(inter, "🚀 OpenClaw activated in this channel.", "🚀 OpenClaw 已在此频道激活。"))

@bot.slash_command(
    name="deactivate", 
    description="Deactivate listening in the current channel.",
    name_localizations={disnake.Locale.zh_CN: "停用", disnake.Locale.zh_TW: "停用"},
    description_localizations={disnake.Locale.zh_CN: "停止当前频道的监听", disnake.Locale.zh_TW: "停止當前頻道的監聽"}
)
@authorized_check()
async def deactivate(inter: disnake.ApplicationCommandInteraction):
    await inter.response.defer(ephemeral=True)
    ChannelManager.instances.pop(inter.channel.id, None)
    await set_channel_config(inter.channel.id, {"activated": False})
    await inter.edit_original_message(content=get_loc(inter, "🛑 OpenClaw has stopped listening.", "🛑 OpenClaw 已停止监听。"))

@bot.slash_command(
    name="config", 
    description="View or modify the listening parameters for the current channel.",
    name_localizations={disnake.Locale.zh_CN: "频道配置", disnake.Locale.zh_TW: "頻道設定"},
    description_localizations={disnake.Locale.zh_CN: "查看或修改当前频道的监听参数", disnake.Locale.zh_TW: "查看或修改當前頻道的監聽參數"}
)
@authorized_check()
async def config_cmd(
    inter: disnake.ApplicationCommandInteraction, 
    key: str = commands.Param(None, description="Configuration key / 配置项名称"), 
    value: str = commands.Param(None, description="Configuration value / 新的配置值")
):
    await inter.response.defer(ephemeral=True)
    cfg = get_channel_config(inter.channel.id)
    if not key: 
        msg = get_loc(inter, "**Channel Config:**\n", "**频道配置：**\n")
        return await inter.edit_original_message(content=msg + "\n".join(f"`{k}` = `{v}`" for k, v in cfg.items() if k != "authorized_users"))
    if key not in DEFAULT_CHANNEL_CONFIG: 
        return await inter.edit_original_message(content=get_loc(inter, f"❌ Unknown config key: `{key}`", f"❌ 未知的频道配置: `{key}`"))
    if not value: 
        return await inter.edit_original_message(content=f"`{key}` = `{cfg.get(key)}`")
    
    orig = DEFAULT_CHANNEL_CONFIG[key]
    try: 
        typed = (value.lower() in ("true", "1", "yes")) if isinstance(orig, bool) else type(orig)(value)
    except ValueError: 
        return await inter.edit_original_message(content=get_loc(inter, "❌ Type error", "❌ 类型错误"))
        
    await set_channel_config(inter.channel.id, {key: typed})
    await inter.edit_original_message(content=get_loc(inter, f"✅ Updated channel config `{key}` = `{typed}`", f"✅ 更新频道 `{key}` = `{typed}`"))

@bot.slash_command(
    name="set_api", 
    description="Configure the LLM and API for the current server (Model is required).",
    name_localizations={disnake.Locale.zh_CN: "设置api", disnake.Locale.zh_TW: "設定api"},
    description_localizations={disnake.Locale.zh_CN: "为当前服务器配置大模型与API (模型为必填项)", disnake.Locale.zh_TW: "為當前伺服器設定大模型與API (模型為必填項)"}
)
@authorized_check()
async def set_api(
    inter: disnake.ApplicationCommandInteraction, 
    model: str = commands.Param(description="The model name / 模型名称 (e.g. gpt-4o-mini)"), 
    api_key: str = commands.Param(None, description="Your API Key / API密钥"), 
    base_url: str = commands.Param(None, description="Custom Base URL / 自定义请求地址")
):
    if not inter.guild:
        return await inter.response.send_message(get_loc(inter, "❌ This command is only available in servers. DMs automatically use your server's config.", "❌ 此命令仅限服务器内使用，私聊将自动读取您所在服务器的配置。"), ephemeral=True)

    await inter.response.defer(ephemeral=True)
    updates = {"model": model}
    if api_key: updates["api_key"] = api_key
    if base_url: updates["base_url"] = base_url
    
    await set_guild_config(inter.guild.id, updates)
    
    mask = f"sk-***{api_key[-4:]}" if api_key and len(api_key) > 4 else ("Unchanged" if not api_key else "***")
    zh_mask = f"sk-***{api_key[-4:]}" if api_key and len(api_key) > 4 else ("未修改" if not api_key else "***")
    
    msg = get_loc(
        inter, 
        f"✅ **Server Config Updated!**\n🤖 Model: `{model}`\n🔑 Key: `{mask}`\n🔗 URL: `{base_url or 'Default/Unchanged'}`",
        f"✅ **服务器级配置成功！**\n🤖 模型: `{model}`\n🔑 Key: `{zh_mask}`\n🔗 接口: `{base_url or '使用默认/未修改'}`"
    )
    await inter.edit_original_message(content=msg)

@bot.slash_command(
    name="whois", 
    description="Query user ID to nickname mappings.",
    name_localizations={disnake.Locale.zh_CN: "查询用户", disnake.Locale.zh_TW: "查詢用戶"},
    description_localizations={disnake.Locale.zh_CN: "查询用户名对照", disnake.Locale.zh_TW: "查詢用戶名對照"}
)
@authorized_check()
async def whois(
    inter: disnake.ApplicationCommandInteraction, 
    keyword: str = commands.Param(description="User ID or Name to search / 用户ID或名称关键字")
):
    await inter.response.defer(ephemeral=True)
    results = UserIndex.search(keyword)
    
    if results:
        msg = get_loc(inter, "🔍 Search Results:\n", "🔍 查询结果：\n") + "\n".join(f"`{u}` → **{n}**" for u, n in results[:20])
    else:
        msg = get_loc(inter, "❌ Not found.", "❌ 未找到。")
        
    await inter.edit_original_message(content=msg)

@bot.slash_command(
    name="retrieve_history", 
    description="Trace back and extract memory from channel history.",
    name_localizations={disnake.Locale.zh_CN: "追溯历史", disnake.Locale.zh_TW: "追溯歷史"},
    description_localizations={disnake.Locale.zh_CN: "追溯并提取记忆", disnake.Locale.zh_TW: "追溯並提取記憶"}
)
@authorized_check()
async def retrieve_history(
    inter: disnake.ApplicationCommandInteraction, 
    limit: int = commands.Param(description="Number of messages to retrieve / 获取的消息数量"), 
    start_time_str: str = commands.Param(None, description="Start time / 开始时间 (e.g. 2023-12-01 15:30)")
):
    await inter.response.defer()
    mgr, before_time = ChannelManager.get(inter.channel.id), None
    if start_time_str:
        try: before_time = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone(timedelta(hours=8)))
        except ValueError: return await inter.edit_original_message(content=get_loc(inter, "❌ Format: 2023-12-01 15:30", "❌ 格式: 2023-12-01 15:30"))

    msgs = []
    try:
        async for msg in inter.channel.history(limit=limit, before=before_time):
            if not msg.author.bot or msg.author.id == get_bot_id(): msgs.append(msg)
    except disnake.errors.Forbidden: return await inter.edit_original_message(content=get_loc(inter, "❌ No read permission.", "❌ 无读取权限。"))
    if not msgs: return await inter.edit_original_message(content=get_loc(inter, "❌ No messages retrieved.", "❌ 未抓取到任何消息。"))

    msgs.reverse()
    batches = (len(msgs) + 99) // 100
    await inter.edit_original_message(content=get_loc(inter, f"⏳ Retrieved {len(msgs)} msgs, extracting in {batches} batches...", f"⏳ 已抓取 {len(msgs)} 条，分 {batches} 批提取..."))

    success = 0
    for i in range(0, len(msgs), 100):
        fmt, last_id, last_ts = [], 0, 0.0
        for m in msgs[i:i+100]:
            nick = "[OpenClaw]" if m.author.id == get_bot_id() else await UserIndex.get_or_create_nickname(m)
            lines, last_id, last_ts = format_message(m, nick, last_id, last_ts)
            fmt.extend(lines)
        if await mgr.memory_manager.run_review(fmt, 0, f"RETRIEVE-B{(i//100)+1}"): success += 1
        await asyncio.sleep(2)
        
    res_msg = get_loc(inter, f"✅ Trace complete! Success {success}/{batches} batches.", f"✅ 追溯完毕！成功 {success}/{batches} 批。") if success else get_loc(inter, "❌ Extraction entirely failed.", "❌ 提取全部失败。")
    await inter.edit_original_message(content=res_msg)

if __name__ == "__main__":
    bot.run(TOKEN)