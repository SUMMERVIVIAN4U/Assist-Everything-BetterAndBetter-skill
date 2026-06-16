# V5 Workbench 改造与 Skill 使用路线说明

本文用于 V5 分支的 Workbench 改造基线：先说明当前 skill 的结构、启动方式、授权与记忆管理流程，再说明比赛演示时如何让评委理解 Local 和 Mem0 Hosted 两种记忆模式。

## 1. 当前 Skill 的结构

当前系统不是一个独立聊天机器人，而是一套协作记忆 skill runtime，加上 eval/workbench harness。

核心分层：

```text
AssistSkill
  记忆提取、授权、召回、更新、删除、隐私拦截、profile/snapshot/layers 视图

MemoryStore
  本地 Markdown/JSON 记忆存储、事件日志、版本状态

Mem0 Backend
  可选长期记忆后端：Hosted REST

MemoryToolbox
  把 AssistSkill 包成 harness agent 可调用的 tool

HarnessAgent
  Workbench Agent Chat 的对话壳；先跑 memory tool，再交给 MiniMax 改写最终回复

Workbench
  浏览器界面：真实 LLM Agent Chat、Scenario Library / History Evals、统计、设置、Memory Scale Eval
```

记忆实体类型：

```text
preference    偏好
constraint    约束、禁忌、边界
workflow      可复用工作方式
decision      当前任务已选方案
history       已发生事实
context_fact  背景事实
```

记忆生命周期：

```text
active        正常参与召回
superseded    被新反馈替代，保留审计
archived      归档，不默认参与召回
deleted       删除，不允许参与召回
```

当前 V4/V5 的重要设计点：

```text
1. 默认本地记忆为主，结构清晰，可审计，适合比赛演示。
2. Mem0 是可选后端，适合展示持久化和大规模长期记忆能力。
3. 本地 deterministic agent 只用于 CLI smoke/contract 测试，不进入 Workbench 主流程。
4. Workbench 只保留真实 LLM provider；比赛提交版默认 provider 是 MiniMax，agent chat 和 eval 都不允许静默回退到本地草稿或 heuristic judge。
```

## 2. Codex / 命令行使用路线

### 2.1 安装/放置 Skill

如果是在本仓库内使用，直接在仓库根目录运行命令即可。runtime package 位于：

```text
assist_everything_betterandbetter_skill/
```

Codex skill 指令文件位于：

```text
skill/SKILL.md
```

如果要把这份 skill 指令安装到 Codex 的全局 skills 目录，可以复制：

```bash
mkdir -p ~/.codex/skills/assist-everything-betterandbetter-skill
cp skill/SKILL.md ~/.codex/skills/assist-everything-betterandbetter-skill/SKILL.md
```

注意：这只安装 Codex 触发说明。真正运行 Workbench、CLI、eval 仍需要在本仓库根目录执行 Python 命令。

### 2.2 本地 JSON/Markdown 记忆配置

比赛和本地调试建议默认使用 local：

```dotenv
ASSIST_MEMORY_BACKEND=local
ASSIST_MEMORY_PERSIST=1
ASSIST_MEMORY_DIR=memories/default
ASSIST_MEMORY_ENABLED=1
```

本地记忆文件默认 git-ignore，不会提交用户隐私数据。

### 2.3 CLI 启动和使用

一次性处理一条消息：

```bash
python3 -m assist_everything_betterandbetter_skill.cli chat "以后写材料请先给结论，再列风险和下一步。"
```

查看画像：

```bash
python3 -m assist_everything_betterandbetter_skill.cli profile
```

查看紧凑快照：

```bash
python3 -m assist_everything_betterandbetter_skill.cli snapshot
```

查看三层记忆：

```bash
python3 -m assist_everything_betterandbetter_skill.cli layers
```

查看隐私报告：

```bash
python3 -m assist_everything_betterandbetter_skill.cli privacy
```

记忆管理命令：

```bash
python3 -m assist_everything_betterandbetter_skill.cli memory "展示当前记忆"
python3 -m assist_everything_betterandbetter_skill.cli memory "删除 紫色"
python3 -m assist_everything_betterandbetter_skill.cli memory "降权 番茄钟"
python3 -m assist_everything_betterandbetter_skill.cli memory "归档 老规则"
python3 -m assist_everything_betterandbetter_skill.cli memory "reset memory"
```

### 2.4 对话中的授权流程

系统不会默认把所有话都写成长期记忆。当前策略是置信度分层：

```text
reject   隐私或临时内容，不保存
ask      弱信号，需要澄清
propose  中等置信长期记忆，需要用户确认
add      高置信结构化记忆，直接写入
dedupe   重复记忆，不重复写
```

典型流程：

```text
用户：预算1000左右；她喜欢紫色；以前送过玫瑰金项链，送过的不要再送。
系统：我捕捉到这可能是长期偏好，需要你确认后再保存：同意保存 / 拒绝保存。
用户：同意保存
系统：已获授权并保存长期记忆：...
```

用户也可以拒绝：

```text
拒绝保存
```

或者直接管理：

```text
展示当前记忆
删除 她喜欢紫色
画像
快照
三层记忆
隐私报告
```

## 3. Workbench Agent Chat 使用路线

Workbench 只支持真实 LLM 模式。比赛提交版默认配置 MiniMax：

```dotenv
MINIMAX_API_KEY=...
MINIMAX_BASE_URL=https://api.minimax.io/v1
MINIMAX_MODEL=MiniMax-M2.7
MINIMAX_TIMEOUT=60
```

然后启动 Workbench：

```bash
python3 -m evalharness.cli serve --port 8787 --agent minimax
```

打开：

