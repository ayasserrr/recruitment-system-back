#!/usr/bin/env python3
"""
Fix database schema from scratch for Recruitment System
"""
import os
import sys
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

def load_config():
    """Load configuration from .env file"""
    load_dotenv()
    return os.getenv('DATABASE_URL', 'postgresql://postgres:12345@localhost:5432/recruitment_system_db')

def check_tables_exist():
    """Check if any tables exist in the database"""
    try:
        database_url = load_config()
        engine = create_engine(database_url)
        
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT table_name FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """))
            tables = [row[0] for row in result.fetchall()]
            print(f"📊 Existing tables: {tables}")
            return len(tables) > 0
            
    except Exception as e:
        print(f"❌ Error checking tables: {e}")
        return False

def run_initial_migration():
    """Run the initial schema migration"""
    try:
        print("🚀 Running initial schema migration...")
        result = os.system("alembic upgrade 462c07477351")
        if result == 0:
            print("✅ Initial schema migration completed!")
            return True
        else:
            print("❌ Initial schema migration failed")
            return False
    except Exception as e:
        print(f"❌ Error running initial migration: {e}")
        return False

def run_merge_migration():
    """Run the merge recruiter migration"""
    try:
        print("🚀 Running merge recruiter migration...")
        result = os.system("alembic upgrade a1b2c3d4e5f6")
        if result == 0:
            print("✅ Merge recruiter migration completed!")
            return True
        else:
            print("❌ Merge recruiter migration failed")
            return False
    except Exception as e:
        print(f"❌ Error running merge migration: {e}")
        return False

def verify_migration():
    """Verify the migration was successful"""
    try:
        database_url = load_config()
        engine = create_engine(database_url)
        
        with engine.connect() as conn:
            # Check if companies table exists
            try:
                result = conn.execute(text("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name = 'companies' 
                    AND column_name = 'email'
                """))
                email_column = result.fetchone()
                
                if email_column:
                    print("✅ Company table has email column")
                else:
                    print("❌ Company table missing email column")
                    return False
                    
            except Exception as e:
                print(f"❌ Error checking company table: {e}")
                return False
            
            # Check if recruiters table exists
            try:
                result = conn.execute(text("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = 'recruiters'
                    )
                """))
                recruiters_exists = result.fetchone()[0]
                
                if not recruiters_exists:
                    print("✅ Recruiters table successfully dropped")
                else:
                    print("❌ Recruiters table still exists")
                    return False
                    
            except Exception as e:
                print(f"❌ Error checking recruiters table: {e}")
                return False
                
            # Check current alembic version
            try:
                result = conn.execute(text("SELECT version_num FROM alembic_version"))
                current_version = result.fetchone()
                if current_version:
                    print(f"✅ Current Alembic version: {current_version[0]}")
                    return current_version[0] == 'a1b2c3d4e5f6'
                else:
                    print("❌ No Alembic version found")
                    return False
            except Exception as e:
                print(f"❌ Error checking Alembic version: {e}")
                return False
                
    except Exception as e:
        print(f"❌ Verification failed: {e}")
        return False

def main():
    """Main function"""
    print("🔧 Complete Database Setup from Scratch")
    print("=" * 45)
    
    # Step 1: Check if any tables exist
    if check_tables_exist():
        print("📊 Database already has tables. Proceeding with migration...")
    else:
        print("📝 Database is empty. Starting from scratch...")
    
    # Step 2: Run initial migration
    print("\n🔧 Step 1: Running initial schema migration...")
    if not run_initial_migration():
        print("❌ Initial migration failed. Please check the error above.")
        return False
    
    # Step 3: Run merge migration
    print("\n🔧 Step 2: Running merge recruiter migration...")
    if not run_merge_migration():
        print("❌ Merge migration failed. Please check the error above.")
        return False
    
    # Step 4: Verify migration
    print("\n🔧 Step 3: Verifying migration...")
    if not verify_migration():
        print("❌ Migration verification failed.")
        return False
    
    print("\n🎉 Complete database setup finished successfully!")
    print("🚀 Your authentication system is ready to use!")
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
