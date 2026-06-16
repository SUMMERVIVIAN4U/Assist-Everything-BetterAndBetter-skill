from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evalharness.env import load_env


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose MiniMax OpenAI-compatible base URL connectivity.")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--post", action="store_true", help="Also call /chat/completions with the configured API key.")
    args = parser.parse_args()

    load_env(args.env_file)
    base_url = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1").rstrip("/")
    model = os.getenv("MINIMAX_MODEL", "MiniMax-M2.7")
    api_key = os.getenv("MINIMAX_API_KEY", "").strip()
    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    print(f"base_url: {base_url}")
    print(f"model: {model}")
    print(f"api_key: {_mask_key(api_key)}")
    print(f"host: {host}")
    print(f"port: {port}")
    print(f"timeout: {args.timeout:g}s")

    addresses = _check_dns(host, port)
    if not addresses:
        return
    if not _check_tcp(host, port, args.timeout):
        return
    if parsed.scheme == "https" and not _check_tls(host, port, args.timeout):
        return
    _check_http_head(base_url, args.timeout)
    if args.post:
        _check_chat_post(base_url, model, api_key, args.timeout)


def _check_dns(host: str, port: int) -> list[str]:
    start = time.perf_counter()
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except Exception as exc:
        print(f"DNS: FAIL {type(exc).__name__}: {exc}")
        return []
    elapsed = _ms(start)
    addresses = sorted({info[4][0] for info in infos})
    print(f"DNS: OK {elapsed}ms {addresses}")
    return addresses


def _check_tcp(host: str, port: int, timeout: float) -> bool:
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except Exception as exc:
        print(f"TCP: FAIL {type(exc).__name__}: {exc}")
        return False
    print(f"TCP: OK {_ms(start)}ms")
    return True


def _check_tls(host: str, port: int, timeout: float) -> bool:
    start = time.perf_counter()
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
    except Exception as exc:
        print(f"TLS: FAIL {type(exc).__name__}: {exc}")
        return False
    subject = dict(part[0] for part in cert.get("subject", []) if part)
    print(f"TLS: OK {_ms(start)}ms subject_cn={subject.get('commonName', '-')}")
    return True


def _check_http_head(base_url: str, timeout: float) -> None:
    start = time.perf_counter()
    request = urllib.request.Request(base_url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            print(f"HTTP HEAD: OK status={response.status} {_ms(start)}ms")
    except urllib.error.HTTPError as exc:
        print(f"HTTP HEAD: HTTP {exc.code} {_ms(start)}ms")
    except Exception as exc:
        print(f"HTTP HEAD: FAIL {type(exc).__name__}: {exc}")


def _check_chat_post(base_url: str, model: str, api_key: str, timeout: float) -> None:
    if not api_key:
        print("CHAT POST: SKIP missing MINIMAX_API_KEY")
        return
    start = time.perf_counter()
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "只回复 pong"}],
        "temperature": 0,
        "max_tokens": 16,
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            print(f"CHAT POST: OK status={response.status} {_ms(start)}ms")
            print(f"CHAT POST BODY: {_brief(body)}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"CHAT POST: HTTP {exc.code} {_ms(start)}ms")
        print(f"CHAT POST BODY: {_brief(body)}")
    except Exception as exc:
        print(f"CHAT POST: FAIL {type(exc).__name__}: {exc}")


def _mask_key(key: str) -> str:
    if not key:
        return "<empty>"
    if len(key) <= 8:
        return "<set>"
    return f"<set ...{key[-4:]}>"


def _ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


def _brief(text: str, limit: int = 600) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[:limit] + "..."


if __name__ == "__main__":
    main()
