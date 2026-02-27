import os
import yaml
import datetime
import threading
import time
import io
import base64
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from werkzeug.security import check_password_hash
from sqlalchemy import text
from PIL import Image
import webbrowser
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from db import engine, get_session
from crawler import fetch_and_save
from models import WebPage, WebImage
from urllib.parse import urlparse, urlunparse
import imagehash

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", os.urandom(24).hex())
CONFIG_PATH = "config.yaml"


login_manager = LoginManager(app)
login_manager.login_view = 'login'


scheduler = None
scheduler_lock = threading.Lock()


class User(UserMixin):
    def __init__(self, uid, username):
        self.id = str(uid)
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    with engine.connect() as conn:
        row = conn.execute(text("SELECT id,username FROM users WHERE id=:id"), {"id": user_id}).first()
        return User(row[0], row[1]) if row else None


def scheduled_crawl_job():

    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
        
        seeds = config.get('seeds', [])
        print(f"[SCHEDULER] 开始执行定时任务，种子URL数量: {len(seeds)}")
        
        for url in seeds:
            try:
                fetch_and_save(url, depth=0)
                print(f"[SCHEDULER] 成功抓取: {url}")
            except Exception as e:
                print(f"[SCHEDULER] 抓取失败 {url}: {e}")
        
        build_index()
        print("[SCHEDULER] 索引重建完成")
    except Exception as e:
        print(f"[SCHEDULER] 任务执行失败: {e}")

def init_scheduler():
    global scheduler
    with scheduler_lock:
        if scheduler is None or not scheduler.running:
            scheduler = BackgroundScheduler()
            return True
    return False

def normalize_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        # 统小写协议
        scheme = parsed.scheme.lower()

        netloc = parsed.netloc
        if (scheme == 'http' and netloc.endswith(':80')) or \
           (scheme == 'https' and netloc.endswith(':443')):
            netloc = netloc.rsplit(':', 1)[0]
        

        path = parsed.path
        if not path:
            path = '/'
        
        # 规范化后的URL
        normalized = urlunparse((scheme, netloc, path, '', '', ''))
        return normalized
    except:
        return url

#路由
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u, p = request.form['username'], request.form['password']
        with engine.connect() as conn:
            row = conn.execute(text("SELECT id,password FROM users WHERE username=:u"), {"u": u}).first()
            if row and check_password_hash(row[1], p):
                login_user(User(row[0], u))
                return redirect(url_for('dashboard'))
        flash('用户名或密码错误')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def dashboard():
    return render_template('index.html')


@app.route('/api/search_text', methods=['POST'])
@login_required
def api_search_text():
    kw = request.form.get('keyword', '').strip()
    if not kw: 
        return jsonify({'ok': False, 'msg': '关键字为空'})
    
    # 查询数据库 - 返回所有版本，包括同一网页的不同时间版本
    with get_session() as s:
        pages = s.query(WebPage).filter(
            WebPage.text.contains(kw)
        ).order_by(WebPage.timestamp.desc()).limit(100).all()
        
        #返回所有匹配的网页版本
        results = []
        for page in pages:
            results.append({
                "url": page.url,
                "ip": page.ip,
                "timestamp": page.timestamp.strftime('%Y-%m-%d %H:%M:%S') if isinstance(page.timestamp, datetime.datetime) else str(page.timestamp),
                "sha256": page.sha256,
            })
    
    return jsonify({'ok': True, 'data': results})

@app.route('/api/search_img', methods=['POST'])
@login_required
def api_search_img():
    try:
        img = Image.open(io.BytesIO(request.files['img'].read()))
        target_hash = imagehash.phash(img)
        threshold = 5
        
        # 查询数据库 - 返回所有匹配的图片，包括同一网页的不同版本
        results = []
        seen_page_images = set()  # 用于去重同一页面内完全相同的图片
        
        with get_session() as s:
            images = s.query(WebImage).filter(
                WebImage.phash.is_not(None),
                WebImage.phash != ""
            ).yield_per(500)
            
            for img_db in images:
                try:
                    h_db = imagehash.hex_to_flathash(img_db.phash, hashsize=8)
                    distance = target_hash - h_db
                    
                    if distance <= threshold:
                        page = img_db.page
                        
                        # 生成唯一标识：页面URL + 图片URL + 时间戳
                        # 这样可以保留同一页面的不同时间版本，但避免重复数据
                        unique_key = f"{page.url}|{img_db.image_url}|{page.timestamp}"
                        if unique_key in seen_page_images:
                            continue
                        seen_page_images.add(unique_key)
                        
                        result = {
                            "url": page.url,
                            "image_url": img_db.image_url,
                            "phash": img_db.phash,
                            "order_index": img_db.order_index,
                            "hamming": distance,
                            "ip": page.ip,
                            "timestamp": page.timestamp.strftime('%Y-%m-%d %H:%M:%S') if isinstance(page.timestamp, datetime.datetime) else str(page.timestamp),
                            "sha256": page.sha256,
                        }
                        
                        # 添加缩略图数据
                        if img_db.thumb_data:
                            result["img_b64"] = base64.b64encode(img_db.thumb_data).decode()
                        
                        results.append(result)
                except Exception as e:
                    continue
            
            # 按汉明距离排序，然后按时间排序
            results = sorted(results, key=lambda x: (x["hamming"], x["timestamp"]), reverse=True)[:100]
        
        return jsonify({'ok': True, 'data': results})
    except Exception as e:
        return jsonify({'ok': False, 'msg': f'图片处理失败: {str(e)}'})

