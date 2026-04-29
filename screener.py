"""興櫃股票篩選工具 (悠哉選 / 短線飆股 雙模式)

資料源: 櫃買中心 興櫃股票當日行情表 (政府資料開放平臺 dataset 11747)

兩種模式:
  【悠哉選】高價活躍股，避免追高
    股價>150, 漲幅<7%, 成交額>2000萬, 委託差>1000, side=B, 成交>日均價

  【短線飆股】啟動但未過熱的強勢股 (興櫃無漲跌停，邏輯特別調整)
    股價≥30, 5%≤漲幅≤25%, 成交額>3000萬, side=B, 成交>日均價,
    收盤≥當日最高×0.95 (收最高), 日均/收盤≥0.93 (避免尾盤偷拉),
    報買量≥報賣量
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen, Request

CSV_URL = "https://www.tpex.org.tw/web/emergingstock/lateststats/new_dl.php"
OUT_DIR = Path(__file__).parent / "docs"
LATEST_LINK = Path(__file__).parent / "emerging_screener.html"


@dataclass
class Row:
    code: str
    name: str
    bid_qty: int
    ask_qty: int
    diff: int           # 報買量 - 報賣量
    high: float
    low: float
    avg: float          # 日均價
    last: float         # 成交
    side: str           # B / S
    volume: int         # 股
    prev_avg: float
    change_pct: float   # 漲幅 %
    turnover: float     # 成交金額(萬元)
    above_avg: bool     # 成交 > 日均價
    close_to_high: float  # 收盤 / 當日最高
    avg_to_close: float   # 日均 / 收盤
    bid_ask_ratio: float  # 報買量 / 報賣量
    # 短線目標價 (適用 短線飆股 模式)
    t_short_conservative: float  # 收盤 × 1.05
    t_short_aggressive: float    # 收盤 + 日內波幅 (high - low)
    t_short_extend: float        # 收盤 × (1 + 漲幅%)  動量延伸
    stop_short: float            # min(日均, 低 × 1.02)
    rr_short: float              # 風險報酬比 = (積極-收盤)/(收盤-停損)
    overheat: bool               # 漲幅>20% 動量延伸不可信
    # 中期目標價 (適用 悠哉選 模式)
    t_mid: float                 # 收盤 × 1.10
    t_long: float                # 收盤 × 1.20
    stop_mid: float              # 收盤 × 0.93


def to_int(x: str) -> int:
    x = (x or "").strip()
    if not x or x == "-":
        return 0
    try:
        return int(float(x))
    except ValueError:
        return 0


def to_float(x: str) -> float:
    x = (x or "").strip()
    if not x or x == "-":
        return 0.0
    try:
        return float(x)
    except ValueError:
        return 0.0


def fetch_csv(url: str = CSV_URL) -> str:
    """先試 urllib, 若 SSL 驗證失敗 (此站 cert 缺 Subject Key Identifier) 改用 curl"""
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"urllib 失敗 ({e}), 改用 curl ...", file=sys.stderr)
        import subprocess
        result = subprocess.run(
            ["curl", "-sSf", "--max-time", "30", "-A", "Mozilla/5.0", url],
            capture_output=True, text=True, encoding="utf-8",
        )
        if result.returncode != 0:
            raise RuntimeError(f"curl 也失敗: {result.stderr}")
        return result.stdout


def parse_rows(text: str) -> tuple[list[Row], str]:
    reader = csv.DictReader(io.StringIO(text))
    rows: list[Row] = []
    data_date = ""
    for r in reader:
        data_date = data_date or r.get("資料日期", "").strip().strip('"')
        last = to_float(r.get("成交", ""))
        side = (r.get("投資人成交買賣別", "") or "").strip()
        if last <= 0 or side not in ("B", "S"):
            continue
        bid = to_int(r.get("報買量", ""))
        ask = to_int(r.get("報賣量", ""))
        avg = to_float(r.get("日均價", ""))
        prev_avg = to_float(r.get("前日均價", ""))
        high = to_float(r.get("日最高", ""))
        volume = to_int(r.get("成交量", ""))
        low = to_float(r.get("日最低", ""))
        change_pct = ((last - prev_avg) / prev_avg * 100) if prev_avg > 0 else 0.0
        turnover = volume * avg / 10000
        # 短線目標價
        t_sc = round(last * 1.05, 2)
        t_sa = round(last + max(high - low, 0), 2)
        t_se = round(last * (1 + max(change_pct, 0) / 100), 2)
        stop_s = round(min(avg if avg > 0 else last, low * 1.02 if low > 0 else last * 0.95), 2)
        risk = last - stop_s
        rr = round((t_sa - last) / risk, 2) if risk > 0 else 0.0
        # 流動性折價: 成交額 < 5000 萬, 目標價 × 0.95
        if turnover < 5000:
            t_sc = round(t_sc * 0.95, 2)
            t_sa = round(t_sa * 0.95, 2)
            t_se = round(t_se * 0.95, 2)
        rows.append(Row(
            code=r.get("代號", "").strip(),
            name=r.get("名稱", "").strip(),
            bid_qty=bid,
            ask_qty=ask,
            diff=bid - ask,
            high=high,
            low=low,
            avg=avg,
            last=last,
            side=side,
            volume=volume,
            prev_avg=prev_avg,
            change_pct=round(change_pct, 2),
            turnover=round(turnover, 0),
            above_avg=last > avg,
            close_to_high=round(last / high, 4) if high > 0 else 0.0,
            avg_to_close=round(avg / last, 4) if last > 0 else 0.0,
            bid_ask_ratio=round(bid / ask, 3) if ask > 0 else (999.0 if bid > 0 else 0.0),
            t_short_conservative=t_sc,
            t_short_aggressive=t_sa,
            t_short_extend=t_se,
            stop_short=stop_s,
            rr_short=rr,
            overheat=change_pct > 20,
            t_mid=round(last * 1.10, 2),
            t_long=round(last * 1.20, 2),
            stop_mid=round(last * 0.93, 2),
        ))
    return rows, data_date


def roc_to_ad(roc: str) -> str:
    roc = (roc or "").strip().strip('"')
    if len(roc) != 7:
        return roc
    try:
        y = int(roc[:3]) + 1911
        return f"{y}-{roc[3:5]}-{roc[5:7]}"
    except ValueError:
        return roc


PRESETS = {
    "leisure": {  # 悠哉選
        "price_min": 150, "change_min": -99, "change_max": 7,
        "turnover_min": 2000, "diff_min": 1000,
        "close_to_high_min": 0, "avg_to_close_min": 0,
        "bid_ask_min": 0, "above_avg": True,
    },
    "breakout": {  # 短線飆股
        "price_min": 30, "change_min": 5, "change_max": 25,
        "turnover_min": 3000, "diff_min": 0,
        "close_to_high_min": 0.95, "avg_to_close_min": 0.93,
        "bid_ask_min": 1.0, "above_avg": True,
    },
}


def screen(rows: list[Row], p: dict) -> list[Row]:
    return [
        r for r in rows
        if r.last >= p["price_min"]
        and p["change_min"] <= r.change_pct <= p["change_max"]
        and r.turnover > p["turnover_min"]
        and r.diff >= p["diff_min"]
        and r.close_to_high >= p["close_to_high_min"]
        and r.avg_to_close >= p["avg_to_close_min"]
        and r.bid_ask_ratio >= p["bid_ask_min"]
        and r.side == "B"
        and (not p["above_avg"] or r.above_avg)
    ]


def render_html(all_rows: list[Row], data_date: str) -> str:
    payload = json.dumps([asdict(r) for r in all_rows], ensure_ascii=False)
    return (HTML_TEMPLATE
            .replace("__DATA__", payload)
            .replace("__PRESETS__", json.dumps(PRESETS))
            .replace("__DATE__", roc_to_ad(data_date))
            .replace("__GEN__", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))


HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>興櫃股票篩選 — 悠哉選 / 短線飆股</title>
<style>
  :root { --bg:#0f172a; --panel:#1e293b; --fg:#e2e8f0; --muted:#94a3b8;
          --accent:#22d3ee; --accent2:#f59e0b; --buy:#ef4444; --sell:#10b981; --border:#334155; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: -apple-system, "PingFang TC", "Microsoft JhengHei", sans-serif;
         background: var(--bg); color: var(--fg); }
  header { padding: 18px 24px; border-bottom:1px solid var(--border);
           display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; }
  h1 { font-size: 20px; margin:0; color: var(--accent); }
  .meta { color: var(--muted); font-size: 13px; }
  .modes { display:flex; gap:6px; }
  .modes button { padding:8px 16px; border-radius:8px; border:1px solid var(--border);
                  background:#0b1220; color:var(--fg); font-size:14px; cursor:pointer; }
  .modes button.active { background: var(--accent2); color:#0b1220; font-weight:600; border-color:var(--accent2); }
  .panel { background: var(--panel); margin: 16px 24px; padding: 16px;
           border-radius: 10px; border:1px solid var(--border); }
  .controls { display:grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
  .field label { display:block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
  .field input { width: 100%; padding: 8px 10px; font-size: 14px;
                 background:#0b1220; color:var(--fg); border:1px solid var(--border); border-radius:6px; }
  .field .hint { font-size: 11px; color: #64748b; margin-top: 2px; }
  .summary { margin-top: 12px; font-size: 13px; color: var(--muted); }
  .summary b { color: var(--accent); font-size: 16px; }
  table { width: calc(100% - 48px); margin: 0 24px 24px; border-collapse: collapse;
          background: var(--panel); border-radius: 10px; overflow: hidden;
          border:1px solid var(--border); }
  th, td { padding: 9px 10px; text-align: right; font-size: 13px; border-bottom: 1px solid var(--border); }
  th { background: #0b1220; cursor:pointer; user-select: none; color: var(--accent); position: sticky; top:0; }
  th.sorted-asc::after { content: " ▲"; }
  th.sorted-desc::after { content: " ▼"; }
  td.text, th.text { text-align: left; }
  tr:hover { background: #273449; }
  .side-B { color: var(--buy); font-weight: 600; }
  .side-S { color: var(--sell); }
  .pos { color: var(--buy); }
  .neg { color: var(--sell); }
  footer { padding: 14px 24px; color: var(--muted); font-size: 12px; }
  .tip { background:#422006; border:1px solid #b45309; color:#fcd34d; padding:8px 12px;
         border-radius:6px; font-size:12px; margin-top:12px; display:none; }
  .tip.show { display:block; }
  .tgt-c { color:#34d399; }     /* 保守/中期 綠 */
  .tgt-a { color:#fbbf24; }     /* 積極/波段 黃 */
  .tgt-e { color:#f87171; }     /* 追擊 紅 */
  .stop  { color:#94a3b8; }     /* 停損 灰 */
  .rr-good { color:#22d3ee; font-weight:600; }
  .overheat { color:#f87171; font-size:11px; }
  /* 模式切換顯示欄位 */
  .mode-leisure  .col-breakout { display: none; }
  .mode-breakout .col-leisure  { display: none; }
</style>
</head>
<body>
<header>
  <h1>興櫃股票篩選</h1>
  <div class="modes">
    <button data-mode="leisure" class="active">悠哉選</button>
    <button data-mode="breakout">短線飆股</button>
  </div>
  <div class="meta">資料日期 <b>__DATE__</b> ｜ 產出 __GEN__</div>
</header>

<div class="panel">
  <div class="controls">
    <div class="field"><label>股價 ≥</label>
      <input type="number" id="f-price" step="0.01"></div>
    <div class="field"><label>漲幅 (%) ≥</label>
      <input type="number" id="f-chg-min" step="0.1">
      <div class="hint">悠哉設 -99 = 不限</div></div>
    <div class="field"><label>漲幅 (%) ≤</label>
      <input type="number" id="f-chg-max" step="0.1"></div>
    <div class="field"><label>成交金額 (萬) ＞</label>
      <input type="number" id="f-turn"></div>
    <div class="field"><label>委託差 ≥</label>
      <input type="number" id="f-diff">
      <div class="hint">短線設 0 = 不限</div></div>
    <div class="field"><label>收盤/最高 ≥</label>
      <input type="number" id="f-cth" step="0.01">
      <div class="hint">短線用 (0.95)；悠哉設 0</div></div>
    <div class="field"><label>日均/收盤 ≥</label>
      <input type="number" id="f-atc" step="0.01">
      <div class="hint">短線用 (0.93)；悠哉設 0</div></div>
    <div class="field"><label>報買/報賣 ≥</label>
      <input type="number" id="f-ba" step="0.1">
      <div class="hint">短線用 (1.0)；悠哉設 0</div></div>
    <div class="field"><label>成交＞日均價</label>
      <label style="display:flex;align-items:center;gap:6px;padding:8px 0">
        <input type="checkbox" id="f-above"> 啟用</label></div>
  </div>
  <div class="tip" id="warn-breakout">
    ⚠️ 興櫃無漲跌停限制，短線飆股單日波動可達 ±30% 以上。建議部位 ≤ 5%、嚴守停損。
    流動性低時，造市商價差可能 2~5%，買進即先賠價差。
  </div>
  <div class="summary">符合條件 <b id="count">0</b> / 全部 <b id="total">0</b> 檔
    ｜ 模式：<b id="mode-name" style="color:var(--accent2)">悠哉選</b></div>
</div>

<table id="tbl" class="mode-leisure">
  <thead><tr>
    <th class="text" data-k="code">代號</th>
    <th class="text" data-k="name">名稱</th>
    <th data-k="last">成交</th>
    <th data-k="change_pct">漲幅%</th>
    <th class="col-breakout" data-k="t_short_conservative">保守目標</th>
    <th class="col-breakout" data-k="t_short_aggressive">積極目標</th>
    <th class="col-breakout" data-k="t_short_extend">追擊目標</th>
    <th class="col-breakout" data-k="stop_short">停損</th>
    <th class="col-breakout" data-k="rr_short">R/R</th>
    <th class="col-leisure"  data-k="t_mid">中期目標</th>
    <th class="col-leisure"  data-k="t_long">波段目標</th>
    <th class="col-leisure"  data-k="stop_mid">停損</th>
    <th data-k="close_to_high">收/高</th>
    <th data-k="avg_to_close">均/收</th>
    <th data-k="avg">日均</th>
    <th data-k="high">最高</th>
    <th data-k="low">最低</th>
    <th data-k="bid_ask_ratio">買/賣</th>
    <th data-k="diff">委託差</th>
    <th data-k="turnover">成交額(萬)</th>
  </tr></thead>
  <tbody></tbody>
</table>

<footer>
  資料源：證券櫃檯買賣中心 興櫃股票當日行情表
</footer>

<script>
const DATA = __DATA__;
const PRESETS = __PRESETS__;
const MODE_NAME = { leisure: '悠哉選', breakout: '短線飆股' };
document.getElementById('total').textContent = DATA.length;

let mode = 'leisure';
let sortKey = 'turnover', sortDir = -1;

function fmt(n, dp){
  if (n === 0 || n === null || n === undefined) return '-';
  return Number(n).toLocaleString('en-US', {minimumFractionDigits:dp, maximumFractionDigits:dp});
}

function loadPreset(m){
  const p = PRESETS[m];
  document.getElementById('f-price').value   = p.price_min;
  document.getElementById('f-chg-min').value = p.change_min;
  document.getElementById('f-chg-max').value = p.change_max;
  document.getElementById('f-turn').value    = p.turnover_min;
  document.getElementById('f-diff').value    = p.diff_min;
  document.getElementById('f-cth').value     = p.close_to_high_min;
  document.getElementById('f-atc').value     = p.avg_to_close_min;
  document.getElementById('f-ba').value      = p.bid_ask_min;
  document.getElementById('f-above').checked = p.above_avg;
  document.getElementById('mode-name').textContent = MODE_NAME[m];
  document.getElementById('warn-breakout').classList.toggle('show', m === 'breakout');
  document.getElementById('tbl').className = 'mode-' + m;
  sortKey = (m === 'breakout') ? 'rr_short' : 'turnover';
  sortDir = -1;
}

function apply(){
  const v = id => +document.getElementById(id).value;
  const above = document.getElementById('f-above').checked;
  let rows = DATA.filter(r =>
    r.last         >= v('f-price') &&
    r.change_pct   >= v('f-chg-min') &&
    r.change_pct   <= v('f-chg-max') &&
    r.turnover     >  v('f-turn') &&
    r.diff         >= v('f-diff') &&
    r.close_to_high>= v('f-cth') &&
    r.avg_to_close >= v('f-atc') &&
    r.bid_ask_ratio>= v('f-ba') &&
    r.side === 'B' &&
    (!above || r.above_avg)
  );
  rows.sort((a,b) => {
    const va=a[sortKey], vb=b[sortKey];
    if (typeof va === 'string') return va.localeCompare(vb) * sortDir;
    return (va - vb) * sortDir;
  });
  document.getElementById('count').textContent = rows.length;
  function pct(target, base){
    if (!target || !base) return '';
    const p = ((target - base) / base * 100);
    return ` <span style="color:#64748b;font-size:11px">(${p>=0?'+':''}${p.toFixed(1)}%)</span>`;
  }
  document.querySelector('#tbl tbody').innerHTML = rows.map(r => `<tr>
    <td class="text">${r.code}</td>
    <td class="text">${r.name}</td>
    <td>${fmt(r.last,2)}</td>
    <td class="${r.change_pct>=0?'pos':'neg'}">${fmt(r.change_pct,2)}${r.overheat?' <span class="overheat">⚠️過熱</span>':''}</td>
    <td class="col-breakout tgt-c">${fmt(r.t_short_conservative,2)}${pct(r.t_short_conservative,r.last)}</td>
    <td class="col-breakout tgt-a">${fmt(r.t_short_aggressive,2)}${pct(r.t_short_aggressive,r.last)}</td>
    <td class="col-breakout tgt-e">${r.overheat?'<span class="overheat">⚠️</span> ':''}${fmt(r.t_short_extend,2)}</td>
    <td class="col-breakout stop">${fmt(r.stop_short,2)}${pct(r.stop_short,r.last)}</td>
    <td class="col-breakout ${r.rr_short>=2?'rr-good':''}">${fmt(r.rr_short,2)}</td>
    <td class="col-leisure tgt-c">${fmt(r.t_mid,2)}${pct(r.t_mid,r.last)}</td>
    <td class="col-leisure tgt-a">${fmt(r.t_long,2)}${pct(r.t_long,r.last)}</td>
    <td class="col-leisure stop">${fmt(r.stop_mid,2)}${pct(r.stop_mid,r.last)}</td>
    <td>${fmt(r.close_to_high,3)}</td>
    <td>${fmt(r.avg_to_close,3)}</td>
    <td>${fmt(r.avg,2)}</td>
    <td>${fmt(r.high,2)}</td>
    <td>${fmt(r.low,2)}</td>
    <td>${fmt(r.bid_ask_ratio,2)}</td>
    <td class="${r.diff>=0?'pos':'neg'}">${fmt(r.diff,0)}</td>
    <td>${fmt(r.turnover,0)}</td>
  </tr>`).join('');
  document.querySelectorAll('th').forEach(th => {
    th.classList.remove('sorted-asc','sorted-desc');
    if (th.dataset.k === sortKey) th.classList.add(sortDir>0?'sorted-asc':'sorted-desc');
  });
}

document.querySelectorAll('.modes button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.modes button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    mode = btn.dataset.mode;
    loadPreset(mode);
    apply();
  });
});

document.querySelectorAll('th').forEach(th => {
  th.addEventListener('click', () => {
    const k = th.dataset.k;
    if (sortKey === k) sortDir = -sortDir; else { sortKey = k; sortDir = -1; }
    apply();
  });
});

['f-price','f-chg-min','f-chg-max','f-turn','f-diff','f-cth','f-atc','f-ba']
  .forEach(id => document.getElementById(id).addEventListener('input', apply));
document.getElementById('f-above').addEventListener('change', apply);

loadPreset('leisure');
apply();
</script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="興櫃股票篩選器 (悠哉選 / 短線飆股)")
    p.add_argument("--mode", choices=["leisure", "breakout"], default="leisure",
                   help="CLI 預覽模式 (HTML 內可即時切換)")
    p.add_argument("--input", type=Path, help="本機 CSV 檔 (除錯用)")
    p.add_argument("--out", type=Path, default=None,
                   help="HTML 輸出路徑 (預設 reports/emerging_screener_YYYY-MM-DD.html)")
    p.add_argument("--no-link", action="store_true",
                   help="不更新 emerging_screener.html 捷徑")
    args = p.parse_args(argv)

    if args.input:
        text = args.input.read_text(encoding="utf-8", errors="replace")
    else:
        print(f"下載 {CSV_URL} ...", file=sys.stderr)
        text = fetch_csv()

    rows, data_date = parse_rows(text)
    print(f"解析筆數: {len(rows)} (資料日期 {roc_to_ad(data_date)})", file=sys.stderr)

    matched = screen(rows, PRESETS[args.mode])
    label = {"leisure": "悠哉選", "breakout": "短線飆股"}[args.mode]
    print(f"[{label}] 符合: {len(matched)} 檔", file=sys.stderr)
    for r in matched[:30]:
        print(f"  {r.code} {r.name:<8} 成交={r.last:>7.2f} 漲幅={r.change_pct:>6.2f}% "
              f"收/高={r.close_to_high:.3f} 均/收={r.avg_to_close:.3f} "
              f"額={r.turnover:>6.0f}萬", file=sys.stderr)

    date_ad = roc_to_ad(data_date)
    if args.out is None:
        OUT_DIR.mkdir(exist_ok=True)
        out_path = OUT_DIR / f"{date_ad}.html"
    else:
        out_path = args.out

    html = render_html(rows, data_date)
    out_path.write_text(html, encoding="utf-8")
    print(f"HTML 已輸出: {out_path}  (頁面內可切換模式)", file=sys.stderr)

    if args.out is None and not args.no_link:
        # 同時更新捷徑檔, 方便快速開啟最新報表
        LATEST_LINK.write_text(html, encoding="utf-8")
        print(f"捷徑已更新: {LATEST_LINK}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
