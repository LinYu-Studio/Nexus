import os
import json

# 检查游戏目录结构
def check_game_directory():
    # 读取games.json
    with open('games.json', 'r', encoding='utf-8') as f:
        games = json.load(f)
    
    print("=== 游戏列表信息 ===")
    for game in games:
        print(f"游戏ID: {game['id']}")
        print(f"标题: {game['title']}")
        print(f"文件名: {game['filename']}")
        print(f"是否文件夹: {game.get('is_folder', False)}")
        
        # 检查游戏目录是否存在
        game_dir = os.path.join('games', game['id'])
        print(f"游戏目录: {game_dir}")
        if os.path.exists(game_dir):
            print(f"  目录存在")
            
            # 检查指定的文件是否存在
            file_path = os.path.join(game_dir, game['filename'])
            print(f"  指定文件路径: {file_path}")
            if os.path.exists(file_path):
                print(f"  文件存在，大小: {os.path.getsize(file_path) / 1024:.2f} KB")
            else:
                print(f"  文件不存在")
                
                # 查找目录中的可执行文件
                print("  查找可执行文件:")
                executables = []
                for root, dirs, files in os.walk(game_dir):
                    for file in files:
                        if file.lower().endswith('.exe'):
                            exe_path = os.path.join(root, file)
                            executables.append(exe_path)
                            print(f"    找到: {exe_path}")
                
                if not executables:
                    print("    未找到可执行文件")
        else:
            print(f"  目录不存在")
        print()

if __name__ == '__main__':
    check_game_directory()