/**
 * Feishu Bot Creator — Automates enterprise custom app creation on open.feishu.cn.
 *
 * Two modes:
 *   • auto    (`create` / `batch`)  — runs all 7 stages back-to-back, fire-and-forget
 *   • staged  (`stage` / `next` / `status`) — pause between stages so the
 *                                              operating agent can sanity-check
 *                                              the live page and decide whether
 *                                              to continue or fix something.
 *
 * Stages (each is internally batch-automated; the pause is between stages):
 *   1. create-app     - Create custom app (name + desc), capture appId from URL
 *   2. add-bot        - Add Bot capability
 *   3. import-scopes  - Import ~480 permission scopes via Monaco editor batch JSON
 *   4. data-range     - Set data access range = All
 *   5. events         - Persistent-connection mode + subscribe message events
 *                       (Tenant + User token)
 *   6. callbacks      - Persistent-connection mode + add card.action.trigger
 *   7. publish        - Create version + publish
 *
 * Usage:
 *   node create_feishu_bot.js login
 *       Open browser, scan QR with Feishu mobile, save cookies.
 *
 *   node create_feishu_bot.js create <name> <desc>
 *       Auto: run all 7 stages without pausing.
 *
 *   node create_feishu_bot.js batch <json_file>
 *       Auto: each entry in [{name, description}, ...] runs all 7 stages.
 *
 *   node create_feishu_bot.js stage <stage-id> [--app <name>]
 *                                              [--name <n> --desc <d>]
 *       Run ONE stage only (e.g. `stage create-app --name my-bot --desc "..."`),
 *       then exit. State is persisted to .state/<app-name>.json so the next
 *       invocation knows where you left off. The first stage must come with
 *       --name + --desc; later stages auto-resolve appId from state.
 *
 *   node create_feishu_bot.js next [--app <name>]
 *       Run the NEXT incomplete stage based on the saved state. If --app
 *       is omitted, defaults to the most-recently-modified state file.
 *
 *   node create_feishu_bot.js status [--app <name>]
 *       Print state JSON (which stages are done, app id, last error).
 *
 * Prerequisites:
 *   - Node.js 18+
 *   - playwright (npx playwright install chromium)
 *   - First run: `node create_feishu_bot.js login` to scan QR and save cookies
 *
 * State files live at scripts/feishu_bot_creator/.state/<app-name>.json
 * and survive across runs. Delete them to start fresh.
 */

// Playwright is lazy-required inside withBrowser() so commands that don't
// need a browser (--help, status) work without `npm install` first.
const fs = require('fs');
const path = require('path');

const COOKIE_FILE = path.join(__dirname, '.feishu_cookies.json');
const SCOPES_FILE = path.join(__dirname, 'feishu_scopes.json');
const STATE_DIR = path.join(__dirname, '.state');

const SCOPES_JSON = fs.readFileSync(SCOPES_FILE, 'utf-8').replace(/\s+/g, ' ').trim();


function log(msg) {
  console.log(`[${new Date().toLocaleTimeString()}] ${msg}`);
}

// --- cookies ---

async function saveCookies(context) {
  const cookies = await context.cookies();
  fs.writeFileSync(COOKIE_FILE, JSON.stringify(cookies, null, 2));
  log(`Saved ${cookies.length} cookies`);
}

async function loadCookies(context) {
  if (!fs.existsSync(COOKIE_FILE)) return false;
  const cookies = JSON.parse(fs.readFileSync(COOKIE_FILE, 'utf-8'));
  await context.addCookies(cookies);
  log(`Loaded ${cookies.length} cookies`);
  return true;
}

// Poll page.url() until it contains `pattern` (string substring or
// RegExp). Returns true on match, false on timeout. Used instead of
// page.waitForURL because Feishu SPA routes navigate without firing
// the 'load' event that waitForURL waits for by default — the URL
// changes but the page never "loads", and waitForURL times out even
// though navigation succeeded.
async function pollForUrl(page, pattern, timeoutMs = 30000) {
  const isRegex = pattern instanceof RegExp;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const u = page.url();
    if (isRegex ? pattern.test(u) : u.includes(pattern)) return true;
    await page.waitForTimeout(400);
  }
  return false;
}

