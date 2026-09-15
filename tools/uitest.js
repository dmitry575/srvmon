/* Render the interface against real data without a browser.

   Only the minimum of DOM that app.js actually uses is stubbed out, then every
   page is rendered: this catches "undefined field" mistakes that would
   otherwise only show up by eye in a browser. */
const http = require('http');

// app.js checks `kid instanceof Node`, so the class has to exist
class Node {}
global.Node = Node;

const PASSWORD = process.env.SRVMON_PASSWORD || '';
// The address comes from the environment: both a live install and a fresh one
// running next to it on another port need to be checked.
const URL_PARTS = (process.env.SRVMON_URL || 'http://127.0.0.1:8452')
  .replace(/^https?:\/\//, '').split(':');
const HOST = URL_PARTS[0];
const PORT = parseInt(URL_PARTS[1] || '80', 10);
let cookie = '';

function makeEl(tag) {
  const el = Object.create(Node.prototype);
  Object.assign(el, {
    tagName: tag, children: [], attrs: {}, style: {}, _text: '',
    className: '', innerHTML: '',
    append(...kids) { kids.forEach(k => { if (k !== null && k !== undefined) this.children.push(k); }); },
    appendChild(k) { this.children.push(k); return k; },
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return this.attrs[k]; },
    addEventListener() {},
    querySelector() { return null; },
    focus() {}, scrollTo() {},
    set textContent(v) { this._text = v; },
    get textContent() { return this._text; },
    get firstChild() { return this.children[0] || null; },
  });
  return el;
}

// app.js checks `kid instanceof Node`, so the class has to exist
const root = makeEl('div');
global.document = {
  createTextNode: t => { const n = makeEl('#text'); n._text = String(t); return n; },
  createElement: makeEl,
  createElementNS: (ns, tag) => makeEl(tag),
  querySelector: sel => (sel === '#root' ? root : null),
  addEventListener() {},
  body: makeEl('body'),
  hidden: false,
};
global.window = { addEventListener() {}, scrollTo() {} };
global.history = { pushState() {} };
global.location = { pathname: '/' };
global.setInterval = () => 0;
global.clearInterval = () => {};
global.setTimeout = () => 0;
global.confirm = () => false;
global.console_error = console.error;

global.fetch = function (url, opts) {
  opts = opts || {};
  return new Promise((resolve, reject) => {
    const body = opts.body;
    // Always send Content-Length: that is what the dashboard reads, and
    // chunked request bodies come only from test clients anyway.
    const extra = body ? { 'Content-Length': Buffer.byteLength(body) } : {};
    const req = http.request({
      host: HOST, port: PORT, path: url, method: opts.method || 'GET',
      headers: Object.assign({ Connection: 'close' }, opts.headers || {}, extra,
        cookie ? { Cookie: cookie } : {}),
      agent: false,
    }, res => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => {
        const sc = res.headers['set-cookie'];
        if (sc) cookie = sc[0].split(';')[0];
        resolve({
          status: res.statusCode,
          headers: { get: k => res.headers[k.toLowerCase()] },
          json: () => Promise.resolve(JSON.parse(data || '{}')),
        });
      });
    });
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
};

function countNodes(el, depth) {
  if (depth > 60 || !el || typeof el !== 'object') return 0;
  let n = 1;
  for (const k of (el.children || [])) n += typeof k === 'object' ? countNodes(k, depth + 1) : 1;
  return n;
}
function collectText(el, depth, acc) {
  if (depth > 60 || el === null || el === undefined) return acc;
  if (typeof el !== 'object') { acc.push(String(el)); return acc; }
  if (el._text) acc.push(el._text);
  if (el.innerHTML) acc.push(String(el.innerHTML));
  for (const k of (el.children || [])) collectText(k, depth + 1, acc);
  return acc;
}

