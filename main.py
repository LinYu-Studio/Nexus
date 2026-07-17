import os
import json
import time
import zipfile
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash, session, jsonify, send_file, g
from werkzeug.exceptions import RequestEntityTooLarge
import bcrypt
import os
import uuid
import shutil
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
# 使用随机生成的SECRET_KEY提高安全性
app.config['SECRET_KEY'] = '8f7d3b9a1c2e4f0e9b8a7c6d5e4f3a2b'
# 配置session cookie，设置登录状态在退出浏览器后保持30分钟
app.config['PERMANENT_SESSION_LIFETIME'] = 60 * 30
app.config['UPLOAD_FOLDER'] = 'games/'
app.config['STATIC_FOLDER'] = 'static/'
app.config['TEMPLATES_FOLDER'] = 'templates/'
# 设置一个极大的上传文件大小限制（100GB）
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024 * 1024  # 100GB
# 确保JSON序列化支持更大的文件大小
app.json.compact = False


# 全局请求预处理函数，在每次请求前检查用户封禁状态
@app.before_request
def check_user_status():
    # 初始化应用（如果尚未初始化）
    if not hasattr(app, 'online_users'):
        init_app()
    
    # 自动审核待处理作品（每30秒内最多执行一次）
    auto_review_pending_games()
    
    # 排除登录和注册等不需要登录的页面
    excluded_routes = ['login', 'register', 'static', 'favicon']
    if request.endpoint in excluded_routes:
        return
    
    # 检查用户是否已登录
    if 'user_id' in session:
        user_id = session['user_id']
        # 更新在线用户状态
        app.online_users.add(user_id)
        
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

# 提供根目录中的收款码文件访问
@app.route('/<path:filename>')
def serve_root_file(filename):
    # 只允许访问特定的收款码文件，增强安全性
    allowed_files = ['支付宝收款.jpg', '微信收款.png']
    if filename in allowed_files:
        return send_file(filename, mimetype='image/jpeg' if filename.endswith('.jpg') else 'image/png')
    # 对于其他文件，返回404错误
    return '文件不存在', 404

# 确保必要的目录存在
def ensure_directories():
    for dir_path in [app.config['UPLOAD_FOLDER'], app.config['STATIC_FOLDER'], app.config['TEMPLATES_FOLDER']]:
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)
    # 确保临时上传目录存在
    ensure_temp_uploads_exists()

# 加载游戏列表
def load_games():
    games_file = 'games.json'
    if not os.path.exists(games_file):
        return []
    with open(games_file, 'r', encoding='utf-8') as f:
        games = json.load(f)
    # 为旧数据兼容
    for game in games:
        if 'origin' not in game:
            game['origin'] = 'repost'
        if 'release_status' not in game:
            game['release_status'] = 'released'
        if 'release_date' not in game:
            game['release_date'] = None
        if 'min_specs' not in game:
            game['min_specs'] = {}
        if 'rec_specs' not in game:
            game['rec_specs'] = {}
    return games