async function gotoWithRetry(page, url, opts = {}) {
  // Feishu open-platform has flaky load behavior under chromium —
  // first goto after launch occasionally returns ERR_CONNECTION_CLOSED
  // or hangs past `load`. Retry 3× with `domcontentloaded` so a single
  // glitch doesn't kill the whole drive session.
  const options = { waitUntil: 'domcontentloaded', timeout: 60000, ...opts };
  let lastErr;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      await page.goto(url, options);
      return;
    } catch (e) {
      lastErr = e;
      const oneLine = (e.message || '').split('\n')[0];
      log(`page.goto ${url} failed (attempt ${attempt}/3): ${oneLine}`);
      if (attempt < 3) await page.waitForTimeout(3000);
    }
  }
  throw lastErr;
}

async function ensureLoggedIn(page, context) {
  await gotoWithRetry(page, 'https://open.feishu.cn/app');
  // 500 ms is enough for the redirect to flush; the prior 2 s was
  // padding "to be safe" but it added 7 s to drive cold-start once
  // gotoWithRetry already waited for domcontentloaded.
  await page.waitForTimeout(500);
  if (page.url().includes('accounts.feishu.cn')) {
    log('Not logged in. Please scan QR code...');
    await page.waitForURL('**/open.feishu.cn/app**', { timeout: 120000 });
    log('Login successful!');
    await saveCookies(context);
  } else {
    log('Already logged in');
  }
}

// --- state file ---

function statePath(appName) {
  return path.join(STATE_DIR, `${appName}.json`);
}

function loadState(appName) {
  const p = statePath(appName);
  if (!fs.existsSync(p)) return null;
  return JSON.parse(fs.readFileSync(p, 'utf-8'));
}

function saveState(state) {
  fs.mkdirSync(STATE_DIR, { recursive: true });
  fs.writeFileSync(statePath(state.appName), JSON.stringify(state, null, 2));
}

// Resolve which app the user means when --app isn't passed.
// Picks the most-recently-modified state file. Returns null if none exist.
function findMostRecentApp() {
  if (!fs.existsSync(STATE_DIR)) return null;
  const files = fs.readdirSync(STATE_DIR)
    .filter(f => f.endsWith('.json'))
    .map(f => ({
      name: f.replace(/\.json$/, ''),
      mtime: fs.statSync(path.join(STATE_DIR, f)).mtimeMs,
    }))
    .sort((a, b) => b.mtime - a.mtime);
  return files[0]?.name || null;
}

// --- helpers ---

// Capture App Secret from the Credentials & Basic Info page. Feishu
// hides the secret behind a "Show" / "查看" button (eye icon); we
// click any matching button, then scan all inputs for an
// alphanumeric token of secret-like length (Feishu app secrets are
// 32 chars). Returns the secret string or null if extraction fails
// (selectors may shift between Feishu releases — best-effort, the
// caller falls back to telling the user to copy by hand).
async function captureAppSecret(page, appId) {
  // The credentials page is /info (not /safe — /safe is IP whitelist).
  // App Secret renders as a `<span class="secret-code__code">` masked
  // with `∗∗∗∗∗∗...`; revealing it requires clicking the
  // `data-icon="VisibleOutlined"` SVG inside `.secret-code__btns`.
  await gotoWithRetry(page, `https://open.feishu.cn/app/${appId}/info`);
  // The label "App Secret" appears only after the credentials block
  // hydrates — wait for it explicitly rather than fixed sleep.
  try {
    await page.locator('text=App Secret').first().waitFor({ timeout: 30000 });
  } catch (e) {
    log(`   captureAppSecret: "App Secret" label never appeared`);
    return null;
  }
  await page.waitForTimeout(800);

  // Click the eye icon to unmask. Feishu wraps it in a <span>, not a
  // <button> — so we look for the SVG by data-icon and click its
  // parent span. The Copy icon shares the same wrapper class so we
  // disambiguate by data-icon.
  const eyeIcon = page.locator('[data-icon="VisibleOutlined"]').first();
  if ((await eyeIcon.count()) === 0) {
    log(`   captureAppSecret: VisibleOutlined icon not found on /info`);
    return null;
  }
  // Click the icon's parent .secret-code__btn span (the SVG itself
  // doesn't get the click handler, the wrapper does).
  try {
    await eyeIcon.locator('xpath=ancestor::*[contains(@class,"secret-code__btn")][1]')
      .first().click({ timeout: 2000 });
  } catch (e) {
    // fallback: click the icon directly with force
    await eyeIcon.click({ force: true, timeout: 2000 }).catch(() => {});
  }
  await page.waitForTimeout(800);

  // Read the unmasked text out of .secret-code__code
  const codeSpans = await page.locator('.secret-code__code').all();
  for (const span of codeSpans) {
    const text = ((await span.textContent()) || '').trim();
    if (looksLikeRealSecret(text)) return text;
  }

  // Fallback: click the Copy icon and read clipboard. Feishu copies
  // even from the masked state.
  try {
    const copyIcon = page.locator('[data-icon="CopyOutlined"]').first();
    if ((await copyIcon.count()) > 0) {
      await copyIcon.locator('xpath=ancestor::*[contains(@class,"secret-code__btn")][1]')
        .first().click({ timeout: 2000 });
      await page.waitForTimeout(500);
      const fromClipboard = await page.evaluate(
        () => navigator.clipboard.readText().catch(() => ''));
      if (looksLikeRealSecret(fromClipboard.trim())) {
        return fromClipboard.trim();
      }
    }
  } catch (e) {}

  // Final diagnostic for failure path
  try {
    const codeText = await page.locator('.secret-code__code').first()
      .textContent().catch(() => '');
    log(`   captureAppSecret diag: .secret-code__code text = "${(codeText||'').substring(0,40)}..."`);
  } catch (e) {}
  return null;
}

