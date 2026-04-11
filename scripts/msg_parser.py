#!/usr/bin/env python3
"""
消息解析模块 — ClaudeTeam

从飞书 API 返回的原始消息中提取结构化信息。
支持 text / post / interactive / image 四种消息类型。
"""
import json, re


def extract_post_text(obj):
    """递归提取富文本（post）消息中的所有文本片段和图片 key。"""
    parts = []
    images = []
    if isinstance(obj, dict):
        if obj.get("tag") == "img":
            image_key = obj.get("image_key", "")
            if image_key:
                images.append(image_key)
        elif "text" in obj:
            parts.append(obj["text"])
        for v in obj.values():
            if isinstance(v, (dict, list)):
                sub_parts, sub_images = extract_post_text(v)
                parts.extend(sub_parts)
                images.extend(sub_images)
    elif isinstance(obj, list):
        for item in obj:
            sub_parts, sub_images = extract_post_text(item)
            parts.extend(sub_parts)
            images.extend(sub_images)
    return parts, images


def parse_text_message(content_raw):
    """解析 text 类型消息，返回文本。"""
    try:
        content_obj = json.loads(content_raw)
        return content_obj.get("text", "")
    except Exception:
        return content_raw


def parse_image_message(content_raw):
    """解析 image 类型消息，返回 (text, image_key)。"""
    content_obj = json.loads(content_raw) if content_raw else {}
    image_key = content_obj.get("image_key", "")
    text = f"[图片消息] image_key: {image_key}（下载中...）"
    return text, image_key


def parse_post_message(content_raw):
    """解析 post（富文本）类型消息，返回 (text, image_keys)。"""
    try:
        content_obj = json.loads(content_raw) if content_raw else {}
    except Exception:
        content_obj = {}
    text_parts, images = extract_post_text(content_obj)
    text = " ".join(t for t in text_parts if t).strip()
    if not text and not images:
        text = "[富文本消息，无法解析内容]"
    return text, images


def parse_interactive_message(content_raw):
    """解析 interactive（消息卡片）类型消息，返回文本。"""
    try:
        card = json.loads(content_raw) if content_raw else {}
        header_text = card.get("title", "")
        if not header_text:
            header_text = card.get("header", {}).get("title", {}).get("content", "")
        body_parts = []
        for row in card.get("elements", []):
            if isinstance(row, list):
                for elem in row:
                    if isinstance(elem, dict):
                        body_parts.append(elem.get("text", "") or elem.get("content", ""))
            elif isinstance(row, dict):
                body_parts.append(row.get("content", "") or row.get("text", ""))
        body_text = "\n".join(p for p in body_parts if p)
        return f"{header_text}\n{body_text}" if header_text else body_text
    except Exception:
        return content_raw


def parse_message(msg):
    """统一消息解析入口。

    参数:
        msg: 飞书 API 返回的消息对象

    返回:
        dict with keys:
            text: str — 解析后的文本内容
            msg_type: str — 消息类型
            image_keys: list[str] — 图片 key 列表（需下载）
            skipped: bool — 是否应跳过该消息
    """
    msg_type = msg.get("msg_type", "text")
    content_raw = msg.get("body", {}).get("content", "{}")

    result = {
        "text": "",
        "msg_type": msg_type,
        "image_keys": [],
        "skipped": False,
    }

    if msg_type == "image":
        text, image_key = parse_image_message(content_raw)
        result["text"] = text
        if image_key:
            result["image_keys"] = [image_key]

    elif msg_type == "text":
        result["text"] = parse_text_message(content_raw)

    elif msg_type == "post":
        text, images = parse_post_message(content_raw)
        result["text"] = text
        result["image_keys"] = images

    elif msg_type == "interactive":
        result["text"] = parse_interactive_message(content_raw)

    else:
        result["skipped"] = True

    return result
