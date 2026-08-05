/* ARESCO Treasury & Finance — front end.
   Implements the ARESCO Treasury UI design handoff against the live API.
   Vanilla JS, no build step, matching the BOQ engine's setup. */

const API = location.origin;
const page = document.getElementById('page');

/* ---------------------------------------------------------------- helpers */

const get = (p) => fetch(API + p).then(r => r.ok ? r.json() : r.json().then(e => Promise.reject(e)));
const send = (p, method, body) =>
  fetch(API + p, {
    method, headers: {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then(r => r.ok ? r.json() : r.json().then(e => Promise.reject(e)));

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

/* Rule 01: no decimals above a thousand. A CFO reads magnitudes, not piastres. */
function f0(v) {
  if (v === null || v === undefined || isNaN(v)) return '—';
  return Math.round(v).toLocaleString('en-US');
}
function fN(v, dp) {
  if (v === null || v === undefined || isNaN(v)) return '—';
  const d = dp !== undefined ? dp : (Math.abs(v) >= 1000 ? 0 : 2);
  return v.toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d});
}
const fM  = (v) => v === null || v === undefined ? '—' : (v / 1e6).toFixed(1) + 'm';
const fMs = (v) => v === null || v === undefined ? '—' : (v < 0 ? '−' : '') + Math.abs(v / 1e6).toFixed(1);
/* Accounting convention: negatives in parentheses, not with a minus sign. */
const fK  = (v) => v === null || v === undefined ? '—' : (v < 0 ? '(' + f0(Math.abs(v)) + ')' : f0(v));
const pct = (v, dp) => v === null || v === undefined ? '—' : (v * 100).toFixed(dp === undefined ? 1 : dp) + '%';

function day(d) {
  if (!d) return '—';
  return new Date(d + 'T00:00:00').toLocaleDateString('en-GB', {day: '2-digit', month: 'short', year: '2-digit'});
}
function dayLong(d) {
  if (!d) return '—';
  return new Date(d + 'T00:00:00').toLocaleDateString('en-GB', {day: '2-digit', month: 'short', year: 'numeric'});
}
const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

/* Arabic and English counterparty names share a column; dir+isolate keeps each
   correct without dragging the column's alignment with it. */
const isArabic = (s) => /[؀-ۿ]/.test(s || '');
function nameCell(primary, secondary) {
  const p = isArabic(primary)
    ? `<div class="ar">${esc(primary)}</div>`
    : `<div style="font-weight:500;font-size:13.5px;color:var(--ink)">${esc(primary)}</div>`;
  return p + (secondary ? `<div class="en">${esc(secondary)}</div>` : '');
}

function card(title, stamp, body, extra) {
  return `<section class="card">
    <div class="card-head">
      <div><h2>${esc(title)}</h2>${stamp ? `<span class="stamp">${esc(stamp)}</span>` : ''}</div>
      ${extra || ''}
    </div>${body}</section>`;
}
function table(head, rows, empty) {
  if (!rows.length) return `<div class="empty">${empty || 'Nothing to show.'}</div>`;
  return `<div class="scroll"><table><thead><tr>${head}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
}
function seg(items, active, fn) {
  return `<div class="seg">${items.map(i =>
    `<button class="${i.id === active ? 'on' : ''}" onclick="${fn}('${i.id}')">${esc(i.label)}</button>`
  ).join('')}</div>`;
}
function notice(kind, title, body) {
  const TRIANGLE = '<path d="M12 9v5M12 17.5v.5"></path><path d="M10.3 3.9 2.6 17.2A1.9 1.9 0 0 0 4.3 20h15.4a1.9 1.9 0 0 0 1.7-2.8L13.7 3.9a1.9 1.9 0 0 0-3.4 0Z"></path>';
  const icons = {
    warn: TRIANGLE,
    bad: TRIANGLE,
    info: '<circle cx="12" cy="12" r="9"></circle><path d="M12 8h.01M11 12h1v4h1"></path>',
  };
  const stroke = kind === 'bad' ? 'var(--bad)' : kind === 'info' ? 'var(--steel-deep)' : 'var(--warn)';
  return `<div class="notice ${kind}">
    ${title ? `<div class="t"><svg viewBox="0 0 24 24" fill="none" stroke="${stroke}" stroke-width="1.7" stroke-linecap="round">${icons[kind] || icons.warn}</svg><span>${esc(title)}</span></div>` : ''}
    <div>${body}</div></div>`;
}
function noticeList(items, kind, title) {
  if (!items || !items.length) return '';
  return notice(kind || 'warn', title, `<ul>${items.map(i => `<li>${esc(i)}</li>`).join('')}</ul>`);
}

/* Rule 03: `splitAt` is where actual ends and projection begins. Everything to
   its right is dashed — a projection must never read as a fact. */
function lineChart(points, opts) {
  opts = opts || {};
  if (!points.length) return '<div class="empty">No data.</div>';
  const W = 900, H = opts.h || 200, padT = 10, padB = 10;
  const ys = points.map(p => p.y);
  let mn = Math.min(0, ...ys), mx = Math.max(...ys);
  if (mn === mx) mx = mn + 1;
  const X = i => (i / Math.max(points.length - 1, 1)) * W;
  const Y = v => H - ((v - mn) / (mx - mn)) * (H - padT - padB) - padB;

  const seg = (a, b) => points.slice(a, b).map((p, i) =>
    `${i ? 'L' : 'M'}${X(a + i).toFixed(1)},${Y(p.y).toFixed(1)}`).join('');
  const split = opts.splitAt === undefined ? points.length : Math.max(1, Math.min(opts.splitAt, points.length));
  const area = `M${X(0)},${Y(mn)} ` + points.map((p, i) => `L${X(i).toFixed(1)},${Y(p.y).toFixed(1)}`).join('') + ` L${X(points.length - 1)},${Y(mn)}Z`;

  const dots = points.map((p, i) => p.flag
    ? `<circle cx="${X(i).toFixed(1)}" cy="${Y(p.y).toFixed(1)}" r="3.4" fill="var(--bad)"><title>${esc(p.label)}: ${f0(p.y)}</title></circle>` : '').join('');

  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:${opts.px || 230}px">
    <path d="${area}" fill="var(--surface-3)" opacity="0.7"></path>
    ${mn < 0 ? `<line x1="0" y1="${Y(0).toFixed(1)}" x2="${W}" y2="${Y(0).toFixed(1)}" stroke="var(--bad)" stroke-width="1.2" stroke-dasharray="2 4"></line>` : ''}
    ${split < points.length ? `<line x1="${X(split - 1).toFixed(1)}" y1="0" x2="${X(split - 1).toFixed(1)}" y2="${H}" stroke="var(--ink-3)" stroke-width="1" stroke-dasharray="3 3"></line>` : ''}
    <path d="${seg(0, split)}" fill="none" stroke="var(--steel-deep)" stroke-width="2.4" stroke-linejoin="round"></path>
    ${split < points.length ? `<path d="${seg(split - 1, points.length)}" fill="none" stroke="var(--steel)" stroke-width="2.2" stroke-dasharray="6 4" stroke-linejoin="round"></path>` : ''}
    ${dots}
  </svg>`;
}

/* ------------------------------------------------------------------- nav */

const NAV = [
  {id:'cash',    label:'Cash Dashboard',      eyebrow:'Treasury',       title:'Cash Dashboard', group:'Cash & Treasury', icon:'M12 14 16 9M4.5 18a9 9 0 1 1 15 0'},
  {id:'cheques', label:'Cheques',             eyebrow:'Treasury',       title:'Cheques — register, aging & PDC calendar', group:'Cash & Treasury', icon:'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8ZM14 2v6h6M9 13h6M9 17h4'},
  {id:'ar',      label:'Receivables',         eyebrow:'Working capital',title:'Receivables — aging & collection forecast', group:'Cash & Treasury', icon:'M3 4h18v16H3zM3 9h18M3 14h18M9 4v16M15 4v16'},
  {id:'cf',      label:'Cash Flow Forecast',  eyebrow:'Treasury',       title:'Rolling cash flow forecast', group:'Cash & Treasury', icon:'m12 2 9 5-9 5-9-5 9-5ZM3 12l9 5 9-5M3 17l9 5 9-5'},
  {id:'pnl',     label:'P&L / Reporting',     eyebrow:'Controlling',    title:'P&L, variance & statement matrix', group:'Reporting', icon:'M3 4h18v16H3zM3 9h18M9 4v16M13 12l4 4M17 12l-4 4'},
  {id:'bp',      label:'Business Plan',       eyebrow:'Controlling',    title:'Business plan 2026–2030 — actual vs plan', group:'Reporting', icon:'M3 17 9 11l4 4 8-8M21 7v6h-6'},
  {id:'ecl',     label:'ECL — IFRS 9',        eyebrow:'Controlling',    title:'Expected credit loss — provision matrix', group:'Reporting', icon:'M12 2 4 5v6c0 5 3.4 8.5 8 11 4.6-2.5 8-6 8-11V5Zm-3 10 2 2 4-4'},
  {id:'audit',   label:'Audit Assistant',     eyebrow:'Controlling',    title:'External audit assistant', group:'Reporting', icon:'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14ZM20 20l-3.2-3.2'},
  {id:'funding', label:'Funding Position',    eyebrow:'Treasury',       title:'Funding position — cash, receivables, advances and commitments', group:'Commitments', icon:'M3 21h18M5 21V8l7-5 7 5v13M9 21v-6h6v6'},
  {id:'tf',      label:'L/G & L/C',           eyebrow:'Trade finance',  title:'Letters of guarantee & credit — clients and suppliers', group:'Commitments', icon:'M4 6h16v12H4zM4 10h16M8 14h5'},
  {id:'advances',label:'Down Payments',       eyebrow:'Trade finance',  title:'Down payments — advances received and paid', group:'Commitments', icon:'M12 3v13M7 11l5 5 5-5M4 21h16'},
  {id:'tax',     label:'Tax & Gov. Dues',     eyebrow:'Compliance',     title:'Tax and governmental dues', group:'Commitments', icon:'M6 3h12l2 4H4ZM4 7h16v14H4zM9 12h6M9 16h6'},
  {id:'proc',    label:'PR → PO',             eyebrow:'Procurement',    title:'Purchase requisitions and orders — committed spend', group:'Commitments', icon:'M6 2h9l5 5v15H6zM15 2v5h5M9 13h7M9 17h4'},
  {id:'margin',  label:'Contribution Margin', eyebrow:'Controlling',    title:'Contribution margin and break-even', group:'Reporting', icon:'M4 20V10M10 20V4M16 20v-7M22 20H2'},
  {id:'intake',  label:'Data Intake',         eyebrow:'Foundations',    title:'Data intake — what each team sends, and when', group:'System', icon:'M12 3v12M8 11l4 4 4-4M4 21h16M4 17v4M20 17v4'},
  {id:'data',    label:'Data & Sources',      eyebrow:'Foundations',    title:'Data, sources & ingest log', group:'System', icon:'M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3ZM4 7v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3'},
];

const NAV_GROUPS = ['Cash & Treasury', 'Commitments', 'Receivables', 'Reporting', 'System'];

function renderNav(active) {
  const groups = [];
  NAV.forEach(n => {
    let g = groups.find(x => x.name === n.group);
    if (!g) groups.push(g = {name: n.group, items: []});
    g.items.push(n);
  });
  groups.sort((a, b) => NAV_GROUPS.indexOf(a.name) - NAV_GROUPS.indexOf(b.name));
  document.getElementById('nav').innerHTML = groups.map(g =>
    `<div class="nav-group">${esc(g.name)}</div>` + g.items.map(n =>
      `<a href="#${n.id}" class="${n.id === active ? 'on' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="${n.icon}"></path></svg>
        <span class="label">${esc(n.label)}</span><span class="dot"></span></a>`).join('')
  ).join('');
}

/* ----------------------------------------------------------------- state */

const state = {ccy: 'CONS', chq: 'pdc', horizon: 60, period: 'annual', scope: 'standalone',
               bpYear: null, calMonth: null};

function shiftMonth(dir) {
  if (dir === 0) { state.calMonth = null; render(); return; }
  const [y, m] = (state.calMonth || '').split('-').map(Number);
  const d = new Date(y, m + dir, 1);
  state.calMonth = d.getFullYear() + '-' + d.getMonth();
  render();
}

const setCcy     = (v) => { state.ccy = v; render(); };
const setTfKind = (v) => { state.tfKind = v; render(); };

async function scheduleTax(id, label) {
  const dlg = document.getElementById('dlg');
  document.getElementById('dlg-title').textContent = 'Set settlement date';
  document.getElementById('dlg-body').innerHTML = `
    <div class="row"><label>Obligation</label><div><strong>${esc(label)}</strong></div>
      <div class="basis" style="max-width:none">An unscheduled due is real money the cash
      forecast cannot place. Setting a date brings it in.</div></div>
    <div class="row"><label>Due date</label><input type="date" id="tx-due"></div>`;
  document.getElementById('dlg-save').onclick = async () => {
    const v = document.getElementById('tx-due').value;
    if (!v) { alert('Pick a date.'); return; }
    await send(`/tax/obligations/${id}`, 'PUT', {due_date: v});
    dlg.close(); render();
  };
  dlg.showModal();
}

async function reloadDues() {
  const r = await send('/tax/ingest-governmental-dues', 'POST');
  alert(`${r.loaded} obligations loaded (${f0(r.total_egp)} EGP).\n\n${r.note || ''}`);
  render();
}

function editBehaviour(name, behaviour, portion) {
  const dlg = document.getElementById('dlg');
  document.getElementById('dlg-title').textContent = 'Cost behaviour';
  document.getElementById('dlg-body').innerHTML = `
    <div class="row"><label>Category</label><div><strong>${esc(name)}</strong></div></div>
    <div class="row"><label>Behaviour</label>
      <select id="cb-behaviour">${['variable','fixed','semi'].map(b =>
        `<option value="${b}" ${b === behaviour ? 'selected' : ''}>${b}</option>`).join('')}</select></div>
    <div class="row"><label>Variable share (0–1)</label>
      <input type="number" id="cb-portion" step="0.05" min="0" max="1" value="${portion}"></div>
    <div class="row"><label>Why</label><textarea id="cb-note" rows="2" placeholder="What makes this cost move, or not"></textarea></div>`;
  document.getElementById('dlg-save').onclick = async () => {
    await send('/margin/categories', 'PUT', {
      name, behaviour: document.getElementById('cb-behaviour').value,
      variable_portion: parseFloat(document.getElementById('cb-portion').value),
      note: document.getElementById('cb-note').value,
    });
    dlg.close(); render();
  };
  dlg.showModal();
}

const setChq     = (v) => { state.chq = v; render(); };
const setHorizon = (v) => { state.horizon = +v; render(); };
const setPeriod  = (v) => { state.period = v; render(); };
const setScope   = (v) => { state.scope = v; render(); };
const setBpYear  = (v) => { state.bpYear = v; render(); };

function toggleTheme() {
  const el = document.getElementById('shell');
  const next = el.dataset.theme === 'dark' ? 'light' : 'dark';
  el.dataset.theme = next;
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem('aresco-theme', next); } catch (e) { /* private mode */ }
}

/* ----------------------------------------------------------------- views */

