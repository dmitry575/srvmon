/* Take dashboard screenshots for the README.

   Drives a headless Chromium over the DevTools protocol — no puppeteer, no
   node_modules, in keeping with the rest of the project. Node 22 already has
   a WebSocket client built in, which is all the protocol needs.

   The session cookie is injected directly, so the browser never sees the
   password and no screenshot ends up showing a login form.

     SRVMON_PASSWORD='...' node tools/screenshots.js [--out docs/screenshots]
*/
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const PASSWORD = process.env.SRVMON_PASSWORD || '';
const LOGIN = process.env.SRVMON_LOGIN || 'admin';
const BASE_PORT = parseInt(process.env.SRVMON_PORT || '8452', 10);
const LANG = process.env.SRVMON_LANG || 'en';
const OUT = (() => {
  const i = process.argv.indexOf('--out');
  return i > 0 ? process.argv[i + 1] : 'docs/screenshots';
})();
const DEBUG_PORT = 9222;

/* Processes, Ports and Services are deliberately absent: even in demo mode
   those pages show real command lines and real port numbers of whatever else
   runs on the machine. Names can be substituted; numbers cannot, because
   replacing digits in an answer would also rewrite sizes and timestamps. */
const SHOTS = [
  { name: 'overview', url: '/', wait: 2500 },
  { name: 'domains', url: '/domains', wait: 1800 },
  { name: 'domain', url: null, wait: 2500 },      // filled in from the API
  { name: 'databases', url: '/databases', wait: 1800 },
  { name: 'database-postgres', url: null, wait: 2000 },
  { name: 'storage', url: '/storage', wait: 2000 },
  { name: 'certificates', url: '/ssl', wait: 1800 },
  { name: 'events', url: '/alerts', wait: 1800 },
  { name: 'settings', url: '/settings', wait: 1800 },
];

function request(port, urlPath, opts = {}) {
  return new Promise((resolve, reject) => {
    const body = opts.body;
    const req = http.request({
      host: '127.0.0.1', port, path: urlPath, method: opts.method || 'GET',
      agent: false,
      headers: Object.assign({ Connection: 'close' }, opts.headers || {},
        body ? { 'Content-Length': Buffer.byteLength(body) } : {}),
    }, res => {
      let data = '';
      res.on('data', c => (data += c));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, data }));
    });
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

async function waitFor(fn, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      const v = await fn();
      if (v) return v;
    } catch (e) { /* not ready yet */ }
    if (Date.now() > deadline) throw new Error('timed out waiting for ' + label);
    await new Promise(r => setTimeout(r, 300));
  }
}

