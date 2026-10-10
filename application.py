from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from pathlib import Path
import sqlite3
import pickle
import traceback
import pandas as pd
import numpy as np

import os
import uuid
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from PIL import Image, UnidentifiedImageError

app = Flask(__name__)
load_dotenv()

app.secret_key = os.environ["SECRET_KEY"]

app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER")
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", "587"))
app.config["MAIL_USE_TLS"] = os.getenv("MAIL_USE_TLS", "True").lower() == "true"
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER")

mail = Mail(app)

reset_serializer = URLSafeTimedSerializer(app.secret_key)

CORS(app)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "car_price.db"


# ---------------- PROFILE PHOTO UPLOAD ----------------

UPLOAD_FOLDER = BASE_DIR / "static" / "uploads" / "profiles"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2 MB

ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


def allowed_profile_image(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS
    )







def is_valid_profile_image(photo):
    try:
        photo.stream.seek(0)

        with Image.open(photo.stream) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                return False

            image.verify()

        photo.stream.seek(0)
        return True

    except (UnidentifiedImageError, OSError, ValueError):
        return False












with open(BASE_DIR / "LinearRegressionModel.pkl", "rb") as file:
    model = pickle.load(file)

car = pd.read_csv(BASE_DIR / "Cleaned_Car_data.csv")


# ---------------- DATABASE ----------------

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                company TEXT NOT NULL,
                car_model TEXT NOT NULL,
                year INTEGER NOT NULL,
                fuel_type TEXT NOT NULL,
                kilometres INTEGER NOT NULL,
                predicted_price REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
       
        # Add profile_image column if it does not exist
        user_columns = [
            row["name"]
            for row in db.execute("PRAGMA table_info(users)").fetchall()
        ]

        if "profile_image" not in user_columns:
            db.execute(
                "ALTER TABLE users ADD COLUMN profile_image TEXT"
            )



init_db()


# ---------------- AUTH HELPERS ----------------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "info")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


# ---------------- SHARED TEMPLATE DATA ----------------

def get_car_options():
    companies = sorted(car["company"].dropna().unique().tolist())
    car_models = sorted(car["name"].dropna().unique().tolist())
    years = sorted(car["year"].dropna().unique().tolist(), reverse=True)
    fuel_types = sorted(car["fuel_type"].dropna().unique().tolist())
    company_models = (
        car.groupby("company")["name"]
        .apply(lambda names: sorted(names.dropna().unique().tolist()))
        .to_dict()
    )
    companies.insert(0, "Select Company")
    return {
        "companies": companies,
        "car_models": car_models,
        "company_models": company_models,
        "years": years,
        "fuel_types": fuel_types
    }


# ---------------- HOME ----------------

@app.route("/")
def home():
    if "user_id" in session:
        if session.get("role") == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


# ---------------- REGISTER ----------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not username or not email or not password:
            flash("Please fill in all required fields.", "error")
            return render_template("register.html")
        if len(password) < 8:
            flash("Password must contain at least 8 characters.", "error")
            return render_template("register.html")
        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("register.html")

        try:
            with get_db() as db:
                db.execute(
                    """INSERT INTO users (username, email, password_hash, role)
                       VALUES (?, ?, ?, 'user')""",
                    (username, email, generate_password_hash(password))
                )
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("This email is already registered.", "error")

    return render_template("register.html")


# ---------------- LOGIN ----------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        with get_db() as db:
            user = db.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            if user["role"] == "admin":
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "error")

    return render_template("login.html")


# ---------------- LOGOUT ----------------

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# ---------------- USER DASHBOARD ----------------

@app.route("/dashboard")
@login_required
def dashboard():
    with get_db() as db:
        total = db.execute(
            "SELECT COUNT(*) FROM predictions WHERE user_id = ?",
            (session["user_id"],)
        ).fetchone()[0]
        recent = db.execute(
            """SELECT * FROM predictions WHERE user_id = ?
               ORDER BY id DESC LIMIT 5""",
            (session["user_id"],)
        ).fetchall()

    return render_template(
        "dashboard.html",
        username=session["username"],
        total_predictions=total,
        recent_predictions=recent
    )


