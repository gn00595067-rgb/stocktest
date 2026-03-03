# 上傳 GitHub 並用 Streamlit Cloud 部署

## 一、上傳到 GitHub

### 方法 A：用 Git 指令（推薦）

在專案資料夾 `stockanalysis` 裡打開終端機（PowerShell 或 CMD），依序執行：

```powershell
# 1. 進入專案目錄（若尚未進入）
cd "c:\Users\gn005\OneDrive\桌面\stockanalysis"

# 2. 初始化 Git（若尚未 init）
git init

# 3. 加入所有檔案（.gitignore 會排除 .env、*.db 等）
git add .
git status

# 4. 第一次提交
git commit -m "Initial commit: 台股股價分析系統"

# 5. 在 GitHub 網頁先建立一個新 repo（不要勾選 README），複製 repo 網址後：
git remote add origin https://github.com/你的帳號/你的repo名稱.git

# 6. 推送到 GitHub（主分支名稱可能是 main 或 master）
git branch -M main
git push -u origin main
```

若 GitHub 要求登入，可改用 **Personal Access Token** 當密碼，或使用 **GitHub Desktop** 做 commit / push。

### 方法 B：用 GitHub Desktop

1. 下載安裝 [GitHub Desktop](https://desktop.github.com/)。
2. File → Add local repository → 選 `stockanalysis` 資料夾。
3. 若顯示「不是 Git repository」，先選 Create repository，再選同一個資料夾。
4. 左側勾選要提交的檔案（不要勾 `.env`、`*.db`），寫 commit 訊息後點 Commit。
5. 點 Publish repository，選公開，再 Publish。

---

## 二、用 Streamlit Community Cloud 部署

1. 打開 **https://share.streamlit.io**，用 GitHub 帳號登入。
2. 點 **New app**。
3. 選擇：
   - **Repository**：你的 `帳號/repo名稱`
   - **Branch**：`main`（或你用的分支）
   - **Main file path**：`app.py`
4. 點 **Advanced settings**，在 **Secrets** 裡設定環境變數（選填）：
   ```toml
   FINMIND_TOKEN = "你的token"
   DB_PATH = "/tmp/stock_analysis.db"
   ```
   （Cloud 上重啟後 SQLite 會清空，僅適合展示；要持久化需改用雲端資料庫。）
5. 點 **Deploy**，等幾分鐘即可取得一個 `https://xxx.streamlit.app` 的網址。

---

## 注意事項

- **不要**把 `.env` 或內含 API key 的檔案推上 GitHub；已用 `.gitignore` 排除。
- 若要把 **SQLite 檔**也放進 repo（例如含範例資料），可把 `.gitignore` 裡的 `*.db` 那行刪掉或註解，再 `git add` 該 `.db` 檔。
- Streamlit Cloud 免費方案重啟後本機 SQLite 會還原，若要長期保存資料可之後改接雲端 DB（如 PostgreSQL）。
