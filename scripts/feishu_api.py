#!/usr/bin/env python3
"""
飞书 API 底层封装 — ClaudeTeam

统一提供 token 获取、请求头构造、带重试的 HTTP 请求等基础函数。
所有需要调用飞书 API 的脚本应从此模块导入，避免重复实现。
"""
import time, requests

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from config import APP_ID, APP_SECRET, BASE
from token_cache import get_token_cached


def get_token():
    """获取飞书 app_access_token（带缓存）。"""
    return get_token_cached(APP_ID, APP_SECRET, BASE)


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
