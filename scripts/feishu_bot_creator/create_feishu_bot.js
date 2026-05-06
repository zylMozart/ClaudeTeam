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

async function ensureLoggedIn(page, context) {
  await page.goto('https://open.feishu.cn/app');
  await page.waitForTimeout(2000);
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
  await page.goto('https://open.feishu.cn/app');
  await page.waitForTimeout(2000);
  await page.getByRole('button', { name: 'Create Custom App' }).click();
  await page.waitForTimeout(1000);
  await page.getByRole('textbox', { name: /\/32/ }).fill(state.appName);
  await page.locator('textarea').fill(state.appDescription);
  await page.getByRole('button', { name: 'Create', exact: true }).click();
  await page.waitForURL('**/capability/**', { timeout: 10000 });
  const appId = page.url().match(/\/app\/(cli_[a-z0-9]+)\//)?.[1];
  if (!appId) throw new Error('Failed to extract app ID from URL');
  state.appId = appId;
  log(`App created: ${appId}`);
}

async function stage_add_bot(page, _ctx, state) {
  log('Stage 2/7 add-bot: adding Bot capability...');
  await page.goto(`https://open.feishu.cn/app/${state.appId}/capability`);
  await page.waitForTimeout(2000);
  await page.getByRole('button', { name: 'Add' }).first().click();
  await page.waitForURL('**/bot**', { timeout: 5000 });
  log('Bot capability added');
}

async function stage_import_scopes(page, _ctx, state) {
  // Opens "Batch import/export scopes" → Monaco editor → paste full JSON →
  // "Next, Review New Scopes" → "Add". Monaco's textarea is overlaid by
  // spans so click .view-lines instead, then keyboard-paste.
  log('Stage 3/7 import-scopes: importing ~480 permissions...');
  await page.goto(`https://open.feishu.cn/app/${state.appId}/auth`);
  await page.waitForTimeout(2000);
  await page.getByRole('button', { name: 'Batch import/export scopes' }).click();
  await page.waitForTimeout(1000);
  const dialog = page.locator('[role="dialog"]').first();
  const editorArea = dialog.locator('.monaco-editor .view-lines').first();
  await editorArea.click();
  await page.keyboard.press('Meta+a');
  await page.waitForTimeout(200);
  await page.keyboard.press('Backspace');
  await page.waitForTimeout(200);
  await page.evaluate(async (text) => {
    await navigator.clipboard.writeText(text);
  }, SCOPES_JSON);
  await page.keyboard.press('Meta+v');
  await page.waitForTimeout(500);
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
  await page.goto(`https://open.feishu.cn/app/${state.appId}/event`);
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
  await page.goto(`https://open.feishu.cn/app/${state.appId}/event`);
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
  await page.goto(`https://open.feishu.cn/app/${state.appId}/version`);
  await page.waitForTimeout(2000);
  await page.getByRole('button', { name: 'Create Version' }).first().click();
  await page.waitForURL('**/version/create**', { timeout: 5000 });
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

Login (one-time, user scans QR):
  node create_feishu_bot.js login

Drive mode — RECOMMENDED for AI agents:
  node create_feishu_bot.js drive <name> <desc>
                  Opens chromium ONCE, runs the first incomplete
                  stage, then waits on .state/<name>.cmd for the
                  agent's next instruction. Agent advances with:
                    echo next            > .state/<name>.cmd
                    echo "redo events"   > .state/<name>.cmd
                    echo quit            > .state/<name>.cmd
                  Browser stays open across all 7 stages — no
                  re-launch overhead, no Feishu login churn.

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
        log(`🎉 All stages complete for ${name} (${state.appId})`);
        log(`   Read App ID + App Secret from open.feishu.cn/app/${state.appId}/safe`);
        break;
      }
      try {
        await runStage(page, context, next, state);
      } catch (e) {
        log(`❌ Stage [${next.id}] failed: ${e.message}`);
        log(`   Browser stays open — agent can fix the page manually,`);
        log(`   then send 'next' (skip this stage as done) or 'redo ${next.id}'.`);
      }
      const cmd = await waitForCmd(name);
      if (cmd === 'quit') {
        log('👋 Quitting drive (browser closing).');
        break;
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
      // 'next' (default) → loop body picks the next incomplete stage.
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
