import sys
import os
import threading
import webview
import json
import shutil
import zipfile
import datetime
import re
import bcrypt
import uuid
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from flask import Flask, render_template, send_from_directory, request, redirect, url_for, flash, session, jsonify, send_file, g

# 配置资源路径，确保打包后能正确找到模板和静态文件
base_dir = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, 'frozen', False):
    # 打包后的环境
    template_folder = os.path.join(sys._MEIPASS, 'templates')
    static_folder = os.path.join(sys._MEIPASS, 'static')
    games_folder = os.path.join(sys._MEIPASS, 'games')
    # 数据文件路径
    data_files = {
        'games': os.path.join(sys._MEIPASS, 'games.json'),
        'users': os.path.join(sys._MEIPASS, 'users.json'),
        'conversation': os.path.join(sys._MEIPASS, 'conversation.json'),
        'appeals': os.path.join(sys._MEIPASS, 'appeals.json')
    }
else:
    # 开发环境
    template_folder = os.path.join(base_dir, 'templates')
    static_folder = os.path.join(base_dir, 'static')
    games_folder = os.path.join(base_dir, 'games')
    data_files = {
        'games': os.path.join(base_dir, 'games.json'),
        'users': os.path.join(base_dir, 'users.json'),
        'conversation': os.path.join(base_dir, 'conversation.json'),
        'appeals': os.path.join(base_dir, 'appeals.json')
    }

# 确保数据文件可写的复制路径
user_data_dir = os.path.join(os.path.expanduser('~'), '.LVMO_GAME')
os.makedirs(user_data_dir, exist_ok=True)

# 复制只读数据文件到用户目录
for key, source_path in data_files.items():
    dest_path = os.path.join(user_data_dir, os.path.basename(source_path))
    if not os.path.exists(dest_path):
        try:
            shutil.copy2(source_path, dest_path)
        except:
            # 如果复制失败，创建空文件
            with open(dest_path, 'w', encoding='utf-8') as f:
                f.write('[]' if key in ['games', 'users', 'appeals'] else '{}')
    data_files[key] = dest_path

# 创建临时上传目录
temp_uploads_dir = os.path.join(user_data_dir, 'temp_uploads')
os.makedirs(temp_uploads_dir, exist_ok=True)

# 创建Flask应用
app = Flask(__name__, template_folder=template_folder, static_folder=static_folder)
app.config['SECRET_KEY'] = '8f7d3b9a1c2e4f0e9b8a7c6d5e4f3a2b'
app.config['PERMANENT_SESSION_LIFETIME'] = 60 * 30  # 30分钟
app.config['UPLOAD_FOLDER'] = games_folder
app.config['TEMP_UPLOADS'] = temp_uploads_dir
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024 * 1024  # 100GB
app.json.compact = False

# 全局请求预处理函数
@app.before_request
def check_user_status():
    # 排除登录和注册等不需要登录的页面
    excluded_routes = ['login', 'register', 'static', 'favicon']
    if request.endpoint in excluded_routes:
        return
    
    # 检查用户是否已登录
    if 'user_id' in session:
        user_id = session['user_id']
        # 获取用户当前的封禁状态
        is_banned, ban_reason, remaining_time, expire_time = is_user_banned(user_id)
        
        # 更新session中的封禁信息
        session['is_banned'] = is_banned
        if is_banned:
            session['ban_reason'] = ban_reason
            session['ban_remaining'] = remaining_time
            session['ban_expire'] = expire_time
        else:
            # 如果用户已被解封，清除相关session信息
            if 'ban_reason' in session:
                del session['ban_reason']
            if 'ban_remaining' in session:
                del session['ban_remaining']
            if 'ban_expire' in session:
                del session['ban_expire']

# 自定义413错误处理
@app.errorhandler(413)
@app.errorhandler(RequestEntityTooLarge)
def handle_large_file(error):
    print(f"捕获到413错误: {error}")
    print(f"当前MAX_CONTENT_LENGTH配置: {app.config.get('MAX_CONTENT_LENGTH')}")
    flash('上传的文件过大。服务器当前配置允许最大100GB的文件。如果问题仍然存在，请联系管理员。')
    return redirect(url_for('upload'))

# 确保必要的目录存在
def ensure_directories():
    for dir_path in [app.config['UPLOAD_FOLDER'], app.config['TEMP_UPLOADS']]:
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)

