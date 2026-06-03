# MEMORY.md - Assist Everything BetterAndBetter 的长期记忆

每次启动 Workbench 或 Agent Chat 时，优先把这里当作长期记忆说明来读。惜字如金，只保留会影响后续实现、评测和演示的关键事实。

## 索引

> `memories/`、`eval/output/` 和项目源码里的记忆相关文件索引。需要详情时再读取对应文件。

- `memories/workbench/`：Workbench Agent Chat 的本地长期记忆与配置
  - `_state.json`：本地记忆版本号
  - `_events.jsonl`：记忆新增、删除、降权、归档等事件日志
  - `_privacy.json`：Workbench 隐私设置，git-ignored
  - `_backend.json`：长期记忆后端配置，git-ignored，包含 Mem0 开关和本地密钥
  - `mem_*.md`：单条本地记忆，带 JSON front matter 和 evidence
- `eval/output/latest/`：最近一次 Eval Harness 报告
  - `eval_report.json`：结构化评分、trace、user effort、memory snapshots
  - `eval_report.md`：简要报告
- `eval/output/history/`：历史 eval 运行记录
- `assist_everything_betterandbetter_skill/`
  - `skill.py`：核心记忆提取、应用、隐私拦截、profile/layers/snapshot
  - `memory.py`：本地 Markdown MemoryStore
  - `mem0_backend.py`：火山引擎 Mem0-compatible REST 适配层
- `evalharness/`
  - `server.py`：Workbench API 和内嵌 HTML UI
  - `agent.py`：HarnessAgent，对接工具、LLM rewrite 和 trace
  - `quality.py`：用户费力度、语义违规、污染记忆等质量规则
  - `runner.py`：preset cases 运行入口

## 用户偏好

- **Workbench UI 结构**：
  - 顶部主 tab：`Agent Chat`、`History Evals`、`统计`、`设置`
  - 设置页中，`Agent 配置`、`soul.md`、`memory.md`、`Workbench Memory`、`隐私设置` 必须放在同一组 tab 内，不要把 Agent 配置或隐私设置另起一行。
  - `Agent 配置` 只允许用户切换“长期记忆后端”；Mem0 项目名、公网地址、User ID、App ID、API Key 以 `.env` 或本地 git-ignored 配置为准，不在页面中编辑。
  - `隐私设置` 是用户可编辑项，逐行填写，命中后不会写入长期记忆。

- **长期记忆后端**：
  - 默认使用本地 Markdown / JSON 存储，确保 eval 可复现、trace 可审计。
  - 需要提供开关切换到火山引擎记忆库 Mem0。
  - 切到 Mem0 时，本地仍保留 trace/audit 状态；新增长期记忆 mirror 到 Mem0，检索时合并 Mem0 search 结果。
  - Mem0 API key 不写入源码，不进入 git diff，不在浏览器明文回显。
  - Workbench 本地配置文件：`memories/workbench/_backend.json`，目录已 git-ignored。

- **Mem0 项目信息**：
  - 项目名称：`test-self-improving-202606`
  - 公网连接地址：`https://mem0-cnlfjzigaku8gczkzo.mem0.volces.com:8000`
  - Workbench 默认 `user_id`：`workbench-user`
  - Workbench 默认 `app_id`：`test-self-improving-202606`
  - API key 只允许存在于 `.env` 或 git-ignored 本地配置中。

- **评测稳定性优先**：
  - 任何 UI 或后端改动后，都要确认 `python3 -m evalharness.cli run --output /tmp/assist-eval-check --judge heuristic --agent local` 仍保持 7 cases、平均分 100、全部 >= 90。
  - 新能力优先做成可选开关，不破坏默认 local deterministic 路径。

## 事件日志

- 2026-06-03：基于 `workbench-eval-refactor` 创建并推送 `workbench-eval-refactor_v2`。
- 2026-06-03：启动本地 Workbench 服务，地址 `http://127.0.0.1:8787/`。
- 2026-06-03：从 `feature/self_improving_yjw` 迁移优势能力到主项目：三层记忆视图、profile、compact snapshot、privacy report、instant/standard/deep memory mode、置信度分层。
- 2026-06-03：优化设置页，把 `soul.md`、`memory.md`、`Workbench Memory`、`隐私设置` 合并为同一组 tab。
- 2026-06-03：增加长期记忆后端开关，支持本地存储与火山引擎 Mem0 切换；Mem0 配置在设置页可编辑。
- 2026-06-03：将 Workbench 演示界面从 `evalharness/server.py` 内嵌字符串拆分为 `evalharness/static/workbench.html`、`workbench.css`、`workbench.js`，`server.py` 只负责 API 和静态文件返回。
- 2026-06-03：设置页把 `Agent 配置` 也并入同一组 tab；Agent 配置只保留长期记忆后端开关，其余 Mem0 参数以 `.env` 或本地配置为准。
- 2026-06-03：修复火山 Mem0 连接检查：该实例不支持 `/v3/memories/search/`，改为优先使用 `POST /v2/memories/search/`、回退 `POST /v1/memories/search/`，且 search payload 必须包含顶层 `user_id`。

