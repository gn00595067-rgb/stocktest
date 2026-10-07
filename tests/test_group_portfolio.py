# -*- coding: utf-8 -*-
"""股票輸入（仿 Yahoo）分頁：條件篩選、分頁內損益（以買進歸屬）、分頁存取、防呆。"""
from datetime import date
from types import SimpleNamespace as T

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.models import Base, Trade
import services.group_portfolio as gp


def t(id, sid, d, side, qty, price, user="雅雲姐", fee=0.0, tax=0.0):
    return T(id=id, stock_id=sid, trade_date=d, side=side, quantity=qty, price=price, user=user, fee=fee, tax=tax)


TRADES = [
    t(1, "2330", date(2026, 9, 30), "BUY", 1000, 1000),
    t(2, "2330", date(2026, 10, 1), "BUY", 1000, 1100),
    t(3, "2330", date(2026, 10, 2), "SELL", 1000, 1200),
    t(4, "2327", date(2026, 10, 1), "BUY", 2000, 600),
    t(5, "2327", date(2026, 10, 1), "BUY", 500, 600, user="Peggy姐"),
]


def test_filter_by_trader_date_and_list():
    g = gp.GroupSpec(id=1, trader="雅雲姐", name="1001起", start_date=date(2026, 10, 1))
    assert [x.id for x in gp.filter_trades(TRADES, g)] == [2, 3, 4]
    g2 = gp.GroupSpec(id=2, trader="雅雲姐", name="短線", stock_ids=["2327"], only_listed=True)
    assert [x.id for x in gp.filter_trades(TRADES, g2)] == [4]
    # 同一筆交易可同時出現在兩個分頁
    assert set(x.id for x in gp.filter_trades(TRADES, g)) & set(x.id for x in gp.filter_trades(TRADES, g2)) == {4}


def test_all_group_matches_full_engine():
    g = gp.builtin_all_group("雅雲姐")
    s = gp.summarize_group(TRADES, g, [], "CUSTOM_PLUS_FIFO", {"2330": {"price": 1300}}, {})
    r = {x["stock_id"]: x for x in s["rows"]}
    assert r["2330"]["qty"] == 1000 and r["2330"]["avg_cost"] == pytest.approx(1100)   # FIFO 先沖 9/30 那批
    assert r["2330"]["realized"] == pytest.approx(200 * 1000)
    assert r["2330"]["realized_pct"] == pytest.approx(20.0)
    assert r["2330"]["unrealized"] == pytest.approx(200 * 1000)
    assert r["2330"]["breakeven"] is not None and r["2330"]["breakeven"] >= 1100
    assert "2327" in r and r["2327"]["qty"] == 2000   # 別的買賣人（Peggy姐 那 500 股）不算進來


def test_start_date_group_counts_by_buy_lot():
    """以買進歸屬：10/01 起的分頁只算 10/01 起買進的批次。

    FIFO 下 10/02 賣出沖到的是 9/30 那批（分頁外）→ 分頁內已實現 0，10/01 那批 1000 股仍在。
    """
    g = gp.GroupSpec(id=1, trader="雅雲姐", name="1001起", start_date=date(2026, 10, 1))
    s = gp.summarize_group(TRADES, g, [], "CUSTOM_PLUS_FIFO", {}, {})
    r = {x["stock_id"]: x for x in s["rows"]}
    assert r["2330"]["qty"] == 1000 and r["2330"]["avg_cost"] == pytest.approx(1100)
    assert r["2330"]["realized"] == 0 and not r["2330"]["has_realized"]
    assert r["2330"]["n_trades"] == 2   # 分頁內列出的交易：10/01 買、10/02 賣
    # 自定沖銷改沖 10/01 那批 → 算進分頁的已實現
    s = gp.summarize_group(TRADES, g, [(3, 2, 1000)], "CUSTOM_PLUS_FIFO", {}, {})
    r = {x["stock_id"]: x for x in s["rows"]}
    assert r["2330"]["qty"] == 0 and r["2330"]["realized"] == pytest.approx(100 * 1000)
    # 賣出日在分頁結束日之後也算（沖到的是分頁內的買進）
    later = TRADES + [t(6, "2327", date(2026, 12, 1), "SELL", 2000, 650)]
    g2 = gp.GroupSpec(id=2, trader="雅雲姐", name="10月", start_date=date(2026, 10, 1), end_date=date(2026, 10, 31))
    r = {x["stock_id"]: x for x in gp.summarize_group(later, g2, [], "CUSTOM_PLUS_FIFO", {}, {})["rows"]}
    assert r["2327"]["qty"] == 0 and r["2327"]["realized"] == pytest.approx(50 * 2000)


