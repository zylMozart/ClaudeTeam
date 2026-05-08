/**
 * Feishu Bot Creator — Automates enterprise custom app creation on open.feishu.cn
 *
 * Usage:
 *   node create_feishu_bot.js login                  — Open browser for QR login, save cookies
 *   node create_feishu_bot.js create <name> <desc>   — Create a single bot
 *   node create_feishu_bot.js batch <json_file>       — Create multiple bots from JSON file
 *
 * The 7-step creation flow:
 *   1. Create Custom App (name + description)
 *   2. Add Bot capability
 *   3. Import permissions (batch import ~234 tenant+user scopes via Monaco editor)
 *   4. Configure data access range (set to "All")
 *   5. Configure events (persistent connection + message event subscriptions)
 *   6. Configure callbacks (persistent connection + card callback)
 *   7. Create version and publish
 *
 * Prerequisites:
 *   - Node.js + playwright installed (npx playwright install chromium)
 *   - First run: `node create_feishu_bot.js login` to scan QR and save session cookies
 *
 * Maintainability notes:
 *   - Feishu UI selectors are fragile — if the platform updates its UI, the most likely
 *     breakpoints are: Monaco editor interaction (Step 3), dialog button selectors (Step 4),
 *     and checkbox-based event subscription (Step 5).
 *   - Scopes are loaded from feishu_scopes.json. To add/remove scopes, edit that file.
 *   - Timeouts (waitForTimeout) are tuned for typical network latency. Increase if on slow connections.
 */

const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const COOKIE_FILE = path.join(__dirname, '.feishu_cookies.json');

const SCOPES_FILE = path.join(__dirname, '..', '..', 'config', 'feishu_scopes.json');

// Load permissions from external file (feishu_scopes.json).
// Covers ~483 scopes across IM, Docs, Drive, Base, Calendar, Directory, Wiki, Tasks, Mail, etc.
// To modify scopes, edit feishu_scopes.json directly.
const SCOPES_JSON = fs.readFileSync(SCOPES_FILE, 'utf-8').replace(/\s+/g, ' ').trim();


function log(msg) {
  console.log(`[${new Date().toLocaleTimeString()}] ${msg}`);
}

// --- Cookie Management ---

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

// Navigate to dev console; if redirected to login page, wait for QR scan (120s timeout)
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

// Feishu pages use nested scrollable containers (not window scroll).
// This finds all scrollable elements and scrolls them to the bottom.
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

