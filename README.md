# chat-system-ai-coding

> A terminal-based real-time group chat system — built in a 2-hour AI Coding interview.
> 一个基于终端的实时群聊系统,在一场 2 小时 AI Coding 面试中从零交付。

本仓库是一份 **AI Coding 工作流作品集**,展示在严格时间约束下,如何通过驾驭 AI 完成一个有分布式通信、并发、协议设计、TUI 交互、持久化等多维度要求的系统级项目。

---

## 项目背景

2026 年 4 月,蚂蚁集团 AI Coding 面试,命题要求在 **120 分钟内** 完成:

- 中心化 Chat Server(认证、群组、消息路由、广播、持久化)
- TUI Client(多会话并行、未读计数、输入与接收非阻塞)
- 自定义 TCP 应用层协议
- 至少支持 50 并发、端到端延迟 ≤500ms、空闲 CPU ≤5%、协议容错不崩溃
- 完整的架构文档、协议规范、测试脚本、测试报告

面试环境为蚂蚁自研的 AI IDE(CodeFuse),所有代码由 AI 生成,考验的是 **AI 驾驭能力而非编码能力**。

本仓库是考试结束后基于原方案的生产级重构,在保留全部设计决策与工程教训的基础上,补足了考试中没时间做的类型注解、模块拆分、错误处理完善、测试规范化等工作。

---

## 面试成绩(考试环境实测)

| 验收指标 | 要求 | 实测 | 结论 |
|---|---:|---:|:---:|
| 并发在线连接 | ≥ 50 | 50 / 50 | ✅ |
| 消息端到端延迟 p50 | ≤ 500ms | 22ms | ✅ |
| 消息端到端延迟 p95 | ≤ 500ms | 32ms | ✅ |
| 消息端到端延迟 p99 | ≤ 500ms | 49ms | ✅ |
| 消息端到端延迟 max | ≤ 500ms | 79ms | ✅ |
| 50 空闲连接 CPU 占用 | ≤ 5% | 0.0% | ✅ |
| 协议容错(非法消息) | 不崩溃 | PASS | ✅ |

7 项验收维度(基础功能、多会话切换、并发压测、断线检测、协议容错、持久化恢复、TUI 流畅性)全部通过。

---

## 如果你是面试官 / HR,请看这三份文档

1. **[docs/AI_WORKFLOW.md](docs/AI_WORKFLOW.md)** — AI 驾驭工作流方法论
   我如何用「战略层 + 执行层 + 验证层」三层分工完成这个项目,可直接复用到日常工作。

2. **[docs/LESSONS.md](docs/LESSONS.md)** — 8 个真实踩坑复盘
   从"事件循环架构错误导致消息收不到"到"AI 擅自加了每次清库的代码",每一个坑都是真实的工程教训。

3. **[docs/TIMELINE.md](docs/TIMELINE.md)** — 2 小时分钟级时间线
   什么时候做了什么决策、遇到什么问题、怎么解决,一场 AI Coding 面试的完整纪录片。

## 如果你想跑起来玩玩

```bash
git clone https://github.com/<your-username>/chat-system-ai-coding.git
cd chat-system-ai-coding
pip install -r requirements.txt

# 启动服务器
bash scripts/run.sh

# 另开终端,启动客户端(可开多个并行)
python -m src.client
```

客户端命令速查:

| 命令 | 说明 |
|---|---|
| `/register <user> <pass>` | 注册账号 |
| `/login <user> <pass>` | 登录 |
| `/create <group>` | 创建群组 |
| `/join <group>` | 加入群组 |
| `/list` | 列出我加入的群组 |
| `/switch group:<name>` | 切换到群组会话 |
| `/switch user:<name>` | 切换到私聊会话 |
| `/msg <text>` | 发送文本到当前会话 |
| `/img <base64>` | 发送图片(Base64 内嵌) |
| `/logout` / `/quit` | 登出 / 退出 |
| `/help` | 查看完整命令 |

---

## 技术栈

- **Python 3.11**,`asyncio` 单线程事件循环
- **TCP + 自定义帧协议**:4 字节大端长度前缀 + UTF-8 JSON body
- **SQLite**(stdlib,无 ORM)持久化
- **prompt_toolkit** 构建全屏 TUI
- **psutil** 压测时采样服务器 CPU

没有重依赖,部署只需 Python 3.11 和 `prompt_toolkit`、`psutil` 两个包。

## 仓库结构

```
chat-system-ai-coding/
├── src/
│   ├── codec.py              # 帧协议编解码
│   ├── server.py             # 异步 Chat Server
│   ├── client.py             # TUI Client
│   ├── db.py                 # SQLite 访问层
│   └── protocol_types.py     # 消息类型与错误码枚举
├── tests/
│   ├── test_protocol.py      # 单连接协议功能验证
│   ├── test_stress.py        # 50 并发压测 + 延迟统计
│   └── test_malformed.py     # 协议容错测试
├── docs/
│   ├── ARCHITECTURE.md       # 架构设计文档
│   ├── PROTOCOL.md           # 应用层协议规范
│   ├── TEST_REPORT.md        # 测试报告(含真实压测数据)
│   ├── AI_WORKFLOW.md        # AI Coding 工作流方法论  ⭐
│   ├── LESSONS.md            # 8 个真实踩坑复盘  ⭐
│   └── TIMELINE.md           # 2 小时分钟级时间线
├── scripts/
│   └── run.sh                # 一键启动服务器
├── requirements.txt
├── Makefile                  # 常用命令快捷入口
├── .gitignore
├── LICENSE
└── README.md
```

## 致谢

- 面试题目由蚂蚁集团 AI Coding 团队出题,这里不复述题目原文,避免影响后续考生。
- 考试过程中使用了 Anthropic Claude 作为「战略层参谋」,在蚂蚁自研 CodeFuse 中完成代码生成。工作流细节见 [docs/AI_WORKFLOW.md](docs/AI_WORKFLOW.md)。

## License

MIT — 详见 [LICENSE](LICENSE)。
