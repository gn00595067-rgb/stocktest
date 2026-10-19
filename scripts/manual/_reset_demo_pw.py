# -*- coding: utf-8 -*-
"""a_pwd.py 會把示範帳號密碼改掉；跑完用這支改回 demo1234。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from sqlalchemy import create_engine, text
from services.auth_service import hash_password
e = create_engine("sqlite:///" + os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo.db"))
with e.begin() as c:
    c.execute(text("update user_accounts set password_hash=:h where username='老闆'"), {"h": hash_password("demo1234")})
print("示範密碼已改回 demo1234")
