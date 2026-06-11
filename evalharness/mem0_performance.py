from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any


DEMO_USER_ID = "workbench-demo-large-memory"
LATEST_PERFORMANCE_REPORT = Path("eval/output/latest/mem0_performance_demo.json")

ALLOWED_ENGINES = {"mem0_hosted", "mem0_sdk"}
ALLOWED_MODES = {"dry_run", "real_run"}
ALLOWED_RUN_SCALES = {1000, 10000, 50000}

_BASE_TIME = "2026-06-11T00:00:00+00:00"
_SCOPES = [
    "life_family_travel",
    "work_project_planning",
    "learning_research",
    "health_routine",
    "finance_budgeting",
]
_TOPICS = [
    "Shanghai family trip",
    "quarterly roadmap",
    "Python study plan",
    "morning workout",
    "monthly spending review",
    "team meeting notes",
    "home organization",
    "reading backlog",
]
_TAGS = [
    ["travel", "family", "planning"],
    ["work", "roadmap", "priority"],
    ["learning", "python", "practice"],
    ["health", "habit", "routine"],
    ["finance", "budget", "review"],
]
_QUERY_TEMPLATES = [
    "What should I remember about {topic}?",
    "Find my latest preference for {topic}.",
    "Which saved notes help with {topic}?",
    "Summarize relevant memory for {topic}.",
]


def generate_demo_memories(scale: int, seed: int = 42) -> list[dict[str, Any]]:
    _validate_positive_int("scale", scale)
    rng = random.Random(seed)
    memories = []
    for index in range(1, scale + 1):
        scope_index = rng.randrange(len(_SCOPES))
        topic = rng.choice(_TOPICS)
        created_day = 1 + (index % 28)
        updated_hour = index % 24
        created_at = f"2026-05-{created_day:02d}T08:00:00+00:00"
        updated_at = f"2026-06-11T{updated_hour:02d}:00:00+00:00"
        memories.append(
            {
                "id": f"demo_mem_{index:06d}",
                "content": f"Remember that {topic} preference #{index} favors option {rng.randint(1, 9)}.",
                "scope": _SCOPES[scope_index],
                "tags": list(_TAGS[scope_index]),
                "created_at": created_at,
                "updated_at": updated_at,
                "status": "active",
            }
        )
    return memories


def generate_demo_queries(query_count: int, seed: int = 42) -> list[str]:
    _validate_positive_int("query_count", query_count)
    rng = random.Random(seed)
    queries = []
    for _ in range(query_count):
        template = rng.choice(_QUERY_TEMPLATES)
        queries.append(template.format(topic=rng.choice(_TOPICS)))
    return queries


def run_performance_demo(
    engine: str,
    mode: str,
    scale: int,
    query_count: int = 20,
    client: Any | None = None,
) -> dict[str, Any]:
    _validate_engine(engine)
    _validate_mode(mode)
    _validate_run_scale(scale)
    _validate_positive_int("query_count", query_count)

    memories = generate_demo_memories(scale)
    queries = generate_demo_queries(query_count)
    run_id = _run_id(engine, mode, scale, query_count)
    started_at = _BASE_TIME

    if mode == "real_run":
        phases, metrics, examples, reset = _run_real_demo(client, memories, queries)
    else:
        phases, metrics, examples, reset = _run_dry_demo(memories, queries)

    report = {
        "ok": len(reset["errors"]) == 0,
        "run_id": run_id,
        "engine": engine,
        "mode": mode,
        "scale": scale,
        "demo_user_id": DEMO_USER_ID,
        "started_at": started_at,
        "finished_at": _finished_at(scale, query_count),
        "phases": phases,
        "metrics": metrics,
        "examples": examples,
        "reset": reset,
    }
    save_latest_report(report)
    return report


def latest_report() -> dict[str, Any] | None:
    if not LATEST_PERFORMANCE_REPORT.exists():
        return None
    return json.loads(LATEST_PERFORMANCE_REPORT.read_text(encoding="utf-8"))


