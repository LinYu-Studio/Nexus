import bcrypt

# 生成admin密码的bcrypt哈希值
password = "admin"
hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

print(f"原始密码: {password}")
print(f"bcrypt哈希值: {hashed_password}")