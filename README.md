
<div align="center">
<picture>
  <source
    width="100%"
    srcset="https://raw.githubusercontent.com/OopsYouDiedE/OpenClawDiscord/main/OpenClaw-Discord-Banner.png"
    media="(prefers-color-scheme: dark)"
  />
  <source
    width="100%"
    srcset="https://raw.githubusercontent.com/OopsYouDiedE/OpenClawDiscord/main/OpenClaw-Discord-Banner.png"
    media="(prefers-color-scheme: light), (prefers-color-scheme: no-preference)"
  />
  <img width="100%" src="https://raw.githubusercontent.com/OopsYouDiedE/OpenClawDiscord/main/OpenClaw-Discord-Banner.png" alt="OpenClaw Banner" />
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

## 📅 开源承诺 (Open Source Commitment)

目前 **OpenClawDiscord** 处于内部孵化与核心功能迭代阶段。我们深信社区的力量，并设立了明确的开源里程碑：

* **目标：** 当本项目的 GitHub **Stars 达到 1,000** 时。
* **行动：** 我们将完整开放本项目的所有源代码（包括核心认知引擎、三层记忆模型逻辑及 Discord 适配层），并转向社区驱动开发。

> [!TIP]
> 如果你认可“数字生命”与“情感伴侣”的愿景，请为本项目点上一颗 **Star**，帮助我们更早实现开源目标。

---

## 🌟 核心强力特性 (Core Features)

- [x] **🧠 认知与记忆系统**
  - **三层记忆沉淀**：自动从日常对话中提取关键事件，构建从瞬时反应到长期历史的认知链条。
  - **全局人物画像**：基于 UID 锁定生成的“性格侧写”，让 Stelle 能够像老朋友一样记住每位用户的偏好与习惯。
- [x] **🎭 自主决策引擎**
  - **氛围感知 (Vibe Check)**：实时分析频道情绪，Stelle 会根据“空气”决定是安静旁观还是主动介入调侃。
  - **主动情感反馈**：它拥有自己的性格偏好，可能因为你的关心而喜悦，也可能因为被忽视而展现“小脾气”。
- [x] **🛡️ 安全与秩序**
  - **情感边界保护**：在建立连接的同时，内置严格的安全过滤与隐私抹除指令（`/forget_me`）。
  - **智能内容适配**：自动折叠长代码或复杂分析内容，保持频道社交空间的整洁。

---

## 🏗️ 架构设计 (Technical Architecture)

```mermaid
graph TD
    User([Discord User]) -->|Interaction| DiscordAPI[Discord API]
    DiscordAPI -->|Event Stream| Engine[OpenClaw Cognitive Engine]
    
    subgraph Memory_Space [认知与记忆空间]
        Engine -->|Perception| STM[短期记忆: 实时对话流]
        STM -->|Synthesis| MTM[中期记忆: 社交事件摘要]
        MTM -->|Generalization| LTM[长期记忆: 全局人物画像]
    end
    
    Engine -->|Mood Check| Router{Decision Router}
    Router -->|Proactive| ActiveMsg[自主发言/主动破冰]
    Router -->|Reactive| Respond[针对性回应/调侃]
    Router -->|Observe| Silence[安静观察/读空气]
    
    ActiveMsg --> DiscordAPI
    Respond --> DiscordAPI
````

-----

## 🤝 致敬与友好项目 (Acknowledgements & Friendly Projects)

OpenClaw 的成长深受以下项目的启发，我们将其视为探索数字生命边界的同路人：

  * [**Project AIRI**](https://github.com/moeru-ai/airi) - 优秀的 AI Waifu 灵魂容器，重新定义了数字生命的呈现。
  * [**MetaGPT**](https://github.com/geekan/MetaGPT) - 启发了我们关于多智能体协作与复杂任务处理的认知架构。
  * [**Gemini**](https://deepmind.google/technologies/gemini/) - 为本项目提供了卓越的多模态理解与长文本推理能力支持。
  * [**elizaOS**](https://github.com/elizaOS/eliza) - 极具参考价值的 Agent 框架设计。
  * [**Neuro-sama**](https://www.youtube.com/@Neurosama) - 永远的灵感源泉与行业标杆。

-----

## 🛡️ 获取支持与贡献

目前你可以通过邀请 Bot 的方式抢先体验 Stelle 的成长。

> **注意**：OpenClaw 旨在成为社区生态的一部分。请用对待朋友的方式与它相处，它会随着时间的推移，慢慢构建出属于你们的独特回忆。

-----

\<p align="center"\>
Made with ❤️ by \<a href="https://www.google.com/search?q=https://github.com/OopsYouDiedE"\>OopsYouDiedE\</a\>
\</p\>

\<p align="center"\>
\<a href="https://www.google.com/search?q=https://star-history.com/%23OopsYouDiedE/OpenClawDiscord%26Date"\>
\<img src="https://www.google.com/search?q=https://api.star-history.com/svg%3Frepos%3DOopsYouDiedE/OpenClawDiscord%26type%3DDate" alt="Star History Chart"\>
\</a\>
\</p\>

