# pylint: disable=unused-import
# noqa
from flask import Flask, render_template, request, redirect, session, jsonify, url_for, abort
import sqlite3
from datetime import timedelta
from datetime import datetime
import os
import random
from google_auth import google_bp, oauth
# ======================================================
# EMAIL (SMTP – GMAIL)
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

This code is valid for a short time.
If you did not request this, please ignore this email.

– Nexivo Team
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
app.secret_key = "phase2-stable-secret"

app.permanent_session_lifetime = timedelta(days=30)

app.config["SESSION_COOKIE_SAMESITE"] = "None"
app.config["SESSION_COOKIE_SECURE"] = False

oauth.init_app(app)
app.register_blueprint(google_bp)

@app.before_request
def restore_session_from_cookie():
    if "user_id" not in session:
        uid = request.cookies.get("uid")
        if uid:
            session["user_id"] = int(uid)
            session.permanent = True


DB_NAME = "database.db"

# ✅ SAFETY CAP (ADD ONLY)
MAX_CHAT_MESSAGES = 500


# ======================================================
# NO CACHE (SAFE)
# ======================================================
@app.after_request
def no_cache(response):
    response.headers[
        "Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
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


# ------------------------------------------------------
# SAFE DB HELPER (ADD ONLY — STEP 1)
# ------------------------------------------------------
def safe_add_column(cur, table, column, definition):
    try:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except sqlite3.OperationalError:
        pass  # column already exists


def init_db():
    conn = get_db()
    cur = conn.cursor()

    # ---------------- USERS ----------------
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
    # ---------------- PASSWORD RESET (OTP) ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS password_resets (
        user_id INTEGER,
        otp TEXT,
        created_at TEXT
    )
    """)

    # 🔒 STEP 1 — USER FOUNDATION FIELDS (ADD ONLY)
    safe_add_column(cur, "users", "active_sport", "TEXT")
    safe_add_column(cur, "users", "sport_locked", "INTEGER DEFAULT 0")
    safe_add_column(cur, "users", "coach_tone", "TEXT DEFAULT 'calm'")
    safe_add_column(cur, "users", "onboarding_done", "INTEGER DEFAULT 0")
    safe_add_column(cur, "users", "setup_done", "INTEGER DEFAULT 0")

    # ---------------- TRAINING ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS training (
        user_id INTEGER PRIMARY KEY,
        days INTEGER,
        minutes INTEGER,
        fatigue TEXT,
        plan_start_date TEXT
    )
    """)

    # ---------------- DIET ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS diet (
        user_id INTEGER PRIMARY KEY,
        diet_type TEXT,
        budget TEXT,
        allergies TEXT
    )
    """)

    # ---------------- INJURY ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS injury (
        user_id INTEGER PRIMARY KEY,
        status TEXT,
        body_part TEXT,
        pain INTEGER,
        stage TEXT
    )
    """)

    # ---------------- TOURNAMENT ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tournament (
        user_id INTEGER PRIMARY KEY,
        upcoming TEXT,
        days_left INTEGER,
        category TEXT,
        importance TEXT
    )
    """)

    # ---------------- COACH SETTINGS ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS coach_settings (
        user_id INTEGER PRIMARY KEY,
        ai_enabled INTEGER DEFAULT 0,
        style TEXT DEFAULT 'calm',
        reply_length TEXT DEFAULT 'short'
    )
    """)

    # ---------------- HISTORY ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        event TEXT,
        mode TEXT,
        created_at TEXT
    )
    """)

    # ---------------- COACH CHAT MEMORY ----------------
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

    # ======================================================
    # STEP 1 — FOUNDATION TABLES (ADD ONLY)
    # ======================================================

    # MULTI-SPORT SUPPORT
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

    # CHAT SESSIONS (FUTURE DASHBOARD)
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

    # FUTURE CHAT MESSAGE STRUCTURE (NOT USED YET)
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


# ======================================================
# SAVE COACH MEMORY (UNCHANGED)
# ======================================================
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
# BASIC ROUTES
# ======================================================
@app.route("/")
def home():
    return render_template("index.html")

# ======================================================
# SUPPORT & FAQ (ADD ONLY)
# ======================================================
@app.route("/support")
def support_page():
    return render_template("support.html")


@app.route("/support-chat")
def support_chat_page():
    return render_template("support_chat.html")


@app.route("/faq")
def faq_page():
    return render_template("faq.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "GET":
        return render_template("signup.html")

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (email, password, created_at) VALUES (?, ?, ?)",
            (request.form["email"], request.form["password"],
             datetime.now().isoformat()))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return "Email already registered", 400

    conn.close()
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    email = request.form["email"]
    password = request.form["password"]

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email=? AND password=?",
                        (email, password)).fetchone()

    if not user:
        conn.close()
        return render_template(
    "login.html",
    error="Invalid email or password."
        )

    session["user_id"] = user["id"]
    log_history(user["id"], "login")

    conn.execute("INSERT OR IGNORE INTO coach_settings (user_id) VALUES (?)",
                 (user["id"], ))
    conn.commit()

    profile_done = user["name"] is not None
    training = conn.execute("SELECT 1 FROM training WHERE user_id=?",
                            (user["id"], )).fetchone()
    diet = conn.execute("SELECT 1 FROM diet WHERE user_id=?",
                        (user["id"], )).fetchone()
    injury = conn.execute("SELECT 1 FROM injury WHERE user_id=?",
                          (user["id"], )).fetchone()
    tournament = conn.execute("SELECT 1 FROM tournament WHERE user_id=?",
                              (user["id"], )).fetchone()
    conn.close()

    # ✅ ALWAYS establish session first
    session["user_id"] = user["id"]
    session.permanent = True

    # ✅ Decide destination FIRST
    if not profile_done:
        resp = redirect(url_for("onboarding"))
    elif not all([training, diet, injury, tournament]):
        resp = redirect(url_for("setup"))
    else:
        resp = redirect(url_for("dashboard"))

    # ✅ Attach cookie ONCE (critical)
    resp.set_cookie(
        "uid",
        str(user["id"]),
        max_age=60 * 60 * 24 * 30,  # 30 days
        path="/",
        samesite="Lax"
    )
    return resp

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")
# ======================================================
# ONBOARDING
# ======================================================

@app.route("/onboarding", methods=["GET", "POST"])
def onboarding():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        name = request.form.get("name")
        age = request.form.get("age")
        level = request.form.get("level")
        sport = request.form.get("sport")
        goals = request.form.getlist("goals")

        if not name or not level or not sport:
            return redirect(url_for("onboarding"))

        conn = sqlite3.connect(DB_NAME)
        conn.execute("""
