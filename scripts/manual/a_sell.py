from ann import *
b = start(port=9560)
goto(b, "股票輸入（仿Yahoo）")
b.click_text("▸"); time.sleep(6)
ROW = "document.querySelector('input[placeholder=股數]').closest('[data-testid=stHorizontalBlock]')"
pick(b, f"{ROW}.querySelector('[data-testid=stSelectbox] input')", "賣出")
fill(b, "input[placeholder=股數]", "1000"); fill(b, "input[placeholder=股價]", "134", 6)
full(b)
t = rect(b, smallest("新增交易", sel="p,strong,div"))[1]
shot(b, "raw_sell.png", 256, t - 20, 1280, t + 900)
F="window.__find"
box(b, f"{ROW}.querySelector('[data-testid=stSelectbox]')", 1)
box(b, f"{ROW}.querySelector('[data-testid=stCheckbox]')", 2, 4, "tr")
box(b, smallest("接近均價", "賠少", sel="[data-testid=stRadio]"), 3)
box(b, smallest("近3天", "近5天", sel="[data-testid=stRadio]"), 4, 6, "tr")
box(b, "[...document.querySelectorAll('[data-testid=stDataFrame],[data-testid=stDataFrameResizable]')].filter(e=>e.offsetParent)[0]", 5)
box(b, smallest("已配", "預估", sel="[data-testid=stAlert]"), 6)
box(b, f"{F}('button','確認新增')", 7)
t = rect(b, smallest("新增交易", sel="p,strong,div"))[1]
bt = rect(b, f"{F}('button','確認新增')")[3]
shot(b, "y_sell.png", 300, t - 30, 1240, bt + 20)
b.js("window.__clear()")
# 交易明細：✏️ 修改、🗑 刪除
box(b, f"{F}('button','✏️',true)", 1, 4, "l")
box(b, f"{F}('button','🗑',true)", 2, 4, "tr")
t2 = rect(b, smallest("交易明細", sel="p,strong,div"))[1]
shot(b, "y_edit.png", 300, t2 - 15, 1240, t2 + 190)