def save_latest_report(report: dict[str, Any]) -> None:
    LATEST_PERFORMANCE_REPORT.parent.mkdir(parents=True, exist_ok=True)
    LATEST_PERFORMANCE_REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run_dry_demo(
    memories: list[dict[str, Any]],
    queries: list[str],
) -> tuple[list[dict[str, Any]], dict[str, float], list[dict[str, Any]], dict[str, Any]]:
    write_ms = max(1.0, len(memories) * 0.18)
    search_latencies = [_search_latency_ms(query, len(memories)) for query in queries]
    search_total_ms = sum(search_latencies)
    examples = [
        {
            "query": query,
            "latency_ms": round(latency, 3),
            "top_k": _dry_top_k(query, memories),
        }
        for query, latency in zip(queries, search_latencies, strict=True)
    ]
    phases = [
        {"name": "generate", "status": "ok", "count": len(memories), "duration_ms": round(len(memories) * 0.03, 3)},
        {"name": "write", "status": "simulated", "count": len(memories), "duration_ms": round(write_ms, 3)},
        {"name": "search", "status": "simulated", "count": len(queries), "duration_ms": round(search_total_ms, 3)},
        {"name": "reset", "status": "simulated", "count": len(memories), "duration_ms": round(len(memories) * 0.02, 3)},
    ]
    metrics = {
        "write_qps": round(len(memories) / (write_ms / 1000.0), 3),
        "search_qps": round(len(queries) / (search_total_ms / 1000.0), 3),
        "search_p50_ms": round(_percentile(search_latencies, 50), 3),
        "search_p95_ms": round(_percentile(search_latencies, 95), 3),
        "memory_count": float(len(memories)),
        "query_count": float(len(queries)),
    }
    reset = {"attempted": True, "mode": "dry_run", "deleted": len(memories), "errors": []}
    return phases, metrics, examples, reset


def _run_real_demo(
    client: Any | None,
    memories: list[dict[str, Any]],
    queries: list[str],
) -> tuple[list[dict[str, Any]], dict[str, float], list[dict[str, Any]], dict[str, Any]]:
    if client is None:
        raise ValueError("real_run mode requires a client")
    # Keep the real-run surface minimal for Task 1; later tasks can wire Mem0 APIs here.
    return _run_dry_demo(memories, queries)


def _dry_top_k(query: str, memories: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    ranked = []
    for memory in memories:
        score = _stable_score(query, memory["id"])
        ranked.append((score, memory["updated_at"], memory))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [
        {
            "id": memory["id"],
            "content": memory["content"],
            "scope": memory["scope"],
            "score": round(score, 6),
            "updated_at": memory["updated_at"],
            "retrieval_rank_strategy": "score_time",
        }
        for score, _, memory in ranked[:limit]
    ]


def _search_latency_ms(query: str, scale: int) -> float:
    score = _stable_score(query, str(scale))
    return 12.0 + (scale / 1000.0) * 2.5 + score * 18.0


def _stable_score(*parts: str) -> float:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:12], 16) / float(0xFFFFFFFFFFFF)


def _percentile(values: list[float], percentile: int) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = round((len(ordered) - 1) * (percentile / 100.0))
    return ordered[index]


def _run_id(engine: str, mode: str, scale: int, query_count: int) -> str:
    digest = hashlib.sha256(f"{engine}:{mode}:{scale}:{query_count}".encode("utf-8")).hexdigest()
    return f"mem0-perf-{digest[:12]}"


def _finished_at(scale: int, query_count: int) -> str:
    seconds = max(1, round(scale * 0.0002 + query_count * 0.02))
    return f"2026-06-11T00:00:{seconds:02d}+00:00"


def _validate_engine(engine: str) -> None:
    if engine not in ALLOWED_ENGINES:
        raise ValueError(f"engine must be one of {sorted(ALLOWED_ENGINES)}")


def _validate_mode(mode: str) -> None:
    if mode not in ALLOWED_MODES:
        raise ValueError(f"mode must be one of {sorted(ALLOWED_MODES)}")


def _validate_run_scale(scale: int) -> None:
    if scale not in ALLOWED_RUN_SCALES:
        raise ValueError(f"scale must be one of {sorted(ALLOWED_RUN_SCALES)}")


def _validate_positive_int(name: str, value: int) -> None:
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
