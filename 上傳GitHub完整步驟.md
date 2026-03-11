# 把本機專案完整推上 GitHub（詳細步驟）

你的 GitHub 上的 **stocktest** 目前只有根目錄幾個檔案，沒有 `pages/`、`db/`、`services/` 等資料夾，部署會失敗。  
下面用兩種方式教你：**從本機專案一次把完整結構推上去**。

---

## 方式一：用 GitHub Desktop（推薦，不用打指令）

### 第一步：安裝軟體（若還沒裝）

1. **Git for Windows**（GitHub Desktop 會用到）  
   - 開啟：https://git-scm.com/download/win  
   - 下載 64-bit Git for Windows Setup  
   - 安裝時全部按「Next」即可  

2. **GitHub Desktop**  
   - 開啟：https://desktop.github.com/  
   - 下載並安裝  
   - 開啟後用你的 GitHub 帳號登入  

---

### 第二步：把 GitHub 上的 stocktest「抓到本機」一個新資料夾

1. 開啟 **GitHub Desktop**  
2. 左上角選單 **File** → **Clone repository**  
3. 分頁選 **URL**  
   - **Repository URL** 填：  
     `https://github.com/gn00595067-rgb/stocktest`  
     （若你的帳號不是 gn00595067-rgb，請改成你的帳號）  
   - **Local path** 選一個你要放專案的地方，例如：  
     `C:\Users\gn005\OneDrive\桌面`  
   - 下面的資料夾名稱會自動變成 `stocktest`，所以最後路徑會是：  
     `C:\Users\gn005\OneDrive\桌面\stocktest`  
4. 按 **Clone**  
5. 完成後，用檔案總管打開：  
   `C:\Users\gn005\OneDrive\桌面\stocktest`  
   你會看到裡面有：`.env.example`、`README.md`、`app.py`、`requirements.txt`、`stock_analysis.db` 等（和 GitHub 上一樣）  

---

### 第三步：把「完整專案」複製進 stocktest 資料夾

1. 再開一個檔案總管視窗，前往：  
   `C:\Users\gn005\OneDrive\桌面\stockanalysis`  
   （這是你本來開發用的專案，裡面有 **pages**、**db**、**services**、**reports**、**tests** 等資料夾）  

2. 在 **stockanalysis** 資料夾裡按 **Ctrl + A**（全選）  

3. 按 **Ctrl + C**（複製）  

4. 切換到 **stocktest** 資料夾（第一步 Clone 下來的那個）  

5. 按 **Ctrl + V**（貼上）  

6. 若跳出「取代或略過」的對話框：  
   - 選 **取代目標中的檔案**（或「全部取代」）  
   - 這樣會用你本機的 `app.py`、`README.md` 等覆蓋掉 Clone 下來的舊檔  

7. 確認 **stocktest** 資料夾裡現在有：  
   - 檔案：`app.py`、`requirements.txt`、`README.md`、`.env.example`、可能有 `stock_analysis.db`  
   - **資料夾**：`pages`、`db`、`services`、`reports`、`tests`  
   - 點進 `pages` 要能看到 `2_交易輸入.py`、`1_庫存損益.py` 等  

---

### 第四步：用 GitHub Desktop 上傳（Commit + Push）

1. 回到 **GitHub Desktop**  

2. 左上角 **Current repository** 若是別的專案，點一下改成 **stocktest**（Local path 就是你剛貼檔案的那個 `桌面\stocktest`）  

3. 左邊會出現 **Changes**，下面列出很多變更（新增的資料夾、修改的檔案等）  

4. 左下角 **Summary** 填一句說明，例如：  
   `上傳完整專案結構（pages, db, services, reports, tests）`  

5. 按藍色按鈕 **Commit to main**  

6. 再按右上角 **Push origin**  

7. 等幾秒，完成後到瀏覽器打開：  
   `https://github.com/gn00595067-rgb/stocktest`  
   重新整理頁面，應該會看到 **pages**、**db**、**services**、**reports**、**tests** 等資料夾都出現了。  

之後若要更新，只要在 **stocktest** 資料夾改檔或貼上新檔，再到 GitHub Desktop 做 **Commit to main** → **Push origin** 即可。

---

## 方式二：用 Git 指令（在 PowerShell 裡操作）

適合已經有裝 Git、且想用指令的人。

### 第一步：確認本機專案資料夾有完整內容

用檔案總管確認：  
`c:\Users\gn005\OneDrive\桌面\stockanalysis`  
底下要有 **pages**、**db**、**services**、**reports**、**tests** 等資料夾。

---

### 第二步：在專案資料夾裡開啟 PowerShell

1. 在檔案總管打開：  
   `c:\Users\gn005\OneDrive\桌面\stockanalysis`  
2. 在資料夾空白處 **按住 Shift + 右鍵**  
3. 選 **「在此處開啟 PowerShell 視窗」**（或「開啟終端機視窗」）  
4. 會跳出一個藍色/黑色視窗，路徑應該已經是 stockanalysis

---

### 第三步：依序輸入以下指令

**（1）初始化 Git（若這個資料夾還沒做過）**

```powershell
git init
```

**（2）把 GitHub 上的 stocktest 設成遠端**

（請把 `gn00595067-rgb` 改成你的 GitHub 帳號）

```powershell
git remote add origin https://github.com/gn00595067-rgb/stocktest.git
```

若出現 `fatal: remote origin already exists`，表示已經設過，改執行：

```powershell
git remote set-url origin https://github.com/gn00595067-rgb/stocktest.git
```

**（3）加入所有檔案**

```powershell
git add .
```

**（4）做第一次提交**

```powershell
git commit -m "上傳完整專案結構（pages, db, services, reports, tests）"
```

**（5）和 GitHub 上的 main 同步（因為 GitHub 上已經有東西）**

```powershell
git branch -M main
git pull origin main --allow-unrelated-histories
```

若問你 commit message，直接關掉視窗或輸入 `:q` 再 Enter 即可。  
若沒有問，就繼續下一步。

**（6）推上去**

```powershell
git push -u origin main
```

若跳出要你輸入帳號密碼：  
- **密碼**要填 **Personal Access Token**，不是 GitHub 登入密碼。  
- 若還沒有 Token：GitHub 網頁 → 右上頭像 → Settings → Developer settings → Personal access tokens → Generate new token，勾選 `repo`，產生後複製貼上當密碼。

---

### 完成後

到瀏覽器打開：  
`https://github.com/gn00595067-rgb/stocktest`  
重新整理，應該會看到 **pages**、**db**、**services**、**reports**、**tests** 都出現了。  
接下來就可以到 **https://share.streamlit.io** 用這個 repo 部署 Streamlit。

---

## 若遇到錯誤

| 狀況 | 處理方式 |
|------|----------|
| GitHub Desktop 找不到 Git | 先安裝 Git for Windows（上面方式一第一步） |
| `git` 不是內部或外部指令 | 安裝 Git for Windows，安裝時勾選「Add to PATH」，重開 PowerShell |
| Push 時要帳號密碼 | 密碼用 Personal Access Token，不要用登入密碼 |
| `rejected (non-fast-forward)` | 表示 GitHub 上有你本機沒有的提交，可先 `git pull origin main --allow-unrelated-histories` 再 `git push origin main` |

有問題可以把錯誤訊息貼出來再問。
