#!/usr/bin/env python3
"""
上传 Markdown 到飞书文档，支持折叠块（folded heading）。

特殊约定：以 "📌 " 开头的加粗行（**📌 xxx**）标记折叠区域的开始，
到下一个同级或更高级标题结束。折叠区域在飞书文档中以 Heading3 + children + folded=true 呈现。

用法：python3 scripts/upload_folded_doc.py <markdown文件路径>
"""
import sys, os, re, json, time, hashlib, requests
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from config import BASE
from feishu_api import get_token, h, api_request as api
from feishu_blocks import make_text_run, make_text_block, parse_inline, parse_single_line

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_FILE = os.path.join(os.path.dirname(__file__), "sync_manifest.json")

# ── Markdown 解析 ──────────────────────────────────────────

def parse_md(content):
    """解析 Markdown 为 block 列表。
    返回 list of dict，每个 dict：
      {"block": block_dict}  — 普通 block
      {"folded_heading": heading_block, "children": [block_dict...]}  — 折叠区域
    """
    lines = content.split("\n")
    result = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # 折叠区域标记：**📌 xxx**
        if line.startswith("**📌 "):
            title = line.strip("*").strip()
            heading = make_text_block(5, [make_text_run(title, bold=True)])
            children = []
            i += 1
            # 收集直到下一个 ### 或 ## 或 # 或另一个 **📌
            while i < len(lines):
                cl = lines[i]
                if re.match(r'^#{1,3} ', cl) or cl.startswith("**📌 "):
                    break
                block = parse_single_line(cl, lines, i)
                if block is not None:
                    if isinstance(block, tuple):
                        children.append(block[0])
                        i = block[1]
                        continue
                    children.append(block)
                i += 1
            result.append({"folded_heading": heading, "children": children})
            continue

        # 普通行
        block = parse_single_line(line, lines, i)
        if block is not None:
            if isinstance(block, tuple):
                result.append({"block": block[0]})
                i = block[1]
                continue
            result.append({"block": block})
        i += 1

    return result

# ── 飞书文档操作 ──────────────────────────────────────────

def create_doc(token, folder_token, title):
    r = api("POST", f"{BASE}/docx/v1/documents", token,
            json={"title": title, "folder_token": folder_token})
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"创建文档失败: {d}")
    return d["data"]["document"]["document_id"]

def upload_children(token, doc_id, parent_id, blocks):
    """批量追加子 block（每批最多 50 个）。"""
    for start in range(0, len(blocks), 50):
        batch = blocks[start:start+50]
        r = api("POST",
                f"{BASE}/docx/v1/documents/{doc_id}/blocks/{parent_id}/children",
                token, json={"children": batch, "index": -1})
        d = r.json()
        if d.get("code") != 0:
            raise RuntimeError(f"上传 block 失败: {d}")

def upload_descendant(token, doc_id, parent_id, heading_block, children_blocks):
    """使用 descendant API 创建嵌套结构（heading + children），返回 heading block_id。"""
    payload = {
        "children": [{
            **heading_block,
            "children": children_blocks
        }]
    }
    r = api("POST",
            f"{BASE}/docx/v1/documents/{doc_id}/blocks/{parent_id}/descendant",
            token, json=payload)
    d = r.json()
    if d.get("code") != 0:
        # descendant API 可能不可用，降级为两步创建
        print(f"  ⚠️  descendant API 失败 ({d.get('code')}), 降级为两步创建...")
        return fallback_nested_create(token, doc_id, parent_id, heading_block, children_blocks)
    return d["data"]["children"][0]["block_id"]

def fallback_nested_create(token, doc_id, parent_id, heading_block, children_blocks):
    """降级方案：先创建 heading，再往 heading 下追加 children，再 PATCH folded。"""
    # 创建 heading block
    r = api("POST",
            f"{BASE}/docx/v1/documents/{doc_id}/blocks/{parent_id}/children",
            token, json={"children": [heading_block], "index": -1})
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"创建 heading 失败: {d}")
    heading_id = d["data"]["children"][0]["block_id"]

    # 追加 children 到 heading 下
    if children_blocks:
        for start in range(0, len(children_blocks), 50):
            batch = children_blocks[start:start+50]
            r2 = api("POST",
                     f"{BASE}/docx/v1/documents/{doc_id}/blocks/{heading_id}/children",
                     token, json={"children": batch, "index": -1})
            d2 = r2.json()
            if d2.get("code") != 0:
                print(f"  ⚠️  添加子块失败: {d2}")

    return heading_id

