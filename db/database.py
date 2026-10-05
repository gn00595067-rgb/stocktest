# -*- coding: utf-8 -*-
"""資料庫連線與 Session：設 DATABASE_URL → Postgres（正式資料庫）；否則 USE_GOOGLE_SHEET 試算表模式或本機 SQLite"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import StaticPool
from .models import Base

# 雲端部署時 Secrets 可能尚未同步到 os.environ，先從 st.secrets 補上（避免頁面先於 app.py 載入時用錯 engine）
try:
    import streamlit as st
    if hasattr(st, "secrets") and st.secrets:
        if st.secrets.get("USE_GOOGLE_SHEET"):
            os.environ.setdefault("USE_GOOGLE_SHEET", str(st.secrets["USE_GOOGLE_SHEET"]).strip())
        if st.secrets.get("GOOGLE_SHEET_ID"):
            os.environ.setdefault("GOOGLE_SHEET_ID", str(st.secrets["GOOGLE_SHEET_ID"]).strip())
        if st.secrets.get("GOOGLE_SHEET_CREDENTIALS"):
            c = st.secrets.get("GOOGLE_SHEET_CREDENTIALS")
            if isinstance(c, str):
                os.environ.setdefault("GOOGLE_SHEET_CREDENTIALS", c.strip())
            else:
                import json
                os.environ.setdefault("GOOGLE_SHEET_CREDENTIALS", json.dumps(c))
        if st.secrets.get("GOOGLE_SHEET_CREDENTIALS_B64"):
            os.environ.setdefault("GOOGLE_SHEET_CREDENTIALS_B64", str(st.secrets["GOOGLE_SHEET_CREDENTIALS_B64"]).strip())
        if st.secrets.get("DATABASE_URL"):
            os.environ.setdefault("DATABASE_URL", str(st.secrets["DATABASE_URL"]).strip())
except Exception:
    pass

# 雲端部署時可設 DATABASE_URL（如 postgresql://...），未設則用本機 SQLite 或記憶體（試算表模式）
DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip() or None

# 是否以 Google 試算表為正式資料來源（啟動時從試算表載入、每次 commit 寫回試算表）。
# 設了 DATABASE_URL 就以資料庫為準：試算表只當備份（另由匯出腳本寫入），
# 不再載入也不再自動寫回——兩邊都當正式資料會互相覆蓋。
USE_GOOGLE_SHEET = (
    os.environ.get("USE_GOOGLE_SHEET", "").strip().lower() in ("1", "true", "yes")
    and not DATABASE_URL
)


def normalize_database_url(url: str) -> str:
    """常見雲端 DB 會給 postgres://，SQLAlchemy 1.4+ 需改為 postgresql://"""
    url = url.strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def create_schema(target_engine) -> None:
    """建立資料表。Postgres 刻意比照 SQLite 時期的寬鬆行為，避免換資料庫後原本能存的資料存不進去：
      - 不建外鍵限制：SQLite 從未檢查外鍵，既有的失效沖銷規則（指向已刪交易）才能原樣保留。
      - 文字欄位不限長度：SQLite 不檢查 String(n) 長度，主檔裡已有超過 20 字的代號
        （如類股指數 BiotechnologyMedicalCare），Postgres 照長度檢查會整批寫入失敗。
    """
    if target_engine.dialect.name == "sqlite":
        Base.metadata.create_all(target_engine)
        return
    from sqlalchemy import MetaData, ForeignKeyConstraint, String
    md = MetaData()
    for t in Base.metadata.sorted_tables:
        t2 = t.to_metadata(md)
        for c in list(t2.constraints):
            if isinstance(c, ForeignKeyConstraint):
                t2.constraints.discard(c)
        for col in t2.columns:
            col.foreign_keys.clear()
            if isinstance(col.type, String) and getattr(col.type, "length", None):
                col.type = String()
    md.create_all(target_engine)


if DATABASE_URL:
    # pool_pre_ping：Neon 閒置會休眠、連線會被關掉，取用前先確認，避免醒來第一次操作報錯
    engine = create_engine(
        normalize_database_url(DATABASE_URL), echo=False, pool_pre_ping=True, pool_recycle=300,
    )