// Tighter shape: Feishu app secrets are exactly 32 random
// alphanumeric chars AND contain at least one digit and at least one
// lowercase letter — random base62-ish strings rarely lack either.
// English-word concatenations on the page (UI labels, profile names)
// usually fail the digit check.
function looksLikeRealSecret(s) {
  if (!s || s.length !== 32) return false;
  if (!/^[a-zA-Z0-9]+$/.test(s)) return false;
  if (!/[0-9]/.test(s)) return false;
  if (!/[a-z]/.test(s)) return false;
  return true;
}

// Feishu pages use nested scrollable containers (not window scroll).
// Find every scrollable element and scroll it to the bottom.
async function scrollToBottom(page) {
  await page.evaluate(() => {
    document.querySelectorAll('*').forEach(el => {
      if (el.scrollHeight > el.clientHeight + 10 && el.clientHeight > 200) {
        const s = getComputedStyle(el);
        if (['auto','scroll'].includes(s.overflow) || ['auto','scroll'].includes(s.overflowY)) {
          el.scrollTop = el.scrollHeight;
        }
      }
    });
  });
  await page.waitForTimeout(300);
}

// --- stages ---

async function stage_create_app(page, _ctx, state) {
  log('Stage 1/7 create-app: creating custom app...');
  await gotoWithRetry(page, 'https://open.feishu.cn/app');
  await page.waitForTimeout(1000);
  await page.getByRole('button', { name: 'Create Custom App' }).click();
  await page.waitForTimeout(800);
  await page.getByRole('textbox', { name: /\/32/ }).fill(state.appName);
  await page.locator('textarea').fill(state.appDescription);
  await page.getByRole('button', { name: 'Create', exact: true }).click();
  // Poll URL — Feishu SPA changes location without firing 'load',
  // so page.waitForURL would 10 s-timeout even after navigation.
  const navigated = await pollForUrl(page, '/capability/', 30000);
  if (!navigated) throw new Error('app creation: never navigated to capability page');
  const appId = page.url().match(/\/app\/(cli_[a-z0-9]+)\//)?.[1];
  if (!appId) throw new Error('app creation: URL did not contain cli_ id');
  state.appId = appId;
  log(`App created: ${appId}`);
}

async function stage_add_bot(page, _ctx, state) {
  log('Stage 2/7 add-bot: adding Bot capability...');
  await gotoWithRetry(page, `https://open.feishu.cn/app/${state.appId}/capability`);
  await page.waitForTimeout(1500);
  await page.getByRole('button', { name: 'Add' }).first().click();
  const navigated = await pollForUrl(page, '/bot', 30000);
  if (!navigated) throw new Error('add-bot: never navigated to bot page');
  log('Bot capability added');
}

async function stage_import_scopes(page, _ctx, state) {
  // Opens "Batch import/export scopes" → Monaco editor → paste full JSON →
  // "Next, Review New Scopes" → "Add". Monaco's structure:
  //   .monaco-editor
  //     .view-lines   (the visible text layer; aria-hidden="true")
  //     textarea.inputarea  (the actual input target; covered by spans)
  // Click on .view-lines fails Playwright actionability checks because
  // of the aria-hidden attribute (round 2026-05-07: "element is not
  // visible" 60s retry timeout). Solution: focus the inputarea
  // directly via JS, then keyboard-paste — no click needed at all.
  log('Stage 3/7 import-scopes: importing ~480 permissions...');
  await gotoWithRetry(page, `https://open.feishu.cn/app/${state.appId}/auth`);
  await page.waitForTimeout(2000);
  await page.getByRole('button', { name: 'Batch import/export scopes' }).click();
  await page.waitForTimeout(1500);
  const dialog = page.locator('[role="dialog"]').first();
  // Focus Monaco's inputarea programmatically. Trying to click it
  // hits the overlay; .focus() on the underlying textarea bypasses
  // the overlay because focus is a non-pointer-event API.
  await dialog.locator('.monaco-editor textarea.inputarea').first()
    .evaluate(el => el.focus());
  await page.waitForTimeout(200);
  await page.keyboard.press('Meta+a');
  await page.waitForTimeout(200);
  await page.keyboard.press('Backspace');
  await page.waitForTimeout(200);
  await page.evaluate(async (text) => {
    await navigator.clipboard.writeText(text);
  }, SCOPES_JSON);
  await page.keyboard.press('Meta+v');
  await page.waitForTimeout(800);
  await page.getByRole('button', { name: 'Next, Review New Scopes' }).click();
  await page.waitForTimeout(2000);
  await page.getByRole('button', { name: 'Add', exact: true }).click();
  await page.waitForTimeout(2000);
  log('Permissions imported');
}

async function stage_data_range(page, _ctx, _state) {
  // After scopes, a dialog prompts to configure data access range. Scope
  // the "Configure" button to the dialog (page may have multiple). Set
  // range = "All" → Save → Confirm. If dialog already gone (already
  // configured in a prior run), this is a no-op.
  log('Stage 4/7 data-range: setting data access range = All...');
  try {
    const configureBtn = page.getByRole('dialog').getByRole('button', { name: 'Configure' });
    await configureBtn.click({ timeout: 5000 });
    await page.waitForTimeout(500);
    await page.getByText('All', { exact: true }).first().click();
    await page.waitForTimeout(300);
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await page.waitForTimeout(1000);
    await page.getByRole('button', { name: 'Confirm', exact: true }).click();
    await page.waitForTimeout(1000);
    log('Data access range configured');
  } catch (e) {
    log('Data access range dialog not found, may already be configured');
  }
}

async function stage_events(page, _ctx, state) {
  // 1. Subscription mode → persistent connection (long-poll WebSocket); the
  //    edit button is sibling to the "Subscription mode" label. After click
  //    Save (button below the fold; scrollToBottom first).
  // 2. Add events: search "message" → check every checkbox in both the
  //    Tenant Token and User Token tabs.
  // 3. Dismiss the "Suggested scopes to add" dialog if it appears.
  log('Stage 5/7 events: subscribing message events on persistent connection...');
  await gotoWithRetry(page, `https://open.feishu.cn/app/${state.appId}/event`);
  await page.waitForTimeout(2000);

  const editBtn = page.locator('text=Subscription mode').first()
    .locator('..').locator('button').first();
  await editBtn.click();
  await page.waitForTimeout(1000);
  await scrollToBottom(page);
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.waitForTimeout(1000);

  await page.getByRole('button', { name: 'Add Events' }).click();
  await page.waitForTimeout(1000);
  await page.getByPlaceholder('Search').fill('message');
  await page.waitForTimeout(1000);

  for (const tabHook of [null, 'User Token-Based Subscription']) {
    if (tabHook) {
      await page.getByText(tabHook).click();
      await page.waitForTimeout(500);
    }
    const checkboxes = page.getByRole('checkbox');
    const count = await checkboxes.count();
    for (let i = 0; i < count; i++) {
      if (!(await checkboxes.nth(i).isChecked())) {
        await checkboxes.nth(i).check();
      }
    }
  }

  await page.getByRole('button', { name: 'Add', exact: true }).click();
  await page.waitForTimeout(2000);

  try {
    const addScopesBtn = page.getByRole('button', { name: 'Add Scopes' });
    if (await addScopesBtn.isVisible({ timeout: 3000 })) {
      await addScopesBtn.click();
      await page.waitForTimeout(1000);
    }
  } catch (e) { /* no suggested scopes dialog */ }
  log('Events configured');
}

async function stage_callbacks(page, _ctx, state) {
  // Switch to "Callback Configuration" tab. Same pattern: persistent-conn
  // subscription → Save → "Add callback" → check the (only) checkbox →
  // Add. The single available callback is card.action.trigger.
  log('Stage 6/7 callbacks: enabling card.action.trigger...');
  await gotoWithRetry(page, `https://open.feishu.cn/app/${state.appId}/event`);
  await page.waitForTimeout(2000);
  await page.getByText('Callback Configuration').click();
  await page.waitForTimeout(1000);

  const callbackEditBtn = page.locator('text=Subscription mode').first()
    .locator('..').locator('button').first();
  await callbackEditBtn.click();
  await page.waitForTimeout(1000);
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.waitForTimeout(1000);

  await page.getByRole('button', { name: 'Add callback' }).click();
  await page.waitForTimeout(1000);
  const cardCheckbox = page.getByRole('checkbox').first();
  await cardCheckbox.check();
  await page.waitForTimeout(300);
  await page.getByRole('button', { name: 'Add', exact: true }).click();
  await page.waitForTimeout(1000);
  log('Callbacks configured');
}

async function stage_publish(page, _ctx, state) {
  // Version page → "Create Version" → defaults Save → Publish (in
  // confirmation dialog; .last() to skip the disabled main-page button).
  log('Stage 7/7 publish: creating version + publishing...');
  await gotoWithRetry(page, `https://open.feishu.cn/app/${state.appId}/version`);
  await page.waitForTimeout(2000);
  await page.getByRole('button', { name: 'Create Version' }).first().click();
  if (!await pollForUrl(page, '/version/create', 30000)) {
    throw new Error('publish: never navigated to version/create');
  }
  await page.waitForTimeout(1000);
  await scrollToBottom(page);
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.waitForTimeout(2000);
  await page.getByRole('button', { name: 'Publish', exact: true }).last().click();
  await page.waitForTimeout(3000);
  log(`Bot "${state.appName}" (${state.appId}) published.`);
}

const STAGES = [
  { id: 'create-app',    fn: stage_create_app,    summary: 'Create custom app, capture appId' },
  { id: 'add-bot',       fn: stage_add_bot,       summary: 'Add Bot capability' },
  { id: 'import-scopes', fn: stage_import_scopes, summary: 'Import ~480 permission scopes' },
  { id: 'data-range',    fn: stage_data_range,    summary: 'Set data access range = All' },
  { id: 'events',        fn: stage_events,        summary: 'Subscribe message events (persistent connection)' },
  { id: 'callbacks',     fn: stage_callbacks,     summary: 'Enable card callback' },
  { id: 'publish',       fn: stage_publish,       summary: 'Create version + publish' },
];

const STAGE_IDS = STAGES.map(s => s.id);

function findStage(stageId) {
  const stage = STAGES.find(s => s.id === stageId);
  if (!stage) {
    throw new Error(`Unknown stage: ${stageId}. Valid: ${STAGE_IDS.join(', ')}`);
  }
  return stage;
}

function nextIncompleteStage(state) {
  const done = new Set(state.completedStages || []);
  return STAGES.find(s => !done.has(s.id)) || null;
}

async function runStage(page, context, stage, state) {
  log(`▶ Stage [${stage.id}] — ${stage.summary}`);
  try {
    await stage.fn(page, context, state);
  } catch (e) {
    state.lastError = { stage: stage.id, message: e.message, at: new Date().toISOString() };
    saveState(state);
    throw e;
  }
  state.completedStages = state.completedStages || [];
  if (!state.completedStages.includes(stage.id)) {
    state.completedStages.push(stage.id);
  }
  state.lastStageAt = new Date().toISOString();
  state.lastError = null;
  saveState(state);
  await saveCookies(context);
  const remaining = STAGE_IDS.filter(id => !state.completedStages.includes(id));
  log(`✅ Stage [${stage.id}] done. Progress: ${state.completedStages.length}/${STAGES.length}.`);
  if (remaining.length === 0) {
    log(`🎉 All stages complete for ${state.appName} (${state.appId})`);
  } else {
    log(`   Next: ${remaining[0]}.`);
  }
}

// File-based IPC for `drive`: agent writes a command into
// .state/<app>.cmd and the long-running browser session reads it.
// Polling instead of fs.watch — fs.watch is flaky on macOS and we
// don't need sub-second latency for staged dispatch.
function cmdFilePath(appName) {
  return path.join(STATE_DIR, `${appName}.cmd`);
}

function clearCmd(appName) {
  try { fs.unlinkSync(cmdFilePath(appName)); } catch (e) {}
}

async function waitForCmd(appName, validCmds = ['next', 'redo', 'quit']) {
  const p = cmdFilePath(appName);
  log(`💤 Waiting for command at ${p}`);
  log(`   Agent sends one of: ${validCmds.join(' / ')} (e.g. \`echo next > ${p}\`).`);
  log(`   For 'redo <stage-id>' use the form: \`echo "redo events" > ${p}\``);
  while (true) {
    if (fs.existsSync(p)) {
      let raw;
      try { raw = fs.readFileSync(p, 'utf-8').trim(); }
      catch (e) { await new Promise(r => setTimeout(r, 500)); continue; }
      try { fs.unlinkSync(p); } catch (e) {}
      if (!raw) continue;
      const head = raw.split(/\s+/)[0];
      if (validCmds.includes(head)) return raw;
      log(`   ⚠️ unknown command "${raw}" — expected ${validCmds.join('/')}; ignoring.`);
    }
    await new Promise(r => setTimeout(r, 500));
  }
}

// --- CLI ---

function parseFlags(argv) {
  // Returns { positional: [...], flags: {appName, name, desc} }.
  const positional = [];
  const flags = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--app')      { flags.appName = argv[++i]; }
    else if (a === '--name'){ flags.name = argv[++i]; }
    else if (a === '--desc'){ flags.desc = argv[++i]; }
    else                    { positional.push(a); }
  }
  return { positional, flags };
}

