from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from assist_everything_betterandbetter_skill.mem0_backend import Mem0Config


DEMO_USER_ID = "workbench-demo-large-memory"
_REPO_ROOT = Path(__file__).resolve().parents[1]
LATEST_PERFORMANCE_REPORT = _REPO_ROOT / "eval/output/latest/mem0_performance_demo.json"

ALLOWED_ENGINES = {"local", "mem0_hosted", "mem0_sdk"}
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


def config_for_demo_user(config: Mem0Config) -> Mem0Config:
    return replace(config, user_id=DEMO_USER_ID)


def reset_demo_memory(client: Any | None) -> dict[str, Any]:
    if client is None:
        return {
            "ok": False,
            "stage": "config",
            "demo_user_id": DEMO_USER_ID,
            "found_count": 0,
            "deleted_count": 0,
            "errors": ["Mem0 client is not configured"],
        }
    client_user_id = getattr(getattr(client, "config", None), "user_id", None)
    if client_user_id != DEMO_USER_ID:
        return _reset_scope_error(client_user_id)
    try:
        result = client.delete_all(page_size=200)
    except Exception as exc:
        return {
            "ok": False,
            "stage": "delete_all",
            "demo_user_id": DEMO_USER_ID,
            "found_count": 0,
            "deleted_count": 0,
            "errors": [str(exc)],
        }
    if not isinstance(result, dict):
        return {
            "ok": False,
            "stage": "delete_all",
            "demo_user_id": DEMO_USER_ID,
            "found_count": 0,
            "deleted_count": 0,
            "errors": ["Mem0 delete_all returned an invalid result"],
            "result": result,
        }
    errors = _normalize_errors(result.get("errors", []))
    return {
        "ok": not errors,
        "stage": "delete_all",
        "demo_user_id": DEMO_USER_ID,
        "found_count": _safe_int(result.get("found_count")),
        "deleted_count": _safe_int(result.get("deleted_count")),
        "errors": errors,
        "result": result,
    }


def _reset_scope_error(client_user_id: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "stage": "scope",
        "demo_user_id": DEMO_USER_ID,
        "found_count": 0,
        "deleted_count": 0,
        "errors": [
            f"Mem0 reset requires a demo-scoped Mem0 client for {DEMO_USER_ID!r}; got user_id {client_user_id!r}"
        ],
    }


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_errors(errors: Any) -> list[str]:
    if errors is None:
        return []
    if isinstance(errors, list):
        return [str(error) for error in errors]
    return [str(errors)]


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
    started_at = datetime.now(timezone.utc).isoformat() if mode == "real_run" else _BASE_TIME

    if mode == "real_run" and engine == "local":
        phases, metrics, examples, reset = _run_local_demo(memories, queries)
    elif mode == "real_run":
        phases, metrics, examples, reset = _run_real_demo(client, memories, queries)
    else:
        phases, metrics, examples, reset = _run_dry_demo(memories, queries)

    report = {
        "ok": all(phase.get("ok") for phase in phases) and len(reset["errors"]) == 0,
        "run_id": run_id,
        "engine": engine,
        "mode": mode,
        "scale": scale,
        "demo_user_id": DEMO_USER_ID,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat() if mode == "real_run" else _finished_at(scale, query_count),
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
        {"name": "generate", "ok": True, "count": len(memories), "elapsed_ms": round(len(memories) * 0.03, 3)},
        {"name": "write", "ok": True, "count": len(memories), "elapsed_ms": round(write_ms, 3)},
        {"name": "search", "ok": True, "count": len(queries), "elapsed_ms": round(search_total_ms, 3)},
        {"name": "reset", "ok": True, "count": len(memories), "elapsed_ms": round(len(memories) * 0.02, 3)},
    ]
    metrics = {
        "write_qps": round(len(memories) / (write_ms / 1000.0), 3),
        "search_qps": round(len(queries) / (search_total_ms / 1000.0), 3),
        "search_p50_ms": round(_percentile(search_latencies, 50), 3),
        "search_p95_ms": round(_percentile(search_latencies, 95), 3),
        "error_rate": 0.0,
        "memory_count": float(len(memories)),
        "query_count": float(len(queries)),
    }
    reset = {"found_count": len(memories), "deleted_count": len(memories), "errors": []}
    return phases, metrics, examples, reset