UPDATE users
SET name=?,
    age=?,
    level=?,
    active_sport=?,
    goals=?
WHERE id=?
""", (
    name,
    age,
    level,
    sport,
    ",".join(goals),
    session["user_id"]
))
        conn.commit()
        conn.close()

        return redirect(url_for("setup"))

    return render_template("onboarding.html")

# ======================================================
# SETUP
# ======================================================
@app.route("/setup")
def setup():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template("setup.html")


@app.route("/api/setup-status")
def setup_status():
    uid = session.get("user_id")
    if not uid:
        return jsonify({}), 401

    conn = get_db()
    status = {
        "training":
        conn.execute("SELECT 1 FROM training WHERE user_id=?",
                     (uid, )).fetchone() is not None,
        "diet":
        conn.execute("SELECT 1 FROM diet WHERE user_id=?", (uid, )).fetchone()
        is not None,
        "injury":
        conn.execute("SELECT 1 FROM injury WHERE user_id=?",
                     (uid, )).fetchone() is not None,
        "tournament":
        conn.execute("SELECT 1 FROM tournament WHERE user_id=?",
                     (uid, )).fetchone() is not None
    }
    conn.close()
    return jsonify(status)


# ======================================================
# SETUP PAGES
# ======================================================
@app.route("/training")
def training_page():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template("training.html")


@app.route("/diet")
def diet_page():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template("diet.html")


@app.route("/injury")
def injury_page():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template("injury.html")

@app.route("/tournament")
def tournament_page():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template("tournament.html")

@app.route("/video-analysis")
def video_analysis():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    # Phase 1E – backend lock
    active_sport_id = session.get("active_sport_id", 1)
    active_role = session.get("active_role", "player")

    if not can_unlock_module(
        user_id=user_id,
        sport_id=active_sport_id,
        role=active_role,
        module_name="video_analysis"
    ):
      return redirect(url_for("dashboard"))

    return render_template("video_analysis.html")

# ======================================================
# SAVE APIs
# ======================================================
@app.route("/api/training", methods=["POST"])
def save_training():
    uid = session.get("user_id")
    if not uid:
        return jsonify(success=False), 401               

    active_sport_id = session.get("active_sport_id", 1)
    active_role = session.get("active_role", "player")

    if not can_unlock_module(
        user_id=uid,
        sport_id=active_sport_id,
        role=active_role,
        module_name="training"
    ):
        return jsonify(
            success=False,
            error="Training module locked. Complete profile first."
        ), 403

    data = request.get_json()
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO training VALUES (?, ?, ?, ?, ?)",
                 (uid, data["days"], data["minutes"], data["fatigue"],
                  datetime.now().date().isoformat()))
    conn.commit()
    conn.close()
    log_history(uid, "training_saved", "training")
    return jsonify(success=True)


@app.route("/api/diet", methods=["POST"])
def save_diet():
    uid = session.get("user_id")
    if not uid:
        return jsonify(success=False), 401
    active_sport_id = session.get("active_sport_id", 1)
    active_role = session.get("active_role", "player")

    if not can_unlock_module(
    user_id=uid,
    sport_id=active_sport_id,
    role=active_role,
    module_name="diet"
    ):
        
        return jsonify(
           success=False,
           error="Diet module locked. Complete profile first."
    ), 403
    data = request.get_json()
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO diet VALUES (?, ?, ?, ?)",
        (uid, data["diet_type"], data["budget"], data.get("allergies", "")))
    conn.commit()
    conn.close()
    log_history(uid, "diet_saved", "diet")
    return jsonify(success=True)

@app.route("/api/injury", methods=["POST"])
def save_injury():
    uid = session.get("user_id")
    if not uid:
        return jsonify(success=False), 401

    active_sport_id = session.get("active_sport_id", 1)
    active_role = session.get("active_role", "player")

    if not can_unlock_module(
    user_id=uid,
    sport_id=active_sport_id,
    role=active_role,
    module_name="injury"
    ):
        return jsonify(
        success=False,
        error="Injury module locked. Complete profile first."
    ), 403
    data = request.get_json()
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO injury VALUES (?, ?, ?, ?, ?)",
                 (uid, data["status"], data.get("body_part"), data.get("pain"),
                  data["stage"]))
    conn.commit()
    conn.close()
    log_history(uid, "injury_saved", "injury")
    return jsonify(success=True)


@app.route("/api/tournament", methods=["POST"])
def save_tournament():
    uid = session.get("user_id")
    if not uid:
        return jsonify(success=False), 401

    active_sport_id = session.get("active_sport_id", 1)
    active_role = session.get("active_role", "player")

    if not can_unlock_module(
    user_id=uid,
    sport_id=active_sport_id,
    role=active_role,
    module_name="tournament"
    ):
        return jsonify(
        success=False,
        error="Tournament module locked. Complete profile first."
    ), 403
    data = request.get_json()
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO tournament VALUES (?, ?, ?, ?, ?)",
                 (uid, data["upcoming"], data.get("days_left"),
                  data.get("category"), data.get("importance")))
    conn.commit()
    conn.close()
    log_history(uid, "tournament_saved", "tournament")
    return jsonify(success=True)


# ======================================================
# DASHBOARD
# ======================================================
@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    # TEMP (Phase 1D): pick active sport + role
    active_sport_id = session.get("active_sport_id", 1)
    active_role = session.get("active_role", "player")

    # Phase 1D – UI gating ONLY
    video_analysis_unlocked = can_unlock_module(
        user_id=user_id,
        sport_id=active_sport_id,
        role=active_role,
        module_name="video_analysis"
    )

    return render_template(
        "dashboard.html",
        video_analysis_unlocked=video_analysis_unlocked
    )

# ======================================================
# COACH (GPT-4.1-MINI + MEMORY)
# ======================================================
@app.route("/api/coach", methods=["POST"])
def coach():
    uid = session.get("user_id")
    if not uid:
        return jsonify(reply="Please login again."), 401

    data = request.get_json()
    mode = data.get("mode", "Ask")
    message = data.get("text", "")

    if not message.strip():
        return jsonify(reply="Please type a question for the coach.")

    save_chat_message(uid, "user", message, mode)

    conn = get_db()
    settings = conn.execute(
        "SELECT ai_enabled, style, reply_length FROM coach_settings WHERE user_id=?",
        (uid, )).fetchone()
    conn.close()

    if not settings or not settings["ai_enabled"]:
        return jsonify(
            reply="🔒 Coach is locked. Enable AI from Coach Settings.")

    if client is None:
        return jsonify(reply="⚠️ AI not configured yet.")

    system_prompt = f"You are a badminton coach. Tone: {settings['style']}. Reply length: {settings['reply_length']}."

    try:
        res = client.responses.create(
            model="gpt-4.1-mini",
            input=[{
                "role": "system",
                "content": system_prompt
            }, {
                "role": "user",
                "content": message
            }],
            max_output_tokens=300
            if settings["reply_length"] == "short" else 700)
        reply = res.output_text.strip()
    except Exception:
        reply = "⚠️ Coach is resting. Try again."

    save_chat_message(uid, "coach", reply, mode)
    log_history(uid, "used_coach", mode)
    return jsonify(reply=reply)


# ======================================================
# CLEAR CHAT
# ======================================================
@app.route("/api/clear-chat", methods=["POST"])
def clear_chat():
    uid = session.get("user_id")
    if not uid:
        return jsonify(success=False), 401

    conn = get_db()
    conn.execute("DELETE FROM coach_messages WHERE user_id=?", (uid, ))
    conn.commit()
    conn.close()

    log_history(uid, "chat_cleared")
    return jsonify(success=True)


# ======================================================
# HISTORY
# ======================================================
@app.route("/history")
def history_page():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    rows = conn.execute(
        "SELECT event, mode, created_at FROM history WHERE user_id=? ORDER BY created_at DESC LIMIT 100",
        (session["user_id"], )).fetchall()
    conn.close()

    return render_template("history.html", history=rows)


# ======================================================
# COACH SETTINGS
# ======================================================
@app.route("/coach-settings")
def coach_settings_page():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template("coach_settings.html")


@app.route("/api/coach-settings", methods=["GET", "POST"])
def coach_settings_api():
    uid = session.get("user_id")
    if not uid:
        return jsonify({}), 401

    conn = get_db()

    if request.method == "POST":
        data = request.get_json()
        conn.execute(
            "INSERT OR REPLACE INTO coach_settings VALUES (?, ?, ?, ?)",
            (
                uid,
                data.get("ai_enabled", 0),
                data.get("style", "calm"),
                data.get("reply_length", "short"),
            ),
        )
        conn.commit()
        conn.close()
        return jsonify(success=True)

    row = conn.execute(
        "SELECT ai_enabled, style, reply_length FROM coach_settings WHERE user_id=?",
        (uid, ),
    ).fetchone()
    conn.close()

    return jsonify({
        "ai_enabled": row["ai_enabled"],
        "style": row["style"],
        "reply_length": row["reply_length"],
    })


# ======================================================
# 🔒 SAFE, DETERMINISTIC SUPPORT CHAT API (NO LOGIN)
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
            reply = "If you can’t log in, please check your email and password or try resetting your password."

        elif "forgot" in msg or "reset" in msg or "password" in msg:
            reply = "Click ‘Forgot password’ on the login page to reset your password."

        elif "signup" in msg or "register" in msg:
            reply = "To create an account, click Sign Up on the homepage and follow the steps."

        elif "free" in msg or "price" in msg:
            reply = "Nexivo currently offers free access during early development."

        elif "training" in msg or "diet" in msg:
            reply = "Support AI can’t provide training or sports advice. Please use the Coach inside the app."

        else:
            reply = "I can help with login issues, account setup, and general app questions."

        return jsonify(reply=reply)

    except Exception:
        return jsonify(reply="Support is temporarily unavailable.")
        # ======================================================


# 🔑 FORGOT PASSWORD PAGE
# ======================================================
@app.route("/forgot-password", methods=["GET"])
def forgot_password():
    return render_template("forgot_password.html")


@app.route("/forgot-password", methods=["POST"])
def forgot_password_post():
    email = request.form.get("email")
    if not email:
        return render_template(
            "forgot_password.html",
            error="Email is required."
        )

    conn = get_db()
    user = conn.execute(
        "SELECT id FROM users WHERE email = ?",
        (email,)
    ).fetchone()

    if not user:
        conn.close()
        return render_template(
            "forgot_password.html",
            error="No account found with this email."
        )

    otp = str(random.randint(100000, 999999))

    conn.execute(
        "INSERT INTO password_resets (user_id, otp, created_at) VALUES (?, ?, ?)",
        (user["id"], otp, datetime.now().isoformat())
    )
    conn.commit()

    # SEND OTP EMAIL
    send_otp_email(email, otp)

    return redirect(url_for("verify_otp"))


# ============================================================
# 🔐 VERIFY OTP & RESET PASSWORD
# ============================================================

@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():

    if request.method == "GET":
        return render_template("verify_otp.html")

    # ---------------- POST LOGIC ----------------
    email = request.form.get("email")
    otp = request.form.get("otp")
    new_password = request.form.get("new_password")

    if not email or not otp or not new_password:
        return render_template(
            "verify_otp.html",
            error="All fields are required."
        )

    conn = get_db()

    # 🔍 Get user
    user = conn.execute(
        "SELECT id FROM users WHERE email = ?",
        (email,)
    ).fetchone()

    if not user:
        conn.close()
        return render_template(
            "verify_otp.html",
            error="Invalid email or OTP."
        )

    # 🔍 Get latest OTP record
    record = conn.execute(
        """
        SELECT otp, created_at FROM password_resets
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (user["id"],)
    ).fetchone()

    # 🔑 FIRST: OTP existence + match
    if not record or record["otp"] != otp:
        conn.close()
        return render_template(
            "verify_otp.html",
            error="Invalid OTP."
        )

    # ⏱️ SECOND: OTP expiry check (10 minutes)
    created_at = datetime.fromisoformat(record["created_at"])

    if (datetime.now() - created_at).seconds > 600:
        conn.close()
        return render_template(
            "verify_otp.html",
            error="OTP expired. Please request a new one."
        )

    # 🔐 THIRD: Update password
    conn.execute(
        "UPDATE users SET password = ? WHERE id = ?",
        (new_password, user["id"])
    )

    # 🧹 FOURTH: Delete OTP after use
    conn.execute(
        "DELETE FROM password_resets WHERE user_id = ?",
        (user["id"],)
    )

    conn.commit()
    conn.close()

    return redirect(url_for("login"))