async function createBot(page, context, appName, appDescription) {
  log(`Creating bot: ${appName}`);

  // Step 1: Create Custom App
  // Navigate to dev console → click "Create Custom App" → fill name/description → submit
  // After creation, URL changes to /app/cli_xxx/capability, from which we extract the app ID
  log('Step 1: Creating custom app...');
  await page.goto('https://open.feishu.cn/app');
  await page.waitForTimeout(2000);

  await page.getByRole('button', { name: 'Create Custom App' }).click();
  await page.waitForTimeout(1000);

  // Name input is identified by placeholder pattern "/32" (character limit indicator)
  await page.getByRole('textbox', { name: /\/32/ }).fill(appName);
  await page.locator('textarea').fill(appDescription);
  await page.getByRole('button', { name: 'Create', exact: true }).click();
  await page.waitForURL('**/capability/**', { timeout: 10000 });

  const appId = page.url().match(/\/app\/(cli_[a-z0-9]+)\//)?.[1];
  if (!appId) throw new Error('Failed to extract app ID from URL');
  log(`App created: ${appId}`);

  // Step 2: Add Bot capability
  // On the capability page, click the first "Add" button (corresponds to Bot feature card)
  log('Step 2: Adding Bot capability...');
  await page.getByRole('button', { name: 'Add' }).first().click();
  await page.waitForURL('**/bot**', { timeout: 5000 });
  log('Bot capability added');

  // Step 3: Import permissions via batch JSON
  // Opens the "Batch import/export scopes" dialog which contains a Monaco code editor.
  // We click into the editor's .view-lines element (not the textarea, which has pointer-events issues),
  // select all with Cmd+A, delete, then type the full JSON payload.
  // NOTE: Monaco's textarea is overlaid by spans that intercept clicks — always target .view-lines.
  log('Step 3: Importing permissions...');
  await page.goto(`https://open.feishu.cn/app/${appId}/auth`);
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

  // Step 4: Configure data access range
  // After adding permissions, a dialog prompts to configure data access range.
  // We scope the "Configure" button to the dialog to avoid clicking the wrong one
  // (there can be multiple Configure buttons on the page).
  // Sets range to "All" → Save → Confirm.
  log('Step 4: Configuring data access range...');
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

  // Step 5: Configure Event subscription
  // 1. Set subscription mode to "persistent connection" (长连接/WebSocket) — default is request URL.
  //    The edit button is found relative to the "Subscription mode" label.
  //    After clicking edit, the mode defaults to persistent connection; we just need to Save.
  //    scrollToBottom() is needed because the Save button may be below the fold.
  // 2. Add events: search "message" → check all checkboxes in both Tenant and User token tabs.
  // 3. After adding, a "Suggested scopes to add" dialog may appear — click "Add Scopes" to dismiss.
  log('Step 5: Configuring events...');
  await page.goto(`https://open.feishu.cn/app/${appId}/event`);
  await page.waitForTimeout(2000);

  const editBtn = page.locator('text=Subscription mode').first().locator('..').locator('button').first();
  await editBtn.click();
  await page.waitForTimeout(1000);

  await scrollToBottom(page);
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.waitForTimeout(1000);

  await page.getByRole('button', { name: 'Add Events' }).click();
  await page.waitForTimeout(1000);

  await page.getByPlaceholder('Search').fill('message');
  await page.waitForTimeout(1000);

  // Check all checkboxes in Tenant Token tab
  let checkboxes = page.getByRole('checkbox');
  let count = await checkboxes.count();
  for (let i = 0; i < count; i++) {
    if (!(await checkboxes.nth(i).isChecked())) {
      await checkboxes.nth(i).check();
    }
  }

  // Switch to User Token tab and check all
  await page.getByText('User Token-Based Subscription').click();
  await page.waitForTimeout(500);
  checkboxes = page.getByRole('checkbox');
  count = await checkboxes.count();
  for (let i = 0; i < count; i++) {
    if (!(await checkboxes.nth(i).isChecked())) {
      await checkboxes.nth(i).check();
    }
  }

  await page.getByRole('button', { name: 'Add', exact: true }).click();
  await page.waitForTimeout(2000);

  // "Suggested scopes to add" dialog may block further interaction — dismiss it
  try {
    const addScopesBtn = page.getByRole('button', { name: 'Add Scopes' });
    if (await addScopesBtn.isVisible({ timeout: 3000 })) {
      await addScopesBtn.click();
      await page.waitForTimeout(1000);
    }
  } catch (e) { /* no suggested scopes dialog */ }
  log('Events configured');

  // Step 6: Configure Callback
  // Switch to "Callback Configuration" tab on the same events page.
  // Same flow as events: set subscription mode to persistent connection, then add card callback.
  // The card callback (card.action.trigger) enables interactive card responses.
  log('Step 6: Configuring callbacks...');
  await page.getByText('Callback Configuration').click();
  await page.waitForTimeout(1000);

  const callbackEditBtn = page.locator('text=Subscription mode').first().locator('..').locator('button').first();
  await callbackEditBtn.click();
  await page.waitForTimeout(1000);

  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.waitForTimeout(1000);

  await page.getByRole('button', { name: 'Add callback' }).click();
  await page.waitForTimeout(1000);

  // Only one callback available (card.action.trigger) — check the first checkbox
  const cardCheckbox = page.getByRole('checkbox').first();
  await cardCheckbox.check();
  await page.waitForTimeout(300);
  await page.getByRole('button', { name: 'Add', exact: true }).click();
  await page.waitForTimeout(1000);
  log('Callbacks configured');

  // Step 7: Create version and publish
  // Navigate to version page → "Create Version" → fill defaults → Save → Publish.
  // scrollToBottom() needed to reveal the Save button on the version creation form.
  // The Publish button appears in a confirmation dialog — use .last() to target it
  // (there may be a disabled Publish button on the main page behind the dialog).
  log('Step 7: Creating version and publishing...');
  await page.goto(`https://open.feishu.cn/app/${appId}/version`);
  await page.waitForTimeout(2000);

  await page.getByRole('button', { name: 'Create Version' }).first().click();
  await page.waitForURL('**/version/create**', { timeout: 5000 });
  await page.waitForTimeout(1000);

  await scrollToBottom(page);
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.waitForTimeout(2000);

  await page.getByRole('button', { name: 'Publish', exact: true }).last().click();
  await page.waitForTimeout(3000);
  log(`Bot "${appName}" (${appId}) created and published successfully!`);

  await saveCookies(context);
  return appId;
}

async function main() {
  const args = process.argv.slice(2);

  if (args.length === 0 || args[0] === '--help') {
    console.log(`
Usage:
  node create_feishu_bot.js login              - Login and save cookies
  node create_feishu_bot.js create <name> <desc> - Create a bot
  node create_feishu_bot.js batch <json_file>   - Create multiple bots from JSON

Examples:
  node create_feishu_bot.js login
  node create_feishu_bot.js create my-bot "My awesome bot"
  node create_feishu_bot.js batch bots.json

bots.json format:
  [
    {"name": "bot1", "description": "First bot"},
    {"name": "bot2", "description": "Second bot"}
  ]
`);
    return;
  }

  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext();
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);

  try {
    await loadCookies(context);
    const page = await context.newPage();

    if (args[0] === 'login') {
      await ensureLoggedIn(page, context);
      log('Login complete. Cookies saved.');
    } else if (args[0] === 'create') {
      const name = args[1];
      const desc = args[2] || name;
      if (!name) { console.error('Error: name is required'); process.exit(1); }
      await ensureLoggedIn(page, context);
      const appId = await createBot(page, context, name, desc);
      console.log(`\nResult: ${appId}`);
    } else if (args[0] === 'batch') {
      const file = args[1];
      if (!file) { console.error('Error: json file is required'); process.exit(1); }
      const bots = JSON.parse(fs.readFileSync(file, 'utf-8'));
      await ensureLoggedIn(page, context);
      const results = [];
      for (const bot of bots) {
        try {
          const appId = await createBot(page, context, bot.name, bot.description);
          results.push({ name: bot.name, appId, status: 'success' });
        } catch (e) {
          log(`Failed to create ${bot.name}: ${e.message}`);
          results.push({ name: bot.name, status: 'failed', error: e.message });
        }
      }
      console.log('\n=== Results ===');
      console.table(results);
    }
  } finally {
    await browser.close();
  }
}

main().catch(e => { console.error(e); process.exit(1); });
