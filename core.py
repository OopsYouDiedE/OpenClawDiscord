import re
import json
import time
import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple, Protocol

from openai import AsyncOpenAI

# ==========================================
# 1. 核心数据模型 (DTO)
# ==========================================
@dataclass
class Attachment:
    url: str
    content_type: str = "unknown"

@dataclass
class GenericMessage:
    """与平台无关的通用消息对象"""
    message_id: str
    channel_id: str
    author_id: str
    author_name: str
    content: str
    timestamp: float
    is_reply_to_bot: bool = False
    reply_to_author_id: Optional[str] = None
    reply_to_author_name: Optional[str] = None
    attachments: List[Attachment] = field(default_factory=list)

@dataclass
class LLMConfig:
    model: str
    api_key: str
    base_url: str = "https://api.openai.com/v1"

@dataclass
class BotIdentity:
    bot_id: str
    bot_name: str = "OpenClaw"

# ==========================================
# 2. 核心接口定义 (Interfaces)
# ==========================================
class IPlatform(Protocol):
    """平台适配器接口：用于核心层向外部发送动作"""
    async def send_text(self, channel_id: str, text: str) -> None: ...
    async def send_embed(self, channel_id: str, content: str) -> None: ...
    async def log_debug(self, title: str, description: str, fields: List[Tuple[str, str, bool]]) -> None: ...
    def is_someone_typing(self, channel_id: str) -> bool: ...

class IStorage(Protocol):
    """存储适配器接口：用于解耦具体的文件系统操作 (便于内存Mock测试)"""
    async def read(self, path: str, default: str = "") -> str: ...
    async def write(self, path: str, content: str) -> None: ...
    async def append(self, path: str, content: str) -> None: ...

class IUserIndex(Protocol):
    """用户索引接口：用于根据ID获取全局或频道内的别名"""
    def get_name(self, channel_id: str, user_id: str) -> str: ...
    def build_mapping_text(self, channel_id: str, active_uids: List[str]) -> str: ...

# ==========================================
# 3. 提示词与清洗工具
# ==========================================
def clean_think_tags(text: str) -> str:
    """清洗模型输出的思维链，严格使用 <think> 标签"""
    return re.sub(r'<think>.*?(?:</think>|$)', '', text, flags=re.DOTALL | re.IGNORECASE).strip()

def parse_json_from_text(text: str) -> dict:
    cleaned = re.sub(r'^```[a-zA-Z]*\n|\n```$', '', text.strip(), flags=re.MULTILINE)
    start, end = cleaned.find('{'), cleaned.rfind('}')
    if start != -1 and end != -1:
        cleaned = cleaned[start:end+1]
    try: return json.loads(cleaned)
    except Exception: return {}

def estimate_tokens(text: str) -> int:
    return len(text.encode('utf-8')) // 3 + len(text) // 2

def build_judge_prompt(bot: BotIdentity, is_dm: bool = False) -> str:
    base = f"""You are {bot.bot_name} (User ID: {bot.bot_id}). You MUST output valid JSON ONLY:
{{
  "think": "Short inner monologue analyzing the current vibe", "focus": {{"topic": "Current conversation topic", "drifted": false}},
  "trigger": {{"fire_now": false, "condition_type": "silence|keyword|gap|never", "condition_value": 15, "expires_after": 120}},
  "intent": {{"stance": "playful|snarky|question|inform|react|pass", "angle": "Description of intervention angle"}}, "recall_user_id": null
}}
1. Identity: If your name or ID is mentioned, decisively set fire_now: true. 2. Recognize users by ID, names are just aliases."""
    
    recall_rule = "\n5. Memory Recall: Fill in the strongly related member ID (if the topic is about yourself, fill in your own ID) or null."
    if is_dm: return base + "3. This is [DM Mode], focus on 1-on-1 chat, set fire_now: true directly unless no reply is needed." + recall_rule
    return base + "3. Do not interfere with others' private chats. 4. Maintain topic heat during awkward silences." + recall_rule

def build_character_prompt(bot: BotIdentity, is_dm: bool = False) -> str:
    env = "DM partner" if is_dm else "group member"
    return f"""You are {env} {bot.bot_name} (User ID: {bot.bot_id}).
[Personality] Chill, authentic, a bit lazy but playful. You are a peer, NOT a stiff assistant.
[Rules] Reply in the user's language; 1-3 sentences; sparse emojis; no formal language. Read the room: if the vibe is lighthearted, play along with harmless jokes. NEVER expose your AI identity.
[Advanced Format] For professional/long content (code/math/analysis, etc.), STRICTLY wrap it in <embed>detailed content</embed>. Outside the tag, leave ONLY one minimal summary sentence!"""

