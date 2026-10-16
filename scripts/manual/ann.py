import time, json, base64, os
from login import start, goto
JS = r"""
window.__marks = window.__marks || [];
window.__find = (sel, txt, exact) => {
  const els = [...document.querySelectorAll(sel)].filter(e => e.offsetParent !== null);
  return els.find(e => exact ? e.innerText.trim() === txt : e.innerText.trim().includes(txt));
};
window.__box = (el, n, pad, pos) => {
  if (!el) return false;
  pad = pad ?? 6; const r = el.getBoundingClientRect();
  const d = document.createElement('div');
  Object.assign(d.style, {position:'fixed', left:(r.left-pad)+'px', top:(r.top-pad)+'px', width:(r.width+2*pad)+'px',
    height:(r.height+2*pad)+'px', border:'3px solid #E53935', borderRadius:'8px', zIndex:2000001, pointerEvents:'none'});
  document.body.appendChild(d);
  if (n) {
    const c = document.createElement('div'); c.innerText = n;
    const L = pos==='tr' ? r.right+pad-14 : pos==='l' ? r.left-pad-36 : r.left-pad-14;
    const T = pos==='l' ? r.top + r.height/2 - 15 : r.top-pad-14;
    Object.assign(c.style, {position:'fixed', left:L+'px', top:T+'px', width:'30px', height:'30px', borderRadius:'50%',
      background:'#E53935', color:'#fff', font:'bold 17px sans-serif', display:'flex', alignItems:'center',
      justifyContent:'center', zIndex:2000002, boxShadow:'0 1px 3px rgba(0,0,0,.4)'});
    document.body.appendChild(c);
  }
  return true;
};
window.__clear = () => document.querySelectorAll('body > div[style*="z-index: 2000001"], body > div[style*="z-index: 2000002"]').forEach(e=>e.remove());
"""
def prep(b):
    b.js(JS)
def full(b, w=1280):
    b.js("window.__clear && window.__clear()")
    h = b.js("document.querySelector('[data-testid=stMain]').scrollHeight")
    b.size(w, min(int(h or 900) + 40, 6000)); time.sleep(2.5)
    b.js(JS)
def box(b, find_expr, n, pad=6, pos="tl"):
    ok = b.js(f"window.__box(({find_expr}), {json.dumps(str(n)) if n else 'null'}, {pad}, {json.dumps(pos)})")
    if not ok: print("  !! 找不到", n, find_expr[:80])
def rect(b, find_expr):
    return b.js(f"(()=>{{const r=({find_expr}).getBoundingClientRect(); return [r.left,r.top,r.right,r.bottom]}})()")
def shot(b, name, x0, y0, x1, y1):
    d = b.cmd("Page.captureScreenshot", format="png", clip={"x": x0, "y": y0, "width": x1-x0, "height": y1-y0, "scale": 1})["data"]
    open(os.path.join("shots", name), "wb").write(base64.b64decode(d)); print("  saved", name)
# 找「同時包含所有文字」的最小元素
def smallest(*txts, sel="div"):
    cond = " && ".join(f"x.innerText.includes({json.dumps(t)})" for t in txts)
    return f"[...document.querySelectorAll('{sel}')].filter(x=>x.offsetParent&&x.innerText&&{cond}).sort((a,b)=>a.innerText.length-b.innerText.length)[0]"
def fill(b, css, v, wait=4):
    b.js(f"(()=>{{const el=document.querySelector({json.dumps(css)}); el.scrollIntoView({{block:'center'}}); el.focus(); el.select();}})()")
    time.sleep(0.3)
    for ch in v: b.cmd("Input.insertText", text=ch)
    b.cmd("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter", windowsVirtualKeyCode=13, nativeVirtualKeyCode=13, text="\r")
    b.cmd("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13)
    time.sleep(wait)
def box_els(b, els_expr, n, pad=6, pos="tl"):
    """框住一群元素的聯集範圍"""
    ok = b.js(f"""(()=>{{const els=({els_expr}).filter(Boolean); if(!els.length) return false;
      const rs=els.map(e=>e.getBoundingClientRect());
      const fake={{getBoundingClientRect:()=>({{left:Math.min(...rs.map(r=>r.left)),top:Math.min(...rs.map(r=>r.top)),
        right:Math.max(...rs.map(r=>r.right)),bottom:Math.max(...rs.map(r=>r.bottom)),
        width:Math.max(...rs.map(r=>r.right))-Math.min(...rs.map(r=>r.left)),height:Math.max(...rs.map(r=>r.bottom))-Math.min(...rs.map(r=>r.top))}})}};
      return window.__box(fake, {json.dumps(str(n)) if n else 'null'}, {pad}, {json.dumps(pos)});}})()""")
    if not ok: print("  !! 找不到群組", n)
def enter(b):
    b.cmd("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter", windowsVirtualKeyCode=13, nativeVirtualKeyCode=13, text=chr(13))
    b.cmd("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13)
def pick(b, select_input_expr, text):
    b.js(f"(()=>{{const i=({select_input_expr}); i.focus(); i.click();}})()"); time.sleep(1)
    for ch in text: b.cmd("Input.insertText", text=ch)
    time.sleep(1); enter(b); time.sleep(5)
    b.js('document.activeElement && document.activeElement.blur()'); time.sleep(1)
