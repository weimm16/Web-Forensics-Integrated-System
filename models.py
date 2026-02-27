from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, LargeBinary, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

Base = declarative_base()

class WebPage(Base):
    __tablename__ = 'webpages'
    id = Column(Integer, primary_key=True)
    url = Column(String(2048), nullable=False, index=True)
    ip = Column(String(45))
    timestamp = Column(DateTime, server_default=func.now())
    html = Column(Text)
    text = Column(Text)
    sha256 = Column(String(64), index=True)
    
    # 关联多张图片
    images = relationship("WebImage", back_populates="page", cascade="all, delete-orphan")

class WebImage(Base):

    __tablename__ = 'webimages'
    id = Column(Integer, primary_key=True)
    page_id = Column(Integer, ForeignKey('webpages.id'), nullable=False)
    image_url = Column(String(2048), nullable=False)
    phash = Column(String(16))  # 16位十六进制phash
    thumb_data = Column(LargeBinary)  # 缩略图
    order_index = Column(Integer, default=0)  # 图片在页面中的顺序
    
    page = relationship("WebPage", back_populates="images")