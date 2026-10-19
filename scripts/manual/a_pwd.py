from ann import *
F="window.__find"
from cdp import B
# 登入畫面
b = B(port=9562); b.size(1280, 900)
for _ in range(40):
    try:
        b.go("http://localhost:8601/")
        if b.wait("document.querySelectorAll('input[type=password]').length>0", 10): break
    except Exception: time.sleep(1)
b.js(__import__('ann').JS)
box(b, "document.querySelector('input[type=password]').closest('[data-testid=stTextInput]')", 1)   # 只用密碼登入
box(b, "document.querySelector('[data-testid=stCheckbox]')", 2)
box(b, f"{F}('button','登入')", 3)
t = rect(b, smallest("請先登入", sel="h1"))[1]; bt = rect(b, f"{F}('button','登入')")[3]
shot(b, "p_login.png", 300, t - 10, 1240, bt + 25)
b.js("window.__clear()")
b.type("input[type=password]", "demo1234")   # 只用密碼登入
time.sleep(0.5); b.click_text("登入"); time.sleep(12)
b.js(__import__('ann').JS)
# 側欄：View more → 帳號與權限
box(b, "[...document.querySelectorAll('[data-testid=stSidebar] *')].filter(e=>e.offsetParent&&e.innerText&&e.innerText.trim().startsWith('View')).sort((a,c)=>a.innerText.length-c.innerText.length)[0]", 1, 4, "tr")
shot(b, "p_side1.png", 0, 60, 256, 470)
b.js("window.__clear()")
b.js("[...document.querySelectorAll('[data-testid=stSidebar] *')].filter(e=>e.offsetParent&&e.innerText&&e.innerText.trim().startsWith('View')).sort((a,c)=>a.innerText.length-c.innerText.length)[0].click()"); time.sleep(2)
b.js(__import__('ann').JS)
box(b, "[...document.querySelectorAll('[data-testid=stSidebar] a')].find(a=>a.innerText.includes('帳號與權限'))", 2, 3, "tr")
shot(b, "p_side2.png", 0, 60, 256, 560)
b.js("window.__clear()")
b.click_text("帳號與權限", "a"); time.sleep(10)
full(b)
CARD = smallest("admin（管理者）", "重設密碼", sel="[data-testid=stVerticalBlockBorderWrapper],[data-testid=stVerticalBlock]")
box(b, "[...document.querySelectorAll('[data-testid=stTextInput]')].filter(e=>e.offsetParent&&e.innerText.includes('重設密碼'))[0]", 1)
box(b, f"{F}('button','儲存帳號設定')", 2, 6, "tr")
box(b, "[...document.querySelectorAll('input[type=text]')].filter(e=>e.offsetParent&&e.value==='admin')[0].closest('[data-testid=stTextInput]')", "✕", 4)
t = rect(b, smallest("帳號清單與權限", sel="h1,h2,h3"))[1]; bt = rect(b, "[...document.querySelectorAll('[data-testid=stTextInput]')].filter(e=>e.offsetParent&&e.innerText.includes('重設密碼'))[0]")[3]
shot(b, "p_card.png", 300, t - 10, 1240, bt + 30)
b.js("window.__clear()")
# 實際改一次（示範資料庫），拍成功訊息
b.js("window.__pw=[...document.querySelectorAll('[data-testid=stTextInput]')].filter(e=>e.offsetParent&&e.innerText.includes('重設密碼'))[0].querySelector('input'); window.__pw.id='pwreset'")
fill(b, "#pwreset", "demo5678", 2)
b.click_text("儲存帳號設定"); time.sleep(8)
full(b)
box(b, "[...document.querySelectorAll('[data-testid=stAlert]')].filter(e=>e.offsetParent)[0]", 3)
t = rect(b, smallest("帳號與權限管理", sel="h1"))[1]
shot(b, "p_done.png", 300, t - 10, 1240, t + 150)
