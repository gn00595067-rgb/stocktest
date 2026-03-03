# 用 GitHub 網頁上傳完整專案（含資料夾）

完全不用裝 Git 或 GitHub Desktop，只用瀏覽器操作。

---

## 第一步：打開你的 repo 並進入上傳頁面

1. 用瀏覽器打開：  
   **https://github.com/gn00595067-rgb/stocktest**  
   （請改成你的帳號）

2. 確認你在 **repo 根目錄**（網址結尾是 `/stocktest` 或 `/stocktest` 後面沒有 `/xxx`）

3. 點上方按鈕 **「Add file」** → 選 **「Upload files」**

4. 會看到一個大區塊寫 **「Drag additional files here to add them to your repository」**（把檔案拖到這裡）

---

## 第二步：在本機選好要上傳的「所有東西」

1. 打開 **檔案總管**，前往：  
   **`C:\Users\gn005\OneDrive\桌面\stockanalysis`**

2. 在 **stockanalysis** 資料夾裡按 **Ctrl + A**（全選），會選到：
   - 檔案：`app.py`、`requirements.txt`、`README.md`、`.env.example`、可能有 `stock_analysis.db`、`.gitignore`、`DEPLOY.md` 等
   - 資料夾：**pages**、**db**、**services**、**reports**、**tests**

3. 保持全選狀態，不要點掉

---

## 第三步：拖曳到 GitHub 網頁

1. 回到瀏覽器，視窗不要全螢幕比較好操作（例如縮成一半螢幕）

2. 用滑鼠 **拖曳** 剛才選好的那一整批檔案與資料夾，拖到 GitHub 網頁裡 **「Drag additional files here...」** 那塊灰色區域

3. 放開滑鼠後，會開始上傳，你會看到檔名一個一個出現（包含資料夾裡的檔案，例如 `pages/1_交易輸入.py`、`db/models.py` 等）

4. 等全部跑完（可能 10～30 秒）

---

## 第四步：提交（Commit）

1. 往下捲，在 **Commit changes** 區塊：
   - **第一行**（commit 說明）可填：  
     **`上傳完整專案（含 pages, db, services, reports, tests）`**
   - 下面選 **「Commit directly to the main branch」**

2. 按綠色按鈕 **「Commit changes」**

3. 完成後重新整理頁面，應該會看到根目錄出現：
   - **pages**（點進去有 1_交易輸入.py 等）
   - **db**（models.py、database.py、seed_data.py）
   - **services**（price_service.py、pnl_engine.py）
   - **reports**（portfolio_report.py、daily_summary.py）
   - **tests**（test_pnl_engine.py）
   - 以及原本的 app.py、requirements.txt 等

---

## 小提醒

- 若 repo 裡已經有 **app.py**、**README.md** 等，你這次上傳的會 **覆蓋** 它們（以你本機的為準），這是預期行為。
- 若 **stock_analysis.db** 很大，上傳可能會比較久或失敗；若失敗可先從選取項目中取消勾選 `.db`，只上傳程式與資料夾，之後再用 Git/Desktop 補傳 db。
- 之後若又改了本機程式，要更新 GitHub：同樣 **Add file** → **Upload files**，拖曳「有改過的檔案或資料夾」上去，再 Commit 即可。

這樣就完成「用網頁版」上傳完整專案。