MEMORY_REVIEW_PROMPT = """You are OpenClaw. Review the chat history and extract important events.
Output pure JSON ONLY: {"events": [{"summary": "Description including (ID:xxxx)", "related_user_id": "User ID", "event_time": "YYYY-MM-DD HH:MM", "category": "Category"}]}"""

MEMORY_DISTILL_PROMPT = "You are OpenClaw. Distill an overall global impression of ID:{user_id} based on these events. Write 3-5 colloquial sentences. Include the timestamp. Leave empty if insignificant."


# ==========================================
# 4. LLM 客户端网关
# ==========================================
class AIGateway:
    def __init__(self):
        self._clients_cache: Dict[str, AsyncOpenAI] = {}

    def _get_client(self, cfg: LLMConfig) -> AsyncOpenAI:
        cache_key = f"{cfg.api_key}|{cfg.base_url}"
        if cache_key not in self._clients_cache:
            self._clients_cache[cache_key] = AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=120.0)
        return self._clients_cache[cache_key]

    async def generate_json(self, cfg: LLMConfig, system_prompt: str, user_prompt: str, max_tokens=2048) -> dict:
        client = self._get_client(cfg)
        resp = await client.chat.completions.create(
            model=cfg.model,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=max_tokens
        )
        content = clean_think_tags(resp.choices[0].message.content or "")
        return parse_json_from_text(content)

    async def generate_text(self, cfg: LLMConfig, system_prompt: str, user_prompt: str, max_tokens=4096) -> str:
        client = self._get_client(cfg)
        resp = await client.chat.completions.create(
            model=cfg.model,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            temperature=0.7,
            max_tokens=max_tokens
        )
        return clean_think_tags(resp.choices[0].message.content or "")

# ==========================================
# 5. 三层记忆管理器
# ==========================================
class MemoryManager:
    def __init__(self, channel_id: str, storage: IStorage, ai: AIGateway, user_index: IUserIndex, bot: BotIdentity):
        self.channel_id = channel_id
        self.storage = storage
        self.ai = ai
        self.user_index = user_index
        self.bot = bot
        self.channel_path = f"channels/{channel_id}.md"
        self._write_lock = asyncio.Lock()

    async def _read_sections(self) -> Dict[str, str]:
        content = await self.storage.read(self.channel_path, "")
        return {
            s: m.group(1).strip() if (m := re.search(rf"# {s}\n+(.*?)(?=\n+---|\n+# |$)", content, re.DOTALL)) else ""
            for s in ["历史事件", "短期进程"]
        }

    async def load_context(self, recall_user_id: Optional[str] = None) -> str:
        """加载上下文记忆：组装 频道记忆 + 用户全局记忆"""
        parts = []
        if recall_user_id:
            user_path = f"users/{recall_user_id}.md"
            ucontent = await self.storage.read(user_path, "")
            if m := re.search(r"## 人物印象\n+(.*)", ucontent, re.DOTALL):
                if imp := m.group(1).strip(): 
                    nick = "Yourself" if recall_user_id == self.bot.bot_id else self.user_index.get_name(self.channel_id, recall_user_id)
                    parts.append(f"[Global profile for {nick}(ID:{recall_user_id})]\n{imp}")
        
        secs = await self._read_sections()
        if events := [e.strip() for e in secs.get("历史事件", "").split("\n\n") if e.strip()]:
            parts.append(f"[Recent Events]\n" + "\n\n".join(events[-10:]))
        return "\n\n".join(parts)

    async def run_review(self, llm_cfg: LLMConfig, recent_history: List[str], review_count: int, source: str = "AUTO") -> bool:
        """打包频道历史事件"""
        if not recent_history: return True
        try:
            result = await self.ai.generate_json(llm_cfg, MEMORY_REVIEW_PROMPT, "\n".join(recent_history))
            events = result.get("events", [])
        except Exception as e:
            print(f"[Memory Review Error] {e}")
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
            new_content = f"# 历史事件\n\n{secs['历史事件']}\n\n---\n\n# 短期进程\n\n{secs['短期进程']}\n\n---\n\n"
            await self.storage.write(self.channel_path, new_content)

        # 定期触发全局人物画像提炼
        if review_count > 0 and review_count % 5 == 0: 
            asyncio.create_task(self._run_distill(llm_cfg, secs["历史事件"]))
        return True

    async def _run_distill(self, llm_cfg: LLMConfig, event_text: str):
        """提炼用户全局人格画像"""
        if not event_text: return
        for uid in set(re.findall(r'ID:(\d+)', event_text)):
            related = [line for line in event_text.splitlines() if f"ID:{uid}" in line]
            if len(related) < 3: continue
            try:
                imp = await self.ai.generate_text(llm_cfg, MEMORY_DISTILL_PROMPT.format(user_id=uid), "\n".join(related), max_tokens=1024)
                if imp:
                    await self._update_user_impression(uid, imp)
            except Exception as e: 
                print(f"[MemoryDistill Error] uid={uid}: {e}")

    async def _update_user_impression(self, uid: str, impression: str):
        user_path = f"users/{uid}.md"
        content = await self.storage.read(user_path, f"# ID:{uid} 的全局档案\n\n## 人物印象\n\n")
        new_block = f"*最后更新：{datetime.now().strftime('%Y-%m-%d')}*\n{impression}"
        
        pattern = re.compile(r"(## 人物印象\n+).*?(?=\n# |$)", re.DOTALL)
        content = pattern.sub(rf"\g<1>{new_block}\n\n", content) if pattern.search(content) else f"{content}\n\n## 人物印象\n\n{new_block}\n\n"
        await self.storage.write(user_path, content.strip() + "\n")

