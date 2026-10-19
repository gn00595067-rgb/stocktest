# 圖解版操作說明（自動截圖）

畫面改版後要更新操作說明時，照下面重跑，不用手動截圖。全部用示範資料（「示範帳戶」），不含真實資料與人名。

1. 建示範資料庫（在專案根目錄）：`python scripts/manual/make_demo_db.py scripts/manual/demo.db`
2. 用示範資料庫啟動 app（port 8601）：
   `DATABASE_URL=sqlite:///scripts/manual/demo.db USE_GOOGLE_SHEET=false python -m streamlit run app.py --server.headless true --server.port 8601`
3. 在 `scripts/manual/` 目錄依序執行截圖（需要 Chrome、`websocket-client`）：
   `a_yahoo.py`、`a_entry.py`、`a_sell.py`、`a_reports.py`、`a_pwd.py` → 圖存在 `shots/`
   （`a_pwd.py` 會把示範帳號密碼改掉，跑完執行 `python _reset_demo_pw.py` 改回 demo1234）
   示範登入：只輸入密碼 `demo1234`（帳號「老闆」）。
4. 產生 Word：`python build_manual.py 輸出.docx`

- `cdp.py`：用 Chrome DevTools 協定控制無頭 Chrome；`ann.py`：在畫面上畫紅框與編號、裁切截圖。
- 文字內容與編號說明都在 `build_manual.py`。