# ============================================================
# SAFE FALLBACK (DO NOT DUPLICATE)
# ============================================================

@app.route("/<path:any_path>")
def catch_all(any_path):
    abort(404)


# ============================================================
# RUN
# ============================================================
# ============================
# Nexivo Phase 1A – DB Helpers
# READ-ONLY (SAFE)
# ============================

def get_db_connection():
    import sqlite3
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

def get_user_sports(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT DISTINCT s.id, s.name, s.icon
        FROM sports s
        JOIN player_sport_profiles psp ON psp.sport_id = s.id
        WHERE psp.user_id = ? AND s.is_active = 1
    """, (user_id,))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]

def get_player_sport_profile(user_id, sport_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM player_sport_profiles
        WHERE user_id = ? AND sport_id = ?
    """, (user_id, sport_id))

    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None

def is_module_unlocked(user_id, sport_id, module_name):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT 1
        FROM player_sport_module_unlocks
        WHERE user_id = ? AND sport_id = ? AND module_name = ? AND is_unlocked = 1
    """, (user_id, sport_id, module_name))

    result = cursor.fetchone()
    conn.close()

    return result is not None

def get_coach_sport_profile(user_id, sport_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM coach_sport_profiles
        WHERE user_id = ? AND sport_id = ?
    """, (user_id, sport_id))

    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None

