import os
import glob

db_dir = os.path.join(os.path.dirname(__file__), 'models', 'db')
files = glob.glob(os.path.join(db_dir, '*.py'))

count = 0
for filepath in files:
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    if 'from src.database.connection import Base' in content:
        new_content = content.replace('from src.database.connection import Base', 'from database.connection import Base')
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        count += 1
        print(f'Fixed: {os.path.basename(filepath)}')

print(f'Total fixed: {count} files')
