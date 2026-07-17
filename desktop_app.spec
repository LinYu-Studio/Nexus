# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# 获取当前工作目录
current_dir = os.path.dirname(os.path.abspath(__file__))

# 收集PyWebView所需的数据文件
pywebview_datas = collect_data_files('webview')

# 主应用数据文件
datas = [
    ('templates', 'templates'),
    ('static', 'static'),
    ('games', 'games'),
    ('games.json', '.'),
    ('users.json', '.'),
    ('conversation.json', '.'),
    ('appeals.json', '.'),
]

# 合并所有数据文件
datas.extend(pywebview_datas)

# 必要的隐藏导入
hiddenimports = [
    'flask',
    'webview',
    'bcrypt',
    'werkzeug',
    'jinja2',
    'markupsafe',
    'itsdangerous',
    'click',
    'uuid',
    're',
    'shutil',
    'zipfile',
    'datetime',
    'os',
    'sys',
    'threading',
    'json',
]

# 为Windows系统优化的额外配置
extra_config = {
    'win_no_prefer_redirects': False,
    'win_private_assemblies': False
}

a = Analysis(['desktop_app.py'],
             pathex=[current_dir],
             binaries=[],
             datas=datas,
             hiddenimports=hiddenimports,
             hookspath=[],
             hooksconfig={},
             runtime_hooks=[],
             excludes=[],
             cipher=block_cipher,
             noarchive=False,
             **extra_config)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(pyz,
          a.scripts,
          [],
          exclude_binaries=True,
          name='LVMO_GAME',
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          console=False,
          disable_windowed_traceback=False,
          target_arch=None,
          codesign_identity=None,
          entitlements_file=None,
          icon=None)  # 如果有图标文件，可以在这里添加路径

coll = COLLECT(exe,
               a.binaries,
               a.zipfiles,
               a.datas,
               strip=False,
               upx=True,
               upx_exclude=[],
               name='LVMO_GAME')