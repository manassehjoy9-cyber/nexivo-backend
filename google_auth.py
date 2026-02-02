from flask import Blueprint, redirect, url_for, session
from authlib.integrations.flask_client import OAuth
from datetime import datetime
import sqlite3
import os

# ------------------------------------------------------
# OAuth setup
# ------------------------------------------------------
oauth = OAuth()
google_bp = Blueprint("google_auth", __name__)

google = oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    access_token_url="https://oauth2.googleapis.com/token",
    authorize_url="https://accounts.google.com/o/oauth2/auth",
    api_base_url="https://www.googleapis.com/oauth2/v2/",
    client_kwargs={"scope": "openid email profile"},
)

# ------------------------------------------------------
# DB helper (same DB as app.py)
# ------------------------------------------------------
def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

# ------------------------------------------------------
# Start Google login
# ------------------------------------------------------
@google_bp.route("/login/google")
def login_google():
    return google.authorize_redirect(
        url_for("google_auth.google_callback", _external=True)
    )

# ------------------------------------------------------
# Google OAuth callback
# ------------------------------------------------------
@google_bp.route("/auth/google/callback")
def google_callback():
    try:
        google.authorize_access_token()
        user_info = google.get("userinfo").json()
    except Exception:
        return redirect(url_for("login"))

    email = user_info.get("email")
    name = user_info.get("name", "")

    if not email:
        return redirect(url_for("login"))

    conn = get_db()

    # --------------------------------------------------
    # Find or create user
    # --------------------------------------------------
    user = conn.execute(
        "SELECT * FROM users WHERE email=?",
        (email,)
    ).fetchone()

    if not user:
        conn.execute(
            "INSERT INTO users (email, name, created_at) VALUES (?, ?, ?)",
            (email, name, datetime.now().isoformat())
        )
        conn.commit()

        user = conn.execute(
            "SELECT * FROM users WHERE email=?",
            (email,)
        ).fetchone()

    # --------------------------------------------------
    # Ensure coach_settings row exists
    # --------------------------------------------------
    conn.execute(
        "INSERT OR IGNORE INTO coach_settings (user_id) VALUES (?)",
        (user["id"],)
    )
    conn.commit()
    conn.close()

    # --------------------------------------------------
    # ✅ FIXED SESSION HANDLING (NO LOOP)
    # --------------------------------------------------
    session.pop("user_id", None)
    session["user_id"] = user["id"]
    session["login_method"] = "google"
    session.permanent = True

    # --------------------------------------------------
    # Same flow as normal login
    # --------------------------------------------------
    if not user["name"]:
        return redirect(url_for("onboarding"))

    return redirect(url_for("dashboard"))