# 飞书企业自建应用（机器人）创建指南

ClaudeTeam 部署需要一个飞书企业自建 App + 机器人能力 + 一组权限 +
事件订阅 + 卡片回调 + 已发布版本。整个流程由
[`scripts/feishu_bot_creator/create_feishu_bot.js`](../scripts/feishu_bot_creator/create_feishu_bot.js)
分成 **7 个 stage**，每个 stage 内部由 Playwright 跑完一段 UI 操作，
跑完即 exit；驱动它的 AI agent 用 `status` 自检结果，再用 `next`
推进到下一 stage。**用户全程只需要扫一次 QR 登录**，之后由 agent
托管完成，最后报回 `App ID` + `App Secret`。

如果 UI 改版导致脚本某个 stage 失败，agent 可以参照本文里对应章节
的"页面变化"描述手动操作那一个 stage，再用 `next` 接着自动跑剩下
的——不必整套重来。

---

## 入口命令（drive 模式 — 浏览器全程开着）

```bash
cd scripts/feishu_bot_creator
npm install                                      # postinstall 自动装 chromium
node create_feishu_bot.js login                  # 一次性, 用户扫 QR

# 后台启动 drive — chromium 开一次, 7 个 stage 全程不关:
node create_feishu_bot.js drive <bot-name> "<desc>" \
  > /tmp/drive-<bot-name>.log 2>&1 &
```

drive 跑完一个 stage 就阻塞等命令文件，agent 读 state + log
判断 OK 后写命令推进：

```bash
# 推进下一 stage:
echo next > scripts/feishu_bot_creator/.state/<bot-name>.cmd

# 重跑某个 stage (drive 不退出, 不切浏览器):
echo "redo events" > scripts/feishu_bot_creator/.state/<bot-name>.cmd

# 提前结束:
echo quit > scripts/feishu_bot_creator/.state/<bot-name>.cmd
```

状态 / 进度查看：
- `scripts/feishu_bot_creator/.state/<bot-name>.json`：JSON state
  含 `appId` / `completedStages` / `lastError`
- `/tmp/drive-<bot-name>.log`：实时 stdout / stderr
- `node create_feishu_bot.js status --app <bot-name>`：单次打印
  state 表格

drive 跑完 publish 自动退出，浏览器关闭。Crash / kill 后再起一次
`drive` 命令从同一断点续跑（按 `completedStages` 跳过已做完的）。

> **历史 / 调试用**：还有 `stage <id>` / `next` / `create` /
> `batch` 命令——每次重新启动 chromium，不适合 agent 在 drive 之
> 外用，主要给手动调试或重跑单个 stage 用。详见
> `node create_feishu_bot.js --help`。

---

## Stage 1 — `create-app`

**目标**：在飞书开放平台创建一个企业自建应用，从 URL 拿到 App ID。

