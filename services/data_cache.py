# -*- coding: utf-8 -*-
"""交易、沖銷規則、股票主檔的讀取快取：資料沒變（沒有任何 commit）就不重讀資料庫。

正式站連的是美國的 Neon，每次整頁重跑都整批讀 550 筆交易＋3,000 多檔主檔要好幾百毫秒；
這三樣只有在有人存檔時才會變，所以用 db.database.data_version() 判斷要不要重讀。
回傳的是已脫離 session 的物件，**呼叫端不可修改**（同一行程的所有使用者共用）。
"""
import threading

_lock = threading.Lock()
_cache = {"ver": None, "trades": None, "rules": None, "masters": None}


def load_trades_rules_masters():
    """回傳 (全部交易, 沖銷規則 [(sell,buy,qty)], {stock_id: StockMaster})；未做權限篩選。"""
    from db.database import get_session, data_version
    from db.models import Trade, CustomMatchRule, StockMaster

    ver = data_version()   # 先取版本再讀：讀的途中若有人存檔，下次版本不同就會重讀
    with _lock:
        if _cache["ver"] == ver and _cache["trades"] is not None:
            return _cache["trades"], _cache["rules"], _cache["masters"]
    sess = get_session()
    try:
        trades = sess.query(Trade).order_by(Trade.id).all()
        rules = [(r.sell_trade_id, r.buy_trade_id, r.matched_qty) for r in
                 sess.query(CustomMatchRule).order_by(CustomMatchRule.sell_trade_id, CustomMatchRule.buy_trade_id).all()]
        masters = {m.stock_id: m for m in sess.query(StockMaster).all()}
        sess.expunge_all()
    finally:
        sess.close()
    with _lock:
        _cache.update(ver=ver, trades=trades, rules=rules, masters=masters)
    return trades, rules, masters


def clear() -> None:
    with _lock:
        _cache.update(ver=None, trades=None, rules=None, masters=None)
