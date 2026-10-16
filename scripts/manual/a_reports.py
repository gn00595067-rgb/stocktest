from ann import *
F="window.__find"
QUICK = smallest("當日", "近3天", "全部", sel="[data-testid=stHorizontalBlock]")
def H(t): return smallest(t, sel="h1,h2,h3")
def DATES(): return "[...document.querySelectorAll('[data-testid=stDateInput]')].filter(e=>e.offsetParent)"
b = start(port=9561)

# 當日交易明細
goto(b, "當日交易明細"); full(b)
box(b, "document.querySelector('[role=tablist]')", 1)
box(b, smallest("日期", "股票", "買賣人", sel="[data-testid=stHorizontalBlock]"), 2)
box_els(b, f"[{H('交割加總')}, {smallest('交割應收', '當日已實現損益', sel='[data-testid=stHorizontalBlock]')}]", 3)
box_els(b, f"[{H('損益簡表')}, [...document.querySelectorAll('[data-testid=stDataFrame]')].filter(e=>e.offsetParent)[0]]", 4)
box_els(b, f"[{H('每筆明細')}, [...document.querySelectorAll('[data-testid=stDataFrame]')].filter(e=>e.offsetParent)[1]]", 5)
box(b, f"{F}('button','下載')", 6, 4, "tr")
t = rect(b, H("當日交易明細"))[1]; bt = rect(b, f"{F}('button','下載')")[3]
shot(b, "r_daily.png", 300, t - 10, 1240, bt + 15)

# 投資績效
goto(b, "投資績效"); full(b)
box(b, QUICK, 1)
box_els(b, f"{DATES()}", 2)
box(b, "[...document.querySelectorAll('[data-testid=stSelectbox]')].filter(e=>e.offsetParent&&e.innerText.includes('顯示模式'))[0]", 3)
box(b, "[...document.querySelectorAll('[data-testid=stMultiSelect]')].filter(e=>e.offsetParent)[0]", 4, 6, "tr")
box_els(b, f"[{smallest('未實現（目前）', '最差個股', sel='[data-testid=stHorizontalBlock]')}]", 5)
box_els(b, f"[{smallest('總損益', '平均單筆', sel='[data-testid=stHorizontalBlock]')}, {smallest('盈虧比', '最大單筆虧損', sel='[data-testid=stHorizontalBlock]')}]", 6)
t = rect(b, H("損益總覽"))[1]; bt = rect(b, smallest("盈虧比", "最大單筆虧損", sel="[data-testid=stHorizontalBlock]"))[3]
shot(b, "r_perf.png", 300, t - 10, 1240, bt + 20)

# 已實現損益（切到「當年」看比較多資料）
goto(b, "已實現損益")
b.click_text("當年"); time.sleep(8); full(b)
box(b, QUICK, 1)
box(b, smallest("總已實現損益", "報酬率"), 2)
box_els(b, f"[{smallest('勝率', '平均持有天數', sel='[data-testid=stHorizontalBlock],table,[data-testid=stMarkdownContainer]')}, {smallest('總賣出金額', '波段損益', sel='[data-testid=stHorizontalBlock],table,[data-testid=stMarkdownContainer]')}]", 3)
box_els(b, f"[{H('產業損益排名')}, [...document.querySelectorAll('[data-testid=stDataFrame]')].filter(e=>e.offsetParent)[0]]", 4)
box_els(b, f"[{H('個股損益排名')}, [...document.querySelectorAll('[data-testid=stDataFrame]')].filter(e=>e.offsetParent)[1]]", 5)
t = rect(b, H("已實現損益"))[1]; bt = rect(b, "[...document.querySelectorAll('[data-testid=stDataFrame]')].filter(e=>e.offsetParent)[1]")[3]
shot(b, "r_realized.png", 300, t - 10, 1240, bt + 20)

# 庫存損益
goto(b, "庫存損益"); full(b)
box(b, smallest("持倉市值", "未實現損益", "總報酬率", sel="[data-testid=stHorizontalBlock]"), 1)
box(b, smallest("這些數字怎麼算", sel="[data-testid=stExpander]"), 2)
box_els(b, f"[{smallest('持倉明細', sel='h1,h2,h3')}, [...document.querySelectorAll('[data-testid=stDataFrame]')].filter(e=>e.offsetParent)[0]]", 3)
box(b, smallest("底價怎麼算", sel="[data-testid=stExpander]"), 4)
t = rect(b, H("庫存損益"))[1]; bt = rect(b, smallest("底價怎麼算", sel="[data-testid=stExpander]"))[3]
shot(b, "r_stock.png", 300, t - 10, 1240, bt + 20)

# 個股明細（選台積電）
goto(b, "個股明細")
pick(b, "[...document.querySelectorAll('[data-testid=stSelectbox]')].filter(e=>e.offsetParent)[0].querySelector('input')", "2330")
full(b)
box(b, "[...document.querySelectorAll('[data-testid=stSelectbox]')].filter(e=>e.offsetParent)[0]", 1)
box_els(b, f"[{smallest('庫存股數', '結算後均價', sel='[data-testid=stHorizontalBlock]')}, [...document.querySelectorAll('[data-testid=stDataFrame]')].filter(e=>e.offsetParent)[0]]", 2)
box_els(b, f"[{H('已出售')}, [...document.querySelectorAll('[data-testid=stDataFrame]')].filter(e=>e.offsetParent)[1]]", 3)
box(b, f"{F}('button','匯出 Excel')", 4, 4, "tr")
t = rect(b, H("個股明細表"))[1]; bt = rect(b, f"{F}('button','匯出 Excel')")[3]
shot(b, "r_single.png", 300, t - 10, 1240, bt + 15)