def is_player_profile_complete(user_id, sport_id):
    profile = get_player_sport_profile(user_id, sport_id)
    if not profile:
        return False

    required_fields = [
        profile.get("level"),
        profile.get("goal"),
        profile.get("injury_status"),
        profile.get("preferred_language")
    ]

    return all(required_fields)

def is_coach_profile_complete(user_id, sport_id):
    profile = get_coach_sport_profile(user_id, sport_id)
    if not profile:
        return False

    required_fields = [
        profile.get("coaching_type"),
        profile.get("experience_level"),
        profile.get("languages_supported")
    ]

    return all(required_fields)

def can_unlock_module(user_id, sport_id, role, module_name):
    if role == "player":
        if not is_player_profile_complete(user_id, sport_id):
            return False

    if role == "coach":
        if not is_coach_profile_complete(user_id, sport_id):
            return False

    return True

def get_sport_activity_logs(sport_id, limit=50):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM sport_activity_logs
        WHERE sport_id = ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (sport_id, limit))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]

def get_active_roles(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT role_type
        FROM user_roles
        WHERE user_id = ? AND is_active = 1
    """, (user_id,))
    roles = [row["role_type"] for row in cursor.fetchall()]
    conn.close()
    return roles

def is_profile_complete(user_id, role_type, sport_id):
    conn = get_db_connection()
    cur = conn.cursor()

    if role_type == "player":
        cur.execute("""
            SELECT level FROM player_sport_profiles
            WHERE user_id = ? AND sport_id = ?
        """, (user_id, sport_id))
    else:
        cur.execute("""
            SELECT experience_years FROM coach_sport_profiles
            WHERE user_id = ? AND sport_id = ?
        """, (user_id, sport_id))

    row = cur.fetchone()
    conn.close()
    return row is not None


def can_upload_video(user_id, sport_id):
    return (
        is_profile_complete(user_id, "player", sport_id)
        or is_profile_complete(user_id, "coach", sport_id)
    )

def get_user_language_pref(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT language
        FROM video_analysis_uploads
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 1
    """, (user_id,))
    row = cur.fetchone()
    conn.close()
    return row["language"] if row else "en"

def has_role(user_id, role_type):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 1
        FROM user_roles
        WHERE user_id = ? AND role_type = ? AND is_active = 1
        LIMIT 1
    """, (user_id, role_type))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists

def get_all_sports():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, name, icon
        FROM sports
        WHERE is_active = 1
        ORDER BY name
    """)
    sports = cursor.fetchall()
    conn.close()
    return sports
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000, debug=True)