**自动操作**：
1. 跳转 [https://open.feishu.cn/app](https://open.feishu.cn/app)
2. 点 **"Create Custom App"**（创建企业自建应用）
3. 在弹出的表单填 `--name` 给出的应用名
4. 在 textarea 填 `--desc` 给出的应用描述
5. 点 **"Create"**
6. 跳转后从 URL `…/app/cli_xxx/capability` 中正则匹配 App ID
7. 写入 `.state/<bot-name>.json` 的 `appId` 字段

**对应 manual UI**：登录开放平台 → 「创建企业自建应用」→ 填名字 +
描述 → 「创建」。完成后浏览器地址栏的 `cli_xxx` 就是 App ID。

**完成判断**：state 文件里 `appId` 非空，且 `completedStages` 含
`create-app`。

**失败常见原因**：用户未登录（前置 `login` 没跑或 cookie 过期）。
解决：跑 `node create_feishu_bot.js login` 重新扫码。

---

## Stage 2 — `add-bot`

**目标**：给应用添加"机器人"能力，否则后续没办法发卡 / 收消息。

**自动操作**：
1. 跳转 `…/app/<appId>/capability`
2. 在能力列表里点第一个 **"Add"** 按钮（机器人卡片）
3. 等待跳转到 `…/bot` 页面

**对应 manual UI**：进应用 → 左侧「添加应用能力」→ 找到「机器人」
卡片点「添加」。

**完成判断**：URL 里出现 `/bot`，且 `completedStages` 含 `add-bot`。

**失败常见原因**：能力列表的 "Add" 按钮顺序变了。解决：手动加完
机器人能力后跑 `next` 跳到 stage 3。

---

## Stage 3 — `import-scopes`

**目标**：通过 Monaco 编辑器批量粘贴
[`feishu_scopes.json`](../scripts/feishu_bot_creator/feishu_scopes.json)
里的 ~480 条权限作用域（IM / Docs / Drive / Calendar / Base / Wiki /
Mail 等），一次性全部添加。

**自动操作**：
1. 跳转 `…/app/<appId>/auth`（权限管理）
2. 点 **"Batch import/export scopes"**
3. 在弹出 dialog 的 Monaco editor 里 `Cmd+A` → `Backspace` 清空
4. 把 JSON 内容写到剪贴板，`Cmd+V` 粘贴
5. 点 **"Next, Review New Scopes"**
6. 点 **"Add"** 确认导入

**对应 manual UI**：左侧「权限管理」→「批量导入/导出权限」→ 选
「导入」→ 粘贴 `feishu_scopes.json` 全部内容 → 「下一步」→ 「添加」。

**完成判断**：导入后权限列表显示约 480 条权限；`completedStages`
含 `import-scopes`。

**失败常见原因**：Monaco editor 的 textarea 被 span 覆盖（脚本就是
为此点 `.view-lines` 而不是 textarea）；或剪贴板权限被浏览器拦
截。解决：手动打开 batch import 对话框、粘贴 JSON 完成后跑 `next`。

---

## Stage 4 — `data-range`

**目标**：把"数据访问范围"设为「全部」，否则后续机器人在某些群
里读不到消息。

**自动操作**：
1. stage 3 导入权限后会自动弹"配置数据访问范围"对话框
2. 点对话框内的 **"Configure"**
3. 选 **"All"** → **"Save"** → **"Confirm"**
4. 如果对话框未弹（之前已配过），跳过这步

**对应 manual UI**：弹出对话框 →「配置」→ 选「全部」→ 「保存」→
「确认」。

**完成判断**：对话框消失，`completedStages` 含 `data-range`。

**失败常见原因**：对话框选择器变化。解决：手动在权限管理页面找
「配置数据范围」按钮设为「全部」，然后跑 `next`。

---

## Stage 5 — `events`

**目标**：把订阅模式设为**长连接（persistent connection）**而不是
回调 URL，并订阅所有 `message` 相关事件（Tenant + User token 双
tab 全勾）。

**自动操作**：
1. 跳转 `…/app/<appId>/event`
2. 找「Subscription mode」编辑按钮 → 点开 → 默认是长连接 → **Save**
3. 点 **"Add Events"** → 搜 `message` → Tenant Token tab 勾全部
   checkbox → User Token-Based Subscription tab 切换勾全部
4. **"Add"** 提交
5. 如果弹「建议添加的权限」对话框，点 **"Add Scopes"** 关掉

**对应 manual UI**：左侧「事件与回调」→「事件配置」→ 编辑订阅方
式 → 选「长连接」保存 →「添加事件」→ 搜 `message` → 两个 tab 全
勾 → 「添加」。

**完成判断**：事件列表里出现 `im.message.receive_v1` 等条目；
`completedStages` 含 `events`。

**失败常见原因**：tab 切换的文案 "User Token-Based Subscription" 改
了。解决：手动按上述步骤勾选完事件订阅后跑 `next`。

---

## Stage 6 — `callbacks`

**目标**：在「回调配置」tab 启用 **`card.action.trigger`**，让用户
点卡片按钮的事件能回到机器人（ClaudeTeam 不依赖这个但保留以备
未来用）。

**自动操作**：
1. 在 events 同一页切到 **"Callback Configuration"** tab
2. 编辑订阅方式 → 长连接 → Save
3. 点 **"Add callback"** → 勾第一个 checkbox（`card.action.trigger`）
   → **"Add"**

**对应 manual UI**：「事件与回调」→「回调配置」→ 编辑订阅方式 →
长连接保存 → 「添加回调」→ 勾「卡片回传交互」→ 「添加」。

**完成判断**：回调列表里出现 `card.action.trigger`；
`completedStages` 含 `callbacks`。

---

## Stage 7 — `publish`

**目标**：把以上所有配置打包成一个版本并发布上线，否则机器人不
会真的开始接事件。

**自动操作**：
1. 跳转 `…/app/<appId>/version`
2. 点 **"Create Version"**
3. 跳到表单，滚动到底部点 **"Save"**（保留默认值）
4. 在弹出确认框点 **"Publish"**

**对应 manual UI**：左侧「版本管理与发布」→「创建版本」→ 表单保
留默认 → 滚到底「保存」→ 弹出确认框「确认发布」。

**完成判断**：版本列表里出现新版本，状态「已启用」；
`completedStages` 含 `publish` —— 这时整个 7 stage 走完，agent
应该停下来去开放平台「凭证与基础信息」页读 App ID + App Secret，
报给用户。

---

## 完成之后

把 `App ID` + `App Secret` + 你把机器人加到的飞书群的 `chat_id`
喂给 `claudeteam`（写进 `.env` 或 `claudeteam.toml`），后面就走
[`docs/DEPLOYMENT.md`](DEPLOYMENT.md) 的 step 2-4。

`chat_id` 怎么拿：

```bash
LARK_CLI_NO_PROXY=1 lark-cli im +chat-search \
  --query "<群名关键字>" --as user
```

输出里的 `oc_xxxxxxxx` 就是 chat_id。