def _run_local_demo(
    memories: list[dict[str, Any]],
    queries: list[str],
) -> tuple[list[dict[str, Any]], dict[str, float], list[dict[str, Any]], dict[str, Any]]:
    write_started = time.perf_counter()
    local_index = [dict(memory) for memory in memories]
    write_ms = round((time.perf_counter() - write_started) * 1000, 3)

    examples: list[dict[str, Any]] = []
    search_latencies: list[float] = []
    for query in queries:
        search_started = time.perf_counter()
        top_k = _local_top_k(query, local_index)
        latency = round((time.perf_counter() - search_started) * 1000, 3)
        search_latencies.append(latency)
        examples.append({"query": query, "latency_ms": latency, "top_k": top_k})

    reset_started = time.perf_counter()
    found_count = len(local_index)
    local_index.clear()
    reset_ms = round((time.perf_counter() - reset_started) * 1000, 3)
    phases = [
        {"name": "generate", "ok": True, "count": len(memories), "elapsed_ms": 0.0},
        {"name": "write", "ok": True, "count": found_count, "elapsed_ms": write_ms, "errors": []},
        {"name": "search", "ok": True, "count": len(queries), "elapsed_ms": round(sum(search_latencies), 3), "errors": []},
        {"name": "reset", "ok": True, "count": found_count, "elapsed_ms": reset_ms, "errors": []},
    ]
    metrics = {
        "write_qps": round(found_count / max(write_ms / 1000.0, 0.001), 3),
        "search_qps": round(len(queries) / max(sum(search_latencies) / 1000.0, 0.001), 3),
        "search_p50_ms": round(_percentile(search_latencies, 50), 3),
        "search_p95_ms": round(_percentile(search_latencies, 95), 3),
        "error_rate": 0.0,
        "memory_count": float(found_count),
        "query_count": float(len(queries)),
    }
    reset = {"stage": "local_reset", "found_count": found_count, "deleted_count": found_count, "errors": []}
    return phases, metrics, examples, reset


def _run_real_demo(
    client: Any | None,
    memories: list[dict[str, Any]],
    queries: list[str],
) -> tuple[list[dict[str, Any]], dict[str, float], list[dict[str, Any]], dict[str, Any]]:
    _validate_real_client(client)
    errors: list[str] = []
    write_count = 0
    write_started = time.perf_counter()
    for memory in memories:
        try:
            _add_performance_memory(client, memory["content"])
            write_count += 1
        except Exception as exc:
            errors.append(f"{memory['id']}: {exc}")
            if len(errors) >= 10:
                break
    write_ms = round((time.perf_counter() - write_started) * 1000, 3)

    examples: list[dict[str, Any]] = []
    search_errors: list[str] = []
    search_latencies: list[float] = []
    for query in queries:
        search_started = time.perf_counter()
        try:
            raw_results = client.search(query, top_k=10)
            top_k = _normalize_real_results(raw_results)
            examples.append(
                {
                    "query": query,
                    "latency_ms": round((time.perf_counter() - search_started) * 1000, 3),
                    "top_k": top_k[:5],
                }
            )
        except Exception as exc:
            search_errors.append(f"{query}: {exc}")
            examples.append({"query": query, "latency_ms": 0.0, "top_k": []})
        search_latencies.append(round((time.perf_counter() - search_started) * 1000, 3))

    reset = reset_demo_memory(client)
    reset_errors = _normalize_errors(reset.get("errors", []))
    phases = [
        {"name": "generate", "ok": True, "count": len(memories), "elapsed_ms": 0.0},
        {"name": "write", "ok": not errors, "count": write_count, "elapsed_ms": write_ms, "errors": errors[:3]},
        {
            "name": "search",
            "ok": not search_errors,
            "count": len(queries) - len(search_errors),
            "elapsed_ms": round(sum(search_latencies), 3),
            "errors": search_errors[:3],
        },
        {
            "name": "reset",
            "ok": bool(reset.get("ok")),
            "count": reset.get("deleted_count", 0),
            "elapsed_ms": 0.0,
            "errors": reset_errors[:3],
        },
    ]
    total_errors = len(errors) + len(search_errors) + len(reset_errors)
    metrics = {
        "write_qps": round(write_count / max(write_ms / 1000.0, 0.001), 3),
        "search_qps": round(len(queries) / max(sum(search_latencies) / 1000.0, 0.001), 3),
        "search_p50_ms": round(_percentile(search_latencies, 50), 3),
        "search_p95_ms": round(_percentile(search_latencies, 95), 3),
        "error_rate": round(total_errors / max(len(memories) + len(queries) + 1, 1), 6),
        "memory_count": float(len(memories)),
        "query_count": float(len(queries)),
    }
    reset = {**reset, "errors": reset_errors}
    return phases, metrics, examples, reset


