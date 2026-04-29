# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 專案概述

「興櫃悠哉選」— 興櫃股票每日篩選工具。從櫃買中心抓取興櫃當日行情 CSV，套用使用者定義的篩選規則，產出單一互動式 HTML 報表。

## 常用指令

```bash
# 抓最新資料 + 套用預設規則 + 產 HTML
python3 screener.py

# 自訂門檻
python3 screener.py --diff 2000 --volume 300000 --price 150 \
                    --change-max 5 --turnover 3000

# 不要求「成交價 > 日均價」
python3 screener.py --no-above-avg

# 用本機 CSV 除錯（不打網路）
python3 screener.py --input emgstk.csv

# 自訂輸出路徑
python3 screener.py --out /tmp/out.html
```

無 build / lint / test 設定 — 純 Python 標準函式庫腳本，無外部依賴。

## 架構

**單檔架構**：`screener.py` 包含所有邏輯，分成 4 個階段：

1. **`fetch_csv()`** — 從 `https://www.tpex.org.tw/web/emergingstock/lateststats/new_dl.php` 下載 CSV (UTF-8)
2. **`parse_rows()`** — 解析 CSV 並計算衍生欄位（`change_pct`, `turnover`, `above_avg`, `diff`），回傳 `Row` dataclass list 與民國日期
3. **`screen()`** — 套用 7 條篩選規則（4 條原始 + 3 條 P0 補強，見檔頭 docstring）
4. **`render_html()`** — 把 **全部** rows 序列化成 JSON 注入 HTML 模板（不只是篩選後的），讓使用者在瀏覽器端可即時調整門檻重新篩選

`HTML_TEMPLATE` 是內嵌的單頁 HTML（含 CSS + vanilla JS），用 `__PLACEHOLDER__` 字串替換注入資料。沒有外部 JS 依賴。

## 資料源關鍵知識

CSV 欄位（**繁體中文標題**，UTF-8）：
`資料日期, 資料時間, 代號, 名稱, 前日均價, 報買價, 報買量, 報賣價, 報賣量, 日最高, 日最低, 日均價, 成交, 投資人成交買賣別, 暫停交易開始時間, 成交量, 進度日期, 上市櫃進度`

- **日期是民國年** 7 碼（例：`1150428` = 2026-04-28），用 `roc_to_ad()` 轉換
- **「投資人成交買賣別」只有 `B` / `S`**（最後一筆成交是外盤/內盤），**不是法人買賣超**
- **「成交量」單位是股**，不是張
- **「報買量/報賣量」是收盤瞬間最佳一檔的快照**，不是全日累計委託
- 已下市/暫停交易的列會缺欄位，`parse_rows()` 用 `last <= 0 or side not in ("B","S")` 過濾掉

## 規則設計脈絡（重要）

使用者最初提出 4 條規則，分析後發現規則 ① 和 ③ 都是「瞬間訊號」，缺乏全日買賣強度判斷，因此補上 P0 的 ⑤⑥⑦：

- **⑤ 成交 > 日均價** — 補強 ③，「最後一筆方向」升級為「全日買盤強度」
- **⑥ 漲幅 < N%** — 防追高
- **⑦ 成交金額(萬元)** — 取代純股數，反映實質流動性

未來若加規則（例如原規劃但未實作的「20MA 多頭」、「零股策略」），應沿著 `Row` dataclass → `screen()` → HTML controls + 表格欄位 + JS filter 這條路徑同步擴充。

## 待辦 / 已知限制

- **20MA 多頭規則尚未實作**（需累積 20 個交易日歷史，建議用 SQLite + cron 每日存檔）
- 沒有歷史回測機制
- 沒有自動排程（可後續用 cron 或 `/schedule` 設定每日盤後執行）
