import os
import sqlite3

from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.environ.get("SOA_BUILDER_DB", "soa_builder_web.db")


def _connect():
    return sqlite3.connect(DB_PATH)
