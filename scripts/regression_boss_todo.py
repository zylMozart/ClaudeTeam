#!/usr/bin/env python3
"""回归测试老板代办本地 mock store，不触碰真实飞书。"""
import contextlib
import io
import json
import os
import tempfile

import boss_todo


@contextlib.contextmanager
def temp_env(**updates):
    old = {k: os.environ.get(k) for k in updates}
    try:
        for k, v in updates.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False)


def read_json(path):
    with open(path) as f:
        return json.load(f)


def assert_loud_fail_without_table_id(tmp):
    cfg_path = os.path.join(tmp, "runtime_config.json")
    store_path = os.path.join(tmp, "boss_todo_store.json")
    write_json(cfg_path, {"bitable_app_token": "base_dummy"})
    stderr = io.StringIO()
    with temp_env(CLAUDETEAM_RUNTIME_CONFIG=cfg_path, BOSS_TODO_STORE=store_path):
        with contextlib.redirect_stderr(stderr):
            try:
                boss_todo.main(["list"])
            except SystemExit as exc:
                assert exc.code == 1
            else:
                raise AssertionError("missing table_id should SystemExit")
    msg = stderr.getvalue()
    assert "老板代办" in msg and "table_id" in msg and "setup.py ensure-boss-todo" in msg
    assert not os.path.exists(store_path)


def assert_upsert_and_done(tmp):
    cfg_path = os.path.join(tmp, "runtime_config.json")
    store_path = os.path.join(tmp, "boss_todo_store.json")
    write_json(cfg_path, {
        "bitable_app_token": "base_dummy",
        "boss_todo": {
            "base_token": "base_dummy",
            "table_id": "tbl_dummy",
            "table_name": "老板代办",
            "view_link": "",
        },
    })
    env = {
        "CLAUDETEAM_RUNTIME_CONFIG": cfg_path,
        "BOSS_TODO_STORE": store_path,
        "CODEX_AGENT": "toolsmith",
    }
    with temp_env(**env):
        boss_todo.main([
            "upsert", "Gemini OAuth 重新登录",
            "--source-task", "usage-credential-p0",
            "--source-type", "login",
            "--priority", "高",
            "--note", "token 已过期",
        ])
        boss_todo.main([
            "upsert", "  gemini   oauth 重新登录 ",
            "--source-task", "usage-credential-p0",
            "--source-type", "login",
            "--priority", "高",
            "--note", "需要老板重新登录",
        ])
        data = read_json(store_path)
        assert len(data["records"]) == 1, data
        fields = data["records"][0]["fields"]
        assert fields["最新备注"] == "需要老板重新登录"
        assert fields["状态"] == "待处理"

        boss_todo.main([
            "done", "Gemini OAuth 重新登录",
            "--source-task", "usage-credential-p0",
            "--note", "老板已登录，devops 验证通过",
        ])
        data = read_json(store_path)
        fields = data["records"][0]["fields"]
        assert fields["状态"] == "已完成"
        assert fields["最新备注"] == "老板已登录，devops 验证通过"
        assert fields.get("完成时间")


def assert_flat_config_and_dedupe_keys(tmp):
    cfg_path = os.path.join(tmp, "runtime_config.json")
    store_path = os.path.join(tmp, "boss_todo_store.json")
    write_json(cfg_path, {
        "bitable_app_token": "base_dummy",
        "boss_todo_table_id": "tbl_dummy",
        "boss_todo_link": "https://example.invalid/base?table=tbl_dummy",
        "boss_todo_dedupe_keys": ["来源任务", "标题"],
    })
    env = {
        "CLAUDETEAM_RUNTIME_CONFIG": cfg_path,
        "BOSS_TODO_STORE": store_path,
    }
    with temp_env(**env):
        boss_todo.main([
            "upsert", "GitHub PR push/open PR 需要授权",
            "--source-task", "usage-all-credential-portability / PR 凭证验证",
            "--priority", "高",
        ])
        boss_todo.main([
            "upsert", "github pr push/open pr 需要授权",
            "--source-task", "usage-all-credential-portability / PR 凭证验证",
            "--priority", "高",
            "--note", "flat config dedupe ok",
        ])
    data = read_json(store_path)
    assert len(data["records"]) == 1, data
    assert data["records"][0]["fields"]["最新备注"] == "flat config dedupe ok"


def main():
    with tempfile.TemporaryDirectory() as tmp:
        assert_loud_fail_without_table_id(tmp)
    with tempfile.TemporaryDirectory() as tmp:
        assert_upsert_and_done(tmp)
    with tempfile.TemporaryDirectory() as tmp:
        assert_flat_config_and_dedupe_keys(tmp)
    print("✅ regression_boss_todo passed")


if __name__ == "__main__":
    main()