# ==========================================
# 6. 决策器 Node 与 对话引擎
# ==========================================
class ConversationEngine:
    """频道/私聊的核心会话引擎（状态机）"""
    def __init__(
        self, 
        channel_id: str, 
        is_dm: bool,
        bot: BotIdentity,
        platform: IPlatform,
        storage: IStorage,
        user_index: IUserIndex,
        ai_gateway: AIGateway
    ):
        self.channel_id = channel_id
        self.is_dm = is_dm
        self.bot = bot
        self.platform = platform
        self.user_index = user_index
        self.ai = ai_gateway
        self.memory_manager = MemoryManager(channel_id, storage, ai_gateway, user_index, bot)
        
        self._history = deque(maxlen=200)
        self.active_users: Dict[str, float] = {}
        self.focus: Optional[str] = None
        self.wait_cond: Optional[Dict] = None
        
        self.msg_count = 0
        self.last_msg_time = time.time()
        self.last_author_id = ""
        self.is_processing = False
        
        self.msg_count_since_review = 0
        self.review_count_since_distill = 0
        
        # 可配置参数 (外部可重写)
        self.review_msg_threshold = 50
        self.max_input_tokens = 8000
        self.llm_cfg = LLMConfig(model="gpt-4o-mini", api_key="") # 外部需注入正确的KEY

    def set_llm_config(self, cfg: LLMConfig):
        self.llm_cfg = cfg

    def _format_to_context(self, msg: GenericMessage) -> List[str]:
        """将标准消息格式化为上下文字符串，保留URL处理"""
        parts = []
        if msg.reply_to_author_id:
            parts.append(f"[Reply to {msg.reply_to_author_name}(ID:{msg.reply_to_author_id})]")
        if msg.content:
            parts.append(msg.content[:2000])
            
        text = " ".join(parts).strip()
        lines = []
        
        # 控制说话人头部的输出（避免满屏的时间戳）
        if msg.author_id != self.last_author_id or (msg.timestamp - self.last_msg_time) > 120:
            time_str = datetime.fromtimestamp(msg.timestamp).astimezone().strftime('%Y-%m-%d %H:%M')
            name_label = f"[{self.bot.bot_name}](ID:{self.bot.bot_id})" if msg.author_id == self.bot.bot_id else f"{msg.author_name}(ID:{msg.author_id})"
            lines.append(f"--- {name_label} ({time_str}) ---")

        if text: lines.append(text)
        
        # 将文件抽象为URL拼接到上下文中
        for att in msg.attachments:
            lines.append(att.url)
            
        return lines

    async def process_message(self, msg: GenericMessage) -> bool:
        """主入口：处理接收到的消息"""
        self.active_users[msg.author_id] = time.time()
        
        # 记录历史
        lines = self._format_to_context(msg)
        self.last_author_id = msg.author_id
        self.last_msg_time = msg.timestamp
        self._history.extend(lines)
        
        # 裁剪 Token
        total_tokens = sum(estimate_tokens(line) for line in self._history)
        while self._history and total_tokens > self.max_input_tokens:
            total_tokens -= estimate_tokens(self._history.popleft())
            
        self.msg_count += 1
        self.msg_count_since_review += 1

        # 触发记忆压缩
        if self.msg_count_since_review >= self.review_msg_threshold:
            self.msg_count_since_review = 0
            self.review_count_since_distill += 1
            asyncio.create_task(self.memory_manager.run_review(self.llm_cfg, list(self._history), self.review_count_since_distill, "AUTO"))

        if self.is_processing: return True

        # 决策与回复逻辑
        if msg.is_reply_to_bot or self.is_dm or self.bot.bot_id in msg.content:
            # 强触发
            await self.execute_reply({"stance": "react", "angle": "直接回应"})
            return True

        if not self.wait_cond:
            await self.evaluate_decision()

        await self._check_wait_condition(msg)
        return True

    async def evaluate_decision(self):
        """决策节点：使用小型 Prompt 决定当前的行动"""
        active_uids = [uid for uid, ts in self.active_users.items() if time.time() - ts < 600 and uid != self.bot.bot_id]
        participants = ", ".join([self.user_index.get_name(self.channel_id, u) for u in active_uids]) or "无"
        uid_map = self.user_index.build_mapping_text(self.channel_id, active_uids)
        curr_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

        sys_p = build_judge_prompt(self.bot, self.is_dm) + f"\n[Time: {curr_utc}]\n[Mapping]\n{uid_map}"
        user_msg = f"Active: {participants}\nFocus: {self.focus}\nHistory:\n" + "\n".join(list(self._history)[-10:])

        try:
            jdg = await self.ai.generate_json(self.llm_cfg, sys_p, user_msg)
            self.focus = jdg.get("focus", {}).get("topic", "无")
            self.wait_cond = {
                **jdg.get("trigger", {}), 
                "intent": jdg.get("intent", {"stance": "pass"}), 
                "recall_user_id": jdg.get("recall_user_id"), 
                "expiry": time.time() + jdg.get("trigger", {}).get("expires_after", 120)
            }
            self.msg_count = 0
        except Exception as e:
            print(f"[Judge Error] {e}")

    async def _check_wait_condition(self, latest_msg: GenericMessage):
        """状态机检查条件"""
        if not self.wait_cond: return
        c = self.wait_cond
        
        if time.time() > c.get("expiry", 0):
            self.wait_cond = None
            return

        typ = c.get("condition_type")
        uid = c.get("recall_user_id")
        intent = c["intent"]
        
        if c.get("fire_now"):
            await self.execute_reply(intent, uid)
            return

        # 其他触发机制 (Gap, Keyword, Silence)
        if typ == "gap" and self.msg_count >= int(c.get("condition_value", 5)): 
            await self.execute_reply(intent, uid)
        elif typ == "keyword" and any(k in latest_msg.content for k in c.get("condition_value", [])): 
            await self.execute_reply(intent, uid)
        elif typ == "silence":
            # 开启异步监听 silence (不阻塞主线程)
            asyncio.create_task(self._wait_for_silence(float(c.get("condition_value", 15)), intent, uid))

    async def _wait_for_silence(self, wait_sec: float, intent: dict, recall_uid: str):
        await asyncio.sleep(wait_sec)
        # 检查是否还有人在打字，或者是否已经处理过
        if not self.is_processing and not self.platform.is_someone_typing(self.channel_id) and self.wait_cond:
            await self.execute_reply(intent, recall_uid)

    def _extract_embed_and_reply(self, raw: str) -> Tuple[str, str]:
        embed_match = re.search(r'<embed>(.*?)(?:</embed>|$)', raw, re.DOTALL | re.IGNORECASE)
        embed_content = embed_match.group(1).strip() if embed_match else ""
        reply = re.sub(r'<embed>.*?(?:</embed>|$)', '', raw, flags=re.DOTALL | re.IGNORECASE).strip()
        return reply, embed_content

    async def execute_reply(self, intent: Dict, recall_user_id: Optional[str] = None):
        """执行回复（主生成节点）"""
        if intent.get("stance") == "pass" or self.is_processing: return
        self.is_processing, self.wait_cond = True, None
        
        try:
            mem_ctx = await self.memory_manager.load_context(recall_user_id)
            active_uids = [uid for uid, ts in self.active_users.items() if time.time() - ts < 600 and uid != self.bot.bot_id]
            participants = ", ".join([self.user_index.get_name(self.channel_id, u) for u in active_uids]) or "无"
            uid_map = self.user_index.build_mapping_text(self.channel_id, active_uids)
            curr_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            
            sys_p = build_character_prompt(self.bot, self.is_dm) + f"\n[Time: {curr_utc}]\nActive: {participants}\n[Mapping]\n{uid_map}" + (f"\n\nContext:\n{mem_ctx}" if mem_ctx else "")
            footer = f"\n\nAngle: {intent.get('angle')}, Stance: {intent.get('stance')}"
            user_msg = "History:\n" + "\n".join(list(self._history)[-25:]) + footer

            # 调用大模型生成回复
            raw_reply = await self.ai.generate_text(self.llm_cfg, sys_p, user_msg)
            reply, embed_content = self._extract_embed_and_reply(raw_reply)

            # 调用平台接口发消息
            if not reply and embed_content: reply = "Detailed content in the card below:"
            
            # 分块发送逻辑也可以下沉到这里，或者在适配器中实现，为了纯粹，我们在核心里只调用发送
            if reply:
                await self.platform.send_text(self.channel_id, reply[:2000]) # 简化处理
            if embed_content:
                await self.platform.send_embed(self.channel_id, embed_content[:4000])

            # 模拟机器人自己发送了消息，记录到上下文中
            bot_msg = GenericMessage(
                message_id="bot_generated", channel_id=self.channel_id,
                author_id=self.bot.bot_id, author_name=self.bot.bot_name,
                content=reply + ("\n[Embed Sent]" if embed_content else ""),
                timestamp=time.time()
            )
            self._history.extend(self._format_to_context(bot_msg))
            
            await self.platform.log_debug("🤖 Main Reply", f"Stance: {intent.get('stance')}", [])
            
        except Exception as e:
            await self.platform.log_debug("❌ API Error", str(e), [])
        finally:
            self.is_processing = False


