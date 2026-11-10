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
        # Older versions required timelines during creation. Make those fields
        # optional so Admin can configure them later from Manage.
        columns = connection.execute('PRAGMA table_info(examinations)').fetchall()
        if any(column['name'] == 'booking_end' and column['notnull'] for column in columns):
            connection.execute('PRAGMA foreign_keys = OFF')
            try:
                with connection:
                    schema = schema_path.read_text(encoding='utf-8')
                    start = schema.index('CREATE TABLE IF NOT EXISTS examinations (')
                    end = schema.index(';', start) + 1
                    statement = schema[start:end].replace('IF NOT EXISTS examinations', 'examinations_updated')
                    connection.execute(statement)
                    new_columns = [c['name'] for c in connection.execute('PRAGMA table_info(examinations_updated)')]
                    names = ','.join(new_columns)
                    connection.execute(f'INSERT INTO examinations_updated ({names}) SELECT {names} FROM examinations')
                    connection.execute('DROP TABLE examinations')
                    connection.execute('ALTER TABLE examinations_updated RENAME TO examinations')
                    if connection.execute('PRAGMA foreign_key_check').fetchall():
                        raise RuntimeError('Database update failed its relationship check.')
            finally:
                connection.execute('PRAGMA foreign_keys = ON')
        # Merge the old Rejected state into Deactivated, preserving account IDs.
        with connection:
            connection.execute("UPDATE users SET is_active=0 WHERE id IN (SELECT examiner_id FROM examiner_profiles WHERE status='Rejected')")
            connection.execute("UPDATE examiner_profiles SET status='Deactivated' WHERE status='Rejected'")
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
