"""Create the schema and the initial Admin without erasing existing data."""

from getpass import getpass
from pathlib import Path

from werkzeug.security import generate_password_hash

from database import DATABASE_PATH, get_connection


def initialize_database(admin_email=None, admin_password=None, database_path=DATABASE_PATH):
    """Initialize tables; ask for credentials only if no Admin exists."""
    schema_path = Path(__file__).resolve().parent / "schema.sql"
    connection = get_connection(database_path)
    try:
        connection.executescript(schema_path.read_text(encoding="utf-8"))
        admin = connection.execute(
            "SELECT id FROM users WHERE role = 'Admin'"
        ).fetchone()
        if admin is not None:
            return False

        if admin_email is None:
            admin_email = input("Admin email: ")
        admin_email = admin_email.strip().lower()
        if not admin_email or "@" not in admin_email:
            raise ValueError("Enter a valid Admin email address.")

        if admin_password is None:
            admin_password = getpass("Admin password (at least 8 characters): ")
            if admin_password != getpass("Confirm Admin password: "):
                raise ValueError("Passwords do not match.")
        if len(admin_password) < 8:
            raise ValueError("Admin password must contain at least 8 characters.")

        connection.execute(
            "INSERT INTO users (email, password_hash, name, role) VALUES (?, ?, ?, ?)",
            (admin_email, generate_password_hash(admin_password), "Admin", "Admin"),
        )
        connection.commit()
        return True
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        created = initialize_database()
    except ValueError as error:
        raise SystemExit(str(error))
    print(f"Database ready: {DATABASE_PATH}")
    print("Admin created." if created else "Existing Admin and data preserved.")
