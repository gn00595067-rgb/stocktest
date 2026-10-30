# -*- coding: utf-8 -*-
"""券商對帳單（證券 App 盤後匯出的成交明細 CSV）匯入：解析、判斷哪些已經在系統裡、當沖配對、賣出統一沖銷。

規格：docs/specs/對帳單匯入.md。全部是純函式，不碰資料庫；頁面負責讀資料、顯示、寫入。

一列對帳單在這裡是 dict：
  row_no（檔案列號，含表頭從 2 起）、seq（處理順序，日期舊→新、同日照檔案順序）、
  stock_id、stock_name、trade_date、side（BUY/SELL）、price、quantity、fee、tax、order_no、note
classify_rows 之後多 status／claim_trade_id／fee_update／review_with。
"""
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Tuple

from services.pnl_engine import Lot, compute_matches

# 狀態（預覽表顯示文字見 STATUS_LABEL）
NEW = "new"
DUP_IMPORT = "dup_import"
KEYED_EXACT = "keyed_exact"
KEYED_MERGED = "keyed_merged"
REVIEW = "review"
STATUS_LABEL = {
    NEW: "🆕 新增",
    DUP_IMPORT: "⏭️ 已匯入過",
    KEYED_EXACT: "✋ 已手 key",
    KEYED_MERGED: "✋ 已手 key（合併）",
    REVIEW: "⚠️ 要確認",
}

# 賣出統一沖銷方式（key 就是 pnl_engine 的純策略名稱）
SELL_STRATEGIES = {
    "FIFO": "先進先出（預設）",
    "CONSERVATIVE": "保守：成本高的先沖（帳上賺最少）",
    "OPTIMISTIC": "樂觀：成本低的先沖（帳上賺最多）",
    "MEAN": "均價：最接近平均成本的先沖",
    "NONE": "不指定：不寫沖銷規則，交給各頁面的計算方式",
}

MERGE_PRICE_TOL = 0.005   # 合併 key 的均價容許誤差

REQUIRED_COLUMNS = ["股名", "日期", "成交股數", "淨收付", "成交單價", "委託書號"]


# ---------------------------------------------------------------- 解析
def _norm_name(s) -> str:
    """股名比對用：全形轉半形、去空白、各種破折號統一（臻鼎－KY ＝ 臻鼎-KY）。"""
    s = unicodedata.normalize("NFKC", str(s or "")).upper()
    for ch in " 　‐‑‒–—―－":
        s = s.replace(ch, "-" if ch not in " 　" else "")
    return s


def build_name_map(masters: Iterable[Tuple[str, str]]) -> Dict[str, List[str]]:
    """[(stock_id, name)] → {正規化股名: [stock_id…]}。只做完全相同的比對，不猜（避免對錯檔）。"""
    out: Dict[str, List[str]] = defaultdict(list)
    for sid, name in masters:
        if name and sid and str(sid) not in out[_norm_name(name)]:
            out[_norm_name(name)].append(str(sid))
    return dict(out)


def _num(v) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s or s.lower() == "nan":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _date(v) -> Optional[date]:
    s = str(v or "").strip()
    m = re.match(r"^(\d{2,4})[/-](\d{1,2})[/-](\d{1,2})", s)
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    if y < 1911:
        y += 1911  # 民國年
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _cell(rec: dict, col: str) -> str:
    v = rec.get(col)
    s = "" if v is None else str(v).strip()
    return "" if s.lower() == "nan" else s


