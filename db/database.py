# -*- coding: utf-8 -*-
"""SQLite 連線與 Session（支援 DATABASE_URL 雲端可寫入、USE_GOOGLE_SHEET 試算表聯動）"""
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
except Exception:
    pass

# 是否啟用 Google 試算表聯動（持倉與沖銷資料存於試算表，程式重啟時從試算表載入）
USE_GOOGLE_SHEET = os.environ.get("USE_GOOGLE_SHEET", "").strip().lower() in ("1", "true", "yes")

# 雲端部署時可設 DATABASE_URL（如 postgresql://...），未設則用本機 SQLite 或記憶體（試算表模式）
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    # 常見雲端 DB 會給 postgres://，SQLAlchemy 1.4+ 需改為 postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = "postgresql://" + DATABASE_URL[9:]
    engine = create_engine(DATABASE_URL, echo=False)
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

Base.metadata.create_all(engine)
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
else:
    Session = scoped_session(sessionmaker(bind=engine, autocommit=False, autoflush=False))

_sheet_synced_once = False


def get_engine():
    """回傳目前使用的 engine（供手動同步到 Google 試算表等用途）。"""
    return engine


def get_session():
    global _sheet_synced_once
    if USE_GOOGLE_SHEET and not _sheet_synced_once:
        _sheet_synced_once = True
        try:
            from services.sheet_sync import sync_from_sheet_to_db
            ok, err = sync_from_sheet_to_db(engine)
            if not ok and err:
                try:
                    import streamlit as st
                    if hasattr(st, "warning"):
                        st.warning(f"無法從 Google 試算表載入：{err}")
                except Exception:
                    pass
        except Exception:
            pass
    return Session()