# ---------------- USER PROFILE ----------------

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user_id = session["user_id"]

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        photo = request.files.get("profile_image")

        if not username or not email:
            flash("Username and email are required.", "error")
            return redirect(url_for("profile"))

        with get_db() as db:
            existing_user = db.execute(
                """
                SELECT id FROM users
                WHERE lower(email) = ? AND id != ?
                """,
                (email, user_id)
            ).fetchone()

            if existing_user:
                flash("This email is already registered.", "error")
                return redirect(url_for("profile"))

            user = db.execute(
                "SELECT profile_image FROM users WHERE id = ?",
                (user_id,)
            ).fetchone()

            if user is None:
                session.clear()
                flash("Please sign in again.", "error")
                return redirect(url_for("login"))

            profile_image = user["profile_image"]

            if photo and photo.filename:
                if not allowed_profile_image(photo.filename):
                    flash(
                        "Please upload a JPG, JPEG, PNG or WEBP image.",
                        "error"
                    )
                    return redirect(url_for("profile"))

                if not is_valid_profile_image(photo):
                    flash(
                        "The selected file is not a valid image.",
                        "error"
                    )
                    return redirect(url_for("profile"))

                extension = photo.filename.rsplit(".", 1)[1].lower()
                filename = f"{uuid.uuid4().hex}.{extension}"
                upload_path = UPLOAD_FOLDER / filename

                photo.save(str(upload_path))
                profile_image = filename

            db.execute(
                """
                UPDATE users
                SET username = ?, email = ?, profile_image = ?
                WHERE id = ?
                """,
                (username, email, profile_image, user_id)
            )

        session["username"] = username
        flash("Profile updated successfully.", "success")
        return redirect(url_for("profile"))

    with get_db() as db:
        user = db.execute(
            """
            SELECT id, username, email, role, created_at, profile_image
            FROM users
            WHERE id = ?
            """,
            (user_id,)
        ).fetchone()

    if user is None:
        session.clear()
        flash("Please sign in again.", "error")
        return redirect(url_for("login"))

    return render_template("profile.html", user=user)






# ---------------- ACCOUNT SETTINGS ----------------

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    user_id = session["user_id"]

    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not current_password or not new_password or not confirm_password:
            flash("Please fill in all password fields.", "error")
            return redirect(url_for("settings"))

        if len(new_password) < 8:
            flash("New password must be at least 8 characters.", "error")
            return redirect(url_for("settings"))

        if new_password != confirm_password:
            flash("New passwords do not match.", "error")
            return redirect(url_for("settings"))

        with get_db() as db:
            user = db.execute(
                "SELECT password_hash FROM users WHERE id = ?",
                (user_id,)
            ).fetchone()

            if user is None:
                session.clear()
                flash("Please sign in again.", "error")
                return redirect(url_for("login"))

            if not check_password_hash(
                user["password_hash"], current_password
            ):
                flash("Your current password is incorrect.", "error")
                return redirect(url_for("settings"))

            new_password_hash = generate_password_hash(new_password)

            db.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (new_password_hash, user_id)
            )

        flash("Password updated successfully!", "success")
        return redirect(url_for("settings"))

    return render_template("settings.html")










# ---------------- CAR PRICE PREDICTION ----------------

