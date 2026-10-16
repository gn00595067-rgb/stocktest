import json, time, subprocess, base64, urllib.request, sys, os
import websocket, atexit
SP = os.path.dirname(os.path.abspath(__file__))
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

class B:
    def __init__(self, port=9444):
        self.p = subprocess.Popen([CHROME, "--headless=new", "--remote-allow-origins=*", f"--remote-debugging-port={port}",
                                   "--user-data-dir=" + os.path.join(SP, "chprof3"), "--window-size=1300,1000", "about:blank"])
        for _ in range(100):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json")); break
            except Exception: time.sleep(0.2)
        tab = [t for t in tabs if t["type"] == "page"][0]
        atexit.register(lambda: self.p.kill())
        self.ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60)
        self.i = 0
    def cmd(self, m, **p):
        self.i += 1; self.ws.send(json.dumps({"id": self.i, "method": m, "params": p}))
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == self.i: return r.get("result", r)
    def js(self, e):
        r = self.cmd("Runtime.evaluate", expression=e, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            print("  [js錯誤]", r["exceptionDetails"].get("exception", {}).get("description", "")[:200])
        return r.get("result", {}).get("value")
    def size(self, w, h=1000):
        self.cmd("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
    def go(self, url): self.cmd("Page.navigate", url=url)
    def wait(self, cond, t=40):
        for _ in range(int(t / 0.5)):
            if self.js(cond): return True
            time.sleep(0.5)
        return False
    def shot(self, name, full=True):
        m = self.cmd("Page.getLayoutMetrics")
        h = int(m["cssContentSize"]["height"]) if full else None
        kw = {"format": "png", "captureBeyondViewport": True}
        if h: kw["clip"] = {"x": 0, "y": 0, "width": m["cssLayoutViewport"]["clientWidth"], "height": min(h, 6000), "scale": 1}
        d = self.cmd("Page.captureScreenshot", **kw)["data"]
        open(os.path.join(SP, name), "wb").write(base64.b64decode(d))
    def type(self, selector, text):
        self.js(f"""(()=>{{const el=document.querySelector({json.dumps(selector)});
          const s=Object.getOwnPropertyDescriptor(el.__proto__,'value').set; s.call(el,{json.dumps(text)});
          el.dispatchEvent(new Event('input',{{bubbles:true}})); el.focus(); return 1;}})()""")
    def key_enter(self):
        for t in ("keyDown", "keyUp"):
            self.cmd("Input.dispatchKeyEvent", type=t, key="Enter", code="Enter", windowsVirtualKeyCode=13)
    def click_text(self, text, sel="button"):
        return self.js(f"""(()=>{{const b=[...document.querySelectorAll({json.dumps(sel)})].find(x=>x.innerText.trim().includes({json.dumps(text)}));
          if(!b) return false; b.scrollIntoView(); b.click(); return true;}})()""")
    def close(self): self.p.kill()