# 加载游戏列表
def load_games():
    try:
        with open(data_files['games'], 'r', encoding='utf-8') as f:
            games = json.load(f)
        # 确保每个游戏都有必要的字段
        for game in games:
            if 'downloads' not in game:
                game['downloads'] = 0
            if 'views' not in game:
                game['views'] = 0
        return games
    except:
        return []

# 保存游戏列表
def save_games(games):
    try:
        with open(data_files['games'], 'w', encoding='utf-8') as f:
            json.dump(games, f, ensure_ascii=False, indent=2)
        return True
    except:
        return False

# 加载用户列表
def load_users():
    try:
        with open(data_files['users'], 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        # 创建默认管理员用户
        default_users = [{
            'id': '1',
            'username': 'admin',
            'email': 'admin@example.com',
            'password': bcrypt.hashpw('admin'.encode('utf-8'), bcrypt.gensalt()).decode('utf-8'),
            'is_admin': True,
            'status': 'approved',
            'created_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }]
        with open(data_files['users'], 'w', encoding='utf-8') as f:
            json.dump(default_users, f, ensure_ascii=False, indent=4)
        return default_users

# 保存用户列表
def save_users(users):
    try:
        with open(data_files['users'], 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        return True
    except:
        return False

# 加载对话列表
def load_conversations():
    try:
        with open(data_files['conversation'], 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return []

# 保存对话列表
def save_conversations(conversations):
    try:
        with open(data_files['conversation'], 'w', encoding='utf-8') as f:
            json.dump(conversations, f, ensure_ascii=False, indent=2)
        return True
    except:
        return False

# 检查用户是否已登录
def is_logged_in():
    return 'user_id' in session

# 检查用户是否为管理员
def is_admin():
    if not is_logged_in():
        return False
    users = load_users()
    user = next((u for u in users if u['id'] == session['user_id']), None)
    return user and user.get('is_admin', False)

# 获取当前登录用户
def get_current_user():
    if not is_logged_in():
        return None
    users = load_users()
    return next((u for u in users if u['id'] == session['user_id']), None)

# 检查用户是否被封禁
def is_user_banned(user_id):
    users = load_users()
    user = next((u for u in users if u['id'] == user_id), None)
    if not user or 'ban_info' not in user:
        return False, None, None, None
    
    ban_info = user['ban_info']
    # 如果是永久封禁
    if ban_info['duration'] == 0:
        return True, ban_info['reason'], '永久', '永久封禁'
    
    # 检查封禁是否已过期
    ban_time = datetime.datetime.strptime(ban_info['ban_time'], '%Y-%m-%d %H:%M:%S')
    ban_duration = ban_info['duration']  # 秒数
    expire_time = ban_time + datetime.timedelta(seconds=ban_duration)
    now = datetime.datetime.now()
    
    if now > expire_time:
        # 封禁已过期，清除封禁信息
        del user['ban_info']
        save_users(users)
        return False, None, None, None
    
    # 计算剩余时间
    remaining_time = expire_time - now
    days, seconds = remaining_time.days, remaining_time.seconds
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    
    if days > 0:
        remaining_str = f"{days}天{hours}小时"
    elif hours > 0:
        remaining_str = f"{hours}小时{minutes}分钟"
    else:
        remaining_str = f"{minutes}分钟"
    
    return True, ban_info['reason'], remaining_str, expire_time.strftime('%Y-%m-%d %H:%M:%S')

# 获取用户之间的对话
def get_conversation(user_id1, user_id2):
    conversations = load_conversations()
    # 查找两个用户之间的对话
    for conv in conversations:
        if ((conv['user1_id'] == user_id1 and conv['user2_id'] == user_id2) or \
            (conv['user1_id'] == user_id2 and conv['user2_id'] == user_id1)):
            return conv
    # 如果不存在对话，则创建一个新的
    new_conversation = {
        'user1_id': user_id1,
        'user1_name': get_user_by_id(user_id1)['username'],
        'user2_id': user_id2,
        'user2_name': get_user_by_id(user_id2)['username'],
        'messages': [],
        'last_updated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    conversations.append(new_conversation)
    save_conversations(conversations)
    return new_conversation

# 通过用户ID获取用户信息
def get_user_by_id(user_id):
    users = load_users()
    return next((u for u in users if u['id'] == user_id), None)

# 格式化文件大小为人类可读的格式
def format_file_size(size_bytes):
    if size_bytes is None:
        return "未知"
    
    # 定义单位和对应的字节数
    units = [(1024 ** 3, 'GB'), (1024 ** 2, 'MB'), (1024, 'KB')]
    
    # 找出合适的单位
    for size, unit in units:
        if size_bytes >= size:
            return f"{size_bytes / size:.2f} {unit}"
    
    # 如果小于1KB，则返回字节数
    return f"{size_bytes} B"

# 计算游戏文件的总大小
def get_game_size(game_id):
    game_dir = os.path.join(app.config['UPLOAD_FOLDER'], game_id)
    
    if not os.path.exists(game_dir):
        return None
    
    total_size = 0
    
    # 遍历游戏目录下的所有文件和子目录
    for root, dirs, files in os.walk(game_dir):
        for file in files:
            file_path = os.path.join(root, file)
            try:
                # 获取文件大小并累加到总大小
                total_size += os.path.getsize(file_path)
            except OSError:
                # 处理文件无法访问的情况
                continue
    
    return total_size

# 首页 - 显示游戏列表
@app.route('/')
def index():
    games = load_games()
    current_user = get_current_user()
    
    # 获取搜索参数
    search_query = request.args.get('search', '').strip()
    
    # 如果有搜索参数，过滤游戏列表
    if search_query:
        search_lower = search_query.lower()
        games = [game for game in games if search_lower in game['title'].lower()]
    
    # 为每个游戏添加文件大小信息和浏览次数
    for game in games:
        # 添加文件大小信息
        game_size = get_game_size(game['id'])
        game['file_size'] = format_file_size(game_size)
        
        # 确保浏览次数字段存在，默认值为0
        if 'views' not in game:
            game['views'] = 0
    
    # 按下载次数降序排序，下载次数最高的显示在最上面
    games.sort(key=lambda x: x.get('downloads', 0), reverse=True)
    
    return render_template('index.html', games=games, current_user=current_user, search_query=search_query)

# 浏览游戏文件夹内容
@app.route('/browse/<game_id>')
def browse_game(game_id):
    games = load_games()
    game = next((g for g in games if g['id'] == game_id), None)
    
    if not game:
        flash('游戏不存在')
        return redirect(url_for('index'))
    
    # 检查游戏是否被封禁
    if game.get('is_banned', False):
        flash('该项目已被封禁，无法浏览')
        return redirect(url_for('index'))
    
    game_dir = os.path.join(app.config['UPLOAD_FOLDER'], game_id)
    
    # 检查目录是否存在
    if not os.path.exists(game_dir):
        flash('游戏文件夹不存在')
        return redirect(url_for('index'))
    
    # 更新浏览次数
    if 'views' not in game:
        game['views'] = 0
    game['views'] += 1
    save_games(games)
    
    # 获取文件夹内容
    items = []
    for item in os.listdir(game_dir):
        item_path = os.path.join(game_dir, item)
        is_dir = os.path.isdir(item_path)
        items.append({
            'name': item,
            'is_dir': is_dir,
            'size': os.path.getsize(item_path) if not is_dir else None,
            'path': item_path
        })
    
    # 按文件夹优先，然后按名称排序
    items.sort(key=lambda x: (not x['is_dir'], x['name']))
    
    return render_template('browse.html', game=game, items=items, current_user=get_current_user())

# 这里仅包含了部分关键功能
# 在实际实现中，需要添加所有的路由和功能函数

# 启动Flask服务器的函数
def start_flask_server():
    # 在非调试模式下运行Flask服务器
    app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)

# 主函数
def main():
    # 确保必要的目录存在
    ensure_directories()
    
    # 在新线程中启动Flask服务器
    flask_thread = threading.Thread(target=start_flask_server)
    flask_thread.daemon = True
    flask_thread.start()
    
    # 创建PyWebView窗口
    window = webview.create_window(
        title='LVMO_GAME',
        url='http://127.0.0.1:5000',
        width=1200,
        height=800,
        resizable=True
    )
    
    # 启动PyWebView主循环
    webview.start()

if __name__ == '__main__':
    main()