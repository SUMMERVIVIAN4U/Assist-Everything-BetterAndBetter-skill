# V5 Workbench Roadmap

## 目标

V5 Workbench 的目标是把比赛演示从“本地 deterministic 测试台”收敛成“真实 LLM Agent 的记忆演示与评估工作台”。

核心原则：

- Workbench 只展示真实 LLM mode 的 Chat 和 Eval。
- 本地 deterministic/local agent 只作为工程 smoke/contract test 保留在 CLI/API，不进入 Workbench 主流程。
- Agent Chat、History Evals、统计、设置仍是主功能模块。
- Preset case 与女朋友生日礼物 demo 合并为统一的 Scenario Library。
- 超大记忆性能演示暂时后置，作为独立的 Memory Scale Evaluation 规划。

## 1. Workbench 主流程收敛

### 当前问题

当前 Workbench 顶部仍允许选择：

```text
local agent
auto agent
offline judge / heuristic judge
auto judge
```

这会让评委误以为本地 deterministic agent 就是最终产品能力。实际它只适合验证 memory state machine 是否跑通，不适合作为比赛演示或真实 eval。

### 改造方向

Workbench UI 中移除：

```text
local agent
auto agent
offline judge
heuristic judge
auto judge
```

Workbench 主流程只保留真实 LLM：

```text
LLM Agent Chat
LLM Eval
LLM Judge
```

本地 deterministic 能力保留在工程入口：

```bash
python3 -m evalharness.cli run --agent local --judge heuristic
```

但文档和 UI 必须明确标注：

```text
local + heuristic 是 smoke/contract test，不是比赛评分或产品演示。
```

## 2. Provider 抽象

### 当前问题

V4 当前 UI 和代码主要围绕旧 provider 命名。比赛提交版统一使用 MiniMax 命名，避免 Provider 配置和页面展示不一致。

### 改造方向

Workbench 使用统一的 Provider 概念：

```text
LLM Provider
Model
Endpoint configured
API key configured
Timeout
```

首期实现：

```text
MiniMax provider
```

Roadmap 预留：

```text
OpenAI-compatible provider
External command provider
```

UI 展示规则：

- 只显示 provider 是否配置成功。
- 不展示 API key 明文。
- 未配置真实 LLM 时，Agent Chat 和 Eval 按钮给出配置提示，而不是自动退回 local deterministic。

## 3. 主模块保持不变

V5 不拆 Demo Mode / Eval Mode / Debug Mode。主导航保持：

```text
Agent Chat
History Evals
统计
设置
```

Performance Demo 暂时保留但放到最后，并降低主视觉优先级。

### Agent Chat

Agent Chat 是比赛主舞台：

- 使用真实 LLM agent。
- 每轮仍先调用 memory skill，产出 memory actions、snapshot、current memory。
- 最终回复由真实 LLM 基于 tool trace 生成。
- 用户可点击 `Run Eval` 对当前对话进行真实 LLM eval。

Agent Chat 的右侧面板应突出：

```text
当前记忆后端
active memory
本轮 applied memory
memory actions
授权状态
```

### History Evals

History Evals 展示所有历史 eval：

```text
Agent Chat eval
Scenario Library eval
Preset/固定脚本 replay eval
```

这里不再展示 local deterministic 的 100 分作为主要证据。

### 统计

统计聚合真实 LLM eval 结果：

```text
平均分
用户费力度下降
记忆命中次数
记忆更新/删除次数
失败率
场景覆盖
```

### 设置

设置保留：

```text
LLM Provider 配置状态
记忆功能开关
长期记忆后端选择
Local Memory
Mem0 Hosted
隐私设置
```

## 4. Scenario Library：合并 Preset Case 和女朋友 Demo

### 当前问题

Preset case 和“女朋友生日礼物 demo”本质上都是固定场景脚本。分成两个概念会增加认知负担。

### 改造方向

统一为：

```text
Scenario Library
```

场景分组：

```text
Gift Birthday
  女朋友生日礼物，比赛主噱头

General Memory
  家庭旅行、项目周报、学习计划、文献综述
```

每个 scenario 都包含：

```text
场景标题
目标
脚本轮次
预期记忆动作
预期第二轮用户费力度下降
评估维度
```

### Run Scenario 的含义

点击 `Run Scenario` 代表：

```text
使用真实 LLM agent 跑完整脚本
使用真实 LLM judge 评分
结果进入 History Evals
```

不再表示：

```text
local deterministic agent + heuristic judge
```

### 女朋友生日礼物主场景

建议主脚本：

```text
reset memory
帮我给女朋友选个生日礼物。
预算1000左右；她喜欢紫色；如果是首饰，她喜欢玫瑰金；以前送过玫瑰金项链，送过的不要再送。
同意保存
展示当前记忆
给我一个礼物推荐。
不是，我想换个非首饰品类。
那再给一个推荐。
```

要展示的亮点：

- 第一轮用户给出复杂偏好、预算、历史和禁忌。
- 系统不是偷偷保存，而是请求授权。
- 第二轮用户只说短句，系统仍能召回并应用记忆。
- 推荐不重复已送礼物。
- 记忆可展示、可删除、可审计。