class CDP {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.id = 0;
    this.pending = new Map();
    this.ready = new Promise((resolve, reject) => {
      this.ws.addEventListener('open', () => resolve());
      this.ws.addEventListener('error', e => reject(new Error('websocket error')));
    });
    this.ws.addEventListener('message', ev => {
      const msg = JSON.parse(ev.data);
      const waiter = this.pending.get(msg.id);
      if (waiter) {
        this.pending.delete(msg.id);
        msg.error ? waiter.reject(new Error(msg.error.message)) : waiter.resolve(msg.result);
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  close() { this.ws.close(); }
}

async function main() {
  if (!PASSWORD) { console.error('set SRVMON_PASSWORD'); process.exit(1); }
  fs.mkdirSync(OUT, { recursive: true });

  // 1. A session of our own, so the browser never handles the password
  const login = await request(BASE_PORT, '/api/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ login: LOGIN, password: PASSWORD }),
  });
  if (login.status !== 200) { console.error('sign-in failed'); process.exit(1); }
  const cookie = String(login.headers['set-cookie'][0]).split(';')[0];
  const [cookieName, cookieValue] = cookie.split('=');

  // 2. Fill in the pages that depend on what this server actually has
  const headers = { Cookie: cookie };
  const domains = JSON.parse((await request(BASE_PORT, '/api/domains', { headers })).data);
  const dbs = JSON.parse((await request(BASE_PORT, '/api/databases', { headers })).data);
  const firstDomain = (domains.domains || [])[0];
  const pg = (dbs.databases || []).find(d => d.engine === 'postgres' && !d.system);
  for (const shot of SHOTS) {
    if (shot.name === 'domain' && firstDomain) shot.url = '/domains/' + firstDomain.id;
    if (shot.name === 'database-postgres' && pg) shot.url = '/databases/' + encodeURIComponent(pg.id);
  }

  // 3. Headless browser. Memory is capped from the outside by systemd-run,
  //    so a runaway render cannot push anything else out of RAM.
  const chrome = spawn('chromium', [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    '--disable-dev-shm-usage', '--disable-extensions', '--mute-audio',
    '--remote-debugging-port=' + DEBUG_PORT, '--window-size=1440,900',
    'about:blank',
  ], { stdio: 'ignore' });
  const cleanup = () => { try { chrome.kill('SIGTERM'); } catch (e) {} };
  process.on('exit', cleanup);

  const version = await waitFor(async () => {
    const r = await request(DEBUG_PORT, '/json/version');
    return r.status === 200 ? JSON.parse(r.data) : null;
  }, 30000, 'chromium');
  console.log('chromium ready:', version.Browser);

  const targets = JSON.parse((await request(DEBUG_PORT, '/json/list')).data);
  const page = targets.find(t => t.type === 'page');
  const cdp = new CDP(page.webSocketDebuggerUrl);
  await cdp.ready;
  await cdp.send('Page.enable');
  await cdp.send('Network.enable');
  await cdp.send('Emulation.setDeviceMetricsOverride',
    { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await cdp.send('Network.setCookie', {
    name: cookieName, value: cookieValue, domain: '127.0.0.1', path: '/',
  });
  // The language is a browser-side preference, so it is set before the first load
  await cdp.send('Page.addScriptToEvaluateOnNewDocument', {
    source: `try { localStorage.setItem('srvmon_lang', ${JSON.stringify(LANG)}); } catch (e) {}`,
  });

  for (const shot of SHOTS) {
    if (!shot.url) { console.log('skip  ' + shot.name + ' (nothing to show)'); continue; }
    await cdp.send('Page.navigate', { url: `http://127.0.0.1:${BASE_PORT}${shot.url}` });
    await new Promise(r => setTimeout(r, shot.wait));
    const metrics = await cdp.send('Page.getLayoutMetrics');
    const height = Math.min(2600, Math.ceil(metrics.cssContentSize.height));
    await cdp.send('Emulation.setDeviceMetricsOverride',
      { width: 1440, height, deviceScaleFactor: 1, mobile: false });
    await new Promise(r => setTimeout(r, 400));
    const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' });
    const file = path.join(OUT, shot.name + '.png');
    fs.writeFileSync(file, Buffer.from(data, 'base64'));
    console.log('saved ' + file + '  ' + Math.round(fs.statSync(file).size / 1024) + ' KB'
      + '  (1440x' + height + ')');
    await cdp.send('Emulation.setDeviceMetricsOverride',
      { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  }

  // 4. One narrow shot, to show the layout holds on a phone
  await cdp.send('Emulation.setDeviceMetricsOverride',
    { width: 420, height: 900, deviceScaleFactor: 2, mobile: true });
  await cdp.send('Page.navigate', { url: `http://127.0.0.1:${BASE_PORT}/` });
  await new Promise(r => setTimeout(r, 2500));
  const m = await cdp.send('Page.getLayoutMetrics');
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 420, height: Math.min(1600, Math.ceil(m.cssContentSize.height)),
    deviceScaleFactor: 2, mobile: true,
  });
  await new Promise(r => setTimeout(r, 400));
  const mobile = await cdp.send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(path.join(OUT, 'mobile.png'), Buffer.from(mobile.data, 'base64'));
  console.log('saved ' + path.join(OUT, 'mobile.png'));

  cdp.close();
  cleanup();
  console.log('done');
  process.exit(0);
}

main().catch(e => { console.error('failed:', e.message); process.exit(1); });
