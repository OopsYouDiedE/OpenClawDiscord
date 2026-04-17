<div align="center">
<picture>
  <source
    width="100%"
    srcset="./OpenClaw-Discord-Banner.png"
    media="(prefers-color-scheme: dark)"
  />
  <source
    width="100%"
    srcset="./OpenClaw-Discord-Banner.png"
    media="(prefers-color-scheme: light), (prefers-color-scheme: no-preference)"
  />
  <img width="100%" src="./OpenClaw-Discord-Banner.png" alt="OpenClaw Banner" />
</picture>

<h1 align="center">OpenClawDiscord (Stelle)</h1>

<p align="center">让 AI 从工具真正成为人类的陪伴者，安全、自主地与人类建立情感连接。</p>

<p align="center">
  [<a href="https://discord.gg/uyrms6cv5z">立即邀请</a>] [<a href="https://github.com/OopsYouDiedE/OpenClawDiscord/wiki">查看文档</a>] [<a href="./README.en-US.md">English</a>] [<a href="./README.ja-JP.md">日本語</a>]
</p>

<p align="center">
  <a href="https://github.com/OopsYouDiedE/OpenClawDiscord/blob/main/LICENSE"><img src="https://img.shields.io/github/license/OopsYouDiedE/OpenClawDiscord.svg?style=flat&colorA=080f12&colorB=1fa669"></a>
  <a href="https://discord.gg/uyrms6cv5z"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fdiscord.com%2Fapi%2Finvites%2Fuyrms6cv5z%3Fwith_counts%3Dtrue&query=%24.approximate_member_count&suffix=%20members&logo=discord&logoColor=white&label=%20&color=7389D8&labelColor=6A7EC2"></a>
  <a href="https://img.shields.io/badge/Concept-Digital%20Life-FF6600?style=flat"><img src="https://img.shields.io/badge/Concept-Digital%20Life-FF6600?style=flat"></a>
</p>
</div>

---

## 🌟 核心强力特性 (Core Features)

本项目完全开源，基于 Python 与 `disnake` 构建，支持接入 OpenAI 或兼容 OpenRouter 的 LLM 接口。

- [x] **🧠 跨频道的本地 Markdown 记忆引擎**
  - **三层记忆沉淀**：实时上下文队列 (Deque) -> 中期通道事件摘要 (`channel_id.md`) -> 长期全局人物画像 (`user_id.md`)。
  - **基于 UID 的用户溯源**：无论用户在哪个群组（甚至修改了昵称），Stelle 都能通过全局索引 (`index.json`) 认出“老朋友”。
- [x] **🎭 自主决策与“读空气” (Vibe Check)**
  - 内置 `Judge` 裁判模型：实时分析频道情绪，决定是安静旁观、被动记录，还是在沉默时主动抛出话题（支持设定延迟触发）。
  - **防沉迷与刷屏过滤**：自带 AntiSpam 机制，并支持 `/shut_up` 让机器人立刻静音。
- [x] **⚙️ 灵活的多服务器/多模型配置**
  - **独立 API 设定**：支持通过 `/set_api` 为不同服务器单独配置 Model、API Key 和 Base URL。
  - **群组参数微调**：随时通过 `/config` 调整频道触发总结的阈值和上下文长度。
- [x] **🛡️ 绝对的数据主权**
  - 提供 `/forget_me` 指令，一键销毁 AI 对你的所有跨服全局记忆（物理删除本地 Markdown 档案）。

---

## 🏗️ 架构设计 (Technical Architecture)

