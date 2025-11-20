import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from .db import _connect

load_dotenv()
DB_PATH = os.environ.get("SOA_BUILDER_DB", "soa_builder_web.db")
logger = logging.getLogger("soa_builder.concepts")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)


# Migration: rename database table cell to matrix_cells
def _migrate_copy_cell_data():
    try:
        conn = _connect()
        cur = conn.cursor()
        # Check if both tables exist
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cell'")
        cell_exists = cur.fetchone() is not None
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='matrix_cells'"
        )
        matrix_exists = cur.fetchone() is not None
        if not (cell_exists and matrix_exists):
            conn.close()
            return
        # Only copy if matrix_cells is empty
        cur.execute("SELECT COUNT(*) FROM matrix_cells")
        if cur.fetchone()[0] > 0:
            conn.close()
            return
        # Copy data
        cur.execute(
            "INSERT INTO matrix_cells (soa_id, visit_id, activity_id, status) SELECT soa_id, visit_id, activity_id, status FROM cell"
        )
        conn.commit()
        logger.info("Copied data from 'cell' to 'matrix_cells'")
        conn.close()
    except Exception as e:
        logger.warning("cell->matrix_cells data copy error: %s", e)


# Migration: add arm_uid to arm
def _migrate_add_arm_uid():
    """Ensure arm_uid column exists and is populated with StudyArm_<n> unique per soa.
    Backfills existing arms sequentially by id order if missing. Creates unique index (soa_id, arm_uid).
    """
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(arm)")
        cols = {r[1] for r in cur.fetchall()}
        if "arm_uid" not in cols:
            cur.execute("ALTER TABLE arm ADD COLUMN arm_uid TEXT")
            conn.commit()
        # Backfill any NULL arm_uid values
        cur.execute("SELECT DISTINCT soa_id FROM arm WHERE arm_uid IS NULL")
        soa_ids = [r[0] for r in cur.fetchall()]
        for sid in soa_ids:
            cur.execute(
                "SELECT id FROM arm WHERE soa_id=? AND arm_uid IS NULL ORDER BY id",
                (sid,),
            )
            ids = [r[0] for r in cur.fetchall()]
            # Determine existing numbers to avoid collision (if partial data present)
            cur.execute(
                "SELECT arm_uid FROM arm WHERE soa_id=? AND arm_uid IS NOT NULL", (sid,)
            )
            existing_uids = {r[0] for r in cur.fetchall() if r[0]}
            used_nums = set()
            for uid in existing_uids:
                if uid.startswith("StudyArm_"):
                    try:
                        used_nums.add(int(uid.split("StudyArm_")[-1]))
                    except Exception:
                        pass
            next_n = 1
            for arm_id in ids:
                while next_n in used_nums:
                    next_n += 1
                new_uid = f"StudyArm_{next_n}"
                used_nums.add(next_n)
                next_n += 1
                cur.execute("UPDATE arm SET arm_uid=? WHERE id=?", (new_uid, arm_id))
        # Create unique index
        try:
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_arm_soaid_uid ON arm(soa_id, arm_uid)"
            )
            conn.commit()
        except Exception:
            pass
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("arm_uid migration failed: %s", e)


# Migration: drop deprecated arm linkage columns
def _migrate_drop_arm_element_link():
    """If legacy columns (element_id, etcd) exist in arm, rebuild table without them.
    SQLite cannot drop columns directly; we create new table, copy data, replace.
    Safe to run multiple times (idempotent)."""
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(arm)")
        cols = [r[1] for r in cur.fetchall()]
        if "element_id" in cols or "etcd" in cols:
            logger.info(
                "Rebuilding arm table to drop deprecated columns element_id, etcd"
            )
            # Determine if arm_uid index exists to recreate later
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_arm_soaid_uid'"
            )
            has_uid_index = cur.fetchone() is not None
            # Create new table
            cur.execute(
                """
                CREATE TABLE arm_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    soa_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    label TEXT,
                    description TEXT,
                    order_index INTEGER,
                    arm_uid TEXT
                )
            """
            )
            # Copy data (ignore legacy columns)
            # Only select columns that persist
            """
            select_cols = [
                c
                for c in [
                    "id",
                    "soa_id",
                    "name",
                    "label",
                    "description",
                    "order_index",
                    "arm_uid",
                ]
                if c in cols
            ]
            """
            cur.execute(
                "INSERT INTO arm_new (id,soa_id,name,label,description,order_index,arm_uid) SELECT id,soa_id,name,label,description,order_index,arm_uid FROM arm"
            )
            # Drop old table, rename
            cur.execute("DROP TABLE arm")
            cur.execute("ALTER TABLE arm_new RENAME TO arm")
            if has_uid_index:
                try:
                    cur.execute(
                        "CREATE UNIQUE INDEX IF NOT EXISTS idx_arm_soaid_uid ON arm(soa_id, arm_uid)"
                    )
                except Exception:
                    pass
            conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("arm linkage drop migration failed: %s", e)


