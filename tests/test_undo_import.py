# -*- coding: utf-8 -*-
"""復原匯入（2026-11-12 Elsa：短線不小心匯了兩次）：列批次、標疑似重複、整批刪除。"""
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, CustomMatchRule, Trade
from services import trade_ref_service as trs


def _sess():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def test_list_and_undo_duplicate_batch():
    s = _sess()
    d = date.today()
    k1, k2 = f"{d:%Y%m%d}090000", f"{d:%Y%m%d}093000"
    def add(u, side, price, qty):
        t = Trade(user=u, stock_id="2303", trade_date=d, side=side, price=price, quantity=qty)
        s.add(t); s.flush(); return t.id
    b1, s1 = add("短線", "BUY", 100, 1000), add("短線", "SELL", 105, 1000)
    trs.add_refs(s, "短線", [(d, "A1", b1, "import"), (d, "A2", s1, "import")], k1)
    s.add(CustomMatchRule(sell_trade_id=s1, buy_trade_id=b1, matched_qty=1000))
    b2, s2 = add("短線", "BUY", 100, 1000), add("短線", "SELL", 105, 1000)       # 重複匯入的那批
    trs.add_refs(s, "短線", [(d, "B1", b2, "import"), (d, "B2", s2, "import")], k2)
    s.add(CustomMatchRule(sell_trade_id=s2, buy_trade_id=b2, matched_qty=1000))
    keyed = add("短線", "BUY", 50, 1000)                                           # 手 key、這批只補書號
    trs.add_refs(s, "短線", [(d, "B3", keyed, "backfill")], k2)
    s.commit()
    bs = trs.list_import_batches(s)
    assert [(x["batch_id"], x["n"], x["dup"]) for x in bs] == [(k2, 2, 2), (k1, 2, 2)]
    assert trs.undo_batch(s, k2, "短線") == 2
    s.commit()
    assert sorted(t.id for t in s.query(Trade).all()) == sorted([b1, s1, keyed])   # 補書號的手 key 不刪
    assert [(r.sell_trade_id, r.buy_trade_id) for r in s.query(CustomMatchRule).all()] == [(s1, b1)]
    assert [(x["batch_id"], x["dup"]) for x in trs.list_import_batches(s)] == [(k1, 0)]


def test_same_order_no_twice_in_one_batch():
    """奇鋐 k02Zc 一張委託拆兩列：兩筆都要記到批次，復原才刪得乾淨；舊資料（只記到最後一筆）也要刪到。"""
    s = _sess()
    d = date.today()
    k = f"{d:%Y%m%d}100000"
    ids = []
    for q in (37, 13):
        t = Trade(user="短線", stock_id="3017", trade_date=d, side="BUY", price=2845, quantity=q)
        s.add(t); s.flush(); ids.append(t.id)
    trs.add_refs(s, "短線", [(d, "k02Zc", ids[0], "import"), (d, "k02Zc", ids[1], "import")], k)
    s.commit()
    assert sorted(o for _d, o, _t in trs.load_refs(s, "短線")) == ["k02Zc", "k02Zc#2"]
    assert trs.list_import_batches(s)[0]["n"] == 2
    # 舊資料：只剩一筆有書號，另一筆在同批 id 範圍內沒書號
    s2 = _sess()
    a = Trade(user="短線", stock_id="3017", trade_date=d, side="BUY", price=2845, quantity=37)
    b = Trade(user="短線", stock_id="3017", trade_date=d, side="BUY", price=2840, quantity=13)
    c = Trade(user="短線", stock_id="2303", trade_date=d, side="BUY", price=100, quantity=1000)
    s2.add_all([a, b, c]); s2.flush()
    trs.add_refs(s2, "短線", [(d, "X1", a.id, "import"), (d, "X2", c.id, "import")], k)
    s2.commit()
    assert trs.list_import_batches(s2)[0]["n"] == 3
    assert trs.undo_batch(s2, k, "短線") == 3
