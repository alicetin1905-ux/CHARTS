#!/usr/bin/env node
// Checks scripts/atlas_score.py against TradeBot's own analyse() (src/atlasScore.js, classic):
// reads candles + Python scores from a JSON file, reruns analyse() on the full history up to each sampled candle.
//   TRADEBOT_DIR=/path/to/TradeBot node scripts/check_score.js file.json
'use strict';
const fs = require('fs');
const path = require('path');
process.env.TRADEBOT_SETTINGS = 'off';
const DIR = process.env.TRADEBOT_DIR || path.join(__dirname, '..', '..', 'alicetin1905-ux', 'tradebot');
const atlas = require(path.join(DIR, 'src', 'atlasScore'));
const { tf, candles, daily, py, idx } = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const tfMs = +tf * 60000;
let same = 0, worst = 0;
for (const i of idx) {
  const k = candles.slice(0, i + 1).concat([candles[i]]);           // closed history + a dummy forming bar
  const closeT = candles[i].t + tfMs;
  const dc = daily.filter(d => d.t + 86400000 <= closeT);
  const cs = { [tf]: k };
  if (dc.length >= 2) cs.D = dc.concat([dc[dc.length - 1]]);
  const a = atlas.analyse({ symbol: 'X', candles: cs, ticker: null, oi: [], ratio: null, book: null, tape: null, entryTf: tf,
    mtfTfs: [], flipStore: {}, account: 1000, riskPct: 1, leverage: 10, scoreThreshold: 25, scoreMode: 'classic', mtfTrim: false });
  const d = Math.abs(a.score - py[i]);
  if (d === 0) same++; else console.log('candle', i, 'js', a.score, 'py', py[i]);
  worst = Math.max(worst, d);
}
console.log(`${same}/${idx.length} identical, worst difference ${worst}`);