@app.route('/api/取证', methods=['POST'])
@login_required
def api_取证():
    url = request.form.get('url', '').strip()
    if not url.startswith(('http://', 'https://')):
        return jsonify({'ok': False, 'msg': 'URL 必须以 http/https 开头'})
    try:
        fetch_and_save(url, depth=0)
        
        # 查询最新
        with get_session() as s:
            page = s.query(WebPage).filter_by(url=url).order_by(WebPage.id.desc()).first()
            if not page:
                return jsonify({'ok': False, 'msg': '抓取失败'})
            
            images = s.query(WebImage).filter_by(page_id=page.id).order_by(WebImage.order_index).all()
            
            images_data = []
            for img in images:
                images_data.append({
                    'image_url': img.image_url,
                    'img_b64': base64.b64encode(img.thumb_data).decode() if img.thumb_data else '',
                    'phash': img.phash,
                })
            
            return jsonify({
                'ok': True,
                'data': {
                    'url': page.url,
                    'ip': page.ip,
                    'timestamp': page.timestamp.strftime('%Y-%m-%d %H:%M:%S') if isinstance(page.timestamp, datetime.datetime) else str(page.timestamp),
                    'sha256': page.sha256,
                    'images': images_data,
                }
            })
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


def init_config_file():

    default_config = {
        "data_dir": "./data",
        "hamming_threshold": 5,
        "max_depth": 1,
        "seeds": [],
        "schedule": {
            "type": "interval",
            "minutes": 60
        }
    }
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        yaml.safe_dump(default_config, f, allow_unicode=True, sort_keys=False)

@app.route('/api/schedule_config', methods=['GET'])
@login_required
def get_schedule_config():

    try:
        if not os.path.exists(CONFIG_PATH):
            init_config_file()
        
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
        
        config.setdefault('data_dir', './data')
        config.setdefault('hamming_threshold', 5)
        config.setdefault('max_depth', 1)
        config.setdefault('seeds', [])
        config.setdefault('schedule', {'type': 'interval', 'minutes': 60})
        
        return jsonify({'ok': True, 'data': config})
    except Exception as e:
        return jsonify({'ok': False, 'msg': f'读取配置失败: {str(e)}'})

@app.route('/api/schedule_config', methods=['POST'])
@login_required
def save_schedule_config():
    try:
        new_config = request.get_json()
        if not new_config:
            return jsonify({'ok': False, 'msg': '配置数据为空'})
        
        if not isinstance(new_config.get('seeds'), list):
            return jsonify({'ok': False, 'msg': '种子URL必须是数组'})
        if not new_config.get('schedule') or not new_config['schedule'].get('type'):
            return jsonify({'ok': False, 'msg': '执行方式配置不完整'})
        
        existing_config = {}
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                existing_config = yaml.safe_load(f) or {}
        
        existing_config['data_dir'] = new_config.get('data_dir', './data')
        existing_config['hamming_threshold'] = new_config.get('hamming_threshold', 5)
        existing_config['max_depth'] = new_config.get('max_depth', 1)
        existing_config['seeds'] = new_config['seeds']
        existing_config['schedule'] = new_config['schedule']
        
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            yaml.safe_dump(existing_config, f, allow_unicode=True, sort_keys=False)
        
        return jsonify({'ok': True, 'msg': '配置保存成功'})
    except Exception as e:
        return jsonify({'ok': False, 'msg': f'保存失败: {str(e)}'})


@app.route('/api/scheduler_status', methods=['GET'])
@login_required
def get_scheduler_status():
    try:
        status = {
            'running': scheduler is not None and scheduler.running,
            'last_run': None,
            'next_run': None
        }
        
        if status['running'] and scheduler.get_jobs():
            job = scheduler.get_jobs()[0]
            status['next_run'] = job.next_run_time.isoformat() if job.next_run_time else None
        
        return jsonify({'ok': True, 'data': status})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})

@app.route('/api/start_scheduler', methods=['POST'])
@login_required
def start_scheduler():

    global scheduler
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
        
        schedule_cfg = config.get('schedule', {})
        job_type = schedule_cfg.get('type', 'interval')
        
        init_scheduler()
        
        if scheduler.running:
            scheduler.remove_all_jobs()
        
        if job_type == 'interval':
            minutes = schedule_cfg.get('minutes', 60)
            trigger = IntervalTrigger(minutes=minutes)
        else:
            cron = schedule_cfg
            trigger = CronTrigger(
                minute=cron.get('minute', '*'),
                hour=cron.get('hour', '*'),
                day=cron.get('day', '*'),
                month=cron.get('month', '*'),
                day_of_week=cron.get('week', '*')
            )
        
        scheduler.add_job(scheduled_crawl_job, trigger, id='crawl_job')
        
        if not scheduler.running:
            scheduler.start()
        
        return jsonify({'ok': True, 'msg': '调度器启动成功'})
    except Exception as e:
        return jsonify({'ok': False, 'msg': f'启动失败: {str(e)}'})

@app.route('/api/stop_scheduler', methods=['POST'])
@login_required
def stop_scheduler():
    global scheduler
    try:
        if scheduler is None or not scheduler.running:
            return jsonify({'ok': False, 'msg': '调度器未运行'})
        
        scheduler.shutdown()
        scheduler = None
        
        return jsonify({'ok': True, 'msg': '调度器停止成功'})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})

def run_flask_app():
    app.run(debug=False, use_reloader=False, host='127.0.0.1', port=5000)

if __name__ == '__main__':
    if not os.path.exists(CONFIG_PATH):
        init_config_file()
    
    flask_thread = threading.Thread(target=run_flask_app, daemon=True)
    flask_thread.start()
    
    time.sleep(1)
    webbrowser.open("http://127.0.0.1:5000")
    flask_thread.join()