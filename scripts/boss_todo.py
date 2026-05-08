#!/usr/bin/env python3
"""
老板代办 Bitable 持久化入口。

记录等待老板审核、登录、授权、提供凭证或外部操作的事项。它独立于
task_tracker.py，避免把老板动作混入员工任务单。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

_SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
from claudeteam.runtime.config import LARK_CLI, load_runtime_config


TABLE_NAME = "老板代办"
OPEN_STATUSES = {"待处理", "进行中", "阻塞"}
CLOSED_STATUSES = {"已完成", "已取消"}
DEFAULT_STATUS = "待处理"
DEFAULT_OWNER = "boss"
DEFAULT_DEDUPE_KEYS = ["来源任务", "标题"]


def now_ms():
    return int(time.time() * 1000)


def normalize_title(title):
    text = title.strip().lower()
    text = re.sub(r"[\s\-_:/\\|，。、“”‘’'\"`~!@#$%^&*()+=[\]{}<>?；;：:]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_text(value):
    if isinstance(value, list):
        if not value:
            return ""
        first = value[0]
        if isinstance(first, dict):
            return str(first.get("text") or first.get("name") or "")
        return str(first)
    if value is None:
        return ""
    return str(value)


def resolve_boss_todo_config(cfg):
    nested = cfg.get("boss_todo") or {}
    if not isinstance(nested, dict):
        nested = {}
    base_token = nested.get("base_token") or cfg.get("bitable_app_token") or ""
    table_id = nested.get("table_id") or cfg.get("boss_todo_table_id") or ""
    table_name = nested.get("table_name") or TABLE_NAME
    view_link = nested.get("view_link") or cfg.get("boss_todo_link") or ""
    dedupe_keys = nested.get("dedupe_keys") or cfg.get("boss_todo_dedupe_keys") or DEFAULT_DEDUPE_KEYS
    if not isinstance(dedupe_keys, list) or not all(isinstance(k, str) for k in dedupe_keys):
        print("❌ boss_todo_dedupe_keys 必须是字符串数组，例如 [\"来源任务\", \"标题\"]。", file=sys.stderr)
        sys.exit(1)
    if not table_id:
        print("❌ 老板代办 Bitable 未配置 table_id。", file=sys.stderr)
        print("   请先运行: python3 scripts/setup.py ensure-boss-todo", file=sys.stderr)
        print("   或让 devops 将 boss_todo.table_id / boss_todo_link 写入 scripts/runtime_config.json。", file=sys.stderr)
        sys.exit(1)
    if not base_token:
        print("❌ 老板代办 Bitable 未配置 base_token。请先运行 setup/init。", file=sys.stderr)
        sys.exit(1)
    return {
        "base_token": base_token,
        "table_id": table_id,
        "table_name": table_name,
        "view_link": view_link,
        "dedupe_keys": dedupe_keys,
    }


def _lark_run(args, timeout=30):
    r = subprocess.run(LARK_CLI + args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        print(f"❌ lark-cli 调用失败: {(r.stderr or r.stdout).strip()[:300]}", file=sys.stderr)
        sys.exit(1)
    if not r.stdout.strip():
        return {}
    try:
        full = json.loads(r.stdout)
    except json.JSONDecodeError:
        print(f"❌ lark-cli 返回非 JSON: {r.stdout[:300]}", file=sys.stderr)
        sys.exit(1)
    return full.get("data", full)


def _parse_records(data):
    rows = data.get("data", [])
    field_names = data.get("fields", [])
    rid_list = data.get("record_id_list", [])
    out = []
    for i, row in enumerate(rows):
        fields = {}
        if isinstance(row, dict):
            fields = row.get("fields", row)
        else:
            for j, val in enumerate(row):
                if j < len(field_names):
                    fields[field_names[j]] = val
        rid = ""
        if i < len(rid_list):
            rid = rid_list[i]
        elif isinstance(row, dict):
            rid = row.get("record_id") or row.get("id") or ""
        out.append({"record_id": rid, "fields": fields})
    return out, len(rows)


class BitableStore:
    def __init__(self, base_token, table_id):
        self.base_token = base_token
        self.table_id = table_id

    def list_records(self):
        records = []
        offset = 0
        page_size = 200
        for _ in range(50):
            args = [
                "base", "+record-list",
                "--base-token", self.base_token,
                "--table-id", self.table_id,
                "--limit", str(page_size),
                "--as", "bot",
            ]
            if offset:
                args += ["--offset", str(offset)]
            data = _lark_run(args)
            parsed, page_rows = _parse_records(data)
            records.extend(parsed)
            if not data.get("has_more") or page_rows == 0:
                break
            offset += page_rows
        return records

    def create_record(self, fields):
        payload = json.dumps({"fields": list(fields.keys()), "rows": [list(fields.values())]}, ensure_ascii=False)
        data = _lark_run([
            "base", "+record-batch-create",
            "--base-token", self.base_token,
            "--table-id", self.table_id,
            "--json", payload,
            "--as", "bot",
        ])
        ids = data.get("record_id_list") or []
        if ids:
            return ids[0]
        records = data.get("records") or []
        if records:
            return records[0].get("record_id", "")
        return data.get("record_id", "")

    def update_record(self, record_id, patch):
        payload = json.dumps({"record_id_list": [record_id], "patch": patch}, ensure_ascii=False)
        _lark_run([
            "base", "+record-batch-update",
            "--base-token", self.base_token,
            "--table-id", self.table_id,
            "--json", payload,
            "--as", "bot",
        ])


class LocalJsonStore:
    def __init__(self, path):
        self.path = path

    def _load(self):
        if not os.path.exists(self.path):
            return {"_next": 1, "records": []}
        with open(self.path) as f:
            return json.load(f)

    def _save(self, data):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def list_records(self):
        return self._load().get("records", [])

    def create_record(self, fields):
        data = self._load()
        rid = f"local_{data.get('_next', 1)}"
        data["_next"] = data.get("_next", 1) + 1
        data.setdefault("records", []).append({"record_id": rid, "fields": dict(fields)})
        self._save(data)
        return rid

    def update_record(self, record_id, patch):
        data = self._load()
        for rec in data.get("records", []):
            if rec.get("record_id") == record_id:
                rec.setdefault("fields", {}).update(patch)
                self._save(data)
                return
        raise SystemExit(f"❌ 未找到老板代办记录: {record_id}")


def make_store(config):
    mock_path = os.environ.get("BOSS_TODO_STORE")
    if mock_path:
        return LocalJsonStore(mock_path)
    return BitableStore(config["base_token"], config["table_id"])


def unfinished(fields):
    return extract_text(fields.get("状态")) not in CLOSED_STATUSES


def _dedupe_value(field_name, fields, title, source_task):
    if field_name == "标题":
        return normalize_title(extract_text(fields.get(field_name)))
    if field_name == "来源任务":
        return extract_text(fields.get(field_name))
    return extract_text(fields.get(field_name))


def _wanted_dedupe_value(field_name, title, source_task):
    if field_name == "标题":
        return normalize_title(title)
    if field_name == "来源任务":
        return source_task or ""
    return ""


def find_unfinished_by_key(records, title, source_task, dedupe_keys=None):
    dedupe_keys = dedupe_keys or DEFAULT_DEDUPE_KEYS
    wanted_title = normalize_title(title)
    wanted_source = source_task or ""
    for rec in records:
        fields = rec.get("fields", {})
        if not unfinished(fields):
            continue
        matched = True
        for key in dedupe_keys:
            current = _dedupe_value(key, fields, title, source_task)
            wanted = _wanted_dedupe_value(key, title, source_task)
            if key == "标题":
                wanted = wanted_title
            elif key == "来源任务":
                wanted = wanted_source
            if current != wanted:
                matched = False
                break
        if matched:
            return rec
    return None


def find_target(records, target, source_task="", dedupe_keys=None):
    for rec in records:
        if rec.get("record_id") == target:
            return rec
    return find_unfinished_by_key(records, target, source_task, dedupe_keys)


def build_fields(args, existing=None):
    ts = now_ms()
    existing_fields = (existing or {}).get("fields", {}) if existing else {}
    fields = {
        "标题": args.title,
        "状态": getattr(args, "status", None) or extract_text(existing_fields.get("状态")) or DEFAULT_STATUS,
        "优先级": args.priority,
        "来源任务": args.source_task or "",
        "来源类型": args.source_type,
        "创建人": args.creator,
        "负责人": args.owner,
        "截止时间": args.due or "",
        "最新备注": args.note or "",
        "关联消息": args.link or "",
        "更新时间": ts,
    }
    if not existing:
        fields["创建时间"] = ts
    return fields


def cmd_create(store, args, config):
    records = store.list_records()
    existing = find_unfinished_by_key(records, args.title, args.source_task, config["dedupe_keys"])
    if existing:
        print(f"⚠️ 已存在未完成老板代办: {existing['record_id']}，未重复创建")
        return
    rid = store.create_record(build_fields(args))
    print(f"✅ 已创建老板代办: {rid}")


def cmd_upsert(store, args, config):
    records = store.list_records()
    existing = find_unfinished_by_key(records, args.title, args.source_task, config["dedupe_keys"])
    if existing:
        patch = build_fields(args, existing)
        store.update_record(existing["record_id"], patch)
        print(f"✅ 已更新老板代办: {existing['record_id']}")
        return
    rid = store.create_record(build_fields(args))
    print(f"✅ 已创建老板代办: {rid}")


def cmd_list(store, args):
    records = store.list_records()
    rows = []
    for rec in records:
        fields = rec.get("fields", {})
        status = extract_text(fields.get("状态")) or DEFAULT_STATUS
        if args.status and status != args.status:
            continue
        if not args.all and status in CLOSED_STATUSES:
            continue
        rows.append((rec.get("record_id", ""), fields))
    if not rows:
        print("📭 没有匹配的老板代办")
        return
    for rid, fields in rows:
        title = extract_text(fields.get("标题"))
        status = extract_text(fields.get("状态")) or DEFAULT_STATUS
        priority = extract_text(fields.get("优先级"))
        source = extract_text(fields.get("来源任务"))
        note = extract_text(fields.get("最新备注"))
        print(f"── {rid} [{status}/{priority}] {title}")
        if source:
            print(f"   来源任务: {source}")
        if note:
            print(f"   最新备注: {note}")


def cmd_done(store, args, config):
    records = store.list_records()
    rec = find_target(records, args.target, args.source_task, config["dedupe_keys"])
    if not rec:
        print("❌ 未找到匹配的未完成老板代办。请传 record_id，或同时传标题和 --source-task。", file=sys.stderr)
        sys.exit(1)
    patch = {
        "状态": "已完成",
        "完成时间": now_ms(),
        "更新时间": now_ms(),
    }
    if args.note:
        patch["最新备注"] = args.note
    store.update_record(rec["record_id"], patch)
    print(f"✅ 已完成老板代办: {rec['record_id']}")


def add_common_fields(parser):
    parser.add_argument("--source-task", default="", help="来源任务/事件 ID")
    parser.add_argument("--source-type", default="other", help="credential/review/approval/login/reply/deploy/other")
    parser.add_argument("--priority", default="中", choices=["高", "中", "低"])
    parser.add_argument("--note", default="", help="当前卡点和下一步")
    parser.add_argument("--link", default="", help="关联消息 record_id、PR 或日志链接")
    parser.add_argument("--creator", default=os.environ.get("CODEX_AGENT", "toolsmith"))
    parser.add_argument("--owner", default=DEFAULT_OWNER)
    parser.add_argument("--due", default="", help="截止时间，可填文本或日期")


def build_parser():
    parser = argparse.ArgumentParser(description="老板代办 Bitable 持久化工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create", help="创建老板代办；同 key 未完成时不重复创建")
    p_create.add_argument("title")
    add_common_fields(p_create)

    p_upsert = sub.add_parser("upsert", help="按 source_task + normalized title 幂等创建/更新")
    p_upsert.add_argument("title")
    add_common_fields(p_upsert)

    p_list = sub.add_parser("list", help="列出老板代办")
    p_list.add_argument("--status", default="", help="只列指定状态")
    p_list.add_argument("--all", action="store_true", help="包含已完成/已取消")

    p_done = sub.add_parser("done", help="标记老板代办已完成")
    p_done.add_argument("target", help="record_id 或标题")
    p_done.add_argument("--source-task", default="", help="用标题匹配时建议提供")
    p_done.add_argument("--note", default="", help="完成备注")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = resolve_boss_todo_config(load_runtime_config())
    store = make_store(cfg)
    if args.cmd == "create":
        cmd_create(store, args, cfg)
    elif args.cmd == "upsert":
        cmd_upsert(store, args, cfg)
    elif args.cmd == "list":
        cmd_list(store, args)
    elif args.cmd == "done":
        cmd_done(store, args, cfg)
    else:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
