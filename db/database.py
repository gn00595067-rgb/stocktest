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
Session = scoped_session(sessionmaker(bind=engine, autocommit=False, autoflush=False))


def get_session():
    return Session()
