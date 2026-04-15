#!/usr/bin/env python3
import os
import sys
from pathlib import Path

# Read .env in current directory
env_path = Path.cwd() / ".env"
if not env_path.exists():
    print("ERROR: .env file not found in working directory.")
    sys.exit(2)

# Simple .env parser
env = {}
with env_path.open(encoding='utf-8') as f:
    for line in f:
        line=line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            continue
        k,v=line.split('=',1)
        env[k.strip()] = v.strip().strip('"').strip("'")

# Normalize keys to lowercase for flexibility
env_lower = {k.lower(): v for k, v in env.items()}
required = ['db_host','db_port','db_user','db_password','db_name']
missing = [r for r in required if r not in env_lower]
if missing:
    print(f"ERROR: Missing required DB values in .env: {', '.join(missing)}")
    sys.exit(2)

host = env_lower['db_host']
port = int(env_lower.get('db_port') or 3306)
user = env_lower['db_user']
password = env_lower['db_password']
db = env_lower['db_name']

sql_path = Path.cwd() / 'scripts' / 'add_manually_moved.sql'
if not sql_path.exists():
    print(f"ERROR: Migration SQL file not found: {sql_path}")
    sys.exit(2)

sql_text = sql_path.read_text(encoding='utf-8')
# Split statements by ; -- naive but fine for simple ALTERs
statements = [s.strip() for s in sql_text.split(';') if s.strip()]

try:
    import pymysql
except Exception as e:
    print('MISSING_PYMYSQL')
    sys.exit(3)

conn = None
try:
    conn = pymysql.connect(host=host, port=port, user=user, password=password, database=db, charset='utf8mb4', autocommit=False)
    cursor = conn.cursor()
    results = []
    for i,stmt in enumerate(statements, start=1):
        try:
            cursor.execute(stmt)
            results.append((i, 'OK'))
        except Exception as e:
            results.append((i, f'ERROR: {e}'))
            # stop on error
            break
    if all(r[1]=='OK' for r in results):
        conn.commit()
        print('MIGRATION_APPLIED')
        for i,res in results:
            print(f'STMT {i}: OK')
        sys.exit(0)
    else:
        conn.rollback()
        print('MIGRATION_FAILED')
        for i,res in results:
            print(f'STMT {i}: {res}')
        sys.exit(4)
except Exception as e:
    print('CONNECTION_ERROR')
    print(str(e))
    sys.exit(5)
finally:
    if conn:
        conn.close()
