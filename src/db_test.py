import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()
database_url = os.getenv('DATABASE_URL', 'postgresql://postgres:12345@localhost:5432/recruitment_system_db')
print(f'Database URL: {database_url}')

try:
    # Try to connect to the specific database
    engine = create_engine(database_url)
    with engine.connect() as conn:
        print(' Connected to recruitment_system_db')
        result = conn.execute(text('SELECT version()'))
        print(f'PostgreSQL: {result.fetchone()[0]}')
        
        # Check alembic version
        try:
            result = conn.execute(text('SELECT version_num FROM alembic_version'))
            current_version = result.fetchone()
            if current_version:
                print(f'Current Alembic version: {current_version[0]}')
            else:
                print('No Alembic version found')
        except Exception as e:
            print(f'Alembic version table error: {e}')
            
except Exception as e:
    print(f' Could not connect to recruitment_system_db: {e}')
    
    # Try to connect to postgres database and create the database
    try:
        postgres_url = database_url.replace('/recruitment_system_db', '/postgres')
        engine = create_engine(postgres_url)
        with engine.connect() as conn:
            conn.execute(text('COMMIT'))  # Close any transaction
            conn.execute(text('CREATE DATABASE recruitment_system_db'))
            print(' Created recruitment_system_db database')
    except Exception as e2:
        print(f' Could not create database: {e2}')
