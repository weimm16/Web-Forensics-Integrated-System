import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base


DB_FILE = "forensic.db"
engine = create_engine(f"sqlite:///{DB_FILE}", echo=False, future=True)
Base.metadata.create_all(bind=engine)
Session = sessionmaker(bind=engine, expire_on_commit=False)

def get_session():
    return Session()#每次调用返回新的数据库会话

# 获取数据库绝对路径（
def get_db_path():
    return os.path.abspath(DB_FILE)