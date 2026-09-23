# -*- coding: utf-8 -*-
"""交易資料對帳工具：比對兩張 Google 試算表（或轉移前後）的 trades，列出任何遺失/多出的交易。

用途：任何一次資料轉移、程式更新、大量匯入前後，都先跑一次，用數字證明「零遺失、零弄錯」。

用法：
    python scripts/reconcile_sheets.py <來源SHEET_ID> <目標SHEET_ID>
憑證讀取順序與 app 相同：環境變數 GOOGLE_SHEET_CREDENTIALS / _B64。
比對鍵 =（買賣人, 股票代號, 交易日, 買賣別, 價格, 股數），與 id 無關，故即使 id 重編也對得準。
"""
import base64
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_creds():
    raw = os.environ.get("GOOGLE_SHEET_CREDENTIALS") or os.environ.get("GOOGLE_SHEET_CREDENTIALS_B64")
    if not raw:
        raise SystemExit("請先設定 GOOGLE_SHEET_CREDENTIALS 或 GOOGLE_SHEET_CREDENTIALS_B64")
    s = raw.strip()
    if s.startswith("{"):
        return json.loads(s)
    return json.loads(base64.b64decode(s).decode("utf-8"))


def _key(r):
    def num(x):
        try:
            return float(str(x).replace(",", ""))
        except (ValueError, TypeError):
            return x
    try:
        sid = str(int(float(r.get("stock_id"))))
    except (ValueError, TypeError):
        sid = str(r.get("stock_id")).strip()
    return (
        str(r.get("user")).strip(),
        sid,
        str(r.get("trade_date"))[:10],
        str(r.get("side")).strip().upper(),
        num(r.get("price")),
        int(num(r.get("quantity")) or 0),
    )


def reconcile(src_rows, dst_rows):
    """回傳 (只在來源有的, 只在目標有的)，皆為 [(key, 次數差), ...]。"""
    cs, cd = Counter(_key(r) for r in src_rows), Counter(_key(r) for r in dst_rows)
    only_src = [(k, cs[k] - cd.get(k, 0)) for k in cs if cs[k] > cd.get(k, 0)]
    only_dst = [(k, cd[k] - cs.get(k, 0)) for k in cd if cd[k] > cs.get(k, 0)]
    return only_src, only_dst


def _fetch(gc, sheet_id):
    return gc.open_by_key(sheet_id).worksheet("trades").get_all_records()


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    src_id, dst_id = sys.argv[1], sys.argv[2]
    import gspread
    from google.oauth2.service_account import Credentials
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    gc = gspread.authorize(Credentials.from_service_account_info(_load_creds(), scopes=scopes))
    src, dst = _fetch(gc, src_id), _fetch(gc, dst_id)
    only_src, only_dst = reconcile(src, dst)
    print(f"來源 {len(src)} 筆、目標 {len(dst)} 筆")
    if not only_src and not only_dst:
        print("✅ 完全一致，零遺失、零多出。")
        return
    if only_src:
        n = sum(c for _, c in only_src)
        print(f"\n❌ 只在【來源】有、目標缺少 {n} 筆（＝轉移/更新掉的）：")
        for k, c in sorted(only_src):
            print(f"    x{c}  {k[2]} {k[0]} {k[1]} {k[3]} qty={k[5]} price={k[4]}")
    if only_dst:
        n = sum(c for _, c in only_dst)
        print(f"\n⚠ 只在【目標】有、來源沒有 {n} 筆（新增或改動）：")
        for k, c in sorted(only_dst):
            print(f"    x{c}  {k[2]} {k[0]} {k[1]} {k[3]} qty={k[5]} price={k[4]}")


if __name__ == "__main__":
    main()
