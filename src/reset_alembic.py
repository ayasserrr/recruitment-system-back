#!/usr/bin/env python3
"""
Complete Alembic reset for Recruitment System
"""
import os
import sys
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

def load_config():
    """Load configuration from .env file"""
    load_dotenv()
    return os.getenv('DATABASE_URL', 'postgresql://postgres:12345@localhost:5432/recruitment_system_db')

def reset_alembic_completely():
    """Completely reset Alembic state"""
    try:
        database_url = load_config()
        engine = create_engine(database_url)
        
        with engine.connect() as conn:
            print("🔧 Resetting Alembic state...")
            
            # Drop alembic_version table completely
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            conn.commit()
            
            # Recreate alembic_version table
            conn.execute(text("""
                CREATE TABLE alembic_version (
                    version_num VARCHAR(32) NOT NULL
                )
            """))
            conn.commit()
            
            # Set to the initial schema version
            conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('462c07477351')"))
            conn.commit()
            
            print("✅ Alembic state reset to initial schema (462c07477351)")
            return True
            
    except Exception as e:
        print(f"❌ Failed to reset Alembic: {e}")
        return False

def run_commands():
    """Run the manual commands"""
    commands = [
        "alembic heads",
        "alembic history", 
        "alembic current",
        "alembic upgrade a1b2c3d4e5f6",
        "alembic current"
    ]
    
    for cmd in commands:
        print(f"\n🔧 Running: {cmd}")
        result = os.system(cmd)
        if result != 0:
            print(f"❌ Command failed: {cmd}")

def main():
    """Main function"""
    print("🔧 Complete Alembic Reset")
    print("=" * 30)
    
    # Step 1: Reset Alembic state
    if not reset_alembic_completely():
        print("❌ Failed to reset Alembic state")
        return False
    
    print("\n🚀 Now run these commands manually:")
    print("cd src")
    print("alembic heads")
    print("alembic upgrade a1b2c3d4e5f6")
    print("alembic current")
    
    return True

if __name__ == "__main__":
    main()