## 记忆

> 沉淀过的认知。决策、教训、重要偏好、关键上下文。`memories/` 是运行数据，这里是定稿。

- **核心定位**：这是一个“授权式协作记忆 + 自成长 Eval Workbench”。它既要能真实聊天，也要能用 trace 证明记忆提取、应用、更新、删除、用户控制和结果质量。

- **记忆生命周期**：
  - `active`：可检索、可应用
  - `superseded`：被新反馈降权或替代，保留审计，不默认应用
  - `archived`：归档保留，不默认应用
  - `deleted`：用户删除后不得检索和应用

- **三层记忆模型**：
  - L0 即时交互层：`[q]`、问候、轻量消息不加载长期记忆
  - L1 画像快照层：标准任务加载 compact snapshot 和 matching active memories
  - L2 长期审计层：深度/历史请求加载 snapshot、matching memories、event log

- **隐私策略**：
  - 命中隐私设置项时，动作应为 `reject`，detail 使用 `[redacted]`，不得写入长期记忆。
  - 默认隐私项：密码、token、密钥、身份证、银行卡、验证码、隐私不要记。
  - 用户可在设置页追加隐私项，例如家庭住址、手机号等。

- **评分与费力度**：
  - 六维评分：可复测、有效记忆提取、记忆应用、更新淘汰、透明可控、结果质量。
  - 用户费力度是加法模型：轮数、输入长度、追问、重复说明、纠错、不满、语义违规增加成本；正确应用记忆和有效记忆变化计入 saved effort。

- **Workbench 设置页**：
  - `soul.md`：Agent 人设与表达约束，只读展示。
  - `memory.md`：长期记忆说明，只读展示。
  - `Workbench Memory`：当前 Workbench MemoryStore snapshot，只读展示。
  - `隐私设置`：可编辑保存，影响当前 Agent Chat 的记忆写入拦截。

## 教训（LRN）

- **不要把复杂 UI 长期塞在 `server.py` 里**：
  - Workbench HTML/CSS/JS 已拆到 `evalharness/static/`。
  - `server.py` 只负责 API、`/` 返回 `workbench.html`、`/static/...` 返回静态资源。
  - 后续 UI 改动优先改静态文件，不要再把大段 HTML 字符串塞回 Python。

- **远程记忆后端不能替代本地 trace**：
  - Eval Harness 需要本地 snapshots、events、memory status 来评分和解释。
  - 即使使用 Mem0，也要保留本地审计层，否则删除复测、状态迁移、用户费力度分析会失去证据。

- **火山 Mem0 API 路径兼容**：
  - 不要硬编码只用 `/v3/memories/search/`，当前火山实例会返回 404。
  - 搜索应优先尝试 `/v2/memories/search/`，再回退 `/v1/memories/search/`。
  - search payload 需要顶层 `user_id`；只放在 `filters` 中会触发 `At least one of 'user_id', 'agent_id', or 'run_id' must be specified.`。
  - 新增记忆优先使用 `POST /v1/memories/`。
  - 新增记忆如果使用 `infer: false`，必须同时传 `async_mode: false`；否则火山实例会返回 `Async mode is disabled if infer is False.`

- **API key 不能进入源码和前端明文**：
  - `.env.example` 只放变量名，不放真实 key。
  - `/api/settings` 只能返回 `api_key_configured: true/false`。
  - Workbench 本地配置在 `memories/` 下，该目录 git-ignored。

- **功能迁移要保护默认路径**：
  - 默认 backend 必须是 `local`。
  - Mem0 失败不应影响本地 eval。
  - 远程同步失败应体现在 action 的 `remote.ok=false`，但不阻断本地记忆和 trace。

- **设置页交互约束**：
  - 用户明确要求“隐私设置”放在 tab 里，不要另起一行。
  - 隐私设置保存后应立即应用到当前 Workbench Agent Chat。
