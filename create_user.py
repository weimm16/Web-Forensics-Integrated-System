# create_user.py
from db import engine, get_db_path
from sqlalchemy import text
from werkzeug.security import generate_password_hash
import getpass
import os

def create_user_table():

    with engine.begin() as conn:
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS users (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        );
        """))
    print(f"用户表创建/确认完成")

def add_user(username: str, password: str):

    hashed_pwd = generate_password_hash(password)
    
    with engine.begin() as conn:
        result = conn.execute(
            text("INSERT OR IGNORE INTO users(username, password) VALUES (:u, :p)"),
            {"u": username, "p": hashed_pwd}
        )
        
        # 检查是否插入成功
        if result.rowcount > 0:
            print(f"用户 '{username}' 创建成功！")
        else:
            print(f" 用户 '{username}' 已存在，未重复创建")

def list_all_users():
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, username FROM users")).fetchall()
        if rows:
            print("\n当前用户列表：")
            for row in rows:
                print(f"  ID: {row[0]} | 用户名: {row[1]}")
        else:
            print("\n暂无用户")

def main():
    print("=" * 50)
    print("取证系统 - 用户创建工具")
    print("=" * 50)
    print(f"数据库路径: {get_db_path()}")
    print("-" * 50)
    
    # 确保表存在
    create_user_table()
    
    # 输入用户信息
    print("\n请输入新用户信息：")
    user = input("用户名: ").strip()
    if not user:
        print("用户名不能为空！")
        return
    
    pwd = getpass.getpass("密码: ")
    if not pwd:
        print("密码不能为空！")
        return
    
    confirm_pwd = getpass.getpass("确认密码: ")
    if pwd != confirm_pwd:
        print("两次输入的密码不一致！")
        return
    

    add_user(user, pwd)
    

    list_all_users()
    
    print("\n" + "=" * 50)
    print("操作完成！")
    print("=" * 50)

if __name__ == '__main__':
    main()