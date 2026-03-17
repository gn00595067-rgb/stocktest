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
- `USE_GOOGLE_SHEET`: 設為 `1` 或 `true` 時，持倉與沖銷資料改存 Google 試算表（見下方「Google 試算表聯動」）

## 啟動

```bash
streamlit run app.py
```

瀏覽器會開啟 `http://localhost:8501`。

## 使用流程

1. **主檔/設定**：先點「載入種子資料」或上傳 stock_master CSV（欄位：stock_id, name, industry_name, market, exchange, is_etf）。
2. **交易輸入**：選股票、買賣人、日期、買/賣、價格（可依現價帶入）、股數、是否當沖、備註後送出；下方可刪除指定 ID 的今日交易。
3. **庫存損益**：選擇時間區間，持倉與損益依 **自定沖銷** 規則計算；請至「自定沖銷設定」頁設定賣出與買進的配對。
4. **日成交彙總**：切換以日期/股票/買賣人彙總，可匯出 Excel。
5. **投資績效**：查看已實現/未實現/總損益、勝率、產業持股分布與圖表。

## 專案結構

```
stockanalysis/
├── app.py              # Streamlit 主入口
├── pages/              # 多分頁（側欄順序依檔名）
│   ├── 0_投資績效.py
│   ├── 1_庫存損益.py
│   ├── 2_個股明細.py
│   ├── 3_交易輸入.py
│   ├── 4_交易匯入.py
│   ├── 5_自定沖銷設定.py
│   └── 6_主檔設定.py
├── services/           # 即時報價、損益演算法
├── db/                 # SQLAlchemy 模型、連線、種子
├── reports/            # 持倉報表、日彙總
└── tests/              # pytest 單元測試（損益演算法）
```

## Google 試算表聯動（Streamlit 免費版適用）

程式重啟後本機 SQLite 會清空，可改為用 Google 試算表當長期儲存，讓**交易**與**自定沖銷規則**與試算表雙向同步。

### 1. 建立 Google 試算表與服務帳號

1. 在 [Google Cloud Console](https://console.cloud.google.com/) 建立專案（或選現有專案）。
2. 啟用 **Google Sheets API** 與 **Google Drive API**。
3. **IAM 與管理** → **服務帳號** → 建立服務帳號，下載 **JSON 金鑰**。
4. 在 Google Drive 建立一份**新試算表**，把試算表 ID 記下來（網址中 `/d/` 與 `/edit` 之間）。
5. 將該試算表**共用**給服務帳號的 Email（例如 `xxx@yyy.iam.gserviceaccount.com`），權限設為**編輯者**。

### 2. 設定環境

**本機**：在 `.env` 加入：

```env
USE_GOOGLE_SHEET=true
GOOGLE_SHEET_ID=你的試算表ID
GOOGLE_SHEET_CREDENTIALS={"type":"service_account", ...}   # 整個 JSON 金鑰內容貼成一行
```

**Streamlit Cloud**：在 App 的 **Secrets** 加入：

```toml
USE_GOOGLE_SHEET = "true"
GOOGLE_SHEET_ID = "你的試算表ID"
GOOGLE_SHEET_CREDENTIALS = '{"type": "service_account", ...}'   # JSON 字串
```

（若 JSON 較長，可改為用 TOML 多行字串或將金鑰內容存成單一 secret 字串後在程式裡解析。）

### 3. 行為說明

- 啟用後程式使用**記憶體 SQLite**，每次啟動時從試算表讀取 **trades**、**custom_match_rules** 兩張工作表並載入。
- 每次在網頁上**新增／修改／刪除**交易或沖銷規則並成功寫入後，會自動將整份資料**寫回**試算表。
- 試算表內需有兩張工作表：**trades**（欄位：id, user, stock_id, trade_date, side, price, quantity, is_daytrade, fee, tax, note）、**custom_match_rules**（sell_trade_id, buy_trade_id, matched_qty, created_at）。若不存在，第一次寫入時會自動建立。

## 執行測試

```bash
pytest tests/ -v
```

## 資料表說明

- **trades**: 每筆交易（id, user, stock_id, trade_date, side, price, quantity, is_daytrade, fee, tax, note）
- **stock_master**: 股票主檔（stock_id, name, industry_name, market, exchange, is_etf, updated_at）
- **cashflows**: 股利/現金調整（id, user, stock_id, date, type, amount, memo）
- **trade_matches**: 買賣沖銷明細（sell_trade_id, buy_trade_id, matched_qty, buy_price, sell_price, pnl, policy）