# Migration: add epoch_id to visit
def _migrate_add_epoch_id_to_visit():
    """Add epoch_id column to visit table if missing."""
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(visit)")
        cols = {r[1] for r in cur.fetchall()}
        if "epoch_id" not in cols:
            cur.execute("ALTER TABLE visit ADD COLUMN epoch_id INTEGER")
            conn.commit()
            logger.info("Added epoch_id column to visit table")
        conn.close()
    except Exception as e:
        logger.warning("epoch_id migration failed: %s", e)


# Migration: add epoch_seq to epoch
def _migrate_add_epoch_seq():
    """Ensure epoch_seq (immutable sequence per SoA) exists; backfill sequential values per study.
    Creates unique index (soa_id, epoch_seq) to guarantee uniqueness inside a study.
    """
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(epoch)")
        cols = {r[1] for r in cur.fetchall()}
        if "epoch_seq" not in cols:
            cur.execute("ALTER TABLE epoch ADD COLUMN epoch_seq INTEGER")
            conn.commit()
            logger.info("Added epoch_seq column to epoch table")
            # Backfill existing epochs with sequential values by id order per soa
            cur.execute("SELECT DISTINCT soa_id FROM epoch")
            soa_ids = [r[0] for r in cur.fetchall()]
            for sid in soa_ids:
                cur.execute("SELECT id FROM epoch WHERE soa_id=? ORDER BY id", (sid,))
                ids = [r[0] for r in cur.fetchall()]
                for seq, eid in enumerate(ids, start=1):
                    cur.execute("UPDATE epoch SET epoch_seq=? WHERE id=?", (seq, eid))
            conn.commit()
        # Unique index (idempotent)
        try:
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_epoch_soaid_seq ON epoch(soa_id, epoch_seq)"
            )
            conn.commit()
        except Exception as ie:  # pragma: no cover
            logger.warning("Failed creating idx_epoch_soaid_seq: %s", ie)
        conn.close()
    except Exception as e:  # pragma: no cover
        logger.warning("epoch_seq migration failed: %s", e)


# Migration: add epoch label/description
def _migrate_add_epoch_label_desc():
    """Add optional epoch_label and epoch_description columns if missing."""
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(epoch)")
        cols = {r[1] for r in cur.fetchall()}
        alters = []
        if "epoch_label" not in cols:
            alters.append("ALTER TABLE epoch ADD COLUMN epoch_label TEXT")
        if "epoch_description" not in cols:
            alters.append("ALTER TABLE epoch ADD COLUMN epoch_description TEXT")
        for stmt in alters:
            try:
                cur.execute(stmt)
            except Exception as e:  # pragma: no cover
                logger.warning(
                    "Failed epoch label/description migration '%s': %s", stmt, e
                )
        if alters:
            conn.commit()
            logger.info(
                "Applied epoch label/description migration: %s", ", ".join(alters)
            )
        conn.close()
    except Exception as e:  # pragma: no cover
        logger.warning("Epoch label/description migration failed: %s", e)


# Migration: create code_junction table
def _migrate_create_code_junction():
    """Create code_junction linking table if absent.

    Columns:
        id INTEGER PRIMARY KEY AUTOINCREMENT
        code_uid TEXT                -- opaque unique identifier for the code instance
        codelist_table TEXT          -- source table name that provided the code
        codelist_code TEXT           -- code value from source codelist
        type_code TEXT               -- type/category for the code (e.g., TERM, SYNONYM)
        data_origin_type_code TEXT   -- origin classification (e.g., DDF, PROTOCOL, IMPORT)
        soa_id INTEGER               -- optional foreign key to study (not enforced)
        linked_table TEXT            -- target table name being linked
        linked_column TEXT           -- column name in target table referencing the code
        linked_id TEXT               -- id/key in target table row (stored as TEXT for flexibility)

    Indexes can be added later once query patterns emerge. Using TEXT for linked_id avoids
    premature typing constraints (could be INT or UUID)."""
    try:
        conn = _connect()
        cur = conn.cursor()
        # Detect existing table
        cur.execute("PRAGMA table_info(code_junction)")
        existing_cols = [r[1] for r in cur.fetchall()]
        if existing_cols:  # table already exists
            conn.close()
            return
        cur.execute(
            """
                        CREATE TABLE code_junction (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            code_uid TEXT,
                            codelist_table TEXT,
                            codelist_code TEXT,
                            type_code TEXT,
                            data_origin_type_code TEXT,
                            soa_id INTEGER,
                            linked_table TEXT,
                            linked_column TEXT,
                            linked_id TEXT
                        )
                        """
        )
        conn.commit()
        conn.close()
        logger.info("Created code_junction table")
    except Exception as e:  # pragma: no cover
        logger.warning("code_junction migration failed: %s", e)


