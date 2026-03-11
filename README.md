# 台股股價分析系統

單機 SQLite + Streamlit，目標使用者為主管，操作類似 Excel，含仿 Yahoo 奇摩的交易輸入介面。

## 安裝

```bash
cd stockanalysis
pip install -r requirements.txt
```

## 環境變數

複製 `.env.example` 為 `.env` 後依需求設定：

- `DB_PATH`: SQLite 檔案路徑（選填，預設 `stock_analysis.db`）
- `FINMIND_TOKEN`: FinMind API token（選填，無則使用 Mock 報價）
- `FUGLE_API_KEY`: Fugle API key（選填，預留介面）

## 啟動

```bash
streamlit run app.py
```

瀏覽器會開啟 `http://localhost:8501`。

## 使用流程

1. **主檔/設定**：先點「載入種子資料」或上傳 stock_master CSV（欄位：stock_id, name, industry_name, market, exchange, is_etf）。
2. **交易輸入**：選股票、買賣人、日期、買/賣、價格（可依現價帶入）、股數、是否當沖、備註後送出；下方可刪除指定 ID 的今日交易。
3. **Portfolio 持倉與損益**：選擇時間區間，持倉與損益依 **自定沖銷** 規則計算；請至「自定沖銷設定」頁設定賣出與買進的配對。
4. **日成交彙總**：切換以日期/股票/買賣人彙總，可匯出 Excel。
5. **投資績效**：查看已實現/未實現/總損益、勝率、產業持股分布與圖表。

## 專案結構

```
stockanalysis/
├── app.py              # Streamlit 主入口
├── pages/              # 多分頁
│   ├── 1_交易輸入.py
│   ├── 2_Portfolio持倉與損益.py
│   ├── 3_日成交彙總.py
│   ├── 4_投資績效.py
│   └── 5_主檔設定.py
├── services/           # 即時報價、損益演算法
├── db/                 # SQLAlchemy 模型、連線、種子
├── reports/            # 持倉報表、日彙總
└── tests/              # pytest 單元測試（損益演算法）
```

## 執行測試

```bash
pytest tests/ -v
```

## 資料表說明

- **trades**: 每筆交易（id, user, stock_id, trade_date, side, price, quantity, is_daytrade, fee, tax, note）
- **stock_master**: 股票主檔（stock_id, name, industry_name, market, exchange, is_etf, updated_at）
- **cashflows**: 股利/現金調整（id, user, stock_id, date, type, amount, memo）
- **trade_matches**: 買賣沖銷明細（sell_trade_id, buy_trade_id, matched_qty, buy_price, sell_price, pnl, policy）
