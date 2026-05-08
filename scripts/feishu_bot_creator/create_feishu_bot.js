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
  // Client-side check: bot name must be ≤32 chars. Feishu form validates
  // this with a red "Enter up to 32 characters" notice and refuses to
  // navigate, but our pollForUrl just times out and we threw a useless
  // "never navigated to capability page". Caught 2026-05-08 dryrun: agent
  // wasted 3 retries before realizing the name was 35 chars. Throw
  // upfront with the actual cause.
  if (state.appName && state.appName.length > 32) {
    throw new Error(
      `app creation: appName "${state.appName}" is ${state.appName.length} ` +
      `chars; Feishu's form rejects names over 32. Shorten and retry ` +
      `(common abbreviation: drop "ClaudeTeam" prefix or use initials).`);
  }
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
  if (!navigated) {
    // If we reach here despite the upfront length check, something
    // *else* is rejecting (e.g. name collision with existing app, or
    // the form added a new validator we don't know about). Try to
    // surface the form's actual error message before throwing generic.
    const formErr = await page.evaluate(() => {
      const errs = [...document.querySelectorAll(
        '[class*="error"], [class*="invalid"], [class*="warning"], '
        + '[role="alert"]')]
        .map(e => (e.textContent || '').trim())
        .filter(t => t.length > 0 && t.length < 200);
      return [...new Set(errs)].slice(0, 3);
    }).catch(() => []);
    const hint = formErr.length ? ` · form errors: ${JSON.stringify(formErr)}` : '';
    throw new Error(`app creation: never navigated to capability page${hint}`);
  }
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
  // "Next, Review New Scopes" → "Add".
  //
  // The mechanism that actually works (2026-05-08 verified end-to-end:
  // 232/234 tenant scopes + nearly all user scopes hit Monaco's model
  // and reach the review dialog as "Newly added scopes (232)"):
  //
  //   1. CLICK `.view-lines` (the visible text layer) with `force: true`
  //      to bypass Playwright's aria-hidden actionability complaint.
  //      Why click and not `.focus()` on the underlying textarea: a
  //      programmatic focus on the hidden inputarea puts it in IME-only
  //      mode, where Monaco renders synthetic edits in the visual layer
  //      but won't update its underlying TextModel. A real click goes
  //      through Monaco's full mouse-down → cursor-position → enter-edit
  //      pipeline, putting the editor in a state where Cmd+V actually
  //      runs the paste command on the model.
  //   2. `Cmd+A` + `Backspace` to clear (Monaco's pre-fill is the bot's
  //      current scope JSON; we want a clean slate).
  //   3. `clipboard.writeText(SCOPES_JSON)` from page context → OS
  //      clipboard.
  //   4. `keyboard.press('Meta+v')` → Playwright synthesises Cmd+V at the
  //      CDP level (`Input.dispatchKeyEvent` with isTrusted=true). Monaco
  //      reads the OS clipboard via the paste handler and applies the
  //      content via its INSERT command — model updates, dialog parses
  //      the full JSON, review screen shows the full scope list.
  //
  // Path attempts that DON'T work (recorded so future maintainers don't
  // lose another afternoon to them):
  //   - synthetic `ClipboardEvent('paste', {clipboardData: dt})` dispatched
  //     on the textarea — renders text in view-lines but doesn't go
  //     through Monaco's command pipeline; model never updates.
  //   - `keyboard.type(SCOPES_JSON, {delay:0})` — same outcome; ~4s typing
  //     visible but model stays at pre-fill.
  //   - `el.focus()` on textarea + `keyboard.press('Meta+v')` — focus
  //     alone doesn't enter Monaco's edit state; same partial render
  //     without model update.
  log('Stage 3/7 import-scopes: importing ~480 permissions...');
  await gotoWithRetry(page, `https://open.feishu.cn/app/${state.appId}/auth`);
  // Wait long enough for Monaco to fully render — 2s isn't enough on a
  // freshly-created bot (the auth page boots a Monaco instance from
  // scratch instead of restoring an already-warm one). 2026-05-08 dryrun
  // V2 caught this: drive failed on `view-lines click` with "Element is
  // not visible" even with force:true, because the .view-lines element
  // hadn't reached its final DOM position yet (scrollIntoView fails on
  // a still-mounting element). 5s clears it consistently.
  await page.waitForTimeout(5000);
  await page.getByRole('button', { name: 'Batch import/export scopes' }).click();
  await page.waitForTimeout(2500);
  const dialog = page.locator('[role="dialog"]').first();
  // force-click view-lines: aria-hidden on the layer makes Playwright's
  // visibility check refuse without `force`, but the click itself works.
  await dialog.locator('.monaco-editor .view-lines').first().click({ force: true });
  await page.waitForTimeout(300);
  await page.keyboard.press('Meta+a');
  await page.waitForTimeout(200);
  await page.keyboard.press('Backspace');
  await page.waitForTimeout(300);
  await page.evaluate(async (text) => {
    await navigator.clipboard.writeText(text);
  }, SCOPES_JSON);
  await page.waitForTimeout(200);
  await page.keyboard.press('Meta+v');
  await page.waitForTimeout(1000);
  // Quick correctness check: textarea.value should now hold a chunk of
  // our payload. Empty/short = paste mechanism broke (Feishu UI changed
  // again, focus lost, etc.) and clicking Next would submit stale data.
  const taLen = await dialog.locator('.monaco-editor textarea.inputarea').first()
    .evaluate(el => el.value.length);
  if (taLen < 200) {
    throw new Error(
      `import-scopes: textarea has only ${taLen} chars after paste ` +
      `(expected thousands). Monaco didn't accept Cmd+V — Feishu UI may ` +
      `have changed; check stage_import_scopes mechanism.`);
  }
  await page.getByRole('button', { name: 'Next, Review New Scopes' }).click();
  await page.waitForTimeout(2000);
  await page.getByRole('button', { name: 'Add', exact: true }).click();
  await page.waitForTimeout(3000);
  // Post-import verification: how many of what we paste actually got
  // through Feishu's filter (some sensitive scopes need admin approval
  // and won't auto-activate — caller logs it for the operator).
  const expected = JSON.parse(SCOPES_JSON).scopes;
  const expectedFlat = new Set([...(expected.tenant || []), ...(expected.user || [])]);
  const actualResp = await page.evaluate(async (appId) => {
    try {
      const r = await fetch(`/developers/v1/scope/applied/${appId}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: '{}',
      });
      return await r.json();
    } catch (e) { return { error: e.message }; }
  }, state.appId);
  const applied = new Set();
  for (const s of (actualResp?.data?.scopes || [])) {
    if (s.scope) applied.add(s.scope);
    else if (s.scopeId) applied.add(s.scopeId);
    else if (s.name) applied.add(s.name);
  }
  const missing = [...expectedFlat].filter(s => !applied.has(s));
  log(`scope verification: ${applied.size} applied · ${missing.length} of ${expectedFlat.size} requested didn't activate`);
  // Bringup B3 (2026-05-08): many tenants gate non-IM scopes (Calendar
  // / Docs / Wiki / Base / Mail / Contact) behind admin approval. Calling
  // the "didn't activate" warning loud without context made operators
  // think the bot was unusable. Classify by IM-core vs advanced so the
  // operator knows ClaudeTeam's basic flow still works.
  const IM_CORE = ['im:message', 'im:chat:create', 'im:chat:read'];
  const coreApplied = IM_CORE.filter(s => applied.has(s));
  const coreMissing = IM_CORE.filter(s => !applied.has(s));
  if (missing.length) {
    if (coreMissing.length === 0) {
      log(`  ✅ IM core scopes (${coreApplied.join(', ')}) granted — ClaudeTeam's basic flow will work`);
      log(`  ℹ Advanced scopes (Calendar / Docs / Wiki / Base / Mail / Contact) commonly need admin approval in your tenant`);
    } else {
      log(`  ⚠️ IM core scope(s) MISSING (${coreMissing.join(', ')}) — ClaudeTeam's basic flow may fail`);
    }
    log(`  hints: ${missing.slice(0, 6).join(', ')}${missing.length > 6 ? ` (+${missing.length - 6} more)` : ''}`);
    log(`  manually grant any missing at https://open.feishu.cn/app/${state.appId}/auth`);
  }
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
  // Version create → Save → Publish, with a data-range reconfigure
  // detour when the version's tenant scopes include any that need
  // explicit data-range (any organization-level scope does). 2026-05-08
  // dryrun_docker_v2 caught this — the page-level Save stays disabled
  // until the operator clicks the "Configure" link next to the red
  // "Please request the required data permissions" notice, walks
  // through a side-drawer dialog (sidebar with red-dotted unconfigured
  // tabs), enters edit mode, picks "All" radio, saves the inner
  // dialog, and only then page-level Save enables.
  //
  // Sequence the publish-stage agent reverse-engineered (with
  // screenshots):
  //   1. goto /version  →  Create Version → URL flips to /version/create
  //   2. wait 6s for the lazy form
  //   3. scroll bottom; check Save button — if disabled, run data-range
  //      reconfigure subroutine (see below)
  //   4. click page-level Save → URL goes to /version/<id> + a "Submit
  //      the release request?" confirm dialog auto-pops
  //   5. click that dialog's Publish button → URL back to /version
  //      list with the new version showing "Released"
  //
  // Data-range reconfigure subroutine (inside step 3):
  //   3a. click outer Configure link (next to red "Please request..." text)
  //   3b. side dialog opens: sidebar tabs with red-dotted = unconfigured;
  //       right pane shows "Range of data..." with inner Configure
  //   3c. click inner Configure to enter edit mode
  //   3d. click "All" radio (default is "Filter by condition" with
  //       blank form, which keeps inner Save disabled)
  //   3e. click inner Save → red dot turns green ✓
  //   3f. press Escape to close drawer (or it auto-closes)
  //   3g. scroll bottom again; page-level Save now enabled
  log('Stage 7/7 publish: creating version + publishing...');
  await gotoWithRetry(page, `https://open.feishu.cn/app/${state.appId}/version`);
  await page.waitForTimeout(2000);
  await page.getByRole('button', { name: 'Create Version' }).first().click();
  if (!await pollForUrl(page, '/version/create', 30000)) {
    throw new Error('publish: never navigated to version/create');
  }
  await page.waitForTimeout(6000);  // form lazy-renders; 1s isn't enough
  await scrollToBottom(page);
  const pageSave = page.getByRole('button', { name: 'Save', exact: true });
  if (await pageSave.isDisabled().catch(() => false)) {
    log('  Save disabled — running data-range reconfigure subroutine');
    // 3a — outer Configure link (Feishu styles it as a button-role with
    // link visual, hence using getByRole('button', { name: 'Configure' }))
    await page.getByRole('button', { name: 'Configure', exact: true }).first().click();
    await page.waitForTimeout(2000);
    // Side dialog opens. There may be multiple unconfigured tabs in the
    // sidebar — Contacts is usually pre-configured, others (e.g.
    // Organization-Resources-Member and Department) need manual config.
    // Loop: while sidebar has any unconfigured tab (red dot), enter that
    // tab → inner Configure → All → inner Save.
    for (let attempt = 0; attempt < 5; attempt++) {
      const dialog = page.locator('[role="dialog"]').first();
      // Has the right pane shown the "Not configured" red badge?
      const needsConfig = await dialog.getByText('Not configured', { exact: true })
        .first().isVisible({ timeout: 1000 }).catch(() => false);
      if (!needsConfig) break;
      // Inner Configure → edit mode
      await dialog.getByRole('button', { name: 'Configure', exact: true }).first()
        .click({ timeout: 5000 });
      await page.waitForTimeout(1500);
      // Pick "All" radio (default is Filter, which stays Save-disabled)
      await dialog.getByText('All', { exact: true }).first().click();
      await page.waitForTimeout(1000);
      // Inner Save
      await dialog.getByRole('button', { name: 'Save', exact: true }).click();
      await page.waitForTimeout(2500);
      // If sidebar has another red-dotted tab, click it; otherwise the
      // dialog will auto-close on the next tick.
      const nextRedTab = dialog.locator('div[class*="tab"], div[class*="sidebar"]')
        .filter({ has: page.locator('[class*="red"], [class*="error"], [class*="danger"]') })
        .first();
      if (await nextRedTab.isVisible({ timeout: 500 }).catch(() => false)) {
        await nextRedTab.click().catch(() => {});
        await page.waitForTimeout(1500);
      }
    }
    // Close side dialog if still open
    await page.keyboard.press('Escape').catch(() => {});
    await page.waitForTimeout(1500);
    await scrollToBottom(page);
  }
  // Now page-level Save should be enabled
  await pageSave.click({ timeout: 10000 });
  await page.waitForTimeout(3000);
  // "Submit the release request?" confirm dialog auto-pops
  const confirmDialog = page.locator('[role="dialog"]').first();
  await confirmDialog.getByRole('button', { name: 'Publish', exact: true })
    .click({ timeout: 10000 });
  await page.waitForTimeout(8000);
  log(`Bot "${state.appName}" (${state.appId}) published.`);

  // Extract App Secret from /baseinfo via the App-Secret row's copy
  // icon. The wrapper is `span.secret-code__btn` (NOT button-role), so
  // getByRole('button') misses it. Both App ID and App Secret rows
  // share the same `secret-code__btn` class; we differentiate by
  // *which row's copy icon* we click — find the table row whose
  // label cell text matches /App Secret/i and click the copy icon
  // INSIDE that row. Don't filter by `svg` alone (matches eye/refresh
  // icons too — verified 2026-05-08 verify run captured the SCOPES_JSON
  // from prior clipboard.writeText instead of secret because the
  // wrong icon was clicked + the previous clipboard content lingered).
  //
  // Also clear OS clipboard with a noop write before clicking to
  // surface a real failure (if Feishu's copy doesn't fire we'd read
  // the empty noop instead of a stale 14k-char JSON from earlier
  // SCOPES paste).
  log('Stage 7/7 extract: capturing App Secret from /baseinfo...');
  await gotoWithRetry(page, `https://open.feishu.cn/app/${state.appId}`);
  await page.waitForTimeout(4000);
  // Step 1: hunt the row that contains the App-Secret-mask + its copy
  // icon. Identifying the row: find a row whose text mentions "App
  // Secret" (or 应用秘钥) and click the secret-code__btn INSIDE it.
  const secret = await page.evaluate(async () => {
    // Sentinel clipboard write: lets us detect "click never triggered
    // Feishu's copy handler" cleanly. Without it, a failed click
    // silently leaves the previous clipboard content (e.g. the 14k
    // SCOPES_JSON paste from stage 3) and we'd shovel that into
    // state.appSecret. The verify-v1 dryrun caught exactly this.
    const SENTINEL = '__CT_SENTINEL__';
    try { await navigator.clipboard.writeText(SENTINEL); } catch (e) {}
    // Two paths to find the right copy button — both must agree:
    //   (1) Walk DOM for a row whose text contains "App Secret" (or
    //       Chinese 应用秘钥 / Pinyin app_secret), and within that row
    //       pick the FIRST secret-code__btn (rendering-order it's the
    //       copy icon, eye icon comes later).
    //   (2) Filter all secret-code__btn whose svg has the EXACT
    //       data-icon="CopyOutlined" (case-sensitive in Feishu),
    //       then sort by x descending — App Secret's CopyOutlined is
    //       to the right of App ID's.
    // Both should resolve the same element. If they disagree, the
    // page DOM has shifted and we'd rather fail loud than guess.
    const rowMatch = (() => {
      for (const btn of document.querySelectorAll('span.secret-code__btn')) {
        let row = btn;
        for (let i = 0; i < 8 && row; i++) {
          const t = (row.textContent || '');
          if (/App Secret|应用秘钥|app_secret/i.test(t)) {
            return row.querySelector('span.secret-code__btn');
          }
          row = row.parentElement;
        }
      }
      return null;
    })();
    const iconMatch = [...document.querySelectorAll('span.secret-code__btn')]
      .filter(el => el.querySelector('svg[data-icon="CopyOutlined"]'))
      .map(el => ({ el, x: el.getBoundingClientRect().x }))
      .sort((a, b) => b.x - a.x)[0]?.el || null;
    const targetBtn = rowMatch || iconMatch;
    if (!targetBtn) {
      return {
        error: 'no App-Secret copy button found (row-match and icon-match both null)',
      };
    }
    targetBtn.click();
    await new Promise(r => setTimeout(r, 800));
    try {
      const text = await navigator.clipboard.readText();
      if (text === SENTINEL) {
        return {
          error: 'clipboard still sentinel — click did not trigger Feishu copy handler (selector points at wrong icon, e.g. VisibleOutlined eye toggle)',
        };
      }
      return { secret: text };
    } catch (e) {
      return { error: 'clipboard read failed: ' + e.message };
    }
  });
  // Validate: a real Feishu app secret is exactly 32 chars [a-zA-Z0-9].
  // Anything else (empty / 14000-char SCOPES leak / weird) means we
  // captured the wrong content.
  const isValid = (s) => typeof s === 'string'
    && /^[A-Za-z0-9]{32}$/.test(s);
  if (secret.error) {
    throw new Error(`extract-secret: ${secret.error}`);
  }
  if (!isValid(secret.secret)) {
    const len = (secret.secret || '').length;
    const preview = (secret.secret || '').slice(0, 30).replace(/\n/g, '\\n');
    throw new Error(
      `extract-secret: clipboard content doesn't look like an App Secret ` +
      `(want 32 alphanumeric chars; got ${len} chars: "${preview}..."). ` +
      `The wrong icon was probably clicked, or Feishu's copy didn't fire ` +
      `and we got stale clipboard. Check baseinfo page DOM for changes.`);
  }
  state.appSecret = secret.secret;
  log(`  App Secret captured: ${secret.secret.slice(0, 8)}...${secret.secret.slice(-4)}`);

  // End-to-end verification (task #13): swap app_secret for a real
  // tenant_access_token via Feishu API. If this fails, the bot was
  // "published" per UI but isn't actually usable yet — better to fail
  // loud here than to let drive declare success and have the operator
  // hit `code: 232034 "app unavailable"` on every downstream call.
  log('Stage 7/7 verify: swapping app_secret for tenant_access_token...');
  const verify = await page.evaluate(async ({ appId, appSecret }) => {
    try {
      const r = await fetch('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ app_id: appId, app_secret: appSecret }),
      });
      return await r.json();
    } catch (e) { return { error: e.message }; }
  }, { appId: state.appId, appSecret: secret.secret });
  if (verify.code !== 0 || !verify.tenant_access_token) {
    throw new Error(
      `verify: tenant_token swap returned code=${verify.code} msg=${verify.msg}; ` +
      `bot ${state.appId} probably hasn't fully published or has wrong secret. ` +
      `Check https://open.feishu.cn/app/${state.appId}/version for status.`);
  }
  log(`  tenant_token swap OK · token=${verify.tenant_access_token.slice(0, 12)}...`);
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
