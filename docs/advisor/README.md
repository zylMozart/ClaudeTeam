# Advisor Notes

这个文件夹由 Claude 维护，沉淀对 ClaudeTeam 项目的竞品调研、差异化定位分析和缺陷修复建议。每次做新的分析或建议落地后，更新对应文件并在下方索引中登记。

## 索引

| 文件 | 内容 | 最近更新 |
|------|------|---------|
| [competitive-landscape.md](competitive-landscape.md) | 多 CLI agent 编排赛道竞品格局 + 我们的差异化定位 | 2026-07-04 |
| [defects-and-fix-plan.md](defects-and-fix-plan.md) | 缺陷诊断与修复优先级（P0/P1/P2 + T0-T5 施工序列） | 2026-07-04 |
| [acp-migration.md](acp-migration.md) | 技术短板根因分析（拿 TUI 当 API）+ ACP 迁移方案（Runner 抽象，四阶段） | 2026-07-04 |

## 维护约定

- 每个文件顶部标注「最近更新」日期和结论摘要
- 建议状态用标记跟踪：⬜ 未开始 / 🟨 进行中 / ✅ 已完成 / ❌ 已否决
- 竞品信息注明来源链接和调研日期（这个赛道变化极快，超过一个季度的数据视为过期）
- 重大定位调整（如是否做 Slack、是否做 Windows）先在这里记录论证，再动代码
