#!/usr/bin/env python3
"""Check whether router processed a given feishu message_id.

Usage:
    python3 scripts/smoke/router_trace.py <msg_id> [--log <path>] [--container <name>]
    python3 scripts/smoke/router_trace.py --self-test

Output (stdout, single JSON line):
    {"msg_id": ..., "status": "processed" | "dropped" | "unknown",
     "hits": N, "log_path": ..., "context": [lines]}

Default log path:
    - If --container given: docker exec <container> cat /app/state/router.log
    - Else: $CLAUDETEAM_RUNTIME_ROOT/state/router.log
    - Fallback: /app/state/router.log (container-native run)

Verdict:
    - hits ≥ 1 and any context line contains any of DROP_MARKERS → dropped
    - hits ≥ 1 otherwise → processed
    - hits = 0 → dropped (router never saw the msg_id, typical of the
      known non-text ingestion bug)
"""
import json
import os
import re
import subprocess
import sys
import tempfile

DROP_MARKERS = [
    "msg_type not text",
    "msg_type is not text",
    "跳过非文本",
    "drop non-text",
    "skip non-text",
    "吞掉",
    "skipped:",
    "unsupported msg_type",
]


def _read_log(log_path: str | None, container: str | None) -> tuple[str, str]:
    """Return (log_path_used, log_content). Raises on failure."""
    if container:
        path = log_path or "/app/state/router.log"
        r = subprocess.run(
            ["docker", "exec", container, "cat", path],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            raise RuntimeError(
                f"docker exec {container} cat {path} failed: {r.stderr[:400]}"
            )
        return f"{container}:{path}", r.stdout
    candidates = []
    if log_path:
        candidates.append(log_path)
    rt_root = os.environ.get("CLAUDETEAM_RUNTIME_ROOT")
    if rt_root:
        candidates.append(os.path.join(rt_root, "state", "router.log"))
    candidates.append("/app/state/router.log")
    for p in candidates:
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="ignore") as f:
                return p, f.read()
    raise FileNotFoundError(
        f"router.log not found in any of: {candidates}"
    )


def trace(
    msg_id: str,
    log_path: str | None = None,
    container: str | None = None,
    context_lines: int = 3,
) -> dict:
    path_used, content = _read_log(log_path, container)
    lines = content.splitlines()
    pat = re.compile(re.escape(msg_id))
    hit_idx = [i for i, ln in enumerate(lines) if pat.search(ln)]
    context: list[str] = []
    for i in hit_idx[:20]:
        lo = max(0, i - context_lines)
        hi = min(len(lines), i + context_lines + 1)
        context.extend(lines[lo:hi])
        context.append("---")
    if context and context[-1] == "---":
        context.pop()
    if not hit_idx:
        status = "dropped"
    elif any(any(dm in ln for dm in DROP_MARKERS) for ln in context):
        status = "dropped"
    else:
        status = "processed"
    return {
        "msg_id": msg_id, "status": status, "hits": len(hit_idx),
        "log_path": path_used, "context": context[:60],
    }


def _self_test() -> int:
    with tempfile.TemporaryDirectory() as td:
        processed_log = os.path.join(td, "router_processed.log")
        with open(processed_log, "w") as f:
            f.write(
                "2026-04-24 18:00:00 router: received om_abc123\n"
                "2026-04-24 18:00:00 router: routed om_abc123 to coder\n"
                "2026-04-24 18:00:05 router: received om_xyz789\n"
            )
        r = trace("om_abc123", log_path=processed_log)
        assert r["status"] == "processed", r
        assert r["hits"] == 2, r
        assert any("coder" in ln for ln in r["context"]), r

        dropped_log = os.path.join(td, "router_dropped.log")
        with open(dropped_log, "w") as f:
            f.write(
                "2026-04-24 18:10:00 router: received om_card1\n"
                "2026-04-24 18:10:00 router: drop non-text om_card1 msg_type=interactive\n"
            )
        r2 = trace("om_card1", log_path=dropped_log)
        assert r2["status"] == "dropped", r2
        assert r2["hits"] == 2, r2

        r3 = trace("om_never_seen", log_path=processed_log)
        assert r3["status"] == "dropped", r3
        assert r3["hits"] == 0, r3

        missing_path = os.path.join(td, "nope.log")
        try:
            trace("om_any", log_path=missing_path)
            assert False, "expected FileNotFoundError"
        except FileNotFoundError:
            pass
    print("OK: router_trace self-test passed (processed + dropped + zero-hit + missing-file)")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--self-test":
        return _self_test()
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    msg_id = argv[0]
    log_path = None
    container = None
    i = 1
    while i < len(argv):
        if argv[i] == "--log" and i + 1 < len(argv):
            log_path = argv[i + 1]; i += 2
        elif argv[i] == "--container" and i + 1 < len(argv):
            container = argv[i + 1]; i += 2
        else:
            print(f"Unknown arg: {argv[i]}", file=sys.stderr)
            return 2
    result = trace(msg_id, log_path=log_path, container=container)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "processed" else 1


if __name__ == "__main__":
    sys.exit(main())
