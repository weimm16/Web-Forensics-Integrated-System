# crawler.py
import hashlib
import datetime
import os
import io
import time
import random
import uuid
import requests
import socket
import chardet
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from PIL import Image
import imagehash
from models import WebPage, WebImage
from db import get_session
import yaml
import re

#反爬
class CrawlerConfig:

    def __init__(self):
        self.HEADERS_LIST = [
            {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebkit/537.36"},
            {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
            {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0"},
        ]
       
        self.REQUEST_DELAY = (1.0, 3.0)
        self.RETRY_TIMES = 3
        
        self.RETRY_DELAY = 5
        self.TIMEOUT = (10, 30)
    
    def get_headers(self):


        headers = random.choice(self.HEADERS_LIST).copy()
        headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
            "Referer": f"https://www.google.com/search?q={random.randint(1000, 9999)}",
        })
        return headers
    
    def get_image_headers(self, referer_url: str):

        headers = self.get_headers()
        headers.update({
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Referer": referer_url,
            "Sec-Fetch-Dest": "image",
            "Sec-Fetch-Mode": "no-cors",
        })
        return headers

# 全局配置实例
CRAWLER_CONFIG = CrawlerConfig()

def get_config():

    try:
        with open("config.yaml", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except:
        return {"max_depth": 1, "hamming_threshold": 5}

def get_ip(url: str) -> str:

    hostname = urlparse(url).hostname
    if not hostname:
        return ""
    try:
        return socket.gethostbyname(hostname)
    except:
        return ""

def fetch_with_retry(url: str, session: requests.Session, **kwargs): #带重试的请求

    
    
    for attempt in range(CRAWLER_CONFIG.RETRY_TIMES):
        try:
            if attempt > 0:
                delay = random.uniform(*CRAWLER_CONFIG.REQUEST_DELAY) * (attempt + 1)
                time.sleep(delay)
            
            resp = session.get(url, **kwargs)
            
            if resp.status_code == 403:
                print(f"[WARNING] 可能触发反爬: {url}")
            
            resp.raise_for_status()
            return resp
        
        
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] 请求失败 (尝试 {attempt + 1}/{CRAWLER_CONFIG.RETRY_TIMES}): {e}")
            if attempt == CRAWLER_CONFIG.RETRY_TIMES - 1:
                raise
            time.sleep(CRAWLER_CONFIG.RETRY_DELAY)
    return None

def is_image_url(url: str):

    if not url or url.startswith('data:'):
        return False
    
    parsed = urlparse(url)
    path = parsed.path.lower()
    
    # 扩展名检查
    image_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp', '.ico', '.tiff', '.jfif', '.avif')
    
    
    if any(path.endswith(ext) for ext in image_extensions):
        return True

    if any(k in parsed.netloc.lower() for k in ['img', 'image', 'cdn', 'pic']):
        return True
    
    return False

def is_valid_image_response(resp: requests.Response):

    # 检查Content-Type
    content_type = resp.headers.get('Content-Type', '').lower()
    if content_type and not content_type.startswith('image/'):
        return False
    
    # 检查内容长度
    if len(resp.content) < 100:
        return False
    
    try:
        img = Image.open(io.BytesIO(resp.content))
        img.verify()
        return True
    except:
        return False

def extract_images_from_css(style_str: str, base_url: str):

    images = []
    if not style_str:
        return images
    
    # 匹配背景图
    pattern = r'url\([\'"]?([^\'"\)]+)[\'"]?\)'
    matches = re.findall(pattern, style_str, re.IGNORECASE)
    
    for match in matches:
        img_url = urljoin(base_url, match.strip())
        if is_image_url(img_url):
            images.append(img_url)
    
    return images

def fetch_and_save(url: str, depth: int = 0, visited_urls: set = None):

    if visited_urls is None:
        visited_urls = set()
    
    
    indent = "  " * depth#美观
    config = get_config()
    max_depth = config.get("max_depth", 1)
    max_links_per_page = config.get("max_links_per_page", 10)
    
    
    if url in visited_urls:
        print(f"{indent}[SKIP] URL已访问过: {url[:80]}...") #返回前80个字符
        return
    visited_urls.add(url)
    
    print(f"{indent}[FETCH] 开始抓取: {url} (depth={depth}, max_depth={max_depth})")
    
    with requests.Session() as session: #复用TCP连接
        try:
            resp = fetch_with_retry(url, session, timeout=CRAWLER_CONFIG.TIMEOUT, allow_redirects=True)
            if not resp:
                print(f"{indent}[SKIP] 请求失败: {url}")
                return
        except Exception as e:
            print(f"{indent}[ERROR] fetch {url} -> {e}")
            return


    html_bytes = resp.content
    enc = chardet.detect(html_bytes)["encoding"] or "utf-8" #解码HTTP响应内容
    html = html_bytes.decode(enc, errors="replace")

    ip = get_ip(url)
    sha256 = hashlib.sha256(html.encode("utf-8")).hexdigest()
    ts = datetime.datetime.utcnow()

    #保存网页
    print(f"{indent}[SAVE] 保存新版本: {url}")

    #纯文本提取
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator=" ", strip=True)

    #保存网页基本信息到数据库
    with get_session() as s:
        page = WebPage(
            url=url,
            ip=ip,
            timestamp=ts,
            html=html,
            text=text,
            sha256=sha256,
        )
        s.add(page)
        s.flush()
        page_id = page.id

        #爬取并保存所有图片
        image_list = []
        
        #从<img>标签提取
        for idx, img_tag in enumerate(soup.find_all("img")):
            img_url = None
            
            #检查多个可能的属性
            possible_attrs = ["src", "data-src", "data-original", "data-lazy-src", 
                             "data-lazy", "data-srcset", "data-thumb", "original"]
            for attr in possible_attrs:
                if img_tag.get(attr):
                    img_url = img_tag.get(attr)
                    break
            
            if not img_url:
                continue
            
            #处理srcset
            if "srcset" in img_url:
                img_url = img_url.split(",")[0].split()[0]
            
            img_url = urljoin(url, img_url)#相对URL转绝对URL
            if "#" in img_url:
                img_url = img_url.split("#")[0]
            
            if not is_image_url(img_url):
                continue
            
            if img_url in image_list:
                continue
            
            try:
                img_resp = fetch_with_retry(
                    img_url, 
                    session, 
                    headers=CRAWLER_CONFIG.get_image_headers(url),
                    timeout=(8, 20)  #图片请求超时稍长
                )
                if not img_resp or img_resp.status_code != 200: #检查HTTP状态码是否为200
                    continue
                
                if not is_valid_image_response(img_resp):#验证响应头
                    continue
                
                pil_img = Image.open(io.BytesIO(img_resp.content)).convert("RGB")
                phash = imagehash.phash(pil_img)#计算感知哈希
                
                pil_img.thumbnail((320, 320))
                buf = io.BytesIO()#临时的二进制文件对象
                pil_img.save(buf, format="JPEG")
                thumb_bytes = buf.getvalue()
                
                
                web_image = WebImage(
                    page_id=page_id,
                    image_url=img_url,
                    phash=str(phash),
                    thumb_data=thumb_bytes,
                    order_index=len(image_list)
                )
                s.add(web_image)
                image_list.append(img_url) # 标记为已处理
                print(f"{indent}[IMG]成功: {img_url[:60]}...")
                
            except Exception as e:
                print(f"{indent}[IMG]失败: {img_url[:60]}... {str(e)[:30]}")
                continue
        
        #从CSS背景图片提取
        for idx, tag in enumerate(soup.find_all(style=True)):
            style = tag.get("style")
            bg_images = extract_images_from_css(style, url)
            
            for bg_img_url in bg_images:
                if bg_img_url in image_list:
                    continue
                
                try:
                    img_resp = fetch_with_retry(
                        bg_img_url,
                        session,
                        headers=CRAWLER_CONFIG.get_image_headers(url),
                        timeout=(8, 20)
                    )
                    if not img_resp or img_resp.status_code != 200:
                        continue
                    
                    if not is_valid_image_response(img_resp): #验证HTTP响应的内容是否确实是有效的图片
                        continue
                    
                    pil_img = Image.open(io.BytesIO(img_resp.content)).convert("RGB")
                    phash = imagehash.phash(pil_img)
                    
                    pil_img.thumbnail((320, 320))
                    buf = io.BytesIO()
                    pil_img.save(buf, format="JPEG")
                    thumb_bytes = buf.getvalue()
                    


                    web_image = WebImage(
                        page_id=page_id,
                        image_url=bg_img_url,
                        phash=str(phash),
                        thumb_data=thumb_bytes,
                        order_index=len(image_list)
                    )
                    s.add(web_image)
                    image_list.append(bg_img_url)
                    print(f"{indent}[BG]背景图: {bg_img_url[:60]}...")
                    
                except Exception as e:
                    continue
        
        s.commit()
        print(f"{indent}[SUCCESS] {url} 图片总数={len(image_list)}")

    #深度爬取
    if depth < max_depth:
        all_links = soup.find_all("a", href=True) #提取页面中所有超链接标签
        print(f"{indent}[DEEP] 发现 {len(all_links)} 个url...")
        links_processed = 0
        unique_urls = set()
        
        for a in all_links:
            try:
                href = a.get("href", "").strip()
                if not href:
                    continue
                
                next_url = urljoin(url, href)
                
                if not next_url.startswith(('http://', 'https://')):
                    continue
                
                if any(next_url.startswith(skip) for skip in ['javascript:', 'mailto:', 'tel:', '#', 'data:']):#过滤JavaScript、邮件、电话、锚点、Base64等特殊协议
                    continue
                
                if next_url in unique_urls:
                    continue
                unique_urls.add(next_url)
                
                print(f"{indent} └─ [{links_processed+1}] 爬取: {next_url[:80]}...")
                time.sleep(random.uniform(0.5, 1.5))
                fetch_and_save(next_url, depth + 1, visited_urls) #递归
                links_processed += 1
                
                if links_processed >= max_links_per_page:
                    break
                
            except Exception as e:
                continue
        
        print(f"{indent}[DEEP] 本次共处理 {links_processed} 个有效内链")
    else:
        print(f"{indent}[DEEP] 已达到最大深度 {max_depth}，不再继续爬取")