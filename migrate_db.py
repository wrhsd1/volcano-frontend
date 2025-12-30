"""
数据库迁移脚本
将旧数据库结构迁移到新结构（支持图片生成功能）

运行方式:
    cd volcano-frontend
    python migrate_db.py
"""

import sqlite3
import shutil
from datetime import datetime
from pathlib import Path


def migrate_database():
    """执行数据库迁移"""
    
    db_path = Path("data/volcano.db")
    
    if not db_path.exists():
        print("数据库文件不存在，无需迁移。首次运行时会自动创建新结构。")
        return
    
    # 备份数据库
    backup_path = db_path.with_suffix(f".db.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy(db_path, backup_path)
    print(f"已备份数据库到: {backup_path}")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # 检查 accounts 表结构
        cursor.execute("PRAGMA table_info(accounts)")
        columns = {row[1] for row in cursor.fetchall()}
        
        # 迁移 accounts 表
        if "model_id" in columns and "video_model_id" not in columns:
            print("迁移 accounts 表: model_id -> video_model_id")
            
            # SQLite 不支持直接重命名列，需要通过创建新表的方式
            cursor.execute("""
                CREATE TABLE accounts_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(100) NOT NULL,
                    video_model_id VARCHAR(200),
                    image_model_id VARCHAR(200),
                    api_key VARCHAR(500) NOT NULL,
                    is_active BOOLEAN DEFAULT 1,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """)
            
            # 复制数据，将 model_id 迁移到 video_model_id
            cursor.execute("""
                INSERT INTO accounts_new (id, name, video_model_id, image_model_id, api_key, is_active, created_at, updated_at)
                SELECT id, name, model_id, NULL, api_key, is_active, created_at, updated_at
                FROM accounts
            """)
            
            # 删除旧表，重命名新表
            cursor.execute("DROP TABLE accounts")
            cursor.execute("ALTER TABLE accounts_new RENAME TO accounts")
            
            print("  ✓ accounts 表迁移完成")
        else:
            print("  accounts 表无需迁移")
        
        # 检查 daily_usages 表结构
        cursor.execute("PRAGMA table_info(daily_usages)")
        columns = {row[1] for row in cursor.fetchall()}
        
        if "used_images" not in columns:
            print("迁移 daily_usages 表: 添加 used_images 列")
            cursor.execute("ALTER TABLE daily_usages ADD COLUMN used_images INTEGER DEFAULT 0")
            print("  ✓ daily_usages 表迁移完成")
        else:
            print("  daily_usages 表无需迁移")
        
        # 检查 tasks 表结构
        cursor.execute("PRAGMA table_info(tasks)")
        columns = {row[1] for row in cursor.fetchall()}
        
        migrations_needed = []
        if "task_type" not in columns:
            migrations_needed.append(("task_type", "VARCHAR(20) DEFAULT 'video'"))
        if "result_urls" not in columns:
            migrations_needed.append(("result_urls", "TEXT"))
        if "image_count" not in columns:
            migrations_needed.append(("image_count", "INTEGER"))
        
        if migrations_needed:
            print("迁移 tasks 表: 添加新列")
            for col_name, col_def in migrations_needed:
                cursor.execute(f"ALTER TABLE tasks ADD COLUMN {col_name} {col_def}")
                print(f"  ✓ 添加列 {col_name}")
            print("  ✓ tasks 表迁移完成")
        else:
            print("  tasks 表无需迁移")
        
        conn.commit()
        print("\n✅ 数据库迁移成功完成!")
        print("\n注意: 请登录系统后在设置页面为需要图片生成的账户添加 image_model_id")
        
    except Exception as e:
        conn.rollback()
        print(f"\n❌ 迁移失败: {e}")
        print(f"已自动回滚。备份文件: {backup_path}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    migrate_database()