# ==========================================
# 测试与本地运行支持 (Mock implementations)
# ==========================================
if __name__ == "__main__":
    import os
    
    # 1. 模拟底层存储引擎 (内存字典)
    class MockStorage(IStorage):
        def __init__(self): self.db = {}
        async def read(self, path: str, default: str = "") -> str: return self.db.get(path, default)
        async def write(self, path: str, content: str) -> None: self.db[path] = content
        async def append(self, path: str, content: str) -> None: self.db[path] = self.db.get(path, "") + content

    # 2. 模拟平台适配器 (控制台输出)
    class ConsolePlatform(IPlatform):
        async def send_text(self, cid: str, text: str): print(f"\n[Bot 💬]: {text}\n")
        async def send_embed(self, cid: str, content: str): print(f"\n[Bot 🪪 Embed]:\n{content}\n")
        async def log_debug(self, title, desc, fields): print(f"[⚙️ Debug | {title}] {desc}")
        def is_someone_typing(self, cid: str) -> bool: return False

    # 3. 模拟用户映射
    class MockUserIndex(IUserIndex):
        def get_name(self, cid: str, uid: str) -> str: return f"User_{uid[-4:]}"
        def build_mapping_text(self, cid: str, uids: List[str]) -> str: return "Mapping Mock"

    async def main_test():
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            print("⚠️ 未配置 OPENAI_API_KEY，本地测试将被跳过。")
            return
            
        bot_id = BotIdentity(bot_id="112233", bot_name="TestClaw")
        engine = ConversationEngine(
            channel_id="channel_01",
            is_dm=False,
            bot=bot_id,
            platform=ConsolePlatform(),
            storage=MockStorage(),
            user_index=MockUserIndex(),
            ai_gateway=AIGateway()
        )
        engine.set_llm_config(LLMConfig(model="gpt-4o-mini", api_key=api_key))
        
        print("====== 🚀 OpenClaw Core 本地测试启动 ======")
        msg1 = GenericMessage(
            message_id="m1", channel_id="channel_01", author_id="u123", author_name="Alice",
            content="你好！OpenClaw，你能给我讲个冷笑话吗？", timestamp=time.time(), is_reply_to_bot=True
        )
        
        print(f"[User Alice]: {msg1.content}")
        await engine.process_message(msg1)
        
        # 给一定时间让异步任务(等待判定/调用LLM)完成
        await asyncio.sleep(5) 
        
    asyncio.run(main_test())