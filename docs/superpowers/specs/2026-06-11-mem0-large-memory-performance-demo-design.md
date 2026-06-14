# Mem0 超大记忆性能演示设计

## 背景

Workbench 已支持三种互斥记忆引擎：

- `LocalMemoryStore`：本地 JSON/Markdown，保留透明审计链路
- `HostedMem0Client`：托管或 REST 兼容 Mem0
- `Mem0SdkClient`：开源 `mem0ai` Python SDK 本地库模式

当前项目还缺一个面向演示的端到端能力：在“超大记忆”场景下，直观展示 Mem0 记忆准备、写入、检索、排序、重置和报告能力。该能力应服务比赛演示和本地验证，不能污染当前 Agent Chat 记忆，也不能默认触发高成本真实写入。

## 目标

新增 Workbench 顶层 `Performance Demo` Tab，提供一键自动化演示：

1. 生成可复现的大规模记忆数据集。
2. 执行写入、检索、统一排序、报告生成和安全清理流程。
3. 可视化展示阶段进度、关键指标和 Top-K 命中样例。
4. 默认使用 Dry Run，真实 Mem0 调用必须显式选择。
5. Real Run 使用独立 demo 用户隔离，不触碰当前 Agent Chat 的用户记忆。

## 非目标

- 不把 Performance Demo 混入 Agent Chat 主流程。
- 不默认向真实 Mem0 写入大量数据。
- 不用真实用户记忆作为压测语料。
- 不承诺代表生产级基准测试结果；该功能是可复现演示和本地诊断工具。
- 不新增外部 SaaS 品牌露出，UI 使用 `Mem0`、`Mem0 SDK` 等中性名称。

## 用户体验

Workbench 顶层导航新增：

- `Agent Chat`
- `History Evals`
- `统计`
- `设置`
- `Performance Demo`

`Performance Demo` 页面分三块：

- 左侧配置区：
  - 记忆引擎：`Mem0` 或 `Mem0 SDK`
  - 运行模式：`Dry Run` 或 `Real Run`
  - 数据规模：`1k`、`10k`、`50k`
  - Query 轮数：默认 20
  - `Run Demo` 按钮
  - `Reset Demo Memory` 按钮

- 中间实时进度区：
  - 阶段：生成数据、写入、检索、排序、生成报告、清理
  - 进度条和阶段状态
  - 指标卡片：写入 QPS、P50 latency、P95 latency、错误率
  - 简单时间线或阶段耗时条

- 右侧证据区：
  - Top-K 命中样例
  - `score + time` 排序结果
  - demo user_id
  - reset/delete 结果
  - JSON 报告下载或复制入口

## 数据隔离

Performance Demo 必须使用独立用户 ID：

```text
workbench-demo-large-memory
```

Real Run 写入、检索和删除都必须带该 user_id。Reset 只清理 demo user_id 下的记忆。不得调用全局删除，不得复用 `MEM0_USER_ID` 或当前 Agent Chat 的 user_id 作为 demo user_id。

Dry Run 不访问远端后端。它通过内存模拟器生成写入和检索指标，用于演示完整流程、UI 状态和报告结构。

## 数据集

数据集由确定性生成器产生，输入参数包括：

- `scale`: `1000 | 10000 | 50000`
- `seed`: 默认固定值
- `domains`: 旅行、工作汇报、学习计划、研究综述、购物偏好
- `noise_ratio`: 默认 0.35

每条记忆包含统一结构：

```json
{
  "id": "demo_mem_000001",
  "content": "用户带 3-4 岁孩子去上海时优先室内、少走路。",
  "scope": "life_family_travel",
  "tags": ["上海", "亲子", "少走路"],
  "created_at": "2026-06-11T00:00:00+00:00",
  "updated_at": "2026-06-11T00:00:00+00:00"
}
```

Query 集合同样确定性生成，覆盖目标主题和干扰主题。报告中保留部分样例，不展示完整大数据集。

## 后端 API

新增 API：

- `POST /api/mem0-performance-demo/run`
  - 入参：engine、mode、scale、query_count
  - 出参：完整报告

- `POST /api/mem0-performance-demo/reset`
  - 清理 demo user_id
  - 出参：found_count、deleted_count、errors

- `GET /api/mem0-performance-demo/latest`
  - 返回最近一次报告

第一版可以采用同步请求，限制默认规模为 `1k`。后续如 10k/50k Real Run 阻塞明显，再升级为异步 job + polling。

## 报告结构

报告包含：

```json
{
  "run_id": "perf_20260611_001",
  "engine": "mem0_hosted",
  "mode": "dry_run",
  "scale": 10000,
  "demo_user_id": "workbench-demo-large-memory",
  "started_at": "...",
  "finished_at": "...",
  "phases": [
    {"name": "generate", "elapsed_ms": 12, "ok": true},
    {"name": "write", "elapsed_ms": 320, "ok": true},
    {"name": "search", "elapsed_ms": 180, "ok": true}
  ],
  "metrics": {
    "write_qps": 1800.0,
    "search_p50_ms": 42.0,
    "search_p95_ms": 88.0,
    "error_rate": 0.0
  },
  "examples": [
    {
      "query": "上海亲子游少走路",
      "top_k": [
        {"content": "...", "score": 0.91, "updated_at": "..."}
      ]
    }
  ],
  "reset": {
    "found_count": 10000,
    "deleted_count": 10000,
    "errors": []
  }
}
```

## 排序策略

Demo 报告必须使用现有统一检索排序策略：

1. 只保留 active 候选。
2. 过滤污染或删除内容。
3. 标注 `retrieval_score` 和 `retrieval_rank_strategy=score_time`。
4. 按 `retrieval_score` 降序，再按 `updated_at/created_at` 降序。

Dry Run 和 Real Run 的报告展示同一套排序字段。

## 错误处理

- Mem0 未配置：页面提示配置缺失，Dry Run 仍可运行。
- Mem0 SDK 缺依赖：页面提示当前 Python 环境缺少依赖。
- Real Run 写入失败：报告保留失败阶段、错误率和错误摘要。
- Reset 失败：报告必须展示错误，不声称已清理。
- 请求超时：返回阶段错误，UI 停在失败状态并允许重新运行。

## 测试

需要覆盖：

- 数据集生成确定性：同 seed 同 scale 输出一致。
- Dry Run 报告结构完整，指标非空。
- demo user_id 隔离：Real Run/reset 不使用当前 Agent Chat user_id。
- reset 只调用 demo user_id 范围。
- UI 能渲染 latest report、空状态、失败状态。
- JS 语法检查通过。

## 实施顺序

1. 后端生成器和 Dry Run runner。
2. Performance Demo API。
3. Workbench 新 Tab 和报告渲染。
4. Real Run 接入 HostedMem0Client/Mem0SdkClient。
5. Reset Demo Memory 和错误状态完善。
6. 文档和回归验证。
