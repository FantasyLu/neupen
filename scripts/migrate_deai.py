import sqlite3, os
db_path = os.path.join(os.path.dirname(__file__), 'core', 'data', 'novels.db')
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("PRAGMA table_info(novels)")
columns = [row[1] for row in cursor.fetchall()]
if 'deai_rules' not in columns:
    cursor.execute('ALTER TABLE novels ADD COLUMN deai_rules TEXT')
    conn.commit()
    print('deai_rules column added')
else:
    print('deai_rules column already exists')
conn.close()