# Migrations: add study metadata columns
def _migrate_add_study_fields():
    """Ensure study metadata columns (study_id, study_label, study_description) exist on soa table.
    Safe to run repeatedly; SQLite ADD COLUMN is idempotent when guarded by schema inspection.
    """
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(soa)")
        existing = {r[1] for r in cur.fetchall()}  # column names
        alters = []
        if "study_id" not in existing:
            alters.append("ALTER TABLE soa ADD COLUMN study_id TEXT")
        if "study_label" not in existing:
            alters.append("ALTER TABLE soa ADD COLUMN study_label TEXT")
        if "study_description" not in existing:
            alters.append("ALTER TABLE soa ADD COLUMN study_description TEXT")
        for stmt in alters:
            try:
                cur.execute(stmt)
            except (
                Exception
            ) as e:  # pragma: no cover - defensive; should not fail normally
                logger.warning("Failed executing migration statement '%s': %s", stmt, e)
        if alters:
            conn.commit()
        # Create unique index on study_id (NULLs allowed multiple times by SQLite)
        try:
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_soa_study_id ON soa(study_id)"
            )
            conn.commit()
        except Exception as e:  # pragma: no cover
            logger.warning("Failed creating unique index idx_soa_study_id: %s", e)
        conn.close()
        if alters:
            logger.info("Applied study field migrations: %s", ", ".join(alters))
    except Exception as e:  # pragma: no cover
        logger.warning("Study field migration failed: %s", e)


# Migrations: Drop legacy activity_concept_override table
def _drop_unused_override_table():
    """Drop legacy activity_concept_override table if it still exists.
    This table supported mutable concept titles which are no longer allowed.
    Safe to run repeatedly; will no-op if table absent."""
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='activity_concept_override'"
        )
        if cur.fetchone():
            try:
                cur.execute("DROP TABLE activity_concept_override")
                conn.commit()
                logger.info("Dropped obsolete table activity_concept_override")
            except Exception as e:
                logger.warning(
                    "Failed to drop obsolete table activity_concept_override: %s", e
                )
        conn.close()
    except Exception as e:
        logger.warning("Migration check for activity_concept_override failed: %s", e)


# Migration: ensure element table columns exist
def _migrate_element_table():
    """Ensure element table has full expected schema (order_index, label, description, testrl, teenrl, created_at).
    Backfills order_index sequentially by id if missing.
    Safe to run repeatedly."""
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='element'"
        )
        if not cur.fetchone():
            conn.close()
            return  # table does not exist yet (fresh init will create with full schema)
        cur.execute("PRAGMA table_info(element)")
        cols = {r[1] for r in cur.fetchall()}
        alters = []
        # Add missing columns
        if "order_index" not in cols:
            alters.append("ALTER TABLE element ADD COLUMN order_index INTEGER")
        if "label" not in cols:
            alters.append("ALTER TABLE element ADD COLUMN label TEXT")
        if "description" not in cols:
            alters.append("ALTER TABLE element ADD COLUMN description TEXT")
        if "testrl" not in cols:
            alters.append("ALTER TABLE element ADD COLUMN testrl TEXT")
        if "teenrl" not in cols:
            alters.append("ALTER TABLE element ADD COLUMN teenrl TEXT")
        if "created_at" not in cols:
            alters.append("ALTER TABLE element ADD COLUMN created_at TEXT")
        for stmt in alters:
            try:
                cur.execute(stmt)
            except Exception as e:  # pragma: no cover
                logger.warning("Element migration failed executing '%s': %s", stmt, e)
        if alters:
            conn.commit()
        # Backfill order_index if newly added
        if "order_index" not in cols:
            cur.execute("SELECT id FROM element ORDER BY id")
            ids = [r[0] for r in cur.fetchall()]
            for idx, eid in enumerate(ids, start=1):
                cur.execute("UPDATE element SET order_index=? WHERE id=?", (idx, eid))
        # Backfill created_at
        if "created_at" not in cols:
            now = datetime.now(timezone.utc).isoformat()
            cur.execute(
                "UPDATE element SET created_at=? WHERE created_at IS NULL", (now,)
            )
        conn.commit()
        conn.close()
        if alters:
            logger.info("Applied element table migration: %s", ", ".join(alters))
    except Exception as e:  # pragma: no cover
        logger.warning("Element table migration encountered error: %s", e)


