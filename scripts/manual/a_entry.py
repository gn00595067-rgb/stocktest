from ann import *
b = start(port=9559)
goto(b, "股票輸入（仿Yahoo）")
b.click_text("▸"); time.sleep(6)
fill(b, "input[placeholder=股數]", "1000"); fill(b, "input[placeholder=股價]", "133")
full(b)
F="window.__find"
box(b, "document.querySelector('input[placeholder=股數]').closest('[data-testid=stHorizontalBlock]').querySelector('[data-testid=stDateInput]')", 1)
box(b, "document.querySelector('input[placeholder=股數]').closest('[data-testid=stHorizontalBlock]').querySelector('[data-testid=stSelectbox]')", 2)
box(b, "document.querySelector('input[placeholder=股數]').closest('[data-testid=stNumberInput]')", 3)
box(b, "document.querySelector('input[placeholder=股價]').closest('[data-testid=stNumberInput]')", 4)
box_els(b, "(()=>{const row=document.querySelector('input[placeholder=股數]').closest('[data-testid=stHorizontalBlock]'); return [...row.querySelectorAll('.yh-td')];})()", 5, 6, "tr")
box(b, f"{F}('button','＋ 再加一筆買入')", 6)
box(b, f"{F}('button','確認新增')", 7)
top = rect(b, smallest("新增交易", sel="p,strong,div"))[1] - 60
bot = rect(b, f"{F}('button','確認新增')")[3] + 20
shot(b, "y_buy.png", 300, top - 40, 1240, bot)
