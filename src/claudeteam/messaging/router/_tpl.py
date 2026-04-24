"""Message templates used by the router daemon."""

TPL_AGENT_NOTIFY = (
    "【Router】你有来自 {sender} 的新消息。\n"
    "执行: python3 scripts/feishu_msg.py inbox {agent}\n"
    "消息预览: {preview}"
)

TPL_USER_MSG_LONG = (
    "【群聊消息】用户在群里发了消息（较长，已保存到文件）。\n"
    "请先读取文件: {file_path}\n"
    "预览: {preview}\n\n"
    "处理完成后用以下命令回复群里:\n"
    'python3 scripts/feishu_msg.py say {agent} "<你的回复>"'
)
