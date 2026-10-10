
from getpass import getpass
from werkzeug.security import generate_password_hash
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "car_price.db"

email = input("Admin email: ").strip().lower()
username = input("Admin username: ").strip()
password = getpass("Admin password (minimum 8 characters): ")

if not username or not email or len(password) < 8:
    raise SystemExit("Username/email required; password must be at least 8 characters.")

with sqlite3.connect(DB_PATH) as db:
    try:
        db.execute(
            """
            INSERT INTO users (username, email, password_hash, role)
            VALUES (?, ?, ?, 'admin')
            """,
            (username, email, generate_password_hash(password))
        )
        print("Admin account created successfully.")
    except sqlite3.IntegrityError:
        print("This email already exists. Use another email.")
