# 興櫃股票篩選工具

每日從證券櫃檯買賣中心抓取興櫃當日行情，套用篩選規則並產出互動式 HTML 報表。

🌐 **線上報表**：https://kaka0513.github.io/tpex-emerging-screener/

## 功能

- **悠哉選模式**：高價活躍股（股價 > 150、漲幅 < 7%、成交額 > 2,000 萬）
- **短線飆股模式**：啟動但未過熱（5% ≤ 漲幅 ≤ 25%、收最高、量能足）
- 預估目標價、停損點、風險報酬比 (R/R)
- HTML 內可即時切換模式、調整門檻、排序

## 使用

```bash
# 抓最新資料、產 HTML（輸出到 docs/YYYY-MM-DD.html）
python3 screener.py

# CLI 預覽特定模式
python3 screener.py --mode breakout

# 重產 docs/index.html
./scripts/publish.sh

# 推到 GitHub Pages
./scripts/publish.sh --push
```

## 資料源

- 櫃買中心興櫃當日行情表
- 政府資料開放平臺 dataset 11747
- 每日盤後 15:30 後更新

## 免責

興櫃市場無漲跌停限制、流動性低，篩選結果僅供研究參考，不構成投資建議。
