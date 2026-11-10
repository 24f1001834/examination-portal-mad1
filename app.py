"""Authentication and role-specific entry pages for the examination portal."""

import re
import secrets
import sqlite3
from functools import wraps
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from database import DATABASE_PATH, get_connection


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=DATABASE_PATH,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=16 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    if not Path(app.config["DATABASE"]).is_file():
        raise RuntimeError("Database missing. Run init_db.py before starting the portal.")

    # Keep the signing key between restarts, but never commit it to Git.
    if not app.config.get("SECRET_KEY"):
        secret_path = Path(__file__).resolve().parent / ".session-secret"
        try:
            with secret_path.open("x", encoding="utf-8") as secret_file:
                secret_file.write(secrets.token_hex(32))
        except FileExistsError:
            pass
        app.config["SECRET_KEY"] = secret_path.read_text(encoding="utf-8").strip()
        if not app.config["SECRET_KEY"]:
            raise RuntimeError("The local .session-secret file is empty.")

    def get_db():
        if "db" not in g:
            g.db = get_connection(app.config["DATABASE"])
        return g.db

    @app.teardown_appcontext
    def close_db(error=None):
        connection = g.pop("db", None)
        if connection is not None:
            connection.close()

    @app.before_request
    def load_user_and_check_form():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(32)
        if request.method == "POST":
            token = request.form.get("csrf_token", "")
            if not secrets.compare_digest(token.encode("utf-8"), session["csrf_token"].encode("utf-8")):
                abort(400, description="Invalid form token. Reload the page and try again.")

        # Read current permissions from SQLite, not from a client-supplied role.
        g.user = None
        user_id = session.get("user_id")
        if user_id is not None:
            user = get_db().execute(
                """SELECT u.*, p.status AS examiner_status
                   FROM users AS u LEFT JOIN examiner_profiles AS p
                   ON p.examiner_id = u.id WHERE u.id = ?""",
                (user_id,),
            ).fetchone()
            if user and user["is_active"] and (
                user["role"] != "Examiner" or user["examiner_status"] == "Approved"
            ):
                g.user = user
            else:
                session.pop("user_id", None)

    def role_required(role):
        def decorator(view):
            @wraps(view)
            def protected_view(*args, **kwargs):
                if g.user is None:
                    return redirect(url_for("login"))
                if g.user["role"] != role:
                    abort(403)
                return view(*args, **kwargs)
            return protected_view
        return decorator

    def dashboard_url():
        return url_for(g.user["role"].lower() + "_dashboard")

    @app.route("/")
    def index():
        return redirect(dashboard_url() if g.user else url_for("login"))

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if g.user:
            return redirect(dashboard_url())
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            role = request.form.get("role", "")
            phone = request.form.get("phone", "").strip()
            department = request.form.get("department", "").strip()
            error = None
            if role not in ("Student", "Examiner"):
                error = "Choose Student or Examiner. Admin registration is not allowed."
            elif not name or len(name) > 100:
                error = "Enter a name between 1 and 100 characters."
            elif len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                error = "Enter a valid email address."
            elif not 8 <= len(password) <= 128:
                error = "Password must contain between 8 and 128 characters."
            elif password != request.form.get("confirm_password", ""):
                error = "Passwords do not match."
            elif len(phone) > 30 or len(department) > 100:
                error = "Phone must be at most 30 characters and department at most 100."
            elif role == "Examiner" and not department:
                error = "Department is required for Examiners."

            if error is None:
                db = get_db()
                try:
                    # A single transaction prevents half-created Examiner accounts.
                    with db:
                        cursor = db.execute(
                            "INSERT INTO users (email,password_hash,name,role,phone) VALUES (?,?,?,?,?)",
                            (email, generate_password_hash(password), name, role, phone),
                        )
                        if role == "Examiner":
                            db.execute(
                                "INSERT INTO examiner_profiles (examiner_id,department) VALUES (?,?)",
                                (cursor.lastrowid, department),
                            )
                except sqlite3.IntegrityError:
                    error = "An account with this email already exists."
                else:
                    message = "Registration successful. You can now log in."
                    if role == "Examiner":
                        message = "Registration successful. Wait for Admin approval before logging in."
                    flash(message, "success")
                    return redirect(url_for("login"))
            flash(error, "danger")
        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(dashboard_url())
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = get_db().execute(
                """SELECT u.*, p.status AS examiner_status
                   FROM users AS u LEFT JOIN examiner_profiles AS p
                   ON p.examiner_id = u.id WHERE u.email = ?""", (email,),
            ).fetchone()
            if not user or not check_password_hash(user["password_hash"], password):
                flash("Invalid email or password.", "danger")
            elif not user["is_active"]:
                flash("Your account is inactive. Contact the Admin.", "danger")
            elif user["role"] == "Examiner" and user["examiner_status"] != "Approved":
                flash("Examiner access requires Admin approval. Contact the Admin.", "warning")
            else:
                session.clear()
                session["user_id"] = user["id"]
                session["csrf_token"] = secrets.token_hex(32)
                return redirect(url_for(user["role"].lower() + "_dashboard"))
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/admin/dashboard")
    @role_required("Admin")
    def admin_dashboard():
        return render_template("dashboard.html", role="Admin")

    @app.route("/examiner/dashboard")
    @role_required("Examiner")
    def examiner_dashboard():
        return render_template("dashboard.html", role="Examiner")

    @app.route("/student/dashboard")
    @role_required("Student")
    def student_dashboard():
        return render_template("dashboard.html", role="Student")

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("forbidden.html"), 403

    return app


if __name__ == "__main__":
    create_app().run()