def parse_statement(records: List[dict], name_map: Dict[str, List[str]]) -> Tuple[List[dict], List[Tuple[int, str]]]:
    """records：CSV 每列一個 dict（欄名→文字，建議 pandas dtype=str 讀）。

    檔案數字一律照收（證券 App 匯出，不檢核、不重算手續費）。回傳 (rows 依處理順序排好, errors[(列號, 原因)])。
    """
    if not records:
        return [], [(0, "檔案沒有資料")]
    missing = [c for c in REQUIRED_COLUMNS if c not in records[0]]
    if missing:
        return [], [(0, f"不是證券 App 的成交明細格式：缺少欄位「{'」「'.join(missing)}」")]
    rows, errors, seen = [], [], set()
    for i, rec in enumerate(records):
        row_no = i + 2
        name = _cell(rec, "股名")
        cur = _cell(rec, "幣別")
        if cur and cur != "台幣":
            errors.append((row_no, f"{name}：幣別是「{cur}」，只匯入台幣交易"))
            continue
        ids = name_map.get(_norm_name(name), [])
        if not ids:
            errors.append((row_no, f"主檔找不到「{name}」，請先到「主檔設定」補上這檔股票"))
            continue
        if len(ids) > 1:
            errors.append((row_no, f"「{name}」在主檔有多個代號（{'、'.join(ids)}），請先修正主檔"))
            continue
        d = _date(rec.get("日期"))
        if not d:
            errors.append((row_no, f"{name}：日期看不懂「{_cell(rec, '日期')}」"))
            continue
        qty, net, price = _num(rec.get("成交股數")), _num(rec.get("淨收付")), _num(rec.get("成交單價"))
        if not qty or qty <= 0 or price is None or price <= 0 or not net:
            errors.append((row_no, f"{name}：股數、價格或淨收付是空的"))
            continue
        order_no = _cell(rec, "委託書號")
        if not order_no:
            errors.append((row_no, f"{name}：沒有委託書號"))
            continue
        side = "BUY" if net < 0 else "SELL"   # 淨收付：付錢＝買進、收錢＝賣出
        row = {
            "row_no": row_no, "stock_id": ids[0], "stock_name": name, "trade_date": d, "side": side,
            "price": float(price), "quantity": int(round(qty)),
            "fee": float(_num(rec.get("手續費")) or 0), "tax": float(_num(rec.get("交易稅")) or 0),
            "order_no": order_no, "note": _cell(rec, "備註") or None,
        }
        key = (d, order_no, row["stock_id"], side, row["price"], row["quantity"], row["fee"], row["tax"])
        if key in seen:   # 同一個檔裡一模一樣的列（重複下載拼檔）只算一次
            errors.append((row_no, f"{name} {order_no}：檔案裡重複出現，只算一次"))
            continue
        seen.add(key)
        rows.append(row)
    # 檔案是新到舊：改成日期舊→新、同日照檔案順序，前幾天買的才會先成為庫存
    rows.sort(key=lambda r: (r["trade_date"], r["row_no"]))
    for k, r in enumerate(rows):
        r["seq"] = k
    return rows, errors


# ---------------------------------------------------------------- 判斷哪些已在系統裡
def _side(t) -> str:
    return str(getattr(t, "side", "") or "").strip().upper()


