# pylint: disable=unused-import
# noqa
from flask import Flask, render_template, request, redirect, session, jsonify, url_for, abort, make_response, flash
import sqlite3
from datetime import timedelta
from datetime import datetime
import os
import random
from functools import wraps

# ======================================================
# EMAIL (SMTP — GMAIL)
# ======================================================
SMTP_EMAIL = "manassehjoy9@gmail.com"
SMTP_PASSWORD = "mibjtyfrnlvqrutk"
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

try:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=20)
except Exception:
    client = None

# ======================================================
# EMAIL SENDER (OTP)
# ======================================================
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def send_otp_email(to_email, otp):
    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_EMAIL
        msg["To"] = to_email
        msg["Subject"] = "Your Nexivo Password Reset Code"

        body = f"""
Hello,

Your OTP for password reset is:

{otp}

This code is valid for 10 minutes.
If you did not request this, please ignore this email.

— Nexivo Team
"""
        msg.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()

        return True
    except Exception as e:
        print("EMAIL ERROR:", e)
        return False

# ======================================================
# APP CONFIG
# ======================================================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "phase2-stable-secret-railway-safe")

app.permanent_session_lifetime = timedelta(days=30)

# Railway-safe session configuration
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("RAILWAY_ENVIRONMENT") is not None
app.config["SESSION_COOKIE_HTTPONLY"] = True

DB_NAME = "database.db"
MAX_CHAT_MESSAGES = 500

# ======================================================
# GOOGLE OAUTH SETUP (OPTIONAL)
# ======================================================
try:
    from google_auth import google_bp, oauth
    oauth.init_app(app)
    app.register_blueprint(google_bp)
    GOOGLE_AUTH_ENABLED = True
except ImportError:
    GOOGLE_AUTH_ENABLED = False

# ======================================================
# NO CACHE (SAFE)
# ======================================================
@app.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# ======================================================
# DATABASE
# ======================================================
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def safe_add_column(cur, table, column, definition):
    try:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except sqlite3.OperationalError:
        pass

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE,
        password TEXT,
        name TEXT,
        age INTEGER,
        level TEXT,
        goals TEXT,
        created_at TEXT
    )
    """)
    
    cur.execute("""
    CREATE TABLE IF NOT EXISTS password_resets (
        user_id INTEGER,
        otp TEXT,
        created_at TEXT
    )
    """)

    safe_add_column(cur, "users", "active_sport", "TEXT")
    safe_add_column(cur, "users", "sport_locked", "INTEGER DEFAULT 0")
    safe_add_column(cur, "users", "coach_tone", "TEXT DEFAULT 'calm'")
    safe_add_column(cur, "users", "onboarding_done", "INTEGER DEFAULT 0")
    safe_add_column(cur, "users", "setup_done", "INTEGER DEFAULT 0")
    safe_add_column(cur, "users", "role", "TEXT DEFAULT 'player'")
    safe_add_column(cur, "users", "google_id", "TEXT")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS training (
        user_id INTEGER PRIMARY KEY,
        days INTEGER,
        minutes INTEGER,
        fatigue TEXT,
        plan_start_date TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS diet (
        user_id INTEGER PRIMARY KEY,
        diet_type TEXT,
        budget TEXT,
        allergies TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS injury (
        user_id INTEGER PRIMARY KEY,
        status TEXT,
        body_part TEXT,
        pain INTEGER,
        stage TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tournament (
        user_id INTEGER PRIMARY KEY,
        upcoming TEXT,
        days_left INTEGER,
        category TEXT,
        importance TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS coach_settings (
        user_id INTEGER PRIMARY KEY,
        ai_enabled INTEGER DEFAULT 0,
        style TEXT DEFAULT 'calm',
        reply_length TEXT DEFAULT 'short'
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        event TEXT,
        mode TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS coach_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        role TEXT,
        mode TEXT,
        message TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_sports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        sport_name TEXT,
        goals_text TEXT,
        preferences_json TEXT,
        is_active INTEGER DEFAULT 1,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS chat_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        sport_name TEXT,
        mode TEXT,
        is_archived INTEGER DEFAULT 0,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS chat_messages_v2 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER,
        sender TEXT,
        message_text TEXT,
        has_reference INTEGER DEFAULT 0,
        is_deleted INTEGER DEFAULT 0,
        created_at TEXT
    )
    """)

    conn.commit()
    conn.close()

