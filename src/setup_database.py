#!/usr/bin/env python3
"""
Database setup and migration script for Recruitment System
"""
import os
import sys
import subprocess
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

def load_config():
    """Load configuration from .env file"""
    load_dotenv()
    return {
        'database_url': os.getenv('DATABASE_URL', 'postgresql://postgres:12345@localhost:5432/recruitment_system_db'),
        'secret_key': os.getenv('SECRET_KEY', 'your-super-secret-jwt-key-change-in-production')
    }

def test_postgres_connection():
    """Test if PostgreSQL is running and accessible"""
    try:
        # Try connecting to postgres database first
        postgres_url = 'postgresql://postgres:12345@localhost:5432/postgres'
        engine = create_engine(postgres_url)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version()"))
            print("✅ PostgreSQL is running")
            print(f"📊 Version: {result.fetchone()[0]}")
            return True
    except Exception as e:
        print(f"❌ PostgreSQL connection failed: {e}")
        return False

def create_database():
    """Create the recruitment_system_db database if it doesn't exist"""
    try:
        postgres_url = 'postgresql://postgres:12345@localhost:5432/postgres'
        engine = create_engine(postgres_url)
        with engine.connect() as conn:
            conn.execute(text("COMMIT"))
            try:
                conn.execute(text("CREATE DATABASE recruitment_system_db"))
                print("✅ Created recruitment_system_db database")
            except Exception as e:
                if "already exists" in str(e):
                    print("✅ Database recruitment_system_db already exists")
                else:
                    raise e
        return True
    except Exception as e:
        print(f"❌ Failed to create database: {e}")
        return False

def run_alembic_migration():
    """Run Alembic migration"""
    try:
        print("🚀 Running Alembic migration...")
        result = subprocess.run(['alembic', 'upgrade', 'head'], 
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
    """Verify that migration was successful"""
    try:
        config = load_config()
        engine = create_engine(config['database_url'])
        with engine.connect() as conn:
            # Check if companies table has the new columns
            try:
                result = conn.execute(text("""
                    SELECT column_name, data_type, is_nullable 
                    FROM information_schema.columns 
                    WHERE table_name = 'companies' 
                    AND column_name IN ('email', 'password_hash', 'first_name', 'last_name')
                    ORDER BY column_name
                """))
                columns = result.fetchall()
                
                if len(columns) >= 4:
                    print("✅ Company table has required authentication columns:")
                    for col in columns:
                        print(f"   - {col[0]}: {col[1]} (nullable: {col[2]})")
                else:
                    print(f"❌ Company table missing columns. Found: {len(columns)}")
                    return False
                
                # Check if recruiters table was dropped
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
                
                # Check alembic version
                try:
                    result = conn.execute(text("SELECT version_num FROM alembic_version"))
                    current_version = result.fetchone()
                    if current_version:
                        print(f"✅ Current Alembic version: {current_version[0]}")
                    else:
                        print("❌ No Alembic version found")
                        return False
                except Exception as e:
                    print(f"❌ Error checking Alembic version: {e}")
                    return False
                    
            except Exception as e:
                print(f"❌ Error verifying schema: {e}")
                return False
                
        return True
    except Exception as e:
        print(f"❌ Verification failed: {e}")
        return False

def main():
    """Main setup function"""
    print("🔍 Recruitment System Database Setup")
    print("=" * 40)
    
    # Load configuration
    config = load_config()
    print(f"📋 Database URL: {config['database_url']}")
    print(f"🔐 Secret Key: {'✅ Set' if config['secret_key'] else '❌ Missing'}")
    print()
    
    # Step 1: Test PostgreSQL connection
    if not test_postgres_connection():
        print("\n❌ Please start PostgreSQL service and try again")
        return False
    
    # Step 2: Create database
    if not create_database():
        print("\n❌ Database setup failed")
        return False
    
    # Step 3: Run migration
    if not run_alembic_migration():
        print("\n❌ Migration failed")
        return False
    
    # Step 4: Verify migration
    if not verify_migration():
        print("\n❌ Migration verification failed")
        return False
    
    print("\n🎉 Database setup completed successfully!")
    print("🚀 Your authentication system is ready to use!")
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
