"""
多账户功能数据库迁移脚本
为 tasks 表添加 submitted_by 字段

运行方式:
    cd volcano-frontend
    python migrate_multi_user.py
"""

import sqlite3
import shutil
from datetime import datetime
from pathlib import Path


def migrate_database():
    """执行多账户功能数据库迁移"""
    
    db_path = Path("data/volcano.db")
    
    if not db_path.exists():
        print("数据库文件不存在，无需迁移。首次运行时会自动创建新结构。")
        return
    
    # 备份数据库
    backup_path = db_path.with_suffix(f".db.backup_multiuser_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy(db_path, backup_path)
    print(f"已备份数据库到: {backup_path}")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # 检查 tasks 表是否已有 submitted_by 列
        cursor.execute("PRAGMA table_info(tasks)")
        columns = {row[1] for row in cursor.fetchall()}
        
        if "submitted_by" not in columns:
            print("迁移 tasks 表: 添加 submitted_by 列")
            
            # 添加 submitted_by 列，默认值为 'admin'（现有任务都视为管理员提交）
            cursor.execute("ALTER TABLE tasks ADD COLUMN submitted_by VARCHAR(50) DEFAULT 'admin'")
            
            # 更新现有记录确保都有值
            cursor.execute("UPDATE tasks SET submitted_by = 'admin' WHERE submitted_by IS NULL")
            
            print("  ✓ submitted_by 列添加完成")
            print("  ✓ 现有任务已标记为 admin 提交")
        else:
            print("  tasks 表已有 submitted_by 列，无需迁移")
        
        conn.commit()
        print("\n✅ 多账户功能数据库迁移成功完成!")
        print("\n使用说明:")
        print("  1. 在 .env 文件中设置 GUEST_PASSWORD1, GUEST_PASSWORD2 等访客密码")
        print("  2. 访客登录后只能看到自己提交的任务，无法访问设置页面")
        print("  3. 管理员（使用 ACCESS_PASSWORD 登录）可以看到所有任务")
        
    except Exception as e:
        conn.rollback()
        print(f"\n❌ 迁移失败: {e}")
        print(f"已自动回滚。备份文件: {backup_path}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    migrate_database()
