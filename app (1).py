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
SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
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

    # Add soft delete columns to coach_messages
    safe_add_column(cur, "coach_messages", "is_deleted", "INTEGER DEFAULT 0")
    safe_add_column(cur, "coach_messages", "deleted_at", "TEXT")

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

    # Add soft delete columns to chat_sessions
    safe_add_column(cur, "chat_sessions", "is_deleted", "INTEGER DEFAULT 0")
    safe_add_column(cur, "chat_sessions", "deleted_at", "TEXT")

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

    # Add deleted_at column to chat_messages_v2
    safe_add_column(cur, "chat_messages_v2", "deleted_at", "TEXT")

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
        "INSERT INTO coach_messages (user_id, role, mode, message, created_at, is_deleted) VALUES (?, ?, ?, ?, ?, 0)",
        (user_id, role, mode, message, datetime.now().isoformat()))
    # Only delete non-soft-deleted messages when trimming
    conn.execute(
        """
        DELETE FROM coach_messages
        WHERE id NOT IN (
            SELECT id FROM coach_messages
            WHERE user_id=? AND is_deleted=0
            ORDER BY id DESC
            LIMIT ?
        ) AND user_id=? AND is_deleted=0
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

def is_onboarding_complete(user_id):
    """Check if user has completed onboarding (profile setup)"""
    try:
        conn = get_db()
        user = conn.execute("SELECT name, onboarding_done FROM users WHERE id=?", (user_id,)).fetchone()
        conn.close()
        if user:
            return (user["name"] is not None and user["name"] != "") or user["onboarding_done"] == 1
        return False
    except Exception:
        return False

def is_setup_complete(user_id):
    """Check if user has completed all setup steps (legacy - for backward compatibility)"""
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
    """Determine redirect destination after login based on onboarding status"""
    user = None
    try:
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        conn.close()
    except Exception:
        pass

    profile_done = user and user["name"] is not None and user["name"] != ""

    if not profile_done:
        return url_for("onboarding")
    else:
        # After onboarding, go directly to dashboard (setup is optional)
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
    """Decorator that requires onboarding to be complete (NOT full setup)"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        user_id = session["user_id"]
        # Changed: Only require onboarding, not full setup
        if not is_onboarding_complete(user_id):
            return redirect(url_for("onboarding"))
        return f(*args, **kwargs)
    return decorated_function

# ======================================================
# COACH INTELLIGENCE LAYER
# ======================================================

def get_user_context(user_id):
    """Fetch user's sport, tone, level, goals and all module data for AI context"""
    context = {
        "sport": None,
        "tone": "calm",
        "level": None,
        "goals": None,
        "name": None,
        "training": None,
        "diet": None,
        "injury": None,
        "tournament": None,
        "recent_message": None
    }

    try:
        conn = get_db()

        user = conn.execute(
            "SELECT name, active_sport, coach_tone, level, goals FROM users WHERE id=?",
            (user_id,)
        ).fetchone()

        if user:
            context["name"] = user["name"]
            context["sport"] = user["active_sport"] or "general fitness"
            context["tone"] = user["coach_tone"] or "calm"
            context["level"] = user["level"]
            context["goals"] = user["goals"]

        training = conn.execute(
            "SELECT days, minutes, fatigue, plan_start_date FROM training WHERE user_id=?",
            (user_id,)
        ).fetchone()
        if training:
            context["training"] = {
                "days": training["days"],
                "minutes": training["minutes"],
                "fatigue": training["fatigue"],
                "plan_start_date": training["plan_start_date"]
            }

        diet = conn.execute(
            "SELECT diet_type, budget, allergies FROM diet WHERE user_id=?",
            (user_id,)
        ).fetchone()
        if diet:
            context["diet"] = {
                "diet_type": diet["diet_type"],
                "budget": diet["budget"],
                "allergies": diet["allergies"]
            }

        injury = conn.execute(
            "SELECT status, body_part, pain, stage FROM injury WHERE user_id=?",
            (user_id,)
        ).fetchone()
        if injury:
            context["injury"] = {
                "status": injury["status"],
                "body_part": injury["body_part"],
                "pain": injury["pain"],
                "stage": injury["stage"]
            }

        tournament = conn.execute(
            "SELECT upcoming, days_left, category, importance FROM tournament WHERE user_id=?",
            (user_id,)
        ).fetchone()
        if tournament:
            context["tournament"] = {
                "upcoming": tournament["upcoming"],
                "days_left": tournament["days_left"],
                "category": tournament["category"],
                "importance": tournament["importance"]
            }

        # Memory anchor: get last non-deleted coach message for context
        last_msg = conn.execute(
            "SELECT message, mode FROM coach_messages WHERE user_id=? AND role='coach' AND (is_deleted=0 OR is_deleted IS NULL) ORDER BY id DESC LIMIT 1",
            (user_id,)
        ).fetchone()
        if last_msg:
            context["recent_message"] = {
                "text": last_msg["message"][:200],
                "mode": last_msg["mode"]
            }

        conn.close()
    except Exception:
        pass

    return context

def get_memory_anchor(context, mode):
    """Extract ONE relevant memory anchor based on mode. Returns string or None."""

    # Priority: mode-specific data first
    if mode == "injury" and context.get("injury"):
        inj = context["injury"]
        if inj.get("status") and inj["status"] != "none":
            return f"You previously mentioned a {inj.get('body_part', 'injury')} issue (pain: {inj.get('pain', '?')}/10, stage: {inj.get('stage', 'unknown')})."

    if mode == "tournament" and context.get("tournament"):
        tourn = context["tournament"]
        if tourn.get("upcoming") and tourn["upcoming"].lower() not in ["no", "none", ""]:
            days = tourn.get("days_left")
            if days:
                return f"Your {tourn.get('upcoming', 'tournament')} is in {days} days ({tourn.get('importance', 'standard')} priority)."

    if mode == "training" and context.get("training"):
        tr = context["training"]
        if tr.get("fatigue") and tr["fatigue"].lower() in ["high", "very high"]:
            return f"Your recent fatigue level was {tr['fatigue']}. Let's factor that in."

    if mode == "diet" and context.get("diet"):
        d = context["diet"]
        if d.get("allergies") and d["allergies"].strip():
            return f"Keeping in mind your dietary restrictions: {d['allergies']}."

    # Cross-mode relevant anchors
    if context.get("tournament"):
        tourn = context["tournament"]
        days = tourn.get("days_left")
        if days and isinstance(days, int) and days <= 14:
            return f"With your {tourn.get('upcoming', 'competition')} just {days} days away, timing is critical."

    if context.get("injury"):
        inj = context["injury"]
        if inj.get("status") and inj["status"].lower() not in ["none", "healthy", ""]:
            return f"Remember to be mindful of your {inj.get('body_part', 'injury')} situation."

    return None

def determine_reply_depth(message, context):
    """Determine reply depth: SHORT (default), MEDIUM, or DEEP based on triggers."""

    message_lower = message.lower()

    # Trigger words for expansion
    deep_triggers = ["explain", "why", "detail", "elaborate", "more info", "tell me more", "in depth", "thoroughly"]
    medium_triggers = ["how", "what should", "can you", "help me understand"]

    # Check for deep triggers
    for trigger in deep_triggers:
        if trigger in message_lower:
            return "deep"

    # Tournament urgency: within 14 days
    if context.get("tournament"):
        days = context["tournament"].get("days_left")
        if days and isinstance(days, int) and days <= 14:
            return "medium"

    # Injury risk detected
    if context.get("injury"):
        inj = context["injury"]
        pain = inj.get("pain")
        if pain and isinstance(pain, int) and pain >= 6:
            return "medium"
        if inj.get("status") and inj["status"].lower() in ["injured", "recovering"]:
            return "medium"

    # Confusion detection (question marks, repeated short queries)
    if message_lower.count("?") >= 2:
        return "medium"

    # Check for medium triggers
    for trigger in medium_triggers:
        if trigger in message_lower:
            return "medium"

    return "short"

def get_tone_instructions(tone, mode):
    """Get tone-specific + mode-aware instructions for the AI."""

    # Base tone DNA
    tone_dna = {
        "calm": {
            "base": "Be supportive, patient, and reassuring. Use gentle encouragement.",
            "style": "warm and understanding"
        },
        "strict": {
            "base": "Be concise, direct, and corrective. No fluff, focus on action.",
            "style": "disciplined and no-nonsense"
        },
        "motivational": {
            "base": "Be energetic and encouraging. Inspire confidence and action.",
            "style": "enthusiastic and uplifting"
        },
        "analytical": {
            "base": "Be structured and rational. Use bullet points and clear logic.",
            "style": "methodical and data-focused"
        }
    }

    # Default to calm if unknown tone
    tone_config = tone_dna.get(tone, tone_dna["calm"])

    # Mode-specific adaptations
    mode_adaptations = {
        "injury": {
            "calm": "Use extra cautious, reassuring language. Emphasize safety and proper recovery.",
            "strict": "Be direct about risks but still prioritize safety. No shortcuts.",
            "motivational": "Focus on recovery milestones and comeback potential. Stay realistic.",
            "analytical": "Present recovery timelines and risk factors clearly. Be precise."
        },
        "tournament": {
            "calm": "Be focused and time-aware while staying supportive. Build confidence.",
            "strict": "Emphasize preparation priorities. Cut non-essentials. Focus on readiness.",
            "motivational": "Channel competition energy. Visualize success. Peak performance mindset.",
            "analytical": "Structure prep timeline. Prioritize high-impact activities. Track readiness."
        },
        "training": {
            "calm": "Guide through workouts with patience. Celebrate progress.",
            "strict": "Push for consistency and proper form. Address weaknesses directly.",
            "motivational": "Make training exciting. Connect effort to goals. Celebrate gains.",
            "analytical": "Structure sessions logically. Track volume and intensity. Optimize recovery."
        },
        "diet": {
            "calm": "Make nutrition approachable. Avoid judgment. Practical suggestions.",
            "strict": "Be clear about nutritional needs. No excuses for poor choices.",
            "motivational": "Frame nutrition as fuel for performance. Make healthy eating exciting.",
            "analytical": "Focus on macros, timing, and practical meal planning. Be specific."
        }
    }

    mode_adapt = mode_adaptations.get(mode, {}).get(tone, "")

    return f"{tone_config['base']} Your style is {tone_config['style']}. {mode_adapt}"

def build_ai_system_prompt(mode, context, depth="short"):
    """Build intelligent system prompt with tone, clarity, trust, and memory."""

    sport = context.get("sport", "general fitness")
    tone = context.get("tone", "calm")
    level = context.get("level", "intermediate")
    name = context.get("name", "athlete")

    # Get tone + mode instructions
    tone_instructions = get_tone_instructions(tone, mode)

    # Depth/clarity instructions
    depth_instructions = {
        "short": "Keep responses concise (2-4 sentences max). Get to the point quickly.",
        "medium": "Provide moderate detail (4-6 sentences). Balance brevity with clarity.",
        "deep": "Give thorough explanations (6-10 sentences). Cover important details."
    }
    clarity_instruction = depth_instructions.get(depth, depth_instructions["short"])

    # Memory anchor
    memory_anchor = get_memory_anchor(context, mode)
    memory_instruction = ""
    if memory_anchor:
        memory_instruction = f"\nMEMORY CONTEXT: {memory_anchor}"

    # WHY-TRUST system (mandatory)
    trust_system = """
RESPONSE FORMAT (MANDATORY):
Every recommendation MUST include:
1. WHAT: Clear action to take
2. WHY: Why this matters (1-2 lines)
3. RISK: What could happen if ignored (soft, ethical language)

Example format:
"[Action recommendation]

WHY: [Brief explanation of benefit]
RISK: [Gentle warning if skipped]"
"""

    # Safety rules
    safety_rules = """
SAFETY RULES:
- NO medical diagnosis or treatment claims
- NO guaranteed results or timelines
- Use "typically", "often", "may help" language
- For injuries: always suggest consulting professionals for serious concerns
- Be ethical and realistic in all advice
"""

    # Sport-specific instruction
    sport_instruction = f"You are an expert {sport} coach for {name} (level: {level}). ONLY provide {sport}-specific advice. If asked about other sports, redirect politely."

    # Mode-specific context
    mode_context = ""
    if mode == "training" and context.get("training"):
        tr = context["training"]
        mode_context = f"""
TRAINING DATA:
- Schedule: {tr.get('days', '?')} days/week, {tr.get('minutes', '?')} min/session
- Fatigue: {tr.get('fatigue', 'unknown')}
- Plan started: {tr.get('plan_start_date', 'unknown')}
Focus: workout structure, drills, periodization, recovery for {sport}."""

    elif mode == "diet" and context.get("diet"):
        d = context["diet"]
        mode_context = f"""
DIET DATA:
- Type: {d.get('diet_type', 'unknown')}
- Budget: {d.get('budget', 'unknown')}
- Restrictions: {d.get('allergies', 'none')}
Focus: nutrition timing, practical meals, hydration for {sport} athletes."""

    elif mode == "injury" and context.get("injury"):
        inj = context["injury"]
        mode_context = f"""
INJURY DATA:
- Status: {inj.get('status', 'unknown')}
- Area: {inj.get('body_part', 'unknown')}
- Pain: {inj.get('pain', '?')}/10
- Stage: {inj.get('stage', 'unknown')}
Focus: safe modifications, recovery guidance, when to rest. ALWAYS recommend professional consultation for serious issues."""

    elif mode == "tournament" and context.get("tournament"):
        tourn = context["tournament"]
        mode_context = f"""
TOURNAMENT DATA:
- Event: {tourn.get('upcoming', 'unknown')}
- Days until: {tourn.get('days_left', '?')}
- Category: {tourn.get('category', 'unknown')}
- Priority: {tourn.get('importance', 'standard')}
Focus: competition prep, peaking strategy, mental preparation, taper for {sport}."""

    elif mode == "ask":
        # FIX: For "ask" mode, include ALL available user context
        context_parts = []
        if context.get("training"):
            tr = context["training"]
            context_parts.append(f"Training: {tr.get('days', '?')} days/week, {tr.get('minutes', '?')} min/session, fatigue: {tr.get('fatigue', 'unknown')}")
        if context.get("diet"):
            d = context["diet"]
            context_parts.append(f"Diet: {d.get('diet_type', 'unknown')}, restrictions: {d.get('allergies', 'none')}")
        if context.get("injury"):
            inj = context["injury"]
            if inj.get("status") and inj["status"].lower() not in ["none", "healthy", ""]:
                context_parts.append(f"Injury: {inj.get('body_part', 'unknown')} ({inj.get('status', 'unknown')}), pain: {inj.get('pain', '?')}/10")
        if context.get("tournament"):
            tourn = context["tournament"]
            if tourn.get("upcoming") and tourn["upcoming"].lower() not in ["no", "none", ""]:
                context_parts.append(f"Tournament: {tourn.get('upcoming', 'unknown')} in {tourn.get('days_left', '?')} days")
        
        if context_parts:
            context_lines = "\n- ".join(context_parts)
            mode_context = f"""
USER CONTEXT (use this to personalize your response):
- {context_lines}
Focus: Answer the user's question while considering their complete athletic profile."""

    # Assemble full prompt
    full_prompt = f"""{sport_instruction}

{tone_instructions}

{clarity_instruction}
{memory_instruction}

{trust_system}

{safety_rules}

{mode_context}"""

    return full_prompt

def generate_fallback_response(mode, context, depth="short"):
    """Generate deterministic fallback respecting tone + WHY system."""

    sport = context.get("sport", "your sport")
    tone = context.get("tone", "calm")
    name = context.get("name", "athlete")

    # Tone-aware greeting
    greetings = {
        "calm": f"Hey {name}, here's what I'd suggest:",
        "strict": f"{name}, here's what you need to do:",
        "motivational": f"Alright {name}, let's get after it!",
        "analytical": f"{name}, here's my analysis:"
    }
    greeting = greetings.get(tone, greetings["calm"])

    # Memory anchor
    memory = get_memory_anchor(context, mode)
    memory_line = f"\n(Noted: {memory})\n" if memory else ""

    # Mode-specific responses with WHY system
    responses = {
        "training": {
            "calm": f"""{greeting}
{memory_line}
WHAT: Focus on consistent, quality practice with proper warm-up and cool-down specific to {sport}. Listen to your body.

WHY: Building sustainable habits creates lasting improvement without burnout.

RISK: Skipping recovery or overtraining can lead to fatigue and setbacks.

Note: AI coach is resting. Try again soon for personalized guidance.""",

            "strict": f"""{greeting}
{memory_line}
WHAT: Execute your {sport} drills with full focus. No half efforts. Warm up properly, train hard, cool down.

WHY: Disciplined training separates good from great athletes.

RISK: Inconsistent effort wastes time and delays progress.

Note: AI coach offline. Return for detailed programming.""",

            "motivational": f"""{greeting}
{memory_line}
WHAT: Show up and give your best! Every {sport} session is a chance to improve. Embrace the process!

WHY: Champions are made in practice. Your effort today builds tomorrow's success!

RISK: Missing sessions breaks momentum and delays your breakthrough.

Note: Coach resting. Come back fired up for more guidance!""",

            "analytical": f"""{greeting}
{memory_line}
WHAT: Structure your {sport} training into phases: warm-up (10min), skill work (60%), conditioning (30%), cool-down (10min).

WHY: Systematic training optimizes adaptation and reduces injury risk.

RISK: Unstructured training leads to imbalanced development and plateaus.

Note: AI offline. Return for detailed periodization."""
        },

        "diet": {
            "calm": f"""{greeting}
{memory_line}
WHAT: Focus on balanced meals with lean protein, complex carbs, and vegetables. Stay hydrated throughout the day.

WHY: Proper nutrition supports your {sport} performance and recovery.

RISK: Poor nutrition can leave you feeling tired and slow your progress.

Note: Coach resting. Try again for personalized meal ideas.""",

            "strict": f"""{greeting}
{memory_line}
WHAT: Eat clean. Protein at every meal, vegetables, whole grains. Cut the junk.

WHY: Your body performs how you fuel it. No shortcuts.

RISK: Poor diet undermines all your {sport} training efforts.

Note: AI offline. Return for strict meal protocols.""",

            "motivational": f"""{greeting}
{memory_line}
WHAT: Fuel your body like the athlete you are! Quality nutrition = quality performance in {sport}!

WHY: The right food gives you the energy to crush your goals!

RISK: Bad fuel means bad performance. You deserve better!

Note: Coach charging up. Come back for fuel strategies!""",

            "analytical": f"""{greeting}
{memory_line}
WHAT: Target protein (1.6-2g/kg), carbs around training, fats for hormones. Time meals strategically.

WHY: Optimized nutrition maximizes training adaptations and recovery rates.

RISK: Suboptimal intake compromises performance metrics and recovery.

Note: AI offline. Return for macro calculations."""
        },

        "injury": {
            "calm": f"""{greeting}
{memory_line}
WHAT: Listen to your body. Rest when needed, use ice for acute pain, and don't push through sharp discomfort.

WHY: Early care prevents small issues from becoming serious problems.

RISK: Ignoring pain signals can extend recovery time significantly.

IMPORTANT: Please consult a physiotherapist for proper assessment.

Note: Coach resting. Take care of yourself.""",

            "strict": f"""{greeting}
{memory_line}
WHAT: Stop if it hurts. RICE protocol for acute issues. No training through sharp pain.

WHY: Injuries worsen when ignored. Smart athletes recover fully.

RISK: Pushing through injury can sideline you for months instead of days.

IMPORTANT: See a professional. No exceptions.

Note: AI offline. Prioritize recovery.""",

            "motivational": f"""{greeting}
{memory_line}
WHAT: Recovery is part of the journey! Take care of your body now so you can come back stronger.

WHY: Every champion has overcome setbacks. This is your comeback story!

RISK: Rushing back too soon can turn a small setback into a major one.

IMPORTANT: A physio can help you return faster and safer!

Note: Coach resting. Heal up, champion!""",

            "analytical": f"""{greeting}
{memory_line}
WHAT: Apply RICE protocol. Monitor pain levels (1-10 scale). Track recovery progress daily.

WHY: Systematic recovery monitoring optimizes return-to-play timelines.

RISK: Unmonitored return increases re-injury probability by 2-3x.

IMPORTANT: Professional assessment provides accurate diagnosis and timeline.

Note: AI offline. Document symptoms for your physio."""
        },

        "tournament": {
            "calm": f"""{greeting}
{memory_line}
WHAT: Trust your preparation. Focus on rest, visualization, and staying calm. Review your {sport} game plan.

WHY: Confidence comes from knowing you've done the work.

RISK: Overthinking or last-minute changes can disrupt your performance.

Note: Coach resting. You've got this!""",

            "strict": f"""{greeting}
{memory_line}
WHAT: Taper training. Sleep 8+ hours. Review strategy. No experiments. Execute what you know.

WHY: Peak performance requires fresh body and focused mind.

RISK: Overtraining or trying new things before competition kills performance.

Note: AI offline. Stick to the plan.""",

            "motivational": f"""{greeting}
{memory_line}
WHAT: This is YOUR moment! Trust your training, visualize success, and go compete with confidence!

WHY: You've earned this opportunity. Now it's time to show what you can do!

RISK: Doubt kills performance. Believe in yourself and execute!

Note: Coach resting. Go make it happen!""",

            "analytical": f"""{greeting}
{memory_line}
WHAT: Reduce volume 40-60% in final week. Maintain intensity briefly. Prioritize sleep and nutrition.

WHY: Taper allows full glycogen restoration and neuromuscular freshness.

RISK: Overtraining in final week depletes energy reserves for competition.

Note: AI offline. Follow taper protocol."""
        }
    }

    # FIX: Add "ask" mode responses that use full user context (sport, injury, tournament)
    ask_responses = {
        "calm": f"""{greeting}
{memory_line}
WHAT: I'm here to help with your {sport} journey. Ask me about training, nutrition, injury management, or competition prep.

WHY: Personalized guidance based on your profile helps you progress safely and effectively.

RISK: Generic advice without context may not address your specific needs.

Note: AI coach is resting. Try again soon for personalized guidance.""",

        "strict": f"""{greeting}
{memory_line}
WHAT: Ask me something specific about {sport}. Training, diet, injury, or tournament prep.

WHY: Focused questions get better answers.

RISK: Vague questions waste your time.

Note: AI offline. Return with a clear question.""",

        "motivational": f"""{greeting}
{memory_line}
WHAT: Let's talk {sport}! I'm here to help you with training, nutrition, recovery, or competition mindset!

WHY: Every great athlete needs a coach in their corner!

RISK: Going solo means missing out on guidance that could accelerate your progress!

Note: Coach charging up. Come back ready to level up!""",

        "analytical": f"""{greeting}
{memory_line}
WHAT: Ready to assist with {sport}-specific queries: training protocols, nutrition timing, injury management, competition strategy.

WHY: Data-driven coaching optimizes your athletic development.

RISK: Without structured guidance, progress may be inefficient.

Note: AI offline. Return for detailed analysis."""
    }

    # Use ask responses for "ask" mode, otherwise use mode-specific responses
    if mode == "ask":
        mode_responses = ask_responses
    else:
        mode_responses = responses.get(mode, responses["training"])
    response = mode_responses.get(tone, mode_responses["calm"])

    # Adjust length for depth
    if depth == "short" and len(response) > 600:
        lines = response.split("\n")
        response = "\n".join(lines[:8])

    return response

def check_sport_mismatch(message, user_sport):
    """Check if user is asking about a different sport"""
    other_sports = [
        "football", "soccer", "basketball", "tennis", "cricket", "golf",
        "swimming", "running", "cycling", "volleyball", "baseball", "hockey",
        "rugby", "boxing", "mma", "wrestling", "gymnastics", "skiing",
        "snowboarding", "surfing", "skateboarding", "table tennis", "squash"
    ]

    message_lower = message.lower()
    user_sport_lower = (user_sport or "").lower()

    for sport in other_sports:
        if sport in message_lower and sport not in user_sport_lower:
            return sport

    return None

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
        # Changed: Only check onboarding, not full setup
        if is_onboarding_complete(user_id):
            return redirect(url_for("dashboard"))
        return redirect(url_for("onboarding"))

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

        session.clear()
        session["user_id"] = user["id"]
        session.permanent = True

        log_history(user["id"], "login")

        conn.execute("INSERT OR IGNORE INTO coach_settings (user_id) VALUES (?)", (user["id"],))
        conn.commit()
        conn.close()

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
# GOOGLE OAUTH CALLBACK
# ======================================================
@app.route("/google-callback")
def google_callback():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    try:
        conn = get_db()
        conn.execute("INSERT OR IGNORE INTO coach_settings (user_id) VALUES (?)", (user_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass

    log_history(user_id, "google_login")
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
# FORGOT PASSWORD FLOW
# ======================================================
@app.route("/forgot-password", methods=["GET"])
def forgot_password():
    return render_template("forgot_password.html")

@app.route("/forgot-password", methods=["POST"])
def forgot_password_post():
    email = request.form.get("email", "").strip()

    if not email:
        return render_template("forgot_password.html", error="Email is required.")

    try:
        conn = get_db()
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()

        if user:
            otp = str(random.randint(100000, 999999))
            conn.execute("DELETE FROM password_resets WHERE user_id = ?", (user["id"],))
            conn.execute(
                "INSERT INTO password_resets (user_id, otp, created_at) VALUES (?, ?, ?)",
                (user["id"], otp, datetime.now().isoformat())
            )
            conn.commit()
            try:
                send_otp_email(email, otp)
            except Exception:
                pass

        conn.close()
    except Exception:
        pass

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
            "SELECT otp, created_at FROM password_resets WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
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

    user = get_current_user()
    if user and user["name"]:
        # After onboarding, go directly to dashboard
        return redirect(url_for("dashboard"))

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

            # Go directly to dashboard after onboarding
            return redirect(url_for("dashboard"))
        except Exception:
            return render_template("onboarding.html", error="An error occurred. Please try again.")

    return render_template("onboarding.html")

# ======================================================
# SETUP (PROTECTED - OPTIONAL)
# ======================================================
@app.route("/setup")
@login_required
def setup():
    user_id = session["user_id"]

    # Setup page is always accessible after onboarding
    if not is_onboarding_complete(user_id):
        return redirect(url_for("onboarding"))

    return render_template("setup.html")

@app.route("/api/setup-status")
def setup_status():
    """Returns setup status for each module - used to show 'Set' badge on cards"""
    uid = session.get("user_id")
    # FIX: Try cookie-based restore if session is empty (prevents race condition on refresh)
    if not uid:
        cookie_uid = request.cookies.get("uid")
        if cookie_uid:
            try:
                uid = int(cookie_uid)
                session["user_id"] = uid
                session.permanent = True
            except (ValueError, TypeError):
                pass
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

        # Mark setup complete if all modules are set (legacy behavior)
        if all(status.values()):
            mark_setup_complete(uid)

        # complete flag indicates if all 4 modules have data (for UI display only)
        status["complete"] = all(status.values())
        
        # can_access_dashboard is always true after onboarding
        status["can_access_dashboard"] = is_onboarding_complete(uid)

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
# SAVE APIs
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

        days_left = data.get("days_left")
        category = data.get("category", "")
        importance = data.get("importance", "")

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
        return jsonify(success=False, error="Error saving tournament data."), 500

# ======================================================
# DASHBOARD (PROTECTED - ONBOARDING REQUIRED)
# ======================================================
@app.route("/dashboard")
@setup_required
def dashboard():
    user_id = session["user_id"]
    role = get_user_role(user_id)
    return render_template("dashboard.html", user_role=role)

# ======================================================
# CHAT PERSISTENCE API
# ======================================================
@app.route("/api/chat/history")
def get_chat_history():
    """Get last 25 messages for dashboard chat restoration"""
    uid = session.get("user_id")
    # FIX: Try cookie-based restore if session is empty (prevents race condition on refresh)
    if not uid:
        cookie_uid = request.cookies.get("uid")
        if cookie_uid:
            try:
                uid = int(cookie_uid)
                session["user_id"] = uid
                session.permanent = True
            except (ValueError, TypeError):
                pass
    if not uid:
        return jsonify(messages=[]), 401

    try:
        limit = request.args.get("limit", 25, type=int)
        mode = request.args.get("mode", None)

        conn = get_db()
        if mode:
            rows = conn.execute(
                """SELECT id, role, mode, message, created_at 
                   FROM coach_messages 
                   WHERE user_id=? AND mode=? AND (is_deleted=0 OR is_deleted IS NULL)
                   ORDER BY id DESC LIMIT ?""",
                (uid, mode, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, role, mode, message, created_at 
                   FROM coach_messages 
                   WHERE user_id=? AND (is_deleted=0 OR is_deleted IS NULL)
                   ORDER BY id DESC LIMIT ?""",
                (uid, limit)
            ).fetchall()
        conn.close()

        messages = [
            {
                "id": row["id"],
                "role": row["role"],
                "mode": row["mode"],
                "message": row["message"],
                "created_at": row["created_at"]
            }
            for row in rows
        ]
        # Reverse to show oldest first (chronological order)
        messages.reverse()

        return jsonify(messages=messages)
    except Exception:
        return jsonify(messages=[]), 500

# ======================================================
# HISTORY (PROTECTED - ONBOARDING REQUIRED)
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
# COACH SETTINGS (PROTECTED - ONBOARDING REQUIRED)
# ======================================================
@app.route("/coach-settings")
@setup_required
def coach_settings_page():
    return render_template("coach_settings.html")

@app.route("/api/coach-settings", methods=["GET", "POST"])
def coach_settings_api():
    uid = session.get("user_id")
    # FIX: Try cookie-based restore if session is empty
    if not uid:
        cookie_uid = request.cookies.get("uid")
        if cookie_uid:
            try:
                uid = int(cookie_uid)
                session["user_id"] = uid
                session.permanent = True
            except (ValueError, TypeError):
                pass
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
# COACH API (INTELLIGENCE LAYER)
# ======================================================
@app.route("/api/coach", methods=["POST"])
def coach():
    """
    Coach Intelligence Layer API
    - Tone DNA: calm, strict, motivational, analytical (mode-aware)
    - Smart Clarity: SHORT default, auto-expand for triggers
    - WHY-TRUST: WHAT + WHY + RISK format
    - Memory Anchors: ONE relevant fact from context
    """
    uid = session.get("user_id")
    # FIX: Try cookie-based restore if session is empty
    if not uid:
        cookie_uid = request.cookies.get("uid")
        if cookie_uid:
            try:
                uid = int(cookie_uid)
                session["user_id"] = uid
                session.permanent = True
            except (ValueError, TypeError):
                pass
    if not uid:
        return jsonify(reply="Please login again."), 401

    try:
        data = request.get_json()
        mode = data.get("mode", "Ask").lower()
        message = data.get("text", "")

        if not message.strip():
            return jsonify(reply="Please type a question for the coach.")

        valid_modes = ["training", "diet", "injury", "tournament", "ask"]
        if mode not in valid_modes:
            mode = "ask"

        save_chat_message(uid, "user", message, mode)

        conn = get_db()
        settings = conn.execute(
            "SELECT ai_enabled, style, reply_length FROM coach_settings WHERE user_id=?",
            (uid,)).fetchone()
        conn.close()

        if not settings or not settings["ai_enabled"]:
            return jsonify(reply="Coach is locked. Enable AI from Coach Settings.")

        # Get full user context
        context = get_user_context(uid)

        # Determine reply depth (smart clarity)
        depth = determine_reply_depth(message, context)

        # Check for sport mismatch
        mismatched_sport = check_sport_mismatch(message, context.get("sport"))
        if mismatched_sport:
            user_sport = context.get("sport", "your sport")
            tone = context.get("tone", "calm")

            redirects = {
                "calm": f"I noticed you're asking about {mismatched_sport}. I specialize in {user_sport} coaching, so let me help you with that instead!",
                "strict": f"That's about {mismatched_sport}. I coach {user_sport}. Let's stay focused.",
                "motivational": f"Hey, {mismatched_sport} is cool, but you're here to dominate {user_sport}! Let's focus on your sport!",
                "analytical": f"Query detected for {mismatched_sport}. Current profile: {user_sport}. Redirecting to relevant advice."
            }

            reply = f"""{redirects.get(tone, redirects['calm'])}

WHAT: Ask me anything about {user_sport} training, nutrition, or competition prep.

WHY: Sport-specific advice is more effective for your goals.

RISK: Generic advice may not optimize your {user_sport} performance."""

            save_chat_message(uid, "coach", reply, mode)
            log_history(uid, "used_coach", mode)
            return jsonify(reply=reply)

        # Try OpenAI, fallback to deterministic response
        if client is None:
            reply = generate_fallback_response(mode, context, depth)
        else:
            try:
                system_prompt = build_ai_system_prompt(mode, context, depth)

                max_tokens_map = {"short": 250, "medium": 400, "deep": 600}
                max_tokens = max_tokens_map.get(depth, 250)

                res = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": message}
                    ],
                    max_tokens=max_tokens,
                    temperature=0.7
                )
                reply = res.choices[0].message.content.strip()
            except Exception as e:
                print(f"OpenAI error: {e}")
                reply = generate_fallback_response(mode, context, depth)

        save_chat_message(uid, "coach", reply, mode)
        log_history(uid, "used_coach", mode)
        return jsonify(reply=reply)
    except Exception as e:
        print(f"Coach API error: {e}")
        return jsonify(reply="An error occurred. Please try again.")

# ======================================================
# CLEAR CHAT (SOFT DELETE - preserves messages)
# ======================================================
@app.route("/api/clear-chat", methods=["POST"])
def clear_chat():
    """Soft delete all chat messages for user (can be recovered)"""
    uid = session.get("user_id")
    if not uid:
        return jsonify(success=False), 401

    try:
        conn = get_db()
        # Soft delete: mark as deleted instead of removing
        conn.execute(
            "UPDATE coach_messages SET is_deleted=1, deleted_at=? WHERE user_id=? AND (is_deleted=0 OR is_deleted IS NULL)",
            (datetime.now().isoformat(), uid)
        )
        conn.commit()
        conn.close()
        log_history(uid, "chat_cleared")
        return jsonify(success=True)
    except Exception:
        return jsonify(success=False), 500

# ======================================================
# CHAT LIFECYCLE APIs
# ======================================================

@app.route("/api/chat/messages", methods=["GET"])
def get_chat_messages():
    """Get active (non-deleted) chat messages for dashboard"""
    uid = session.get("user_id")
    # FIX: Try cookie-based restore if session is empty (prevents race condition on refresh)
    if not uid:
        cookie_uid = request.cookies.get("uid")
        if cookie_uid:
            try:
                uid = int(cookie_uid)
                session["user_id"] = uid
                session.permanent = True
            except (ValueError, TypeError):
                pass
    if not uid:
        return jsonify(messages=[]), 401

    try:
        mode = request.args.get("mode", None)
        limit = request.args.get("limit", 50, type=int)

        conn = get_db()
        if mode:
            rows = conn.execute(
                """SELECT id, role, mode, message, created_at
                   FROM coach_messages
                   WHERE user_id=? AND mode=? AND (is_deleted=0 OR is_deleted IS NULL)
                   ORDER BY id DESC LIMIT ?""",
                (uid, mode, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, role, mode, message, created_at
                   FROM coach_messages
                   WHERE user_id=? AND (is_deleted=0 OR is_deleted IS NULL)
                   ORDER BY id DESC LIMIT ?""",
                (uid, limit)
            ).fetchall()
        conn.close()

        messages = [
            {
                "id": row["id"],
                "role": row["role"],
                "mode": row["mode"],
                "message": row["message"],
                "created_at": row["created_at"]
            }
            for row in rows
        ]
        # Reverse to show oldest first
        messages.reverse()

        return jsonify(messages=messages)
    except Exception:
        return jsonify(messages=[]), 500

@app.route("/api/chat/deleted", methods=["GET"])
def get_deleted_messages():
    """Get soft-deleted messages for recovery (settings page)"""
    uid = session.get("user_id")
    if not uid:
        return jsonify(messages=[]), 401

    try:
        limit = request.args.get("limit", 100, type=int)

        conn = get_db()
        rows = conn.execute(
            """SELECT id, role, mode, message, created_at, deleted_at
               FROM coach_messages
               WHERE user_id=? AND is_deleted=1
               ORDER BY deleted_at DESC LIMIT ?""",
            (uid, limit)
        ).fetchall()
        conn.close()

        messages = [
            {
                "id": row["id"],
                "role": row["role"],
                "mode": row["mode"],
                "message": row["message"],
                "created_at": row["created_at"],
                "deleted_at": row["deleted_at"]
            }
            for row in rows
        ]

        return jsonify(messages=messages)
    except Exception:
        return jsonify(messages=[]), 500

@app.route("/api/chat/delete", methods=["POST"])
def soft_delete_message():
    """Soft delete a specific message by ID"""
    uid = session.get("user_id")
    if not uid:
        return jsonify(success=False, error="Please login again."), 401

    try:
        data = request.get_json()
        message_id = data.get("message_id")

        if not message_id:
            return jsonify(success=False, error="Message ID required."), 400

        conn = get_db()
        # Verify ownership and soft delete
        result = conn.execute(
            "UPDATE coach_messages SET is_deleted=1, deleted_at=? WHERE id=? AND user_id=? AND (is_deleted=0 OR is_deleted IS NULL)",
            (datetime.now().isoformat(), message_id, uid)
        )
        conn.commit()

        if result.rowcount == 0:
            conn.close()
            return jsonify(success=False, error="Message not found or already deleted."), 404

        conn.close()
        log_history(uid, "message_deleted", f"id:{message_id}")
        return jsonify(success=True)
    except Exception:
        return jsonify(success=False, error="Error deleting message."), 500

@app.route("/api/chat/recover", methods=["POST"])
def recover_message():
    """Recover a soft-deleted message by ID"""
    uid = session.get("user_id")
    if not uid:
        return jsonify(success=False, error="Please login again."), 401

    try:
        data = request.get_json()
        message_id = data.get("message_id")

        if not message_id:
            return jsonify(success=False, error="Message ID required."), 400

        conn = get_db()
        # Verify ownership and recover
        result = conn.execute(
            "UPDATE coach_messages SET is_deleted=0, deleted_at=NULL WHERE id=? AND user_id=? AND is_deleted=1",
            (message_id, uid)
        )
        conn.commit()

        if result.rowcount == 0:
            conn.close()
            return jsonify(success=False, error="Message not found or not deleted."), 404

        conn.close()
        log_history(uid, "message_recovered", f"id:{message_id}")
        return jsonify(success=True)
    except Exception:
        return jsonify(success=False, error="Error recovering message."), 500

@app.route("/api/chat/recover-all", methods=["POST"])
def recover_all_messages():
    """Recover all soft-deleted messages for user"""
    uid = session.get("user_id")
    if not uid:
        return jsonify(success=False, error="Please login again."), 401

    try:
        conn = get_db()
        result = conn.execute(
            "UPDATE coach_messages SET is_deleted=0, deleted_at=NULL WHERE user_id=? AND is_deleted=1",
            (uid,)
        )
        conn.commit()
        recovered_count = result.rowcount
        conn.close()

        log_history(uid, "all_messages_recovered", f"count:{recovered_count}")
        return jsonify(success=True, recovered_count=recovered_count)
    except Exception:
        return jsonify(success=False, error="Error recovering messages."), 500

@app.route("/api/chat/permanent-delete", methods=["POST"])
def permanent_delete_message():
    """Permanently delete a message (cannot be recovered)"""
    uid = session.get("user_id")
    if not uid:
        return jsonify(success=False, error="Please login again."), 401

    try:
        data = request.get_json()
        message_id = data.get("message_id")

        if not message_id:
            return jsonify(success=False, error="Message ID required."), 400

        conn = get_db()
        # Verify ownership and permanently delete
        result = conn.execute(
            "DELETE FROM coach_messages WHERE id=? AND user_id=?",
            (message_id, uid)
        )
        conn.commit()

        if result.rowcount == 0:
            conn.close()
            return jsonify(success=False, error="Message not found."), 404

        conn.close()
        log_history(uid, "message_permanently_deleted", f"id:{message_id}")
        return jsonify(success=True)
    except Exception:
        return jsonify(success=False, error="Error deleting message."), 500

@app.route("/api/chat/permanent-delete-all", methods=["POST"])
def permanent_delete_all_deleted():
    """Permanently delete all soft-deleted messages (empty trash)"""
    uid = session.get("user_id")
    if not uid:
        return jsonify(success=False, error="Please login again."), 401

    try:
        conn = get_db()
        result = conn.execute(
            "DELETE FROM coach_messages WHERE user_id=? AND is_deleted=1",
            (uid,)
        )
        conn.commit()
        deleted_count = result.rowcount
        conn.close()

        log_history(uid, "trash_emptied", f"count:{deleted_count}")
        return jsonify(success=True, deleted_count=deleted_count)
    except Exception:
        return jsonify(success=False, error="Error emptying trash."), 500

@app.route("/api/chat/stats", methods=["GET"])
def get_chat_stats():
    """Get chat statistics including deleted message count"""
    uid = session.get("user_id")
    if not uid:
        return jsonify({}), 401

    try:
        conn = get_db()
        active_count = conn.execute(
            "SELECT COUNT(*) as count FROM coach_messages WHERE user_id=? AND (is_deleted=0 OR is_deleted IS NULL)",
            (uid,)
        ).fetchone()["count"]

        deleted_count = conn.execute(
            "SELECT COUNT(*) as count FROM coach_messages WHERE user_id=? AND is_deleted=1",
            (uid,)
        ).fetchone()["count"]

        conn.close()

        return jsonify({
            "active_messages": active_count,
            "deleted_messages": deleted_count,
            "total_messages": active_count + deleted_count
        })
    except Exception:
        return jsonify({}), 500

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
