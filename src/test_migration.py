#!/usr/bin/env python3
import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database.connection import engine, Base
from sqlalchemy import text
import models.db

def test_connection():
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version()"))
            version = result.fetchone()[0]
            print(f"✅ Database connected successfully!")
            print(f"📊 PostgreSQL version: {version}")
            
            # Check if alembic_version table exists
            try:
                result = conn.execute(text("SELECT version_num FROM alembic_version"))
                current_version = result.fetchone()
                if current_version:
                    print(f"🔢 Current Alembic version: {current_version[0]}")
                else:
                    print("📝 No Alembic version found - fresh database")
            except Exception:
                print("📝 Alembic version table doesn't exist - fresh database")
                
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False
    
    return True

def run_migration():
    try:
        print("🚀 Running Alembic migration...")
        os.system("alembic upgrade head")
        print("✅ Migration completed successfully!")
    except Exception as e:
        print(f"❌ Migration failed: {e}")

if __name__ == "__main__":
    print("🔍 Testing database connection...")
    if test_connection():
        print("\n📋 Running migration...")
        run_migration()
    else:
        print("\n❌ Please check your database configuration in .env file")