function printHelp() {
  console.log(`
Feishu Bot Creator

Drive mode — RECOMMENDED entry point for AI agents:
  node create_feishu_bot.js drive <name> <desc>
                  Opens chromium ONCE, runs all 7 stages back-to-back
                  with NO pauses on the happy path. After publish it
                  auto-navigates to the credentials page, extracts
                  the App Secret, writes it to .state/<name>.json,
                  and exits cleanly. The user only scans QR on first
                  ever run (cookies persist).
                  Pauses ONLY when a stage fails — then the agent
                  writes one of:
                    echo skip            > .state/<name>.cmd
                    echo "redo <stage>"  > .state/<name>.cmd
                    echo quit            > .state/<name>.cmd
                  - skip: agent fixed it manually in the open browser
                  - redo: drive re-runs that stage
                  - quit: close browser and exit

Login only (rarely needed; drive auto-logs in):
  node create_feishu_bot.js login

Unattended mode (no pauses, no agent involvement):
  node create_feishu_bot.js create <name> <desc>
  node create_feishu_bot.js batch <bots.json>      # [{name, description}, ...]

One-shot stage (re-launch chromium per call — slower; useful for
re-running a single failed stage outside drive):
  node create_feishu_bot.js stage <stage-id> [--app <name>] [--name <n> --desc <d>]
  node create_feishu_bot.js next [--app <name>]    # run the next incomplete stage
  node create_feishu_bot.js status [--app <name>]  # show saved state

Stages: ${STAGE_IDS.join(' → ')}

State files live at scripts/feishu_bot_creator/.state/<name>.json.
Delete a state file to start fresh.
`);
}

