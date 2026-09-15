/* Панель мониторинга. Ванильный JS без сборки и без внешних библиотек:
   сервер отдаёт три файла, страница живёт на одном запросе к API за отрисовку. */
'use strict';

// ---------- утилиты ----------
const $ = (sel, ctx) => (ctx || document).querySelector(sel);
const state = { page: null, timer: null, data: {}, range: '24h', sort: {}, sidebar: false };

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

function bytes(n, digits) {
  if (n === null || n === undefined || isNaN(n)) return '—';
  const u = [t('Б'), t('КБ'), t('МБ'), t('ГБ'), t('ТБ')];
  let i = 0, v = Number(n);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  const d = digits !== undefined ? digits : (v >= 100 || i === 0 ? 0 : 1);
  return v.toFixed(d) + ' ' + u[i];
}
function num(n) {
  if (n === null || n === undefined || isNaN(n)) return '—';
  return Number(n).toLocaleString(I18N.locale());
}
function pct(n, d) { return (n === null || n === undefined) ? '—' : Number(n).toFixed(d === undefined ? 1 : d) + '%'; }
function ms(n) { return (n === null || n === undefined) ? '—' : (n >= 1000 ? (n / 1000).toFixed(2) + t(' с') : Math.round(n) + t(' мс')); }
function dur(sec) {
  if (sec === null || sec === undefined) return '—';
  sec = Math.max(0, Math.floor(sec));
  const d = Math.floor(sec / 86400), hh = Math.floor(sec % 86400 / 3600), mm = Math.floor(sec % 3600 / 60);
  if (d) return d + t(' д ') + hh + t(' ч');
  if (hh) return hh + t(' ч ') + mm + t(' мин');
  if (mm) return mm + t(' мин');
  return sec + t(' с');
}
function ago(ts) {
  if (!ts) return '—';
  return dur(Math.floor(Date.now() / 1000) - ts) + t(' назад');
}
function dt(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return d.toLocaleString(I18N.locale(), { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
}
function level(v, warn, crit) { return v >= crit ? 'bad' : v >= warn ? 'warn' : 'ok'; }

async function api(path, opts) {
  const r = await fetch('/api/' + path, Object.assign({ credentials: 'same-origin' }, opts || {}));
  if (r.status === 401) { renderLogin(); throw new Error('unauth'); }
  const ct = r.headers.get('content-type') || '';
  if (!ct.includes('json')) throw new Error('HTTP ' + r.status);
  return r.json();
}

// ---------- графики ----------
const PALETTE = ['#5ccfe6', '#a371f7', '#3fb950', '#d9a227', '#f2555a', '#4d9fff'];

function chart(sets, opts) {
  /* sets: [{name, color, points:[[ts,val],...], area, fmt}] */
  opts = opts || {};
  const W = 1000, H = opts.height || 190, P = { l: 46, r: 12, t: 12, b: 22 };
  const all = sets.flatMap(s => s.points).filter(p => p[1] !== null && p[1] !== undefined && !isNaN(p[1]));
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('class', 'chart');
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.style.height = H + 'px';
  if (!all.length) {
    svg.innerHTML = `<text x="${W / 2}" y="${H / 2}" text-anchor="middle" class="axis-t">данных пока нет</text>`;
    return svg;
  }
  const xs = all.map(p => p[0]), ys = all.map(p => p[1]);
  let x0 = Math.min(...xs), x1 = Math.max(...xs);
  if (x1 === x0) x1 = x0 + 1;
  let y1 = opts.max !== undefined ? opts.max : Math.max(...ys);
  let y0 = opts.min !== undefined ? opts.min : Math.min(0, Math.min(...ys));
  if (y1 === y0) y1 = y0 + 1;
  y1 = y1 * 1.08;
  const X = t => P.l + (t - x0) / (x1 - x0) * (W - P.l - P.r);
  const Y = v => H - P.b - (v - y0) / (y1 - y0) * (H - P.t - P.b);
  const fmt = opts.fmt || (v => Math.round(v));
  let out = '';
  for (let i = 0; i <= 3; i++) {
    const v = y0 + (y1 - y0) * i / 3, y = Y(v);
    out += `<line class="grid-l" x1="${P.l}" y1="${y}" x2="${W - P.r}" y2="${y}"/>`;
    out += `<text class="axis-t" x="${P.l - 6}" y="${y + 3}" text-anchor="end">${fmt(v)}</text>`;
  }
  const span = x1 - x0;
  for (let i = 0; i <= 4; i++) {
    const t = x0 + span * i / 4, x = X(t);
    const d = new Date(t * 1000);
    const lab = span > 3 * 86400
      ? d.toLocaleDateString(I18N.locale(), { day: '2-digit', month: '2-digit' })
      : d.toLocaleTimeString(I18N.locale(), { hour: '2-digit', minute: '2-digit' });
    out += `<text class="axis-t" x="${x}" y="${H - 7}" text-anchor="${i === 0 ? 'start' : i === 4 ? 'end' : 'middle'}">${lab}</text>`;
  }
  sets.forEach((s, i) => {
    const pts = s.points.filter(p => p[1] !== null && p[1] !== undefined && !isNaN(p[1]));
    if (!pts.length) return;
    const color = s.color || PALETTE[i % PALETTE.length];
    const d = pts.map((p, j) => (j ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1)).join(' ');
    if (s.area !== false) {
      const id = 'g' + Math.random().toString(36).slice(2, 8);
      out += `<defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="${color}" stop-opacity=".22"/>
        <stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>`;
      out += `<path d="${d} L ${X(pts[pts.length - 1][0]).toFixed(1)} ${H - P.b} L ${X(pts[0][0]).toFixed(1)} ${H - P.b} Z" fill="url(#${id})" stroke="none"/>`;
    }
    out += `<path class="ln" d="${d}" stroke="${color}"/>`;
  });
  svg.innerHTML = out;
  return svg;
}

function chartBox(title, sets, opts) {
  const card = h('div', { class: 'card' }, h('h2', {}, title));
  card.append(chart(sets, opts));
  if (sets.length > 1) {
    card.append(h('div', { class: 'legend' }, sets.map((s, i) =>
      h('span', {}, h('i', { style: 'background:' + (s.color || PALETTE[i % PALETTE.length]) }), s.name))));
  }
  return card;
}

function rangeSwitch(onChange) {
  const box = h('div', { class: 'range-sw' });
  ['1h', '6h', '24h', '7d', '30d'].forEach(r => {
    box.append(h('button', {
      class: state.range === r ? 'on' : '',
      onclick: () => { state.range = r; onChange(); }
    }, r));
  });
  return box;
}

// ---------- вспомогательные элементы ----------
function metric(label, value, opts) {
  opts = opts || {};
  const cls = 'card metric' + (opts.level ? ' ' + opts.level : '');
  const kids = [h('div', { class: 'label' }, label),
    h('div', { class: 'val', html: value })];
  if (opts.bar !== undefined && opts.bar !== null) {
    kids.push(h('div', { class: 'bar' }, h('i', {
      class: opts.level || '', style: 'width:' + Math.min(100, Math.max(0, opts.bar)) + '%'
    })));
  }
  if (opts.hint) kids.push(h('div', { class: 'hint' }, opts.hint));
  return h('div', { class: cls }, kids);
}

function regPill(days, status, till) {
  if (days === null || days === undefined) return h('span', { class: 'muted' }, '—');
  const cls = status === 'ok' ? 'ok' : status === 'warning' ? 'warn' : status === 'unknown' ? '' : 'bad';
  return h('span', { class: 'pill ' + cls, title: till ? t('до ') + dt(till) : '' }, days + t(' дн'));
}

function statusOf(st) {
  const map = { ok: ['ok', t('работает')], warning: ['warn', t('внимание')], down: ['bad', t('недоступен')],
    disabled: ['off', t('выключен')], unknown: ['off', t('нет данных')] };
  const [cls, text] = map[st] || ['off', st || '—'];
  return h('span', { class: 'st ' + cls }, text);
}

function sortableTable(cols, rows, key) {
  const s = state.sort[key] || { col: null, dir: -1 };
  if (s.col !== null) {
    const c = cols[s.col];
    rows = rows.slice().sort((a, b) => {
      const av = c.sortVal ? c.sortVal(a) : c.val(a), bv = c.sortVal ? c.sortVal(b) : c.val(b);
      if (av === bv) return 0;
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      return (av > bv ? 1 : -1) * s.dir;
    });
  }
  const thead = h('tr', {}, cols.map((c, i) => h('th', {
    class: (c.num ? 'num ' : '') + (c.sortable === false ? '' : 'sortable'),
    onclick: c.sortable === false ? null : () => {
      state.sort[key] = { col: i, dir: s.col === i ? -s.dir : -1 };
      render();
    }
  }, c.title, s.col === i ? h('span', { class: 'arr' }, s.dir === 1 ? '▲' : '▼') : null)));
  const tbody = h('tbody', {}, rows.map(r => h('tr', {}, cols.map(c =>
    h('td', { class: (c.num ? 'num ' : '') + (c.cls || '') }, c.render ? c.render(r) : String(c.val(r) ?? '—'))))));
  return h('div', { class: 'tw' }, h('table', {}, h('thead', {}, thead), tbody));
}

function go(path) {
  history.pushState({}, '', path);
  state.sidebar = false;
  render();
}

document.addEventListener('click', e => {
  const a = e.target.closest('a[data-nav]');
  if (a) { e.preventDefault(); go(a.getAttribute('href')); }
});
window.addEventListener('popstate', () => render());

// ---------- каркас страницы ----------
const NAV = [
  ['/', t('Обзор'), '▣'], ['/domains', t('Домены'), '◉'], ['/databases', t('Базы данных'), '▤'],
  ['/storage', t('Диск'), '▥'], ['/services', t('Службы'), '⚙'], ['/processes', t('Процессы'), '≡'],
  ['/network', t('Порты'), '⇄'], ['/ssl', t('Сертификаты'), '⚿'], ['/alerts', t('События'), '⚑'],
  ['/settings', t('Настройки'), '⚒'],
];

function shell(content, opts) {
  opts = opts || {};
  const path = location.pathname;
  const alerts = (state.data.overview && state.data.overview.alerts) || [];
  const crit = alerts.filter(a => a.severity === 'critical').length;
  const warns = alerts.length - crit;
  const stale = state.data.overview && state.data.overview.system_ts
    && (Date.now() / 1000 - state.data.overview.system_ts) > 120;

  const nav = h('nav', {}, NAV.map(([href, title, ic]) => {
    const on = href === '/' ? path === '/' : path.startsWith(href);
    const badge = href === '/alerts' && alerts.length
      ? h('span', { class: 'badge' + (crit ? '' : ' warn') }, crit || warns) : null;
    return h('a', { href, 'data-nav': '1', class: on ? 'on' : '' },
      h('span', { class: 'ic' }, ic), title, badge);
  }));

  const side = h('aside', { class: 'sidebar' + (state.sidebar ? ' open' : '') },
    h('div', { class: 'brand' },
      h('span', { class: 'dot-live' + (stale ? ' stale' : '') }),
      h('div', {}, h('b', {}, t('Мониторинг')),
        h('small', {}, (state.data.overview?.system?.os?.hostname || '').split('.')[0] || t('сервер')))),
    nav,
    h('div', { class: 'side-foot' },
      h('div', {}, t('Обновлено: ') + (state.lastUpdate ? new Date(state.lastUpdate).toLocaleTimeString(I18N.locale()) : '—')),
      h('div', { style: 'display:flex;gap:8px;align-items:center;margin-top:6px' },
        h('button', { onclick: logout }, t('Выйти')),
        h('div', { class: 'range-sw', style: 'margin-left:auto' },
          ['ru', 'en'].map(lang => h('button', {
            class: I18N.lang === lang ? 'on' : '',
            onclick: () => { I18N.set(lang); document.documentElement.lang = lang; render(); },
          }, lang.toUpperCase()))))));

  const root = $('#root');
  root.innerHTML = '';
  const main = h('main', {}, content);
  const layout = h('div', { class: 'layout' }, side, h('div', { style: 'min-width:0' },
    h('div', { class: 'mobile-top' },
      h('button', { onclick: () => { state.sidebar = !state.sidebar; render(); } }, '☰'),
      h('b', {}, opts.title || t('Мониторинг'))),
    main));
  root.append(layout);
  if (state.sidebar) root.append(h('div', { class: 'backdrop', onclick: () => { state.sidebar = false; render(); } }));
}

function head(title, sub, right) {
  return h('div', { class: 'page-head' }, h('h1', {}, title),
    sub ? h('span', { class: 'sub' }, sub) : null,
    right ? h('span', { class: 'spacer' }, right) : null);
}

// ---------- страница: обзор ----------
async function pageOverview() {
  const d = await api('overview');
  state.data.overview = d;
  const m = await api('metrics?range=' + state.range);
  const sys = d.system || {};
  const mem = sys.memory || {};
  const load = sys.load || {};
  const disk = sys.disk || {};
  const net = sys.network || {};
  const th = state.thresholds || { disk_warning: 80, disk_critical: 90, ram_warning: 85, ram_critical: 95, cpu_warning: 85, cpu_critical: 95 };

  const cards = h('div', { class: 'grid g-metrics' },
    metric(t('Процессор'), pct(sys.cpu), {
      level: level(sys.cpu || 0, th.cpu_warning, th.cpu_critical), bar: sys.cpu,
      hint: (sys.os?.cores || '?') + t(' ядра · ') + (sys.os?.cpu_model || '').split(' ').slice(0, 3).join(' ')
    }),
    metric(t('Память'), pct(mem.percent), {
      level: level(mem.percent || 0, th.ram_warning, th.ram_critical), bar: mem.percent,
      hint: bytes(mem.used) + t(' из ') + bytes(mem.total)
    }),
    metric(t('Подкачка'), pct(mem.swap_percent), {
      level: level(mem.swap_percent || 0, 60, 95), bar: mem.swap_percent,
      hint: bytes(mem.swap_used) + t(' из ') + bytes(mem.swap_total)
    }),
    metric(t('Диск'), pct(disk.percent), {
      level: level(disk.percent || 0, th.disk_warning, th.disk_critical), bar: disk.percent,
      hint: t('свободно ') + bytes(disk.free)
    }),
    metric(t('Очередь задач'), (load.load1 ?? 0).toFixed(2), {
      level: level(load.load1 || 0, (load.cores || 2) * 2, (load.cores || 2) * 4),
      hint: [load.load5?.toFixed(2), load.load15?.toFixed(2)].join(' · ') + t(' (5/15 мин)')
    }),
    metric(t('Сеть'), bytes(net.rx_rate) + t('<span class="u">/с</span>'), {
      hint: '↑ ' + bytes(net.tx_rate) + t('/с исходящий')
    }),
    metric(t('Аптайм'), dur(sys.uptime).replace(/ (д|ч|мин|с)/, '<span class="u"> $1</span>'), {
      hint: sys.os?.name || ''
    }),
    metric(t('Температура'), sys.temp ? sys.temp + '<span class="u">°C</span>' : t('<span style="color:var(--text-faint)">н/д</span>'), {
      hint: sys.temp ? t('датчик доступен') : t('датчиков на этой машине нет')
    })
  );

  // сводки
  const sd = d.summary_domains, sdb = d.summary_db;
  const sums = h('div', { class: 'grid g-3', style: 'margin-top:12px' },
    h('div', { class: 'card' }, h('h2', {}, t('Сайты'), h('span', { class: 'r' },
      h('a', { href: '/domains', 'data-nav': '1' }, t('все →')))),
      h('div', { class: 'kv' },
        h('dt', {}, t('Доменов')), h('dd', {}, sd.total),
        h('dt', {}, t('Работают')), h('dd', { style: 'color:var(--ok)' }, sd.up),
        h('dt', {}, t('С замечаниями')), h('dd', { style: sd.warning ? 'color:var(--warn)' : '' }, sd.warning),
        h('dt', {}, t('Недоступны')), h('dd', { style: sd.down ? 'color:var(--bad)' : '' }, sd.down),
        h('dt', {}, t('Бэкендов')), h('dd', {}, sd.backends),
        h('dt', {}, t('Запросов за 24 ч')), h('dd', {}, num(sd.requests_24h)),
        h('dt', {}, t('Ответов 5xx')), h('dd', { style: sd.c5xx_24h ? 'color:var(--warn)' : '' }, num(sd.c5xx_24h)))),
    h('div', { class: 'card' }, h('h2', {}, t('Базы данных'), h('span', { class: 'r' },
      h('a', { href: '/databases', 'data-nav': '1' }, t('все →')))),
      h('div', { class: 'kv' },
        h('dt', {}, t('Баз')), h('dd', {}, sdb.count),
        h('dt', {}, t('MySQL всего')), h('dd', {}, bytes(sdb.mysql_total)),
        h('dt', {}, t('SQLite всего')), h('dd', {}, bytes(sdb.sqlite_total)),
        h('dt', {}, t('Крупнейшая')), h('dd', {}, sdb.biggest
          ? sdb.biggest.name + ' — ' + bytes(sdb.biggest.size) : '—'),
        h('dt', {}, t('Таблиц')), h('dd', {}, num(sdb.tables)),
        h('dt', {}, t('Соединений')), h('dd', {}, sdb.connections + t(' из ') +
          (sdb.mysql_status?.max_connections || '?')),
        h('dt', {}, t('Версия MySQL')), h('dd', {}, (sdb.mysql_status?.version || '—').split('-')[0]))),
    h('div', { class: 'card' }, h('h2', {}, t('Куда ушло место'), h('span', { class: 'r' },
      h('a', { href: '/storage', 'data-nav': '1' }, t('подробно →')))),
      h('div', { class: 'kv' },
        ...(d.disk_dirs || []).slice(0, 7).flatMap(x => [
          h('dt', { class: 'trunc', title: x.path }, x.path.replace(/^\/root\//, '~/')),
          h('dd', {}, bytes(x.bytes))]),
        h('dt', {}, t('Свободно')), h('dd', { style: 'color:var(--ok)' }, bytes(disk.free))))
  );

  // предупреждения
  const alertsCard = h('div', { class: 'card pad0', style: 'margin-top:12px' },
    h('h2', { style: 'padding:14px 15px 10px;margin:0' }, t('Предупреждения'),
      h('span', { class: 'r' }, h('a', { href: '/alerts', 'data-nav': '1' }, t('история →')))),
    d.alerts.length
      ? h('div', {}, d.alerts.slice(0, 8).map(a => h('div', { class: 'alert-row ' + a.severity },
        h('span', { class: 'ic' }, a.severity === 'critical' ? '🔴' : a.severity === 'warning' ? '🟡' : '🔵'),
        h('span', { class: 'msg' }, alertText(a)),
        h('span', { class: 't' }, ago(a.ts)))))
      : h('div', { class: 'empty' }, t('✓ Проблем не обнаружено')));

  // таблица сайтов
  const domCard = h('div', { class: 'card pad0', style: 'margin-top:12px' },
    h('h2', { style: 'padding:14px 15px 10px;margin:0' }, t('Сайты')),
    sortableTable([
      { title: t('Домен'), val: r => r.domain, render: r => h('a', { href: '/domains/' + r.id, 'data-nav': '1' }, r.domain) },
      { title: t('Состояние'), val: r => r.state, render: r => statusOf(r.state) },
      { title: t('Ответ'), num: true, val: r => r.resp_ms, render: r => ms(r.resp_ms) },
      { title: t('Запросов 24 ч'), num: true, val: r => r.requests_24h, render: r => num(r.requests_24h) },
      { title: t('Запр./мин'), num: true, val: r => r.rpm, render: r => (r.rpm ?? 0).toFixed(1) },
      { title: '4xx', num: true, val: r => r.c4xx_24h, render: r => num(r.c4xx_24h) },
      { title: '5xx', num: true, val: r => r.c5xx_24h, render: r => r.c5xx_24h ? h('span', { style: 'color:var(--warn)' }, num(r.c5xx_24h)) : '0' },
      { title: t('Аптайм 24 ч'), num: true, val: r => r.uptime_24h, render: r => pct(r.uptime_24h, 1) },
      { title: 'SSL', num: true, val: r => r.ssl_days, render: r => r.ssl_days === null || r.ssl_days === undefined ? '—' : h('span', { class: 'pill ' + (r.ssl_status === 'ok' ? 'ok' : r.ssl_status === 'warning' ? 'warn' : 'bad') }, r.ssl_days + t(' дн')) },
      { title: t('Домен оплачен'), num: true, val: r => r.reg_days, render: r => regPill(r.reg_days, r.reg_status, r.reg_till) },
      { title: t('Объём'), num: true, val: r => r.disk, render: r => bytes(r.disk) },
    ], d.domains, 'ov-domains'));

  const sysSeries = m.system || [];
  const charts = h('div', { class: 'grid g-2', style: 'margin-top:12px' },
    chartBox(t('Процессор и память, %'), [
      { name: 'CPU', points: sysSeries.map(r => [r.ts, r.cpu]) },
      { name: t('Память'), points: sysSeries.map(r => [r.ts, r.ram_pct]), color: PALETTE[1] },
    ], { max: 100, fmt: v => Math.round(v) + '%' }),
    chartBox(t('Запросы к сайтам'), Object.entries(m.domains || {}).map(([name, rows], i) => ({
      name, points: rows.map(r => [r.ts, r.requests]), color: PALETTE[i % PALETTE.length]
    })), { fmt: v => num(Math.round(v)) })
  );

  shell(h('div', {},
    head(t('Обзор сервера'),
      (sys.os?.name || '') + ' · ' + (sys.os?.hostname || ''),
      rangeSwitch(render)),
    cards, alertsCard, sums, domCard, charts), { title: t('Обзор') });
}

// ---------- страница: домены ----------
async function pageDomains() {
  const d = await api('domains');
  shell(h('div', {},
    head(t('Домены'), d.domains.length + t(' под наблюдением')),
    h('div', { class: 'note' },
      h('b', {}, t('Список закрытый. ')),
      t('Домен попадает сюда только если добавлен в настройках. Обнаруженные в nginx имена '),
      t('сами объектами наблюдения не становятся — их можно посмотреть в разделе «Настройки».')),
    h('div', { class: 'card pad0' }, sortableTable([
      { title: t('Домен'), val: r => r.domain, render: r => h('a', { href: '/domains/' + r.id, 'data-nav': '1' }, r.domain) },
      { title: t('Состояние'), val: r => r.state, render: r => statusOf(r.state) },
      { title: 'HTTP', num: true, val: r => r.http_status, render: r => r.http_status || '—' },
      { title: 'HTTPS', num: true, val: r => r.https_status, render: r => r.https_status || '—' },
      { title: t('Ответ'), num: true, val: r => r.resp_ms, render: r => ms(r.resp_ms) },
      { title: t('Запросов 24 ч'), num: true, val: r => r.requests_24h, render: r => num(r.requests_24h) },
      { title: t('Запр./мин'), num: true, val: r => r.rpm, render: r => (r.rpm ?? 0).toFixed(1) },
      { title: '4xx', num: true, val: r => r.c4xx_24h, render: r => num(r.c4xx_24h) },
      { title: '5xx', num: true, val: r => r.c5xx_24h, render: r => num(r.c5xx_24h) },
      { title: t('SSL до'), val: r => r.ssl_expires, render: r => r.ssl_expires ? dt(r.ssl_expires).split(',')[0] : '—' },
      { title: t('Дней'), num: true, val: r => r.ssl_days, render: r => r.ssl_days === null || r.ssl_days === undefined ? '—' : h('span', { class: 'pill ' + (r.ssl_status === 'ok' ? 'ok' : r.ssl_status === 'warning' ? 'warn' : 'bad') }, r.ssl_days) },
      { title: t('Домен оплачен до'), val: r => (r.registration || {}).paid_till, render: r => (r.registration || {}).paid_till ? dt(r.registration.paid_till).split(',')[0] : '—' },
      { title: t('Дней'), num: true, val: r => (r.registration || {}).days_left, render: r => regPill((r.registration || {}).days_left, (r.registration || {}).status, (r.registration || {}).paid_till) },
      { title: t('Регистратор'), val: r => (r.registration || {}).registrar, render: r => (r.registration || {}).registrar || '—' },
      { title: t('Служба'), val: r => r.service, render: r => r.service ? h('a', { href: '/services', 'data-nav': '1', class: 'mono' }, r.service.replace('.service', '')) : '—' },
      { title: t('Порт'), num: true, val: r => r.backend_port, render: r => r.backend_port || '—' },
      { title: t('Каталог'), val: r => r.project_dir, cls: 'mono', render: r => h('span', { class: 'trunc', title: r.project_dir }, (r.project_dir || '—').replace(/^\/root\//, '~/')) },
      { title: t('Объём'), num: true, val: r => r.disk, render: r => bytes(r.disk) },
      { title: t('Проверен'), val: r => r.last_check, render: r => ago(r.last_check) },
    ], d.domains, 'domains'))), { title: t('Домены') });
}

// ---------- страница: карточка домена ----------
async function pageDomain(id) {
  const d = await api('domains/' + encodeURIComponent(id) + '?range=' + state.range);
  if (d.error) { shell(h('div', {}, head(t('Домен не найден')), h('div', { class: 'empty' }, d.error))); return; }
  const hl = d.health || {}, av = d.availability || {}, tr = d.traffic || {}, ssl = d.ssl || {};
  const reg = d.registration || {};
  const svc = d.service || {}, proc = d.process || {};
  const h24 = tr.h24 || {};

  const top = h('div', { class: 'grid g-metrics' },
    metric(t('Состояние'), hl.state === 'ok' ? t('<span style="color:var(--ok)">Работает</span>')
      : hl.state === 'warning' ? t('<span style="color:var(--warn)">Внимание</span>')
        : t('<span style="color:var(--bad)">Недоступен</span>'),
      { hint: hl.error || ('HTTPS ' + (hl.https_status || '—')) }),
    metric(t('Ответ сейчас'), ms(hl.resp_ms), {
      level: level(hl.resp_ms || 0, 1500, 5000),
      hint: t('среднее за период ') + ms(av.avg_ms)
    }),
    metric(t('Аптайм 24 ч'), pct(av.uptime_24h), {
      level: av.uptime_24h >= 99.5 ? 'ok' : av.uptime_24h >= 95 ? 'warn' : 'bad',
      bar: av.uptime_24h, hint: t('7 дн: ') + pct(av.uptime_7d) + t(' · 30 дн: ') + pct(av.uptime_30d)
    }),
    metric(t('Запросов 24 ч'), num(h24.requests), {
      hint: t('за час: ') + num((tr.h1 || {}).requests) + ' · ' +
        (((tr.h1 || {}).requests || 0) / 60).toFixed(1) + t('/мин')
    }),
    metric(t('Ошибки 24 ч'), num((h24.c4xx || 0) + (h24.c5xx || 0)), {
      level: (h24.c5xx || 0) > 20 ? 'bad' : (h24.c4xx || 0) > 100 ? 'warn' : 'ok',
      hint: '4xx: ' + num(h24.c4xx) + ' · 5xx: ' + num(h24.c5xx)
    }),
    metric(t('Отдано 24 ч'), bytes(h24.bytes), { hint: t('по журналу nginx') }),
    metric(t('Сертификат'), (ssl.days_left ?? '—') + t('<span class="u"> дн</span>'), {
      level: ssl.status === 'ok' ? 'ok' : ssl.status === 'warning' ? 'warn' : 'bad',
      hint: ssl.issuer || '—'
    }),
    metric(t('Домен оплачен'), (reg.days_left ?? '—') + t('<span class="u"> дн</span>'), {
      level: reg.status === 'ok' ? 'ok' : reg.status === 'warning' ? 'warn' : reg.status === 'unknown' ? '' : 'bad',
      hint: reg.paid_till ? t('до ') + dt(reg.paid_till).split(',')[0] : (reg.error || t('нет данных'))
    }),
    metric(t('Объём проекта'), bytes(d.disk), { hint: (d.config?.project_dir || '').replace(/^\/root\//, '~/') })
  );

  const regWarn = reg.days_left !== null && reg.days_left !== undefined && reg.days_left < 30
    ? h('div', { class: 'note', style: 'border-color:rgba(242,85,90,.35);background:rgba(242,85,90,.07)' },
      h('b', {}, t('⚠ Регистрация домена заканчивается через ') + reg.days_left + t(' дн.')),
      t(' — оплачен до ') + dt(reg.paid_till) + t(', регистратор ') + (reg.registrar || '—'))
    : null;

  const sslWarn = ssl.days_left !== null && ssl.days_left !== undefined && ssl.days_left < 30
    ? h('div', { class: 'note', style: 'border-color:rgba(217,162,39,.35);background:rgba(217,162,39,.07)' },
      h('b', {}, t('⚠ Сертификат истекает через ') + ssl.days_left + t(' дн.')), ' — ', dt(ssl.not_after))
    : null;

  const series = tr.series || [];
  const rs = d.response_series || [];
  const charts = h('div', { class: 'grid g-2', style: 'margin-top:12px' },
    chartBox(t('Запросы'), [{ name: t('запросы'), points: series.map(r => [r.ts, r.requests]) }],
      { fmt: v => num(Math.round(v)) }),
    chartBox(t('Время ответа (собственные проверки)'),
      [{ name: t('среднее, мс'), points: rs.map(r => [r.ts, r.resp_ms]), color: PALETTE[2] },
      { name: t('максимум, мс'), points: rs.map(r => [r.ts, r.max_ms]), color: PALETTE[3], area: false }],
      { fmt: v => Math.round(v) }),
    chartBox(t('Коды ответов'), [
      { name: '2xx', points: series.map(r => [r.ts, r.c2xx]), color: PALETTE[2] },
      { name: '3xx', points: series.map(r => [r.ts, r.c3xx]), color: PALETTE[5], area: false },
      { name: '4xx', points: series.map(r => [r.ts, r.c4xx]), color: PALETTE[3], area: false },
      { name: '5xx', points: series.map(r => [r.ts, r.c5xx]), color: PALETTE[4], area: false },
    ], { fmt: v => num(Math.round(v)) }),
    chartBox(t('Отдано данных'), [{ name: t('байты'), points: series.map(r => [r.ts, r.bytes]), color: PALETTE[1] }],
      { fmt: v => bytes(v, 0) })
  );

  const periods = h('div', { class: 'card' }, h('h2', {}, t('Трафик за период')),
    h('div', { class: 'kv' },
      ...[[t('За час'), tr.h1], [t('За 24 часа'), tr.h24], [t('За 7 дней'), tr.d7], [t('За 30 дней'), tr.d30]]
        .flatMap(([lab, sum]) => [h('dt', {}, lab), h('dd', {},
          num((sum || {}).requests) + t(' запр. · ') + bytes((sum || {}).bytes) +
          ' · 5xx: ' + num((sum || {}).c5xx))])));

  const backend = h('div', { class: 'card' }, h('h2', {}, t('Бэкенд')),
    h('div', { class: 'kv' },
      h('dt', {}, t('Служба')), h('dd', {}, svc.name || '—'),
      h('dt', {}, t('Состояние')), h('dd', {}, svc.active === 'active'
        ? h('span', { class: 'st ok' }, 'active / ' + (svc.sub || '')) : h('span', { class: 'st bad' }, svc.active || '—')),
      h('dt', {}, t('Автозапуск')), h('dd', {}, svc.enabled || '—'),
      h('dt', {}, 'PID'), h('dd', {}, svc.pid || '—'),
      h('dt', {}, t('Процесс')), h('dd', {}, proc.name || '—'),
      h('dt', {}, t('Процессор')), h('dd', {}, proc.cpu === null || proc.cpu === undefined ? '—' : proc.cpu + '%'),
      h('dt', {}, t('Память')), h('dd', {}, bytes(svc.rss ?? proc.rss)),
      h('dt', {}, t('Порт')), h('dd', {}, d.config?.backend_port || '—'),
      h('dt', {}, t('Аптайм')), h('dd', {}, dur(svc.uptime)),
      h('dt', {}, t('Перезапусков')), h('dd', {}, svc.restarts ?? '—'),
      h('dt', {}, t('Прямая проверка')), h('dd', {}, d.backend_check
        ? (d.backend_check.ok ? 'HTTP ' + d.backend_check.status + t(' за ') + ms(d.backend_check.ms)
          : t('ошибка: ') + (d.backend_check.error || d.backend_check.status)) : '—')));

  const sslCard = h('div', { class: 'card' }, h('h2', {}, t('Сертификат')),
    h('div', { class: 'kv' },
      h('dt', {}, t('Издатель')), h('dd', {}, ssl.issuer || '—'),
      h('dt', {}, t('Кому выдан')), h('dd', {}, ssl.subject || '—'),
      h('dt', {}, t('Действует с')), h('dd', {}, dt(ssl.not_before)),
      h('dt', {}, t('Действует до')), h('dd', {}, dt(ssl.not_after)),
      h('dt', {}, t('Осталось')), h('dd', {}, (ssl.days_left ?? '—') + t(' дн.')),
      h('dt', {}, t('Протокол')), h('dd', {}, ssl.tls_version || '—'),
      h('dt', {}, t('Источник')), h('dd', {}, ssl.source === 'live' ? t('живое соединение с портом 443') : t('файл на диске')),
      h('dt', {}, t('Файл')), h('dd', { class: 'trunc' }, d.config?.ssl_cert || '—')));

  const regCard = h('div', { class: 'card' }, h('h2', {}, t('Регистрация домена'),
    reg.stale ? h('span', { class: 'r' }, t('данные прошлой проверки')) : null),
    h('div', { class: 'kv' },
      h('dt', {}, t('Оплачен до')), h('dd', {}, dt(reg.paid_till)),
      h('dt', {}, t('Осталось')), h('dd', {}, reg.days_left === null || reg.days_left === undefined
        ? '—' : reg.days_left + t(' дн.')),
      h('dt', {}, t('Освободится')), h('dd', {}, dt(reg.free_date)),
      h('dt', {}, t('Зарегистрирован')), h('dd', {}, dt(reg.created)),
      h('dt', {}, t('Регистратор')), h('dd', {}, reg.registrar || '—'),
      h('dt', {}, t('Состояние')), h('dd', {}, reg.state || '—'),
      h('dt', {}, t('Серверы имён')), h('dd', {}, (reg.nservers || []).join(', ') || '—'),
      h('dt', {}, t('Проверено')), h('dd', {}, ago(reg.checked || reg.ts)),
      reg.error ? h('dt', {}, t('Ошибка')) : null,
      reg.error ? h('dd', { style: 'color:var(--warn)' }, reg.error) : null));

  const avail = h('div', { class: 'card' }, h('h2', {}, t('Доступность')),
    h('div', { class: 'kv' },
      h('dt', {}, 'DNS'), h('dd', {}, (hl.dns?.addresses || []).join(', ') || '—'),
      h('dt', {}, t('Отклик DNS')), h('dd', {}, ms(hl.dns?.ms)),
      h('dt', {}, 'TCP 443'), h('dd', {}, hl.tcp?.ok ? t('открыт, ') + ms(hl.tcp.ms) : (hl.tcp?.error || '—')),
      h('dt', {}, 'HTTP'), h('dd', {}, (hl.http?.status || '—') + (hl.http?.location ? ' → ' + hl.http.location : '')),
      h('dt', {}, 'HTTPS'), h('dd', {}, hl.https?.status || hl.https?.error || '—'),
      h('dt', {}, t('Проверок за период')), h('dd', {}, num(av.checks)),
      h('dt', {}, t('Неудачных')), h('dd', { style: av.failed ? 'color:var(--bad)' : '' }, num(av.failed)),
      h('dt', {}, t('Худший отклик')), h('dd', {}, ms(av.max_ms)),
      h('dt', {}, t('Лучший отклик')), h('dd', {}, ms(av.min_ms))));

  const dbCard = d.databases.length ? h('div', { class: 'card' }, h('h2', {}, t('Базы проекта')),
    h('div', { class: 'kv' }, ...d.databases.flatMap(db => [
      h('dt', {}, db.engine === 'mysql' ? 'MySQL' : 'SQLite'),
      h('dd', {}, h('a', {
        href: '/databases/' + encodeURIComponent(db.engine === 'mysql' ? 'mysql:' + db.name : 'sqlite:' + db.path),
        'data-nav': '1'
      }, db.name || db.path))]))) : null;

  const errs = h('div', { class: 'card pad0' },
    h('h2', { style: 'padding:14px 15px 10px;margin:0' }, t('Последние ошибки')),
    d.errors.length
      ? h('pre', { class: 'log', style: 'border:none;border-radius:0' },
        d.errors.map(e => '[' + dt(e.ts) + '] ' + (e.status ? e.status + ' ' : '') + e.line).join('\n'))
      : h('div', { class: 'empty' }, t('Ошибок в журналах не найдено')));

  shell(h('div', {},
    h('div', { class: 'crumbs' }, h('a', { href: '/domains', 'data-nav': '1' }, t('← Домены'))),
    head(d.domain, d.title || '', rangeSwitch(render)),
    regWarn, sslWarn, top,
    h('div', { class: 'grid g-2', style: 'margin-top:12px' }, backend, avail, sslCard, regCard, periods),
    charts,
    h('div', { class: 'grid g-2', style: 'margin-top:12px' }, dbCard, errs)
  ), { title: d.domain });
}

// ---------- страница: базы данных ----------
async function pageDatabases() {
  const d = await api('databases');
  const list = d.databases || [];
  const user = list.filter(x => !x.system);
  const st = d.mysql_status || {};
  const mysqlTotal = list.filter(x => x.engine === 'mysql').reduce((a, b) => a + (b.size || 0), 0);
  const sqliteTotal = list.filter(x => x.engine === 'sqlite').reduce((a, b) => a + (b.size || 0), 0);
  const biggest = user.slice().sort((a, b) => (b.size || 0) - (a.size || 0))[0];

  const cards = h('div', { class: 'grid g-metrics' },
    metric(t('Баз под наблюдением'), user.length, { hint: list.length + t(' всего, включая служебные') }),
    metric('MySQL', bytes(mysqlTotal), { hint: t('версия ') + (st.version || '—').split('-')[0] }),
    metric('SQLite', bytes(sqliteTotal), { hint: t('файловые базы сайтов') }),
    metric(t('Крупнейшая'), biggest ? bytes(biggest.size) : '—', { hint: biggest ? biggest.name : '' }),
    metric(t('Таблиц'), num(user.reduce((a, b) => a + (b.tables || 0), 0)), { hint: t('во всех базах') }),
    metric(t('Соединений MySQL'), (st.Threads_connected ?? '—') + '', {
      level: level(Number(st.Threads_connected || 0), Number(st.max_connections || 151) * 0.66,
        Number(st.max_connections || 151) * 0.9),
      hint: t('предел ') + (st.max_connections || '—') + t(' · активных ') + (st.Threads_running || '—')
    }),
    metric(t('Запросов'), num(Number(st.Queries || 0)), { hint: t('с момента запуска MySQL') }),
    metric(t('MySQL работает'), dur(Number(st.Uptime || 0)), { hint: t('медленных запросов: ') + (st.Slow_queries ?? '—') })
  );

  const table = h('div', { class: 'card pad0', style: 'margin-top:12px' },
    h('h2', { style: 'padding:14px 15px 10px;margin:0' }, t('Все базы')),
    sortableTable([
      { title: t('База'), val: r => r.name, render: r => h('a', { href: '/databases/' + encodeURIComponent(r.id), 'data-nav': '1' }, r.name) },
      { title: t('Движок'), val: r => r.engine, render: r => h('span', { class: 'pill ' + (r.engine === 'mysql' ? 'info' : '') }, r.engine) },
      { title: t('Проект'), val: r => r.owner, render: r => r.owner || h('span', { class: 'muted' }, r.system ? t('служебная') : '—') },
      { title: t('Размер'), num: true, val: r => r.size, render: r => bytes(r.size) },
      { title: t('Таблиц'), num: true, val: r => r.tables, render: r => num(r.tables) },
      { title: t('Строк'), num: true, val: r => r.rows, render: r => r.rows ? num(r.rows) : '—' },
      { title: t('Индексы'), num: true, val: r => r.index_size, render: r => r.index_size ? bytes(r.index_size) : '—' },
      { title: t('Свободно'), num: true, val: r => r.free, render: r => r.free ? bytes(r.free) : '—' },
      { title: t('Соединений'), num: true, val: r => r.connections, render: r => r.connections ?? '—' },
      { title: t('Рост 7 дн'), num: true, val: r => r.growth_7d, render: r => r.growth_7d === null || r.growth_7d === undefined ? h('span', { class: 'muted' }, t('мало истории')) : h('span', { style: r.growth_7d > 15 ? 'color:var(--warn)' : '' }, (r.growth_7d > 0 ? '+' : '') + r.growth_7d + '%') },
    ], list, 'dbs'));

  shell(h('div', {},
    head(t('Базы данных'), t('MySQL и SQLite')),
    d.error ? h('div', { class: 'note' }, 'MySQL: ' + d.error) : null,
    cards, table), { title: t('Базы данных') });
}

async function pageDatabase(id) {
  const d = await api('databases/' + id + '?range=' + state.range);
  if (d.error) { shell(h('div', {}, head(t('База не найдена')), h('div', { class: 'empty' }, d.error))); return; }
  const isMy = d.engine === 'mysql';
  const cards = h('div', { class: 'grid g-metrics' },
    metric(t('Общий размер'), bytes(d.size), { hint: d.engine }),
    metric(isMy ? t('Данные') : t('Страниц'), isMy ? bytes(d.data_size) : num(d.page_count),
      { hint: isMy ? t('без индексов') : t('по ') + bytes(d.page_size) }),
    metric(isMy ? t('Индексы') : t('Файл WAL'), isMy ? bytes(d.index_size) : bytes(d.wal_size || 0),
      { hint: isMy ? t('вместе с базой') : t('журнал рядом с базой') }),
    metric(t('Свободно внутри'), bytes(d.free), { hint: t('место, которое вернёт сжатие') }),
    metric(t('Таблиц'), num(d.tables), {}),
    metric(t('Соединений'), d.connections ?? (isMy ? 0 : '—'), {
      hint: isMy ? t('сейчас к этой базе') : t('файловая база, соединений нет')
    }),
    metric(t('Строк'), d.rows ? num(d.rows) : '—', { hint: isMy ? t('оценка InnoDB') : '' }),
    metric(t('Рост за 7 дней'), d.growth_7d === null || d.growth_7d === undefined ? t('мало истории')
      : (d.growth_7d > 0 ? '+' : '') + d.growth_7d + '%',
      { level: (d.growth_7d || 0) > 15 ? 'warn' : 'ok' })
  );

  const hist = d.history || [];
  const charts = h('div', { class: 'grid g-2', style: 'margin-top:12px' },
    chartBox(t('Размер базы'), [{ name: t('размер'), points: hist.map(r => [r.ts, r.size]) }],
      { fmt: v => bytes(v, 0) }),
    isMy ? chartBox(t('Соединения'), [{ name: t('соединений'), points: hist.map(r => [r.ts, r.conns]), color: PALETTE[1] }],
      { fmt: v => Math.round(v) }) : null
  );

  const tcols = isMy ? [
    { title: t('Таблица'), val: r => r.name, cls: 'mono' },
    { title: t('Строк'), num: true, val: r => r.rows, render: r => num(r.rows) },
    { title: t('Всего'), num: true, val: r => r.total_size, render: r => bytes(r.total_size) },
    { title: t('Данные'), num: true, val: r => r.data_size, render: r => bytes(r.data_size) },
    { title: t('Индексы'), num: true, val: r => r.index_size, render: r => bytes(r.index_size) },
    { title: t('Свободно'), num: true, val: r => r.free, render: r => bytes(r.free) },
    { title: t('Движок'), val: r => r.engine },
    { title: t('Изменена'), val: r => r.updated, render: r => r.updated || '—' },
  ] : [
    { title: t('Таблица'), val: r => r.name, cls: 'mono' },
    { title: t('Строк'), num: true, val: r => r.rows, render: r => num(r.rows) },
    { title: t('Размер'), num: true, val: r => r.total_size, render: r => r.total_size ? bytes(r.total_size) : '—' },
    { title: t('Индексов'), num: true, val: r => r.indexes },
  ];

  const tables = h('div', { class: 'card pad0', style: 'margin-top:12px' },
    h('h2', { style: 'padding:14px 15px 10px;margin:0' }, t('Крупнейшие таблицы (до 20)')),
    (d.tables_list || []).length ? sortableTable(tcols, d.tables_list, 'dbtables')
      : h('div', { class: 'empty' }, d.tables_error || t('нет данных')));

  const pl = (d.processlist || []).length ? h('div', { class: 'card pad0', style: 'margin-top:12px' },
    h('h2', { style: 'padding:14px 15px 10px;margin:0' }, t('Активные соединения')),
    sortableTable([
      { title: 'ID', val: r => r.id }, { title: t('Пользователь'), val: r => r.user },
      { title: t('Команда'), val: r => r.command },
      { title: t('Секунд'), num: true, val: r => r.time },
      { title: t('Состояние'), val: r => r.state, render: r => r.state || '—' },
    ], d.processlist, 'dbpl')) : null;

  shell(h('div', {},
    h('div', { class: 'crumbs' }, h('a', { href: '/databases', 'data-nav': '1' }, t('← Базы данных'))),
    head(d.name, d.engine === 'mysql' ? 'MySQL' : d.path, rangeSwitch(render)),
    cards, charts, tables, pl), { title: d.name });
}

// ---------- страница: диск ----------
async function pageStorage() {
  const d = await api('storage');
  const root = d.filesystems.find(f => f.mount === '/') || d.filesystems[0] || {};
  const dirs = d.directories || [];
  const maxDir = Math.max(1, ...dirs.map(x => x.bytes || 0));

  const cards = h('div', { class: 'grid g-metrics' },
    metric(t('Всего'), bytes(root.total), { hint: root.device }),
    metric(t('Занято'), bytes(root.used), { level: level(root.percent || 0, 80, 90), bar: root.percent }),
    metric(t('Свободно'), bytes(root.free), { hint: pct(100 - (root.percent || 0)) + t(' от объёма') }),
    metric(t('Заполнение'), pct(root.percent), { level: level(root.percent || 0, 80, 90), bar: root.percent })
  );

  const fsTable = h('div', { class: 'card pad0', style: 'margin-top:12px' },
    h('h2', { style: 'padding:14px 15px 10px;margin:0' }, t('Файловые системы')),
    sortableTable([
      { title: t('Точка монтирования'), val: r => r.mount, cls: 'mono' },
      { title: t('Устройство'), val: r => r.device, cls: 'mono' },
      { title: t('Тип'), val: r => r.fstype },
      { title: t('Всего'), num: true, val: r => r.total, render: r => bytes(r.total) },
      { title: t('Занято'), num: true, val: r => r.used, render: r => bytes(r.used) },
      { title: t('Свободно'), num: true, val: r => r.free, render: r => bytes(r.free) },
      {
        title: t('Заполнение'), num: true, val: r => r.percent, render: r => h('span', {
          style: r.percent > 90 ? 'color:var(--bad)' : r.percent > 80 ? 'color:var(--warn)' : ''
        }, pct(r.percent))
      },
    ], d.filesystems, 'fs'));

  const dirCard = h('div', { class: 'card pad0', style: 'margin-top:12px' },
    h('h2', { style: 'padding:14px 15px 10px;margin:0' }, t('Крупнейшие каталоги')),
    h('div', {}, dirs.map(x => h('div', { class: 'tree-item' },
      h('span', { class: 'nm', title: x.path }, x.path),
      h('span', { style: 'width:130px;flex:none' },
        h('span', { class: 'bar', style: 'margin:0' },
          h('i', { style: 'width:' + (100 * (x.bytes || 0) / maxDir) + '%' }))),
      h('span', { class: 'sz', style: 'width:82px;text-align:right' }, bytes(x.bytes)),
      h('span', { class: 'sz muted', style: 'width:92px;text-align:right' },
        x.growth_7d === null || x.growth_7d === undefined ? '' :
          (x.growth_7d > 0 ? '+' : '') + bytes(Math.abs(x.growth_7d)) + t(' / 7 дн'))))));

  const charts = chartBox(t('Занятое место на /'), [{
    name: t('занято'), points: (d.history || []).map(r => [r.ts, r.used])
  }], { fmt: v => bytes(v, 0) });

  // просмотр дерева
  const treeBox = h('div', { class: 'card pad0', style: 'margin-top:12px' },
    h('h2', { style: 'padding:14px 15px 10px;margin:0' }, t('Просмотр каталогов'),
      h('span', { class: 'r' }, t('только чтение'))));
  const treeBody = h('div', {});
  treeBox.append(treeBody);
  async function openDir(p) {
    const r = await api('browse?path=' + encodeURIComponent(p));
    treeBody.innerHTML = '';
    if (r.error) { treeBody.append(h('div', { class: 'empty' }, r.error)); return; }
    treeBody.append(h('div', { class: 'tree-item', style: 'background:var(--bg-panel-2)' },
      h('span', { class: 'nm' }, r.path),
      r.parent ? h('a', { href: '#', class: 'sz', onclick: e => { e.preventDefault(); openDir(r.parent); } }, t('↑ вверх')) : null));
    r.entries.forEach(e => treeBody.append(h('div', { class: 'tree-item' + (e.dir ? ' dir' : '') },
      h('span', { style: 'width:14px;flex:none;color:var(--text-faint)' }, e.dir ? '▸' : '·'),
      h('span', {
        class: 'nm', onclick: e.dir ? () => openDir(r.path.replace(/\/$/, '') + '/' + e.name) : null
      }, e.name, e.sensitive ? h('span', { class: 'pill bad', style: 'margin-left:8px' }, t('секрет — не читается')) : null),
      h('span', { class: 'sz muted', style: 'width:130px;text-align:right' }, dt(e.mtime)),
      h('span', { class: 'sz', style: 'width:82px;text-align:right' }, e.dir ? '' : bytes(e.size)))));
    if (r.truncated) treeBody.append(h('div', { class: 'empty' }, t('показаны первые 400 из ') + r.count));
  }
  openDir((d.allowed_roots || ['/'])[0]);

  shell(h('div', {},
    head(t('Диск'), root.mount + ' · ' + bytes(root.used) + t(' из ') + bytes(root.total)),
    cards, fsTable, dirCard,
    h('div', { style: 'margin-top:12px' }, charts),
    h('div', { class: 'note', style: 'margin-top:12px' },
      t('Просмотр ограничен каталогами: '), h('b', {}, (d.allowed_roots || []).join(', ')),
      t('. Содержимое файлов не читается, изменение и удаление невозможны.')),
    treeBox), { title: t('Диск') });
}

// ---------- страница: службы ----------
async function pageServices() {
  const d = await api('services');
  const svcs = d.services || [];
  const down = svcs.filter(s => s.active !== 'active');
  const detail = h('div', {});
  async function showLog(name) {
    detail.innerHTML = '';
    detail.append(h('div', { class: 'card', style: 'margin-top:12px' }, h('h2', {}, t('Журнал: ') + name),
      h('div', { class: 'empty' }, t('загрузка…'))));
    const r = await api('services/' + encodeURIComponent(name) + '/log');
    detail.innerHTML = '';
    detail.append(h('div', { class: 'card', style: 'margin-top:12px' },
      h('h2', {}, t('Журнал: ') + name, h('span', { class: 'r' }, t('последние 40 строк'))),
      h('pre', { class: 'log' }, (r.lines || []).join('\n') || t('пусто'))));
  }

  shell(h('div', {},
    head(t('Службы'), svcs.length + t(' наблюдаемых · ') + (down.length ? down.length + t(' не работает') : t('все работают'))),
    down.length ? h('div', { class: 'note', style: 'border-color:rgba(242,85,90,.35);background:rgba(242,85,90,.07)' },
      h('b', {}, t('🔴 Не работают: ')), down.map(s => s.name + ' (' + s.active + ')').join(', ')) : null,
    h('div', { class: 'card pad0' }, sortableTable([
      { title: t('Служба'), val: r => r.name, render: r => h('a', { href: '#', class: 'mono', onclick: e => { e.preventDefault(); showLog(r.name); } }, r.name) },
      { title: t('Описание'), val: r => r.description, render: r => h('span', { class: 'trunc muted', title: r.description }, r.description || '—') },
      { title: t('Состояние'), val: r => r.active, render: r => h('span', { class: 'st ' + (r.active === 'active' ? 'ok' : r.active === 'failed' ? 'bad' : 'off') }, r.active + (r.sub ? ' / ' + r.sub : '')) },
      { title: t('Автозапуск'), val: r => r.enabled, render: r => r.enabled || '—' },
      { title: 'PID', num: true, val: r => r.pid, render: r => r.pid || '—' },
      { title: t('Память'), num: true, val: r => r.rss, render: r => bytes(r.rss) },
      { title: t('Порты'), val: r => (r.ports || []).join(','), render: r => (r.ports || []).length ? (r.ports || []).join(', ') : '—', cls: 'mono' },
      { title: t('Аптайм'), num: true, val: r => r.uptime, render: r => dur(r.uptime) },
      { title: t('Перезапусков'), num: true, val: r => r.restarts, render: r => r.restarts ? h('span', { style: 'color:var(--warn)' }, r.restarts) : '0' },
    ], svcs, 'svc')),
    detail,
    h('div', { class: 'note', style: 'margin-top:12px' },
      t('Панель только читает состояние. Управлять службами через неё нельзя — '),
      t('ни запуск, ни остановка, ни перезапуск из веба не выполняются.'))
  ), { title: t('Службы') });
}

// ---------- страница: процессы ----------
async function pageProcesses() {
  const d = await api('processes');
  const procs = d.processes || [];
  const groups = { nginx: [], php: [], mysql: [], python: [], node: [], other: [] };
  procs.forEach(p => {
    const n = (p.name || '').toLowerCase();
    if (n.includes('nginx')) groups.nginx.push(p);
    else if (n.includes('php')) groups.php.push(p);
    else if (n.includes('mysql')) groups.mysql.push(p);
    else if (n.includes('python')) groups.python.push(p);
    else if (n.includes('node') || n.includes('deno')) groups.node.push(p);
    else groups.other.push(p);
  });
  const sum = arr => arr.reduce((a, b) => a + (b.rss || 0), 0);
  const sumCpu = arr => arr.reduce((a, b) => a + (b.cpu || 0), 0);

  const cards = h('div', { class: 'grid g-metrics' },
    ...Object.entries(groups).filter(([, v]) => v.length).map(([k, v]) =>
      metric({ nginx: 'nginx', php: t('PHP (сайты)'), mysql: 'MySQL', python: 'Python', node: 'Node/Deno', other: t('Прочие') }[k],
        bytes(sum(v)), { hint: v.length + t(' проц. · ЦП ') + sumCpu(v).toFixed(1) + '%' })));

  shell(h('div', {},
    head(t('Процессы'), procs.length + t(' самых заметных · ') + d.cores + t(' ядра')),
    cards,
    h('div', { class: 'card pad0', style: 'margin-top:12px' }, sortableTable([
      { title: t('Процесс'), val: r => r.name, cls: 'mono' },
      { title: 'PID', num: true, val: r => r.pid },
      { title: t('Пользователь'), val: r => r.user },
      { title: t('ЦП'), num: true, val: r => r.cpu, render: r => r.cpu === null ? '—' : h('span', { style: r.cpu > 50 ? 'color:var(--warn)' : '' }, r.cpu + '%') },
      { title: t('Память'), num: true, val: r => r.rss, render: r => bytes(r.rss) },
      { title: t('Доля ОЗУ'), num: true, val: r => r.rss, render: r => pct(100 * (r.rss || 0) / ((state.data.overview?.system?.memory?.total) || 1)) },
      { title: t('Аптайм'), num: true, val: r => r.started, sortVal: r => -r.started, render: r => dur(Math.floor(Date.now() / 1000) - r.started) },
      { title: t('Команда'), val: r => r.cmd, render: r => h('span', { class: 'trunc mono', title: r.cmd }, r.cmd) },
    ], procs, 'procs')),
    h('div', { class: 'note', style: 'margin-top:12px' },
      t('Пароли и ключи в командных строках скрыты: значения после password/token/secret заменяются на ***.'))
  ), { title: t('Процессы') });
}

// ---------- страница: порты ----------
async function pageNetwork() {
  const d = await api('network');
  const all = d.ports || [];
  // Временные исходящие UDP-сокеты прокси в таблицу не попадают: их бывают
  // десятки, и службами они не являются. Их число показано отдельной строкой.
  const ports = all.filter(p => !p.ephemeral);
  const ephem = all.length - ports.length;
  const pub = ports.filter(p => p.scope === 'public');
  const loc = ports.filter(p => p.scope !== 'public');
  const net = d.network || {};
  const hist = d.history || [];

  const cards = h('div', { class: 'grid g-metrics' },
    metric(t('Открытых портов'), ports.length, { hint: pub.length + t(' наружу · ') + loc.length + t(' локально') }),
    ephem ? metric(t('Временных UDP'), ephem, { hint: t('исходящие сокеты прокси, не службы') }) : null,
    metric(t('Входящий'), bytes(net.rx_rate) + t('<span class="u">/с</span>'), { hint: t('всего ') + bytes(net.rx_total) }),
    metric(t('Исходящий'), bytes(net.tx_rate) + t('<span class="u">/с</span>'), { hint: t('всего ') + bytes(net.tx_total) }),
    metric(t('Интерфейсов'), (net.interfaces || []).length, { hint: (net.interfaces || []).map(i => i.iface).join(', ') })
  );

  const table = (rows, title) => h('div', { class: 'card pad0', style: 'margin-top:12px' },
    h('h2', { style: 'padding:14px 15px 10px;margin:0' }, title,
      h('span', { class: 'r' }, rows.length + t(' шт.'))),
    sortableTable([
      { title: t('Порт'), num: true, val: r => r.port, render: r => h('b', {}, r.port) },
      { title: t('Протокол'), val: r => r.proto },
      { title: t('Адрес'), val: r => (r.addresses || []).join(', '), cls: 'mono' },
      { title: t('Процесс'), val: r => r.process, cls: 'mono' },
      { title: 'PID', num: true, val: r => r.pid, render: r => r.pid || '—' },
      { title: t('Назначение'), val: r => portLabel(r), render: r => portLabel(r) || h('span', { class: 'muted' }, '—') },
      {
        title: t('Доступ'), val: r => r.scope, render: r => h('span', { class: 'pill ' + (r.scope === 'public' ? 'warn' : 'ok') },
          r.scope === 'public' ? t('наружу') : t('только localhost'))
      },
      { title: t('Команда'), val: r => r.cmd, render: r => h('span', { class: 'trunc mono', title: r.cmd }, r.cmd || '—') },
    ], rows, 'ports-' + title));

  shell(h('div', {},
    head(t('Порты и сеть'), ports.length + t(' слушающих сокетов') + (ephem ? ' · ' + ephem + t(' временных скрыто') : '')),
    cards,
    chartBox(t('Сетевая нагрузка за час'), [
      { name: t('входящий'), points: hist.map(r => [r.ts, r.net_rx]) },
      { name: t('исходящий'), points: hist.map(r => [r.ts, r.net_tx]), color: PALETTE[1] },
    ], { fmt: v => bytes(v, 0) }),
    table(pub, t('Доступны снаружи')), table(loc, t('Только localhost'))
  ), { title: t('Порты') });
}

// ---------- страница: сертификаты ----------
async function pageSSL() {
  const d = await api('ssl');
  state.data.domainList = (await api('domains')).domains || [];
  const certs = d.certificates || [];
  const worst = certs.reduce((a, c) => Math.min(a, c.days_left ?? 9999), 9999);
  shell(h('div', {},
    head(t('Сертификаты'), certs.length + t(' доменов')),
    h('div', { class: 'grid g-metrics' },
      metric(t('Ближайшее истечение'), worst === 9999 ? '—' : worst + t('<span class="u"> дн</span>'),
        { level: worst < 8 ? 'bad' : worst < 30 ? 'warn' : 'ok' }),
      metric(t('В порядке'), certs.filter(c => c.status === 'ok').length, { hint: t('больше 30 дней') }),
      metric(t('Скоро истекут'), certs.filter(c => c.status === 'warning').length, { hint: t('8–30 дней') }),
      metric(t('Критично'), certs.filter(c => ['critical', 'expired'].includes(c.status)).length, { hint: t('меньше 8 дней') })),
    h('div', { class: 'card pad0', style: 'margin-top:12px' }, sortableTable([
      { title: t('Домен'), val: r => r.domain, render: r => r.id ? h('a', { href: '/domains/' + r.id, 'data-nav': '1' }, r.domain) : h('span', { title: r.note || '' }, r.domain, r.note ? h('div', { class: 'muted', style: 'font-size:11.5px' }, r.note) : null) },
      { title: t('Издатель'), val: r => r.issuer, render: r => r.issuer || '—' },
      { title: t('Выдан на'), val: r => r.subject, render: r => r.subject || '—' },
      { title: t('Действует с'), val: r => r.not_before, render: r => dt(r.not_before) },
      { title: t('Истекает'), val: r => r.not_after, render: r => dt(r.not_after) },
      { title: t('Осталось'), num: true, val: r => r.days_left, render: r => r.days_left === null || r.days_left === undefined ? '—' : r.days_left + t(' дн') },
      {
        title: t('Статус'), val: r => r.status, render: r => h('span', {
          class: 'pill ' + (r.status === 'ok' ? 'ok' : r.status === 'warning' ? 'warn' : 'bad')
        }, { ok: t('в порядке'), warning: t('скоро истечёт'), critical: t('критично'), expired: t('просрочен'), unknown: t('нет данных') }[r.status] || r.status)
      },
      { title: t('Протокол'), val: r => r.tls_version, render: r => r.tls_version || '—' },
      { title: t('Источник'), val: r => r.source, render: r => r.source === 'live' ? t('порт 443') : t('файл') },
      { title: t('Файл'), val: r => r.cert_path, render: r => h('span', { class: 'trunc mono', title: r.cert_path }, r.cert_path || '—') },
    ], certs, 'ssl')),
    h('div', { class: 'note', style: 'margin-top:12px' },
      t('Проверяется то, что сервер реально отдаёт на 443, а не только файл на диске: '),
      t('обновлённый, но не перечитанный nginx сертификат так будет виден сразу.')),
    h('div', { class: 'card pad0', style: 'margin-top:12px' },
      h('h2', { style: 'padding:14px 15px 10px;margin:0' }, t('Сроки регистрации доменов'),
        h('span', { class: 'r' }, t('по данным реестра, раз в 12 часов'))),
      sortableTable([
        { title: t('Домен'), val: r => r.domain, render: r => h('a', { href: '/domains/' + r.id, 'data-nav': '1' }, r.domain) },
        { title: t('Оплачен до'), val: r => (r.registration || {}).paid_till, render: r => dt((r.registration || {}).paid_till) },
        { title: t('Осталось'), num: true, val: r => (r.registration || {}).days_left, render: r => regPill((r.registration || {}).days_left, (r.registration || {}).status, (r.registration || {}).paid_till) },
        { title: t('Освободится'), val: r => (r.registration || {}).free_date, render: r => dt((r.registration || {}).free_date) },
        { title: t('Зарегистрирован'), val: r => (r.registration || {}).created, render: r => dt((r.registration || {}).created) },
        { title: t('Регистратор'), val: r => (r.registration || {}).registrar, render: r => (r.registration || {}).registrar || '—' },
        { title: t('Состояние'), val: r => (r.registration || {}).state, render: r => h('span', { class: 'trunc', title: (r.registration || {}).state || '' }, (r.registration || {}).state || '—') },
        { title: t('Проверено'), val: r => (r.registration || {}).checked, render: r => ago((r.registration || {}).checked) },
      ], state.data.domainList || [], 'reg'))
  ), { title: t('Сертификаты') });
}

// ---------- страница: события ----------
async function pageAlerts() {
  const d = await api('alerts');
  const active = d.active || [], events = d.events || [];
  const icon = s => s === 'critical' ? '🔴' : s === 'warning' ? '🟡' : '🔵';
  shell(h('div', {},
    head(t('События'), active.length ? active.length + t(' активных') : t('всё спокойно')),
    h('div', { class: 'card pad0' },
      h('h2', { style: 'padding:14px 15px 10px;margin:0' }, t('Сейчас')),
      active.length ? h('div', {}, active.map(a => h('div', { class: 'alert-row ' + a.severity },
        h('span', { class: 'ic' }, icon(a.severity)),
        h('span', { class: 'msg' }, alertText(a), h('div', { class: 'muted', style: 'font-size:11.5px' }, a.kind + ' · ' + a.subject)),
        h('span', { class: 't' }, ago(a.ts)))))
        : h('div', { class: 'empty' }, t('✓ Активных проблем нет'))),
    h('div', { class: 'card pad0', style: 'margin-top:12px' },
      h('h2', { style: 'padding:14px 15px 10px;margin:0' }, t('Лента событий')),
      events.length ? h('div', {}, events.map(e => h('div', { class: 'alert-row ' + (e.resolved_ts ? 'info' : e.severity) },
        h('span', { class: 'ic' }, e.resolved_ts ? '✓' : icon(e.severity)),
        h('span', { class: 'msg' }, alertText(e),
          h('div', { class: 'muted', style: 'font-size:11.5px' },
            e.subject + ' · ' + (e.resolved_ts ? t('закрыто ') + dt(e.resolved_ts) : t('открыто')))),
        h('span', { class: 't' }, dt(e.ts)))))
        : h('div', { class: 'empty' }, t('Событий пока не записано'))),
    h('div', { class: 'card pad0', style: 'margin-top:12px' },
      h('h2', { style: 'padding:14px 15px 10px;margin:0' }, t('Последние ошибки в журналах')),
      (d.recent_errors || []).length
        ? h('pre', { class: 'log', style: 'border:none;border-radius:0' },
          d.recent_errors.map(e => '[' + dt(e.ts) + '] ' + (e.domain || '') + ' ' +
            (e.status || '') + ' ' + e.line).join('\n'))
        : h('div', { class: 'empty' }, t('Ошибок не найдено'))),
    h('div', { class: 'note', style: 'margin-top:12px' },
      t('Каналы доставки (Telegram, почта, Discord) пока не подключены. '),
      t('Место для них подготовлено в lib/alerts.py — функция dispatch принимает готовый список срабатываний.'))
  ), { title: t('События') });
}

// ---------- страница: настройки ----------
async function pageSettings() {
  const d = await api('settings');
  state.thresholds = d.thresholds;
  const msg = h('span', { class: 'saved' });

  const LABELS = {
    disk_warning: t('Диск, внимание (%)'), disk_critical: t('Диск, критично (%)'),
    ram_warning: t('Память, внимание (%)'), ram_critical: t('Память, критично (%)'),
    cpu_warning: t('Процессор, внимание (%)'), cpu_critical: t('Процессор, критично (%)'),
    load_warning: t('Очередь задач, внимание'), load_critical: t('Очередь задач, критично'),
    ssl_warning_days: t('SSL, предупредить за дней'), ssl_critical_days: t('SSL, критично за дней'),
    response_warning_ms: t('Ответ, внимание (мс)'), response_critical_ms: t('Ответ, критично (мс)'),
    http5xx_warning_per_hour: t('5xx за час, внимание'), http5xx_critical_per_hour: t('5xx за час, критично'),
    db_growth_warning_pct: t('Рост базы за неделю (%)'),
    domain_warning_days: t('Домен, предупредить за дней'), domain_critical_days: t('Домен, критично за дней'),
    mysql_conn_warning: t('Соединений MySQL, внимание'), mysql_conn_critical: t('Соединений MySQL, критично'),
  };
  const IV_LABELS = {
    system: t('Системные метрики'), domain_health: t('Проверка сайтов'), nginx_logs: t('Разбор журналов'),
    services: t('Службы и порты'), databases: t('Базы данных'), ssl: t('Сертификаты'),
    disk_scan: t('Обход каталогов (du)'), processes: t('Процессы'),
    whois: t('Сроки регистрации доменов'),
  };

  const thInputs = {};
  const thGrid = h('div', { class: 'grid g-3' }, Object.entries(d.thresholds).map(([k, v]) => {
    const inp = h('input', { class: 'f', type: 'number', step: 'any', value: v });
    thInputs[k] = inp;
    return h('label', { style: 'display:block' },
      h('div', { class: 'muted', style: 'font-size:12px;margin-bottom:4px' }, LABELS[k] || k), inp);
  }));

  const ivInputs = {};
  const ivGrid = h('div', { class: 'grid g-3' }, Object.entries(d.intervals).map(([k, v]) => {
    const inp = h('input', { class: 'f', type: 'number', min: 10, value: v });
    ivInputs[k] = inp;
    return h('label', { style: 'display:block' },
      h('div', { class: 'muted', style: 'font-size:12px;margin-bottom:4px' }, (IV_LABELS[k] || k) + t(', с')), inp);
  }));

  async function save(body) {
    const r = await api('settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    msg.textContent = r.error
      ? '⚠ ' + (SETTINGS_ERRORS[I18N.lang][r.code] || r.error)
      : t('✓ сохранено: ') + (r.changed || []).join(', ');
    if (!r.error) setTimeout(render, 900);
  }

  // форма домена
  const f = {};
  const field = (key, label, ph, type) => {
    f[key] = h('input', { class: 'f', type: type || 'text', placeholder: ph || '' });
    return h('label', { style: 'display:block' },
      h('div', { class: 'muted', style: 'font-size:12px;margin-bottom:4px' }, label), f[key]);
  };
  const domForm = h('div', { class: 'grid g-3' },
    field('id', t('Идентификатор'), 'site3'),
    field('domain', t('Домен'), 'site3.ru'),
    field('title', t('Название'), t('Третий сайт')),
    field('project_dir', t('Каталог проекта'), '/root/site3'),
    field('access_log', t('Журнал доступа'), '/var/log/nginx/site3.access.log'),
    field('error_log', t('Журнал ошибок'), '/var/log/nginx/site3.error.log'),
    field('ssl_cert', t('Файл сертификата'), '/root/cert/site3/fullchain.pem'),
    field('service', t('systemd-служба'), 'site3.service'),
    field('backend_port', t('Порт бэкенда'), '8190', 'number'),
    field('health_path', t('Путь проверки'), '/'),
    field('mysql_db', t('База MySQL (необязательно)'), 'site3'),
    field('sqlite_path', t('Файл SQLite (необязательно)'), '/root/site3/var/app.sqlite'));

  function fillForm(dom) {
    ['id', 'domain', 'title', 'project_dir', 'access_log', 'error_log', 'ssl_cert',
      'service', 'backend_port', 'health_path'].forEach(k => { f[k].value = dom[k] ?? ''; });
    const my = (dom.databases || []).find(x => x.engine === 'mysql');
    const sq = (dom.databases || []).find(x => x.engine === 'sqlite');
    f.mysql_db.value = my ? my.name : '';
    f.sqlite_path.value = sq ? sq.path : '';
  }

  function collectForm(enabled) {
    const databases = [];
    if (f.mysql_db.value.trim()) databases.push({ engine: 'mysql', name: f.mysql_db.value.trim() });
    if (f.sqlite_path.value.trim()) databases.push({
      engine: 'sqlite', path: f.sqlite_path.value.trim(),
      name: f.sqlite_path.value.trim().split('/').pop().replace(/\.(sqlite|db)$/, '')
    });
    const out = { databases, enabled };
    ['id', 'domain', 'title', 'project_dir', 'access_log', 'error_log', 'ssl_cert',
      'service', 'backend_port', 'health_path'].forEach(k => { out[k] = f[k].value.trim() || null; });
    return out;
  }

  const domList = h('div', { class: 'card pad0' },
    h('h2', { style: 'padding:14px 15px 10px;margin:0' }, t('Домены под наблюдением')),
    sortableTable([
      { title: 'ID', val: r => r.id, cls: 'mono' },
      { title: t('Домен'), val: r => r.domain },
      { title: t('Наблюдение'), val: r => r.enabled, render: r => h('span', { class: 'pill ' + (r.enabled ? 'ok' : '') }, r.enabled ? t('включено') : t('выключено')) },
      { title: t('Служба'), val: r => r.service, render: r => r.service || '—', cls: 'mono' },
      { title: t('Порт'), num: true, val: r => r.backend_port, render: r => r.backend_port || '—' },
      { title: t('Каталог'), val: r => r.project_dir, cls: 'mono', render: r => h('span', { class: 'trunc' }, r.project_dir || '—') },
      { title: t('Баз'), num: true, val: r => (r.databases || []).length },
      {
        title: '', sortable: false, val: () => '', render: r => h('span', {},
          h('button', { class: 'btn', style: 'padding:3px 9px', onclick: () => { fillForm(r); window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' }); } }, t('править')),
          ' ',
          h('button', {
            class: 'btn', style: 'padding:3px 9px',
            onclick: () => save({ domain: Object.assign({}, r, { enabled: !r.enabled }) })
          }, r.enabled ? t('выключить') : t('включить')),
          ' ',
          h('button', {
            class: 'btn danger', style: 'padding:3px 9px',
            onclick: () => confirm(t('Убрать ') + r.domain + t(' из наблюдения? Сам сайт не изменится.'))
              && save({ delete_domain: r.id })
          }, t('убрать')))
      },
    ], d.domains, 'setdom'));

  shell(h('div', {},
    head(t('Настройки'), t('конфигурация: ') + d.config_path, msg),
    domList,
    h('div', { class: 'card', style: 'margin-top:12px' },
      h('h2', {}, t('Добавить или изменить домен')),
      h('div', { class: 'note' },
        t('Новый сайт начинает отслеживаться сразу после сохранения — править код не нужно. '),
        t('Коллектор перечитывает конфигурацию на ходу.')),
      domForm,
      h('div', { style: 'margin-top:14px;display:flex;gap:10px' },
        h('button', { class: 'btn primary', onclick: () => save({ domain: collectForm(true) }) }, t('Сохранить домен')),
        h('button', { class: 'btn', onclick: () => { Object.values(f).forEach(i => i.value = ''); } }, t('Очистить')))),
    h('div', { class: 'card', style: 'margin-top:12px' },
      h('h2', {}, t('Пороговые значения')), thGrid,
      h('div', { style: 'margin-top:14px' },
        h('button', {
          class: 'btn primary', onclick: () => save({
            thresholds: Object.fromEntries(Object.entries(thInputs).map(([k, i]) => [k, i.value]))
          })
        }, t('Сохранить пороги')))),
    h('div', { class: 'card', style: 'margin-top:12px' },
      h('h2', {}, t('Интервалы сбора')), ivGrid,
      h('div', { style: 'margin-top:14px' },
        h('button', {
          class: 'btn primary', onclick: () => save({
            intervals: Object.fromEntries(Object.entries(ivInputs).map(([k, i]) => [k, i.value]))
          })
        }, t('Сохранить интервалы')))),
    h('div', { class: 'card', style: 'margin-top:12px' }, h('h2', {}, t('Где что лежит')),
      h('div', { class: 'kv' },
        h('dt', {}, t('Конфигурация')), h('dd', {}, d.config_path),
        h('dt', {}, t('База метрик')), h('dd', {}, d.db_path),
        h('dt', {}, t('Хранение высокого разрешения')), h('dd', {}, (d.retention?.highres_days ?? '?') + t(' дней')),
        h('dt', {}, t('Хранение часовых точек')), h('dd', {}, (d.retention?.hourly_days ?? '?') + t(' дней')),
        h('dt', {}, t('Наблюдаемые каталоги')), h('dd', {}, (d.storage_watch || []).join(', ')),
        h('dt', {}, t('Прочие службы')), h('dd', {}, (d.extra_services || []).join(', '))))
  ), { title: t('Настройки') });
}

// ---------- вход ----------
function renderLogin(err) {
  clearInterval(state.timer);
  state.timer = null;
  const login = h('input', { type: 'text', autocomplete: 'username', autofocus: true });
  const pass = h('input', { type: 'password', autocomplete: 'current-password' });
  const errBox = h('div', { class: 'err' }, err || '');
  async function submit(e) {
    if (e) e.preventDefault();
    errBox.textContent = '';
    const r = await fetch('/api/login', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ login: login.value, password: pass.value })
    });
    const j = await r.json();
    if (j.ok) { state.booted = false; render(); }
    else errBox.textContent = j.error || t('Не удалось войти');
  }
  const form = h('form', { class: 'login', onsubmit: submit },
    h('h1', {}, t('Мониторинг сервера')),
    h('p', {}, t('Доступ только для владельца')),
    errBox,
    h('label', {}, t('Логин')), login,
    h('label', {}, t('Пароль')), pass,
    h('button', { type: 'submit' }, t('Войти')),
    h('div', { class: 'range-sw', style: 'margin-top:14px;width:100%' },
      ['ru', 'en'].map(lang => h('button', {
        type: 'button', style: 'flex:1',
        class: I18N.lang === lang ? 'on' : '',
        onclick: () => { I18N.set(lang); document.documentElement.lang = lang; renderLogin(); },
      }, lang.toUpperCase()))));
  $('#root').innerHTML = '';
  $('#root').append(h('div', { class: 'login-wrap' }, form));
  login.focus();
}

async function logout() {
  await fetch('/api/logout', { method: 'POST', credentials: 'same-origin' });
  renderLogin();
}

// ---------- роутер ----------
async function render() {
  const path = location.pathname;
  try {
    if (!state.thresholds) {
      const s = await api('settings').catch(() => null);
      if (s) state.thresholds = s.thresholds;
    }
    if (path.startsWith('/domains/')) await pageDomain(decodeURIComponent(path.slice(9)));
    else if (path.startsWith('/databases/')) await pageDatabase(path.slice(11));
    else if (path === '/domains') await pageDomains();
    else if (path === '/databases') await pageDatabases();
    else if (path === '/storage') await pageStorage();
    else if (path === '/services') await pageServices();
    else if (path === '/processes') await pageProcesses();
    else if (path === '/network') await pageNetwork();
    else if (path === '/ssl') await pageSSL();
    else if (path === '/alerts') await pageAlerts();
    else if (path === '/settings') await pageSettings();
    else await pageOverview();
    state.lastUpdate = Date.now();
  } catch (e) {
    if (String(e.message) === 'unauth') return;
    const root = $('#root');
    if (!root.firstChild) root.append(h('div', { class: 'empty' }, t('Ошибка: ') + e.message));
    else console.error(e);
  }
}

async function boot() {
  document.documentElement.lang = I18N.lang;
  const s = await fetch('/api/session', { credentials: 'same-origin' }).then(r => r.json());
  if (!s.authenticated) { renderLogin(); return; }
  await render();
  // текущие значения обновляем раз в 20 секунд: чаще незачем,
  // коллектор всё равно пишет реже
  clearInterval(state.timer);
  state.timer = setInterval(() => {
    if (!document.hidden) render();
  }, 20000);
}

boot();