const views = {};

views.cash = async function () {
  const [cash, bal, trend, aging] = await Promise.all([
    get('/cash/available'), get('/cash/balances'),
    get('/cash/balances/trend?days=14'), get('/cash/checks/aging'),
  ]);
  stampHeader(cash.as_of, true);

  const ccys = ['EGP', 'USD', 'EUR', 'AED'].filter(c => bal.by_currency[c] !== undefined);
  const native = state.ccy !== 'CONS';
  const unit = native ? state.ccy + ' · as booked' : 'EGP · consolidated';

  // In a single-currency view every figure is that currency's own book: the
  // cheque legs are filtered to it rather than apportioned, so the chain still
  // subtracts like for like.
  const legs = native
    ? {bank: bal.by_currency[state.ccy] || 0,
       deliv: cash.by_currency_checks ? 0 : null, onhand: null}
    : {bank: cash.bank_balance_egp, deliv: cash.less_delivered_checks_egp, onhand: cash.less_on_hand_checks_egp};

  let hero;
  if (native) {
    const checks = await get('/cash/checks');
    const d = checks.delivered.filter(c => c.currency === state.ccy).reduce((s, c) => s + c.value, 0);
    const h = checks.on_hand.filter(c => c.currency === state.ccy).reduce((s, c) => s + c.value, 0);
    const dn = checks.delivered.filter(c => c.currency === state.ccy).length;
    const hn = checks.on_hand.filter(c => c.currency === state.ccy).length;
    hero = {bank: legs.bank, deliv: d, onhand: h, dn, hn,
            banks: bal.banks.filter(b => b.currencies[state.ccy]).length};
  } else {
    hero = {bank: legs.bank, deliv: legs.deliv, onhand: legs.onhand,
            dn: cash.counts.delivered, hn: cash.counts.on_hand, banks: bal.banks.length};
  }
  const net = hero.bank - hero.deliv - hero.onhand;

  const gaps = [];
  if (cash.coverage_note) gaps.push(cash.coverage_note);
  if (cash.unmapped_statuses && cash.unmapped_statuses.length)
    gaps.push('Cheque statuses not yet classified as delivered or on-hand: ' + cash.unmapped_statuses.join(', ') +
              '. They are excluded from the chain until classified.');

  const bankRows = bal.banks.map(b => {
    const partial = ccys.some(c => (cash.missing_rates || []).includes(c) && b.currencies[c]);
    return `<tr>
      <td style="white-space:nowrap">${nameCell(b.bank, '')}</td>
      ${ccys.map(c => `<td class="num ${!b.currencies[c] ? 'zero' : b.currencies[c] < 0 ? 'neg' : ''}"
          ${state.ccy === c ? 'style="background:var(--steel-soft)"' : ''}>${b.currencies[c] ? f0(b.currencies[c]) : '—'}</td>`).join('')}
      <td class="cons">${partial && !b.total_egp ? '<span class="zero">—</span>' : f0(b.total_egp)}${partial ? ' <span class="badge bad">partial</span>' : ''}</td>
    </tr>`;
  });
  bankRows.push(`<tr class="total"><td>Total</td>
    ${ccys.map(c => `<td class="num">${f0(bal.by_currency[c])}</td>`).join('')}
    <td class="cons">${f0(bal.total_egp)}</td></tr>`);

  const mixCols = {EGP: 'var(--steel-deep)', USD: 'var(--steel)', EUR: 'var(--gold)', AED: 'var(--ink-3)'};
  const fxNow = await get('/fx/current?as_of=' + cash.as_of);
  const mix = ccys.map(c => {
    const r = fxNow.rates[c];
    return {ccy: c, native: bal.by_currency[c], egp: r === undefined ? null : bal.by_currency[c] * r, col: mixCols[c]};
  });
  const mixTotal = mix.reduce((s, m) => s + (m.egp || 0), 0);

  const agingBars = (aging.buckets || []).map(b => {
    const share = aging.total_egp ? b.total_egp / aging.total_egp : 0;
    const col = b.bucket === '0-30' ? 'var(--steel)' : b.bucket === '31-60' ? 'var(--gold)'
              : b.bucket === '61-90' ? 'var(--warn)' : 'var(--bad)';
    return `<div>
      <div style="display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin-bottom:6px">
        <span style="font:500 11.5px var(--font-mono);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-2)">${esc(b.bucket)} days</span>
        <span style="font:600 14px var(--font-mono);font-variant-numeric:tabular-nums;color:var(--figure)">${f0(b.total_egp)}</span>
      </div>
      <div class="bar"><span style="width:${(share * 100).toFixed(1)}%;background:${col}"></span></div>
      <div style="display:flex;justify-content:space-between;font:400 10.5px var(--font-mono);color:var(--ink-3);margin-top:5px">
        <span>${b.count} cheque${b.count === 1 ? '' : 's'}</span><span>${pct(share)}</span></div>
    </div>`;
  });

  page.innerHTML = `
    <div class="intro">
      <p>Consolidated position across ${bal.banks.length} banks and ${ccys.length} currencies. Every figure below is the
         bank statement balance less cheques already issued — the chain, not the tiles, is the number that matters.</p>
      ${seg([{id:'CONS',label:'Consolidated EGP'}].concat(ccys.map(c => ({id:c, label:c}))), state.ccy, 'setCcy')}
    </div>
    ${gaps.length ? notice('warn', gaps.length + ' item' + (gaps.length === 1 ? '' : 's') + ' excluded from the chain', `<ul>${gaps.map(g => `<li>${esc(g)}</li>`).join('')}</ul>`) : ''}

    <div class="equation-card">
      <div class="equation-head">
        <span class="k">Net available cash — derivation</span><span class="rule"></span><span class="k">${esc(unit)}</span>
      </div>
      <div class="equation">
        <div class="eq-term">
          <div class="k">Bank Balance</div>
          <div class="v">${f0(hero.bank)}</div>
          <div class="sub">${hero.banks} bank accounts · statement balance</div>
          <div class="src">As of ${dayLong(cash.as_of)} · Bank Cash workbook</div>
        </div>
        <div class="eq-op">−</div>
        <div class="eq-term deep">
          <div class="k-row"><span class="k">Outstanding Cheques</span><span class="badge delivered">Delivered</span></div>
          <div class="v">${f0(hero.deliv)}</div>
          <div class="sub">${hero.dn} cheque${hero.dn === 1 ? '' : 's'} in beneficiaries' hands</div>
          <div class="src">As of ${dayLong(cash.check_snapshot)} · Checks List</div>
        </div>
        <div class="eq-op">−</div>
        <div class="eq-term gold">
          <div class="k-row"><span class="k">On-Hand Cheques</span><span class="badge onhand">Not delivered</span></div>
          <div class="v">${f0(hero.onhand)}</div>
          <div class="sub">${hero.hn} cheque${hero.hn === 1 ? '' : 's'} still in treasury</div>
          <div class="src"><span class="ar inline">جاهز للتسليم</span> + <span class="ar inline">تمويل خزنة</span></div>
        </div>
        <div class="eq-op equals">=</div>
        <div class="eq-term result">
          <div class="k">Net Available Cash</div>
          <div class="v ${net < 0 ? 'bad' : ''}">${f0(net)}</div>
          <div class="sub">Spendable today after all issued cheques</div>
          <div class="src">Computed · ${dayLong(cash.as_of)}</div>
        </div>
      </div>
    </div>

    <div class="grid g-15">
      ${card('Balances by bank & currency', bal.banks.length + ' banks · source Bank Cash workbook',
        table(`<th>Bank</th>${ccys.map(c => `<th class="num">${c}</th>`).join('')}<th class="num cons">Cons. EGP</th>`, bankRows))}

      <div class="stack">
        <section class="card pad">
          <h2>Currency exposure</h2>
          <div class="stamp" style="margin:4px 0 16px">Share of consolidated EGP · rates ${day(cash.as_of)}</div>
          <div class="mixbar">${mix.map(m => `<span style="flex:${m.egp || 0.0001};background:${m.col}"></span>`).join('')}</div>
          <div style="display:flex;flex-direction:column;gap:9px">
            ${mix.map(m => `<div style="display:flex;align-items:center;gap:10px">
              <span style="width:9px;height:9px;border-radius:2px;flex:none;background:${m.col}"></span>
              <span style="font:500 12px var(--font-mono);letter-spacing:.06em;color:var(--ink-2);width:38px">${m.ccy}</span>
              <span style="flex:1;font-size:12.5px;color:var(--ink-3)">${m.ccy} ${f0(m.native)}</span>
              <span style="font:600 13px var(--font-mono);font-variant-numeric:tabular-nums;color:var(--ink)">${m.egp === null ? '<span class="badge bad">no rate</span>' : f0(m.egp)}</span>
              <span style="font:500 11.5px var(--font-mono);color:var(--ink-3);width:44px;text-align:right">${m.egp === null ? '—' : pct(m.egp / mixTotal)}</span>
            </div>`).join('')}
          </div>
        </section>

        <section class="card pad">
          <div style="display:flex;align-items:baseline;justify-content:space-between;gap:10px">
            <h2>Bank balance · 14 days</h2>
            <span style="font:600 12px var(--font-mono);color:${trend.length > 1 && trend[trend.length-1].balance >= trend[0].balance ? 'var(--ok)' : 'var(--bad)'}">
              ${trend.length > 1 ? (trend[trend.length-1].balance - trend[0].balance >= 0 ? '+' : '−') + Math.abs((trend[trend.length-1].balance - trend[0].balance) / 1e6).toFixed(1) + 'm' : '—'}</span>
          </div>
          <div class="stamp" style="margin-top:4px">Actual · daily close · EGP</div>
          ${lineChart(trend.map(t => ({y: t.balance, label: day(t.date)})), {px: 96, h: 96})}
          <div class="axis"><span>${trend.length ? day(trend[0].date) : ''}</span><span>${trend.length ? day(trend[trend.length-1].date) : ''}</span></div>
        </section>

        <section class="card pad">
          <h2>On-hand cheque aging</h2>
          <div class="stamp" style="margin:4px 0 18px">Days since the cheque date · not yet delivered</div>
          <div style="display:flex;flex-direction:column;gap:14px">${agingBars.join('') || '<div class="empty">None on hand.</div>'}</div>
          <div style="margin-top:20px;padding-top:16px;border-top:1px solid var(--line);display:flex;align-items:baseline;justify-content:space-between">
            <span class="k-inline">Total on hand</span>
            <span style="font:600 20px var(--font-mono);font-variant-numeric:tabular-nums;color:var(--figure)">${f0(aging.total_egp)}</span>
          </div>
        </section>
      </div>
    </div>`;
};