async function withBrowser(fn) {
  let chromium;
  try {
    ({ chromium } = require('playwright'));
  } catch (e) {
    console.error('Error: playwright not installed. Run: npm install && npx playwright install chromium');
    process.exit(1);
  }
  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext();
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  try {
    await loadCookies(context);
    const page = await context.newPage();
    await fn(page, context);
  } finally {
    await browser.close();
  }
}

async function cmd_login() {
  await withBrowser(async (page, context) => {
    await ensureLoggedIn(page, context);
    log('Login complete. Cookies saved.');
  });
}

async function cmd_drive(name, desc) {
  // Long-running browser session. The browser stays open across all 7
  // stages — old `stage <id>` and `next` flow re-launch chromium each
  // time, which wastes 2-3s per call AND occasionally trips Feishu's
  // "too many login churns" rate limit. `drive` opens once, runs the
  // first incomplete stage, then waits for the agent to write `next`
  // / `redo <id>` / `quit` to .state/<name>.cmd before continuing.
  if (!name) { console.error('Error: name is required'); process.exit(1); }
  const state = loadState(name) || {
    appName: name,
    appDescription: desc || name,
    appId: null,
    completedStages: [],
    lastStageAt: null,
    lastError: null,
  };
  saveState(state);
  clearCmd(name);  // ignore stale commands from previous sessions

  await withBrowser(async (page, context) => {
    await ensureLoggedIn(page, context);
    while (true) {
      const next = nextIncompleteStage(state);
      if (!next) {
        log(`🎉 All 7 stages complete for ${name} (${state.appId})`);
        // Auto-capture App Secret so the agent doesn't need to ask
        // the user to copy it out of the browser. We click any
        // reveal-style button on /safe, then scan inputs for an
        // alphanumeric token of secret-like shape. Best-effort — if
        // it fails (selectors shifted), fall back to leaving the
        // browser parked on /safe and asking for `quit`.
        try {
          const secret = await captureAppSecret(page, state.appId);
          if (secret) {
            state.appSecret = secret;
            saveState(state);
            log(`   🔑 App Secret captured: ${secret.substring(0, 6)}...${secret.substring(secret.length - 4)}`);
            log(`   📋 Full credentials saved to ${statePath(state.appName)}`);
            log(`      App ID: ${state.appId}`);
            log(`      App Secret: ${secret}`);
            log('👋 Drive done; closing browser.');
            break;
          }
          log(`   ⚠️ Could not auto-extract App Secret (UI may have shifted).`);
          log(`      Browser parked on https://open.feishu.cn/app/${state.appId}/safe`);
          log(`      Click "Show" next to App Secret, copy it, then send 'quit'.`);
        } catch (e) {
          log(`   ⚠️ secret extraction error: ${e.message.split('\n')[0]}`);
          log(`      Browser parked on /safe; copy secret manually + 'quit'.`);
        }
        const cmd = await waitForCmd(name, ['quit']);
        if (cmd === 'quit') log('👋 Quitting drive (browser closing).');
        break;
      }
      let stageFailed = false;
      try {
        await runStage(page, context, next, state);
      } catch (e) {
        stageFailed = true;
        log(`❌ Stage [${next.id}] failed: ${e.message.split('\n')[0]}`);
        log(`   Browser stays open. Fix the page manually then send:`);
        log(`     'skip'        — you completed [${next.id}] by hand, mark done`);
        log(`     'redo ${next.id}' — drive re-tries the same stage`);
        log(`     'quit'        — close browser and exit`);
      }
      // Happy path: stage just succeeded → loop straight into the next
      // stage. Only block on a command file when something failed (or
      // we hit the all-done branch above). This keeps drive driving —
      // no manual `echo next` between every stage on a clean run.
      if (!stageFailed) continue;

      const cmd = await waitForCmd(name, ['skip', 'redo', 'quit']);
      if (cmd === 'quit') {
        log('👋 Quitting drive (browser closing).');
        break;
      }
      if (cmd === 'skip') {
        if (!state.completedStages.includes(next.id)) {
          state.completedStages.push(next.id);
        }
        state.lastError = null;
        saveState(state);
        log(`⏭  Marked [${next.id}] as done (manual takeover).`);
        continue;
      }
      if (cmd.startsWith('redo ')) {
        const redoId = cmd.substring(5).trim();
        if (!STAGE_IDS.includes(redoId)) {
          log(`   ⚠️ unknown stage "${redoId}" — ignoring.`);
          continue;
        }
        state.completedStages = (state.completedStages || []).filter(s => s !== redoId);
        saveState(state);
        log(`🔁 Will re-run stage [${redoId}].`);
      }
    }
  });
}