# 加载用户列表
def load_users():
    users_file = 'users.json'
    if not os.path.exists(users_file):
        # 创建默认管理员用户
        default_users = [
            {
                'id': '1',
                'username': 'admin',
                'email': 'admin@example.com',
                'password': bcrypt.hashpw('admin'.encode('utf-8'), bcrypt.gensalt()).decode('utf-8'),
                'is_admin': True,
                'wallet_balance': 0.0,
                'purchased_games': [],
                'status': 'approved',
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        ]
        with open(users_file, 'w', encoding='utf-8') as f:
            json.dump(default_users, f, ensure_ascii=False, indent=4)
        return default_users
    with open(users_file, 'r', encoding='utf-8') as f:
        users = json.load(f)
        # 确保所有用户都有钱包余额和已购买游戏字段
        for user in users:
            if 'wallet_balance' not in user:
                user['wallet_balance'] = 0.0
            if 'purchased_games' not in user:
                user['purchased_games'] = []
            if 'is_developer' not in user:
                user['is_developer'] = False
            if 'developer_name' not in user:
                user['developer_name'] = None
            if 'developer_application' not in user:
                user['developer_application'] = None
        return users

# 保存用户列表
def save_users(users):
    with open('users.json', 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=4)

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
    user = next((u for u in users if u['id'] == session['user_id']), None)
    # 管理员自动拥有开发者权限
    if user and user.get('is_admin'):
        user['is_developer'] = True
    return user

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
    ban_time = datetime.strptime(ban_info['ban_time'], '%Y-%m-%d %H:%M:%S')
    ban_duration = ban_info['duration']  # 秒数
    expire_time = ban_time + timedelta(seconds=ban_duration)
    now = datetime.now()
    
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

# 保存游戏列表
def save_games(games):
    with open('games.json', 'w', encoding='utf-8') as f:
        json.dump(games, f, ensure_ascii=False, indent=4)

# 自动审核：检查待审核作品是否超过12小时
def auto_review_pending_games():
    try:
        games = load_games()
        now = datetime.now()
        changed = False
        
        for game in games:
            if game.get('review_status') != 'pending_review':
                continue
            
            submit_time_str = game.get('review_submit_time')
            if not submit_time_str:
                continue
            
            try:
                submit_time = datetime.strptime(submit_time_str, '%Y-%m-%d %H:%M:%S')
            except:
                continue
            
            # 超过12小时自动审核
            if (now - submit_time).total_seconds() >= 12 * 3600:
                # 模拟安全检测
                import random
                risk_keywords = ['virus', 'malware', 'trojan', 'worm', 'ransomware',
                                 'spyware', 'keylogger', 'backdoor', 'rootkit',
                                 'crack', 'keygen', 'patch', 'injector', 'exploit',
                                 'payload', 'shellcode', 'rat', 'botnet']
                filename_lower = (game.get('filename') or '').lower()
                has_risk = any(kw in filename_lower for kw in risk_keywords)
                
                if not has_risk:
                    has_risk = random.random() < 0.15  # 15%概率随机风险
                
                game['review_status'] = 'auto_approved'
                game['auto_reviewed'] = True
                game['review_time'] = now.strftime('%Y-%m-%d %H:%M:%S')
                game['reviewed_by'] = '系统自动审核'
                game['review_notes'] = '超过12小时未人工审核，系统自动审核通过'
                
                if has_risk:
                    game['security_status'] = 'risk'
                    game['review_notes'] = '超过12小时未人工审核，系统自动审核通过（检测到潜在风险）'
                else:
                    game['security_status'] = 'passed'
                    game['security_check_time'] = now.strftime('%Y-%m-%d %H:%M:%S')
                
                changed = True
        
        if changed:
            save_games(games)
    except Exception as e:
        print(f"自动审核出错: {e}")

# 加载对话列表
def load_conversations():
    conversations_file = 'conversation.json'
    if not os.path.exists(conversations_file):
        return []
    with open(conversations_file, 'r', encoding='utf-8') as f:
        return json.load(f)

# 保存对话列表
def save_conversations(conversations):
    with open('conversation.json', 'w', encoding='utf-8') as f:
        json.dump(conversations, f, ensure_ascii=False, indent=4)

# 分块上传处理函数
def handle_chunked_upload(file_id, chunk, chunk_index, total_chunks, relative_path=None):
    """处理文件分块上传并在完成后合并"""
    # 创建临时目录存储分块
    temp_dir = os.path.join('temp_uploads', file_id)
    os.makedirs(temp_dir, exist_ok=True)
    
    # 保存当前分块
    chunk_path = os.path.join(temp_dir, f'chunk_{chunk_index}')
    with open(chunk_path, 'wb') as f:
        chunk.save(f)
    
    # 如果提供了相对路径，保存路径信息
    if relative_path and chunk_index == 0:
        path_info_file = os.path.join(temp_dir, 'path_info.txt')
        with open(path_info_file, 'w', encoding='utf-8') as f:
            f.write(relative_path)
    
    # 检查是否所有分块都已上传完成
    if chunk_index == total_chunks - 1:
        # 所有分块上传完成，开始合并
        merged_file_path = os.path.join('temp_uploads', f'{file_id}.merged')
        
        with open(merged_file_path, 'wb') as merged_file:
            for i in range(total_chunks):
                chunk_path = os.path.join(temp_dir, f'chunk_{i}')
                if os.path.exists(chunk_path):
                    with open(chunk_path, 'rb') as chunk_file:
                        merged_file.write(chunk_file.read())
                    os.remove(chunk_path)  # 删除已合并的分块
        
        # 读取相对路径信息
        path_info_file = os.path.join(temp_dir, 'path_info.txt')
        if os.path.exists(path_info_file):
            with open(path_info_file, 'r', encoding='utf-8') as f:
                stored_path = f.read().strip()
            # 将路径信息保存到文件，供upload_final使用
            path_store_file = os.path.join('temp_uploads', f'{file_id}.path')
            with open(path_store_file, 'w', encoding='utf-8') as f:
                f.write(stored_path)
        
        # 删除临时目录及其内容
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        
        return merged_file_path
    
    return None

# 确保临时上传目录存在
def ensure_temp_uploads_exists():
    if not os.path.exists('temp_uploads'):
        os.makedirs('temp_uploads')

# 获取用户之间的对话
def get_conversation(user_id1, user_id2):
    conversations = load_conversations()
    # 查找两个用户之间的对话
    for conv in conversations:
        if ((conv['user1_id'] == user_id1 and conv['user2_id'] == user_id2) or 
            (conv['user1_id'] == user_id2 and conv['user2_id'] == user_id1)):
            return conv
    # 如果不存在对话，则创建一个新的
    new_conversation = {
        'user1_id': user_id1,
        'user1_name': get_user_by_id(user_id1)['username'],
        'user2_id': user_id2,
        'user2_name': get_user_by_id(user_id2)['username'],
        'messages': [],
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    conversations.append(new_conversation)
    save_conversations(conversations)
    return new_conversation

# 通过用户ID获取用户信息
def get_user_by_id(user_id):
    users = load_users()
    return next((u for u in users if u['id'] == user_id), None)

# 发送消息
def send_message(sender_id, receiver_id, content):
    conversations = load_conversations()
    conversation = get_conversation(sender_id, receiver_id)
    
    # 创建新消息
    new_message = {
        'sender_id': sender_id,
        'sender_name': get_user_by_id(sender_id)['username'],
        'content': content,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    # 更新对话
    for conv in conversations:
        if ((conv['user1_id'] == sender_id and conv['user2_id'] == receiver_id) or 
            (conv['user1_id'] == receiver_id and conv['user2_id'] == sender_id)):
            conv['messages'].append(new_message)
            conv['last_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            break
    
    save_conversations(conversations)
    return new_message

# 加载通知
def load_notifications():
    try:
        with open('notifications.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return []

# 保存通知
def save_notifications(notifications):
    try:
        with open('notifications.json', 'w', encoding='utf-8') as f:
            json.dump(notifications, f, ensure_ascii=False, indent=2)
        return True
    except:
        return False

# 检查用户是否在线
def is_user_online(user_id):
    # 在实际环境中，这里应该有更复杂的在线状态检测逻辑
    # 这里简化处理，检查是否有用户的会话信息
    # 注意：这只是一个基本实现，实际项目中可能需要使用WebSocket或其他技术实现实时在线检测
    return user_id in app.online_users if hasattr(app, 'online_users') else False

# 创建通知
def create_notification(user_id, title, content, notification_type='info'):
    notifications = load_notifications()
    
    # 创建新通知
    new_notification = {
        'id': str(len(notifications) + 1),
        'user_id': user_id,
        'title': title,
        'content': content,
        'type': notification_type,
        'is_read': False,
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    notifications.append(new_notification)
    save_notifications(notifications)
    return new_notification

# 获取用户未读通知
def get_unread_notifications(user_id):
    notifications = load_notifications()
    return [n for n in notifications if n['user_id'] == user_id and not n['is_read']]

# 将通知标记为已读
def mark_notification_as_read(notification_id):
    notifications = load_notifications()
    for notification in notifications:
        if notification['id'] == notification_id:
            notification['is_read'] = True
            save_notifications(notifications)
            return True
    return False

# 初始化应用时设置在线用户集合
def init_app():
    app.online_users = set()

# 搜索用户
def search_users(query):
    users = load_users()
    query = query.lower()
    return [u for u in users if query in u['username'].lower() or query in u['email'].lower()]

# 格式化文件大小为人类可读的格式
def format_file_size(size_bytes):
    """将字节大小转换为KB, MB, GB等人类可读格式"""
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
    """计算指定游戏ID的文件总大小"""
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
    
    # 过滤：只显示已审核上架的作品（非原创或审核通过的）
    if current_user and current_user.get('is_admin'):
        pass  # 管理员可见所有
    else:
        games = [g for g in games if g.get('origin') != 'original' or g.get('review_status') in ('approved', 'auto_approved')]
    
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

# 邮箱页面
@app.route('/mailbox')
def mailbox():
    if not is_logged_in():
        flash('请先登录')
        return redirect(url_for('login'))
    
    current_user = get_current_user()
    conversations = load_conversations()
    
    # 获取当前用户参与的所有对话
    user_conversations = []
    for conv in conversations:
        if conv['user1_id'] == current_user['id'] or conv['user2_id'] == current_user['id']:
            # 确定对话对象
            if conv['user1_id'] == current_user['id']:
                other_user_id = conv['user2_id']
                other_user_name = conv['user2_name']
            else:
                other_user_id = conv['user1_id']
                other_user_name = conv['user1_name']
            
            # 获取最新消息
            latest_message = conv['messages'][-1] if conv['messages'] else None
            
            user_conversations.append({
                'conversation_id': f"{min(conv['user1_id'], conv['user2_id'])}_{max(conv['user1_id'], conv['user2_id'])}",
                'other_user_id': other_user_id,
                'other_user_name': other_user_name,
                'latest_message': latest_message,
                'last_updated': conv['last_updated'],
                'message_count': len(conv['messages'])
            })
    
    # 按最后更新时间排序
    user_conversations.sort(key=lambda x: x['last_updated'], reverse=True)
    
    return render_template('mailbox.html', conversations=user_conversations, current_user=current_user)

# 搜索用户页面
@app.route('/search_users', methods=['GET', 'POST'])
def search_users_route():
    if not is_logged_in():
        flash('请先登录')
        return redirect(url_for('login'))
    
    current_user = get_current_user()
    results = []
    query = ''
    
    if request.method == 'POST':
        query = request.form.get('query', '').strip()
        if query:
            results = search_users(query)
            # 排除当前用户自己
            results = [u for u in results if u['id'] != current_user['id']]
        else:
            flash('请输入搜索内容')
    
    return render_template('search_users.html', results=results, query=query, current_user=current_user)

# 与特定用户的对话页面
@app.route('/conversation/<user_id>', methods=['GET', 'POST'])
def conversation(user_id):
    if not is_logged_in():
        flash('请先登录')
        return redirect(url_for('login'))
    
    current_user = get_current_user()
    other_user = get_user_by_id(user_id)
    
    if not other_user:
        flash('用户不存在')
        return redirect(url_for('mailbox'))
    
    # 加载对话
    conv = get_conversation(current_user['id'], user_id)
    
    if request.method == 'POST':
        content = request.form.get('content', '').strip()
        if content:
            # 检查用户是否被封禁
            if session.get('is_banned'):
                flash('您的账户已被封禁，无法发送消息')
                return redirect(url_for('conversation', user_id=user_id))
            send_message(current_user['id'], user_id, content)
            return redirect(url_for('conversation', user_id=user_id))
        else:
            flash('消息内容不能为空')
    
    return render_template('conversation.html', conversation=conv, other_user=other_user, current_user=current_user)

# 注册页面
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        # 获取表单数据
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        # 验证表单数据
        if not username or not email or not password or not confirm_password:
            flash('请填写所有必填字段')
            return redirect(url_for('register'))
            
        if password != confirm_password:
            flash('两次输入的密码不一致')
            return redirect(url_for('register'))
            
        # 检查用户名和邮箱是否已存在
        users = load_users()
        if any(u['username'] == username for u in users):
            flash('用户名已存在')
            return redirect(url_for('register'))
            
        if any(u['email'] == email for u in users):
            flash('邮箱已被注册')
            return redirect(url_for('register'))
            
        # 创建新用户（待审批状态）
        new_user_id = str(len(users) + 1)
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        new_user = {
            'id': new_user_id,
            'username': username,
            'email': email,
            'password': hashed_password,
            'is_admin': False,
            'wallet_balance': 0.0,
            'purchased_games': [],
            'status': 'pending',  # 待审批状态
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        users.append(new_user)
        save_users(users)
        
        # 向管理员发送提示
        flash('注册申请已提交，请等待管理员审批。您的用户ID是：' + new_user_id)
        return redirect(url_for('login'))
        
    return render_template('register.html')

# 登录页面
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # 获取表单数据
        username = request.form.get('username')
        password = request.form.get('password')
        
        # 验证表单数据
        if not username or not password:
            flash('请填写用户名和密码')
            return redirect(url_for('login'))
            
        # 检查用户是否存在
        users = load_users()
        user = next((u for u in users if u['username'] == username), None)
        
        if not user:
            flash('用户名不存在')
            return redirect(url_for('login'))
            
        # 检查用户状态
        if user['status'] == 'pending':
            flash('您的账户正在等待管理员审批')
            return redirect(url_for('login'))
            
        if user['status'] == 'rejected':
            flash('您的账户申请已被拒绝')
            return redirect(url_for('login'))
            
        # 验证密码
        if not bcrypt.checkpw(password.encode('utf-8'), user['password'].encode('utf-8')):
            flash('密码错误')
            return redirect(url_for('login'))
            
        # 登录成功，设置session
        session.permanent = True  # 启用持久化会话
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['is_admin'] = user.get('is_admin', False)
        
        # 检查用户是否被封禁
        is_banned, ban_reason, remaining_time, expire_time = is_user_banned(user['id'])
        if is_banned:
            session['is_banned'] = True
            session['ban_reason'] = ban_reason
            session['ban_remaining'] = remaining_time
            session['ban_expire'] = expire_time
        else:
            session['is_banned'] = False
            
        # 检查是否有未读通知
        unread_notifications = get_unread_notifications(user['id'])
        if unread_notifications:
            # 将未读通知信息存储到session中
            session['has_unread_notifications'] = True
            
        flash('登录成功！')
        return redirect(url_for('index'))
        
    return render_template('login.html')

# 登出
@app.route('/logout')
def logout():
    # 从在线用户列表中移除
    if hasattr(app, 'online_users') and 'user_id' in session:
        user_id = session['user_id']
        if user_id in app.online_users:
            app.online_users.remove(user_id)
    
    # 清除session
    session.clear()
    flash('已成功登出')
    return redirect(url_for('login'))

# 获取未读通知
@app.route('/api/notifications/unread', methods=['GET'])
def api_get_unread_notifications():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '未登录'}), 401
    
    user_id = session['user_id']
    try:
        notifications = get_unread_notifications(user_id)
        return jsonify({'success': True, 'notifications': notifications})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# 标记通知为已读
@app.route('/api/notifications/read/<notification_id>', methods=['POST'])
def api_mark_notification_as_read(notification_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '未登录'}), 401
    
    user_id = session['user_id']
    try:
        success = mark_notification_as_read(notification_id, user_id)
        if success:
            # 如果没有更多未读通知，清除session标记
            remaining_unread = get_unread_notifications(user_id)
            if not remaining_unread and 'has_unread_notifications' in session:
                session['has_unread_notifications'] = False
            
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'message': '通知不存在或已读'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# 管理员审批用户页面
@app.route('/admin/users')
def admin_users():
    if not is_admin():
        flash('您没有权限访问此页面')
        return redirect(url_for('index'))
        
    users = load_users()
    # 检查每个用户的封禁状态
    for user in users:
        if 'ban_info' in user:
            is_banned, _, remaining_time, _ = is_user_banned(user['id'])
            if is_banned:
                user['ban_status'] = True
                user['ban_remaining'] = remaining_time
            else:
                user['ban_status'] = False
        else:
            user['ban_status'] = False
            
    return render_template('admin_users.html', users=users, current_user=get_current_user())

# 审批用户
@app.route('/admin/approve/<user_id>')
def approve_user(user_id):
    if not is_admin():
        flash('您没有权限执行此操作')
        return redirect(url_for('index'))
        
    users = load_users()
    user = next((u for u in users if u['id'] == user_id), None)
    
    if user:
        user['status'] = 'approved'
        save_users(users)
        flash('用户已批准')
    else:
        flash('用户不存在')
        
    return redirect(url_for('admin_users'))

# 拒绝用户
@app.route('/admin/reject/<user_id>')
def reject_user(user_id):
    if not is_admin():
        flash('您没有权限执行此操作')
        return redirect(url_for('admin_users'))
        
    users = load_users()
    user = next((u for u in users if u['id'] == user_id), None)
    
    if user:
        user['status'] = 'rejected'
        save_users(users)
        flash('用户已拒绝')
    else:
        flash('用户不存在')
        
    return redirect(url_for('admin_users'))

# 删除用户
@app.route('/admin/delete/<user_id>')
def delete_user(user_id):
    if not is_admin():
        flash('您没有权限执行此操作')
        return redirect(url_for('admin_users'))
        
    users = load_users()
    # 检查是否是最后一个管理员用户
    admin_users = [u for u in users if u.get('is_admin', False)]
    user_to_delete = next((u for u in users if u['id'] == user_id), None)
    
    if not user_to_delete:
        flash('用户不存在')
        return redirect(url_for('admin_users'))
        
    # 不允许删除管理员用户
    if user_to_delete.get('is_admin', False):
        flash('不允许删除管理员用户')
        return redirect(url_for('admin_users'))
        
    # 从用户列表中移除用户
    users = [u for u in users if u['id'] != user_id]
    save_users(users)
    flash('用户已成功删除')
    
    return redirect(url_for('admin_users'))

# 管理员撤销开发者权限
@app.route('/admin/revoke_developer/<user_id>', methods=['POST'])
def revoke_developer(user_id):
    if not is_admin():
        flash('您没有权限执行此操作')
        return redirect(url_for('index'))
    
    users = load_users()
    user = next((u for u in users if u['id'] == user_id), None)
    
    if not user:
        flash('用户不存在')
        return redirect(url_for('admin_users'))
    
    if not user.get('is_developer'):
        flash('该用户不是开发者')
        return redirect(url_for('admin_users'))
    
    # 撤销开发者权限，清除开发者信息
    user['is_developer'] = False
    user['developer_name'] = None
    user.pop('developer_application', None)
    
    save_users(users)
    flash(f'已撤销 {user["username"]} 的开发者权限')
    return redirect(url_for('admin_users'))

# 显示封禁用户表单
@app.route('/admin/ban/<user_id>')
def show_ban_form(user_id):
    if not is_admin():
        flash('您没有权限执行此操作')
        return redirect(url_for('index'))
        
    users = load_users()
    user = next((u for u in users if u['id'] == user_id), None)
    
    if not user:
        flash('用户不存在')
        return redirect(url_for('admin_users'))
        
    # 不允许封禁管理员用户
    if user.get('is_admin', False):
        flash('不允许封禁管理员用户')
        return redirect(url_for('admin_users'))
        
    return render_template('ban_user.html', user=user)

# 处理封禁用户请求
@app.route('/admin/ban/<user_id>', methods=['POST'])
def ban_user(user_id):
    if not is_admin():
        flash('您没有权限执行此操作')
        return redirect(url_for('index'))
        
    users = load_users()
    user = next((u for u in users if u['id'] == user_id), None)
    
    if not user:
        flash('用户不存在')
        return redirect(url_for('admin_users'))
        
    # 不允许封禁管理员用户
    if user.get('is_admin', False):
        flash('不允许封禁管理员用户')
        return redirect(url_for('admin_users'))
        
    # 获取表单数据
    ban_duration = int(request.form.get('ban_duration', 0))
    ban_reason = request.form.get('ban_reason', '').strip()
    
    if not ban_reason:
        flash('请输入封禁原因')
        return redirect(url_for('show_ban_form', user_id=user_id))
        
    # 添加封禁信息
    user['ban_info'] = {
        'ban_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'duration': ban_duration,
        'reason': ban_reason
    }
    
    save_users(users)
    
    duration_text = '永久' if ban_duration == 0 else f'{ban_duration//3600}小时' if ban_duration >= 3600 else f'{ban_duration//60}分钟'
    flash(f'用户{user["username"]}已被封禁{duration_text}')
    
    return redirect(url_for('admin_users'))

# 解除用户封禁
@app.route('/admin/unban/<user_id>')
def unban_user(user_id):
    if not is_admin():
        flash('您没有权限执行此操作')
        return redirect(url_for('index'))
        
    users = load_users()
    user = next((u for u in users if u['id'] == user_id), None)
    
    if not user:
        flash('用户不存在')
        return redirect(url_for('admin_users'))
        
    # 检查用户是否被封禁
    if 'ban_info' in user:
        # 清除封禁信息
        del user['ban_info']
        save_users(users)
        flash(f'用户{user["username"]}的封禁已解除')
    else:
        flash('该用户未被封禁')
        
    return redirect(url_for('admin_users'))

# 处理分块上传
@app.route('/upload_chunk', methods=['POST'])
def upload_chunk():
    try:
        if not is_logged_in():
            return jsonify({'error': '请先登录'}), 401
        
        # 获取分块数据
        file_id = request.form.get('file_id')
        chunk_index = int(request.form.get('chunk_index'))
        total_chunks = int(request.form.get('total_chunks'))
        filename = request.form.get('filename')
        chunk = request.files.get('chunk')
        relative_path = request.form.get('relative_path')
        
        if not file_id or chunk is None:
            return jsonify({'error': '缺少必要的上传参数'}), 400
        
        # 处理分块，传入相对路径信息
        merged_file_path = handle_chunked_upload(file_id, chunk, chunk_index, total_chunks, relative_path)
        
        # 记录上传进度
        progress = (chunk_index + 1) / total_chunks * 100
        print(f"文件 {filename} 上传进度: {progress:.2f}%")
        
        # 如果是最后一个分块，返回合并后的文件路径
        if merged_file_path:
            return jsonify({
                'success': True,
                'is_complete': True,
                'file_id': file_id
            })
        else:
            return jsonify({
                'success': True,
                'is_complete': False,
                'chunk_index': chunk_index,
                'total_chunks': total_chunks
            })
    except Exception as e:
        print(f"分块上传错误: {str(e)}")
        return jsonify({'error': str(e)}), 500

# 处理最终的上传表单
@app.route('/upload_final', methods=['POST'])
def upload_final():
    try:
        if not is_logged_in():
            flash('请先登录')
            return redirect(url_for('login'))
        
        # 获取表单数据
        file_ids = request.form.getlist('file_ids[]')
        title = request.form.get('title')
        description = request.form.get('description')
        author = request.form.get('author')
        tags = request.form.getlist('tags[]')
        
        if not title or not description or not author or not file_ids:
            flash('请填写所有必填字段')
            return redirect(url_for('upload'))
        
        # 创建游戏目录
        game_id = str(len(load_games()) + 1)
        game_dir = os.path.join(app.config['UPLOAD_FOLDER'], game_id)
        os.makedirs(game_dir, exist_ok=True)
        
        current_user = get_current_user()
        
        # 处理上传的文件
        print(f"开始处理最终上传的文件，文件ID列表: {file_ids}")
        
        # 记录主文件名和是否为文件夹
        main_filename = None
        is_folder = len(file_ids) > 1
        total_size = 0
        
        # 遍历所有上传的文件ID
        for file_id in file_ids:
            # 查找合并后的文件
            merged_file_path = os.path.join('temp_uploads', f'{file_id}.merged')
            
            if not os.path.exists(merged_file_path):
                print(f"错误: 未找到合并后的文件 {merged_file_path}")
                flash(f'上传失败: 未找到文件内容')
                return redirect(url_for('upload'))
            
            # 获取文件名和相对路径
            filename = request.form.get(f'filename_{file_id}') or f'file_{file_id}'
            
            # 尝试从path文件中读取相对路径
            path_store_file = os.path.join('temp_uploads', f'{file_id}.path')
            relative_path = None
            if os.path.exists(path_store_file):
                with open(path_store_file, 'r', encoding='utf-8') as f:
                    relative_path = f.read().strip()
                os.remove(path_store_file)  # 删除路径信息文件
            
            # 如果表单中也有路径信息，使用表单中的
            form_path = request.form.get(f'path_{file_id}')
            if form_path:
                relative_path = form_path
            
            # 如果是第一个文件，设置为主文件名
            if main_filename is None:
                main_filename = filename
            
            # 确定目标路径，保留文件夹结构
            if relative_path:
                # 构建完整的目标路径，保留文件夹层次
                dest_path = os.path.join(game_dir, relative_path)
                # 确保目标文件夹存在
                dest_dir = os.path.dirname(dest_path)
                os.makedirs(dest_dir, exist_ok=True)
            else:
                # 没有相对路径信息，使用简单文件名
                dest_path = os.path.join(game_dir, filename)
            
            try:
                # 读取合并后的文件内容并写入目标文件
                print(f"正在处理文件: {relative_path or filename}")
                file_size = os.path.getsize(merged_file_path)
                
                with open(merged_file_path, 'rb') as src:
                    with open(dest_path, 'wb') as dst:
                        # 使用较大的缓冲区进行复制
                        buffer_size = 1024 * 1024  # 1MB
                        while True:
                            buffer = src.read(buffer_size)
                            if not buffer:
                                break
                            dst.write(buffer)
                
                total_size += file_size
                print(f"  文件 {relative_path or filename} 处理完成，大小: {file_size/1024/1024:.2f} MB")
                
                # 删除临时文件
                os.remove(merged_file_path)
            except Exception as file_error:
                print(f"  文件 {filename} 处理失败: {str(file_error)}")
                raise
        
        print(f"所有文件处理完成，总大小: {total_size/1024/1024:.2f} MB")
        
        # 如果是单个ZIP文件，解压它
        if not is_folder and main_filename and main_filename.lower().endswith('.zip'):
            try:
                file_path = os.path.join(game_dir, main_filename)
                print(f"开始解压ZIP文件: {file_path}")
                with zipfile.ZipFile(file_path, 'r') as zip_ref:
                    zip_ref.extractall(game_dir)
                is_folder = True
                print(f"ZIP文件解压完成")
                # 删除原始ZIP文件，避免重复
                os.remove(file_path)
            except zipfile.BadZipFile:
                flash('ZIP文件格式不正确')
                return redirect(url_for('upload'))
        
        # 添加游戏信息到列表
        game_info = {
            'id': game_id,
            'title': title,
            'description': description,
            'author': author,
            'filename': main_filename,
            'upload_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'downloads': 0,
            'is_folder': is_folder,
            'user_id': current_user['id'],
            'username': current_user['username'],
            'tags': tags,
            # 来源标记：direct上传为转载，开发者平台上传为原创
            'origin': 'repost',  # repost 转载, original 原创
            # 安全检测相关字段
            'security_status': 'pending',  # pending, passed, risk
            'security_check_time': None
        }

        # 模拟文件安全检测（在实际环境中应该使用专业的安全扫描工具）
        # 由于是模拟，我们根据文件名或其他属性随机决定检测结果
        import random
        # 检查文件名中是否包含可能的风险关键词 - 增加更多敏感词以提高安全检测准确性
        risk_keywords = [
            # 恶意软件类型
            'virus', 'malware', 'trojan', 'worm', 'ransomware', 
            'spyware', 'adware', 'keylogger', 'backdoor', 'rootkit',
            'rat', 'botnet', 'cryptojacker', 'ransom', 'crypto',
            
            # 危险程序特征
            'crack', 'keygen', 'patch', 'serial', 'activator',
            'loader', 'injector', 'exploit', 'payload', 'shellcode',
            
            # 敏感操作相关
            'format', 'wipe', 'delete', 'corrupt', 'encrypt',
            'decrypt', 'steal', 'sniff', 'log', 'record',
            
            # 系统关键操作
            'kernel', 'system32', 'registry', 'boot', 'mbr',
            'service', 'task', 'process', 'admin', 'root',
            
            # 可疑扩展名和格式
            'bat', 'cmd', 'ps1', 'vbs', 'js', 'scr'
        ]
        has_risk = any(keyword in main_filename.lower() for keyword in risk_keywords)
        
        # 70%的概率通过安全检测，30%的概率存在风险
        if not has_risk:
            is_risk = random.random() < 0.3
        else:
            is_risk = True
            
        if is_risk:
            game_info['security_status'] = 'risk'
        else:
            game_info['security_status'] = 'passed'
        
        game_info['security_check_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        games = load_games()
        games.append(game_info)
        save_games(games)
        
        flash('游戏上传成功！')
        return redirect(url_for('index'))
    except Exception as e:
        print(f"上传处理过程中发生错误: {str(e)}")
        import traceback
        traceback.print_exc()
        flash(f'上传失败: {str(e)}')
        return redirect(url_for('upload'))

# 上传游戏页面
@app.route('/upload', methods=['GET'])
def upload():
    print(f"MAX_CONTENT_LENGTH配置值: {app.config.get('MAX_CONTENT_LENGTH')}")
    print(f"当前进程的内存限制: {os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / (1024*1024*1024):.2f} GB" if hasattr(os, 'sysconf') else "无法获取内存限制")
    
    # 确保用户已登录
    if not is_logged_in():
        flash('请先登录')
        return redirect(url_for('login'))
    
    # 检查用户是否被封禁
    if session.get('is_banned'):
        flash('您的账户已被封禁，无法上传游戏')
        return redirect(url_for('index'))
    
    return render_template('upload.html', current_user=get_current_user())

# 获取用户余额
@app.route('/api/user/wallet/balance', methods=['GET'])
def get_user_balance():
    if not is_logged_in():
        return jsonify({'success': False, 'message': '未登录'}), 401
    
    user = get_current_user()
    # 确保余额字段存在
    if 'wallet_balance' not in user:
        user['wallet_balance'] = 0.0
        users = load_users()
        for u in users:
            if u['id'] == user['id']:
                u['wallet_balance'] = 0.0
                break
        save_users(users)
    
    return jsonify({'success': True, 'balance': user['wallet_balance']})

# 购买游戏
@app.route('/api/game/purchase', methods=['POST'])
def purchase_game():
    if not is_logged_in():
        return jsonify({'success': False, 'message': '未登录'}), 401
    
    data = request.get_json()
    game_id = data.get('game_id')
    price = data.get('price', 0)
    
    if not game_id:
        return jsonify({'success': False, 'message': '游戏ID不能为空'}), 400
    
    # 加载游戏和用户数据
    games = load_games()
    game = next((g for g in games if g['id'] == game_id), None)
    
    if not game:
        return jsonify({'success': False, 'message': '游戏不存在'}), 404
    
    # 检查游戏是否被封禁
    if game.get('is_banned', False):
        return jsonify({'success': False, 'message': '该游戏已被封禁'}), 400
    
    # 获取游戏实际价格（如果有）
    actual_price = game.get('price', 0)
    if actual_price > 0 and actual_price != price:
        return jsonify({'success': False, 'message': '价格不匹配，请刷新页面后重试'}), 400
    
    # 确保价格大于0
    if actual_price <= 0:
        return jsonify({'success': False, 'message': '该游戏无需购买'}), 400
    
    # 获取当前用户
    current_user = get_current_user()
    users = load_users()
    user_index = next((i for i, u in enumerate(users) if u['id'] == current_user['id']), None)
    
    if user_index is None:
        return jsonify({'success': False, 'message': '用户不存在'}), 404
    
    # 确保用户有余额和已购买游戏字段
    if 'wallet_balance' not in users[user_index]:
        users[user_index]['wallet_balance'] = 0.0
    if 'purchased_games' not in users[user_index]:
        users[user_index]['purchased_games'] = []
    
    # 检查用户是否已购买过该游戏
    if game_id in users[user_index]['purchased_games']:
        return jsonify({'success': False, 'message': '您已经购买过该游戏'}), 400
    
    # 检查余额是否足够
    if users[user_index]['wallet_balance'] < actual_price:
        return jsonify({'success': False, 'message': '余额不足，请先充值'}), 400
    
    # 扣款并添加游戏到已购买列表
    users[user_index]['wallet_balance'] -= actual_price
    users[user_index]['purchased_games'].append(game_id)
    
    # 保存用户数据
    save_users(users)
    
    # 创建购买成功通知
    create_notification(
        user_id=current_user['id'],
        title='购买成功',
        content=f'您已成功购买游戏《{game["title"]}》，花费{actual_price}元',
        notification_type='purchase_success'
    )
    
    return jsonify({'success': True, 'message': '购买成功'})

# ===== 支付系统（聚合支付异步通知） =====
import hashlib

# 支付配置（请根据实际支付平台修改）
PAYMENT_CONFIG = {
    'api_url': 'https://your-pay-api.com/mapi.php',  # 支付网关地址
    'pid': 1000,           # 商户ID
    'key': 'your_key_here', # 商户密钥
    'notify_url': 'http://your-domain.com/api/payment/notify',
    'return_url': 'http://your-domain.com/my_games',
}

def load_payment_orders():
    f = 'payment_orders.json'
    if os.path.exists(f):
        with open(f, 'r', encoding='utf-8') as fp:
            return json.load(fp)
    return []

def save_payment_orders(orders):
    with open('payment_orders.json', 'w', encoding='utf-8') as fp:
        json.dump(orders, fp, ensure_ascii=False, indent=4)

# 生成MD5签名（易支付标准格式）
def make_payment_sign(params, key):
    # 按照 key 排序拼接
    keys = sorted(params.keys())
    items = []
    for k in keys:
        if params[k] and k != 'sign' and k != 'sign_type':
            items.append(f'{k}={params[k]}')
    items.append(f'key={key}')
    return hashlib.md5('&'.join(items).encode('utf-8')).hexdigest()

# 验证异步通知签名
def verify_payment_notify(params, key):
    sign = params.pop('sign', '')
    sign_type = params.pop('sign_type', '')
    expected = make_payment_sign(params, key)
    params['sign'] = sign
    params['sign_type'] = sign_type
    return sign == expected

def load_dlcs():
    if os.path.exists('dlc.json'):
        with open('dlc.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

# 创建充值订单（返回支付跳转URL）
@app.route('/api/payment/create', methods=['POST'])
def payment_create():
    if not is_logged_in():
        return jsonify({'success': False, 'message': '未登录'}), 401
    
    data = request.get_json()
    payment_type = data.get('type', 'alipay')  # alipay / wechat
    amount = float(data.get('amount', 0))
    
    if amount <= 0:
        return jsonify({'success': False, 'message': '金额无效'}), 400
    if amount > 10000:
        return jsonify({'success': False, 'message': '单次充值不能超过10000元'}), 400
    
    current_user = get_current_user()
    orders = load_payment_orders()
    
    # 生成唯一订单号
    import time
    out_trade_no = f'CZ{time.strftime("%Y%m%d%H%M%S")}{len(orders) + 1}'
    
    order = {
        'out_trade_no': out_trade_no,
        'user_id': current_user['id'],
        'username': current_user['username'],
        'amount': amount,
        'type': payment_type,
        'status': 'pending',  # pending / success / failed
        'trade_no': None,     # 支付平台交易号
        'create_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'pay_time': None,
    }
    orders.append(order)
    save_payment_orders(orders)
    
    # 构造支付参数（易支付标准格式）
    cfg = PAYMENT_CONFIG
    params = {
        'pid': cfg['pid'],
        'type': payment_type,
        'out_trade_no': out_trade_no,
        'notify_url': cfg['notify_url'],
        'return_url': cfg['return_url'],
        'name': f'Nexus钱包充值 ¥{amount:.2f}',
        'money': f'{amount:.2f}',
        'sign_type': 'MD5',
    }
    params['sign'] = make_payment_sign(params, cfg['key'])
    
    # 构造支付跳转URL
    import urllib.parse
    pay_url = cfg['api_url'] + '?' + urllib.parse.urlencode(params)
    
    return jsonify({
        'success': True,
        'out_trade_no': out_trade_no,
        'pay_url': pay_url,
        'amount': amount,
    })

# 支付异步通知（支付平台回调）
@app.route('/api/payment/notify', methods=['POST'])
def payment_notify():
    params = request.form.to_dict()
    cfg = PAYMENT_CONFIG
    
    # 验签
    if not verify_payment_notify(params.copy(), cfg['key']):
        print(f'支付通知验签失败: {params}')
        return 'fail', 400
    
    out_trade_no = params.get('out_trade_no')
    trade_no = params.get('trade_no')  # 支付平台交易号
    trade_status = params.get('trade_status', '')
    money = float(params.get('money', 0))
    
    if trade_status != 'TRADE_SUCCESS':
        return 'success'  # 非成功状态也返回success避免重试
    
    orders = load_payment_orders()
    order = next((o for o in orders if o['out_trade_no'] == out_trade_no), None)
    
    if not order:
        print(f'订单不存在: {out_trade_no}')
        return 'fail', 404
    
    if order['status'] != 'pending':
        return 'success'  # 已处理，无需重复
    
    # 校验金额
    if abs(order['amount'] - money) > 0.01:
        print(f'金额不匹配: 订单={order["amount"]}, 通知={money}')
        return 'fail', 400
    
    # 更新订单状态
    order['status'] = 'success'
    order['trade_no'] = trade_no
    order['pay_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    save_payment_orders(orders)
    
    # 自动到账（充值到钱包）
    users = load_users()
    user_index = next((i for i, u in enumerate(users) if u['id'] == order['user_id']), None)
    if user_index is not None:
        if 'wallet_balance' not in users[user_index]:
            users[user_index]['wallet_balance'] = 0.0
        users[user_index]['wallet_balance'] += order['amount']
        save_users(users)
    
    print(f'支付成功: {out_trade_no}, {trade_no}, ¥{money:.2f}')
    return 'success'

# 查询订单状态
@app.route('/api/payment/query', methods=['GET'])
def payment_query():
    if not is_logged_in():
        return jsonify({'success': False, 'message': '未登录'}), 401
    
    out_trade_no = request.args.get('out_trade_no')
    if not out_trade_no:
        return jsonify({'success': False, 'message': '参数错误'}), 400
    
    orders = load_payment_orders()
    order = next((o for o in orders if o['out_trade_no'] == out_trade_no), None)
    
    if not order:
        return jsonify({'success': False, 'message': '订单不存在'}), 404
    
    current_user = get_current_user()
    if order['user_id'] != current_user['id'] and not current_user.get('is_admin'):
        return jsonify({'success': False, 'message': '无权限'}), 403
    
    return jsonify({
        'success': True,
        'status': order['status'],
        'amount': order['amount'],
        'pay_time': order['pay_time'],
    })

# 下载游戏文件
@app.route('/download/<game_id>')
def download(game_id):
    games = load_games()
    game = next((g for g in games if g['id'] == game_id), None)
    
    if not game:
        flash('游戏不存在')
        return redirect(url_for('index'))
    
    # 检查审核状态：非管理员不能下载未上架的作品
    if game.get('origin') == 'original' and game.get('review_status') not in ('approved', 'auto_approved'):
        if not is_logged_in() or not get_current_user().get('is_admin'):
            flash('该作品正在审核中，暂不可下载')
            return redirect(url_for('index'))
    
    # 检查游戏是否被封禁
    if game.get('is_banned', False):
        flash('该项目已被封禁，无法下载')
        return redirect(url_for('index'))
    
    # 检查游戏是否需要付费
    game_price = game.get('price', 0)
    if game_price > 0:
        # 需要登录才能下载付费游戏
        if not is_logged_in():
            flash('请先登录后下载付费游戏')
            return redirect(url_for('login'))
        
        # 检查用户是否已购买该游戏
        current_user = get_current_user()
        if 'purchased_games' not in current_user or game_id not in current_user['purchased_games']:
            flash('您尚未购买该游戏，请先购买')
            return redirect(url_for('index'))
    
    # 更新下载次数
    game['downloads'] += 1
    save_games(games)
    
    # ===== 防刷下载：IP限速（同IP每分钟只能下载同一项目一次） =====
    ip = request.remote_addr or 'unknown'
    dl_key = f'dl_{ip}_{game_id}'
    now_ts = time.time()
    
    if not hasattr(app, '_dl_limits'):
        app._dl_limits = {}
    
    last_dl = app._dl_limits.get(dl_key, 0)
    if now_ts - last_dl < 60:
        app._dl_limits[dl_key] = last_dl  # 不更新时间，保持限制
        flash('⏱ 下载太频繁了，请等待1分钟后再试')
        return redirect(url_for('index'))
    
    app._dl_limits[dl_key] = now_ts
    # 清理过期记录
    if len(app._dl_limits) > 1000:
        cutoff = now_ts - 120
        app._dl_limits = {k: v for k, v in app._dl_limits.items() if v > cutoff}
    
    # ===== 收益计算：原创作品每10次下载 = ¥0.01 =====
    if game.get('origin') == 'original' and game['downloads'] % 10 == 0:
        try:
            earn_file = 'earnings.json'
            earnings = []
            if os.path.exists(earn_file):
                with open(earn_file, 'r', encoding='utf-8') as f:
                    earnings = json.load(f)
            
            earnings.append({
                'game_id': game_id,
                'game_title': game['title'],
                'developer_id': game['user_id'],
                'developer_name': game.get('username', ''),
                'amount': 0.01,
                'downloads_milestone': game['downloads'],
                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })
            
            with open(earn_file, 'w', encoding='utf-8') as f:
                json.dump(earnings, f, ensure_ascii=False, indent=4)
        except:
            pass  # 收益记录出错不影响下载
    
    game_dir = os.path.join(app.config['UPLOAD_FOLDER'], game_id)
    
    # 处理文件名可能包含路径的情况
    filename = game['filename']
    
    # 检查是否是文件夹类型的游戏
    if game.get('is_folder', False):
        # 如果是文件夹类型，创建一个临时ZIP文件包含整个游戏文件夹
        import tempfile
        import zipfile
        
        # 创建临时ZIP文件
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as temp_zip:
            temp_zip_path = temp_zip.name
        
        # 将游戏文件夹内容添加到ZIP文件
        with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # 获取原始上传的ZIP文件名（如果是单个ZIP文件上传的情况）
            original_zip = game['filename'] if game['filename'] and game['filename'].lower().endswith('.zip') else None
            
            for root, dirs, files in os.walk(game_dir):
                for file in files:
                    # 排除原始上传的ZIP文件，只打包解压后的内容
                    if original_zip and file == original_zip:
                        continue
                        
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, game_dir)
                    zipf.write(file_path, arcname)
        
        # 提供临时ZIP文件下载
        zip_filename = f"{game['title']}_完整游戏.zip"
        print(f"下载游戏ZIP文件: {zip_filename}")
        
        # 创建一个响应对象，发送ZIP文件并删除临时文件
        from flask import send_file, after_this_request
        
        @after_this_request
        def cleanup(response):
            try:
                os.remove(temp_zip_path)
            except Exception as e:
                print(f"清理临时文件时出错: {e}")
            return response
        
        return send_file(temp_zip_path, as_attachment=True, download_name=zip_filename)
    
    # 检查文件是否存在
    file_path = os.path.join(game_dir, filename)
    if not os.path.exists(file_path):
        # 如果文件不存在，尝试在游戏目录直接查找可执行文件
        executables = []
        for root, dirs, files in os.walk(game_dir):
            for file in files:
                if file.lower().endswith('.exe'):
                    executables.append(os.path.join(root, file))
        
        if executables:
            # 选择第一个可执行文件作为下载目标
            exe_path = executables[0]
            exe_dir = os.path.dirname(exe_path)
            exe_filename = os.path.basename(exe_path)
            print(f"提供可执行文件: {exe_filename} 从 {exe_dir}")
            return send_from_directory(exe_dir, exe_filename, as_attachment=True)
        
        flash('游戏文件不存在')
        return redirect(url_for('index'))
    
    print(f"下载游戏文件: {filename} 从 {game_dir}")
    return send_from_directory(game_dir, filename, as_attachment=True)

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

# 管理员项目管理页面
@app.route('/admin/projects')
def admin_projects():
    if not is_logged_in() or not get_current_user().get('is_admin', False):
        flash('您没有权限访问此页面')
        return redirect(url_for('login'))
    
    games = load_games()
    return render_template('admin_projects.html', games=games, current_user=get_current_user())

# 管理员用户充值管理页面
@app.route('/admin/recharge')
def admin_recharge():
    if not is_logged_in() or not get_current_user().get('is_admin', False):
        flash('您没有权限访问此页面')
        return redirect(url_for('login'))
    
    # 加载所有用户
    users = load_users()
    # 确保所有用户都有余额字段
    for user in users:
        if 'wallet_balance' not in user:
            user['wallet_balance'] = 0.0
    
    # 加载用户充值记录（从localStorage中读取）
    # 在实际应用中，这应该从服务器端数据库或文件中读取
    # 这里我们只是为了演示
    
    return render_template('admin_recharge.html', users=users, current_user=get_current_user())

# 管理员管理用户余额
@app.route('/admin/user/recharge', methods=['POST'])
def admin_user_recharge():
    if not is_logged_in() or not get_current_user().get('is_admin', False):
        return jsonify({'success': False, 'message': '权限不足'}), 403
    
    user_id = request.form.get('user_id')
    amount = request.form.get('amount')
    payment_type = request.form.get('payment_type', 'unknown')
    operation_type = request.form.get('operation_type', 'add')  # add 或 subtract
    
    if not user_id or not amount:
        return jsonify({'success': False, 'message': '用户ID和金额不能为空'}), 400
    
    try:
        amount = float(amount)
        if amount <= 0:
            return jsonify({'success': False, 'message': '金额必须大于0'}), 400
    except ValueError:
        return jsonify({'success': False, 'message': '请输入有效的金额'}), 400
    
    # 加载用户数据
    users = load_users()
    user_index = next((i for i, u in enumerate(users) if u['id'] == user_id), None)
    
    if user_index is None:
        return jsonify({'success': False, 'message': '用户不存在'}), 404
    
    # 确保用户有余额字段
    if 'wallet_balance' not in users[user_index]:
        users[user_index]['wallet_balance'] = 0.0
    
    # 处理余额操作
    if operation_type == 'add':
        # 增加用户余额
        users[user_index]['wallet_balance'] += amount
        action_text = '充值'
        notification_title = '充值成功'
        notification_content = f'您的账户已充值{amount}元，充值方式：{payment_type}'
        notification_type = 'recharge_success'
    elif operation_type == 'subtract':
        # 扣除用户余额，确保余额不小于0
        if users[user_index]['wallet_balance'] >= amount:
            users[user_index]['wallet_balance'] -= amount
            action_text = '扣除'
            notification_title = '余额调整'
            notification_content = f'您的账户已扣除{amount}元'
            notification_type = 'balance_adjustment'
        else:
            return jsonify({'success': False, 'message': '用户余额不足，无法扣除'}), 400
    else:
        return jsonify({'success': False, 'message': '无效的操作类型'}), 400
    
    # 保存用户数据
    save_users(users)
    
    # 创建通知
    create_notification(
        user_id=user_id,
        title=notification_title,
        content=notification_content,
        notification_type=notification_type
    )
    
    return jsonify({'success': True, 'message': f'成功为用户{users[user_index]["username"]}{action_text}{amount}元'})

# 我的游戏页面
@app.route('/my_games')
def my_games():
    if not is_logged_in():
        flash('请先登录')
        return redirect(url_for('login'))
    
    current_user = get_current_user()
    # 确保用户有已购买游戏字段
    if 'purchased_games' not in current_user:
        current_user['purchased_games'] = []
    
    # 加载所有游戏
    all_games = load_games()
    
    # 获取用户已购买的游戏
    purchased_games = []
    for game_id in current_user['purchased_games']:
        game = next((g for g in all_games if g['id'] == game_id), None)
        if game and not game.get('is_banned', False):
            # 添加文件大小信息
            game_size = get_game_size(game['id'])
            game['file_size'] = format_file_size(game_size)
            purchased_games.append(game)
    
    return render_template('my_games.html', games=purchased_games, current_user=current_user)

# 处理官方安全验证
@app.route('/admin/project/verify/<game_id>', methods=['POST'])
def verify_project(game_id):
    if not is_logged_in() or not get_current_user().get('is_admin', False):
        flash('您没有权限执行此操作')
        return redirect(url_for('login'))
    
    games = load_games()
    game = next((g for g in games if g['id'] == game_id), None)
    
    if not game:
        flash('游戏不存在')
        return redirect(url_for('admin_projects'))
    
    # 设置为官方验证通过
    game['security_status'] = 'official'
    game['verified_by'] = get_current_user()['username']
    game['verified_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    save_games(games)
    flash(f'游戏《{game["title"]}》已通过官方安全验证')
    return redirect(url_for('admin_projects'))

# 恢复为普通状态
@app.route('/admin/project/restore_normal/<game_id>', methods=['POST'])
def restore_normal_project(game_id):
    if not is_logged_in() or not get_current_user().get('is_admin', False):
        flash('您没有权限执行此操作')
        return redirect(url_for('login'))
    
    games = load_games()
    game = next((g for g in games if g['id'] == game_id), None)
    
    if not game:
        flash('游戏不存在')
        return redirect(url_for('admin_projects'))
    
    # 恢复为普通通过状态
    game['security_status'] = 'passed'
    game.pop('verified_by', None)
    game.pop('verified_time', None)
    
    save_games(games)
    flash(f'游戏《{game["title"]}》已恢复为普通状态')
    return redirect(url_for('admin_projects'))

# 标记为有风险
@app.route('/admin/project/mark_risk/<game_id>', methods=['POST'])
def mark_risk_project(game_id):
    if not is_logged_in() or not get_current_user().get('is_admin', False):
        flash('您没有权限执行此操作')
        return redirect(url_for('login'))
    
    games = load_games()
    game = next((g for g in games if g['id'] == game_id), None)
    
    if not game:
        flash('游戏不存在')
        return redirect(url_for('admin_projects'))
    
    # 标记为有风险
    game['security_status'] = 'risk'
    game.pop('verified_by', None)
    game.pop('verified_time', None)
    
    save_games(games)
    flash(f'游戏《{game["title"]}》已标记为有风险')
    return redirect(url_for('admin_projects'))

# 封禁/解除封禁项目
@app.route('/admin/project/toggle_ban/<game_id>', methods=['POST'])
def toggle_ban_project(game_id):
    if not is_logged_in() or not get_current_user().get('is_admin', False):
        flash('您没有权限执行此操作')
        return redirect(url_for('login'))
    
    games = load_games()
    game = next((g for g in games if g['id'] == game_id), None)
    
    if not game:
        flash('游戏不存在')
        return redirect(url_for('admin_projects'))
    
    # 切换封禁状态
    game['is_banned'] = not game.get('is_banned', False)
    action = '封禁' if game['is_banned'] else '解除封禁'
    game[f'{"banned" if game["is_banned"] else "unbanned"}_by'] = get_current_user()['username']
    game[f'{"banned" if game["is_banned"] else "unbanned"}_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    save_games(games)
    flash(f'游戏《{game["title"]}》已{action}')
    return redirect(url_for('admin_projects'))

# 管理员撤销原创标记
@app.route('/admin/project/unmark_original/<game_id>', methods=['POST'])
def unmark_original(game_id):
    if not is_logged_in() or not get_current_user().get('is_admin', False):
        flash('您没有权限执行此操作')
        return redirect(url_for('login'))
    
    games = load_games()
    game = next((g for g in games if g['id'] == game_id), None)
    
    if not game:
        flash('游戏不存在')
        return redirect(url_for('admin_projects'))
    
    if game.get('origin') != 'original':
        flash('该游戏不是原创作品，无需撤销')
        return redirect(url_for('admin_projects'))
    
    game['origin'] = 'repost'
    save_games(games)
    flash(f'已撤销游戏《{game["title"]}》的原创标记')
    return redirect(url_for('admin_projects'))

# 游戏详情页
@app.route('/game/<game_id>')
def game_detail(game_id):
    games = load_games()
    game = next((g for g in games if g['id'] == game_id), None)
    
    if not game:
        flash('游戏不存在')
        return redirect(url_for('index'))
    
    # 检查审核状态
    current_user = get_current_user()
    if game.get('origin') == 'original' and game.get('review_status') not in ('approved', 'auto_approved'):
        if not current_user or not current_user.get('is_admin'):
            flash('该作品正在审核中')
            return redirect(url_for('index'))
    
    # 增加浏览次数
    game['views'] = game.get('views', 0) + 1
    save_games(games)
    
    # 获取文件大小
    game_size = get_game_size(game['id'])
    game['file_size'] = format_file_size(game_size)
    
    # 获取开发者信息
    users = load_users()
    developer = next((u for u in users if u['id'] == game.get('user_id')), None)
    
    return render_template('game_detail.html', game=game, developer=developer, current_user=current_user, dlcs=load_dlcs())

# 浏览游戏文件夹内容
@app.route('/browse/<game_id>')
def browse(game_id):
    games = load_games()
    game = next((g for g in games if g['id'] == game_id), None)
    
    if not game:
        flash('游戏不存在')
        return redirect(url_for('index'))
    
    # 检查审核状态：非管理员不能浏览未上架的作品
    current_user = get_current_user()
    if game.get('origin') == 'original' and game.get('review_status') not in ('approved', 'auto_approved'):
        if not current_user or not current_user.get('is_admin'):
            flash('该作品正在审核中，暂不可访问')
            return redirect(url_for('index'))
    
    # 检查是否为文件夹类型或已解压的ZIP文件
    if not game.get('is_folder', False):
        flash('游戏不是文件夹类型')
        return redirect(url_for('index'))
    
    game_dir = os.path.join(app.config['UPLOAD_FOLDER'], game_id)
    
    # 检查游戏目录是否存在
    if not os.path.exists(game_dir):
        flash('游戏文件夹不存在')
        return redirect(url_for('index'))
    
    # 更新浏览次数
    if 'views' not in game:
        game['views'] = 0
    game['views'] += 1
    save_games(games)
    
    # 获取文件夹内容（包括所有子目录）
    files = []
    try:
        for root, dirs, filenames in os.walk(game_dir):
            for filename in filenames:
                file_path = os.path.join(root, filename)
                rel_path = os.path.relpath(file_path, game_dir)
                try:
                    files.append({
                        'name': filename,
                        'path': rel_path,
                        'size': os.path.getsize(file_path)
                    })
                except OSError:
                    # 处理无法访问的文件
                    continue
    except Exception as e:
        print(f"获取文件夹内容错误: {str(e)}")
        flash('获取文件夹内容时出错')
    
    if not files:
        print(f"游戏目录 {game_dir} 中未找到文件")
    
    return render_template('browse.html', game=game, files=files, current_user=get_current_user())

# 下载文件夹中的单个文件
@app.route('/download_file/<game_id>/<path:file_path>')
def download_file(game_id, file_path):
    # 无需登录即可下载单个文件
    games = load_games()
    game = next((g for g in games if g['id'] == game_id), None)
    
    if not game or not game.get('is_folder', False):
        flash('游戏文件夹不存在')
        return redirect(url_for('index'))
    
    game_dir = os.path.join(app.config['UPLOAD_FOLDER'], game_id)
    full_path = os.path.join(game_dir, file_path)
    
    if not os.path.exists(full_path):
        flash('文件不存在')
        return redirect(url_for('browse', game_id=game_id))
    
    # 更新下载次数
    game['downloads'] += 1
    save_games(games)
    
    # 获取文件名和目录路径
    filename = os.path.basename(file_path)
    directory = os.path.dirname(full_path)
    
    return send_from_directory(directory, filename, as_attachment=True)

# 删除游戏
@app.route('/delete/<game_id>')
def delete(game_id):
    if not is_logged_in():
        flash('请先登录')
        return redirect(url_for('login'))
        
    games = load_games()
    game = next((g for g in games if g['id'] == game_id), None)
    
    if not game:
        flash('游戏不存在')
        return redirect(url_for('index'))
        
    current_user = get_current_user()
    
    # 检查权限：只有管理员或游戏上传者才能删除
    # 管理员可以删除任意项目
    if not is_admin():
        # 普通用户只能删除自己上传的项目
        # 增强检查：同时验证用户ID和用户名
        game_user_id = game.get('user_id')
        game_username = game.get('username')
        current_user_id = current_user['id']
        current_username = current_user['username']
        
        # 如果游戏没有用户信息，或者当前用户不是游戏上传者，则禁止删除
        if not game_user_id or not game_username or current_user_id != game_user_id or current_username != game_username:
            flash('您没有权限删除此游戏')
            return redirect(url_for('index'))
    
    # 删除游戏文件
    game_dir = os.path.join(app.config['UPLOAD_FOLDER'], game_id)
    if os.path.exists(game_dir):
        import shutil
        shutil.rmtree(game_dir)
    
    # 从列表中删除游戏
    games.remove(game)
    save_games(games)
    
    flash('游戏删除成功！')
    return redirect(url_for('index'))

# 加载申诉列表
def load_appeals():
    appeals_file = 'appeals.json'
    if not os.path.exists(appeals_file):
        return []
    with open(appeals_file, 'r', encoding='utf-8') as f:
        return json.load(f)

# 保存申诉列表
def save_appeals(appeals):
    with open('appeals.json', 'w', encoding='utf-8') as f:
        json.dump(appeals, f, ensure_ascii=False, indent=4)

# 加载收费申请列表
def load_price_requests():
    requests_file = 'price_requests.json'
    if not os.path.exists(requests_file):
        # 创建一个默认的示例请求
        default_requests = [
            {
                "id": "1",
                "user_id": "1",
                "username": "admin",
                "project_id": "1",
                "project_title": "示例游戏",
                "requested_price": 10.0,
                "reason": "这是一个高质量的游戏",
                "status": "pending",
                "request_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "processed_time": None,
                "processed_by": None,
                "admin_feedback": None
            }
        ]
        with open(requests_file, 'w', encoding='utf-8') as f:
            json.dump(default_requests, f, ensure_ascii=False, indent=4)
        return default_requests
    with open(requests_file, 'r', encoding='utf-8') as f:
        return json.load(f)

# 保存收费申请列表
def save_price_requests(requests):
    with open('price_requests.json', 'w', encoding='utf-8') as f:
        json.dump(requests, f, ensure_ascii=False, indent=4)

# 玩家查看自己的申诉列表
@app.route('/appeals')
def appeals():
    if not is_logged_in():
        flash('请先登录')
        return redirect(url_for('login'))
    
    current_user = get_current_user()
    appeals = load_appeals()
    # 过滤出当前用户的申诉
    user_appeals = [appeal for appeal in appeals if appeal['user_id'] == current_user['id']]
    # 按提交时间倒序排序
    user_appeals.sort(key=lambda x: x['created_at'], reverse=True)
    
    return render_template('appeals.html', appeals=user_appeals, current_user=current_user)

# 提交申诉页面
@app.route('/appeal/create/<game_id>', methods=['GET', 'POST'])
def create_appeal(game_id):
    if not is_logged_in():
        flash('请先登录')
        return redirect(url_for('login'))
    
    current_user = get_current_user()
    games = load_games()
    game = next((g for g in games if g['id'] == game_id), None)
    
    if not game:
        flash('游戏不存在')
        return redirect(url_for('index'))
    
    # 检查用户是否是游戏的上传者
    if game['user_id'] != current_user['id']:
        flash('您只能为自己上传的游戏提交申诉')
        return redirect(url_for('index'))
    
    # 检查游戏是否被标记为风险
    if game.get('security_status') != 'risk':
        flash('只有被标记为风险的游戏才能提交申诉')
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        reason = request.form.get('reason', '').strip()
        if not reason:
            flash('请填写申诉原因')
            return redirect(url_for('create_appeal', game_id=game_id))
        
        # 处理上传的证据文件
        evidence_files = []
        if 'evidence' in request.files:
            files = request.files.getlist('evidence')
            for file in files:
                if file.filename:
                    # 生成唯一的文件名
                    unique_filename = f"{uuid.uuid4()}_{file.filename}"
                    file_path = os.path.join('appeal_evidence', unique_filename)
                    file.save(file_path)
                    evidence_files.append(file_path)
        
        # 创建新申诉
        appeals = load_appeals()
        new_appeal = {
            'id': str(len(appeals) + 1),
            'user_id': current_user['id'],
            'username': current_user['username'],
            'game_id': game_id,
            'game_title': game['title'],
            'reason': reason,
            'evidence_files': evidence_files,
            'status': 'pending',  # pending, approved, rejected
            'admin_reason': None,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        appeals.append(new_appeal)
        save_appeals(appeals)
        
        flash('申诉已提交，请等待管理员处理')
        return redirect(url_for('appeals'))
    
    return render_template('appeal_create.html', game=game, current_user=current_user)

# 我的项目页面
@app.route('/my_projects')
def my_projects():
    if not is_logged_in():
        flash('请先登录')
        return redirect(url_for('login'))
    
    current_user = get_current_user()
    games = load_games()
    
    # 过滤出当前用户的项目
    user_games = [game for game in games if game.get('user_id') == current_user['id']]
    
    # 为每个项目添加价格申请状态
    price_requests = load_price_requests()
    for game in user_games:
        game_request = next((req for req in price_requests if req['project_id'] == game['id']), None)
        if game_request and game_request['status'] == 'pending':
            game['price_request_status'] = 'pending'
    
    # 按上传时间倒序排序
    user_games.sort(key=lambda x: x.get('upload_time', ''), reverse=True)
    
    return render_template('my_projects.html', projects=user_games, current_user=current_user)

# 开发者平台
@app.route('/developer')
def developer():
    if not is_logged_in():
        flash('请先登录')
        return redirect(url_for('login'))
    
    current_user = get_current_user()
    
    # 检查是否为开发者
    if not current_user.get('is_developer'):
        flash('您还不是开发者，请在"我的项目"中申请')
        return redirect(url_for('my_projects'))
    
    games = load_games()
    return render_template('developer.html', current_user=current_user, games=games)

# 开发者项目管理
@app.route('/developer/projects')
def developer_projects():
    if not is_logged_in():
        flash('请先登录')
        return redirect(url_for('login'))
    current_user = get_current_user()
    if not current_user.get('is_developer'):
        flash('您还不是开发者')
        return redirect(url_for('my_projects'))
    
    games = load_games()
    my_originals = [g for g in games if g.get('origin') == 'original' and g.get('user_id') == current_user['id']]
    
    # 加载DLC数据
    dlcs = []
    if os.path.exists('dlc.json'):
        with open('dlc.json', 'r', encoding='utf-8') as f:
            dlcs = json.load(f)
    
    return render_template('developer_projects.html', projects=my_originals, dlcs=dlcs, current_user=current_user)

# 编辑项目信息
@app.route('/developer/project/<game_id>/edit', methods=['GET', 'POST'])
def developer_project_edit(game_id):
    if not is_logged_in():
        flash('请先登录')
        return redirect(url_for('login'))
    current_user = get_current_user()
    if not current_user.get('is_developer'):
        flash('您还不是开发者')
        return redirect(url_for('my_projects'))
    
    games = load_games()
    game = next((g for g in games if g['id'] == game_id), None)
    if not game or game.get('user_id') != current_user['id'] or game.get('origin') != 'original':
        flash('项目不存在或无权操作')
        return redirect(url_for('developer_projects'))
    
    if request.method == 'GET':
        return render_template('developer_project_edit.html', game=game, current_user=current_user)
    
    # POST: 保存修改
    title = request.form.get('title')
    description = request.form.get('description')
    author = request.form.get('author')
    publisher = request.form.get('publisher', '')
    distributor = request.form.get('distributor', '')
    price = float(request.form.get('price') or 0)
    
    if not title or not description or not author:
        flash('请填写必填字段')
        return redirect(url_for('developer_project_edit', game_id=game_id))
    
    game['title'] = title
    game['description'] = description
    game['author'] = author
    game['publisher'] = publisher
    game['distributor'] = distributor
    game['price'] = price
    
    # 系统配置要求
    game['min_specs'] = {
        'os': request.form.get('min_os', ''),
        'cpu': request.form.get('min_cpu', ''),
        'ram': request.form.get('min_ram', ''),
        'gpu': request.form.get('min_gpu', ''),
        'storage': request.form.get('min_storage', ''),
        'dx': request.form.get('min_dx', ''),
    }
    game['rec_specs'] = {
        'os': request.form.get('rec_os', ''),
        'cpu': request.form.get('rec_cpu', ''),
        'ram': request.form.get('rec_ram', ''),
        'gpu': request.form.get('rec_gpu', ''),
        'storage': request.form.get('rec_storage', ''),
        'dx': request.form.get('rec_dx', ''),
    }
    
    # 处理标签
    tags = request.form.getlist('tags[]')
    if tags:
        game['tags'] = tags
    
    # 处理版本更新（新文件上传）
    new_version = request.form.get('new_version', '').strip()
    if new_version:
        file_ids = request.form.getlist('file_ids[]')
        if file_ids:
            game_dir = os.path.join(app.config['UPLOAD_FOLDER'], game_id)
            os.makedirs(game_dir, exist_ok=True)
            
            main_filename = None
            is_folder = len(file_ids) > 1
            for file_id in file_ids:
                merged_path = os.path.join('temp_uploads', f'{file_id}.merged')
                if not os.path.exists(merged_path):
                    continue
                filename = request.form.get(f'filename_{file_id}') or f'file_{file_id}'
                path_store = os.path.join('temp_uploads', f'{file_id}.path')
                rel_path = None
                if os.path.exists(path_store):
                    with open(path_store, 'r') as f:
                        rel_path = f.read().strip()
                    os.remove(path_store)
                if not main_filename:
                    main_filename = filename
                dest = os.path.join(game_dir, rel_path) if rel_path else os.path.join(game_dir, filename)
                if rel_path:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                import shutil
                shutil.copy2(merged_path, dest)
                os.remove(merged_path)
            
            if not is_folder and main_filename and main_filename.lower().endswith('.zip'):
                import zipfile
                try:
                    zf = os.path.join(game_dir, main_filename)
                    with zipfile.ZipFile(zf, 'r') as z:
                        z.extractall(game_dir)
                    is_folder = True
                    os.remove(zf)
                except:
                    pass
            
            game['filename'] = main_filename
            game['is_folder'] = is_folder
            game['version'] = new_version
    
    save_games(games)
    flash('项目信息已更新')
    return redirect(url_for('developer_projects'))

# 删除项目
@app.route('/developer/project/<game_id>/delete', methods=['POST'])
def developer_project_delete(game_id):
    if not is_logged_in():
        flash('请先登录')
        return redirect(url_for('login'))
    current_user = get_current_user()
    if not current_user.get('is_developer'):
        flash('您还不是开发者')
        return redirect(url_for('my_projects'))
    
    games = load_games()
    game = next((g for g in games if g['id'] == game_id), None)
    if not game or game.get('user_id') != current_user['id'] or game.get('origin') != 'original':
        flash('项目不存在或无权操作')
        return redirect(url_for('developer_projects'))
    
    games = [g for g in games if g['id'] != game_id]
    save_games(games)
    
    # 删除游戏目录
    import shutil
    game_dir = os.path.join(app.config['UPLOAD_FOLDER'], game_id)
    if os.path.exists(game_dir):
        shutil.rmtree(game_dir)
    
    flash('项目已删除')
    return redirect(url_for('developer_projects'))

# 封禁/解封项目
@app.route('/developer/project/<game_id>/toggle_ban', methods=['POST'])
def developer_project_toggle_ban(game_id):
    if not is_logged_in():
        flash('请先登录')
        return redirect(url_for('login'))
    current_user = get_current_user()
    if not current_user.get('is_developer'):
        flash('您还不是开发者')
        return redirect(url_for('my_projects'))
    
    games = load_games()
    game = next((g for g in games if g['id'] == game_id), None)
    if not game or game.get('user_id') != current_user['id'] or game.get('origin') != 'original':
        flash('项目不存在或无权操作')
        return redirect(url_for('developer_projects'))
    
    game['is_banned'] = not game.get('is_banned', False)
    save_games(games)
    flash('项目状态已更新')
    return redirect(url_for('developer_projects'))

# DLC管理
@app.route('/developer/project/<game_id>/dlc/create', methods=['POST'])
def developer_dlc_create(game_id):
    if not is_logged_in():
        flash('请先登录')
        return redirect(url_for('login'))
    current_user = get_current_user()
    if not current_user.get('is_developer'):
        flash('您还不是开发者')
        return redirect(url_for('my_projects'))
    
    games = load_games()
    game = next((g for g in games if g['id'] == game_id), None)
    if not game or game.get('user_id') != current_user['id'] or game.get('origin') != 'original':
        flash('项目不存在或无权操作')
        return redirect(url_for('developer_projects'))
    
    title = request.form.get('dlc_title', '').strip()
    description = request.form.get('dlc_description', '').strip()
    price = float(request.form.get('dlc_price') or 0)
    
    if not title:
        flash('请填写DLC标题')
        return redirect(url_for('developer_project_edit', game_id=game_id))
    
    dlcs = []
    if os.path.exists('dlc.json'):
        with open('dlc.json', 'r', encoding='utf-8') as f:
            dlcs = json.load(f)
    
    dlc_id = str(len(dlcs) + 1)
    
    # 处理DLC文件上传
    dlc_filename = None
    dlc_is_folder = False
    file_ids = request.form.getlist('dlc_file_ids[]')
    
    if file_ids:
        dlc_dir = os.path.join(app.config['UPLOAD_FOLDER'], f'{game_id}_dlc_{dlc_id}')
        os.makedirs(dlc_dir, exist_ok=True)
        
        for file_id in file_ids:
            merged_path = os.path.join('temp_uploads', f'{file_id}.merged')
            if not os.path.exists(merged_path):
                continue
            filename = request.form.get(f'dlc_filename_{file_id}') or f'file_{file_id}'
            if not dlc_filename:
                dlc_filename = filename
            dest = os.path.join(dlc_dir, filename)
            import shutil
            shutil.copy2(merged_path, dest)
            os.remove(merged_path)
        
        dlc_is_folder = len(file_ids) > 1
    
    dlc = {
        'id': dlc_id,
        'game_id': game_id,
        'game_title': game['title'],
        'title': title,
        'description': description,
        'price': price,
        'filename': dlc_filename,
        'is_folder': dlc_is_folder,
        'upload_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'downloads': 0
    }
    dlcs.append(dlc)
    
    with open('dlc.json', 'w', encoding='utf-8') as f:
        json.dump(dlcs, f, ensure_ascii=False, indent=4)
    
    flash('DLC发布成功')
    return redirect(url_for('developer_projects'))

# 下载DLC
@app.route('/download/dlc/<dlc_id>')
def download_dlc(dlc_id):
    if not os.path.exists('dlc.json'):
        flash('DLC不存在')
        return redirect(url_for('index'))
    
    with open('dlc.json', 'r', encoding='utf-8') as f:
        dlcs = json.load(f)
    
    dlc = next((d for d in dlcs if d['id'] == dlc_id), None)
    if not dlc:
        flash('DLC不存在')
        return redirect(url_for('index'))
    
    dlc['downloads'] += 1
    with open('dlc.json', 'w', encoding='utf-8') as f:
        json.dump(dlcs, f, ensure_ascii=False, indent=4)
    
    dlc_dir = os.path.join(app.config['UPLOAD_FOLDER'], f"{dlc['game_id']}_dlc_{dlc_id}")
    filename = dlc.get('filename')
    
    if not filename or not os.path.exists(os.path.join(dlc_dir, filename)):
        flash('DLC文件不存在')
        return redirect(url_for('index'))
    
    return send_from_directory(dlc_dir, filename, as_attachment=True)

# ===== CDK 密钥管理 =====
import random
import string

def load_cdks():
    f = 'cdk_records.json'
    if os.path.exists(f):
        with open(f, 'r', encoding='utf-8') as fp:
            return json.load(fp)
    return []

def save_cdks(cdks):
    with open('cdk_records.json', 'w', encoding='utf-8') as fp:
        json.dump(cdks, fp, ensure_ascii=False, indent=4)

def generate_cdk():
    chars = string.digits + string.ascii_uppercase
    segments = []
    for _ in range(4):
        segments.append(''.join(random.choices(chars, k=4)))
    return '-'.join(segments)

@app.route('/developer/project/<game_id>/cdk', methods=['GET', 'POST'])
def developer_cdk(game_id):
    if not is_logged_in():
        flash('请先登录')
        return redirect(url_for('login'))
    current_user = get_current_user()
    if not current_user.get('is_developer'):
        flash('您还不是开发者')
        return redirect(url_for('my_projects'))
    
    games = load_games()
    game = next((g for g in games if g['id'] == game_id), None)
    if not game or game.get('user_id') != current_user['id'] or game.get('origin') != 'original':
        flash('项目不存在或无权操作')
        return redirect(url_for('developer_projects'))
    
    price = game.get('price', 0)
    if not price or price <= 0:
        flash('免费游戏无需 CDK')
        return redirect(url_for('developer_projects'))
    
    cdks = load_cdks()
    game_cdks = [c for c in cdks if c['game_id'] == game_id]
    
    if request.method == 'POST':
        action = request.form.get('action', '')
        if action == 'generate':
            count = int(request.form.get('count', 1))
            if count < 1 or count > 50:
                flash('每次最多生成 50 个 CDK')
                return redirect(url_for('developer_cdk', game_id=game_id))
            for _ in range(count):
                while True:
                    code = generate_cdk()
                    if not any(c['code'] == code for c in cdks):
                        break
                cdks.append({
                    'code': code,
                    'game_id': game_id,
                    'game_title': game['title'],
                    'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'used_by': None,
                    'used_at': None,
                })
            save_cdks(cdks)
            flash(f'成功生成 {count} 个 CDK')
        elif action == 'delete':
            code = request.form.get('code', '')
            cdks = [c for c in cdks if not (c['code'] == code and c['game_id'] == game_id)]
            save_cdks(cdks)
            flash('CDK 已删除')
        return redirect(url_for('developer_cdk', game_id=game_id))
    
    return render_template('developer_cdk.html', game=game, cdks=game_cdks, current_user=current_user)

# 开发者资料设置
@app.route('/developer/settings', methods=['GET', 'POST'])
def developer_settings():
    if not is_logged_in():
        flash('请先登录')
        return redirect(url_for('login'))
    current_user = get_current_user()
    if not current_user.get('is_developer'):
        flash('您还不是开发者，请在"我的项目"中申请')
        return redirect(url_for('my_projects'))
    
    if request.method == 'POST':
        developer_name = request.form.get('developer_name', '').strip()
        bio = request.form.get('bio', '').strip()
        contact_email = request.form.get('contact_email', '').strip()
        website = request.form.get('website', '').strip()
        social_weibo = request.form.get('social_weibo', '').strip()
        social_bilibili = request.form.get('social_bilibili', '').strip()
        social_twitter = request.form.get('social_twitter', '').strip()
        
        if not developer_name:
            flash('工作室名称不能为空')
            return redirect(url_for('developer_settings'))
        
        # 检查重名
        users = load_users()
        user_index = next((i for i, u in enumerate(users) if u['id'] == current_user['id']), None)
        if user_index is None:
            flash('用户不存在')
            return redirect(url_for('index'))
        
        existing = next((u for u in users if u.get('developer_name') == developer_name and u['id'] != current_user['id'] and u.get('is_developer')), None)
        if existing:
            flash('该工作室名称已被使用')
            return redirect(url_for('developer_settings'))
        
        users[user_index]['developer_name'] = developer_name
        users[user_index]['developer_bio'] = bio
        users[user_index]['developer_contact_email'] = contact_email
        users[user_index]['developer_website'] = website
        users[user_index]['developer_social'] = {
            'weibo': social_weibo,
            'bilibili': social_bilibili,
            'twitter': social_twitter,
        }
        save_users(users)
        
        # 刷新 session 中的用户信息
        session['user'] = users[user_index]
        flash('开发者资料已更新')
        return redirect(url_for('developer_settings'))
    
    # 获取当前资料填充表单
    dev_info = {
        'developer_name': current_user.get('developer_name', ''),
        'bio': current_user.get('developer_bio', ''),
        'contact_email': current_user.get('developer_contact_email', ''),
        'website': current_user.get('developer_website', ''),
        'social': current_user.get('developer_social', {}),
    }
    return render_template('developer_settings.html', current_user=current_user, dev_info=dev_info)

# 开发者上传原创作品（页面）
@app.route('/developer/upload', methods=['GET'])
def developer_upload():
    if not is_logged_in():
        flash('请先登录')
        return redirect(url_for('login'))
    
    current_user = get_current_user()
    
    if not current_user.get('is_developer'):
        flash('您还不是开发者，请在"我的项目"中申请')
        return redirect(url_for('my_projects'))
    
    return render_template('upload.html', current_user=current_user, is_developer_upload=True, upload_action='/developer/upload_final')

# 开发者提交原创作品（处理）
@app.route('/developer/upload_final', methods=['POST'])
def developer_upload_final():
    try:
        if not is_logged_in():
            flash('请先登录')
            return redirect(url_for('login'))
        
        current_user = get_current_user()
        
        # 检查是否为开发者
        if not current_user.get('is_developer'):
            flash('只有开发者才能发布原创作品')
            return redirect(url_for('my_projects'))
        
        # 获取表单数据
        file_ids = request.form.getlist('file_ids[]')
        title = request.form.get('title')
        description = request.form.get('description')
        author = request.form.get('author')
        publisher = request.form.get('publisher', '')
        distributor = request.form.get('distributor', '')
        price = float(request.form.get('price') or 0)
        tags = request.form.getlist('tags[]')
        release_mode = request.form.get('release_mode', 'now')
        release_date = request.form.get('release_date', '')
        
        # 系统配置要求
        min_specs = {
            'os': request.form.get('min_os', ''),
            'cpu': request.form.get('min_cpu', ''),
            'ram': request.form.get('min_ram', ''),
            'gpu': request.form.get('min_gpu', ''),
            'storage': request.form.get('min_storage', ''),
            'dx': request.form.get('min_dx', ''),
        }
        rec_specs = {
            'os': request.form.get('rec_os', ''),
            'cpu': request.form.get('rec_cpu', ''),
            'ram': request.form.get('rec_ram', ''),
            'gpu': request.form.get('rec_gpu', ''),
            'storage': request.form.get('rec_storage', ''),
            'dx': request.form.get('rec_dx', ''),
        }
        
        if not title or not description or not author:
            flash('请填写所有必填字段')
            return redirect(url_for('developer_upload'))
        
        if not publisher or not distributor:
            flash('请填写开发商和发行商信息')
            return redirect(url_for('developer_upload'))
        
        if release_mode == 'coming_soon':
            if not release_date:
                flash('请选择发行日期')
                return redirect(url_for('developer_upload'))
        elif not file_ids:
            flash('请上传游戏文件或选择「计划发行」模式')
            return redirect(url_for('developer_upload'))
        
        games = load_games()
        game_id = str(len(games) + 1)
        
        # 处理上传的文件
        main_filename = None
        is_folder = False
        
        if release_mode != 'coming_soon' and file_ids:
            game_dir = os.path.join(app.config['UPLOAD_FOLDER'], game_id)
            os.makedirs(game_dir, exist_ok=True)
            
            is_folder = len(file_ids) > 1
            total_size = 0
            
            for file_id in file_ids:
                merged_file_path = os.path.join('temp_uploads', f'{file_id}.merged')
                
                if not os.path.exists(merged_file_path):
                    flash(f'上传失败: 未找到文件内容')
                    return redirect(url_for('developer_upload'))
                
                filename = request.form.get(f'filename_{file_id}') or f'file_{file_id}'
                
                path_store_file = os.path.join('temp_uploads', f'{file_id}.path')
                relative_path = None
                if os.path.exists(path_store_file):
                    with open(path_store_file, 'r', encoding='utf-8') as f:
                        relative_path = f.read().strip()
                    os.remove(path_store_file)
                
                form_path = request.form.get(f'path_{file_id}')
                if form_path:
                    relative_path = form_path
                
                if main_filename is None:
                    main_filename = filename
                
                if relative_path:
                    dest_path = os.path.join(game_dir, relative_path)
                    dest_dir = os.path.dirname(dest_path)
                    os.makedirs(dest_dir, exist_ok=True)
                else:
                    dest_path = os.path.join(game_dir, filename)
                
                try:
                    file_size = os.path.getsize(merged_file_path)
                    with open(merged_file_path, 'rb') as src:
                        with open(dest_path, 'wb') as dst:
                            buffer_size = 1024 * 1024
                            while True:
                                buffer = src.read(buffer_size)
                                if not buffer:
                                    break
                                dst.write(buffer)
                    total_size += file_size
                    os.remove(merged_file_path)
                except Exception as file_error:
                    flash(f'文件处理失败: {str(file_error)}')
                    return redirect(url_for('developer_upload'))
            
            # 如果是单个ZIP文件，解压
            if not is_folder and main_filename and main_filename.lower().endswith('.zip'):
                try:
                    file_path = os.path.join(game_dir, main_filename)
                    with zipfile.ZipFile(file_path, 'r') as zip_ref:
                        zip_ref.extractall(game_dir)
                    is_folder = True
                    os.remove(file_path)
                except zipfile.BadZipFile:
                    flash('ZIP文件格式不正确')
                    return redirect(url_for('developer_upload'))
        
        # 为计划发行的作品设置安全状态（game_info 中已有默认 pending）
        
        # 创建游戏记录
        game_info = {
            'id': game_id,
            'title': title,
            'description': description,
            'author': author,
            'publisher': publisher,
            'distributor': distributor,
            'filename': main_filename,
            'upload_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'downloads': 0,
            'is_folder': is_folder,
            'user_id': current_user['id'],
            'username': current_user['username'],
            'tags': tags,
            'origin': 'original',  # 开发者上传标记为原创
            'price': price,
            'release_status': 'coming_soon' if release_mode == 'coming_soon' else 'released',
            'release_date': release_date if release_mode == 'coming_soon' else None,
            'min_specs': min_specs,
            'rec_specs': rec_specs,
            'security_status': 'pending',
            'security_check_time': None,
            'review_status': 'pending_review',  # pending_review, approved, rejected, auto_approved
            'review_submit_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'reviewed_by': None,
            'review_time': None,
            'review_notes': None,
            'auto_reviewed': False
        }
        
        games.append(game_info)
        save_games(games)
        
        if release_mode == 'coming_soon':
            flash('计划发行作品已提交，等待管理员审核')
        else:
            flash('原创作品已提交，等待管理员审核上架')
        return redirect(url_for('developer'))
    
    except Exception as e:
        flash(f'上传失败: {str(e)}')
        return redirect(url_for('developer_upload'))

# 提交价格变更申请
@app.route('/request_price_change', methods=['POST'])
def request_price_change():
    if not is_logged_in():
        flash('请先登录')
        return redirect(url_for('login'))
    
    current_user = get_current_user()
    project_id = request.form.get('project_id')
    price = float(request.form.get('price', 0))
    reason = request.form.get('reason', '').strip()
    
    if not project_id or not reason:
        flash('请填写所有必填字段')
        return redirect(url_for('my_projects'))
    
    # 检查项目是否存在且属于当前用户
    games = load_games()
    game = next((g for g in games if g['id'] == project_id), None)
    
    if not game:
        flash('项目不存在')
        return redirect(url_for('my_projects'))
    
    if game.get('user_id') != current_user['id']:
        flash('您没有权限修改此项目的价格')
        return redirect(url_for('my_projects'))
    
    # 非开发者项目价格最高设为10元
    if not current_user.get('is_developer') and price > 10:
        flash('非开发者项目价格最高只能设为10元')
        return redirect(url_for('my_projects'))
    
    # 加载现有的价格申请
    price_requests = load_price_requests()
    
    # 检查是否已有待处理的申请
    existing_request = next((req for req in price_requests if req['project_id'] == project_id and req['status'] == 'pending'), None)
    if existing_request:
        flash('已有待处理的价格申请，请等待管理员处理')
        return redirect(url_for('my_projects'))
    
    # 创建新的价格申请
    new_request = {
        'id': str(len(price_requests) + 1),
        'user_id': current_user['id'],
        'username': current_user['username'],
        'project_id': project_id,
        'project_title': game['title'],
        'requested_price': price,
        'reason': reason,
        'status': 'pending',
        'request_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'processed_time': None,
        'processed_by': None,
        'admin_feedback': None
    }
    
    price_requests.append(new_request)
    save_price_requests(price_requests)
    
    flash('价格变更申请已提交，请等待管理员审核')
    return redirect(url_for('my_projects'))

# 提交开发者申请
@app.route('/apply_developer', methods=['POST'])
def apply_developer():
    if not is_logged_in():
        flash('请先登录')
        return redirect(url_for('login'))
    
    current_user = get_current_user()
    
    # 检查是否已经是开发者
    if current_user.get('is_developer'):
        flash('您已经是开发者')
        return redirect(url_for('my_projects'))
    
    # 检查是否有待处理的申请
    application = current_user.get('developer_application')
    if application and application.get('status') == 'pending':
        flash('您已提交申请，请等待管理员审核')
        return redirect(url_for('my_projects'))
    
    # 获取表单数据
    password = request.form.get('password', '')
    email = request.form.get('email', '')
    developer_name = request.form.get('developer_name', '').strip()
    reason = request.form.get('reason', '').strip()
    
    # 验证必填字段
    if not password or not email or not developer_name or not reason:
        flash('请填写所有必填字段')
        return redirect(url_for('my_projects'))
    
    # 验证邮箱
    if email != current_user['email']:
        flash('邮箱验证失败，请输入注册时使用的邮箱')
        return redirect(url_for('my_projects'))
    
    # 验证密码
    if not bcrypt.checkpw(password.encode('utf-8'), current_user['password'].encode('utf-8')):
        flash('密码验证失败')
        return redirect(url_for('my_projects'))
    
    # 检查开发者名称是否已被使用
    users = load_users()
    existing_dev = next((u for u in users if u.get('developer_name') == developer_name and u.get('is_developer') and u['id'] != current_user['id']), None)
    if existing_dev:
        flash('该开发者名称已被使用')
        return redirect(url_for('my_projects'))
    
    # 更新用户申请信息
    current_user['developer_application'] = {
        'developer_name': developer_name,
        'reason': reason,
        'status': 'pending',
        'applied_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'reviewed_by': None,
        'reviewed_at': None,
        'review_reason': None
    }
    
    # 保存
    for i, u in enumerate(users):
        if u['id'] == current_user['id']:
            users[i] = current_user
            break
    save_users(users)
    
    flash('开发者申请已提交，请等待管理员审核')
    return redirect(url_for('my_projects'))


# 管理员查看开发者申请
@app.route('/admin/developer_applications')
def admin_developer_applications():
    if not is_admin():
        flash('您没有权限访问此页面')
        return redirect(url_for('index'))
    
    users = load_users()
    applicants = []
    for user in users:
        application = user.get('developer_application')
        if application and application.get('status') in ['pending', 'approved', 'rejected']:
            applicants.append({
                'user': user,
                'application': application
            })
    
    # 待审批的排前面，按申请时间倒序
    applicants.sort(key=lambda x: (0 if x['application']['status'] == 'pending' else 1, x['application'].get('applied_at', '')), reverse=True)
    
    return render_template('admin_developer_applications.html', applicants=applicants, current_user=get_current_user())


# 管理员处理开发者申请
@app.route('/admin/developer_application/<user_id>/<action>', methods=['POST'])
def process_developer_application(user_id, action):
    if not is_admin():
        flash('您没有权限执行此操作')
        return redirect(url_for('index'))
    
    if action not in ['approve', 'reject']:
        flash('无效的操作')
        return redirect(url_for('admin_developer_applications'))
    
    users = load_users()
    user = next((u for u in users if u['id'] == user_id), None)
    
    if not user:
        flash('用户不存在')
        return redirect(url_for('admin_developer_applications'))
    
    application = user.get('developer_application')
    if not application or application.get('status') != 'pending':
        flash('没有待处理的开发者申请')
        return redirect(url_for('admin_developer_applications'))
    
    if action == 'approve':
        application['status'] = 'approved'
        application['reviewed_by'] = session.get('username')
        application['reviewed_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        user['is_developer'] = True
        user['developer_name'] = application['developer_name']
        flash(f'已批准 {user["username"]} 的开发者申请')
    
    elif action == 'reject':
        reject_reason = request.form.get('reject_reason', '').strip()
        application['status'] = 'rejected'
        application['reviewed_by'] = session.get('username')
        application['reviewed_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        application['review_reason'] = reject_reason
        flash(f'已拒绝 {user["username"]} 的开发者申请')
    
    save_users(users)
    return redirect(url_for('admin_developer_applications'))

# 管理员查看待审核原创作品
@app.route('/admin/review_uploads')
def admin_review_uploads():
    if not is_admin():
        flash('您没有权限访问此页面')
        return redirect(url_for('index'))
    
    games = load_games()
    
    # 筛选原创作品
    pending = [g for g in games if g.get('origin') == 'original' and g.get('review_status') == 'pending_review']
    approved = [g for g in games if g.get('origin') == 'original' and g.get('review_status') == 'approved']
    rejected = [g for g in games if g.get('origin') == 'original' and g.get('review_status') == 'rejected']
    auto_approved = [g for g in games if g.get('origin') == 'original' and g.get('review_status') == 'auto_approved']
    
    pending.sort(key=lambda x: x.get('review_submit_time', ''))
    approved.sort(key=lambda x: x.get('review_time', ''), reverse=True)
    auto_approved.sort(key=lambda x: x.get('review_time', ''), reverse=True)
    rejected.sort(key=lambda x: x.get('review_time', ''), reverse=True)
    
    return render_template('admin_review_uploads.html',
                          pending=pending,
                          approved=approved,
                          rejected=rejected,
                          auto_approved=auto_approved,
                          current_user=get_current_user())

# 管理员审核原创作品
@app.route('/admin/review_upload/<game_id>/<action>', methods=['POST'])
def process_review_upload(game_id, action):
    if not is_admin():
        flash('您没有权限执行此操作')
        return redirect(url_for('index'))
    
    if action not in ['approve', 'reject']:
        flash('无效的操作')
        return redirect(url_for('admin_review_uploads'))
    
    games = load_games()
    game = next((g for g in games if g['id'] == game_id), None)
    
    if not game:
        flash('作品不存在')
        return redirect(url_for('admin_review_uploads'))
    
    if game.get('review_status') != 'pending_review':
        flash('该作品已被审核')
        return redirect(url_for('admin_review_uploads'))
    
    current_user = get_current_user()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    if action == 'approve':
        game['review_status'] = 'approved'
        game['reviewed_by'] = current_user['username']
        game['review_time'] = now
        game['review_notes'] = request.form.get('review_notes', '管理员审核通过')
        
        # 管理员通过时也执行安全检测
        import random
        risk_keywords = ['virus', 'malware', 'trojan', 'worm', 'ransomware',
                         'spyware', 'keylogger', 'backdoor', 'rootkit',
                         'crack', 'keygen', 'patch', 'injector', 'exploit',
                         'payload', 'shellcode', 'rat', 'botnet']
        filename_lower = (game.get('filename') or '').lower()
        has_risk = any(kw in filename_lower for kw in risk_keywords)
        if not has_risk:
            has_risk = random.random() < 0.15
        
        if has_risk:
            game['security_status'] = 'risk'
            game['review_notes'] += '（已检测到潜在风险）'
        else:
            game['security_status'] = 'passed'
            game['security_check_time'] = now
        
        flash(f'已通过《{game["title"]}》的审核上架申请')
    
    elif action == 'reject':
        reject_reason = request.form.get('reject_reason', '').strip()
        game['review_status'] = 'rejected'
        game['reviewed_by'] = current_user['username']
        game['review_time'] = now
        game['review_notes'] = reject_reason or '管理员拒绝上架'
        flash(f'已拒绝《{game["title"]}》的上架申请')
    
    save_games(games)
    return redirect(url_for('admin_review_uploads'))


# 管理员查看所有申诉
@app.route('/admin/appeals')
def admin_appeals():
    if not is_admin():
        flash('您没有权限访问此页面')
        return redirect(url_for('index'))
    
    appeals = load_appeals()
    # 按提交时间倒序排序
    appeals.sort(key=lambda x: x['created_at'], reverse=True)
    
    return render_template('admin_appeals.html', appeals=appeals, current_user=get_current_user())

# 管理员批准申诉
@app.route('/admin/appeal/<appeal_id>/approve', methods=['POST'])
def approve_appeal(appeal_id):
    if not is_admin():
        flash('您没有权限执行此操作')
        return redirect(url_for('index'))
    
    appeals = load_appeals()
    appeal = next((a for a in appeals if a['id'] == appeal_id), None)
    
    if not appeal or appeal['status'] != 'pending':
        flash('申诉不存在或已处理')
        return redirect(url_for('admin_appeals'))
    
    # 更新申诉状态
    appeal['status'] = 'approved'
    save_appeals(appeals)
    
    # 更新游戏的安全状态
    games = load_games()
    game = next((g for g in games if g['id'] == appeal['game_id']), None)
    if game:
        game['security_status'] = 'passed'
        save_games(games)
    
    flash('申诉已批准')
    return redirect(url_for('admin_appeals'))

# 管理员拒绝申诉
@app.route('/admin/appeal/<appeal_id>/reject', methods=['POST'])
def reject_appeal(appeal_id):
    if not is_admin():
        flash('您没有权限执行此操作')
        return redirect(url_for('index'))
    
    appeals = load_appeals()
    appeal = next((a for a in appeals if a['id'] == appeal_id), None)
    
    if not appeal or appeal['status'] != 'pending':
        flash('申诉不存在或已处理')
        return redirect(url_for('admin_appeals'))
    
    admin_reason = request.form.get('admin_reason', '').strip()
    if not admin_reason:
        flash('请填写拒绝原因')
        return redirect(url_for('admin_appeals'))
    
    # 更新申诉状态
    appeal['status'] = 'rejected'
    appeal['admin_reason'] = admin_reason
    save_appeals(appeals)
    
    flash('申诉已拒绝')
    return redirect(url_for('admin_appeals'))

# 提供申诉证据文件下载
@app.route('/appeal/evidence/<path:file_path>')
def serve_evidence_file(file_path):
    # 检查是否登录
    if not is_logged_in():
        flash('请先登录')
        return redirect(url_for('login'))
    
    current_user = get_current_user()
    appeals = load_appeals()
    
    # 检查当前用户是否有访问此文件的权限
    file_accessible = False
    for appeal in appeals:
        if file_path in appeal['evidence_files']:
            # 管理员可以访问所有证据文件
            if is_admin():
                file_accessible = True
                break
            # 普通用户只能访问自己的证据文件
            elif appeal['user_id'] == current_user['id']:
                file_accessible = True
                break
    
    if not file_accessible:
        flash('您没有权限访问此文件')
        return redirect(url_for('index'))
    
    full_path = os.path.join('appeal_evidence', file_path)
    if not os.path.exists(full_path):
        flash('文件不存在')
        return redirect(url_for('index'))
    
    return send_file(full_path, as_attachment=True)

# 管理员查看所有收费申请
@app.route('/admin/price_requests')
def admin_price_requests():
    if not is_admin():
        flash('您没有权限访问此页面')
        return redirect(url_for('index'))
    
    price_requests = load_price_requests()
    
    # 按请求时间倒序排序
    price_requests.sort(key=lambda x: x['request_time'], reverse=True)
    
    # 分类请求
    pending_requests = [req for req in price_requests if req['status'] == 'pending']
    approved_requests = [req for req in price_requests if req['status'] == 'approved']
    rejected_requests = [req for req in price_requests if req['status'] == 'rejected']
    
    return render_template('admin_price_requests.html', 
                          pending_requests=pending_requests, 
                          approved_requests=approved_requests, 
                          rejected_requests=rejected_requests, 
                          current_user=get_current_user())

# 管理员处理收费申请
@app.route('/admin/process_price_request', methods=['POST'])
def process_price_request():
    if not is_admin():
        flash('您没有权限访问此页面')
        return redirect(url_for('index'))
    
    request_id = request.form.get('request_id')
    action = request.form.get('action')
    feedback = request.form.get('feedback', '').strip()
    
    # 加载价格申请
    price_requests = load_price_requests()
    request_index = next((i for i, req in enumerate(price_requests) if str(req['id']) == request_id), None)
    
    if request_index is None:
        flash('收费申请不存在')
        return redirect(url_for('admin_price_requests'))
    
    current_request = price_requests[request_index]
    current_user = get_current_user()
    
    # 如果是通过请求
    if action == 'approve':
        # 检查申请用户是否为开发者，非开发者价格不能超过10元
        users = load_users()
        req_user = next((u for u in users if u['id'] == current_request['user_id']), None)
        if req_user and not req_user.get('is_developer') and current_request['requested_price'] > 10:
            flash('不能批准：该用户不是开发者，项目价格最高只能设为10元')
            return redirect(url_for('admin_price_requests'))
        
        price_requests[request_index]['status'] = 'approved'
        price_requests[request_index]['processed_by'] = current_user['username']
        price_requests[request_index]['processed_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        price_requests[request_index]['admin_feedback'] = feedback
        
        # 更新游戏价格
        games = load_games()
        game_index = next((i for i, g in enumerate(games) if g['id'] == current_request['project_id']), None)
        if game_index is not None:
            games[game_index]['price'] = current_request['requested_price']
            save_games(games)
        
        flash('收费申请已通过')
    # 如果是拒绝请求
    elif action == 'reject':
        price_requests[request_index]['status'] = 'rejected'
        price_requests[request_index]['processed_by'] = current_user['username']
        price_requests[request_index]['processed_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        price_requests[request_index]['admin_feedback'] = feedback
        
        # 发送拒绝邮件通知
        send_rejection_email(current_request, feedback)
        
        flash('收费申请已拒绝并已发送邮件通知')
    
    # 保存更新后的价格申请
    save_price_requests(price_requests)
    return redirect(url_for('admin_price_requests'))

# 确保必要的目录存在（包括申诉证据目录）
def ensure_directories():
    for dir_path in [app.config['UPLOAD_FOLDER'], app.config['STATIC_FOLDER'], app.config['TEMPLATES_FOLDER'], 'appeal_evidence']:
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)
    # 确保临时上传目录存在
    ensure_temp_uploads_exists()

def send_rejection_email(price_request, feedback):
    """
    通过弹窗系统发送收费申请拒绝通知
    
    Args:
        price_request: 价格申请对象
        feedback: 管理员的拒绝原因
    """
    try:
        # 加载相关数据
        users = load_users()
        games = load_games()
        
        # 获取申请人信息
        user = next((u for u in users if u['id'] == price_request['user_id']), None)
        if not user:
            print("警告: 找不到申请用户信息，无法发送通知")
            return
        
        # 获取项目信息
        game = next((g for g in games if g['id'] == price_request['project_id']), None)
        if not game:
            print("警告: 找不到申请项目信息，无法发送通知")
            return
        
        # 构建弹窗通知内容
        title = f"您的收费申请已被拒绝"
        content = {
            "project_name": game['title'],
            "reject_reason": feedback,
            "request_time": price_request['request_time'],
            "requested_price": price_request['requested_price']
        }
        
        # 创建弹窗通知
        try:
            notification = create_notification(
                user_id=user['id'],
                title=title,
                content=content,
                notification_type="price_request_rejection"
            )
            
            if notification:
                print(f"\n--- 弹窗通知创建成功 ---")
                print(f"接收者: {user['username']} (ID: {user['id']})")
                print(f"标题: {title}")
                print(f"项目: {game['title']}")
                print(f"--- 弹窗通知创建完成 ---")
            
        except Exception as notification_error:
            print(f"\n--- 弹窗通知创建失败: {str(notification_error)} ---")
        
    except Exception as e:
        print(f"发送拒绝通知失败: {str(e)}")

# 初始化应用（如果尚未初始化）
def init_app():
    if not hasattr(app, 'online_users'):
        app.online_users = set()
    
    # 确保必要的目录存在
    ensure_directories()

if __name__ == '__main__':
    init_app()
    app.run(debug=True, host='0.0.0.0')