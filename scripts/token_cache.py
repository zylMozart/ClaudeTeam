#!/usr/bin/env python3
"""
飞书 Token 缓存 — ClaudeTeam

功能描述:
  缓存飞书 app_access_token，避免每次 API 调用都重新请求。
  内存缓存 + 文件缓存双层，token 有效期 2 小时，缓存 1.5 小时。

依赖:
  Python 3.6+, requests
"""
import os, json, time, requests

_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".token_cache.json")
_cached = {"token": "", "expires_at": 0}


def _fetch_new_token(app_id, app_secret, base_url):
    """请求新 token 并写入内存+文件缓存。"""
    global _cached
    r = requests.post(f"{base_url}/auth/v3/app_access_token/internal",
                      json={"app_id": app_id, "app_secret": app_secret})
    token = r.json()["app_access_token"]
    _cached = {"token": token, "expires_at": time.time() + 5400}
    try:
        tmp = _CACHE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_cached, f)
        os.replace(tmp, _CACHE_FILE)
    except Exception:
        pass
    return token


def invalidate():
    """清除内存和文件缓存，下次调用 get_token_cached 时强制刷新。"""
    global _cached
    _cached = {"token": "", "expires_at": 0}
    try:
        os.remove(_CACHE_FILE)
    except OSError:
        pass


def get_token_cached(app_id, app_secret, base_url):
    """返回有效的 app_access_token，1.5小时内复用缓存。"""
    global _cached
    now = time.time()

    # 内存缓存命中
    if _cached["token"] and now < _cached["expires_at"]:
        return _cached["token"]

    # 文件缓存命中
    if os.path.exists(_CACHE_FILE):
        try:
            with open(_CACHE_FILE) as f:
                disk = json.load(f)
            if disk.get("token") and now < disk.get("expires_at", 0):
                _cached = disk
                return _cached["token"]
        except Exception:
            pass

    return _fetch_new_token(app_id, app_secret, base_url)
