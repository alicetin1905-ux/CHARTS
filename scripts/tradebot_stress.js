#!/usr/bin/env node
// Stress test for TradeBot (github.com/alicetin1905-ux/TradeBot): reruns the bot's OWN backtest code
// (scripts/backtest.js -> simulate / precompute, unchanged rules) with more realistic fills switched on:
//
//   slip     stops, flip exits and market orders fill this fraction worse (e.g. 0.001 = 0.1%)
//   gaps     a stop the candle opened beyond fills at that open instead of exactly at the stop
//   through  targets and limit entries only fill if price trades this fraction beyond them (not a touch)
//   funding  { rate, both }: perp funding per 8h on the open position; longs pay / shorts receive,
//            or with both: true every position pays (pessimistic)
//
// Everything else is the bot's live setup as in its backtest/LIVE_NOW.md row "LIVE NOW".
//
//   TRADEBOT_DIR=/path/to/TradeBot node scripts/tradebot_stress.js [--coins ETH,BNB,...] [--tag name]
//
// Needs the bot's 1H candle cache (backtest/cache/*-1H.json, written by its own backtest.js).
'use strict';
const fs = require('fs');
const path = require('path');
const Module = require('module');

const DIR = process.env.TRADEBOT_DIR || path.join(__dirname, '..', '..', 'alicetin1905-ux', 'tradebot');
const HOUR = 3600000;
const args = process.argv.slice(2);
const opt = (k, d) => (args.includes(k) ? args[args.indexOf(k) + 1] : d);

// ---- load the bot's backtest.js with the realism switches patched into simulate() ----
function loadBacktest() {
  const file = path.join(DIR, 'scripts', 'backtest.js');
  let src = fs.readFileSync(file, 'utf8');
  const patch = (from, to) => {
    if (!src.includes(from)) throw new Error('patch target not found: ' + from.slice(0, 60));
    src = src.replace(from, to);
  };
  patch("const FEE_TAKER = R.fees ? FEES.taker : 0, FEE_MAKER = R.fees ? FEES.maker : 0;",
    "const FEE_TAKER = R.fees ? FEES.taker : 0, FEE_MAKER = R.fees ? FEES.maker : 0;\n" +
    "  const SLIP = R.slip || 0, THRU = R.through || 0;\n" +
    "  const stopFill = (p, c, t) => { let px = p.stop; if (R.gaps && t > p.openedAt) px = p.bias === 1 ? Math.min(px, c.o) : Math.max(px, c.o); return px * (1 - p.bias * SLIP); };");
  patch("closeFill(p, p.qtyRemaining, p.stop, p.trailing ?", "closeFill(p, p.qtyRemaining, stopFill(p, c, t), p.trailing ?");
  patch("if (!(p.bias === 1 ? c.h >= p[k] : c.l <= p[k])) break;",
    "if (!(p.bias === 1 ? c.h >= p[k] * (1 + THRU) : c.l <= p[k] * (1 - THRU))) break;");
  patch("if (o.sig.bias === 1 ? c.l <= o.price : c.h >= o.price) {",
    "if (o.sig.bias === 1 ? c.l <= o.price * (1 - THRU) : c.h >= o.price * (1 + THRU)) {");
  patch("closeFill(p, p.qtyRemaining, series[p.symbol].h1[i].c, 'signal flip', FEE_TAKER, t);",
    "closeFill(p, p.qtyRemaining, series[p.symbol].h1[i].c * (1 - p.bias * SLIP), 'signal flip', FEE_TAKER, t);");
  patch("openPosition(s, sig, sig.close, t, FEE_TAKER, margin);",
    "openPosition(s, sig, sig.close * (1 + sig.bias * SLIP), t, FEE_TAKER, margin);");
  patch("      if (i == null || t <= p.openedAt) continue;\n      manage(",
    "      if (i == null || t <= p.openedAt) continue;\n" +
    "      if (R.funding) { const fc = (R.funding.both ? 1 : p.bias) * R.funding.rate / 8 * series[p.symbol].h1[i].c * p.qtyRemaining; p.pnl -= fc; balance -= fc; }\n" +
    "      manage(");
  const m = new Module(file, module);
  m.filename = file;
  m.paths = Module._nodeModulePaths(path.dirname(file));
  m._compile(src, file);
  return m.exports;
}

