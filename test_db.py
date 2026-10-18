import os
from dotenv import load_dotenv
from sqlalchemy import create_engine

# Load .env file
load_dotenv()

# Get the raw string
db_url = os.getenv("DATABASE_URL")

print("--- DEBUG INFO ---")
print(f"Type: {type(db_url)}")
print(f"Length: {len(str(db_url))}")
print(f"Starts with 'postgresql': {str(db_url).startswith('postgresql')}")
print(f"Contains 'channel_binding': {'channel_binding' in str(db_url)}")
print("------------------")

if not db_url or not str(db_url).startswith('postgresql'):
    print("❌ CRITICAL: URL is invalid or None. Check .env file content.")
else:
    try:
        engine = create_engine(db_url)
        conn = engine.connect()
        print("✅ NEON DB CONNECTED SUCCESSFULLY!")
        conn.close()
    except Exception as e:
        print(f" CONNECTION ERROR: {e}")
