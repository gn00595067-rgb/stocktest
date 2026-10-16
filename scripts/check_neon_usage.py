# -*- coding: utf-8 -*-
"""每日檢查 Neon 本月運算用量（規格：docs/specs/資料庫額度監控.md）。

超過門檻（預設 80 CU 小時，可用 NEON_ALERT_CU 調整）就以結束碼 1 結束，
讓 GitHub Actions 失敗 → GitHub 寄信通知。
需要環境變數 NEON_API_KEY；NEON_PROJECT_ID 選填。沒設 NEON_API_KEY 時略過。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from services.neon_usage import ALERT_CU_HOURS, FREE_CU_HOURS, fetch_usage


def main() -> int:
    key = (os.environ.get("NEON_API_KEY") or "").strip()
    if not key:
        print("沒有設定 NEON_API_KEY，略過檢查。")
        return 0
    alert = float(os.environ.get("NEON_ALERT_CU") or ALERT_CU_HOURS)
    u = fetch_usage(key, (os.environ.get("NEON_PROJECT_ID") or "").strip() or None)
    period = ""
    if u["period_start"] and u["period_end"]:
        period = f"（計費週期 {u['period_start']:%Y-%m-%d} ～ {u['period_end']:%Y-%m-%d}）"
    print(f"專案 {u['project_name']}：本月運算 {u['cu_hours']:.2f} / {FREE_CU_HOURS:.0f} CU 小時，"
          f"醒著 {u['active_hours']:.1f} 小時{period}")
    if u["cu_hours"] >= alert:
        print(f"::error::Neon 本月運算已用 {u['cu_hours']:.1f} CU 小時，超過提醒門檻 {alert:.0f}"
              f"（免費上限 {FREE_CU_HOURS:.0f}，用完資料庫會暫停）。請到 console.neon.tech 升級或降低運算上限。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
