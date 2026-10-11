import os
import sqlite3
from datetime import datetime

from app.core.logger import setup_logger

logger = setup_logger(__name__)


# Duong dan den file SQLite local
DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
if not os.path.exists(DB_DIR):
    os.makedirs(DB_DIR)

DB_PATH = os.path.join(DB_DIR, "ai_training.db")


def init_db():
    """Khoi tao database và bang neu chua co"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS training_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            platform TEXT,
            combined_text TEXT,
            category TEXT,
            niche TEXT,
            creator_type TEXT,
            followers INTEGER,
            source TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Kiem tra va them cot neu chua co (do DB da tao truoc do)
    try:
        cursor.execute("ALTER TABLE training_logs ADD COLUMN creator_type TEXT")
        cursor.execute("ALTER TABLE training_logs ADD COLUMN followers INTEGER")
    except sqlite3.OperationalError:
        pass  # Cot da ton tai

    conn.commit()
    conn.close()


def save_training_data(
    username: str,
    platform: str,
    combined_text: str,
    category: str,
    niche: str,
    creator_type: str = "UNKNOWN",
    followers: int = 0,
    source: str = "GEMINI_AUTO_LABEL",
):
    """Luu 1 record data vao bang training_logs"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO training_logs (username, platform, combined_text, category, niche, creator_type, followers, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                username,
                platform,
                combined_text,
                category,
                niche,
                creator_type,
                followers,
                source,
                datetime.now(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.info(f"[DB Error] Khong the luu training data: {e}")


# Tu dong tao bang luc import
init_db()
