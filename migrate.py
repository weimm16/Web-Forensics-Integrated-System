# 数据库迁移原本存储在 WebPage 表中的图片信息迁移到独立的 WebImage 表中
from db import engine, Base
from models import WebPage, WebImage

print("开始创建/更新数据库表结构...")
Base.metadata.create_all(bind=engine)
print("数据库表结构创建完成！")


from db import get_session

with get_session() as s:
    pages = s.query(WebPage).filter(WebPage.phash.is_not(None), WebPage.phash != "").all()
    if pages:
        print(f"发现 {len(pages)} 条旧数据需要迁移...")
        for page in pages:
            # 创建对应的WebImage
            img = WebImage(
                page_id=page.id,
                image_url="legacy_image", 
                phash=page.phash,
                thumb_data=getattr(page, 'image', b''),
                order_index=0
            )
            s.add(img)

            page.phash = None
            page.image = None
        s.commit()
        print("旧数据迁移完成！")
    else:
        print("无需迁移旧数据。")