def _d(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()


def _same_price(a, b) -> bool:
    return abs(float(a or 0) - float(b or 0)) < 1e-6


def _fee_update(trade, fee: float, tax: float) -> Optional[dict]:
    old_fee, old_tax = float(getattr(trade, "fee", None) or 0), float(getattr(trade, "tax", None) or 0)
    if abs(old_fee - fee) < 0.005 and abs(old_tax - tax) < 0.005:
        return None
    return {"trade_id": int(trade.id), "old_fee": old_fee, "old_tax": old_tax, "fee": fee, "tax": tax}


def classify_rows(rows: List[dict], trades: List, refs: List[Tuple[date, str, int]]) -> List[dict]:
    """trades：同一個買賣人的既有交易（只傳這個人的，所以不會被別人的手 key 影響）。
    refs：這個買賣人的書號紀錄 [(成交日, 書號, 交易 id)]。依規格 §4.2 標狀態，回傳新的 rows。
    """
    trade_ids = {int(t.id) for t in trades}
    live_refs = {(d, o) for d, o, tid in refs if tid in trade_ids}         # 交易被刪掉的書號不算
    ref_trade_ids = {tid for _d0, _o, tid in refs if tid in trade_ids}     # 已有書號的交易不再被認領
    out = [dict(r, status=None, claim_trade_id=None, fee_update=None, review_with=None) for r in rows]

    for r in out:
        if (r["trade_date"], r["order_no"]) in live_refs:
            r["status"] = DUP_IMPORT

    groups: Dict[tuple, List[dict]] = defaultdict(list)
    for r in out:
        if r["status"] is None:
            groups[(r["trade_date"], r["stock_id"], r["side"])].append(r)
    cands: Dict[tuple, List] = defaultdict(list)
    for t in sorted(trades, key=lambda x: int(x.id)):
        if int(t.id) in ref_trade_ids or _side(t) not in ("BUY", "SELL"):
            continue
        cands[(_d(t.trade_date), str(t.stock_id).strip(), _side(t))].append(t)

    for key, grp in groups.items():
        left = list(cands.get(key, []))
        # 1) 同價同量：一對一認領（同價同量有好幾筆就照數量配）
        for r in grp:
            hit = next((t for t in left if _same_price(t.price, r["price"]) and int(t.quantity or 0) == r["quantity"]), None)
            if hit is not None:
                left.remove(hit)
                r.update(status=KEYED_EXACT, claim_trade_id=int(hit.id), fee_update=_fee_update(hit, r["fee"], r["tax"]))
        rest = [r for r in grp if r["status"] is None]
        if not rest:
            continue
        if not left:
            for r in rest:
                r["status"] = NEW
            continue
        # 2) 合併 key：剩下的手 key 股數合計＝檔內剩下的合計，而且均價接近（差 0.5% 內，手 key 均價會四捨五入）。
        #    股數對、價格差很多 → 多半是手 key 打錯價格，交給人看，不默默留著錯的價格
        lq, rq = sum(int(t.quantity or 0) for t in left), sum(r["quantity"] for r in rest)
        l_avg = sum(float(t.price or 0) * int(t.quantity or 0) for t in left) / lq if lq else 0
        r_avg = sum(r["price"] * r["quantity"] for r in rest) / rq if rq else 0
        if lq == rq and r_avg and abs(l_avg - r_avg) <= r_avg * MERGE_PRICE_TOL:
            target = left[0]
            for r in rest:
                r.update(status=KEYED_MERGED, claim_trade_id=int(target.id))
            if len(left) == 1:   # 只有一筆手 key 才知道稅費該改到哪一筆
                rest[0]["fee_update"] = _fee_update(target, sum(r["fee"] for r in rest), sum(r["tax"] for r in rest))
            continue
        # 3) 對不起來：寧可停下來讓人看，也不要自動重複算
        desc = "、".join(f"{float(t.price):g} × {int(t.quantity or 0):,}" for t in left)
        for r in rest:
            r.update(status=REVIEW, review_with=desc)
    return out


# ---------------------------------------------------------------- 當沖與賣出沖銷
TMP_BASE = -10 ** 9


def assign_tmp_ids(rows: List[dict]) -> List[dict]:
    """要新增的列先給暫時 id（負數、照處理順序遞增：配對引擎同日用 id 排先後），寫入資料庫後再換成真 id。"""
    for r in rows:
        r["tmp_id"] = TMP_BASE + int(r["seq"])
    return rows


def _rule_used(rules: List[Tuple[int, int, int]]) -> Tuple[Dict[int, int], Dict[int, int]]:
    by_sell, by_buy = defaultdict(int), defaultdict(int)
    for s, b, q in rules:
        by_sell[int(s)] += int(q)
        by_buy[int(b)] += int(q)
    return by_sell, by_buy


def plan_daytrade(new_rows: List[dict], trades: List, rules: List[Tuple[int, int, int]]) -> Tuple[List[Tuple[int, int, int]], set]:
    """同日同股有買有賣 → 當沖互沖。new_rows 要先有 tmp_id（負數）；trades 是這個人的既有交易（含當天手 key 的）。

    至少一邊是這次新匯入的才配（既有交易之間不動）。既有交易扣掉已寫好的沖銷規則才算剩餘。
    回傳 (規則 [(賣 id, 買 id, 股數)]（id 可能是 tmp_id）, 要標當沖的 id 集合)。
    """
    used_sell, used_buy = _rule_used(rules)
    by_day: Dict[tuple, Dict[str, List[list]]] = defaultdict(lambda: {"BUY": [], "SELL": []})
    for t in sorted(trades, key=lambda x: int(x.id)):
        sd = _side(t)
        if sd not in ("BUY", "SELL"):
            continue
        rem = int(t.quantity or 0) - (used_buy if sd == "BUY" else used_sell).get(int(t.id), 0)
        if rem > 0:
            by_day[(_d(t.trade_date), str(t.stock_id).strip())][sd].append([int(t.id), rem, False])
    for r in sorted(new_rows, key=lambda x: x["seq"]):
        by_day[(r["trade_date"], r["stock_id"])][r["side"]].append([r["tmp_id"], r["quantity"], True])

    out, flagged = [], set()
    for _key, sides in by_day.items():
        buys, sells = sides["BUY"], sides["SELL"]
        if not buys or not sells or not any(x[2] for x in buys + sells):
            continue
        for s in sells:
            for b in buys:
                if s[1] <= 0:
                    break
                if b[1] <= 0 or not (s[2] or b[2]):
                    continue
                q = min(s[1], b[1])
                out.append((s[0], b[0], q))
                flagged.update((s[0], b[0]))
                s[1] -= q
                b[1] -= q
    return out, flagged


def plan_sell_matches(new_rows: List[dict], trades: List, rules: List[Tuple[int, int, int]],
                      strategy: str) -> Tuple[List[Tuple[int, int, int]], Dict[int, int]]:
    """當沖以外剩下的新賣出，照統一方式從庫存沖。rules 要包含既有規則＋剛排好的當沖規則。

    能沖的庫存＝既有交易用「自定＋先進先出」（頁面預設口徑）算完還剩的買進＋這次新匯入的買進。
    回傳 (規則, {賣出 tmp_id: 庫存不足股數})。strategy=NONE 不寫規則。
    """
    if strategy not in SELL_STRATEGIES or strategy == "NONE":
        return [], {}
    used_sell, _ = _rule_used(rules)
    out, short = [], {}
    stocks = {r["stock_id"] for r in new_rows if r["side"] == "SELL"}
    for sid in sorted(stocks):
        old = [t for t in trades if str(t.stock_id).strip() == sid and _side(t) in ("BUY", "SELL")]
        new = [r for r in new_rows if r["stock_id"] == sid]
        buys = [Lot(int(t.id), int(t.quantity or 0), float(t.price or 0), str(_d(t.trade_date))) for t in old if _side(t) == "BUY"]
        buys += [Lot(r["tmp_id"], r["quantity"], r["price"], str(r["trade_date"])) for r in new if r["side"] == "BUY"]
        old_sells = [Lot(int(t.id), int(t.quantity or 0), float(t.price or 0), str(_d(t.trade_date))) for t in old if _side(t) == "SELL"]
        ids = {b.trade_id for b in buys} | {s.trade_id for s in old_sells} | {r["tmp_id"] for r in new}
        my_rules = [(s, b, q) for s, b, q in rules if s in ids and b in ids]
        # 先算既有賣出（＋已排好的當沖規則）吃掉哪些買進
        new_sell_lots = [Lot(r["tmp_id"], 0, r["price"], str(r["trade_date"])) for r in new if r["side"] == "SELL"]
        taken = defaultdict(int)
        for m in compute_matches(buys, old_sells + new_sell_lots, "CUSTOM_PLUS_FIFO", custom_rules=my_rules):
            taken[m[0]] += m[2]
        for s, b, q in my_rules:   # 新賣出的當沖規則：sell lot 的 qty 是 0，上面沒算到，這裡補扣
            if s < 0:
                taken[b] += q
        remain = [Lot(b.trade_id, b.qty - taken.get(b.trade_id, 0), b.price, b.date) for b in buys]
        remain = [b for b in remain if b.qty > 0]
        to_match = [Lot(r["tmp_id"], r["quantity"] - used_sell.get(r["tmp_id"], 0), r["price"], str(r["trade_date"]))
                    for r in sorted(new, key=lambda x: x["seq"]) if r["side"] == "SELL"]
        to_match = [s for s in to_match if s.qty > 0]
        if not to_match:
            continue
        got = defaultdict(int)
        for b_id, s_id, q, *_ in compute_matches(remain, to_match, strategy):
            out.append((s_id, b_id, q))
            got[s_id] += q
        for s in to_match:
            if got[s.trade_id] < s.qty:
                short[s.trade_id] = s.qty - got[s.trade_id]
    return out, short
