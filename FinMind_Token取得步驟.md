# FinMind Token 取得步驟說明

依下列步驟即可取得 API Token，用來在系統中顯示**真實股價**（非模擬）。

---

## 第一步：開啟 FinMind 官網

用瀏覽器打開：

**https://finmindtrade.com**

---

## 第二步：註冊帳號（若尚未註冊）

1. 點選網頁上的 **「登入」** 或 **「註冊」** 連結。  
   （若找不到，可直接打開：**https://finmindtrade.com/analysis/#/account/register**）

2. 選擇 **「註冊」**，填寫：
   - 電子信箱（用來收驗證信）
   - 密碼（請自訂並記住）

3. 送出後到信箱收信，點擊 **驗證連結** 完成註冊。

4. 若已有帳號，可跳過此步，直接做第三步「登入」。

---

## 第三步：登入

1. 打開登入頁：  
   **https://finmindtrade.com/analysis/#/account/login**

2. 輸入你的 **信箱** 與 **密碼**，按登入。

---

## 第四步：進入「使用者資訊」頁取得 Token

1. 登入後，在網站裡找到 **「帳戶」**、**「使用者資訊」**、**「個人資料」** 或 **「API」** 等類似選單（每個網站版型可能不同）。

2. 進入後，畫面上會有一串 **API Token**（或「API 金鑰」），是一長串英文與數字，例如：  
   `eyJhbGciOiJIUzI1NiIsInR5cCI6...`

3. 點 **「複製」** 或手動全選後複製，把這串 Token 存到記事本或密碼管理員，**不要分享給別人**。

---

## 第五步：把 Token 填進本系統

### 本機執行（Streamlit 跑在自己電腦）

1. 在專案資料夾裡找到 **`.env`** 檔案（若沒有，可複製 `.env.example` 再改名為 `.env`）。

2. 用記事本或 VS Code 打開 `.env`，加入或修改這一行（把 `你的token` 換成你剛複製的那串）：  
   ```  
   FINMIND_TOKEN=你的token  
   ```  
   例如：  
   ```  
   FINMIND_TOKEN=eyJhbGciOiJIUzI1NiIsInR5cCI6...  
   ```

3. 存檔後**重新啟動** Streamlit（關掉再執行一次 `streamlit run app.py`）。

### Streamlit Cloud 部署

1. 打開 **https://share.streamlit.io**，登入後點進你的 App。

2. 右上角 **「Settings」** → **「Secrets」**。

3. 在欄位裡加上（把 `你的token` 換成你複製的 Token）：  
   ```toml  
   FINMIND_TOKEN = "你的token"  
   ```

4. 存檔後，Streamlit 會自動重新部署；等一兩分鐘再重新整理你的 App 頁面。

---

## 完成後

回到本系統的 **「交易輸入」** 頁，按 **「🔄 更新即時現價」**，報價卡應會顯示 **「資料來源：FinMind」**，數字即為真實行情（依 FinMind 更新頻率）；若仍顯示「模擬報價」，請確認 Token 已正確貼上、無多餘空格，且已重啟或重新部署。

---

## 常見問題

- **找不到 Token 在哪裡？**  
  登入後多點「帳戶 / 個人 / API / 使用者資訊」等選單，Token 通常在同一頁或「API 金鑰」區塊。

- **Token 貼上後還是模擬報價？**  
  請確認：變數名是 `FINMIND_TOKEN`、沒有多打空格、本機有重啟 Streamlit 或 Cloud 有重新部署。

- **請求次數限制**  
  有登入並使用 Token 時，每小時請求次數會較高（例如 600 次）；未帶 Token 則較低（例如 300 次）。本系統有 15 秒快取，一般使用不易超過。