# Migration: rename legacy 'cell' table to 'matrix_cells'
def _migrate_rename_cell_table():
    try:
        conn = _connect()
        cur = conn.cursor()
        # If new table already exists nothing to do
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='matrix_cells'"
        )
        if cur.fetchone():
            conn.close()
            return
        # If legacy table exists rename it
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cell'")
        if cur.fetchone():
            try:
                cur.execute("ALTER TABLE cell RENAME TO matrix_cells")
                conn.commit()
                logger.info("Renamed legacy table 'cell' to 'matrix_cells'")
            except Exception as e:  # pragma: no cover
                logger.warning("Failed renaming cell table: %s", e)
        else:
            # Create fresh matrix_cells if neither present (defensive)
            cur.execute(
                """CREATE TABLE IF NOT EXISTS matrix_cells (id INTEGER PRIMARY KEY AUTOINCREMENT, soa_id INTEGER, visit_id INTEGER, activity_id INTEGER, status TEXT)"""
            )
            conn.commit()
            logger.info("Created matrix_cells table (no prior cell table found)")
        conn.close()
    except Exception as e:  # pragma: no cover
        logger.warning("cell->matrix_cells migration error: %s", e)


# Migration: ensure element_id column with unique StudyElement_<n> values
def _migrate_element_id():
    """Ensure element.element_id column exists and values follow prefix 'StudyElement_<n>' unique per SOA.

    Steps per SOA:
      - Add column if missing (nullable initially)
      - Collect existing values; parse numbers from well-formed prefixes StudyElement_<n>
      - Reassign malformed/NULL/duplicate values to next available sequential numbers starting at 1.
      - Create unique index (soa_id, element_id).
    Safe to run multiple times; idempotent aside from normalizing malformed values."""
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(element)")
        cols = {r[1] for r in cur.fetchall()}
        if "element" not in cols and not cols:  # table missing entirely
            conn.close()
            return
        if "element_id" not in cols:
            try:
                cur.execute("ALTER TABLE element ADD COLUMN element_id TEXT")
                conn.commit()
                logger.info("Added element_id column to element table")
            except Exception as e:  # pragma: no cover
                logger.warning("Failed adding element_id column: %s", e)
        # Backfill / normalize per SOA
        cur.execute("SELECT DISTINCT soa_id FROM element")
        soa_ids = [r[0] for r in cur.fetchall()]
        for sid in soa_ids:
            cur.execute(
                "SELECT id, element_id FROM element WHERE soa_id=? ORDER BY id", (sid,)
            )
            rows = cur.fetchall()
            used_nums = set()
            # Capture already valid numbers
            for _id, _eid in rows:
                if _eid and isinstance(_eid, str) and _eid.startswith("StudyElement_"):
                    try:
                        n = int(_eid.split("StudyElement_")[-1])
                        if n > 0:
                            if n not in used_nums:
                                used_nums.add(n)
                            else:
                                # mark duplicate for reassignment by blanking
                                cur.execute(
                                    "UPDATE element SET element_id=NULL WHERE id=?",
                                    (_id,),
                                )
                    except Exception:  # pragma: no cover
                        pass
            # Re-fetch after clearing duplicates
            cur.execute(
                "SELECT id, element_id FROM element WHERE soa_id=? ORDER BY id", (sid,)
            )
            rows = cur.fetchall()
            next_n = 1
            for _id, _eid in rows:
                valid = (
                    _eid
                    and isinstance(_eid, str)
                    and _eid.startswith("StudyElement_")
                    and _eid.split("StudyElement_")[-1].isdigit()
                )
                if valid:
                    continue  # leave intact
                while next_n in used_nums:
                    next_n += 1
                new_val = f"StudyElement_{next_n}"
                used_nums.add(next_n)
                next_n += 1
                cur.execute(
                    "UPDATE element SET element_id=? WHERE id=?", (new_val, _id)
                )
        # Create unique index
        try:
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_element_soaid_elementid ON element(soa_id, element_id)"
            )
            conn.commit()
        except Exception as e:  # pragma: no cover
            logger.warning(
                "Failed creating unique index idx_element_soaid_elementid: %s", e
            )
        conn.commit()
        conn.close()
    except Exception as e:  # pragma: no cover
        logger.warning("element_id migration encountered error: %s", e)