else:
    if USE_GOOGLE_SHEET:
        # StaticPool：單一連線共用，避免多執行緒時每人一個 :memory: 導致「no such table」
        # check_same_thread=False 允許同一連線在多執行緒使用（Streamlit 腳本跑在不同 thread）
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            echo=False,
        )
    else:
        DB_PATH = os.environ.get("DB_PATH", "stock_analysis.db")
        engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)

create_schema(engine)
# 若 custom_match_rules 尚無 created_at 欄位則補上（既有資料庫遷移）
try:
    from sqlalchemy import text
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            r = conn.execute(text("PRAGMA table_info(custom_match_rules)"))
            cols = [row[1] for row in r]
            if "created_at" not in cols:
                conn.execute(text("ALTER TABLE custom_match_rules ADD COLUMN created_at DATETIME"))
                conn.commit()
        else:
            r = conn.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'custom_match_rules' AND column_name = 'created_at'"
            ))
            if r.fetchone() is None:
                conn.execute(text("ALTER TABLE custom_match_rules ADD COLUMN created_at TIMESTAMP"))
                conn.commit()
except Exception:
    pass

# 試算表模式：Session 在 commit 後自動寫回試算表
if USE_GOOGLE_SHEET:
    from sqlalchemy.orm import Session as _BaseSession
    class _SheetSyncSession(_BaseSession):
        def commit(self):
            super().commit()
            try:
                from services.sheet_sync import sync_db_to_sheet
                ok, err = sync_db_to_sheet(engine)
                if not ok and err:
                    try:
                        import streamlit as st
                        if hasattr(st, "warning"):
                            st.warning(f"已寫入資料庫，但同步到 Google 試算表失敗：{err}")
                    except Exception:
                        pass
            except Exception as e:
                try:
                    import streamlit as st
                    if hasattr(st, "warning"):
                        st.warning(f"已寫入資料庫，但同步到 Google 試算表時發生錯誤：{e}")
                except Exception:
                    pass
    Session = scoped_session(sessionmaker(bind=engine, autocommit=False, autoflush=False, class_=_SheetSyncSession))
elif DATABASE_URL:
    from sqlalchemy.orm import Session as _BaseSession

    class _TimedSession(_BaseSession):
        """資料庫模式：記錄每次存檔耗時（Streamlit Cloud log 看得到），切換後量測實際速度用。"""
        def commit(self):
            import time as _time
            t0 = _time.monotonic()
            super().commit()
            print(f"[db] 存檔完成，耗時 {_time.monotonic() - t0:.2f} 秒", flush=True)
    Session = scoped_session(sessionmaker(bind=engine, autocommit=False, autoflush=False, class_=_TimedSession))
else:
    Session = scoped_session(sessionmaker(bind=engine, autocommit=False, autoflush=False))

_sheet_synced_once = False

# 資料版本：任何 ORM commit 後 +1，給讀取快取判斷「資料有沒有變」（services/data_cache.py）。
# 加上行程代號：Streamlit 部署後重新載入本模組時計數歸零，代號不同就不會誤用舊快取。
import uuid as _uuid
from sqlalchemy import event as _event
from sqlalchemy.orm import Session as _OrmSession
_DATA_TOKEN = _uuid.uuid4().hex
_DATA_COUNTER = 0


def _bump_data_version(_session) -> None:
    global _DATA_COUNTER
    _DATA_COUNTER += 1


_event.listen(_OrmSession, "after_commit", _bump_data_version)


def data_version():
    return (_DATA_TOKEN, _DATA_COUNTER)


def get_engine():
    """回傳目前使用的 engine（供手動同步到 Google 試算表等用途）。"""
    return engine


def get_session():
    global _sheet_synced_once
    if USE_GOOGLE_SHEET and not _sheet_synced_once:
        try:
            from services.sheet_sync import sync_from_sheet_to_db
            ok, err = sync_from_sheet_to_db(engine)
            if ok:
                # 只有「載入成功」才標記已同步；失敗則下次 get_session 會重試，
                # 避免載入失敗後記憶體停在空狀態、又被寫回覆蓋試算表。
                _sheet_synced_once = True
            elif err:
                try:
                    import streamlit as st
                    if hasattr(st, "warning"):
                        st.warning(f"無法從 Google 試算表載入：{err}（將於下次重試，暫不寫回以免覆蓋既有資料）")
                except Exception:
                    pass
        except Exception:
            pass
    return Session()
