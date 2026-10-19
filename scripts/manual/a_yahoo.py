from ann import *
b = start(port=9557)
goto(b, "股票輸入（仿Yahoo）"); full(b)
F = "window.__find"
box(b, smallest("示範帳戶", sel="button,[role=tab],p"), 1)
box(b, f"{F}('button','新增股票',true)", 2)
box(b, "document.querySelector('input[aria-label=搜尋股票]').closest('[data-baseweb=select]')", 3)
box(b, smallest("輸入日期", "今天", sel="[data-testid=stHorizontalBlock]"), 4)
box(b, "document.querySelector('.yh-cardwrap')", 5)
box(b, f"{F}('button','更新股價')", 6)
box(b, smallest("聯電", "2303") + ".closest('[data-testid=stHorizontalBlock]')", 7, 4, "tr")
box(b, "[...document.querySelectorAll('div,span')].filter(x=>x.offsetParent&&x.innerText&&x.innerText.trim().startsWith('底價')&&x.innerText.trim().length<6).sort((a,b)=>a.innerText.length-b.innerText.length)[0]", 8, 4)
top = rect(b, smallest("示範帳戶", sel="button,[role=tab],p"))[1] - 25
bot = rect(b, smallest("富邦金", "2881"))[3] + 8
print(top, bot)
shot(b, "y_overview.png", 300, top, 1240, bot)