views.cheques = async function () {
  const [checks, sched, aging] = await Promise.all([
    get('/cash/checks'), get(`/cash/checks/schedule?horizon_days=${state.horizon}`), get('/cash/checks/aging'),
  ]);
  stampHeader(checks.snapshot, true);

  const tabs = seg([{id:'pdc',label:'PDC calendar'},{id:'reg',label:'Outstanding register'},{id:'age',label:'On-hand aging'}], state.chq, 'setChq');
  let body = '';

  if (state.chq === 'pdc') {
    // The API returns only days that carry a movement; the calendar needs the
    // running balance on every day of the month, carried forward.
    const start = new Date(sched.as_of + 'T00:00:00');
    // Default to the month carrying the most scheduled value in the horizon —
    // the as-of month is usually near its end and nearly empty.
    if (state.calMonth === null || state.calMonth === undefined) {
      const weight = {};
      sched.days.forEach(d => {
        const dt = new Date(d.date + 'T00:00:00');
        const key = dt.getFullYear() + '-' + dt.getMonth();
        weight[key] = (weight[key] || 0) + d.outflow_egp;
      });
      const best = Object.entries(weight).sort((a, b) => b[1] - a[1])[0];
      state.calMonth = best ? best[0] : start.getFullYear() + '-' + start.getMonth();
    }
    const [y, m] = state.calMonth.split('-').map(Number);
    const dim = new Date(y, m + 1, 0).getDate();
    const byDay = {};
    sched.days.forEach(d => {
      const dt = new Date(d.date + 'T00:00:00');
      if (dt.getFullYear() === y && dt.getMonth() === m) byDay[dt.getDate()] = d;
    });
    // Carry the balance in from any activity before this month, so a month the
    // user pages to opens on the right number rather than the horizon opening.
    let running = sched.opening_balance_egp;
    sched.days.forEach(d => {
      const dt = new Date(d.date + 'T00:00:00');
      if (dt < new Date(y, m, 1)) running = d.closing_balance_egp;
    });
    const cells = [];
    for (let d = 1; d <= dim; d++) {
      const hit = byDay[d];
      if (hit) running = hit.closing_balance_egp;
      cells.push({d, items: hit ? hit.checks : [], bal: running, neg: running < 0});
    }
    // 1 Aug 2026 is a Saturday; the Egyptian working week starts Saturday.
    const week = ['Sat','Sun','Mon','Tue','Wed','Thu','Fri'];
    const lead = (new Date(y, m, 1).getDay() + 1) % 7;
    const blanks = Array.from({length: lead}, () => '<div class="cell blank"></div>');

    body = `
      <div class="tiles-4">
        <div class="tile"><div class="k">Opening net available</div><div class="v">${f0(sched.opening_balance_egp)}</div><div class="n">Carried from cash dashboard</div></div>
        <div class="tile gold"><div class="k">Cheques clearing</div><div class="v">${f0(sched.total_scheduled_egp)}</div><div class="n">${sched.days.reduce((s, d) => s + d.checks.length, 0)} post-dated cheques</div></div>
        <div class="tile grey"><div class="k">Closing ${day(sched.days.length ? sched.days[sched.days.length-1].date : sched.as_of)}</div><div class="v ${sched.closing_balance_egp < 0 ? 'bad' : ''}">${f0(sched.closing_balance_egp)}</div><div class="n">Projected · cheques only</div></div>
        <div class="tile ${sched.first_shortage ? 'alert' : 'ok'}">
          <div class="k">Shortage days</div>
          <div class="v">${sched.days.filter(d => d.shortage).length}</div>
          <div class="n plain">${sched.first_shortage ? 'First breach ' + day(sched.first_shortage) : 'None in this horizon'}</div>
        </div>
      </div>
      <section class="card">
        <div class="card-head">
          <div>
            <h2>Post-dated cheque calendar — ${MON[m]} ${y}</h2>
            <span class="stamp">Projected running balance after each cheque clears · week starts Saturday</span>
          </div>
          <div class="legend">
            <div class="seg" style="margin-right:6px">
              <button onclick="shiftMonth(-1)">‹</button>
              <button onclick="shiftMonth(0)">today</button>
              <button onclick="shiftMonth(1)">›</button>
            </div>
            <span class="i"><span class="sw" style="background:var(--surface-2);border:1px solid var(--line)"></span>Positive</span>
            <span class="i" style="color:var(--bad)"><span class="sw" style="background:var(--bad-soft);border:1px solid var(--bad);background-image:var(--hatch)"></span>Negative</span>
            <span class="i"><span class="sw" style="border:1px dashed var(--ink-3)"></span>Projected</span>
          </div>
        </div>
        <div class="cal-head">${week.map(w => `<div>${w}</div>`).join('')}</div>
        <div class="cal">${blanks.join('')}${cells.map(c => `
          <div class="cell ${c.neg ? 'neg' : c.items.length ? 'has' : ''}">
            <div style="display:flex;align-items:baseline;justify-content:space-between;gap:6px">
              <span class="d">${String(c.d).padStart(2, '0')}</span>
              <span class="chip">${c.items.length ? c.items.length + (c.items.length > 1 ? ' chqs' : ' chq') : ''}</span>
            </div>
            <div class="items">${c.items.slice(0, 3).map(i => `<div class="item">
              <span class="nm">${esc(i.supplier || i.label || '—')}</span>
              <span class="amt">${fM(i.amount_egp)}</span></div>`).join('')}
              ${c.items.length > 3 ? `<div class="item"><span class="nm muted">+${c.items.length - 3} more</span></div>` : ''}</div>
            <div class="balbox"><span class="k">Bal</span><span class="v">${fMs(c.bal)}</span></div>
          </div>`).join('')}</div>
      </section>`;
  }

  if (state.chq === 'reg') {
    const rows = checks.delivered.map(c => `<tr>
      <td style="font:500 12.5px var(--font-mono);color:var(--ink-2);white-space:nowrap">${esc(c.check_number)}</td>
      <td style="max-width:280px">${nameCell(c.supplier, '')}</td>
      <td style="font-size:13px;color:var(--ink-2);white-space:nowrap">${esc(c.bank)}</td>
      <td class="num" style="font-weight:600;font-size:13.5px">${esc(c.currency)} ${f0(c.value)}</td>
      <td class="num" style="font-weight:500;color:var(--ink-2)">${day(c.check_date)}</td>
      <td style="white-space:nowrap">
        <span class="badge delivered"><span class="ar inline">${esc(c.status)}</span></span>
        <button class="btn tiny" style="margin-left:8px" onclick="clearCheque(${c.id})">Cleared</button>
      </td></tr>`);
    body = card('Outstanding issued cheques — delivered',
      'As of ' + dayLong(checks.snapshot) + ' · Bank Balance With Checks List',
      table(`<th>Cheque no.</th><th>Beneficiary</th><th>Bank</th><th class="num">Value</th><th class="num">Cheque date</th><th>Status</th>`,
            rows, 'No delivered cheques outstanding.'),
      `<div class="right"><div style="font:600 20px var(--font-mono);font-variant-numeric:tabular-nums;color:var(--figure)">${f0(checks.delivered_total_egp)}</div>
       <div class="stamp">${checks.delivered.length} cheques · EGP equivalent</div></div>`);
  }

  if (state.chq === 'age') {
    const total = aging.total_egp || 1;
    const bars = (aging.buckets || []).map(b => {
      const col = b.bucket === '0-30' ? 'var(--steel)' : b.bucket === '31-60' ? 'var(--gold)'
                : b.bucket === '61-90' ? 'var(--warn)' : 'var(--bad)';
      return `<div>
        <div style="display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin-bottom:6px">
          <span style="font:500 11.5px var(--font-mono);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-2)">${esc(b.bucket)} days</span>
          <span style="font:600 14px var(--font-mono);font-variant-numeric:tabular-nums;color:var(--figure)">${f0(b.total_egp)}</span></div>
        <div class="bar"><span style="width:${(b.total_egp / total * 100).toFixed(1)}%;background:${col}"></span></div>
        <div style="display:flex;justify-content:space-between;font:400 10.5px var(--font-mono);color:var(--ink-3);margin-top:5px">
          <span>${b.count} cheques</span><span>${pct(b.total_egp / total)}</span></div></div>`;
    });
    const rows = checks.on_hand.map(c => {
      const age = c.check_date ? Math.round((new Date(aging.as_of) - new Date(c.check_date)) / 864e5) : null;
      const cls = age === null ? 'muted' : age > 60 ? 'neg' : age > 30 ? 'warnv' : '';
      return `<tr>
        <td>${nameCell(c.supplier, c.check_number)}</td>
        <td class="num" style="font-weight:600;font-size:13.5px">${esc(c.currency)} ${f0(c.value)}</td>
        <td class="num ${cls}">${age === null ? '—' : age + ' d'}</td>
        <td><span class="badge onhand"><span class="ar inline">${esc(c.status)}</span></span></td></tr>`;
    });
    body = `<div class="grid g-125">
      <section class="card pad">
        <h2>On-hand cheque aging</h2>
        <div class="stamp" style="margin:4px 0 18px">Days since cheque date · not yet delivered to beneficiary</div>
        <div style="display:flex;flex-direction:column;gap:14px">${bars.join('') || '<div class="empty">None on hand.</div>'}</div>
        <div style="margin-top:20px;padding-top:16px;border-top:1px solid var(--line);display:flex;align-items:baseline;justify-content:space-between">
          <span class="k-inline">Total on hand</span>
          <span style="font:600 20px var(--font-mono);font-variant-numeric:tabular-nums;color:var(--figure)">${f0(aging.total_egp)}</span></div>
      </section>
      ${card('On-hand cheque register', 'Printed &amp; signed, still in treasury · ageing from the cheque date',
        table('<th>Beneficiary</th><th class="num">Value</th><th class="num">Age</th><th>Status</th>', rows, 'None on hand.'))}
    </div>`;
  }

  page.innerHTML = `<div class="intro">${tabs}
    ${state.chq === 'pdc' ? seg([{id:'30',label:'30 d'},{id:'60',label:'60 d'},{id:'90',label:'90 d'},{id:'180',label:'180 d'}], String(state.horizon), 'setHorizon') : ''}
  </div>
  ${sched.first_shortage ? notice('bad', 'Projected shortage on ' + dayLong(sched.first_shortage),
      'If every cheque presents on its date the bank balance goes negative. This projection covers cheques only — see the cash flow forecast for the full picture.') : ''}
  ${body}`;
};

views.ar = async function () {
  const [aging, fc, rdoh] = await Promise.all([
    get('/receivables/aging'), get('/receivables/forecast?horizon_days=180'), get('/receivables/rdoh'),
  ]);
  stampHeader(aging.snapshot, true);

  const BUCKETS = ['current', '1m', '2m', '3m+', 'dormant'];
  const LABEL = {current: 'Current', '1m': '1–30', '2m': '31–60', '3m+': '60+', dormant: 'Dormant'};
  const rows = aging.customers.slice(0, 14).map(c => {
    const cellFor = (b) => {
      const v = c.by_bucket[b];
      const tone = !v ? 'zero' : (b === '3m+' || b === 'dormant') ? 'neg' : b === '2m' ? 'warnv' : '';
      return `<td class="num ${tone}">${v ? f0(v) : '—'}</td>`;
    };
    const catCls = c.category === 'Dormant' ? 'bad' : c.category === 'Factory' ? 'delivered' : 'neutral';
    return `<tr>
      <td style="max-width:250px">${nameCell(c.customer, '')}
        <div style="margin-top:3px"><span class="badge ${catCls}">${esc(c.category || '—')}</span></div></td>
      <td style="text-align:center;font:500 11.5px var(--font-mono);color:var(--ink-3)">${Object.keys(c.by_currency).join(' ')}</td>
      ${BUCKETS.map(cellFor).join('')}
      <td class="cons">${f0(c.total_egp)}</td></tr>`;
  });
  const bt = {};
  BUCKETS.forEach(b => bt[b] = (aging.buckets.find(x => x.bucket === b) || {}).total_egp || 0);
  rows.push(`<tr class="total"><td colspan="2">Total · EGP equiv</td>
    ${BUCKETS.map(b => `<td class="num ${b === '3m+' || b === 'dormant' ? 'neg' : b === '2m' ? 'warnv' : ''}">${f0(bt[b])}</td>`).join('')}
    <td class="cons">${f0(aging.total_egp)}</td></tr>`);

  const overdue = bt['1m'] + bt['2m'] + bt['3m+'] + bt.dormant;

  const fcRows = fc.items.map(r => {
    const shift = r.is_override
      ? Math.round((new Date(r.expected_date) - new Date(r.computed_date)) / 864e5) : 0;
    return `<tr>
      <td style="max-width:290px;vertical-align:top;padding:11px 16px">
        ${nameCell(r.customer, '')}
        <div style="margin-top:3px"><span class="badge neutral">${esc(r.category || '—')}</span></div>
        ${r.is_override ? `<div class="trail">
          <svg viewBox="0 0 24 24" fill="none" stroke="var(--gold-ink)" stroke-width="1.7" stroke-linecap="round"><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"></path></svg>
          <div style="min-width:0">
            <div class="who">${esc(r.override_by || 'overridden')}${r.override_at ? ' · ' + esc(r.override_at.slice(0, 16).replace('T', ' ')) : ''}</div>
            ${r.override_note ? `<div class="note">${esc(r.override_note)}</div>` : ''}
          </div>
          <button class="btn tiny" style="margin-left:auto;flex:none" onclick="clearOverride(${r.receivable_id})">Revert</button>
        </div>` : ''}
      </td>
      <td class="num" style="vertical-align:top;padding:11px 12px">
        <div style="font-weight:600;font-size:13.5px">${f0(r.amount)}</div>
        <div style="font:400 10.5px var(--font-mono);color:var(--ink-3);letter-spacing:.06em">${esc(r.currency)}</div></td>
      <td class="num" style="vertical-align:top;font-weight:500;color:var(--ink-2)">${day(r.due_date)}</td>
      <td style="vertical-align:top;padding:11px 12px;white-space:nowrap">
        <div class="model-out">${day(r.computed_date)}</div>
        <div class="basis">${esc(r.basis || '')}</div></td>
      <td style="vertical-align:top;padding:11px 12px;white-space:nowrap">
        <button class="f-date ${r.is_override ? 'ovr' : ''}" onclick='editForecast(${JSON.stringify(r).replace(/'/g, "&#39;")})'>${day(r.expected_date)}</button>
        ${shift ? `<div class="shift">${shift > 0 ? '+' : ''}${shift} d</div>` : ''}</td>
      <td style="vertical-align:top;padding:11px 12px;white-space:nowrap">
        <span class="f-prob ${r.is_override ? 'ovr' : ''}" style="display:inline-block">${pct(r.probability, 0)}</span></td>
      <td class="cons" style="vertical-align:top;padding:11px 16px">${f0(r.weighted_egp)}</td>
    </tr>`;
  });

  const rd = rdoh.rdoh_days;
  page.innerHTML = `
    <div class="intro">
      <p>Aging by customer and currency, and the collection forecast the receivables team owns.
         The model's own output is never overwritten — an override is stored beside it.</p>
      <button class="btn" onclick="recompute()">Recompute forecast</button>
    </div>
    ${rdoh.caveats && rdoh.caveats.length
      ? noticeList(rdoh.caveats, 'warn', `RDOH reads ${rd} days on the loaded data, but is not reportable as it stands`) : ''}

    <div class="grid g-16" style="margin-bottom:18px">
      ${card('AR aging by customer & currency', 'As of ' + dayLong(aging.snapshot) + ' · Cash-In workbook',
        table(`<th>Customer</th><th class="mid">Ccy</th>${BUCKETS.map(b => `<th class="num">${LABEL[b]}</th>`).join('')}<th class="num cons">Total</th>`, rows),
        `<div class="right"><div style="font:600 20px var(--font-mono);font-variant-numeric:tabular-nums;color:var(--figure)">${f0(aging.total_egp)}</div>
         <div class="stamp">Total EGP equiv · ${pct(overdue / (aging.total_egp || 1), 0)} overdue</div></div>`)}

      <section class="card pad">
        <div style="display:flex;align-items:baseline;justify-content:space-between;gap:10px">
          <h2>Receivables days on hand</h2>
          ${rdoh.reportable ? '<span class="badge ok">reportable</span>' : '<span class="badge bad">not reportable</span>'}
        </div>
        <div class="stamp" style="margin-top:4px">${esc(rdoh.revenue_source || 'no revenue basis')}</div>
        <div style="display:flex;align-items:baseline;gap:10px;margin-top:16px">
          <span class="big">${rd ?? '—'}</span><span class="k-inline">days · working list</span>
        </div>
        ${rdoh.rdoh_days_on_balance_sheet_ar ? `
        <div style="display:flex;align-items:baseline;gap:10px;margin-top:14px;padding-top:14px;border-top:1px solid var(--line)">
          <span style="font:600 26px var(--font-mono);font-variant-numeric:tabular-nums;color:var(--bad)">${rdoh.rdoh_days_on_balance_sheet_ar}</span>
          <span class="k-inline">days · on balance-sheet AR</span></div>
        <div class="prose" style="margin-top:12px">Balance-sheet receivables of ${f0(rdoh.balance_sheet_ar_egp)} EGP against a
          working list of ${f0(rdoh.ar_total_egp)} EGP. The gap, not the ratio, is the finding.</div>` : ''}
      </section>
    </div>

    ${card('Collection forecast — editable',
      'Model output is never overwritten · overrides are stored alongside and feed the cash flow forecast',
      table(`<th>Customer</th><th class="num">Amount</th><th class="num">Due</th><th>Model forecast</th>
             <th class="edit">Expected date ✎</th><th class="edit">Conf.</th><th class="num cons">Weighted</th>`,
            fcRows, 'No forecast yet — press Recompute forecast.') +
      `<div class="card-foot">
        <span class="legend"><span class="i"><span class="sw" style="border:1px dashed var(--steel-line);background:var(--surface);width:22px;height:13px;border-radius:4px"></span>Editable, on model</span></span>
        <span class="legend"><span class="i" style="color:var(--gold-ink)"><span class="sw" style="width:22px;height:13px;border-radius:4px;border:1px solid var(--gold);background:var(--gold-soft);box-shadow:inset 0 -2px 0 var(--gold)"></span>Overridden by user</span></span>
        <span class="legend"><span class="i"><span class="ln" style="border-bottom:1px dashed var(--ink-3);height:0"></span>Model output — read only</span></span>
        <span style="margin-left:auto" class="stamp">${fc.override_count} override${fc.override_count === 1 ? '' : 's'} · each written with user + timestamp</span>
      </div>`,
      `<div style="display:flex;align-items:center;gap:20px">
        <div class="right"><div class="stamp">Gross open</div><div style="font:600 17px var(--font-mono);font-variant-numeric:tabular-nums;color:var(--ink-2)">${f0(aging.total_egp)}</div></div>
        <div class="right"><div class="stamp">Probability-weighted</div><div style="font:600 22px var(--font-mono);font-variant-numeric:tabular-nums;color:var(--figure)">${f0(fc.total_expected_egp)}</div></div>
      </div>`)}`;
};

