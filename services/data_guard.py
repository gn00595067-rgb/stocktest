# -*- coding: utf-8 -*-
"""危險操作（大量刪除、灌假資料）的共用保護。

- is_production_data()：目前連的是不是正式資料（資料庫模式或試算表模式）。
  正式資料上不顯示「產生模擬數據」這類開發用按鈕。
- backup_before_destructive()：大量刪除前，先把資料庫整份匯出到 Google 試算表
  （含 trades_backup 與滾動備份分頁）。備份失敗就不准刪。
"""
from typing import Optional, Tuple


def is_production_data() -> bool:
    from db.database import DATABASE_URL, USE_GOOGLE_SHEET
    return bool(DATABASE_URL) or bool(USE_GOOGLE_SHEET)


def backup_before_destructive() -> Tuple[bool, Optional[str]]:
    """資料庫模式：先匯出一份到試算表再刪；回傳 (可以繼續, 錯誤訊息)。

    試算表模式不必另外備份：試算表本身就是正式資料，且寫回時有「大量縮水就中止」的防呆與滾動備份。
    本機檔案模式（開發用）不備份。
    """
    from db.database import DATABASE_URL, get_engine
    if not DATABASE_URL:
        return True, None
    try:
        from services.sheet_sync import is_google_sheet_enabled, sync_db_to_sheet
    except Exception as e:  # 缺套件
        return False, f"無法載入備份功能：{e}"
    if not is_google_sheet_enabled():
        return False, "沒有設定 Google 試算表憑證，無法先備份，為保護資料已取消刪除。"
    ok, err = sync_db_to_sheet(get_engine(), force=True)
    if not ok:
        return False, f"刪除前備份失敗，為保護資料已取消刪除：{err}"
    return True, None