def patch_folded(token, doc_id, block_id):
    """设置 block 为折叠状态。"""
    r = api("PATCH",
            f"{BASE}/docx/v1/documents/{doc_id}/blocks/{block_id}",
            token, json={
                "update_text_style": {
                    "style": {"folded": True},
                    "fields": [3]
                }
            })
    d = r.json()
    if d.get("code") != 0:
        print(f"  ⚠️  设置折叠失败: {d}")

def enable_link_share(token, doc_id):
    api("PATCH", f"{BASE}/drive/v1/permissions/{doc_id}/public",
        token, params={"type": "docx"},
        json={"external_access_entity": "open", "link_share_entity": "tenant_readable"})

# ── 主流程 ──────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("用法: python3 scripts/upload_folded_doc.py <markdown相对路径>")
        sys.exit(1)

    rel_path = sys.argv[1]
    abs_path = os.path.join(ROOT, rel_path)
    if not os.path.exists(abs_path):
        print(f"❌ 文件不存在: {abs_path}")
        sys.exit(1)

    # 加载配置
    cfg_path = os.path.join(os.path.dirname(__file__), "runtime_config.json")
    with open(cfg_path) as f:
        cfg = json.load(f)
    folder_token = cfg.get("sync_folder_token", "")
    if not folder_token:
        print("❌ 未配置 sync_folder_token")
        sys.exit(1)

    content = open(abs_path, encoding="utf-8").read()
    token = get_token()

    # 解析 Markdown
    print("📝 解析 Markdown...")
    items = parse_md(content)
    print(f"   解析完成: {len(items)} 个块（含折叠区域）")

    # 统计折叠块
    folded_count = sum(1 for it in items if "folded_heading" in it)
    print(f"   其中 {folded_count} 个折叠区域")

    # 创建新文档
    title = rel_path.replace("/", "_").replace(".md", "") + "_v2"
    print(f"📄 创建飞书文档: {title}")
    doc_id = create_doc(token, folder_token, title)
    print(f"   doc_id: {doc_id}")

    # 上传内容
    print("⬆️  上传内容...")
    normal_batch = []
    folded_ids = []

    for item in items:
        if "block" in item:
            normal_batch.append(item["block"])
        elif "folded_heading" in item:
            # 先刷掉之前积攒的普通 block
            if normal_batch:
                upload_children(token, doc_id, doc_id, normal_batch)
                normal_batch = []
                time.sleep(0.3)

            # 创建折叠区域
            heading_id = fallback_nested_create(
                token, doc_id, doc_id,
                item["folded_heading"],
                item["children"]
            )
            folded_ids.append(heading_id)
            time.sleep(0.3)

    # 刷掉剩余普通 block
    if normal_batch:
        upload_children(token, doc_id, doc_id, normal_batch)

    # 设置折叠状态
    if folded_ids:
        print(f"🔽 设置 {len(folded_ids)} 个折叠块...")
        for bid in folded_ids:
            patch_folded(token, doc_id, bid)
            time.sleep(0.2)

    # 开启链接分享
    enable_link_share(token, doc_id)

    # 更新 manifest
    with open(abs_path, "rb") as f:
        file_hash = "sha256:" + hashlib.sha256(f.read()).hexdigest()[:16]

    if os.path.exists(MANIFEST_FILE):
        with open(MANIFEST_FILE) as f:
            manifest = json.load(f)
    else:
        manifest = {"folder_token": folder_token, "files": {}}

    manifest["files"][rel_path] = {
        "doc_token": doc_id,
        "last_hash": file_hash,
        "last_synced": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    }
    with open(MANIFEST_FILE, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"\n✅ 上传完成！")
    print(f"   文档 ID: {doc_id}")
    print(f"   链接: https://feishu.cn/docx/{doc_id}")

if __name__ == "__main__":
    main()
