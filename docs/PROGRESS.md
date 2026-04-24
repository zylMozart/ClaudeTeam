# 重构进展总表

Last updated: 2026-04-24

## 今日目标

| 编号 | 目标 | 状态 |
|---|---|---|
| WATCHDOG-DOCS-12 | 文档对齐 watchdog_daemon.py 提取 | ✅ 完成 |
| WATCHDOG-DOCS-13 | 文档对齐 watchdog_orphans.py 提取 | ✅ 已覆盖（-13 节已存在） |
| phase0-baseline-docs | 建立重构进展总表 v1 | ✅ 完成（本文件） |

## 已完成

- ARCHITECTURE.md：watchdog 节标题更新为 SVC-12+SVC-13+GATE-12+GATE-13
- TESTING.md：新增独立 WATCHDOG-SVC-12+GATE-12 覆盖节；-13 节内重复项改为引用
- ROADMAP.md：新增 WATCHDOG-SVC-12/GATE-12 完成记录（在 -13 节之前）
- docs/README.md：对齐说明新增 WATCHDOG-SVC-12/GATE-12 条目

## 进行中

_（当前无进行中项）_

## 阻塞

_（当前无阻塞项）_

---

## 已确认事实

| 事实 | 来源 |
|---|---|
| `watchdog_daemon.py` 已存在于 `src/claudeteam/supervision/` | `ls` 验证 |
| `watchdog_orphans.py` 已存在于 `src/claudeteam/supervision/` | `ls` 验证 |
| WATCHDOG-SVC-13/GATE-13 文档在改动前已完整 | 阅读四份文档确认 |
| WATCHDOG-SVC-12/GATE-12 文档在改动前缺少独立 ROADMAP 章节和 docs/README.md 条目 | 阅读确认 |
| docs 目录下无 PROGRESS.md（改动前） | `ls` 确认 NOT FOUND |

## 待验证事项

| 事项 | 优先级 |
|---|---|
| py_compile 验证未改动的 Python 文件无语法问题 | 高 |
| 污染检查：workspace/.env/scripts/runtime_config.json/team.json 均为 0 | 高 |
| WATCHDOG-GATE-12 测试用例名称与 tests/compat_scripts_entrypoints.py 实际函数名一致 | 中 |

---

## 5 分钟巡视摘要模板

```
[巡视 HH:MM 北京时间]
- 在做：<当前任务>
- 完成：<本轮完成内容>
- 阻塞：<阻塞项或"无">
- 下一步：<预计 N 分钟后完成>
```
