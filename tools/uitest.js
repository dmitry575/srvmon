/* Прогон интерфейса на настоящих данных без браузера.
   Подменяем ровно тот минимум DOM, которым пользуется app.js, и вызываем
   отрисовку каждой страницы: так ловятся ошибки вида «поле undefined»,
   которые иначе видны только глазами в браузере. */
const http = require('http');

// app.js проверяет `kid instanceof Node`, поэтому класс должен существовать
class Node {}
global.Node = Node;

const PASSWORD = process.env.SRVMON_PASSWORD || '';
// Адрес берём из окружения: проверять приходится и рабочую установку,
// и свежую, поднятую рядом на другом порту.
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

// app.js проверяет `kid instanceof Node` — значит класс должен существовать
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
    // Тело всегда с Content-Length: сервер панели читает именно его,
    // chunked-запросы ему не нужны, их шлют только тестовые клиенты.
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
  if (!lj.ok) { console.log('СБОЙ: не удалось войти'); process.exit(1); }

  // app.js в конце сам вызывает boot(); подменяем его, чтобы управлять вручную
  const fs = require('fs');
  // Словарь языков живёт отдельным файлом и грузится раньше приложения
  global.localStorage = { getItem: () => null, setItem: () => {} };
  global.navigator = { language: 'ru-RU' };
  global.document.documentElement = { lang: 'ru' };
  // Оба файла выполняются одним куском: в браузере они делят общую область
  // видимости, и разносить их по разным eval означало бы проверять не то.
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
  // На свежей установке доменов и баз ещё нет — тогда проверяем только
  // те страницы, которым не нужны данные. Пустая панель тоже должна
  // открываться, а не падать.
  const sqlite = dbs.databases.find(d => d.engine === 'sqlite');
  const postgres = dbs.databases.find(d => d.engine === 'postgres' && !d.system);
  const cases = [
    ['Обзор', () => ctx.pages.overview()],
    ['Домены', () => ctx.pages.domains()],
    ...doms.domains.slice(0, 2).map(d =>
      ['Карточка ' + d.domain, () => ctx.pages.domain(d.id)]),
    ['Базы данных', () => ctx.pages.databases()],
    ...(dbs.databases[0] ? [['База ' + dbs.databases[0].name,
      () => ctx.pages.database(encodeURIComponent(dbs.databases[0].id))]] : []),
    ...(sqlite ? [['База sqlite',
      () => ctx.pages.database(encodeURIComponent(sqlite.id))]] : []),
    ...(postgres ? [['База postgres ' + postgres.name,
      () => ctx.pages.database(encodeURIComponent(postgres.id))]] : []),
    ['Диск', () => ctx.pages.storage()],
    ['Службы', () => ctx.pages.services()],
    ['Процессы', () => ctx.pages.processes()],
    ['Порты', () => ctx.pages.network()],
    ['Сертификаты', () => ctx.pages.ssl()],
    ['События', () => ctx.pages.alerts()],
    ['Настройки', () => ctx.pages.settings()],
  ];

  let fails = 0;
  const LANGS = (process.env.SRVMON_LANGS || 'ru,en').split(',');
  for (const lang of LANGS) {
  I18N.set(lang);
  console.log('\n[ язык ' + lang + ' ]');
  for (const [name, fn] of cases) {
    root.children.length = 0;
    try {
      await fn();
      const nodes = countNodes(root, 0);
      const frags = collectText(root, 0, []);
      const text = frags.join(' ');
      const bad = /undefined|NaN|\[object Object\]/.test(text);
      // Забытый перевод — это когда в английском режиме на странице
      // оказалась ровно та строка, что есть в словаре. Кириллица из данных
      // сервера (описания юнитов systemd, примечания из конфига) переводу
      // не подлежит и сбоем не считается.
      // Забытая обёртка t() даёт узел, дословно совпадающий со строкой
      // словаря. Вхождение проверяем только для длинных фраз: короткое слово
      // вроде «сервер» встречается внутри описаний юнитов systemd и внутри
      // старых записей событий, где русский текст — это данные, а не вёрстка.
      const exact = new Set(frags.map(x => String(x).trim()).filter(Boolean));
      const untranslated = lang === 'en'
        ? Object.keys(DICT).filter(k => {
          const key = k.trim();
          if (key.length < 3) return false;
          // Термины вроде TOAST или MySQL в обоих языках пишутся одинаково:
          // их присутствие на английской странице — норма, а не пропуск.
          if (DICT[k] === k) return false;
          return exact.has(key) || (key.length >= 24 && text.includes(key));
        }).slice(0, 3)
        : [];
      // Порог узлов ниже для пустой установки: там и показывать почти нечего
      const minNodes = doms.domains.length ? 30 : 12;
      if (nodes < minNodes) { console.log('  СБОЙ ' + name + ' — страница почти пустая (' + nodes + ' узлов)'); fails++; }
      else if (bad) {
        const m = text.match(/\S*\s?(undefined|NaN|\[object Object\])\S*/);
        console.log('  СБОЙ ' + name.padEnd(26) + ' — в тексте «' + (m && m[0] || '').slice(0, 40) + '»');
        fails++;
      } else if (untranslated.length) {
        console.log('  СБОЙ ' + name.padEnd(26) + ' — без перевода: '
          + untranslated.map(x => JSON.stringify(x)).join(' | '));
        fails++;
      } else {
        console.log('  OK   ' + name.padEnd(26) + ' ' + nodes + ' узлов');
      }
    } catch (e) {
      console.log('  СБОЙ ' + name.padEnd(26) + ' — ' + e.message);
      fails++;
    }
  }
  }
  console.log('\nСтраниц с ошибками:', fails);
  process.exit(fails ? 1 : 0);
}
main().catch(e => { console.log('ошибка прогона:', e); process.exit(1); });
