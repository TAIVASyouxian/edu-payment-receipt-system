from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
QR_DIR = DATA_DIR / "qr"
RECEIPT_DIR = DATA_DIR / "receipts"
DB_PATH = DATA_DIR / "kindergarten_v3.db"
SAMPLE_BANK_CSV = DATA_DIR / "sample_bank_statement.csv"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    QR_DIR.mkdir(exist_ok=True)
    RECEIPT_DIR.mkdir(exist_ok=True)


def connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS students (
                student_id TEXT PRIMARY KEY,
                student_name TEXT NOT NULL,
                class_name TEXT NOT NULL,
                department TEXT NOT NULL DEFAULT '待確認',
                parent_name TEXT NOT NULL,
                contact TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS programs (
                program_id TEXT PRIMARY KEY,
                program_name TEXT NOT NULL,
                program_category TEXT NOT NULL,
                default_fee_amount INTEGER NOT NULL DEFAULT 0,
                billing_cycle TEXT NOT NULL DEFAULT 'monthly',
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS enrollments (
                enrollment_id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL,
                program_id TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT,
                enrollment_status TEXT NOT NULL DEFAULT 'active',
                custom_fee_amount INTEGER,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(student_id) REFERENCES students(student_id),
                FOREIGN KEY(program_id) REFERENCES programs(program_id)
            );

            CREATE TABLE IF NOT EXISTS bills (
                bill_id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL,
                program_id TEXT,
                enrollment_id TEXT,
                student_name TEXT NOT NULL,
                class_name TEXT NOT NULL,
                parent_name TEXT NOT NULL,
                month TEXT NOT NULL,
                billing_month TEXT,
                fee_item TEXT NOT NULL,
                amount INTEGER NOT NULL,
                due_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Unpaid',
                payment_status TEXT,
                payment_date TEXT,
                receipt_number TEXT,
                receipt_issue_date TEXT,
                notes TEXT,
                qr_path TEXT,
                qr_signature TEXT,
                qr_stale INTEGER NOT NULL DEFAULT 0,
                receipt_path TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(student_id) REFERENCES students(student_id)
            );

            CREATE TABLE IF NOT EXISTS transactions (
                transaction_id TEXT PRIMARY KEY,
                transaction_date TEXT NOT NULL,
                amount INTEGER NOT NULL,
                payer_name TEXT,
                payment_note TEXT,
                match_status TEXT NOT NULL,
                confidence TEXT,
                matched_bill_id TEXT,
                warning TEXT,
                imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS payment_records (
                payment_id TEXT PRIMARY KEY,
                bill_id TEXT NOT NULL,
                transaction_id TEXT,
                transaction_date TEXT NOT NULL,
                amount INTEGER NOT NULL,
                payer_name TEXT,
                payment_note TEXT,
                match_status TEXT NOT NULL,
                imported_batch_id TEXT,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(bill_id) REFERENCES bills(bill_id)
            );

            CREATE TABLE IF NOT EXISTS payment_arrangements (
                arrangement_id TEXT PRIMARY KEY,
                bill_id TEXT NOT NULL,
                arrangement_status TEXT NOT NULL,
                promised_payment_date TEXT,
                grace_until_date TEXT,
                arrangement_note TEXT,
                handled_by TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(bill_id) REFERENCES bills(bill_id)
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                entity_type TEXT,
                entity_id TEXT,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            """
        )

        def column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
            rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            return any(row[1] == column_name for row in rows)

        def table_columns(table: str) -> set[str]:
            return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

        def add_column_if_missing(table: str, column: str, definition: str) -> None:
            if not column_exists(conn, table, column):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

        def copy_column_if_both_exist(table: str, target_column: str, source_column: str) -> None:
            if column_exists(conn, table, target_column) and column_exists(conn, table, source_column):
                conn.execute(
                    f"UPDATE {table} SET {target_column} = {source_column} "
                    f"WHERE {target_column} IS NULL"
                )

        add_column_if_missing("bills", "receipt_issue_date", "TEXT")
        add_column_if_missing("bills", "qr_signature", "TEXT")
        add_column_if_missing("bills", "qr_stale", "INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing("bills", "program_id", "TEXT")
        add_column_if_missing("bills", "enrollment_id", "TEXT")
        add_column_if_missing("bills", "month", "TEXT DEFAULT ''")
        add_column_if_missing("bills", "amount", "INTEGER DEFAULT 0")
        add_column_if_missing("bills", "billing_month", "TEXT")
        copy_column_if_both_exist("bills", "billing_month", "month")
        if column_exists(conn, "bills", "month") and column_exists(conn, "bills", "billing_month"):
            conn.execute(
                "UPDATE bills SET month = billing_month "
                "WHERE (month IS NULL OR month = '') AND billing_month IS NOT NULL"
            )

        add_column_if_missing("bills", "payment_status", "TEXT")
        copy_column_if_both_exist("bills", "payment_status", "status")

        add_column_if_missing("bills", "total_amount", "REAL DEFAULT 0")
        copy_column_if_both_exist("bills", "total_amount", "amount")
        if column_exists(conn, "bills", "amount") and column_exists(conn, "bills", "total_amount"):
            conn.execute(
                "UPDATE bills SET amount = total_amount "
                "WHERE (amount IS NULL OR amount = 0) AND total_amount IS NOT NULL"
            )

        add_column_if_missing("bills", "paid_amount", "REAL DEFAULT 0")
        if column_exists(conn, "bills", "paid_amount"):
            conn.execute("UPDATE bills SET paid_amount = 0 WHERE paid_amount IS NULL")

        add_column_if_missing("bills", "remaining_amount", "REAL DEFAULT 0")
        if (
            column_exists(conn, "bills", "remaining_amount")
            and column_exists(conn, "bills", "total_amount")
            and column_exists(conn, "bills", "paid_amount")
        ):
            conn.execute(
                "UPDATE bills SET remaining_amount = MAX(COALESCE(total_amount, 0) - COALESCE(paid_amount, 0), 0) "
                "WHERE remaining_amount IS NULL"
            )

        add_column_if_missing("bills", "grace_until_date", "TEXT")
        add_column_if_missing("bills", "last_payment_date", "TEXT")
        add_column_if_missing("bills", "qr_token", "TEXT")
        add_column_if_missing("bills", "qr_token_status", "TEXT DEFAULT 'active'")
        add_column_if_missing("bills", "qr_token_created_at", "TEXT")
        add_column_if_missing("bills", "qr_token_used_at", "TEXT")
        add_column_if_missing("bills", "qr_token_expires_at", "TEXT")
        bill_columns = table_columns("bills")
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_bills_qr_token
            ON bills(qr_token)
            WHERE qr_token IS NOT NULL
            """
        )
        if {"paid_amount", "total_amount", "remaining_amount", "payment_status", "last_payment_date", "payment_date", "status"}.issubset(bill_columns):
            conn.execute("UPDATE bills SET paid_amount = COALESCE(total_amount, 0), remaining_amount = 0, payment_status = '已付款', last_payment_date = COALESCE(last_payment_date, payment_date) WHERE status = 'Paid'")
        if {"payment_status", "status"}.issubset(bill_columns):
            conn.execute("UPDATE bills SET payment_status = '未付款' WHERE status = 'Unpaid' AND (payment_status IS NULL OR payment_status = 'Unpaid')")
            conn.execute("UPDATE bills SET payment_status = '待對帳確認' WHERE status = 'Pending Review' AND (payment_status IS NULL OR payment_status = 'Pending Review')")

        student_columns = table_columns("students")
        if "department" not in student_columns:
            conn.execute("ALTER TABLE students ADD COLUMN department TEXT NOT NULL DEFAULT '待確認'")
        conn.executemany(
            """
            INSERT INTO settings(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO NOTHING
            """,
            list(default_settings().items()),
        )
        _seed_default_programs(conn)


def _seed_default_programs(conn: sqlite3.Connection) -> None:
    programs = [
        ("PRG-KG-MONTHLY", "幼兒園月費", "幼兒園", 8500, "monthly", "active", "幼兒園月費或主要服務"),
        ("PRG-KG-REGISTRATION", "幼兒園註冊費", "幼兒園", 12000, "semester", "active", "幼兒園學期註冊費"),
        ("PRG-KG-MATERIALS", "幼兒園材料費", "材料", 1500, "semester", "active", "幼兒園材料費"),
        ("PRG-KG-TRANSPORT", "幼兒園交通費", "交通", 2000, "monthly", "active", "幼兒園交通服務"),
        ("PRG-KG-ACTIVITY", "幼兒園活動費", "幼兒園", 800, "one-time", "active", "幼兒園活動相關費用"),
        ("PRG-KG-OTHER", "幼兒園其他費用", "其他", 0, "one-time", "active", "幼兒園其他收費項目"),
        ("PRG-KG-SOCCER", "幼兒足球班", "幼兒園才藝", 0, "monthly", "active", "幼兒園加選才藝課程"),
        ("PRG-KG-POP-KEYBOARD", "流行音樂鍵盤班", "幼兒園才藝", 0, "monthly", "active", "幼兒園加選才藝課程"),
        ("PRG-KG-JOSH-STORYBOOK", "外師 Josh 繪本班", "幼兒園才藝", 0, "monthly", "active", "幼兒園加選才藝課程"),
        ("PRG-KG-BOARDGAME", "幼兒桌遊班", "幼兒園才藝", 0, "monthly", "active", "幼兒園加選才藝課程"),
        ("PRG-KG-DINNER", "幼兒晚餐費", "幼兒園延伸照顧", 0, "monthly", "active", "幼兒園延伸照顧加收服務"),
        ("PRG-KG-EXTENDED-CARE", "幼兒延托費", "幼兒園延伸照顧", 0, "monthly", "active", "幼兒園延伸照顧加收服務"),
        ("PRG-KG-TEMP-EXTENDED-CARE", "幼兒臨時延托費", "幼兒園延伸照顧", 0, "one-time", "active", "幼兒園臨時延伸照顧加收服務"),
        ("PRG-KG-OVERTIME-CARE", "幼兒加時照顧費", "幼兒園延伸照顧", 0, "one-time", "active", "幼兒園加時照顧服務"),
        ("PRG-AFTERSCHOOL", "一般安親班", "一般平日安親班", 8000, "monthly", "active", "平日課後照顧"),
        ("PRG-AFTERSCHOOL-SNACK", "安親點心費", "一般平日安親班", 800, "monthly", "active", "安親點心費"),
        ("PRG-AFTERSCHOOL-TRANSPORT", "安親交通費", "一般平日安親班", 2000, "monthly", "active", "安親交通服務"),
        ("PRG-AFTERSCHOOL-VACATION", "寒暑假安親", "一般平日安親班", 9000, "monthly", "active", "寒暑假安親服務"),
        ("PRG-AFTERSCHOOL-OTHER", "安親其他費用", "一般平日安親班", 0, "one-time", "active", "安親其他收費項目"),
        ("PRG-AFTERSCHOOL-ENGLISH", "安親兒童美語", "安親延伸課程", 3000, "monthly", "active", "安親學生加選兒童美語"),
        ("PRG-AFTERSCHOOL-ART", "安親美術班", "安親延伸課程", 2600, "monthly", "active", "安親學生加選美術"),
        ("PRG-AFTERSCHOOL-CALLIGRAPHY", "安親書法班", "安親延伸課程", 2500, "monthly", "active", "安親學生加選書法"),
        ("PRG-ENGLISH", "兒童美語", "兒童美語", 3000, "monthly", "active", "兒童美語課程"),
        ("PRG-ART", "美術班", "美術", 2600, "monthly", "active", "美術課程"),
        ("PRG-CALLIGRAPHY", "書法班", "書法", 2500, "monthly", "active", "書法課程"),
        ("PRG-MUSIC", "音樂班", "才藝班", 3000, "monthly", "active", "音樂課程"),
        ("PRG-WEEKEND-ART", "假日美術班", "假日才藝", 2800, "monthly", "active", "假日美術課程"),
        ("PRG-WEEKEND-TALENT", "假日才藝班", "假日才藝", 2800, "monthly", "active", "週末才藝課程"),
        ("PRG-TALENT-OTHER", "其他才藝課程", "才藝班", 0, "monthly", "active", "其他才藝課程"),
        ("PRG-TRANSPORT", "交通費", "交通", 2000, "monthly", "active", "一般交通費"),
        ("PRG-MATERIALS", "材料費", "材料", 1000, "semester", "active", "一般材料費"),
        ("PRG-ACTIVITY", "活動費", "其他", 800, "one-time", "active", "活動費"),
        ("PRG-ADJUSTMENT", "補收費用", "其他", 0, "one-time", "active", "補收或調整項目"),
        ("PRG-OTHER", "其他費用", "其他", 0, "one-time", "active", "其他收費項目"),
    ]
    conn.executemany(
        """
        INSERT INTO programs(program_id, program_name, program_category, default_fee_amount, billing_cycle, status, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(program_id) DO NOTHING
        """,
        programs,
    )
    conn.execute(
        """
        UPDATE programs
        SET default_fee_amount = 2500
        WHERE program_id = 'PRG-CALLIGRAPHY' AND default_fee_amount = 2400
        """
    )
    category_updates = [
        ("幼兒園才藝", "PRG-KG-SOCCER"),
        ("幼兒園才藝", "PRG-KG-POP-KEYBOARD"),
        ("幼兒園才藝", "PRG-KG-JOSH-STORYBOOK"),
        ("幼兒園才藝", "PRG-KG-BOARDGAME"),
        ("幼兒園延伸照顧", "PRG-KG-DINNER"),
        ("幼兒園延伸照顧", "PRG-KG-EXTENDED-CARE"),
        ("幼兒園延伸照顧", "PRG-KG-TEMP-EXTENDED-CARE"),
        ("幼兒園延伸照顧", "PRG-KG-OVERTIME-CARE"),
        ("一般平日安親班", "PRG-AFTERSCHOOL"),
        ("一般平日安親班", "PRG-AFTERSCHOOL-SNACK"),
        ("一般平日安親班", "PRG-AFTERSCHOOL-TRANSPORT"),
        ("一般平日安親班", "PRG-AFTERSCHOOL-VACATION"),
        ("一般平日安親班", "PRG-AFTERSCHOOL-OTHER"),
        ("安親延伸課程", "PRG-AFTERSCHOOL-ENGLISH"),
        ("安親延伸課程", "PRG-AFTERSCHOOL-ART"),
        ("安親延伸課程", "PRG-AFTERSCHOOL-CALLIGRAPHY"),
    ]
    conn.executemany(
        "UPDATE programs SET program_category = ? WHERE program_id = ?",
        category_updates,
    )


def read_df(query: str, params: tuple = ()) -> pd.DataFrame:
    with connect() as conn:
        return pd.read_sql_query(query, conn, params=params)


def get_setting(key: str, fallback: str = "") -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else fallback


def get_settings() -> dict[str, str]:
    defaults = default_settings()
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    values = {row["key"]: row["value"] for row in rows}
    return {**defaults, **values}


def save_settings(values: dict[str, str]) -> None:
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO settings(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            list(values.items()),
        )


def default_settings() -> dict[str, str]:
    return {
        "kindergarten_name": "晴禾幼兒園",
        "address": "台北市中正區和平路 100 號",
        "contact_phone": "02-1234-5678",
        "receipt_prefix": "RCP",
        "bank_account_text": "請匯款至園方官方帳戶：銀行 000，帳號 000-000-000000，戶名：晴禾幼兒園",
        "receipt_footer_text": "本收據供家長留存與園方對帳使用。",
        "responsible_person": "園方行政",
        "payment_page_base_url": "",
        "privacy_mode": "standard",
        "default_qr_token_valid_days": "14",
    }


def seed_sample_data() -> None:
    init_db()
    with connect() as conn:
        student_count = conn.execute("SELECT COUNT(*) AS count FROM students").fetchone()["count"]
        if student_count:
            return

        save_settings(default_settings())
        students = [
            ("S001", "林小安", "小熊班", "林媽媽", "parent1@example.com", "active"),
            ("S002", "陳語恩", "小熊班", "陳爸爸", "parent2@example.com", "active"),
            ("S003", "黃子晴", "海豚班", "黃媽媽", "0912-345-678", "active"),
            ("S004", "王柏宇", "海豚班", "王爸爸", "parent4@example.com", "active"),
            ("S005", "李芮希", "兔子班", "李媽媽", "0922-222-222", "inactive"),
        ]
        conn.executemany(
            """
            INSERT INTO students(student_id, student_name, class_name, parent_name, contact, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            students,
        )
        bills = [
            ("KG-202605-0001", "S001", "林小安", "小熊班", "林媽媽", "2026-05", "月費", 8500, "2026-05-10", "Unpaid", None, None, "範例帳單"),
            ("KG-202605-0002", "S002", "陳語恩", "小熊班", "陳爸爸", "2026-05", "月費", 8500, "2026-05-10", "Unpaid", None, None, ""),
            ("KG-202605-0003", "S003", "黃子晴", "海豚班", "黃媽媽", "2026-05", "月費", 8800, "2026-05-10", "Unpaid", None, None, ""),
            ("KG-202605-0004", "S004", "王柏宇", "海豚班", "王爸爸", "2026-05", "月費", 8800, "2026-05-10", "Paid", "2026-05-09", "RCP-202605-0004", "已完成付款"),
        ]
        conn.executemany(
            """
            INSERT INTO bills(
                bill_id, student_id, student_name, class_name, parent_name, month, fee_item, amount,
                due_date, status, payment_date, receipt_number, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            bills,
        )

    sample = pd.DataFrame(
        [
            {
                "transaction_date": "2026-05-11",
                "amount": 8500,
                "payer_name": "林媽媽",
                "payment_note": "KG-202605-0001 林小安 月費",
                "transaction_id": "T20260511001",
            },
            {
                "transaction_date": "2026-05-11",
                "amount": 8500,
                "payer_name": "陳爸爸",
                "payment_note": "陳語恩 月費",
                "transaction_id": "T20260511002",
            },
            {
                "transaction_date": "2026-05-11",
                "amount": 8800,
                "payer_name": "未知付款人",
                "payment_note": "月費",
                "transaction_id": "T20260511003",
            },
            {
                "transaction_date": "2026-05-12",
                "amount": 7600,
                "payer_name": "黃媽媽",
                "payment_note": "KG-202605-0003 金額輸入錯誤",
                "transaction_id": "T20260512001",
            },
        ]
    )
    ensure_dirs()
    sample.to_csv(SAMPLE_BANK_CSV, index=False, encoding="utf-8-sig")