def log_history(user_id, event, mode=None):
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO history (user_id, event, mode, created_at) VALUES (?, ?, ?, ?)",
            (user_id, event, mode, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except Exception:
        pass

def save_chat_message(user_id, role, message, mode):
    conn = get_db()
    conn.execute(
        "INSERT INTO coach_messages (user_id, role, mode, message, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, role, mode, message, datetime.now().isoformat()))
    conn.execute(
        """
        DELETE FROM coach_messages
        WHERE id NOT IN (
            SELECT id FROM coach_messages
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT ?
        ) AND user_id=?
    """, (user_id, MAX_CHAT_MESSAGES, user_id))
    conn.commit()
    conn.close()

init_db()

# ======================================================
# AUTH HELPERS
# ======================================================
def get_current_user():
    """Get current user from session, returns None if not logged in"""
    user_id = session.get("user_id")
    if not user_id:
        return None
    try:
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        conn.close()
        return user
    except Exception:
        return None

def is_setup_complete(user_id):
    """Check if user has completed all setup steps"""
    try:
        conn = get_db()
        user = conn.execute("SELECT setup_done FROM users WHERE id=?", (user_id,)).fetchone()
        if user and user["setup_done"] == 1:
            conn.close()
            return True
        
        training = conn.execute("SELECT 1 FROM training WHERE user_id=?", (user_id,)).fetchone()
        diet = conn.execute("SELECT 1 FROM diet WHERE user_id=?", (user_id,)).fetchone()
        injury = conn.execute("SELECT 1 FROM injury WHERE user_id=?", (user_id,)).fetchone()
        tournament = conn.execute("SELECT 1 FROM tournament WHERE user_id=?", (user_id,)).fetchone()
        conn.close()
        
        return all([training, diet, injury, tournament])
    except Exception:
        return False

def mark_setup_complete(user_id):
    """Mark setup as complete in database"""
    try:
        conn = get_db()
        conn.execute("UPDATE users SET setup_done=1 WHERE id=?", (user_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass

def get_user_role(user_id):
    """Get user role (player, coach, admin)"""
    try:
        conn = get_db()
        user = conn.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
        conn.close()
        return user["role"] if user and user["role"] else "player"
    except Exception:
        return "player"

def get_post_login_redirect(user_id):
    """Determine redirect destination after login based on setup status"""
    user = None
    try:
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        conn.close()
    except Exception:
        pass
    
    # Check profile completion
    profile_done = user and user["name"] is not None and user["name"] != ""
    
    if not profile_done:
        return url_for("onboarding")
    elif not is_setup_complete(user_id):
        return url_for("setup")
    else:
        return url_for("dashboard")

# ======================================================
# LOGIN REQUIRED DECORATOR
# ======================================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

def setup_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        user_id = session["user_id"]
        if not is_setup_complete(user_id):
            return redirect(url_for("setup"))
        return f(*args, **kwargs)
    return decorated_function

# ======================================================
# PUBLIC ROUTES (NO LOGIN REQUIRED)
# ======================================================
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/support")
def support_page():
    return render_template("support.html")

@app.route("/support-chat")
def support_chat_page():
    return render_template("support_chat.html")

@app.route("/faq")
def faq_page():
    return render_template("faq.html")

# ======================================================
# AUTH ROUTES
# ======================================================
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    
    if request.method == "GET":
        return render_template("signup.html")

    try:
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        
        if not email or not password:
            return render_template("signup.html", error="Email and password are required.")
        
        conn = get_db()
        conn.execute(
            "INSERT INTO users (email, password, created_at, role) VALUES (?, ?, ?, ?)",
            (email, password, datetime.now().isoformat(), "player"))
        conn.commit()
        conn.close()
        return redirect(url_for("login"))
    except sqlite3.IntegrityError:
        return render_template("signup.html", error="Email already registered.")
    except Exception as e:
        return render_template("signup.html", error="An error occurred. Please try again.")

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        user_id = session["user_id"]
        if is_setup_complete(user_id):
            return redirect(url_for("dashboard"))
        return redirect(url_for("setup"))
    
    if request.method == "GET":
        return render_template("login.html")

    try:
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        if not email or not password:
            return render_template("login.html", error="Email and password are required.")

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email=? AND password=?",
                            (email, password)).fetchone()

        if not user:
            conn.close()
            return render_template("login.html", error="Invalid email or password.")

        # Set session
        session.clear()
        session["user_id"] = user["id"]
        session.permanent = True
        
        log_history(user["id"], "login")

        # Ensure coach_settings exists
        conn.execute("INSERT OR IGNORE INTO coach_settings (user_id) VALUES (?)", (user["id"],))
        conn.commit()
        conn.close()

        # Get redirect destination
        dest = get_post_login_redirect(user["id"])

        resp = make_response(redirect(dest))
        resp.set_cookie(
            "uid",
            str(user["id"]),
            max_age=60 * 60 * 24 * 30,
            path="/",
            samesite="Lax",
            httponly=True
        )
        return resp
    except Exception as e:
        return render_template("login.html", error="An error occurred. Please try again.")

@app.route("/logout")
def logout():
    session.clear()
    resp = make_response(redirect(url_for("home")))
    resp.delete_cookie("uid", path="/")
    return resp

# ======================================================
# GOOGLE OAUTH CALLBACK (FIX 2)
# ======================================================
@app.route("/google-callback")
def google_callback():
    """
    Handle Google OAuth callback.
    Redirect based on setup status:
    - If setup NOT complete → /setup
    - If setup complete → /dashboard
    """
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    user_id = session["user_id"]
    
    # Ensure coach_settings exists for Google users
    try:
        conn = get_db()
        conn.execute("INSERT OR IGNORE INTO coach_settings (user_id) VALUES (?)", (user_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass
    
    log_history(user_id, "google_login")
    
    # Get redirect destination based on setup status
    dest = get_post_login_redirect(user_id)
    
    resp = make_response(redirect(dest))
    resp.set_cookie(
        "uid",
        str(user_id),
        max_age=60 * 60 * 24 * 30,
        path="/",
        samesite="Lax",
        httponly=True
    )
    return resp

# ======================================================
# FORGOT PASSWORD FLOW (FIX 1)
# ======================================================
@app.route("/forgot-password", methods=["GET"])
def forgot_password():
    return render_template("forgot_password.html")

@app.route("/forgot-password", methods=["POST"])
def forgot_password_post():
    """
    FIX 1: Forgot Password UX
    - Always show generic success message
    - Do NOT reveal if email exists
    - Do NOT auto-redirect to OTP page
    - Render forgot_password.html with success message (do NOT redirect to /login)
    - Keep silent failure for email sending
    """
    email = request.form.get("email", "").strip()
    
    if not email:
        return render_template("forgot_password.html", error="Email is required.")

    # Process silently - send OTP if user exists
    try:
        conn = get_db()
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()

        if user:
            # Generate and store OTP
            otp = str(random.randint(100000, 999999))
            conn.execute("DELETE FROM password_resets WHERE user_id = ?", (user["id"],))
            conn.execute(
                "INSERT INTO password_resets (user_id, otp, created_at) VALUES (?, ?, ?)",
                (user["id"], otp, datetime.now().isoformat())
            )
            conn.commit()
            # Send OTP email silently (fail silently if SMTP not configured)
            try:
                send_otp_email(email, otp)
            except Exception:
                pass
        
        conn.close()
    except Exception:
        pass
    
    # Always render with generic success message (do NOT redirect to /login)
    return render_template(
        "forgot_password.html",
        success="If the email exists, password reset instructions have been sent."
    )

@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    if request.method == "GET":
        return render_template("verify_otp.html")

    try:
        email = request.form.get("email", "").strip()
        otp = request.form.get("otp", "").strip()
        new_password = request.form.get("new_password", "").strip()

        if not email or not otp or not new_password:
            return render_template("verify_otp.html", error="All fields are required.")

        conn = get_db()
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()

        if not user:
            conn.close()
            return render_template("verify_otp.html", error="Invalid email or OTP.")

        record = conn.execute(
            """
            SELECT otp, created_at FROM password_resets
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user["id"],)
        ).fetchone()

        if not record or record["otp"] != otp:
            conn.close()
            return render_template("verify_otp.html", error="Invalid OTP.")

        created_at = datetime.fromisoformat(record["created_at"])
        if (datetime.now() - created_at).seconds > 600:
            conn.close()
            return render_template("verify_otp.html", error="OTP expired. Please request a new one.")

        conn.execute("UPDATE users SET password = ? WHERE id = ?", (new_password, user["id"]))
        conn.execute("DELETE FROM password_resets WHERE user_id = ?", (user["id"],))
        conn.commit()
        conn.close()

        return redirect(url_for("login"))
    except Exception:
        return render_template("verify_otp.html", error="An error occurred. Please try again.")

# ======================================================
# ONBOARDING (PROTECTED)
# ======================================================
@app.route("/onboarding", methods=["GET", "POST"])
@login_required
def onboarding():
    user_id = session["user_id"]
    
    # Check if already completed onboarding
    user = get_current_user()
    if user and user["name"]:
        return redirect(url_for("setup"))
    
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            age = request.form.get("age", "")
            level = request.form.get("level", "")
            sport = request.form.get("sport", "")
            goals = request.form.getlist("goals")

            if not name or not level or not sport:
                return render_template("onboarding.html", error="Name, level, and sport are required.")

            conn = get_db()
            conn.execute("""
                UPDATE users SET name=?, age=?, level=?, active_sport=?, goals=?, onboarding_done=1
                WHERE id=?
            """, (name, age, level, sport, ",".join(goals), user_id))
            conn.commit()
            conn.close()

            return redirect(url_for("setup"))
        except Exception:
            return render_template("onboarding.html", error="An error occurred. Please try again.")

    return render_template("onboarding.html")

# ======================================================
# SETUP (PROTECTED - ONCE ONLY)
# ======================================================
@app.route("/setup")
@login_required
def setup():
    user_id = session["user_id"]
    
    # If setup already complete, redirect to dashboard
    if is_setup_complete(user_id):
        return redirect(url_for("dashboard"))
    
    return render_template("setup.html")

@app.route("/api/setup-status")
def setup_status():
    uid = session.get("user_id")
    if not uid:
        return jsonify({}), 401

    try:
        conn = get_db()
        status = {
            "training": conn.execute("SELECT 1 FROM training WHERE user_id=?", (uid,)).fetchone() is not None,
            "diet": conn.execute("SELECT 1 FROM diet WHERE user_id=?", (uid,)).fetchone() is not None,
            "injury": conn.execute("SELECT 1 FROM injury WHERE user_id=?", (uid,)).fetchone() is not None,
            "tournament": conn.execute("SELECT 1 FROM tournament WHERE user_id=?", (uid,)).fetchone() is not None
        }
        conn.close()
        
        # Check if all complete, mark setup_done
        if all(status.values()):
            mark_setup_complete(uid)
            status["complete"] = True
        else:
            status["complete"] = False
            
        return jsonify(status)
    except Exception:
        return jsonify({}), 500

# ======================================================
# SETUP PAGES (PROTECTED)
# ======================================================
@app.route("/training")
@login_required
def training_page():
    return render_template("training.html")

@app.route("/diet")
@login_required
def diet_page():
    return render_template("diet.html")

@app.route("/injury")
@login_required
def injury_page():
    return render_template("injury.html")

@app.route("/tournament")
@login_required
def tournament_page():
    return render_template("tournament.html")

# ======================================================
# SAVE APIs (WITH PROPER ERROR HANDLING)
# ======================================================
@app.route("/api/training", methods=["POST"])
def save_training():
    uid = session.get("user_id")
    if not uid:
        return jsonify(success=False, error="Please login again."), 401

    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, error="Invalid data."), 400
        
        days = data.get("days")
        minutes = data.get("minutes")
        fatigue = data.get("fatigue")
        
        if days is None or minutes is None or not fatigue:
            return jsonify(success=False, error="Missing required fields."), 400
        
        conn = get_db()
        conn.execute("INSERT OR REPLACE INTO training VALUES (?, ?, ?, ?, ?)",
                     (uid, days, minutes, fatigue, datetime.now().date().isoformat()))
        conn.commit()
        conn.close()
        log_history(uid, "training_saved", "training")
        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, error="Error saving training data."), 500

@app.route("/api/diet", methods=["POST"])
def save_diet():
    uid = session.get("user_id")
    if not uid:
        return jsonify(success=False, error="Please login again."), 401

    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, error="Invalid data."), 400
        
        diet_type = data.get("diet_type")
        budget = data.get("budget")
        
        if not diet_type or not budget:
            return jsonify(success=False, error="Missing required fields."), 400
        
        conn = get_db()
        conn.execute("INSERT OR REPLACE INTO diet VALUES (?, ?, ?, ?)",
                     (uid, diet_type, budget, data.get("allergies", "")))
        conn.commit()
        conn.close()
        log_history(uid, "diet_saved", "diet")
        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, error="Error saving diet data."), 500

@app.route("/api/injury", methods=["POST"])
def save_injury():
    uid = session.get("user_id")
    if not uid:
        return jsonify(success=False, error="Please login again."), 401

    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, error="Invalid data."), 400
        
        status = data.get("status")
        stage = data.get("stage")
        
        if not status or not stage:
            return jsonify(success=False, error="Missing required fields."), 400
        
        conn = get_db()
        conn.execute("INSERT OR REPLACE INTO injury VALUES (?, ?, ?, ?, ?)",
                     (uid, status, data.get("body_part"), data.get("pain"), stage))
        conn.commit()
        conn.close()
        log_history(uid, "injury_saved", "injury")
        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, error="Error saving injury data."), 500

@app.route("/api/tournament", methods=["POST"])
def save_tournament():
    """FIXED: Proper error handling for tournament save"""
    uid = session.get("user_id")
    if not uid:
        return jsonify(success=False, error="Please login again."), 401

    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, error="Invalid data."), 400
        
        upcoming = data.get("upcoming")
        if upcoming is None:
            return jsonify(success=False, error="Missing required fields."), 400
        
        # Safe defaults for optional fields
        days_left = data.get("days_left")
        category = data.get("category", "")
        importance = data.get("importance", "")
        
        # Validate days_left if provided
        if days_left is not None:
            try:
                days_left = int(days_left)
            except (ValueError, TypeError):
                days_left = None
        
        conn = get_db()
        conn.execute("INSERT OR REPLACE INTO tournament VALUES (?, ?, ?, ?, ?)",
                     (uid, upcoming, days_left, category, importance))
        conn.commit()
        conn.close()
        log_history(uid, "tournament_saved", "tournament")
        return jsonify(success=True)
    except Exception as e:
        print(f"Tournament save error: {e}")
        return jsonify(success=False, error="Error saving tournament data."), 500

# ======================================================
# DASHBOARD (PROTECTED - SETUP REQUIRED)
# ======================================================
@app.route("/dashboard")
@setup_required
def dashboard():
    user_id = session["user_id"]
    role = get_user_role(user_id)
    
    return render_template("dashboard.html", user_role=role)

# ======================================================
# HISTORY (PROTECTED - SETUP REQUIRED)
# ======================================================
@app.route("/history")
@setup_required
def history_page():
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT event, mode, created_at FROM history WHERE user_id=? ORDER BY created_at DESC LIMIT 100",
            (session["user_id"],)).fetchall()
        conn.close()
        return render_template("history.html", history=rows)
    except Exception:
        return render_template("history.html", history=[])

# ======================================================
# COACH SETTINGS (PROTECTED - SETUP REQUIRED)
# ======================================================
@app.route("/coach-settings")
@setup_required
def coach_settings_page():
    return render_template("coach_settings.html")

@app.route("/api/coach-settings", methods=["GET", "POST"])
def coach_settings_api():
    uid = session.get("user_id")
    if not uid:
        return jsonify({}), 401

    try:
        conn = get_db()

        if request.method == "POST":
            data = request.get_json()
            conn.execute(
                "INSERT OR REPLACE INTO coach_settings VALUES (?, ?, ?, ?)",
                (uid, data.get("ai_enabled", 0), data.get("style", "calm"), data.get("reply_length", "short")))
            conn.commit()
            conn.close()
            return jsonify(success=True)

        row = conn.execute(
            "SELECT ai_enabled, style, reply_length FROM coach_settings WHERE user_id=?",
            (uid,)).fetchone()
        conn.close()

        if row:
            return jsonify({
                "ai_enabled": row["ai_enabled"],
                "style": row["style"],
                "reply_length": row["reply_length"],
            })
        return jsonify({"ai_enabled": 0, "style": "calm", "reply_length": "short"})
    except Exception:
        return jsonify({}), 500

# ======================================================
# COACH API
# ======================================================
@app.route("/api/coach", methods=["POST"])
def coach():
    uid = session.get("user_id")
    if not uid:
        return jsonify(reply="Please login again."), 401

    try:
        data = request.get_json()
        mode = data.get("mode", "Ask")
        message = data.get("text", "")

        if not message.strip():
            return jsonify(reply="Please type a question for the coach.")

        save_chat_message(uid, "user", message, mode)

        conn = get_db()
        settings = conn.execute(
            "SELECT ai_enabled, style, reply_length FROM coach_settings WHERE user_id=?",
            (uid,)).fetchone()
        conn.close()

        if not settings or not settings["ai_enabled"]:
            return jsonify(reply="Coach is locked. Enable AI from Coach Settings.")

        if client is None:
            return jsonify(reply="AI not configured yet.")

        system_prompt = f"You are a badminton coach. Tone: {settings['style']}. Reply length: {settings['reply_length']}."

        try:
            res = client.responses.create(
                model="gpt-4.1-mini",
                input=[{"role": "system", "content": system_prompt}, {"role": "user", "content": message}],
                max_output_tokens=300 if settings["reply_length"] == "short" else 700)
            reply = res.output_text.strip()
        except Exception:
            reply = "Coach is resting. Try again."

        save_chat_message(uid, "coach", reply, mode)
        log_history(uid, "used_coach", mode)
        return jsonify(reply=reply)
    except Exception:
        return jsonify(reply="An error occurred. Please try again.")

# ======================================================
# CLEAR CHAT
# ======================================================
@app.route("/api/clear-chat", methods=["POST"])
def clear_chat():
    uid = session.get("user_id")
    if not uid:
        return jsonify(success=False), 401

    try:
        conn = get_db()
        conn.execute("DELETE FROM coach_messages WHERE user_id=?", (uid,))
        conn.commit()
        conn.close()
        log_history(uid, "chat_cleared")
        return jsonify(success=True)
    except Exception:
        return jsonify(success=False), 500

# ======================================================
# SUPPORT CHAT API (NO LOGIN REQUIRED)
# ======================================================
@app.route("/api/support-chat", methods=["POST"])
def support_chat_api():
    try:
        data = request.get_json() or {}
        message = data.get("message", "").strip()

        if not message:
            return jsonify(reply="Please type a message.")

        msg = message.lower()

        if "login" in msg:
            reply = "If you can't log in, please check your email and password or try resetting your password."
        elif "forgot" in msg or "reset" in msg or "password" in msg:
            reply = "Click 'Forgot password' on the login page to reset your password."
        elif "signup" in msg or "register" in msg:
            reply = "To create an account, click Sign Up on the homepage and follow the steps."
        elif "free" in msg or "price" in msg:
            reply = "Nexivo currently offers free access during early development."
        elif "training" in msg or "diet" in msg:
            reply = "Support AI can't provide training or sports advice. Please use the Coach inside the app."
        else:
            reply = "I can help with login issues, account setup, and general app questions."

        return jsonify(reply=reply)
    except Exception:
        return jsonify(reply="Support is temporarily unavailable.")

# ======================================================
# ERROR HANDLERS
# ======================================================
@app.errorhandler(404)
def page_not_found(e):
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("home"))

@app.errorhandler(500)
def internal_error(e):
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("home"))

# ======================================================
# CATCH-ALL ROUTE
# ======================================================
@app.route("/<path:any_path>")
def catch_all(any_path):
    # Protected paths require login
    protected = ["dashboard", "training", "diet", "injury", "tournament", 
                 "history", "coach-settings", "setup", "onboarding"]
    
    if any(any_path.startswith(p) for p in protected):
        if "user_id" not in session:
            return redirect(url_for("login"))
    
    return redirect(url_for("home"))

# ======================================================
# SESSION RESTORE MIDDLEWARE
# ======================================================
@app.before_request
def restore_session_from_cookie():
    """Restore session from cookie for Railway persistence"""
    if "user_id" not in session:
        uid = request.cookies.get("uid")
        if uid:
            try:
                session["user_id"] = int(uid)
                session.permanent = True
            except (ValueError, TypeError):
                pass

# ======================================================
# RUN
# ======================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=False)