views.cf = async function () {
  const f = await get(`/forecast/daily?horizon_days=${state.horizon}`);
  stampHeader(f.as_of, true);
  const w = f.windows[state.horizon + 'd'] || f.windows['30d'] || {};

  const curve = f.days.map(d => ({y: d.closing_egp, label: day(d.date), flag: d.shortage}));
  const sum = (d, type) => d.items.filter(i => i.type === type).reduce((s, i) => s + Math.abs(i.amount_egp), 0);
  const isPayroll = (i) => i.type === 'expense' && /payroll|salar|أجور|رواتب/i.test(i.label || '');

  const rows = f.days.slice(0, 16).map(d => {
    const coll = sum(d, 'collection') + sum(d, 'forecast_inflow');
    const chq = sum(d, 'check');
    const pay = d.items.filter(isPayroll).reduce((s, i) => s + Math.abs(i.amount_egp), 0);
    const other = d.outflow_egp - chq - pay;
    const dt = new Date(d.date + 'T00:00:00');
    return `<tr class="${d.shortage ? 'shortage' : ''}">
      <td style="padding:9px 16px;white-space:nowrap">
        <span style="font:600 12.5px var(--font-mono);color:var(--ink)">${String(dt.getDate()).padStart(2,'0')} ${MON[dt.getMonth()]}</span>
        <span style="font:400 10.5px var(--font-mono);color:var(--ink-3);margin-left:6px">${['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][dt.getDay()]}</span></td>
      <td class="num" style="color:var(--ink-2)">${fMs(d.opening_egp)}</td>
      <td class="num pos">${coll ? fMs(coll) : '—'}</td>
      <td class="num" style="color:var(--ink-2)">${chq ? '(' + fMs(chq) + ')' : '—'}</td>
      <td class="num" style="color:var(--ink-2)">${pay ? '(' + fMs(pay) + ')' : '—'}</td>
      <td class="num muted">${other > 0.5 ? '(' + fMs(other) + ')' : '—'}</td>
      <td class="num ${d.net_egp < 0 ? 'neg' : 'pos'}">${fMs(d.net_egp)}</td>
      <td class="cons ${d.closing_egp < 0 ? 'neg' : ''}">${fMs(d.closing_egp)}</td>
      <td>${d.shortage ? '<span class="badge bad">shortage</span>' : ''}</td></tr>`;
  });

  page.innerHTML = `
    <div class="intro">
      <p>Opening net available cash, expected collections at the confidence set by the receivables team,
         scheduled cheques, payroll and running opex. Everything to the right of the marker is a projection.</p>
      ${seg([{id:'30',label:'30 days'},{id:'60',label:'60 days'},{id:'90',label:'90 days'}], String(state.horizon), 'setHorizon')}
    </div>
    ${f.first_shortage
      ? notice('bad', 'Cash shortage projected on ' + dayLong(f.first_shortage),
          `${f.shortage_days} day${f.shortage_days === 1 ? '' : 's'} below zero in this window, low point ${f0(f.min_balance_egp)} EGP.` +
          (f.shortages[0] && f.shortages[0].driven_by.length ? ' Largest outflows that day: ' + f.shortages[0].driven_by.map(esc).join(', ') + '.' : ''))
      : notice('info', 'No shortage projected in the next ' + state.horizon + ' days', 'Low point ' + f0(f.min_balance_egp) + ' EGP.')}
    ${noticeList(f.warnings, 'warn', 'Gaps in the inputs')}

    <div class="tiles-4">
      <div class="tile ok"><div class="k">Expected collections</div><div class="v">${f0(w.inflow_egp)}</div><div class="n">Probability-weighted · forecast</div></div>
      <div class="tile"><div class="k">Total outflows</div><div class="v">${f0(w.outflow_egp)}</div><div class="n">Cheques + payroll + opex</div></div>
      <div class="tile grey"><div class="k">Closing balance</div><div class="v ${w.closing_egp < 0 ? 'bad' : ''}">${f0(w.closing_egp)}</div><div class="n">End of horizon · projected</div></div>
      <div class="tile ${w.shortage_days ? 'alert' : 'ok'}"><div class="k">Shortage days</div><div class="v">${w.shortage_days ?? 0}</div>
        <div class="n plain">${w.shortage_days ? 'Low point ' + f0(w.min_balance_egp) : 'Stays positive throughout'}</div></div>
    </div>

    <section class="card pad" style="margin-bottom:18px">
      <div style="display:flex;align-items:baseline;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:14px">
        <div><h2>Daily closing balance</h2><div class="stamp" style="margin-top:4px">EGP · projected from ${dayLong(f.as_of)}</div></div>
        <div class="legend">
          <span class="i"><span class="ln" style="background:var(--steel-deep)"></span>Actual</span>
          <span class="i"><span class="ln" style="border-top:2px dashed var(--steel);height:0"></span>Forecast</span>
          <span class="i" style="color:var(--bad)"><span class="sw" style="border-radius:50%;width:9px;height:9px;background:var(--bad)"></span>Shortage day</span>
        </div>
      </div>
      ${lineChart(curve, {splitAt: 1})}
      <div class="axis"><span>${dayLong(f.as_of)} · today</span><span>end of horizon · projected</span></div>
    </section>

    ${card('Daily projection — first 16 days', 'EGP millions · negatives in parentheses',
      table(`<th>Date</th><th class="num">Opening</th><th class="num">Collections</th><th class="num">Cheques</th>
             <th class="num">Payroll</th><th class="num">Other</th><th class="num">Net</th><th class="num cons">Closing</th><th>Flag</th>`, rows),
      `<span class="stamp legend"><span class="i"><span class="sw" style="width:22px;height:12px;background:var(--bad-soft);background-image:var(--hatch);border:1px solid var(--bad-line)"></span>Shortage row</span></span>`)}`;
};

views.pnl = async function () {
  const [summary, projects, ratios] = await Promise.all([
    get(`/reporting/summary?period_type=${state.period}&scope=${state.scope}`),
    get('/reporting/projects'),
    get(`/reporting/ratios?period_type=${state.period}`),
  ]);
  const bad = summary.unbalanced_periods || {};
  const cols = pickPeriods(summary.periods);

  const projRows = projects.slice(0, 12).map(p => `<tr>
    <td style="max-width:250px">${nameCell(p.name, p.client)}</td>
    <td class="num muted">${esc(p.currency)}</td>
    <td class="num" style="font-weight:600">${f0(p.contract_value)}</td>
    <td class="num muted">${f0(p.planned_revenue)}</td>
    <td><div class="bar thin"><span style="width:${p.contract_value ? Math.min(100, p.planned_revenue / p.contract_value * 100).toFixed(0) : 0}%;background:var(--steel);background-image:var(--hatch-fc)"></span></div></td>
  </tr>`);

  const matrixRows = summary.rows.map(r => `<tr>
    <td style="font-size:13px"><strong>${esc(r.metric)}</strong>
      <span class="stamp" style="margin-left:7px">${r.statement}</span></td>
    ${cols.map(p => {
      const c = r.periods[p];
      if (!c) return '<td class="num zero">—</td>';
      return `<td class="num">${fM(c.value_egp)} <span class="tag ${c.scenario === 'actual' ? 'a' : 'p'}">${c.scenario === 'actual' ? 'A' : 'P'}</span></td>`;
    }).join('')}
  </tr>`);

  const ratioRows = ratios.lines.map(r => `<tr>
    <td style="font-size:13px;color:var(--ink-2)">${esc(r.ratio)}</td>
    ${cols.map(p => `<td class="num">${r.periods[p] === undefined ? '<span class="zero">—</span>'
      : (Math.abs(r.periods[p]) < 3 ? r.periods[p].toFixed(2) : fN(r.periods[p], 1))}</td>`).join('')}
  </tr>`);

  page.innerHTML = `
    <div class="intro">
      <p>Per-project phasing against contract value, and the summarised statement matrix on a
         standalone and consolidated basis.</p>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        ${seg([{id:'monthly',label:'monthly'},{id:'quarterly',label:'quarterly'},{id:'annual',label:'annual'}], state.period, 'setPeriod')}
        ${seg([{id:'standalone',label:'standalone'},{id:'consolidated',label:'consolidated'}], state.scope, 'setScope')}
      </div>
    </div>
    ${summary.note ? notice('warn', 'Periods the plan itself disowns', esc(summary.note)) : ''}
    ${notice('info', '', `<span class="tag a">A</span> reported actual &nbsp; <span class="tag p">P</span> projection from the business plan.
       Figures shown in EGP; the source workbook holds them in thousands.`)}

    ${card('Statement matrix', esc(state.scope) + ' · ' + esc(state.period) + ' · EGP',
      table(`<th>Metric</th>${cols.map(p => `<th class="num">${esc(p)}${bad[p] ? ' <span class="badge bad">unbalanced</span>' : ''}</th>`).join('')}`,
            matrixRows, 'No data for this scope. Consolidated figures need a group pack — the business plan is standalone only.'))}

    <div style="margin:18px 0">
      <div class="seg" style="margin-bottom:12px">
        <button onclick="showStatement('IS')">Income statement</button>
        <button onclick="showStatement('BS')">Balance sheet</button>
        <button onclick="showStatement('CF')">Cash flow</button>
        <button onclick="showVariance()">Actual vs budget</button>
      </div>
      <div id="stmt"></div>
    </div>

    <div class="grid g-15s">
      ${card('Projects', projects.length + ' loaded · budget phasing from the backlog sheet',
        table('<th>Project</th><th class="num">Ccy</th><th class="num">Contract value</th><th class="num">Planned revenue</th><th>Phased</th>', projRows))}
      ${card('Ratios', esc(state.period),
        table(`<th>Ratio</th>${cols.map(p => `<th class="num">${esc(p)}</th>`).join('')}`, ratioRows))}
    </div>`;
};

