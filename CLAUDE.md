# CLAUDE.md — stocktest 交易記帳（stockanalysis 資料夾）

一人開發的台股交易記帳 Streamlit app，成果給 Peggy（非技術）與同事測試。狀態 🟢 LIVE（只修 bug）。

## Push 目標與分支
- remote：`origin` → `github.com/gn00595067-rgb/stocktest.git`
- 主分支：`main`（PR 也用 main）
- 部署：Streamlit Cloud（掛載路徑 `/mount/src/stocktest`）
- **永遠不要 force push**；push 前先 `pytest` 綠。

## 紀錄（不失憶核心）
- 每完成一個任務 → `docs/WORKLOG.md` 追加一條。
- 每個「選 A 不選 B」→ `docs/DECISIONS.md` 追加一條。
- 新功能先寫 `docs/specs/<功能>.md` 再實作。
- `/weekly` 會讀本檔 `docs/WORKLOG.md`；沒寫 = 週報看不到本專案進度。

## 架構重點
- 頁面在 `pages/`，報表計算在 `reports/`，服務層在 `services/`，資料模型 `db/models.py`。
- **沖銷（配對）引擎**：`services/pnl_engine.py`（`compute_matches` / `net_pnl_for_match`），是損益計算的真相源；`reports/` 各報表都靠它，改動要跑 `tests/test_pnl_engine.py`。
- **兩套「已實現」計算**要注意口徑一致：
  - `reports/portfolio_report.py`：庫存損益頁；`build_portfolio_df` 的持倉表只含 `qty>0` 的股票，**KPI 的已實現改用 `get_realized_pnl_by_stock()`（全部股票）**，勿退回用持倉表加總。
  - `reports/realized_report.py`：已實現損益頁；`build_realized_ledger` 不管有無庫存、全部沖銷都算。
- 費率／稅率：`services/trade_fees.py`（主檔/設定頁可調）。
- 即時報價：`services/price_service.py`（TWSE MIS）；歷史股價：FinMind（需 Token）。

## 地雷
- `aggregate_by` 等除法欄位遇「成本為 0」（配股 price=0）要用 `float("nan")` 迴避，勿用 `pd.NA`（會轉 object dtype 讓 `.round()` 崩潰）。
- 交易輸入頁（`pages/3_交易輸入.py`）大量用 `session_state` 管理多列輸入與沖銷配對；改動送出／重置流程（`te_rreset_*`、`te_reset_match_*`、逐筆賣出 `_sell_mode`）要小心 widget 建立前後的時序。