async function main() {
  const login = await fetch('/api/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ login: 'admin', password: PASSWORD }),
  });
  const lj = await login.json();
  if (!lj.ok) { console.log('FAIL: could not sign in'); process.exit(1); }

  // app.js calls boot() at the end; strip it so the run stays under our control
  const fs = require('fs');
  // The dictionary lives in its own file and loads before the application
  global.localStorage = { getItem: () => null, setItem: () => {} };
  global.navigator = { language: 'ru-RU' };
  global.document.documentElement = { lang: 'ru' };
  // Both files run as one chunk: in a browser they share a scope, and
  // splitting them across two evals would test something else entirely.
  const i18nSrc = fs.readFileSync(__dirname + '/../web/i18n.js', 'utf8');
  let src = i18nSrc + '\n'
    + fs.readFileSync(__dirname + '/../web/app.js', 'utf8').replace(/\nboot\(\);\s*$/, '\n');
  const ctx = { pages: {} };
  eval(src + '\nglobal.I18N = I18N; global.t = t; global.DICT = DICT;\nctx.pages = {overview: pageOverview, domains: pageDomains, domain: pageDomain,'
    + ' databases: pageDatabases, database: pageDatabase, storage: pageStorage,'
    + ' services: pageServices, processes: pageProcesses, network: pageNetwork,'
    + ' ssl: pageSSL, alerts: pageAlerts, settings: pageSettings};');

  const doms = await (await fetch('/api/domains')).json();
  const dbs = await (await fetch('/api/databases')).json();
  // A fresh install has neither domains nor databases — then only the pages
  // that need no data are checked. An empty dashboard must open too, not crash.
  const sqlite = dbs.databases.find(d => d.engine === 'sqlite');
  const postgres = dbs.databases.find(d => d.engine === 'postgres' && !d.system);
  const cases = [
    ['Overview', () => ctx.pages.overview()],
    ['Domains', () => ctx.pages.domains()],
    ...doms.domains.slice(0, 2).map(d =>
      ['Domain ' + d.domain, () => ctx.pages.domain(d.id)]),
    ['Databases', () => ctx.pages.databases()],
    ...(dbs.databases[0] ? [['Database ' + dbs.databases[0].name,
      () => ctx.pages.database(encodeURIComponent(dbs.databases[0].id))]] : []),
    ...(sqlite ? [['Database sqlite',
      () => ctx.pages.database(encodeURIComponent(sqlite.id))]] : []),
    ...(postgres ? [['Database postgres ' + postgres.name,
      () => ctx.pages.database(encodeURIComponent(postgres.id))]] : []),
    ['Storage', () => ctx.pages.storage()],
    ['Services', () => ctx.pages.services()],
    ['Processes', () => ctx.pages.processes()],
    ['Ports', () => ctx.pages.network()],
    ['Certificates', () => ctx.pages.ssl()],
    ['Events', () => ctx.pages.alerts()],
    ['Settings', () => ctx.pages.settings()],
  ];

  let fails = 0;
  const LANGS = (process.env.SRVMON_LANGS || 'ru,en').split(',');
  for (const lang of LANGS) {
  I18N.set(lang);
  console.log('\n[ language ' + lang + ' ]');
  for (const [name, fn] of cases) {
    root.children.length = 0;
    try {
      await fn();
      const nodes = countNodes(root, 0);
      const frags = collectText(root, 0, []);
      const text = frags.join(' ');
      const bad = /undefined|NaN|\[object Object\]/.test(text);
      // A forgotten translation means the English page contains exactly a
      // string from the dictionary. Cyrillic coming from server data (systemd
      // unit descriptions, notes from the config) is not ours to translate and
      // is not counted as a failure.
      // A forgotten t() wrapper leaves a node equal to a dictionary string
      // word for word. Substring matching is used only for long phrases: a
      // short word appears inside systemd unit descriptions and inside older
      // event rows, where the Russian text is data rather than markup.
      const exact = new Set(frags.map(x => String(x).trim()).filter(Boolean));
      const untranslated = lang === 'en'
        ? Object.keys(DICT).filter(k => {
          const key = k.trim();
          if (key.length < 3) return false;
          // Terms like TOAST or MySQL read the same in both languages: their
          // presence on an English page is expected, not an omission.
          if (DICT[k] === k) return false;
          return exact.has(key) || (key.length >= 24 && text.includes(key));
        }).slice(0, 3)
        : [];
      // Lower node threshold for an empty install: there is little to show
      const minNodes = doms.domains.length ? 30 : 12;
      if (nodes < minNodes) { console.log('  FAIL ' + name + ' — page is nearly empty (' + nodes + ' nodes)'); fails++; }
      else if (bad) {
        const m = text.match(/\S*\s?(undefined|NaN|\[object Object\])\S*/);
        console.log('  FAIL ' + name.padEnd(26) + ' — text contains "' + (m && m[0] || '').slice(0, 40) + '"');
        fails++;
      } else if (untranslated.length) {
        console.log('  FAIL ' + name.padEnd(26) + ' — untranslated: '
          + untranslated.map(x => JSON.stringify(x)).join(' | '));
        fails++;
      } else {
        console.log('  OK   ' + name.padEnd(26) + ' ' + nodes + ' nodes');
      }
    } catch (e) {
      console.log('  FAIL ' + name.padEnd(26) + ' — ' + e.message);
      fails++;
    }
  }
  }
  console.log('\nPages with problems:', fails);
  process.exit(fails ? 1 : 0);
}
main().catch(e => { console.log('run failed:', e); process.exit(1); });