async function cmd_create(name, desc) {
  if (!name) { console.error('Error: name is required'); process.exit(1); }
  await withBrowser(async (page, context) => {
    await ensureLoggedIn(page, context);
    const state = loadState(name) || {
      appName: name,
      appDescription: desc || name,
      appId: null,
      completedStages: [],
      lastStageAt: null,
      lastError: null,
    };
    saveState(state);
    while (true) {
      const next = nextIncompleteStage(state);
      if (!next) break;
      await runStage(page, context, next, state);
    }
    console.log(`\nResult: ${state.appId}`);
  });
}

async function cmd_batch(file) {
  if (!file) { console.error('Error: json file is required'); process.exit(1); }
  const bots = JSON.parse(fs.readFileSync(file, 'utf-8'));
  await withBrowser(async (page, context) => {
    await ensureLoggedIn(page, context);
    const results = [];
    for (const bot of bots) {
      try {
        const state = loadState(bot.name) || {
          appName: bot.name,
          appDescription: bot.description || bot.name,
          appId: null,
          completedStages: [],
          lastStageAt: null,
          lastError: null,
        };
        saveState(state);
        while (true) {
          const next = nextIncompleteStage(state);
          if (!next) break;
          await runStage(page, context, next, state);
        }
        results.push({ name: bot.name, appId: state.appId, status: 'success' });
      } catch (e) {
        log(`Failed to create ${bot.name}: ${e.message}`);
        results.push({ name: bot.name, status: 'failed', error: e.message });
      }
    }
    console.log('\n=== Results ===');
    console.table(results);
  });
}