views.bp = async function () {
  const bp = await get('/reporting/business-plan' + (state.bpYear ? '?plan_year=' + state.bpYear : ''));
  const k = bp.kpis;
  const cols = bp.default_columns;
  // Two years of history for context, then the plan horizon — no further.
  const lo = +cols[0] - 2, hi = +cols[cols.length - 1];
  const chart = bp.revenue_chart.filter(c => +c.period >= lo && +c.period <= hi);
  const maxRev = Math.max(...chart.map(c => c.value));

  const bars = chart.map(c => {
    const actual = c.scenario === 'actual';
    return `<div class="col">
      <span class="v ${actual ? 'actual' : ''}">${(c.value / 1000).toFixed(0)}m</span>
      <span class="b ${actual ? 'actual' : 'plan'}" style="height:${Math.max(2, c.value / maxRev * 100)}%"></span>
      <div class="lbl">${esc(c.period)}<span class="tag ${actual ? 'a' : 'p'}">${c.tag}</span></div>
    </div>`;
  });

  const maxQ = Math.max(...bp.quarters.map(q => q.revenue), 1);
  const qRows = bp.quarters.map(q => `<tr>
    <td style="font:600 12.5px var(--font-mono);color:var(--ink-2)">${q.quarter}
      <span class="tag ${q.scenario === 'actual' ? 'a' : 'p'}">${q.scenario === 'actual' ? 'A' : 'P'}</span></td>
    <td class="num">
      <div style="font-weight:600;font-size:13px">${f0(q.revenue)}</div>
      <div class="bar thin" style="margin-top:5px"><span style="width:${(q.revenue / maxQ * 100).toFixed(0)}%;background:var(--steel);${q.scenario === 'actual' ? '' : 'background-image:var(--hatch-fc);'}"></span></div></td>
    <td class="num" style="color:var(--ink-2)">${f0(q.ebitda)}</td>
    <td class="num muted" style="font-size:11.5px">${pct(q.margin)}</td>
    <td class="num" style="color:var(--ink-2);padding-right:16px">${f0(q.eat)}</td></tr>`);

  const plRows = bp.pl.map(r => {
    const em = r.kind === 'em', isPct = r.kind === 'pct';
    const byPeriod = {};
    r.cells.forEach(c => byPeriod[c.period] = c);
    return `<tr class="${em ? 'em' : ''}">
      <td style="padding:${em ? '11px' : '8px'} 16px;font-size:${isPct ? '12px' : '13px'};white-space:nowrap;
        ${em ? 'font-weight:600;color:var(--ink);' : isPct ? 'color:var(--ink-3);padding-left:26px;' : 'color:var(--ink-2);'}">${esc(r.line_item)}</td>
      ${cols.map((p, i) => {
        const c = byPeriod[p];
        const v = c ? c.value : null;
        const showVar = c && c.variance !== null && c.variance !== undefined && !isPct;
        // Rule 03: in a column headed 'actual', a line the audited statements
        // don't report falls back to the plan — and must not read as reported.
        const isFallback = c && bp.scenario_by_year[p] === 'actual' && c.actual === null && c.budget !== null;
        return `<td class="num" title="${isFallback ? 'Plan figure — the signed statements do not report this line separately' : ''}"
          style="padding:${em ? '11px' : '8px'} 12px;
          ${i === 0 ? 'border-right:1px solid var(--line);' : ''}
          ${isFallback ? 'background-image:var(--hatch-fc);' : ''}
          ${isPct ? 'font:500 11.5px var(--font-mono);color:var(--ink-3);'
                  : `font:${em ? '600 13px' : '500 12.5px'} var(--font-mono);color:${v !== null && v < 0 ? 'var(--ink-3)' : em ? 'var(--figure)' : 'var(--ink-2)'};`}">
          ${v === null ? '—' : isPct ? pct(v) : fK(v)}${isFallback ? '<span style="font:600 8px var(--font-mono);color:var(--gold-ink);margin-left:4px;vertical-align:super">P</span>' : ''}
          ${showVar ? `<div style="font:500 10px var(--font-mono);margin-top:2px;color:${c.variance < 0 ? 'var(--bad)' : 'var(--ok)'}">
            ${c.variance < 0 ? '' : '+'}${fK(c.variance)}${c.variance_pct !== null ? ' · ' + (c.variance_pct * 100).toFixed(0) + '%' : ''}</div>` : ''}
        </td>`;
      }).join('')}
    </tr>`;
  });

  page.innerHTML = `
    <div class="intro">
      <p>The board-approved plan against reported actuals. Actual years are solid; plan years are hatched
         and dashed throughout — a projection is never shown as a fact.</p>
      ${seg(bp.years.filter(y => bp.scenario_by_year[y] === 'budget').slice(0, 5).map(y => ({id: y, label: y})),
            k.plan_year, 'setBpYear')}
    </div>
    ${bp.note ? notice('warn', 'Years excluded', esc(bp.note)) : ''}
    ${(() => {
      const rev = bp.pl.find(r => r.line_item === 'Total Revenues');
      const prior = rev && rev.cells.find(c => c.period === k.prior_year && c.variance !== null && c.variance !== undefined);
      if (!prior || prior.variance >= 0) return '';
      return notice('bad', `${k.prior_year} landed ${pct(Math.abs(prior.variance_pct), 0)} below plan`,
        `Reported revenue of ${f0(prior.actual)} KEGP against a plan of ${f0(prior.budget)} KEGP.
         The ${k.plan_year} plan of ${f0(k.revenue)} KEGP is therefore a
         <strong>${k.step_up.toFixed(1)}× step-up on what ${k.prior_year} actually delivered</strong>,
         not the ${(k.revenue / prior.budget).toFixed(1)}× the plan-on-plan comparison implies.`);
    })()}

    <div class="tiles-4">
      <div class="tile"><div class="k">${k.plan_year} Revenues — plan</div><div class="v">${f0(k.revenue)}</div><div class="n">KEGP · BP 2026-2030.xlsx</div></div>
      <div class="tile"><div class="k">${k.plan_year} EBITDA — plan</div><div class="v">${f0(k.ebitda)}</div><div class="n">${pct(k.ebitda_margin)} margin · KEGP</div></div>
      <div class="tile ok"><div class="k">${k.plan_year} EAT — plan</div><div class="v">${f0(k.eat)}</div><div class="n">${pct(k.eat_margin)} of revenues · KEGP</div></div>
      <div class="tile warn"><div class="k">Plan step-up</div><div class="v">${k.step_up ? k.step_up.toFixed(1) + '×' : '—'}</div>
        <div class="n plain">${k.plan_year} plan vs ${k.prior_year} ${k.prior_scenario === 'actual' ? '<strong>actual</strong>' : 'plan'}
          (${f0(k.prior_revenue)}) — backed by Backlog &amp; Expected Turnover</div></div>
    </div>

    <div class="grid g-15s" style="margin-bottom:18px">
      <section class="card pad" style="display:flex;flex-direction:column">
        <div style="display:flex;align-items:baseline;justify-content:space-between;gap:14px;flex-wrap:wrap">
          <div><h2>Total revenues — actual vs plan</h2>
            <div class="stamp" style="margin-top:4px">EGP m · Financial Statements sheet · KEGP '000</div></div>
          <div class="legend">
            <span class="i"><span class="sw" style="background:var(--steel-deep)"></span>Actual</span>
            <span class="i"><span class="sw" style="background:var(--steel-soft);background-image:var(--hatch-fc);border:1.5px dashed var(--steel)"></span>Plan</span>
          </div>
        </div>
        <div class="bars">${bars.join('')}</div>
      </section>

      <section class="card" style="display:flex;flex-direction:column">
        <div class="card-head" style="display:block">
          <h2>${k.plan_year} quarterly phasing</h2>
          <span class="stamp">KEGP · hatched = projection</span>
        </div>
        ${table('<th>Q</th><th class="num">Revenue</th><th class="num">EBITDA</th><th class="num">%</th><th class="num">EAT</th>',
                qRows, 'No quarterly phasing for this year.')}
        <div class="card-foot" style="margin-top:auto">
          ${bp.h2_share !== null && bp.h2_share !== undefined
            ? `Phasing is ${bp.h2_share > 0.55 ? 'back-loaded' : bp.h2_share < 0.45 ? 'front-loaded' : 'even'}:
               H2 carries ${pct(bp.h2_share, 0)} of plan-year revenue.
               ${bp.h2_share > 0.55 ? 'Watch Q3 — it assumes the Backlog sheet\'s expected awards convert on time.' : ''}`
            : 'Quarterly phasing is incomplete for this year.'}
        </div>
      </section>
    </div>

    ${card(`P&L — actual vs plan, ${cols[0]}–${cols[cols.length-1]}`,
      `KEGP '000 · Aresco BP - 2026-2030.xlsx · Financial Statements · negatives in parentheses`,
      table(`<th>KEGP '000</th>${cols.map(p => {
        const actual = bp.scenario_by_year[p] === 'actual';
        return `<th class="num" ${actual ? 'style="background:var(--steel-soft);color:var(--steel-deep);border-right:1px solid var(--line)"' : ''}>${esc(p)} ${actual ? 'A' : 'P'}</th>`;
      }).join('')}`, plRows),
      `<div class="legend"><span class="i"><span class="tag a">A · actual</span></span><span class="i"><span class="tag p">P · plan</span></span>
        <span class="i" style="color:var(--bad)">variance under the figure where the year has both</span>
        <span class="i"><span class="sw" style="background-image:var(--hatch-fc);border:1px solid var(--line)"></span>hatched <sup style="color:var(--gold-ink)">P</sup> = plan, not reported</span></div>`)}`;
};

views.ecl = async function () {
  const [run, policy] = await Promise.all([get('/ecl/preview'), get('/ecl/policy')]);
  stampHeader(run.as_of, true);

  const rows = run.by_bucket.map(b => `<tr>
    <td style="font-size:13.5px;font-weight:500;white-space:nowrap">${esc(b.bucket)}</td>
    <td style="font:500 11.5px var(--font-mono);letter-spacing:.05em;text-transform:uppercase;color:var(--ink-3)">${b.count} accounts</td>
    <td class="num">${f0(b.gross_egp)}</td>
    <td><div style="display:flex;align-items:center;gap:9px">
      <span class="bar" style="width:70px;height:8px;flex:none"><span style="width:${Math.min(100, b.effective_rate * 100).toFixed(0)}%;background:var(--bad)"></span></span>
      <span style="font:600 12.5px var(--font-mono);color:var(--ink-2)">${pct(b.effective_rate)}</span></div></td>
    <td class="cons">${f0(b.ecl_egp)}</td></tr>`);
  rows.push(`<tr class="total"><td colspan="2">Total</td><td class="num">${f0(run.total_gross_egp)}</td>
    <td style="font:600 12.5px var(--font-mono);color:var(--ink-2);padding:12px">${pct(run.coverage_ratio)} coverage</td>
    <td class="cons" style="font-size:16px">${f0(run.total_ecl_egp)}</td></tr>`);

  const polRows = policy.map(p => `<tr>
    <td style="font:500 11.5px var(--font-mono);text-transform:uppercase;color:var(--ink-3)">${esc(p.segment)}</td>
    <td style="font-size:13px">${esc(p.bucket)}</td>
    <td class="num"><button class="f-date" onclick="editPolicy('${esc(p.segment)}','${esc(p.bucket)}',${p.loss_rate})">${pct(p.loss_rate)}</button></td>
    <td class="muted" style="font-size:12px">${esc(p.note || '')}</td></tr>`);

  page.innerHTML = `
    <div class="intro">
      <p>${esc(run.basis)}. Loss rates are the provision matrix; the overlay is the forward-looking
         adjustment IFRS 9.5.5.17(c) requires.</p>
      <button class="btn primary" onclick="commitEcl()">Commit monthly run</button>
    </div>
    <div class="grid g-15s">
      ${card('Provision matrix output',
        `IFRS 9 simplified approach · as at ${dayLong(run.as_of)} · overlay ${run.macro_overlay}×`,
        table('<th>Bucket</th><th>Segment</th><th class="num">Gross EGP</th><th style="width:150px">Loss rate</th><th class="num cons">ECL</th>', rows))}
      <div class="stack">
        <section class="card pad" style="border-left:3px solid var(--ok)">
          <div class="k" style="font:500 10px var(--font-mono);letter-spacing:.11em;text-transform:uppercase;color:var(--ink-3)">Provision to recognise</div>
          <div style="font:600 33px/1.05 var(--font-mono);font-variant-numeric:tabular-nums;color:var(--figure);letter-spacing:-.02em;margin-top:10px">${f0(run.total_ecl_egp)}</div>
          <div style="font-size:12.5px;color:var(--ink-2);margin-top:8px">${pct(run.coverage_ratio)} of gross trade receivables of EGP ${f0(run.total_gross_egp)}</div>
          <div class="stamp" style="margin-top:12px">Preview · not yet committed to ecl_runs</div>
        </section>
        <section class="card pad">
          <h2>Forward-looking overlay</h2>
          <div class="stamp" style="margin:4px 0 14px">Basis recorded for the auditor</div>
          <div style="display:flex;gap:10px;font-size:13px;color:var(--ink);line-height:1.5">
            <span style="font:600 12px var(--font-mono);color:var(--gold);flex:none">${run.macro_overlay}×</span>
            <span>${esc(run.overlay_basis)}</span></div>
        </section>
        ${card('Loss rates', 'Click a rate to calibrate it against write-off history',
          table('<th>Segment</th><th>Bucket</th><th class="num">Base rate</th><th>Note</th>', polRows))}
      </div>
    </div>`;
};

views.audit = async function () {
  const [knowledge, queries] = await Promise.all([get('/audit/knowledge'), get('/audit/queries')]);
  const unverified = knowledge.filter(k => k.needs_verification);
  const latest = queries[0];

  page.innerHTML = `
    <div class="intro">
      <p>Drafts responses to auditor queries, citing only references held in the store and quoting only
         figures read from the database. Anything it needed but did not have comes back as a flag.</p>
      <button class="btn" onclick="seedKnowledge()">Reload reference pack</button>
    </div>
    ${unverified.length ? notice('bad', unverified.length + ' references still need verification',
      `Entries marked <em>verify</em> or <em>placeholder</em> carry article numbers, tax rates or company policy that
       must be checked against the official text before any response relying on them is sent to an auditor:
       ${unverified.slice(0, 6).map(k => esc(k.ref)).join(', ')}${unverified.length > 6 ? '…' : ''}`) : ''}

    <div class="grid g-135">
      <div class="stack">
        <section class="card">
          <div class="card-head"><div><h2>New auditor query</h2><span class="stamp">Paste the auditor's comment verbatim</span></div></div>
          <div style="padding:18px">
            <textarea id="q-text" rows="6" placeholder="Please explain the basis for…"
              style="width:100%;border:1px solid var(--line);background:var(--surface-2);color:var(--ink);border-radius:9px;padding:11px;font-size:13.5px;line-height:1.55;resize:vertical"></textarea>
            <div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap">
              <input id="q-ref" placeholder="Auditor ref" style="flex:1;min-width:120px;border:1px solid var(--line);background:var(--surface-2);color:var(--ink);border-radius:8px;padding:8px 10px;font-size:13px">
              <input id="q-area" placeholder="Area" style="flex:1;min-width:120px;border:1px solid var(--line);background:var(--surface-2);color:var(--ink);border-radius:8px;padding:8px 10px;font-size:13px">
              <button class="btn primary" onclick="draftAudit()">Draft response</button>
            </div>
          </div>
        </section>
        ${card('Reference store', knowledge.length + ' references the agent may cite',
          table('<th>Framework</th><th>Ref</th><th>Status</th>',
            knowledge.slice(0, 30).map(k => `<tr>
              <td><span class="badge neutral">${esc(k.framework)}</span></td>
              <td style="font-size:13px"><strong>${esc(k.ref)}</strong><div class="en">${esc(k.title)}</div></td>
              <td>${k.needs_verification ? '<span class="badge bad">verify</span>' : '<span class="badge ok">ok</span>'}</td>
            </tr>`), 'Empty — press "Reload reference pack".'))}
      </div>

      <div class="stack" id="draft-out">
        ${latest ? renderDraft(latest) : `<section class="card pad"><div class="empty">
          No queries drafted yet. Paste an auditor comment on the left and press <strong>Draft response</strong>.</div></section>`}
      </div>
    </div>`;
};

function renderDraft(d) {
  const cites = Array.isArray(d.standards_cited) ? d.standards_cited : [];
  const docs = Array.isArray(d.documents_required) ? d.documents_required : [];
  const flags = Array.isArray(d.risk_flags) ? d.risk_flags : [];
  const body = (d.draft_response || '').split(/\n\n+/).filter(Boolean);
  return `
    <section class="card">
      <div class="card-head">
        <div>
          ${d.reference ? `<div style="font:600 11px var(--font-mono);letter-spacing:.09em;text-transform:uppercase;color:var(--steel-deep)">${esc(d.reference)}</div>` : ''}
          <h2 style="margin-top:5px">Drafted response</h2>
          <span class="stamp">${esc(d.status || 'draft')} · grounded in the reference store</span>
        </div>
        <div class="right"><div class="stamp">${esc(d.area || '')}</div></div>
      </div>
      <div style="padding:18px">
        ${d.assessment ? notice('info', 'Assessment for the controller', esc(d.assessment)) : ''}
        <div style="display:flex;flex-direction:column;gap:14px">
          ${body.map(p => `<p style="margin:0;font-size:13.8px;color:var(--ink);line-height:1.65">${esc(p)}</p>`).join('')}
        </div>
        ${cites.length ? `<div style="margin-top:20px;padding-top:16px;border-top:1px solid var(--line)">
          <div style="font:500 10px var(--font-mono);letter-spacing:.13em;text-transform:uppercase;color:var(--ink-3);margin-bottom:12px">Standards cited</div>
          <div style="display:flex;flex-direction:column;gap:7px">
            ${cites.map(c => `<div style="display:flex;align-items:baseline;gap:12px;padding:8px 11px;border-radius:8px;background:var(--steel-soft);border:1px solid var(--steel-line)">
              <span style="font:600 11.5px var(--font-mono);color:var(--steel-deep);flex:none;min-width:118px">${esc(c.ref || c)}</span>
              <span style="font-size:12.5px;color:var(--ink-2)">${esc(c.relevance || '')}</span></div>`).join('')}
          </div></div>` : ''}
        ${docs.length ? `<div style="margin-top:18px">
          <div style="font:500 10px var(--font-mono);letter-spacing:.13em;text-transform:uppercase;color:var(--ink-3);margin-bottom:10px">Documents to attach</div>
          <div style="display:flex;flex-direction:column;gap:8px">
            ${docs.map(x => `<div style="display:flex;align-items:flex-start;gap:9px;padding:10px 12px;border:1px solid var(--line);border-radius:9px;background:var(--surface-2);font-size:13px">
              <svg viewBox="0 0 24 24" fill="none" stroke="var(--steel-deep)" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" style="width:15px;height:15px;flex:none;margin-top:2px"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8ZM14 2v6h6"></path></svg>
              <span>${esc(x)}</span></div>`).join('')}
          </div></div>` : ''}
        ${flags.length ? `<div style="margin-top:18px">${notice('bad', 'Check before sending',
          `<ul>${flags.map(x => `<li>${esc(x)}</li>`).join('')}</ul>`)}</div>` : ''}
        <div style="margin-top:18px;padding:12px 14px;border-radius:9px;background:var(--surface-2);border:1px solid var(--line);font:400 11px var(--font-mono);letter-spacing:.04em;color:var(--ink-3);line-height:1.6">
          Drafted by ${esc(d.model_used || 'model')}${d.created_at ? ' · ' + esc(d.created_at.slice(0, 16).replace('T', ' ')) : ''}
        </div>
      </div>
    </section>`;
}

views.data = async function () {
  const [status, log, fx] = await Promise.all([get('/data-status'), get('/ingest/log?limit=20'), get('/fx/rates')]);

  const logRows = log.map(l => `<tr>
    <td style="font-size:13px">${esc(l.file)}</td>
    <td><span class="badge neutral">${esc(l.kind)}</span></td>
    <td style="font-size:13px;color:var(--ink-2)">${day(l.as_of)}</td>
    <td class="num">${l.rows_out}</td>
    <td>${l.warnings.length ? `<span class="badge bad">${l.warnings.length}</span>` : '<span class="badge ok">clean</span>'}</td>
    <td class="muted" style="font-size:12px;max-width:460px">${l.warnings.slice(0, 3).map(esc).join('<br>')}</td></tr>`);

  const fxRows = fx.slice(0, 24).map(r => `<tr>
    <td style="font-size:13px">${day(r.date)}</td>
    <td style="font:500 12px var(--font-mono)">${esc(r.currency)}</td>
    <td class="num">${fN(r.rate_to_egp, 4)}</td>
    <td><span class="badge neutral">${esc(r.source)}</span></td></tr>`);

  page.innerHTML = `
    <div class="intro">
      <p>What is loaded, how fresh it is, and where it came from. Worth checking before trusting any
         figure — a projection built on a three-week-old cheque list is exactly that.</p>
      <button class="btn primary" onclick="reloadAll()">Reload workbooks</button>
    </div>
    ${notice(status.snapshot_spread_days > 3 ? 'warn' : 'info', '', esc(status.note))}
    <div class="tiles-4">
      <div class="tile"><div class="k">Bank balances</div><div class="v">${status.bank_balances.days_loaded}</div><div class="n">days · latest ${day(status.bank_balances.latest)}</div></div>
      <div class="tile gold"><div class="k">Open cheques</div><div class="v">${status.checks.open}</div><div class="n">as at ${day(status.checks.snapshot)}</div></div>
      <div class="tile ok"><div class="k">Receivables</div><div class="v">${status.receivables.rows}</div><div class="n">as at ${day(status.receivables.snapshot)}</div></div>
      <div class="tile grey"><div class="k">Business plan lines</div><div class="v">${status.business_plan.lines}</div><div class="n">${status.fx_rates} FX rates on file</div></div>
    </div>
    ${card('Ingest log', 'Warnings are kept — they are how a bad source row is caught',
      table('<th>File</th><th>Kind</th><th>As at</th><th class="num">Rows</th><th>Status</th><th>Warnings</th>', logRows))}
    <div style="height:18px"></div>
    ${card('Exchange rates', 'Conversions use the latest rate on or before the value date',
      table('<th>Date</th><th>Currency</th><th class="num">To EGP</th><th>Source</th>', fxRows))}`;
};


/* ------------------------------------------- commitments & funding views */

function emptyState(title, body, hints) {
  return `<div class="empty-state">
    <h3>${esc(title)}</h3><p>${body}</p>
    <div class="how">${(hints || []).map(h => `<code>${esc(h)}</code>`).join('')}</div>
  </div>`;
}

views.funding = async function () {
  const f = await get(`/funding/position?horizon_days=${state.horizon}`);
  stampHeader(f.as_of, true);

  const rungs = f.ladder.map(r => `<div class="rung ${r.kind}${r.kind === 'total' && f.tight ? ' tight' : ''}">
    <span class="name">${esc(r.rung)}</span>
    <span class="note">${esc(r.note || '')}</span>
    <span class="amt">${r.kind === 'out' ? '(' + f0(r.amount_egp) + ')' : f0(r.amount_egp)}</span>
  </div>`);

  const excluded = (f.excluded || []).map(e => `<tr>
    <td style="font-size:13px">${esc(e.item)}</td>
    <td class="num">${f0(e.amount_egp)}</td>
    <td class="muted" style="font-size:12px">${esc(e.effect)}</td></tr>`);

  page.innerHTML = `
    <div class="intro">
      <p>Everything that is or becomes money, as a ladder from cash outward. The rungs are
         not interchangeable — cash at bank and a government receivable are both money, but
         only one of them pays a supplier this week.</p>
      ${seg([{id:'30',label:'30 days'},{id:'60',label:'60 days'},{id:'90',label:'90 days'}], String(state.horizon), 'setHorizon')}
    </div>
    ${noticeList(f.gaps, 'bad', 'Parts of the position could not be computed')}
    <div class="tiles-4">
      <div class="tile"><div class="k">Spendable today</div><div class="v">${f0(f.spendable_today_egp)}</div><div class="n">after cheques and restricted margins</div></div>
      <div class="tile ok"><div class="k">Contracted in · ${state.horizon}d</div><div class="v">${f0(f.contracted_in_egp)}</div><div class="n">collections and advances due</div></div>
      <div class="tile gold"><div class="k">Committed out · ${state.horizon}d</div><div class="v">${f0(f.committed_out_egp)}</div><div class="n">POs, tax and commission</div></div>
      <div class="tile ${f.tight ? 'alert' : 'ok'}"><div class="k">Position at ${state.horizon}d</div>
        <div class="v">${f0(f.projected_egp)}</div><div class="n plain">${f.tight ? 'negative — funding needed' : 'positive'}</div></div>
    </div>
    ${card('Funding ladder', esc(f.as_of) + ' to ' + esc(f.horizon_end) + ' · EGP',
      `<div class="ladder">${rungs.join('')}</div>`)}
    <div style="height:18px"></div>
    ${card('Deliberately not counted', 'each of these would move the position — none is a rounding difference',
      table('<th>Item</th><th class="num">EGP</th><th>Why it is out</th>', excluded,
            'Nothing excluded — every input is dated and inside the horizon.'))}`;
};

views.tf = async function () {
  const [sum, fac] = await Promise.all([get('/trade-finance/summary'), get('/trade-finance/facilities')]);
  stampHeader(sum.as_of, true);

  if (fac.empty) {
    page.innerHTML = `<div class="intro"><p>Letters of guarantee and letters of credit, on both
      the client and the supplier side. Cash margins on issued facilities are deducted from
      net available cash; commissions feed the cash forecast; expiries are tracked so a margin
      is released or renewed deliberately rather than by surprise.</p></div>
      ${emptyState('No facilities registered yet',
        'This register is empty because no L/G or L/C data is in the source workbooks. Add facilities one at a time, or bulk-load a CSV — the template lists the columns.',
        ['POST /trade-finance/facilities', 'GET /commitments/template/trade-finance', 'POST /commitments/import/trade-finance'])}`;
    return;
  }

  const rows = fac.facilities.map(r => `<tr>
    <td>${nameCell(r.counterparty, r.reference)}
      <div style="margin-top:3px"><span class="badge ${r.instrument === 'LG' ? 'delivered' : 'onhand'}">${esc(r.instrument)}</span>
      <span class="badge neutral">${esc(r.direction)}</span>
      ${r.purpose ? `<span class="badge neutral">${esc(r.purpose)}</span>` : ''}</div></td>
    <td style="font-size:13px;color:var(--ink-2)">${esc(r.bank)}</td>
    <td class="num">${esc(r.currency)} ${f0(r.face_value)}</td>
    <td class="num">${r.face_value_egp === null ? '<span class="badge bad">no rate</span>' : f0(r.face_value_egp)}</td>
    <td class="num ${r.margin_egp ? '' : 'zero'}">${r.margin_egp ? f0(r.margin_egp) : '—'}
      ${r.margin_egp && !r.margin_in_bank_balance ? ' <span class="badge bad" title="not deducted from cash">off-book</span>' : ''}</td>
    <td class="num ${r.expired ? 'neg' : r.expiring_soon ? 'warnv' : ''}">${day(r.expiry_date)}
      ${r.days_to_expiry !== null ? `<div style="font:500 10px var(--font-mono)">${r.days_to_expiry}d</div>` : ''}</td>
    <td><span class="badge ${r.status === 'active' ? 'ok' : 'neutral'}">${esc(r.status)}</span></td></tr>`);

  const rc = sum.restricted_cash || {};
  page.innerHTML = `
    <div class="intro">
      <p>Letters of guarantee and credit, both sides. A cash margin on an issued facility is
         money in the bank the bank will not release, so it is deducted from net available cash.</p>
      ${seg([{id:'all',label:'All'},{id:'LG',label:'L/G'},{id:'LC',label:'L/C'}], state.tfKind || 'all', 'setTfKind')}
    </div>
    ${sum.expired_still_active.length ? notice('bad', sum.expired_still_active.length + ' facility(ies) past expiry but still active',
      'Either the margin should have been released or the facility renewed. Neither happens by itself.') : ''}
    ${sum.expiring_soon.length ? notice('warn', sum.expiring_soon.length + ' expiring within 60 days',
      sum.expiring_soon.slice(0,5).map(f => esc(f.counterparty) + ' · ' + day(f.expiry_date)).join(' · ')) : ''}
    ${rc.note ? notice('warn', 'Margins not deducted from the cash position', esc(rc.note)) : ''}

    <div class="tiles-4">
      <div class="tile"><div class="k">L/G issued</div><div class="v">${f0(sum.lg.issued_face_egp)}</div><div class="n">to clients · face value</div></div>
      <div class="tile gold"><div class="k">L/C opened</div><div class="v">${f0(sum.lc.import_face_egp)}</div><div class="n">import · to suppliers</div></div>
      <div class="tile alert"><div class="k">Restricted cash</div><div class="v">${f0(rc.deducted_from_cash_egp)}</div>
        <div class="n plain">of ${f0(rc.total_margin_egp)} total margin</div></div>
      <div class="tile grey"><div class="k">Commission · 90d</div><div class="v">${f0(sum.commission_90d_egp)}</div><div class="n">in the cash forecast</div></div>
    </div>
    ${card('Facility register', fac.count + ' facilities · ' + esc(fac.as_of),
      table(`<th>Counterparty</th><th>Bank</th><th class="num">Face</th><th class="num">Face EGP</th>
             <th class="num">Margin EGP</th><th class="num">Expiry</th><th>Status</th>`, rows))}`;
};

views.advances = async function () {
  const d = await get('/down-payments');
  stampHeader(d.as_of, true);

  if (d.empty) {
    page.innerHTML = `<div class="intro"><p>Advances received from clients and paid to suppliers.
      A received advance is cash today but unwinds against future progress invoices, so it is
      tracked with its recovery — otherwise the forecast counts it twice.</p></div>
      ${emptyState('No down payments recorded yet',
        'Add advances as they are agreed. Link each received advance to its advance-payment guarantee so an unsecured advance is visible.',
        ['POST /down-payments', 'GET /commitments/template/down-payments', 'POST /commitments/import/down-payments'])}`;
    return;
  }

  const rows = d.down_payments.map(r => `<tr>
    <td>${nameCell(r.counterparty, r.project || r.reference)}
      <div style="margin-top:3px"><span class="badge ${r.direction === 'received' ? 'ok' : 'onhand'}">${esc(r.direction)}</span>
      ${r.unsecured ? '<span class="badge bad">unsecured</span>' : r.guarantee_ref ? `<span class="badge delivered">L/G ${esc(r.guarantee_ref)}</span>` : ''}</div></td>
    <td class="num">${esc(r.currency)} ${f0(r.amount)}</td>
    <td class="num">${f0(r.amount_egp)}</td>
    <td class="num">${r.pct_of_contract ? pct(r.pct_of_contract, 0) : '—'}</td>
    <td class="num">${day(r.expected_date)}</td>
    <td class="num">${r.outstanding_egp ? f0(r.outstanding_egp) : '<span class="zero">recovered</span>'}</td>
    <td><span class="badge ${r.status === 'received' ? 'ok' : r.status === 'recovered' ? 'neutral' : 'onhand'}">${esc(r.status)}</span></td></tr>`);

  page.innerHTML = `
    <div class="intro"><p>${esc(d.note)}</p></div>
    ${d.unsecured.length ? notice('bad', d.unsecured.length + ' advance(s) received with no guarantee linked',
      'An advance received without an advance-payment guarantee is an exposure if the contract terminates.') : ''}
    <div class="tiles-4">
      <div class="tile ok"><div class="k">Received from clients</div><div class="v">${f0(d.received.total_egp)}</div><div class="n">${d.received.count} advances</div></div>
      <div class="tile gold"><div class="k">Still to recover</div><div class="v">${f0(d.received.outstanding_egp)}</div><div class="n">unwinds against progress invoices</div></div>
      <div class="tile"><div class="k">Expected inflow</div><div class="v">${f0(d.expected_inflow_egp)}</div><div class="n">agreed, not yet received</div></div>
      <div class="tile grey"><div class="k">Paid to suppliers</div><div class="v">${f0(d.paid.total_egp)}</div><div class="n">${d.paid.count} advances</div></div>
    </div>
    ${card('Down payment register', esc(d.as_of),
      table(`<th>Counterparty</th><th class="num">Amount</th><th class="num">EGP</th><th class="num">% contract</th>
             <th class="num">Expected</th><th class="num">Outstanding</th><th>Status</th>`, rows))}`;
};

views.tax = async function () {
  const [ob, cal] = await Promise.all([get('/tax/obligations'), get('/tax/calendar')]);
  stampHeader(ob.as_of, true);

  const rows = ob.obligations.map(t => `<tr class="${t.overdue ? 'shortage' : ''}">
    <td><div style="font-weight:500;font-size:13.5px">${esc(t.label)}</div>
      <div class="en">${esc(t.description || t.period || '')}</div></td>
    <td class="num">${f0(t.outstanding_egp)}</td>
    <td class="num">${t.due_date ? day(t.due_date) : '<span class="badge bad">unscheduled</span>'}</td>
    <td class="num ${t.overdue ? 'neg' : ''}">${t.overdue ? t.overdue_days + 'd late' : t.days_to_due !== null ? t.days_to_due + 'd' : '—'}</td>
    <td><span class="badge ${t.status === 'paid' ? 'ok' : t.status === 'unscheduled' ? 'bad' : 'neutral'}">${esc(t.status)}</span></td>
    <td>${!t.due_date ? `<button class="btn tiny" onclick="scheduleTax(${t.id},'${esc(t.label)}')">Set due date</button>` : ''}</td></tr>`);

  const byType = ob.by_type.map(t => `<tr>
    <td style="font-size:13px">${esc(t.label)}</td>
    <td class="num">${t.count}</td>
    <td class="num">${f0(t.outstanding_egp)}</td>
    <td><div class="bar thin"><span style="width:${ob.total_outstanding_egp ? (t.outstanding_egp / ob.total_outstanding_egp * 100).toFixed(0) : 0}%;background:var(--bad)"></span></div></td></tr>`);

  const calRows = cal.calendar.map(c => `<tr>
    <td style="font-size:13px">${esc(c.label)}</td>
    <td><span class="badge neutral">${esc(c.cadence)}</span></td>
    <td class="muted" style="font-size:12px">${esc(c.note || '')}</td></tr>`);

  page.innerHTML = `
    <div class="intro">
      <p>Tax and governmental dues with the date each must be settled. An obligation with no
         due date cannot enter the cash forecast, so it is reported as a gap rather than ignored.</p>
      <button class="btn" onclick="reloadDues()">Reload from Business Plan</button>
    </div>
    ${ob.note ? notice('bad', 'Unscheduled dues are not in the cash forecast', esc(ob.note)) : ''}
    ${ob.overdue_egp ? notice('bad', f0(ob.overdue_egp) + ' EGP already past due', 'Overdue amounts are placed on day one of the projection.') : ''}
    <div class="tiles-4">
      <div class="tile alert"><div class="k">Total outstanding</div><div class="v">${f0(ob.total_outstanding_egp)}</div><div class="n">tax and governmental dues</div></div>
      <div class="tile gold"><div class="k">Unscheduled</div><div class="v">${f0(ob.unscheduled_egp)}</div><div class="n">no settlement date set</div></div>
      <div class="tile ${ob.overdue_egp ? 'alert' : 'ok'}"><div class="k">Overdue</div><div class="v">${f0(ob.overdue_egp)}</div><div class="n">past the due date</div></div>
      <div class="tile grey"><div class="k">Obligations</div><div class="v">${ob.obligations.length}</div><div class="n">on the register</div></div>
    </div>
    <div class="grid g-16">
      ${card('Obligation register', esc(ob.as_of),
        table('<th>Tax</th><th class="num">Outstanding EGP</th><th class="num">Due</th><th class="num">Ageing</th><th>Status</th><th></th>',
              rows, 'No obligations. Press "Reload from Business Plan" to load the governmental dues.'))}
      <div class="stack">
        ${card('By type', '', table('<th>Type</th><th class="num">n</th><th class="num">EGP</th><th>Share</th>', byType))}
        ${card('Egyptian filing calendar', 'cadence per tax type',
          table('<th>Tax</th><th>Cadence</th><th>Note</th>', calRows) +
          `<div class="card-foot">${esc(cal.warning)}</div>`)}
      </div>
    </div>`;
};

views.proc = async function () {
  const [sum, pr, po] = await Promise.all([
    get('/procurement/summary'), get('/procurement/requisitions'), get('/procurement/orders'),
  ]);

  if (sum.empty) {
    page.innerHTML = `<div class="intro"><p>Purchase requisitions and orders. An approved PO is
      committed spend that has not reached payables yet — for a fabricator buying steel months
      ahead, that gap is most of the working capital, and a payables-driven forecast cannot see it.</p></div>
      ${emptyState('No requisitions or orders yet',
        'Raise a PR, approve it, convert it to a PO — or bulk-load both registers from CSV. Once orders exist, their uninvoiced balance enters the cash forecast automatically.',
        ['POST /procurement/requisitions', 'POST /procurement/orders', 'POST /commitments/import/orders'])}`;
    return;
  }

  const prRows = pr.requisitions.map(r => `<tr>
    <td><div style="font:500 12.5px var(--font-mono)">${esc(r.pr_number)}</div>
      <div class="en">${esc(r.description || '').slice(0, 60)}</div></td>
    <td style="font-size:13px">${esc(r.project || '—')}</td>
    <td><span class="badge neutral">${esc(r.category || '—')}</span></td>
    <td class="num">${f0(r.estimated_value_egp)}</td>
    <td class="num">${day(r.required_by)}</td>
    <td><span class="badge ${r.status === 'approved' ? 'ok' : r.status === 'rejected' ? 'bad' : 'onhand'}">${esc(r.status)}</span>
      ${r.age_days > 14 && ['draft','pending'].includes(r.status) ? `<div style="font:500 10px var(--font-mono);color:var(--bad)">${r.age_days}d old</div>` : ''}</td></tr>`);

  const poRows = po.orders.map(o => `<tr class="${o.overdue_delivery ? 'shortage' : ''}">
    <td><div style="font:500 12.5px var(--font-mono)">${esc(o.po_number)}</div>
      ${nameCell(o.supplier, o.project)}</td>
    <td><span class="badge neutral">${esc(o.category || '—')}</span></td>
    <td class="num">${esc(o.currency)} ${f0(o.order_value)}</td>
    <td class="num">${o.rate_missing ? '<span class="badge bad">no rate</span>' : f0(o.order_value_egp)}</td>
    <td class="num warnv">${f0(o.uninvoiced_egp)}</td>
    <td class="num">${day(o.delivery_date)}</td>
    <td><span class="badge ${o.status === 'closed' ? 'ok' : 'neutral'}">${esc(o.status)}</span></td></tr>`);

  page.innerHTML = `
    <div class="intro"><p>${esc(po.note)}</p></div>
    ${po.overdue_delivery.length ? notice('warn', po.overdue_delivery.length + ' order(s) past their delivery date',
      'Late delivery moves the payment date too — the forecast dates these from delivery plus terms.') : ''}
    ${sum.requisitions.pending_approval.count ? notice('warn',
      sum.requisitions.pending_approval.count + ' requisition(s) awaiting approval',
      f0(sum.requisitions.pending_approval.value_egp) + ' EGP, oldest ' + sum.requisitions.pending_approval.oldest_days + ' days.') : ''}
    <div class="tiles-4">
      <div class="tile"><div class="k">Open order value</div><div class="v">${f0(sum.orders.open_value_egp)}</div><div class="n">${sum.orders.count} orders</div></div>
      <div class="tile gold"><div class="k">Committed, uninvoiced</div><div class="v">${f0(sum.orders.committed_uninvoiced_egp)}</div><div class="n">payables cannot see this yet</div></div>
      <div class="tile grey"><div class="k">Invoiced, unpaid</div><div class="v">${f0(sum.orders.invoiced_unpaid_egp)}</div><div class="n">in the payables pipeline</div></div>
      <div class="tile alert"><div class="k">Cash out · 90d</div><div class="v">${f0(sum.cash_out_90d_egp)}</div><div class="n plain">on PO terms, in the forecast</div></div>
    </div>
    ${card('Purchase orders', po.count + ' orders · ' + esc(po.as_of),
      table(`<th>PO / supplier</th><th>Category</th><th class="num">Value</th><th class="num">EGP</th>
             <th class="num">Uninvoiced</th><th class="num">Delivery</th><th>Status</th>`, poRows, 'No orders yet.'))}
    <div style="height:18px"></div>
    ${card('Purchase requisitions', pr.count + ' requisitions',
      table('<th>PR</th><th>Project</th><th>Category</th><th class="num">Est. EGP</th><th class="num">Required by</th><th>Status</th>',
            prRows, 'No requisitions yet.'))}`;
};

views.margin = async function () {
  const [co, proj, cats] = await Promise.all([
    get('/margin/company'), get('/margin/projects'), get('/margin/categories'),
  ]);

  const coRows = co.periods.map(p => `<tr>
    <td>${esc(p.period)} <span class="tag ${p.scenario === 'actual' ? 'a' : 'p'}">${p.scenario === 'actual' ? 'A' : 'P'}</span></td>
    <td class="num">${f0(p.revenue_kegp)}</td>
    <td class="num">${f0(p.variable_cost_kegp)}</td>
    <td class="num" style="font-weight:600;color:var(--figure)">${f0(p.contribution_margin_kegp)}</td>
    <td class="num ${p.cm_pct < 0.2 ? 'warnv' : ''}">${pct(p.cm_pct)}</td>
    <td class="num">${f0(p.fixed_cost_kegp)}</td>
    <td class="num">${f0(p.break_even_revenue_kegp)}</td>
    <td class="num ${p.margin_of_safety !== null && p.margin_of_safety < 0.15 ? 'neg' : ''}">${pct(p.margin_of_safety)}</td></tr>`);

  const latest = co.periods[co.periods.length - 1];
  let bar = '';
  if (latest && latest.revenue_kegp) {
    const v = latest.variable_cost_kegp / latest.revenue_kegp * 100;
    const fx_ = latest.fixed_cost_kegp / latest.revenue_kegp * 100;
    const pf = Math.max(0, 100 - v - fx_);
    bar = `<div class="cm-bar">
      <span class="v" style="width:${v.toFixed(1)}%">${v >= 8 ? 'variable ' + v.toFixed(0) + '%' : ''}</span>
      <span class="f" style="width:${fx_.toFixed(1)}%">${fx_ >= 8 ? 'fixed ' + fx_.toFixed(0) + '%' : ''}</span>
      <span class="p" style="width:${pf.toFixed(1)}%">${pf >= 8 ? 'profit ' + pf.toFixed(0) + '%' : ''}</span></div>
      <div class="stamp" style="margin-top:8px">${esc(latest.period)} revenue of ${f0(latest.revenue_kegp)} KEGP, split by cost behaviour</div>`;
  }

  const catRows = cats.map(c => `<tr>
    <td style="font-size:13px">${esc(c.name)}</td>
    <td><span class="badge ${c.behaviour === 'variable' ? 'delivered' : c.behaviour === 'fixed' ? 'neutral' : 'onhand'}">${esc(c.behaviour)}</span></td>
    <td class="num"><button class="f-date" onclick="editBehaviour('${esc(c.name)}','${esc(c.behaviour)}',${c.variable_portion})">${pct(c.variable_portion, 0)}</button></td>
    <td class="muted" style="font-size:12px">${esc(c.note || '')}</td></tr>`);

  const projRows = proj.projects.slice(0, 20).map(p => `<tr>
    <td>${nameCell(p.project, p.segment)}</td>
    <td class="num">${f0(p.revenue)}</td>
    <td class="num">${f0(p.variable_cost)}</td>
    <td class="num">${p.contribution_margin === null ? '<span class="badge bad">not computable</span>' : f0(p.contribution_margin)}</td>
    <td class="num">${pct(p.cm_pct)}</td>
    <td class="muted" style="font-size:12px">${esc(p.basis)}</td></tr>`);

  page.innerHTML = `
    <div class="intro">
      <p>Contribution margin is revenue less the costs that actually move with the work.
         It is not gross profit: a job with a thin gross margin can still be worth taking if its
         contribution covers overhead that is being paid anyway.</p>
    </div>
    ${notice('info', 'How the split is made', esc(co.basis))}
    ${latest ? `<div class="tiles-4">
      <div class="tile"><div class="k">${esc(latest.period)} contribution margin</div><div class="v">${f0(latest.contribution_margin_kegp)}</div><div class="n">KEGP</div></div>
      <div class="tile ok"><div class="k">CM %</div><div class="v">${pct(latest.cm_pct)}</div><div class="n">of revenue</div></div>
      <div class="tile gold"><div class="k">Break-even revenue</div><div class="v">${f0(latest.break_even_revenue_kegp)}</div><div class="n">KEGP to cover fixed cost</div></div>
      <div class="tile ${latest.margin_of_safety !== null && latest.margin_of_safety < 0.15 ? 'alert' : 'grey'}">
        <div class="k">Margin of safety</div><div class="v">${pct(latest.margin_of_safety)}</div>
        <div class="n plain">revenue could fall this far before a loss</div></div>
    </div>` : ''}
    ${bar ? `<section class="card pad" style="margin-bottom:18px"><h2>Cost structure</h2>
      <div class="stamp" style="margin:4px 0 16px">Where each pound of revenue goes</div>${bar}</section>` : ''}
    ${card('Company contribution margin', 'KEGP · from the P&L',
      table(`<th>Period</th><th class="num">Revenue</th><th class="num">Variable</th><th class="num">CM</th>
             <th class="num">CM %</th><th class="num">Fixed</th><th class="num">Break-even</th><th class="num">Safety</th>`,
            coRows, 'No P&L loaded.'))}
    <div style="height:18px"></div>
    <div class="grid g-16">
      ${card('By project', proj.computable_projects + ' computable of ' + proj.projects.length,
        (proj.note ? notice('warn', 'Project margin needs categorised costs', esc(proj.note)) : '') +
        table('<th>Project</th><th class="num">Revenue</th><th class="num">Variable</th><th class="num">CM</th><th class="num">CM %</th><th>Basis</th>',
              projRows, 'No projects with revenue.'))}
      ${card('Cost behaviour', 'click a share to reclassify — this is a management judgement',
        table('<th>Category</th><th>Behaviour</th><th class="num">Variable share</th><th>Note</th>', catRows))}
    </div>`;
};

/* ---------------------------------------------------------------- actions */

const WINDOW = 7;
function pickPeriods(all) {
  if (!all || all.length <= WINDOW) return all || [];
  const year = String(new Date().getFullYear());
  let i = all.findIndex(p => p.includes(year));
  if (i < 0) i = all.length - 1;
  const start = Math.max(0, Math.min(i - WINDOW + 3, all.length - WINDOW));
  return all.slice(start, start + WINDOW);
}

function stampHeader(asOf, showFx) {
  if (asOf) document.getElementById('asof').textContent = dayLong(asOf);
  if (!showFx) return;
  // The rates in force on the as-of date — /fx/rates would hand back the
  // business plan's 2035 planning rate as the "newest" row.
  get('/fx/current' + (asOf ? '?as_of=' + asOf : '')).then(fx => {
    const parts = ['USD', 'EUR', 'AED']
      .filter(c => fx.rates[c] !== undefined)
      .map(c => c + ' ' + fx.rates[c].toFixed(2));
    fx.missing.forEach(c => parts.push(c + ' —'));
    document.getElementById('fxpill').textContent = parts.length ? parts.join(' · ') : 'no rates';
  });
}

async function clearCheque(id) {
  if (!confirm('Mark this cheque as presented and cleared? It drops out of the cash position.')) return;
  await send(`/cash/checks/${id}/clear`, 'POST', {});
  render();
}

async function recompute() {
  const r = await send('/receivables/forecast/compute', 'POST', {});
  alert(`Recomputed ${r.computed} forecasts. ${r.preserved_overrides} manual override(s) preserved.`);
  render();
}

function editForecast(item) {
  const dlg = document.getElementById('dlg');
  document.getElementById('dlg-title').textContent = 'Adjust collection forecast';
  document.getElementById('dlg-body').innerHTML = `
    <div class="row"><label>Customer</label>
      <div class="ar" style="text-align:right">${esc(item.customer)}</div>
      <div class="en">${f0(item.amount_egp)} EGP · ${esc(item.category || '')}</div></div>
    <div class="row"><label>Model expects</label>
      <div style="font:500 13px var(--font-mono)">${dayLong(item.computed_date)}</div>
      <div class="basis" style="max-width:none">${esc(item.basis || '')}</div></div>
    <div class="row"><label>Expected collection date</label>
      <input type="date" id="ov-date" value="${item.expected_date}"></div>
    <div class="row"><label>Confidence</label>
      <select id="ov-prob">${[0.95, 0.85, 0.7, 0.5, 0.25].map(p =>
        `<option value="${p}" ${Math.abs(p - item.probability) < 0.03 ? 'selected' : ''}>${(p * 100).toFixed(0)} %</option>`).join('')}</select></div>
    <div class="row"><label>Your name</label><input id="ov-user" value="${esc(item.override_by || '')}" placeholder="Who is making the change"></div>
    <div class="row"><label>Why</label><textarea id="ov-note" rows="2" placeholder="Evidence for the change">${esc(item.override_note || '')}</textarea></div>`;
  document.getElementById('dlg-save').onclick = async () => {
    await send(`/receivables/forecast/${item.receivable_id}/override`, 'PUT', {
      expected_date: document.getElementById('ov-date').value,
      probability: parseFloat(document.getElementById('ov-prob').value),
      user: document.getElementById('ov-user').value,
      note: document.getElementById('ov-note').value,
    });
    dlg.close(); render();
  };
  dlg.showModal();
}

async function clearOverride(id) {
  await send(`/receivables/forecast/${id}/override`, 'DELETE');
  render();
}

function editPolicy(segment, bucket, rate) {
  const dlg = document.getElementById('dlg');
  document.getElementById('dlg-title').textContent = 'Set loss rate';
  document.getElementById('dlg-body').innerHTML = `
    <div class="row"><label>Segment / bucket</label><div><strong>${esc(segment)}</strong> · ${esc(bucket)}</div></div>
    <div class="row"><label>Loss rate (0–1)</label><input type="number" id="p-rate" step="0.005" min="0" max="1" value="${rate}"></div>
    <div class="row"><label>Basis for this rate</label><textarea id="p-note" rows="2" placeholder="e.g. 3-year write-off history for this segment"></textarea></div>`;
  document.getElementById('dlg-save').onclick = async () => {
    await send('/ecl/policy', 'PUT', {
      segment, bucket,
      loss_rate: parseFloat(document.getElementById('p-rate').value),
      note: document.getElementById('p-note').value,
    });
    dlg.close(); render();
  };
  dlg.showModal();
}

async function commitEcl() {
  const who = prompt('Commit this ECL run to the monthly series. Your name:');
  if (who === null) return;
  const r = await send('/ecl/run', 'POST', {user: who});
  alert(`Run ${r.run_id} committed. ECL ${f0(r.total_ecl_egp)} EGP at ${pct(r.coverage_ratio)} coverage.`);
  render();
}

async function showStatement(which) {
  const el = document.getElementById('stmt');
  el.innerHTML = '<div class="loading">Loading…</div>';
  const d = await get(`/reporting/statement?which=${which}&period_type=${state.period}&scope=${state.scope}`);
  const cols = pickPeriods(d.periods);
  const rows = d.lines.map(l => `<tr>
    <td style="font-size:13px;color:var(--ink-2)">${esc(l.line_item)}</td>
    ${cols.map(p => {
      const c = l.periods[p];
      return c ? `<td class="num" style="color:${c.value_egp < 0 ? 'var(--ink-3)' : 'var(--ink)'}">${fM(c.value_egp)} <span class="tag ${c.scenario === 'actual' ? 'a' : 'p'}">${c.scenario === 'actual' ? 'A' : 'P'}</span></td>`
               : '<td class="num zero">—</td>';
    }).join('')}</tr>`);
  el.innerHTML = card({IS:'Income statement', BS:'Balance sheet', CF:'Cash flow'}[which], 'EGP · ' + state.scope,
    table(`<th>Line item</th>${cols.map(p => `<th class="num">${esc(p)}</th>`).join('')}`, rows));
}

async function showVariance() {
  const periods = await get(`/reporting/periods?period_type=${state.period}`);
  const p = prompt('Period to compare (' + periods.join(', ') + ')', periods[periods.length - 1]);
  if (!p) return;
  const el = document.getElementById('stmt');
  el.innerHTML = '<div class="loading">Loading…</div>';
  const d = await get(`/reporting/variance?period=${encodeURIComponent(p)}&statement=IS`);
  const rows = d.lines.map(l => `<tr>
    <td style="font-size:13px;color:var(--ink-2)">${esc(l.line_item)}</td>
    <td class="num">${l.actual_egp === null ? '<span class="zero">—</span>' : fM(l.actual_egp)}</td>
    <td class="num muted">${l.budget_egp === null ? '—' : fM(l.budget_egp)}</td>
    <td class="num ${l.variance_egp === null ? '' : l.variance_egp < 0 ? 'neg' : 'pos'}">${l.variance_egp === null ? '<span class="zero">—</span>' : fM(l.variance_egp)}</td>
    <td class="num ${l.variance_pct === null ? '' : l.variance_pct < 0 ? 'neg' : 'pos'}">${l.variance_pct === null ? '<span class="zero">—</span>' : pct(l.variance_pct)}</td></tr>`);
  el.innerHTML = (d.note ? notice('warn', 'No comparison available', esc(d.note)) : '') +
    card('Actual vs budget — ' + p, 'EGP',
      table('<th>Line item</th><th class="num">Actual</th><th class="num">Budget</th><th class="num">Variance</th><th class="num">%</th>', rows));
}

async function draftAudit() {
  const out = document.getElementById('draft-out');
  const text = document.getElementById('q-text').value.trim();
  if (!text) { alert('Paste the auditor query first.'); return; }
  out.innerHTML = '<section class="card pad"><div class="loading">Drafting — this takes a minute on a substantive query…</div></section>';
  try {
    const d = await send('/audit/queries/draft', 'POST', {
      query_text: text,
      reference: document.getElementById('q-ref').value,
      area_hint: document.getElementById('q-area').value,
    });
    d.risk_flags = (d.risk_flags || [])
      .concat((d.missing_references || []).map(m => 'Reference not held: ' + m))
      .concat((d.data_required || []).map(m => 'Figure needed: ' + m));
    out.innerHTML = renderDraft(d);
  } catch (e) {
    out.innerHTML = `<section class="card pad">${notice('bad', 'Could not draft', esc(e.detail || e.message || JSON.stringify(e)))}</section>`;
  }
}

async function seedKnowledge() {
  const r = await send('/audit/knowledge/seed', 'POST');
  alert(`${r.added} references added (${r.total} total).\n\n${r.warning}`);
  render();
}

async function reloadAll() {
  const path = prompt('Folder holding the workbooks:',
    '/Users/kareemelsenosy/Documents/Qalaa Holdings/ARESCO/Finance');
  if (!path) return;
  page.innerHTML = '<div class="loading">Loading workbooks…</div>';
  const r = await send('/ingest/load-directory?path=' + encodeURIComponent(path), 'POST');
  alert(r.loaded.map(l => `${l.file}: ${l.error || 'ok'}`).join('\n'));
  render();
}

/* ----------------------------------------------------------------- router */

async function render() {
  const id = (location.hash || '#cash').slice(1);
  const nav = NAV.find(n => n.id === id) || NAV[0];
  renderNav(nav.id);
  document.getElementById('eyebrow').textContent = nav.eyebrow;
  document.getElementById('title').textContent = nav.title;
  page.innerHTML = '<div class="loading">Loading…</div>';
  try {
    await (views[nav.id] || views.cash)();
  } catch (e) {
    page.innerHTML = notice('bad', 'Could not load this view',
      esc(e.detail || e.message || JSON.stringify(e)));
  }
}

document.getElementById('theme-btn').addEventListener('click', toggleTheme);
window.addEventListener('hashchange', render);

try {
  const saved = localStorage.getItem('aresco-theme');
  if (saved) { document.getElementById('shell').dataset.theme = saved; document.documentElement.dataset.theme = saved; }
} catch (e) { /* private mode */ }

render();

/* ---------- signed-in user + sign out ---------- */
(async function session() {
  try {
    const me = await (await fetch("/auth/me")).json();
    const ini = document.getElementById("me-initials");
    const nm = document.getElementById("me-name");
    if (ini) ini.textContent = me.initials || "·";
    if (nm) nm.textContent = me.name || me.email;
  } catch (_) { /* the gate redirects if the session is gone */ }

  const btn = document.getElementById("signout-btn");
  if (btn) btn.onclick = async () => {
    await fetch("/auth/logout", { method: "POST" });
    window.location.href = "/app/login.html";
  };
})();


/* ------------------------------------------------------------ data intake */

const PRIORITY_BADGE = {
  highest: 'bad', core: 'gold', high: 'warn', medium: 'neutral', setup: 'neutral',
};
const STATE_LABEL = {
  never:   ['bad',     'Never received'],
  overdue: ['warn',    'Overdue'],
  current: ['ok',      'Up to date'],
  received:['ok',      'Received'],
  in_tool: ['neutral', 'Done in the tool'],
};

views.intake = async function () {
  const data = await get('/intake/requirements');
  intakeState.reqs = {};
  data.teams.forEach(t => t.items.forEach(i => { intakeState.reqs[i.id] = i; }));

  const c = data.counts;
  const sections = data.teams.map(t => `
    <div class="intake-team">
      <div class="intake-team-head"><span>${esc(t.team)}</span><span class="muted">${t.items.length} item${t.items.length > 1 ? 's' : ''}</span></div>
      ${t.items.map(intakeCard).join('')}
    </div>`).join('');

  page.innerHTML = `
    <div class="intro">
      <p>Everything the tool needs, who owns it, and how often. Each row takes the file
         in whatever shape your system exports it — column names do not have to match
         ours${data.ai_available ? '' : ' <b>(column mapping needs ANTHROPIC_API_KEY, which is not set)</b>'}.</p>
    </div>
    <div class="tiles-4">
      <div class="tile"><div class="k">Tracked</div><div class="v">${c.total}</div><div class="n">data requirements</div></div>
      <div class="tile ok"><div class="k">Up to date</div><div class="v">${c.current}</div><div class="n">received within their cycle</div></div>
      <div class="tile gold"><div class="k">Overdue</div><div class="v">${c.overdue}</div><div class="n">past their frequency</div></div>
      <div class="tile ${c.never ? 'bad' : 'grey'}"><div class="k">Never received</div><div class="v">${c.never}</div><div class="n">nothing loaded yet</div></div>
    </div>
    <div id="intake-result"></div>
    ${sections}`;
};

const intakeState = {reqs: {}, pending: null};

function intakeCard(i) {
  const [badge, label] = STATE_LABEL[i.state] || ['neutral', i.state];
  // last_at is a full timestamp; dayLong takes a date-only string.
  const last = i.last_at
    ? `Last: ${esc(i.last_file || '')} · ${dayLong(i.last_at.slice(0, 10))}${i.last_rows ? ` · ${i.last_rows} rows` : ''}`
    : 'Nothing received yet';

  let action;
  if (i.handler_kind === 'in_tool') {
    action = `<a class="btn" href="#${esc(i.goto || 'ar')}">Open ${esc(i.goto || 'the screen')}</a>`;
  } else {
    action = `
      <input type="file" id="f-${esc(i.id)}" accept="${esc(i.accepts || '')}" style="display:none"
             onchange="intakeUpload('${esc(i.id)}', this)">
      <button class="btn primary" onclick="document.getElementById('f-${esc(i.id)}').click()">Upload</button>`;
  }

  return `
    <div class="intake-row" id="row-${esc(i.id)}">
      <div class="intake-no">${i.no}</div>
      <div class="intake-main">
        <div class="intake-need">${esc(i.need)}</div>
        <div class="intake-meta">
          <span class="badge ${PRIORITY_BADGE[i.priority] || 'neutral'}">${esc(i.priority)}</span>
          <span class="badge ${badge}">${esc(label)}</span>
          <span>${esc(i.format)}</span><span class="sep">·</span><span>${esc(i.frequency)}</span>
        </div>
        <div class="intake-last">${last}</div>
      </div>
      <div class="intake-act">${action}</div>
    </div>`;
}

async function intakeUpload(reqId, input) {
  const file = input.files[0];
  if (!file) return;
  input.value = '';
  const row = document.getElementById('row-' + reqId);
  const box = document.getElementById('intake-result');
  row.classList.add('busy');
  box.innerHTML = notice('info', '', `Reading <b>${esc(file.name)}</b>…`);
  box.scrollIntoView({behavior: 'smooth', block: 'nearest'});

  const fd = new FormData();
  fd.append('file', file);
  try {
    const res = await fetch(API + `/intake/${reqId}/upload`, {method: 'POST', body: fd});
    const out = await res.json();
    if (!res.ok) throw new Error(out.detail || `Upload failed (${res.status})`);

    if (out.status === 'loaded' || out.status === 'filed') {
      box.innerHTML = notice('ok', 'Loaded',
        `<b>${esc(out.file)}</b> — ${intakeSummary(out)}`);
      views.intake();
    } else if (out.mode === 'mapping') {
      intakeState.pending = out;
      box.innerHTML = renderMapping(reqId, out);
      box.scrollIntoView({behavior: 'smooth', block: 'start'});
    } else if (out.mode === 'fs_pdf') {
      box.innerHTML = notice('warn', 'Transcribed — needs review',
        `<b>${esc(out.file)}</b> was read with vision. Check the cross-footing report
         under Data &amp; Sources before it is loaded into reporting.`);
    }
  } catch (err) {
    box.innerHTML = notice('bad', 'Could not read that file', esc(err.message));
  } finally {
    row.classList.remove('busy');
  }
}

function intakeSummary(out) {
  if (out.message) return esc(out.message);
  const bits = [];
  if (out.created !== undefined) bits.push(`${out.created} created`);
  if (out.updated) bits.push(`${out.updated} updated`);
  if (out.loaded !== undefined) bits.push(`${out.loaded} loaded`);
  if (out.rows_out !== undefined) bits.push(`${out.rows_out} rows`);
  if (out.failed) bits.push(`${out.failed} rejected`);
  return bits.join(' · ') || 'done';
}

function renderMapping(reqId, m) {
  const mapped = m.mapping.filter(x => x.target_field);
  const rows = mapped.map(x => `<tr>
    <td style="font-size:13px">${esc(x.source_column)}</td>
    <td style="font:500 12px var(--font-mono)">${esc(x.target_field)}</td>
    <td><span class="badge ${x.confidence === 'high' ? 'ok' : x.confidence === 'medium' ? 'warn' : 'bad'}">${esc(x.confidence)}</span></td>
    <td class="muted" style="font-size:12px">${esc(x.reason || '')}</td></tr>`);

  const cols = mapped.map(x => x.target_field);
  const preview = m.preview.map(r => `<tr>${cols.map(c =>
    `<td style="font-size:12.5px">${esc(String(r[c] ?? ''))}</td>`).join('')}</tr>`);

  const consts = Object.entries(m.constants || {});

  return `
    <div class="card mapping">
      <div class="card-h">
        <h3>Check this mapping before it is written</h3>
        <span class="stamp">${esc(m.file)} → ${esc(m.label)}</span>
      </div>
      <div class="card-b">
        ${m.warnings.length ? noticeList(m.warnings, 'warn', 'Worth reading first') : ''}
        <p class="muted" style="margin:0 0 14px">
          <b>${m.rows_ready}</b> of ${m.rows_in} rows are ready to write${m.rows_skipped ? `, ${m.rows_skipped} skipped as blank or missing a required field` : ''}.
          Nothing has been saved yet.
        </p>
        ${table('<th>Your column</th><th>Goes to</th><th>Confidence</th><th>Why</th>', rows)}
        ${consts.length ? `<p class="muted" style="margin:12px 0 0">Applied to every row: ${
          consts.map(([k, v]) => `<code>${esc(k)}=${esc(v)}</code>`).join(' ')}</p>` : ''}
        ${m.unmapped_columns.length ? `<p class="muted" style="margin:12px 0 0">
          Ignored: ${m.unmapped_columns.map(c => `<code>${esc(c)}</code>`).join(' ')}</p>` : ''}
        <h4 style="margin:20px 0 8px;font:600 13px var(--font-display)">First ${m.preview.length} rows as they would be saved</h4>
        <div style="overflow-x:auto">${table(cols.map(c => `<th>${esc(c)}</th>`).join(''), preview)}</div>
        <div class="mapping-act">
          <button class="btn primary" onclick="intakeConfirm('${esc(reqId)}')" ${m.rows_ready ? '' : 'disabled'}>
            Write ${m.rows_ready} row${m.rows_ready === 1 ? '' : 's'}
          </button>
          <button class="btn" onclick="intakeState.pending=null;document.getElementById('intake-result').innerHTML=''">Discard</button>
        </div>
      </div>
    </div>`;
}

async function intakeConfirm(reqId) {
  const m = intakeState.pending;
  if (!m) return;
  const box = document.getElementById('intake-result');
  box.innerHTML = notice('info', '', 'Writing…');
  try {
    const res = await fetch(API + `/intake/${reqId}/confirm`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({target: m.target, file: m.file, rows: m.rows}),
    });
    const out = await res.json();
    if (!res.ok) throw new Error(out.detail || `Failed (${res.status})`);
    intakeState.pending = null;
    box.innerHTML = notice(out.failed ? 'warn' : 'ok', 'Written',
      `<b>${esc(m.file)}</b> — ${intakeSummary(out)}` +
      (out.errors && out.errors.length ? `<br><span class="muted">${out.errors.slice(0, 5).map(esc).join('<br>')}</span>` : ''));
    views.intake();
  } catch (err) {
    box.innerHTML = notice('bad', 'Could not write those rows', esc(err.message));
  }
}
