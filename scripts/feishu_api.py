#!/usr/bin/env python3
"""
飞书 API 底层封装 — ClaudeTeam

统一提供 token 获取、请求头构造、带重试的 HTTP 请求等基础函数。
所有需要调用飞书 API 的脚本应从此模块导入，避免重复实现。
"""
import os, json, time, requests

import sys
sys.path.insert(0, os.path.dirname(__file__))
from config import APP_ID, APP_SECRET, BASE

# ── 内联 token 缓存（原 token_cache.py，将在后续 Phase 迁移后删除）──

_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".token_cache.json")
_cached = {"token": "", "expires_at": 0}

def get_token():
    """获取飞书 app_access_token（内存+文件双层缓存，1.5h 有效期）。"""
    global _cached
    now = time.time()
    if _cached["token"] and now < _cached["expires_at"]:
        return _cached["token"]
    if os.path.exists(_CACHE_FILE):
        try:
            with open(_CACHE_FILE) as f:
                disk = json.load(f)
            if disk.get("token") and now < disk.get("expires_at", 0):
                _cached = disk
                return _cached["token"]
        except Exception:
            pass
    r = requests.post(f"{BASE}/auth/v3/app_access_token/internal",
                      json={"app_id": APP_ID, "app_secret": APP_SECRET})
    token = r.json()["app_access_token"]
    _cached = {"token": token, "expires_at": now + 5400}
    try:
        with open(_CACHE_FILE + ".tmp", "w") as f:
            json.dump(_cached, f)
        os.replace(_CACHE_FILE + ".tmp", _CACHE_FILE)
    except Exception:
        pass
    return token

def invalidate_token():
    """清除 token 缓存。"""
    global _cached
    _cached = {"token": "", "expires_at": 0}
    try:
        os.remove(_CACHE_FILE)
    except OSError:
        pass


def h(token):
    """构造飞书 API 请求头。"""
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def api_request(method, url, token, **kwargs):
    """带指数退避重试的 HTTP 请求（最多 3 次，处理 429 限流）。"""
    for attempt in range(3):
        resp = requests.request(method, url, headers=h(token), **kwargs)
        if resp.status_code == 429:
            wait = 2 ** attempt
            print(f"  ⏳ 触发限流，{wait}s 后重试...")
            time.sleep(wait)
            continue
        return resp
    return resp


def now_ms():
    """当前时间的毫秒时间戳。"""
    return int(time.time() * 1000)


def extract_text(v):
    """从 Bitable 字段值中提取文本。"""
    if isinstance(v, list): return v[0].get("text", "") if v else ""
    return str(v) if v else ""