# Migration: Add elements_restored to rollback_audit
def _migrate_rollback_add_elements_restored():
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(rollback_audit)")
        cols = {r[1] for r in cur.fetchall()}
        if "elements_restored" not in cols:
            cur.execute(
                "ALTER TABLE rollback_audit ADD COLUMN elements_restored INTEGER"
            )
            conn.commit()
            logger.info("Added elements_restored column to rollback_audit")
        conn.close()
    except Exception as e:  # pragma: no cover
        logger.warning("rollback_audit migration failed: %s", e)


# Migration: Add activity_uid to activity
def _migrate_activity_add_uid():
    """Add activity_uid column if missing; backfill as Activity_<order_index>."""
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(activity)")
        cols = {r[1] for r in cur.fetchall()}
        if "activity_uid" not in cols:
            cur.execute("ALTER TABLE activity ADD COLUMN activity_uid TEXT")
            # backfill
            cur.execute("SELECT id, order_index FROM activity")
            for rid, oi in cur.fetchall():
                cur.execute(
                    "UPDATE activity SET activity_uid=? WHERE id=?",
                    (f"Activity_{oi}", rid),
                )
            # create unique index scoped per soa
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_activity_soa_uid ON activity(soa_id, activity_uid)"
            )
            conn.commit()
        else:
            # still ensure index exists
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_activity_soa_uid ON activity(soa_id, activity_uid)"
            )
            conn.commit()
        conn.close()
    except Exception as e:  # pragma: no cover
        logger.warning("activity_uid migration failed: %s", e)


# Migration: Add type & data_origin_type to arm
def _migrate_arm_add_type_fields():
    """Ensure arm table has type and data_origin_type columns.
    Safe to run multiple times; adds columns if missing. No backfill logic (NULL acceptable).
    """
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(arm)")
        cols = {r[1] for r in cur.fetchall()}
        alters = []
        if "type" not in cols:
            alters.append("ALTER TABLE arm ADD COLUMN type TEXT")
        if "data_origin_type" not in cols:
            alters.append("ALTER TABLE arm ADD COLUMN data_origin_type TEXT")
        for stmt in alters:
            try:
                cur.execute(stmt)
            except Exception as e:  # pragma: no cover
                logger.warning("Failed arm type field migration '%s': %s", stmt, e)
        if alters:
            conn.commit()
            logger.info(
                "Applied arm type/data_origin_type migration: %s", ", ".join(alters)
            )
        conn.close()
    except Exception as e:  # pragma: no cover
        logger.warning("Arm type/data_origin_type migration failed: %s", e)


# Backfill dataset_date for existing terminology tables
def _backfill_dataset_date(table: str, audit_table: str):
    """If terminology table exists and has dataset_date (or sheet_dataset_date) column with blank values,
    attempt to backfill from the latest audit row that has a non-null dataset_date.
    Safe to run multiple times; will no-op if already populated or columns absent."""
    try:
        conn = _connect()
        cur = conn.cursor()
        # Ensure table exists
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        )
        if not cur.fetchone():
            conn.close()
            return
        cur.execute(f"PRAGMA table_info({table})")
        cols = {r[1] for r in cur.fetchall()}
        date_col = None
        # Prefer dataset_date; fallback sheet_dataset_date
        if "dataset_date" in cols:
            date_col = "dataset_date"
        elif "sheet_dataset_date" in cols:
            date_col = "sheet_dataset_date"
        if not date_col:
            conn.close()
            return
        # Check if any non-empty value exists
        cur.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {date_col} IS NOT NULL AND {date_col} != ''"
        )
        if cur.fetchone()[0] > 0:
            conn.close()
            return  # already populated
        # Find latest audit dataset_date
        cur.execute(
            f"SELECT dataset_date FROM {audit_table} WHERE dataset_date IS NOT NULL AND dataset_date != '' ORDER BY loaded_at DESC LIMIT 1"
        )
        row = cur.fetchone()
        if not row or not row[0]:
            conn.close()
            return
        ds_date = row[0]
        cur.execute(
            f"UPDATE {table} SET {date_col}=? WHERE {date_col} IS NULL OR {date_col}=''",
            (ds_date,),
        )
        conn.commit()
        conn.close()
    except Exception as e:  # pragma: no cover
        logger.warning("dataset_date backfill for %s failed: %s", table, e)