async function cmd_stage(stageId, flags) {
  const stage = findStage(stageId);
  // For non-first stages we resolve appName from --app or state; for the
  // first (create-app) stage, --name + --desc are required (state may not
  // yet exist).
  const isFirst = stage.id === STAGES[0].id;
  let appName = flags.appName || flags.name;
  if (isFirst) {
    if (!flags.name) {
      console.error(`Error: stage ${stageId} (first stage) requires --name and --desc`);
      process.exit(1);
    }
    appName = flags.name;
  } else if (!appName) {
    appName = findMostRecentApp();
    if (!appName) {
      console.error('Error: no saved state. Pass --app <name> or run `stage create-app --name <n> --desc <d>` first.');
      process.exit(1);
    }
  }
  let state = loadState(appName);
  if (!state) {
    if (!isFirst) {
      console.error(`Error: no state for "${appName}". Run \`stage create-app --name ${appName} --desc "..."\` first.`);
      process.exit(1);
    }
    state = {
      appName,
      appDescription: flags.desc || appName,
      appId: null,
      completedStages: [],
      lastStageAt: null,
      lastError: null,
    };
    saveState(state);
  }
  if (state.completedStages.includes(stage.id)) {
    log(`⚠️ Stage [${stage.id}] already done for ${appName}. Re-running anyway.`);
    state.completedStages = state.completedStages.filter(s => s !== stage.id);
  }
  await withBrowser(async (page, context) => {
    await ensureLoggedIn(page, context);
    await runStage(page, context, stage, state);
  });
}