def _validate_real_client(client: Any | None) -> None:
    client_user_id = getattr(getattr(client, "config", None), "user_id", None)
    required_methods = ["add_text", "search", "delete_all"]
    missing = [method for method in required_methods if not callable(getattr(client, method, None))]
    if client_user_id != DEMO_USER_ID or missing:
        missing_text = f"; missing methods: {', '.join(missing)}" if missing else ""
        raise ValueError(
            f"real_run requires a demo-scoped Mem0 client for {DEMO_USER_ID!r}; got user_id {client_user_id!r}{missing_text}"
        )


def _add_performance_memory(client: Any, content: str) -> Any:
    try:
        return client.add_text(content, context="mem0_large_memory_performance_demo", async_mode=True)
    except TypeError:
        return client.add_text(content, context="mem0_large_memory_performance_demo")


def _normalize_real_results(results: Any) -> list[dict[str, Any]]:
    if not isinstance(results, list):
        results = []
    normalized = [_real_result_record(result) for result in results]
    active = [item for item in normalized if item.get("status", "active") == "active"]
    active.sort(key=lambda item: (item["retrieval_score"], item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return active


def _real_result_record(result: Any) -> dict[str, Any]:
    if hasattr(result, "to_dict"):
        data = result.to_dict()
    elif isinstance(result, dict):
        data = dict(result)
    else:
        data = {}
    validity = data.get("validity") if isinstance(data.get("validity"), dict) else {}
    score = _safe_float(validity.get("retrieval_score", validity.get("mem0_score", data.get("score", data.get("confidence", 0.0)))))
    return {
        "id": data.get("id"),
        "content": data.get("content") or data.get("memory") or data.get("text") or "",
        "scope": data.get("scope") or "mem0",
        "status": data.get("status") or "active",
        "score": score,
        "retrieval_score": score,
        "updated_at": data.get("updated_at") or "",
        "created_at": data.get("created_at") or "",
        "retrieval_rank_strategy": "score_time",
    }


def _safe_float(value: Any) -> float:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return 0.0


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
            "retrieval_score": round(score, 6),
            "updated_at": memory["updated_at"],
            "retrieval_rank_strategy": "score_time",
        }
        for score, _, memory in ranked[:limit]
    ]


def _local_top_k(query: str, memories: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    ranked = []
    for memory in memories:
        score = _local_retrieval_score(query, memory)
        if score <= 0:
            continue
        ranked.append((score, memory["updated_at"], memory))
    if not ranked:
        ranked = [(_stable_score(query, memory["id"]) * 0.1, memory["updated_at"], memory) for memory in memories[:limit]]
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [
        {
            "id": memory["id"],
            "content": memory["content"],
            "scope": memory["scope"],
            "score": round(score, 6),
            "retrieval_score": round(score, 6),
            "updated_at": memory["updated_at"],
            "retrieval_rank_strategy": "score_time",
        }
        for score, _, memory in ranked[:limit]
    ]


def _local_retrieval_score(query: str, memory: dict[str, Any]) -> float:
    terms = [term for term in _tokenize(query) if len(term) > 2]
    haystack = " ".join([memory["content"], memory["scope"], *memory.get("tags", [])]).lower()
    hits = sum(1 for term in terms if term in haystack)
    if not terms:
        return 0.0
    lexical = hits / len(terms)
    recency_tiebreaker = _stable_score(query, memory["id"]) * 0.01
    return min(1.0, lexical + recency_tiebreaker)


def _tokenize(text: str) -> list[str]:
    return [part.strip(".,!?;:()[]{}\"'").lower() for part in text.split() if part.strip()]


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
    finished = datetime(2026, 6, 11, tzinfo=timezone.utc) + timedelta(seconds=seconds)
    return finished.isoformat()


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