process.env.TRADEBOT_SETTINGS = 'off';
const BT = loadBacktest();
const config = require(path.join(DIR, 'config'));

// ---- data + signals (signals cached per coin: computing them is the slow part) ----
const SIG_CACHE = path.join(__dirname, '..', 'data', 'tradebot_signals');
function loadSeries(symbols, start) {
  fs.mkdirSync(SIG_CACHE, { recursive: true });
  const from = start - (400 * 4 + 48) * HOUR;
  const series = {};
  for (const s of symbols) {
    const f = path.join(DIR, 'backtest', 'cache', `${s}-1H.json`);
    if (!fs.existsSync(f)) { process.stderr.write(`no candles for ${s}, skipped\n`); continue; }
    const h1 = JSON.parse(fs.readFileSync(f, 'utf8')).filter(c => c.t >= from).sort((a, b) => a.t - b.t);
    const cf = path.join(SIG_CACHE, `${s}-${start}.json`);
    let sig4;
    if (fs.existsSync(cf)) sig4 = new Map(JSON.parse(fs.readFileSync(cf, 'utf8')));
    else {
      process.stderr.write(`scoring ${s} (${h1.length} 1H candles)…\n`);
      sig4 = BT.precompute(s, BT.to4h(h1), 4, start);
      fs.writeFileSync(cf, JSON.stringify([...sig4.entries()]));
    }
    series[s] = { h1, sig1: new Map(), sig4 };
    BT.addFilterInputs(series[s]);
  }
  return series;
}

function liveRules() {
  const live = BT.VARIANTS.find(v => v.focus);
  const P = config.PORTFOLIO;
  return {
    ...live.rules, start: P.STARTING_BALANCE, margin: P.MARGIN_USDT, leverage: P.LEVERAGE, maxOpen: P.MAX_OPEN_POSITIONS,
    maxSameDir: P.MAX_SAME_DIRECTION, minScore: 65, limit: { atr: 0.3, hours: 4 }, beBufferPct: 0.2, riskUsd: null, riskPct: P.RISK_PCT,
  };
}

const STRESS = [
  ['bot backtest as is (reproduces LIVE_NOW.md)', {}],
  ['stops / flip exits 0.05% slippage', { slip: 0.0005 }],
  ['stops / flip exits 0.1% slippage', { slip: 0.001 }],
  ['stops / flip exits 0.2% slippage', { slip: 0.002 }],
  ['stop fills at the open when price gaps through it', { gaps: true }],
  ['targets + limit entries must trade 0.05% through', { through: 0.0005 }],
  ['funding 0.01% / 8h (longs pay, shorts receive)', { funding: { rate: 0.0001 } }],
  ['funding 0.01% / 8h paid on every position', { funding: { rate: 0.0001, both: true } }],
  ['REALISTIC: 0.1% slip + gaps + 0.02% through + funding', { slip: 0.001, gaps: true, through: 0.0002, funding: { rate: 0.0001 } }],
  ['PESSIMISTIC: 0.2% slip + gaps + 0.05% through + funding on all', { slip: 0.002, gaps: true, through: 0.0005, funding: { rate: 0.0001, both: true } }],
];

function pf(r) { let w = 0, l = 0; for (const t of r.tradeList) { if (t.pnl > 0) w += t.pnl; else l -= t.pnl; } return l ? w / l : 0; }

// Same download as the bot's own fetchHistory (OKX 1H candles, same cache format), for coins it never loaded.
async function fetchCoin(symbol, fromMs) {
  const file = path.join(DIR, 'backtest', 'cache', `${symbol}-1H.json`);
  if (fs.existsSync(file)) return;
  const tmp = file + '.part';
  const instId = require(path.join(DIR, 'src', 'okx')).instId(symbol);
  const rows = [];
  let after = '';
  for (let page = 0; page < 1500; page++) {
    const url = `https://www.okx.com/api/v5/market/history-candles?instId=${instId}&bar=1H&limit=100${after ? '&after=' + after : ''}`;
    let d;
    for (let k = 0; k < 5; k++) {
      try { d = await (await fetch(url)).json(); break; } catch (e) { await new Promise(r => setTimeout(r, 2000 * (k + 1))); }
    }
    if (d && d.code === '50011') { await new Promise(r => setTimeout(r, 2000)); page--; continue; } // rate limited: wait, retry
    if (!d || d.code !== '0') { process.stderr.write(`${symbol}: ${d ? d.msg : 'no response'}\n`); break; }
    if (!d.data.length) break;
    for (const k of d.data) if (k[8] === '1') rows.push({ t: +k[0], o: +k[1], h: +k[2], l: +k[3], c: +k[4], v: +k[6] });
    after = d.data[d.data.length - 1][0];
    if (+after <= fromMs) break;
    await new Promise(r => setTimeout(r, 120));
  }
  if (rows.length) { fs.writeFileSync(tmp, JSON.stringify(rows)); fs.renameSync(tmp, file); } // atomic: the bot never sees a half file
  process.stderr.write(`${symbol}: ${rows.length} 1H candles\n`);
}