```mermaid
graph TD
    User([Discord User]) -->|Interaction| DiscordAPI[Discord API / disnake]
    DiscordAPI -->|Event Stream| Engine[OpenClaw Cognitive Engine]
    
    subgraph Memory_Space [本地认知与记忆空间]
        Engine -->|Perception| STM[短期记忆: 内存 Deque 队列]
        STM -->|Review Prompt| MTM[中期记忆: 频道事件 .md]
        MTM -->|Distill Prompt| LTM[长期记忆: 全局人物侧写 .md]
    end
    
    Engine -->|Mood Check / Judge| Router{Decision Router}
    Router -->|Fire Now| ActiveMsg[主动回应 / 调侃]
    Router -->|Wait Condition| Timer[倒计时介入 / 话题接力]
    Router -->|Pass| Silence[安静观察 / 更新上下文]
    
    ActiveMsg --> DiscordAPI
````

-----

## 📦 部署与开发 (Deployment & Development)

### 1\. 系统要求与环境准备

  - **Python**：3.10 或更高版本
  - **Token**：一个有效的 Discord Bot Token (需在 Developer Portal 开启全部 Intents)
  - **API Key**：OpenAI 格式的 API 密钥（也支持 OpenRouter 等第三方中转）

首先，克隆仓库并配置环境变量：

```bash
git clone [https://github.com/OopsYouDiedE/OpenClawDiscord.git](https://github.com/OopsYouDiedE/OpenClawDiscord.git)
cd OpenClawDiscord

# 复制或创建环境变量文件
cp .env.example .env
```

在 `.env` 文件中填入以下内容：

```ini
DISCORD_TOKEN=your_discord_bot_token_here
OPENAI_API_KEY=your_openai_or_openrouter_api_key_here
# OPENROUTER_API_KEY=备用键名
```

### 2\. 安装依赖 (支持 pip 与 uv)

我们推荐使用 [uv](https://github.com/astral-sh/uv) 极速构建虚拟环境，你也可以使用传统的 `pip`。

<details open>
<summary><b>选项 A: 使用 uv 安装 (推荐，速度极快)</b></summary>

```bash
# 1. 如果未安装 uv，先全局安装
curl -LsSf [https://astral.sh/uv/install.sh](https://astral.sh/uv/install.sh) | sh

# 2. 创建并激活虚拟环境
uv venv
source .venv/bin/activate  # Windows 用户使用 .venv\Scripts\activate

# 3. 安装所需依赖
uv pip install disnake openai aiofiles pyyaml python-dotenv
```

</details>

<details>
<summary><b>选项 B: 使用原生 pip 安装</b></summary>

```bash
# 1. 创建并激活虚拟环境
python -m venv venv
source venv/bin/activate  # Windows 用户使用 venv\Scripts\activate

# 2. 安装所需依赖
pip install disnake openai aiofiles pyyaml python-dotenv
```

</details>

### 3\. 启动 Bot

```bash
python main.py
```

当终端输出 `✅ OpenClaw 已登录 (ID: xxxxx)` 时，说明部署成功。数据会自动存储在根目录生成的 `memories` 文件夹与 `config.yaml` 中。

-----

## 🎮 核心指令指南 (Commands)

在 Discord 频道内输入 `/` 即可唤出指令列表：

### 用户基础控制

  - `/forget_me` - 彻底销毁 AI 对你的跨服全局人物画像。
  - `/shut_up` - 强行让机器人在当前频道闭嘴 5 分钟。

### 频道与管理员控制 (需管理员或白名单权限)

  - `/activate` - 在当前频道激活 AI 监听与自主回复引擎。
  - `/deactivate` - 停止当前频道的监听。
  - `/set_api` - 为当前服务器配置独立的大模型名称、API Key 或 Base URL（默认读取全局 `.env`）。
  - `/config` - 查看或修改频道的触发阈值、记忆长度等底层参数。
  - `/whois` - 查询后台记录的用户 ID 与多群组昵称对照表。

### 记忆系统干预

  - `/memorize` - 手动强制触发一次当前频道的“短期上下文 -\> 历史事件”打包。
  - `/distill` - 手动强制将历史事件提炼、进化为全局人物画像。
  - `/clear` - 格式化当前频道的所有历史记忆与上下文。
  - `/retrieve_history` - 从频道的过往历史消息中批量追溯并提取事件记忆。

-----

## 🤝 致敬与友好项目 (Acknowledgements & Friendly Projects)

OpenClaw 的成长深受以下项目的启发，我们将其视为探索数字生命边界的同路人：

  * [**Project AIRI**](https://github.com/moeru-ai/airi) - 优秀的 AI Waifu 灵魂容器，重新定义了数字生命的呈现。
  * [**MetaGPT**](https://github.com/geekan/MetaGPT) - 启发了关于多智能体协作与复杂任务处理的认知架构。
  * [**elizaOS**](https://github.com/elizaOS/eliza) - 极具参考价值的 Agent 框架设计。
  * [**Neuro-sama**](https://www.youtube.com/@Neurosama) - 永远的灵感源泉与行业标杆。

-----

## 🤝 社区与贡献 (Community & Contributing)

本项目已全面开源！我们热烈欢迎各种形式的贡献：

  - 🐛 **Bug 修复**：提交 Issue 或 PR 修复本地文件系统锁争用或异步处理问题。
  - ✨ **新功能**：例如增加视觉多模态支持、接入更丰富的外部记忆数据库等。
  - 🌍 **本地化翻译**：完善中、英、日等多语言支持。

欢迎加入我们的 [Discord 社区](https://discord.gg/uyrms6cv5z) 参与技术讨论！

-----

## 📄 许可证 (License)

本项目采用 MIT License。详见 [LICENSE](https://www.google.com/search?q=LICENSE) 文件。

-----

## 🛡️ 隐私与安全 (Privacy & Security)

OpenClaw 遵循以下原则：

  - 所有用户画像与事件日志均以 `.md` 纯文本格式储存于本地。
  - 提供真正的被遗忘权（`/forget_me` 将彻底删除关联的本地文件）。
  - 严格的权限校验机制，敏感指令仅限服务器管理员可用。

-----

<p align="center">
Made with ❤️ by <a href="https://www.google.com/search?q=https://github.com/OopsYouDiedE">OopsYouDiedE</a>
</p>

<p align="center">
<a href="https://star-history.com/#OopsYouDiedE/OpenClawDiscord&Date">
<img src="https://api.star-history.com/svg?repos=OopsYouDiedE/OpenClawDiscord&type=Date" alt="Star History Chart">
</a>
</p>