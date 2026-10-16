import time, sys
sys.stdout.reconfigure(encoding='utf-8')
from cdp import B
def start(port=9556, w=1280, h=900):
    b = B(port=port); b.size(w, h)
    for _ in range(40):
        try:
            b.go("http://localhost:8601/")
            if b.wait("document.querySelectorAll('input[type=password]').length>0", 10): break
        except Exception: time.sleep(1)
    b.type("input[type=text]", "admin"); b.type("input[type=password]", "demo1234")
    time.sleep(0.5); b.click_text("登入"); time.sleep(12)
    return b
def goto(b, name, wait=12):
    b.click_text(name, "a"); time.sleep(wait)
