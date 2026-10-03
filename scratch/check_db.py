import sqlite3
import os

def check_db():
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "ai", "data", "ai_training.db")
    
    if not os.path.exists(db_path):
        print("Database chua duoc tao. Hay doi he thong AI chay va gap case UNKNOWN de no luu data dau tien nhe.")
        return
        
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Đếm tổng số dòng
        cursor.execute("SELECT COUNT(*) FROM training_logs")
        total = cursor.fetchone()[0]
        
        # Thống kê theo Category
        cursor.execute("SELECT category, COUNT(*) FROM training_logs GROUP BY category")
        cat_stats = cursor.fetchall()
        
        # Thống kê theo Creator Type
        cursor.execute("SELECT creator_type, COUNT(*) FROM training_logs GROUP BY creator_type")
        type_stats = cursor.fetchall()
        
        print("="*40)
        print(f"📊 TỔNG SỐ DỮ LIỆU ĐÃ GÁN NHÃN: {total} record")
        print("="*40)
        print("1. CHI TIẾT THEO CATEGORY:")
        for cat, count in cat_stats:
            print(f" - {cat}: {count} record")
            
        print("\n2. CHI TIẾT THEO CREATOR TYPE (KOL/KOC):")
        for ctype, count in type_stats:
            print(f" - {ctype}: {count} record")
            
        print("\n(Run lai script nay bat cu luc nao de xem tien do nhe!)")
        conn.close()
    except Exception as e:
        print(f"Lỗi: {e}")

if __name__ == "__main__":
    check_db()
