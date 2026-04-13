#!/usr/bin/env python3
"""
重获飞书群聊邀请链接 — ClaudeTeam

读取 scripts/runtime_config.json 里的 chat_id,
调用 lark-cli 重新生成一条永久邀请链接并打印。

用法:
  python3 scripts/get-invite-link.py                 # 永久链接(默认)
  python3 scripts/get-invite-link.py week            # 有效期 7 天
  python3 scripts/get-invite-link.py year            # 有效期 1 年
  python3 scripts/get-invite-link.py permanently     # 永久有效

背景:
  setup.py 在首次 init 时会打印邀请链接,但之后若误关终端或
  需要转发给新成员,没有直接的重获入口。本脚本用于补齐这一环节。

注意:
  - 调用方(bot 或 user)必须是目标群的成员
  - 单聊 / 密聊 / 团队群不支持分享群链接
"""
import sys, os, json, subprocess

sys.path.insert(0, os.path.dirname(__file__))
from config import LARK_CLI, load_runtime_config

# ── 参数校验 ────────────────────────────────────────────────────

VALID_PERIODS = {"week", "year", "permanently"}


def parse_period():
    if len(sys.argv) < 2:
        return "permanently"
    p = sys.argv[1].strip()
    if p in VALID_PERIODS:
        return p
    print(__doc__)
    print(f"❌ 未知有效期: {p}（支持: {', '.join(sorted(VALID_PERIODS))}）")
    sys.exit(1)


# ── 主流程 ──────────────────────────────────────────────────────

def main():
    period = parse_period()

    cfg = load_runtime_config()
    # runtime_config.json 里 chat_id 可能是 null(历史遗留或手动编辑),
    # 先落到 "" 再 strip,避免 None.strip() 抛 AttributeError
    chat_id = (cfg.get("chat_id") or "").strip()
    if not chat_id:
        print("❌ runtime_config.json 中未找到 chat_id")
        print("   请先运行: python3 scripts/setup.py")
        sys.exit(1)

    print(f"🔗 正在为群 {chat_id} 生成邀请链接（有效期: {period}）...")

    args = LARK_CLI + [
        "im", "chats", "link",
        "--params", json.dumps({"chat_id": chat_id}),
        "--data", json.dumps({"validity_period": period}),
        "--as", "bot",
    ]
    r = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        print(f"❌ lark-cli 调用失败: {r.stderr.strip()[:300]}")
        sys.exit(1)

    try:
        payload = json.loads(r.stdout) if r.stdout.strip() else {}
    except json.JSONDecodeError:
        print(f"❌ 无法解析 lark-cli 输出: {r.stdout[:200]}")
        sys.exit(1)

    # lark-cli 常把飞书业务错误(no permission / bot 非群成员 / 群已解散)
    # 以 exit 0 + body 里 code != 0 的形式回来,必须显式检查
    code = payload.get("code")
    if code not in (None, 0):
        msg = payload.get("msg", "")
        print(f"❌ 飞书 API 业务错误 code={code} msg={msg}")
        print("   常见原因: bot 未加入该群 / 群已解散 / 群类型不支持分享")
        sys.exit(1)

    data = payload.get("data", payload)
    share_link = data.get("share_link", "")
    expire_time = data.get("expire_time", "")

    if not share_link:
        print(f"❌ 未拿到 share_link,原始响应: {json.dumps(payload, ensure_ascii=False)[:300]}")
        sys.exit(1)

    print()
    print(f"✅ 邀请链接已生成:")
    print(f"   {share_link}")
    if expire_time:
        print(f"   过期时间: {expire_time}")
    print()
    print("   💡 可直接把链接转发给需要加入群聊的成员")


if __name__ == "__main__":
    main()
