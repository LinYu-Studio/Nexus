# 欢迎来到Nexus

这是一个基于Flask的简单游戏发布平台，可以让用户上传、下载和管理游戏文件。

## 功能特性

- 上传游戏文件（支持各种游戏文件格式）
- 查看游戏列表，显示游戏标题、描述、作者、上传时间和下载次数
- 下载游戏文件
- 删除游戏文件
- 响应式设计，适配不同屏幕尺寸

## 技术栈

- Python
- Flask
- HTML/CSS
- JSON（用于存储游戏信息）

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 运行应用

```bash
python main.py
```

### 3. 访问平台

打开浏览器，访问 http://localhost:5000

## 项目结构

```
Nexus/
├── main.py            # 主应用程序文件
├── requirements.txt   # Python依赖包列表
├── games.json         # 存储游戏信息的JSON文件
├── games/             # 存储上传的游戏文件的目录
├── static/            # 静态文件目录
│   └── style.css      # CSS样式文件
└── templates/         # HTML模板目录
    ├── index.html     # 首页模板（游戏列表）
    └── upload.html    # 上传游戏页面模板
```

## 注意事项

- 此应用使用简单的JSON文件存储游戏信息，适合个人或小型项目使用
- 如需在生产环境中使用，请考虑使用数据库存储游戏信息并添加用户认证系统
- 当前版本没有文件大小限制和文件类型验证，请根据实际需求添加相关功能

## 许可证

本项目采用 MIT 许可证
