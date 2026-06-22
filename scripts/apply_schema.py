import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

db_url = os.environ.get("DATABASE_URL")

if not db_url:
    print("ERROR: DATABASE_URL not found. Check your .env file.")
    raise SystemExit(1)

print(f"Connecting to {db_url.split('@')[-1]}...")

conn = psycopg2.connect(db_url)
cur = conn.cursor()

with open("scripts/schema.sql", "r") as f:
    schema_sql = f.read()

cur.execute(schema_sql)
conn.commit()

print("Schema created successfully (products table + indexes).")

cur.close()
conn.close()