@app.route("/predict", methods=["GET", "POST"])
@login_required
def predict():
    if request.method == "GET":
        return render_template("index.html", **get_car_options())

    company = request.form.get("company", "").strip()
    car_model = request.form.get("car_models", "").strip()
    year_raw = request.form.get("year", "").strip()
    fuel_type = request.form.get("fuel_type", "").strip()
    driven_raw = request.form.get("kilo_driven", "").strip()

   
    fields = {
        "company": company,
        "car_model": car_model,
        "year": year_raw,
        "fuel_type": fuel_type,
        "kilo_driven": driven_raw
    }

    missing_fields = [
        name for name, value in fields.items()
        if not value or not value.strip()
    ]

    print("FORM DATA RECEIVED:", request.form.to_dict())
    print("MISSING FIELDS:", missing_fields)

    if missing_fields:
        message = "Please fill these fields: " + ", ".join(missing_fields)
        return render_template(
            "index.html",
            **get_car_options(),
            prediction_error=message
        ), 400


    try:
        year = int(year_raw)
        driven = int(driven_raw)

        valid_years = set(car["year"].dropna().astype(int).tolist())
        if driven < 0:
            raise ValueError("Distance travelled cannot be negative.")
        if year not in valid_years:
            raise ValueError("Please choose a valid manufacturing year.")

        valid_companies = set(car["company"].dropna().astype(str).tolist())
        valid_models = set(car["name"].dropna().astype(str).tolist())
        valid_fuels = set(car["fuel_type"].dropna().astype(str).tolist())
        if company not in valid_companies:
            raise ValueError("Please choose a valid company.")
        if car_model not in valid_models:
            raise ValueError("Please choose a valid car model.")
        if fuel_type not in valid_fuels:
            raise ValueError("Please choose a valid fuel type.")

        input_data = pd.DataFrame(
            [[car_model, company, year, driven, fuel_type]],
            columns=["name", "company", "year", "kms_driven", "fuel_type"]
        )
        prediction_value = float(model.predict(input_data)[0])
        if not np.isfinite(prediction_value):
            raise ValueError("The model returned an invalid prediction.")
        prediction_value = round(prediction_value, 2)

        with get_db() as db:
            db.execute(
                """INSERT INTO predictions
                   (user_id, company, car_model, year, fuel_type,
                    kilometres, predicted_price)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    session["user_id"], company, car_model,
                    year, fuel_type, driven, prediction_value
                )
            )

        return render_template(
            "index.html",
            **get_car_options(),
            prediction_text=prediction_value
        )

    except (ValueError, TypeError) as error:
        print("PREDICTION ERROR:", repr(error))
        flash(f"Please check the car details: {error}", "error")
        return render_template(
            "index.html",
            **get_car_options(),
            prediction_error=str(error)
        ), 400

    except Exception as error:
        print("UNEXPECTED PREDICTION ERROR:", repr(error))
        traceback.print_exc()
        message = "Prediction failed. Check the VS Code terminal for details."
        flash(message, "error")
        return render_template(
            "index.html",
            **get_car_options(),
            prediction_error=message
        ), 500


# ---------------- ADMIN DASHBOARD ----------------

@app.route("/admin")
@admin_required
def admin_dashboard():
    with get_db() as db:
        total_users = db.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'user'"
        ).fetchone()[0]
        total_predictions = db.execute(
            "SELECT COUNT(*) FROM predictions"
        ).fetchone()[0]
        recent_predictions = db.execute("""
            SELECT p.*, u.username, u.email
            FROM predictions p
            JOIN users u ON u.id = p.user_id
            ORDER BY p.id DESC LIMIT 20
        """).fetchall()
        users = db.execute("""
            SELECT id, username, email, role, created_at
            FROM users ORDER BY id DESC
        """).fetchall()

    return render_template(
        "admin_dashboard.html",
        username=session["username"],
        total_users=total_users,
        total_predictions=total_predictions,
        recent_predictions=recent_predictions,
        users=users
    )


# ---------------- ADMIN USER MANAGEMENT ----------------

@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    if user_id == session["user_id"]:
        flash("You cannot delete your own admin account.", "error")
        return redirect(url_for("admin_dashboard"))

    with get_db() as db:
        user = db.execute(
            "SELECT id, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if user and user["role"] != "admin":
            db.execute("DELETE FROM predictions WHERE user_id = ?", (user_id,))
            db.execute("DELETE FROM users WHERE id = ?", (user_id,))

    flash("User account removed if it existed.", "success")
    return redirect(url_for("admin_dashboard"))


@app.errorhandler(403)
def forbidden(error):
    return "403 - Access denied. Admin permission is required.", 403





@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        conn = sqlite3.connect(str(BASE_DIR / "car_price.db"))
        try:
            user = conn.execute(
                "SELECT id FROM users WHERE lower(email) = ?",
                (email,)
            ).fetchone()
        finally:
            conn.close()

        if user:
            token = reset_serializer.dumps(
                user[0],
                salt="password-reset"
            )

            reset_url = url_for(
                "reset_password",
                token=token,
                _external=True
            )

            message = Message(
                subject="AutoValue - Reset Your Password",
                recipients=[email],
                body=(
                    "Hello,\n\n"
                    "We received a request to reset your AutoValue password.\n\n"
                    f"Open this link within 30 minutes:\n{reset_url}\n\n"
                    "If you did not request this, ignore this email."
                )
            )

            try:
                mail.send(message)
            except Exception:
                app.logger.exception("Password reset email could not be sent")
                flash(
                    "Email could not be sent right now. Please try again later.",
                    "error"
                )
                return redirect(url_for("forgot_password"))

        flash(
            "If that email is registered, a reset link will be sent.",
            "info"
        )
        return redirect(url_for("login"))

    return render_template("forgot_password.html")



@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    try:
        user_id = reset_serializer.loads(
            token,
            salt="password-reset",
            max_age=1800
        )
    except (SignatureExpired, BadSignature):
        flash(
            "Reset link invalid or expired. Please request a new one.",
            "error"
        )
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(password) < 8:
            flash("Password must be at least 8 characters long.", "error")
            return render_template("reset_password.html", token=token)

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("reset_password.html", token=token)

        hashed_password = generate_password_hash(password)

        conn = sqlite3.connect(str(BASE_DIR / "car_price.db"))
        try:
            cursor = conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hashed_password, user_id)
            )
            conn.commit()

            if cursor.rowcount == 0:
                flash("Account not found. Please try again.", "error")
                return redirect(url_for("forgot_password"))
        finally:
            conn.close()

        flash("Password reset successfully. Please sign in.", "success")
        return redirect(url_for("login"))

    return render_template("reset_password.html", token=token)


if __name__ == "__main__":
    app.run(debug=True)
