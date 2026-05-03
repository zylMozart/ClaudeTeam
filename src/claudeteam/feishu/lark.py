"""Thin wrapper around `npx @larksuite/cli`.

Single function: `call(args, *, profile, timeout) -> dict | None`.

Returns the `data` field of lark-cli's JSON response on success, `{}` if
stdout is empty, `None` on any failure.  Proxy bypass is automatic when
`LARK_CLI_NO_PROXY=1` is set in the environment.

Production note: on host networks, lark-cli routinely takes ~73 seconds
per call (memory: lark-cli send is ~73s).  Default timeout = 90s; bump
via `CLAUDETEAM_LARK_TIMEOUT` if needed.

Tests inject a fake subprocess.run via the `run=` kwarg.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Callable

from claudeteam.util import env_str


_PROXY_KEYS = ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")


def subprocess_env() -> dict[str, str]:
    """Build the env to hand to any lark-cli subprocess (one-shot `call` or
    long-running `event +subscribe`). Strips HTTP/HTTPS proxy vars when
    LARK_CLI_NO_PROXY is truthy, since lark-cli doesn't honor that variable
    itself — it's a wrapper-side flag.
    """
    env = os.environ.copy()
    if env_str("LARK_CLI_NO_PROXY").lower() in {"1", "true", "yes", "on"}:
        for key in _PROXY_KEYS:
            env.pop(key, None)
    return env


def _build_argv(args: list[str], profile: str) -> list[str]:
    base = ["npx", "@larksuite/cli"]
    if profile:
        base += ["--profile", profile]
    return base + list(args)


def _resolve_timeout(explicit: int | None) -> int:
    if explicit is not None:
        return explicit
    try:
        return int(env_str("CLAUDETEAM_LARK_TIMEOUT") or "90")
    except ValueError:
        return 90


def call(args: list[str], *, profile: str = "", timeout: int | None = None,
         run: Callable = subprocess.run) -> dict | None:
    """Execute lark-cli; return parsed `data` JSON, `{}` on empty stdout, None on failure.

    `profile` selects the lark-cli profile (`--profile X`).  Pass empty
    string to use the default profile.

    The function intentionally swallows network / lark-cli errors and
    prints a one-line warning instead of raising — callers that need
    to distinguish failure modes should check the return value.
    """
    cmd = _build_argv(args, profile)
    timeout_s = _resolve_timeout(timeout)
    t0 = time.monotonic()
    try:
        r = run(cmd, capture_output=True, text=True, timeout=timeout_s, env=subprocess_env())
    except subprocess.TimeoutExpired:
        elapsed = (time.monotonic() - t0)
        print(f"  ⚠️ lark-cli timeout ({timeout_s}s after {elapsed:.1f}s): {' '.join(args[:3])}")
        return None
    except FileNotFoundError:
        # npx itself isn't on PATH. claudeteam say / router / chat all hit
        # this — better one-line warn than a top-level traceback.
        print(f"  ⚠️ npx not found on PATH; install Node.js to enable lark-cli")
        return None
    except OSError as e:
        # Other Popen-time OS failures (permission, fork failed, etc.).
        # Caller will see None and propagate as "send failed".
        print(f"  ⚠️ lark-cli could not be launched: {e}")
        return None
    if r.returncode != 0:
        msg = (r.stderr or "").strip().splitlines()[-1:]
        print(f"  ⚠️ lark-cli failed (rc={r.returncode}): {msg[0] if msg else ''}"[:200])
        return None
    if not r.stdout.strip():
        return {}
    try:
        full = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        # Don't silently swallow — JSON corruption from lark-cli is rare
        # but when it happens, the operator wants to know (typically means
        # lark-cli printed banner text into stdout, or got proxied to an
        # auth wall). One-line preview helps debugging without flooding
        # the daemon log.
        preview = r.stdout.strip().splitlines()[0][:120] if r.stdout.strip() else "(empty)"
        print(f"  ⚠️ lark-cli returned non-JSON ({e}): {preview}")
        return None
    # lark-cli wraps results in {"ok": ..., "data": ...} or returns data directly.
    # `ok: false` means the API returned an error even though lark-cli exited 0.
    if isinstance(full, dict) and full.get("ok") is False:
        reason = _extract_error_message(full)
        print(f"  ⚠️ lark-cli API error: {reason}"[:200])
        return None
    return full.get("data", full)


def _extract_error_message(full: dict) -> str:
    """Pull the most informative human-readable string out of lark-cli's
    error-shape variants. Real responses seen in the wild:

      {"ok": false, "msg": "plain message"}
      {"ok": false, "error": "plain string"}
      {"ok": false, "error": {"type": "validation", "message": "..."}}
      {"ok": false, "error": {"type": "api_error", "code": 230002,
                              "message": "HTTP 400: Bot/User can NOT be out of the chat."}}

    Round-58 smoke caught this: when error is a structured dict, the
    old `or "?"` chain returned the dict and the warning line printed
    `{'type': ..., 'message': '...'}` — useless to operators. Now we
    extract `error.message` when error is a dict, falling back through
    msg / code / "?" if nothing useful is present.
    """
    if msg := full.get("msg"):
        return str(msg)
    err = full.get("error")
    if isinstance(err, dict):
        # Prefer message; tag with type/code if present so the line
        # gives operators both the human string AND the API code.
        message = err.get("message") or err.get("code") or "?"
        kind = err.get("type")
        return f"{message} (type={kind})" if kind else str(message)
    if isinstance(err, str) and err:
        return err
    if code := full.get("code"):
        return str(code)
    return "?"
