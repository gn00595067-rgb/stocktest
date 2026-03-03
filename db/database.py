# -*- coding: utf-8 -*-
"""SQLite 連線與 Session"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from .models import Base

DB_PATH = os.environ.get("DB_PATH", "stock_analysis.db")
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
Base.metadata.create_all(engine)
Session = scoped_session(sessionmaker(bind=engine, autocommit=False, autoflush=False))


def get_session():
    return Session()
