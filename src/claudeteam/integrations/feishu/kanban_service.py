"""Injected Feishu-kanban service helpers (I/O via caller-supplied runner)."""
from __future__ import annotations

import json
import sys
from typing import Any, Callable

from claudeteam.integrations.feishu import kanban_projection


def fetch_all_agent_status_with_run(
    cfg: dict[str, Any],
    lark_run: Callable[..., Any],
) -> dict[str, dict[str, Any]] | None:
    """Fetch and shape agent status table rows.

    Returns None on query failure so caller can skip this sync round.
    """
    bt = cfg["bitable_app_token"]
    st = cfg["sta_table_id"]
    data = lark_run(
        ["base", "+record-list", "--base-token", bt, "--table-id", st, "--limit", "100", "--as", "bot"],
        label="拉取状态表",
    )
    if data is None:
        print("   🚨 状态表查询失败,本轮同步放弃以保留上一轮数据", file=sys.stderr)
        return None
    return kanban_projection.build_agent_status_map(data.get("items", []))


def ensure_kanban_table_with_run(
    cfg: dict[str, Any],
    lark_run: Callable[..., Any],
    save_cfg: Callable[[dict[str, Any]], Any],
) -> tuple[bool, Any]:
    """Ensure kanban table exists; create and persist table id when missing."""
    if cfg.get("kanban_table_id"):
        print(f"⚠️  项目看板表已存在: {cfg['kanban_table_id']}，跳过创建")
        return True, cfg["kanban_table_id"]

    bt = cfg["bitable_app_token"]
    fields = json.dumps(kanban_projection.build_kanban_table_fields(), ensure_ascii=False)
    data = lark_run(
        ["base", "+table-create", "--base-token", bt, "--name", "项目看板", "--fields", fields, "--as", "bot"],
        label="创建看板表",
    )

    tid = ""
    if data:
        if isinstance(data.get("table"), dict):
            tid = data["table"].get("id", data["table"].get("table_id", ""))
        else:
            tid = data.get("table_id", "")
    if not tid:
        return False, data

    cfg["kanban_table_id"] = tid
    save_cfg(cfg)
    print(f"✅ 项目看板表已创建: {tid}")
    return True, tid


def get_all_kanban_record_ids_with_run(
    cfg: dict[str, Any],
    lark_run: Callable[..., Any],
) -> list[str] | None:
    """Fetch record list and extract record ids.

    Returns None on query failure so caller can skip delete+create this round.
    """
    bt = cfg["bitable_app_token"]
    kt = cfg["kanban_table_id"]
    data = lark_run(
        ["base", "+record-list", "--base-token", bt, "--table-id", kt, "--limit", "500", "--as", "bot"],
        label="获取看板记录",
    )
    if data is None:
        return None
    return kanban_projection.extract_kanban_record_ids(data.get("items", []))


def delete_all_kanban_records_with_run(
    cfg: dict[str, Any],
    lark_run: Callable[..., Any],
    *,
    batch_delete_limit: int = 500,
) -> bool:
    """Delete all records from kanban table with batch API."""
    ids = get_all_kanban_record_ids_with_run(cfg, lark_run)
    if ids is None:
        print("   🚨 获取看板记录列表失败,跳过本轮以保留旧数据", file=sys.stderr)
        return False
    if not ids:
        return True

    bt = cfg["bitable_app_token"]
    kt = cfg["kanban_table_id"]
    path = f"/open-apis/bitable/v1/apps/{bt}/tables/{kt}/records/batch_delete"

    for batch_start in range(0, len(ids), batch_delete_limit):
        batch = ids[batch_start:batch_start + batch_delete_limit]
        payload = json.dumps({"records": batch}, ensure_ascii=False)
        data = lark_run(
            ["api", "POST", path, "--data", payload, "--as", "bot"],
            label=f"批删记录 {batch_start+1}-{batch_start+len(batch)}/{len(ids)}",
        )
        if data is None:
            print(
                f"   🚨 批删记录失败 (batch {batch_start+1}-{batch_start+len(batch)}/{len(ids)}),"
                "跳过本轮 sync 写入,保留旧看板状态等下一轮"
            )
            return False
    return True


def bitable_batch_create_with_run(
    cfg: dict[str, Any],
    records_json: str,
    lark_run: Callable[..., Any],
) -> bool:
    """Write one batch of kanban rows."""
    bt = cfg["bitable_app_token"]
    kt = cfg["kanban_table_id"]
    data = lark_run(
        ["base", "+record-batch-create", "--base-token", bt, "--table-id", kt, "--json", records_json, "--as", "bot"],
        label="批量写入看板",
    )
    if data is None:
        print("   🚨 看板批写失败,跳过本轮剩余批次,等下一轮重刷", file=sys.stderr)
        return False
    return True


def sync_kanban_snapshot_with_run(
    cfg: dict[str, Any],
    tasks: list[dict[str, Any]],
    lark_run: Callable[..., Any],
    *,
    batch_delete_limit: int = 500,
    batch_size: int = 500,
) -> None:
    """Run one kanban full-refresh round with injected I/O runner."""
    agent_status = fetch_all_agent_status_with_run(cfg, lark_run)
    if agent_status is None:
        print("  ─ 跳过本轮(状态表查询失败)")
        return

    if not delete_all_kanban_records_with_run(
        cfg,
        lark_run,
        batch_delete_limit=batch_delete_limit,
    ):
        print("  ─ 跳过本轮看板写入(删除失败,保留旧状态)")
        return

    if not tasks:
        print("  ─ 无任务记录")
        return

    field_names = kanban_projection.KANBAN_FIELD_NAMES
    rows = kanban_projection.build_kanban_rows(tasks, agent_status)

    written = 0
    for batch in kanban_projection.chunks(rows, batch_size):
        payload = json.dumps({"fields": field_names, "rows": batch}, ensure_ascii=False)
        if not bitable_batch_create_with_run(cfg, payload, lark_run):
            print(f"  ─ 看板部分写入失败 (已写 {written}/{len(rows)}),等下一轮全量重刷")
            return
        written += len(batch)

    print(f"✅ 看板已同步: {len(rows)} 条任务")
