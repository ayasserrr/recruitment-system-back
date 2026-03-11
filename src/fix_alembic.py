#!/usr/bin/env python3
"""
Fix Alembic migration issues for Recruitment System
"""
import os
import sys
import subprocess
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

def load_config():
    """Load configuration from .env file"""
    load_dotenv()
    return os.getenv('DATABASE_URL', 'postgresql://postgres:12345@localhost:5432/recruitment_system_db')

def check_database_state():
    """Check current database state"""
    try:
        database_url = load_config()
        engine = create_engine(database_url)
        
        with engine.connect() as conn:
            # Check if alembic_version table exists
            try:
                result = conn.execute(text("SELECT version_num FROM alembic_version"))
                current_version = result.fetchone()
                if current_version:
                    print(f"🔢 Current Alembic version in database: {current_version[0]}")
                    return current_version[0]
                else:
                    print("📝 No Alembic version found in database")
                    return None
            except Exception as e:
                print(f"📝 Alembic version table doesn't exist: {e}")
                return None
                
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return None

def fix_alembic_version():
    """Fix the alembic version table"""
    try:
        database_url = load_config()
        engine = create_engine(database_url)
        
        with engine.connect() as conn:
            # Drop and recreate alembic_version table
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            conn.execute(text("""
                CREATE TABLE alembic_version (
                    version_num VARCHAR(32) NOT NULL
                )
            """))
            
            # Set to the initial schema version
            conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('462c07477351')"))
            conn.commit()
            
            print("✅ Fixed Alembic version table - set to initial schema")
            return True
            
    except Exception as e:
        print(f"❌ Failed to fix Alembic version: {e}")
        return False

def run_migration():
    """Run the migration to merge recruiter into company"""
    try:
        print("🚀 Running migration to merge recruiter into company...")
        result = subprocess.run(['alembic', 'upgrade', 'a1b2c3d4e5f6'], 
                              capture_output=True, text=True, timeout=60)
        
        if result.returncode == 0:
            print("✅ Migration completed successfully!")
            if result.stdout:
                print(f"📝 Output: {result.stdout}")
            return True
        else:
            print(f"❌ Migration failed: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        print("❌ Migration timed out")
        return False
    except Exception as e:
        print(f"❌ Migration error: {e}")
        return False

def verify_migration():
    """Verify the migration was successful"""
    try:
        database_url = load_config()
        engine = create_engine(database_url)
        
        with engine.connect() as conn:
            # Check current version
            result = conn.execute(text("SELECT version_num FROM alembic_version"))
            current_version = result.fetchone()
            print(f"🔢 Current Alembic version: {current_version[0] if current_version else 'None'}")
            
            # Check if companies table has new columns
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
                
        return True
        
    except Exception as e:
        print(f"❌ Verification failed: {e}")
        return False

def main():
    """Main fix function"""
    print("🔧 Fixing Alembic Migration Issues")
    print("=" * 40)
    
    # Step 1: Check current state
    current_version = check_database_state()
    
    # Step 2: Fix alembic version if needed
    if current_version == '0241a4faca35' or current_version is None:
        print("🔧 Fixing Alembic version table...")
        if not fix_alembic_version():
            print("❌ Failed to fix Alembic version")
            return False
    
    # Step 3: Run migration
    if not run_migration():
        print("❌ Migration failed")
        return False
    
    # Step 4: Verify migration
    if not verify_migration():
        print("❌ Migration verification failed")
        return False
    
    print("\n🎉 Migration fix completed successfully!")
    print("🚀 Your authentication system is ready!")
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