## 5. Agent Chat Eval

Agent Chat 里的 `Run Eval` 只允许真实 LLM eval。

评估输入：

```text
当前 Agent Chat session
turn trace
memory actions
snapshots
applied memories
assistant final answers
```

评估输出：

```text
总分
记忆提取
记忆应用
更新与删除
透明度
结果质量
用户费力度
```

必须额外检查：

```text
是否声称保存但 memory_actions 为空
是否把收礼人偏好写成用户本人偏好
是否使用 deleted memory
是否推荐已明确送过/否决/已有的礼物
是否需要用户重复说明已保存信息
```

## 6. Local Memory / Mem0 Hosted 的展示口径

Workbench 设置页只展示两种对外可选记忆后端，避免把比赛主线讲复杂。

### Local Memory

```text
比赛主模式。
可解释、可审计、可复现。
适合展示 memory actions、版本、snapshot、删除和授权。
```

### Mem0 Hosted

```text
托管长期记忆服务。
适合展示跨会话、跨客户端、较大规模持久化。
依赖网络和 API key，不作为比赛唯一主链路。
```

推荐给评委的简化说明：

```text
Local = 比赛主 demo，证明记忆可信和可控。
Mem0 Hosted = 扩展 demo，证明可以接托管长期记忆。
```

## 7. Performance Demo 后置规划

### 当前判断

超大记忆性能演示当前不够直观，不适合作为比赛主舞台。

它应该作为完全单独的一条线：

```text
Memory Scale Evaluation
```

### 后续设计目标

检查它是否可以和 Agent Chat eval 统一，只是 eval 维度不同。

Agent Chat eval 评估：

```text
任务质量
记忆使用
用户费力度
透明度
```

Performance eval 评估：

```text
写入吞吐
检索 P50/P95
错误率
TopK 排序稳定性
reset 是否 scoped
Local / Mem0 Hosted 对比
```

设计要求：

- Performance Demo 不影响 Agent Chat 当前记忆。
- Real Run 必须使用隔离 demo user。
- Reset 只能清理 demo user。
- 结果进入 History Evals 或独立 Performance History，但必须明确这是性能评估，不是任务质量评估。

## 8. 实施阶段

### Phase 1：去掉 Workbench 的本地 deterministic 主入口

- UI 移除 local/auto agent 选项。
- UI 移除 heuristic/offline/auto judge 选项。
- Agent Chat、Run Eval、Run Scenario 默认要求真实 LLM。
- 未配置 LLM 时提示配置，不退回 local。
- CLI local/heuristic 保留为工程测试。

### Phase 2：Provider 抽象

- Workbench 文案从旧 provider 命名改成 MiniMax。
- MiniMax 作为比赛提交版默认 provider。
- 为 OpenAI-compatible provider 预留兼容结构。
- Provider health check 替代单一厂商检查。

### Phase 3：Scenario Library

- 把 preset case 和 gift demo 合并。
- 女朋友生日礼物设为默认重点 scenario。
- Run Scenario 只走真实 LLM chat + LLM eval。
- History Evals 展示 scenario eval 和 Agent Chat eval。

### Phase 4：Memory Evidence 面板

- Agent Chat 右侧突出 active memory、applied memory、memory actions。
- 展示授权状态和保存结果。
- 展示是否命中 Local / Mem0 后端。

### Phase 5：Performance Evaluation 单独设计

- 降低 Performance Demo 在主导航中的优先级。
- 重新设计为 Memory Scale Evaluation。
- 与 Mem0 Hosted 设置联动。
- 后续考虑统一进入 History Evals。

## 9. 验收标准

### Workbench 产品验收

- 页面上不再出现 local agent、offline judge、heuristic judge。
- 未配置真实 LLM 时，Chat/Eval 给出明确错误和配置入口。
- Agent Chat 可以使用真实 LLM 完成对话，并显示 memory trace。
- 当前对话可以 Run Eval，结果进入 History Evals。
- Scenario Library 可以跑女朋友生日礼物场景。
- Scenario Library 的结果明确标注为真实 LLM replay eval。

### 工程回归验收

- CLI local/heuristic 仍能跑 smoke test。
- 原有 MemoryStore、reset、show、delete、privacy、profile、snapshot、layers 不被破坏。
- Local Memory、Mem0 Hosted 两种后端设置仍可查看。
- Performance Demo 不影响 Agent Chat 记忆。

### 比赛演示验收

- 主故事是女朋友生日礼物。
- 第一轮长对话形成可审计记忆。
- 用户授权后才保存长期记忆。
- 第二轮短输入能召回记忆并生成更贴合的推荐。
- Workbench 能证明使用了哪些记忆。
- 评委不会看到 deterministic local agent 被当作真实智能体。

## 10. 明确不做

本阶段不做：

```text
不删除 CLI local/heuristic
不把 Performance Demo 放到主舞台
不新增 Demo Mode / Eval Mode 两套导航
不把女朋友礼物写死成单独 bot
不把 API key 明文展示在 UI
```
