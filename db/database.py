# -*- coding: utf-8 -*-
"""SQLite 連線與 Session（支援 DATABASE_URL 雲端可寫入、USE_GOOGLE_SHEET 試算表聯動）"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from .models import Base

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
        engine = create_engine("sqlite:///:memory:", echo=False)
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
                sync_db_to_sheet(engine)
            except Exception:
                pass
    Session = scoped_session(sessionmaker(bind=engine, autocommit=False, autoflush=False, class_=_SheetSyncSession))
else:
    Session = scoped_session(sessionmaker(bind=engine, autocommit=False, autoflush=False))

_sheet_synced_once = False


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