async function fetchMain() {
  const coins = opt('--fetch').split(',').map(x => x.toUpperCase().replace(/USDT$/, '') + 'USDT');
  const from = Date.now() - (+opt('--days', 2200) + 300) * 24 * HOUR;
  for (const s of coins) await fetchCoin(s, from);
}

function main() {
  const coins = args.includes('--coins') ? opt('--coins').split(',').map(x => x.toUpperCase().replace(/USDT$/, '') + 'USDT') : config.SYMBOLS;
  const days = +opt('--days', 2200);
  const end = +opt('--end', 0) || null;
  const symbols = [...new Set([...coins, 'BTCUSDT'])];
  // same window as the bot's run: its newest cached candle minus `days`
  const newest = Math.max(...symbols.map(s => { try { const r = JSON.parse(fs.readFileSync(path.join(DIR, 'backtest', 'cache', `${s}-1H.json`), 'utf8')); return r.reduce((m, c) => Math.max(m, c.t), 0); } catch (e) { return 0; } }));
  const now = end || newest;
  const start = +opt('--start', 0) || now - days * 24 * HOUR;
  const series = loadSeries(symbols, start);
  const have = coins.filter(s => series[s]);
  const times = [...new Set(Object.keys(series).flatMap(s => series[s].h1.map(c => c.t)))].filter(t => t >= start && t <= now).sort((a, b) => a - b);
  const base = liveRules();
  const years = [...new Set(times.map(t => new Date(t).getUTCFullYear()))];
  const L = [];
  const iso = (t) => new Date(t).toISOString().slice(0, 10);
  L.push(`TradeBot stress test · ${have.length} coins · ${iso(start)} → ${iso(now)} · live rules (score 65, limit 0.3 ATR / 4h, 2.5% risk, 10x, 5 slots)`);
  L.push(`coins: ${have.map(s => s.replace('USDT', '')).join(', ')}`, '');
  L.push('Compound: 2000 USDT, 2.5% risk, whole period. Per year: fresh 2000 USDT, $100 fixed risk (net $).', '');
  const pad = (x, n) => String(x).padStart(n);
  L.push('variant'.padEnd(62) + pad('end $', 12) + pad('PF', 6) + pad('max DD', 8) + pad('trades', 8) + years.map(y => pad(y, 8)).join(''));
  const out = { rows: [] };
  for (const [name, extra] of STRESS) {
    process.stderr.write(name + '\n');
    const c = BT.simulate(series, have, times, { ...base, ...extra });
    const cells = years.map(y => {
      const yt = times.filter(t => new Date(t).getUTCFullYear() === y);
      const r = BT.simulate(series, have, yt, { ...base, ...extra, riskUsd: 100, riskPct: null });
      return pad(r.net.toFixed(0), 8);
    });
    L.push(name.padEnd(62) + pad(Math.round(base.start + c.net).toLocaleString('en-US'), 12) + pad(pf(c).toFixed(2), 6) +
      pad(c.maxDDPct.toFixed(1) + '%', 8) + pad(c.trades, 8) + cells.join(''));
    out.rows.push({ name, extra, end: base.start + c.net, pf: pf(c), maxDD: c.maxDDPct, trades: c.trades, tradeList: name.startsWith('bot') ? c.tradeList : undefined });
  }
  const text = L.join('\n');
  console.log(text);
  const tag = opt('--tag', 'live');
  fs.writeFileSync(path.join(__dirname, '..', 'data', `tradebot_stress_${tag}.json`), JSON.stringify(out));
}

if (args.includes("--fetch")) fetchMain().catch(e => { console.error(e); process.exit(1); }); else main();