```text
http://127.0.0.1:8787
```

如果没有配置当前选中的 provider，Agent Chat、Run LLM Eval 和 Run Scenarios 会显示配置错误，而不是自动退回本地 deterministic 输出。Check Provider 会发起一次真实轻量 completion，而不是只检查网络连通。

Workbench 推荐演示顺序：

```text
1. Settings
   选择记忆功能开启，长期记忆后端先选 Local。

2. Agent Chat
   跑一段真实对话，例如女朋友生日礼物。

3. 当前 Memory
   展示当前 active memory、版本、后端状态。

4. Run LLM Eval
   对当前 Agent Chat 用真实 LLM judge 评分，结果进入 History Evals。

5. Scenario Library / History Evals
   点击 Run Scenarios，用真实 LLM agent + 真实 LLM judge 回放通用场景和女朋友生日礼物彩蛋场景。

6. profile / snapshot / layers / privacy
   在 Agent Chat 中直接输入这些命令，展示可解释记忆视图。
```

女朋友生日礼物演示脚本建议：

```text
reset memory
帮我给女朋友选个生日礼物。
预算1000左右；她喜欢紫色；如果是首饰，她喜欢玫瑰金；以前送过玫瑰金项链，送过的不要再送。
同意保存
展示当前记忆
画像
三层记忆
给我一个礼物推荐。
```

这个脚本在 Workbench 中必须使用真实 LLM provider。Local agent 只保留在 CLI 工程测试中，不作为产品演示入口。

## 4. Eval 使用路线

本地 deterministic eval：

```bash
python3 -m evalharness.cli run --judge heuristic --agent local
```

它的意义是 smoke/contract test：

```text
验证 reset/show/delete/update 是否跑通
验证 memory_actions 和 snapshot 是否完整
验证 active/deleted 生命周期是否正确
验证 trace schema 是否稳定
```

它不等价于真实产品能力评分。当前 heuristic 100 分只能说明预设脚本在规则评测器下通过。

真实 LLM replay eval：

```bash
python3 -m evalharness.cli run --agent minimax --judge minimax
```

这更接近比赛效果，因为 agent 和 judge 都由真实 LLM 参与。V5 Workbench 已把这两类结果明确区分：

```text
Engineering Smoke Check   CLI: local + heuristic
Workbench Scenario Eval   Workbench: selected provider agent + selected provider judge
Workbench Chat Eval       Workbench: 当前真实对话 + selected provider judge
Human Review              评委看 trace、memory snapshot、最终回复和用户费力度
```

## 5. Local / Mem0 Hosted 怎么解释给评委

为了让评委简单理解，建议只讲两个模式：

### 5.1 Local Memory：比赛主模式

一句话：

```text
Local 是可解释、可审计、可复现的本地记忆状态机，适合比赛主 demo。
```

适用场景：

```text
比赛现场演示
隐私敏感
需要展示 memory_actions、版本、snapshot
需要稳定复现 eval
不希望网络/API 影响结果
```

优点：

```text
结构透明
事件日志清晰
可删除、可降权、可归档
不依赖远端服务
评委能直接看到每条记忆怎么产生
```

限制：

```text
不适合证明超大规模长期记忆
本地 deterministic agent 不代表真实 LLM 智能
```

### 5.2 Mem0 Hosted：托管长期记忆服务

一句话：

```text
Mem0 Hosted 是远端长期记忆服务，适合展示跨会话、跨客户端、较大规模持久化。
```

适用场景：

```text
需要持久化到外部服务
多客户端共享同一个 user/app 记忆
希望使用托管检索、存储、扩展能力
演示大规模记忆性能
```

优点：

```text
服务化
跨环境可用
适合长期运行
适合展示 1000/10000/50000 规模 demo
```

限制：

```text
依赖网络和 API key
自动提取可解释性弱于本地 MemoryItem
比赛现场不应把它作为唯一主链路
```

## 6. 推荐给评委的模式说明

不要把模式讲复杂。建议用这张口径：

```text
Local:
  比赛主演示。证明记忆系统可解释、可审计、可控。

Mem0 Hosted:
  扩展演示。证明同一套 skill 可以接托管长期记忆，支持更大规模和跨端持久化。
```

Workbench 改造时只暴露这两种模式：

```text
比赛推荐：Local Memory + MiniMax Agent
规模演示：Mem0 Hosted + Performance Demo
```

## 7. V5 Workbench 已落地改造

V5 分支的 Workbench 改造口径：

```text
1. 不再单独做 Demo Mode / Eval Mode，主模块保持 Agent Chat、History Evals、统计、设置。
2. Agent Chat 是真实 LLM chat，可一键 Run LLM Eval，结果进入 History Evals。
3. Preset case 和女朋友生日礼物 demo 合并为 Scenario Library，点击 Run Scenarios 即运行真实 LLM replay eval。
4. Workbench 页面不出现 local agent、offline judge、heuristic judge 选择。
5. 当前 Memory 面板显示：当前后端、active memory、版本、Mem0 状态。
6. Memory Scale Eval 暂放最后，作为后续与 Mem0 大规模能力结合的单独线。
7. local + heuristic 只在 CLI 中作为 Engineering Smoke Check，不作为比赛主视觉。
```

最终比赛叙事：

```text
第一次长对话：用户给出复杂偏好、预算、禁忌和历史。
系统授权保存：不是偷偷记，用户可确认、查看、删除。
第二次短输入：用户只说“再给一个推荐”，LLM agent 自动使用记忆。
Workbench trace：证明它用了哪些记忆、没有用已删除记忆、用户费力度下降。
Mem0 扩展：同一套记忆能力可以接远端长期记忆和大规模检索。
```
