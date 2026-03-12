# -*- coding: utf-8 -*-
"""SQLite 連線與 Session（支援 DATABASE_URL 雲端可寫入）"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from .models import Base

# 雲端部署時可設 DATABASE_URL（如 postgresql://...），未設則用本機 SQLite
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    # 常見雲端 DB 會給 postgres://，SQLAlchemy 1.4+ 需改為 postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = "postgresql://" + DATABASE_URL[9:]
    engine = create_engine(DATABASE_URL, echo=False)
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
Session = scoped_session(sessionmaker(bind=engine, autocommit=False, autoflush=False))


def get_session():
    return Session()
