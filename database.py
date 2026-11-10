"""Shared SQLite connection settings for the examination portal."""

import sqlite3
from pathlib import Path

DATABASE_PATH = Path(__file__).resolve().parent / "examination_portal.db"


def get_connection(database_path=DATABASE_PATH):
    """Return a connection whose rows can be read using column names."""
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    # SQLite requires this setting on every connection.
    connection.execute("PRAGMA foreign_keys = ON")
    return connection