async function cmd_next(flags) {
  const appName = flags.appName || findMostRecentApp();
  if (!appName) {
    console.error('Error: no saved state. Run `stage create-app --name <n> --desc <d>` first.');
    process.exit(1);
  }
  const state = loadState(appName);
  if (!state) {
    console.error(`Error: no state for "${appName}".`);
    process.exit(1);
  }
  const next = nextIncompleteStage(state);
  if (!next) {
    console.log(`All stages complete for ${appName} (${state.appId}). Nothing to do.`);
    return;
  }
  await withBrowser(async (page, context) => {
    await ensureLoggedIn(page, context);
    await runStage(page, context, next, state);
  });
}

function cmd_status(flags) {
  const appName = flags.appName || findMostRecentApp();
  if (!appName) {
    console.log('No saved state.');
    return;
  }
  const state = loadState(appName);
  if (!state) {
    console.log(`No state for "${appName}".`);
    return;
  }
  console.log(`\nApp: ${state.appName}  (id=${state.appId || '<not yet created>'})`);
  console.log(`Description: ${state.appDescription}`);
  console.log(`Completed: ${state.completedStages.length}/${STAGES.length}`);
  for (const s of STAGES) {
    const done = state.completedStages.includes(s.id);
    console.log(`  ${done ? '✅' : '⬜'} ${s.id.padEnd(14)} — ${s.summary}`);
  }
  if (state.lastError) {
    console.log(`\n❌ Last error (stage ${state.lastError.stage}): ${state.lastError.message}`);
    console.log(`   at ${state.lastError.at}`);
  }
  if (state.lastStageAt) {
    console.log(`\nLast stage at ${state.lastStageAt}`);
  }
}

async function main() {
  const args = process.argv.slice(2);
  if (args.length === 0 || args[0] === '--help' || args[0] === '-h') {
    printHelp();
    return;
  }
  const cmd = args[0];
  const { positional, flags } = parseFlags(args.slice(1));

  if (cmd === 'login') {
    await cmd_login();
  } else if (cmd === 'drive') {
    await cmd_drive(positional[0], positional[1]);
  } else if (cmd === 'create') {
    await cmd_create(positional[0], positional[1]);
  } else if (cmd === 'batch') {
    await cmd_batch(positional[0]);
  } else if (cmd === 'stage') {
    if (!positional[0]) {
      console.error(`Error: stage requires a stage-id. Valid: ${STAGE_IDS.join(', ')}`);
      process.exit(1);
    }
    await cmd_stage(positional[0], flags);
  } else if (cmd === 'next') {
    await cmd_next(flags);
  } else if (cmd === 'status') {
    cmd_status(flags);
  } else {
    console.error(`Unknown command: ${cmd}`);
    printHelp();
    process.exit(1);
  }
}

main().catch(e => {
  // Stack only on DEBUG=1; default is just the message so CLI errors
  // (unknown stage / missing flag / playwright failure) read clean.
  if (process.env.DEBUG) console.error(e);
  else console.error(`Error: ${e.message || e}`);
  process.exit(1);
});
