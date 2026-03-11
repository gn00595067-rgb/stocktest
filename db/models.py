# -*- coding: utf-8 -*-
"""SQLAlchemy 資料模型"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, Date, DateTime, ForeignKey, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Trade(Base):
    __tablename__ = "trades"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user = Column(String(50), nullable=False)
    stock_id = Column(String(20), nullable=False)
    trade_date = Column(Date, nullable=False)
    side = Column(String(10), nullable=False)  # BUY / SELL
    price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False)
    is_daytrade = Column(Boolean, default=False)
    fee = Column(Float, nullable=True)
    tax = Column(Float, nullable=True)
    note = Column(Text, nullable=True)


class StockMaster(Base):
    __tablename__ = "stock_master"
    stock_id = Column(String(20), primary_key=True)
    name = Column(String(100), nullable=True)
    industry_name = Column(String(100), nullable=True)
    market = Column(String(20), nullable=True)
    exchange = Column(String(20), nullable=True)
    is_etf = Column(Boolean, default=False)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Cashflow(Base):
    __tablename__ = "cashflows"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user = Column(String(50), nullable=False)
    stock_id = Column(String(20), nullable=False)
    date = Column(Date, nullable=False)
    type = Column(String(20), nullable=False)  # DIVIDEND / CASH_ADJ
    amount = Column(Float, nullable=False)
    memo = Column(Text, nullable=True)


class TradeMatch(Base):
    __tablename__ = "trade_matches"
    sell_trade_id = Column(Integer, ForeignKey("trades.id"), primary_key=True)
    buy_trade_id = Column(Integer, ForeignKey("trades.id"), primary_key=True)
    matched_qty = Column(Integer, nullable=False)
    buy_price = Column(Float, nullable=False)
    sell_price = Column(Float, nullable=False)
    pnl = Column(Float, nullable=False)
    policy = Column(String(20), nullable=False)


class CustomMatchRule(Base):
    """自定沖銷規則：指定某筆賣出與某筆買進的沖銷股數。"""
    __tablename__ = "custom_match_rules"
    sell_trade_id = Column(Integer, ForeignKey("trades.id"), primary_key=True)
    buy_trade_id = Column(Integer, ForeignKey("trades.id"), primary_key=True)
    matched_qty = Column(Integer, nullable=False)