def test_extra_listed_stock_shows_zero_row():
    g = gp.builtin_all_group("雅雲姐")
    s = gp.summarize_group(TRADES, g, [], "CUSTOM_PLUS_FIFO", {}, {}, extra_stock_ids=["2454"])
    r = {x["stock_id"]: x for x in s["rows"]}
    assert r["2454"]["qty"] == 0 and r["2454"]["n_trades"] == 0


@pytest.fixture
def sess():
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def test_group_crud_never_touches_trades(sess):
    sess.add(Trade(id=1, user="雅雲姐", stock_id="2330", trade_date=date(2026, 10, 1), side="BUY", price=1, quantity=1))
    sess.commit()
    gid, err = gp.create_group(sess, "雅雲姐", "1001起", start_date=date(2026, 10, 1))
    assert err is None
    gid2, _ = gp.create_group(sess, "雅雲姐", "10月短線", stock_ids=["2327"], only_listed=True)
    assert [g.name for g in gp.list_groups(sess, "雅雲姐")] == ["全部", "1001起", "10月短線"]
    assert gp.list_groups(sess, "Peggy姐")[0].name == "全部" and len(gp.list_groups(sess, "Peggy姐")) == 1
    gp.move_group(sess, "雅雲姐", gid2, -1)
    assert [g.name for g in gp.list_groups(sess, "雅雲姐")] == ["全部", "10月短線", "1001起"]
    gp.add_stock_to_group(sess, gid2, "6173")
    assert gp.list_groups(sess, "雅雲姐")[1].stock_ids == ["2327", "6173"]
    gp.delete_group(sess, gid)
    assert [g.name for g in gp.list_groups(sess, "雅雲姐")] == ["全部", "10月短線"]
    assert sess.query(Trade).count() == 1          # 刪分頁不動交易


def test_group_validation(sess):
    assert gp.create_group(sess, "雅雲姐", "  ")[1]
    assert gp.create_group(sess, "雅雲姐", "全部")[1]
    assert gp.create_group(sess, "雅雲姐", "x", start_date=date(2026, 10, 2), end_date=date(2026, 10, 1))[1]
    gp.create_group(sess, "雅雲姐", "A")
    assert "已經有" in gp.create_group(sess, "雅雲姐", "A")[1]
    assert gp.create_group(sess, "Peggy姐", "A")[1] is None      # 不同買賣人可同名


def test_validate_new_trade():
    e, w = gp.validate_new_trade("BUY", 0, 0, None, 0)
    assert len(e) == 2
    e, _ = gp.validate_new_trade("BUY", 1000, 25.0, prev_close=250.0, holding_qty=0)   # 打錯小數點
    assert e and "漲跌停" in e[0]
    e, _ = gp.validate_new_trade("BUY", 1000, 25.0, prev_close=250.0, holding_qty=0, is_today=False)
    assert e == []                                   # 補登過去的交易不套今天漲跌停
    e, _ = gp.validate_new_trade("SELL", 2000, 250, 250.0, holding_qty=1000)
    assert e and "超過目前持股" in e[0]
    e, w = gp.validate_new_trade("BUY", 500, 250, 250.0, holding_qty=0)
    assert e == [] and w and "零股" in w[0]


def test_validate_match_plan():
    assert gp.validate_match_plan([(1, 600, 1000), (2, 400, 500)], 1000) is None
    assert "還差" in gp.validate_match_plan([(1, 600, 1000)], 1000)
    assert "多了" in gp.validate_match_plan([(1, 1000, 1000), (2, 100, 500)], 1000)
    assert "只剩" in gp.validate_match_plan([(1, 1200, 1000)], 1200)
    assert "負數" in gp.validate_match_plan([(1, -1, 1000)], 0)


def test_validate_edit_trade():
    # 賣出改股數：可用持股要加回這筆原本的股數
    e, _ = gp.validate_edit_trade("SELL", 1000, 3000, 100.0, None, holding_qty=2000)
    assert e == []
    e, _ = gp.validate_edit_trade("SELL", 1000, 4000, 100.0, None, holding_qty=2000)
    assert any("超過目前持股" in x for x in e)
    # 買入 10000 改成 1000：持股 10000 → 1000，沒問題
    e, _ = gp.validate_edit_trade("BUY", 10000, 1000, 301.5, None, holding_qty=10000)
    assert e == []
    # 後面已賣掉 9500 股，買入改成 1000 會讓持股變負
    e, _ = gp.validate_edit_trade("BUY", 10000, 1000, 301.5, None, holding_qty=500)
    assert any("持股會變成" in x for x in e)
    e, _ = gp.validate_edit_trade("BUY", 1000, 0, 100.0, None, holding_qty=1000)
    assert any("請輸入股數" in x for x in e)
