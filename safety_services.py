from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
from pathlib import Path
import re
import secrets

import pandas as pd
import qrcode

from database import QR_DIR, connect, get_settings, init_db, read_df
from models import MatchResult
from services import (
    PAID,
    PENDING,
    UNPAID,
    generate_receipt_pdf as _base_generate_receipt_pdf,
    next_bill_id,
)
from payment_services import (
    PAYMENT_AMOUNT_REVIEW,
    PAYMENT_OVERPAID,
    PAYMENT_PAID,
    PAYMENT_PARTIAL,
    PAYMENT_PENDING,
    apply_payment_to_bill,
    existing_payment_record,
    imported_batch_id,
    normalize_bill_amounts,
)


CANCELLED = "Cancelled"
DUPLICATE = "Duplicate"
KINDERGARTEN = "幼兒園"
AFTER_SCHOOL = "安親班"
DEPARTMENT_UNKNOWN = "待確認"


def normalize_department(value: object) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None

    kindergarten_values = {"幼兒園", "幼童", "幼兒部", "kindergarten"}
    after_school_values = {"安親", "安親班", "課輔", "after-school", "afterschool", "after school"}
    if text in {item.lower() for item in kindergarten_values}:
        return KINDERGARTEN
    if text in {item.lower() for item in after_school_values}:
        return AFTER_SCHOOL
    if any(keyword in text for keyword in ["幼兒", "幼童", "kindergarten"]):
        return KINDERGARTEN
    if any(keyword in text for keyword in ["安親", "課輔", "after-school", "afterschool", "after school"]):
        return AFTER_SCHOOL
    return None


def classify_department(class_name: object = "", fee_item: object = "", provided_department: object = "") -> dict[str, str]:
    normalized = normalize_department(provided_department)
    if normalized:
        return {"department": normalized, "confidence": "高", "reason": "使用匯入資料提供的 department 欄位。"}

    class_text = str(class_name or "").strip()
    fee_text = str(fee_item or "").strip()
    kindergarten_keywords = ["幼幼", "小班", "中班", "大班", "兔子班", "小熊班", "海豚班", "幼兒", "幼童"]
    after_school_keywords = [
        "小一",
        "小二",
        "小三",
        "小四",
        "小五",
        "小六",
        "一年級",
        "二年級",
        "三年級",
        "四年級",
        "五年級",
        "六年級",
        "安親",
        "課輔",
    ]
    after_school_fee_keywords = ["安親費", "課輔費", "點心費", "寒暑假營隊費"]

    if any(keyword in class_text for keyword in kindergarten_keywords):
        return {"department": KINDERGARTEN, "confidence": "高", "reason": f"班級「{class_text}」符合幼兒園班級關鍵字。"}
    if any(keyword in class_text for keyword in after_school_keywords):
        return {"department": AFTER_SCHOOL, "confidence": "高", "reason": f"班級「{class_text}」符合安親班或國小年級關鍵字。"}
    if any(keyword in fee_text for keyword in after_school_fee_keywords):
        return {"department": AFTER_SCHOOL, "confidence": "中", "reason": f"收費項目「{fee_text}」符合安親班關鍵字。"}
    return {"department": DEPARTMENT_UNKNOWN, "confidence": "低", "reason": "班級或收費項目不足以判斷，需由行政人員確認。"}


def classify_existing_students() -> None:
    init_db()
    students = read_df("SELECT * FROM students WHERE department IS NULL OR department = '' OR department = ?", (DEPARTMENT_UNKNOWN,))
    updates: list[tuple[str, str]] = []
    for student in students.to_dict("records"):
        result = classify_department(class_name=student.get("class_name"))
        if result["confidence"] in ["高", "中"] and result["department"] != DEPARTMENT_UNKNOWN:
            updates.append((result["department"], student["student_id"]))
    if updates:
        with connect() as conn:
            conn.executemany("UPDATE students SET department = ? WHERE student_id = ?", updates)


def log_audit(event_type: str, entity_type: str | None, entity_id: str | None, message: str) -> None:
    try:
        with connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    entity_type TEXT,
                    entity_id TEXT,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                INSERT INTO audit_logs(event_type, entity_type, entity_id, message)
                VALUES (?, ?, ?, ?)
                """,
                (event_type, entity_type, entity_id, message),
            )
    except Exception:
        return


def _find_bill_id(note: str) -> str | None:
    match = re.search(r"KG-\d{6}-\d{4}", note or "")
    return match.group(0) if match else None


def bill_qr_signature(bill: dict) -> str:
    parts = [
        str(bill.get("bill_id") or ""),
        str(bill.get("student_id") or ""),
        str(bill.get("student_name") or ""),
        str(bill.get("class_name") or ""),
        str(bill.get("fee_item") or ""),
        str(int(float(bill.get("amount") or 0))),
        str(bill.get("due_date") or ""),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def utc_now_text() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def token_expiry_for_bill(bill: dict) -> str:
    due_date = str(bill.get("due_date") or "").strip()
    if due_date:
        try:
            return datetime.fromisoformat(due_date).replace(hour=23, minute=59, second=59).isoformat(timespec="seconds")
        except ValueError:
            pass
    settings = get_settings()
    try:
        days = int(settings.get("default_qr_token_valid_days") or 14)
    except ValueError:
        days = 14
    return (datetime.utcnow() + timedelta(days=max(days, 1))).isoformat(timespec="seconds")


def payment_base_url() -> str:
    settings = get_settings()
    base_url = str(settings.get("payment_page_base_url") or "").strip()
    return base_url.rstrip("/")


def parent_payment_url_for_token(token: str) -> str:
    base_url = payment_base_url()
    if not base_url:
        raise ValueError("請先到管理設定填寫付款頁基礎網址，再產生正式 QR Code。")
    return f"{base_url}/?page=parent&token={token}"


def ensure_qr_token(bill: dict, regenerate: bool = False) -> str:
    bill_id = str(bill.get("bill_id") or "").strip()
    if not bill_id:
        raise ValueError("QR token 必須綁定唯一 Bill ID。")
    current_token = str(bill.get("qr_token") or "").strip()
    current_status = str(bill.get("qr_token_status") or "active").strip()
    if current_token and current_status == "active" and not regenerate:
        return current_token

    token = secrets.token_urlsafe(32)
    now = utc_now_text()
    expires_at = token_expiry_for_bill(bill)
    with connect() as conn:
        while conn.execute("SELECT 1 FROM bills WHERE qr_token = ?", (token,)).fetchone():
            token = secrets.token_urlsafe(32)
        if current_token and regenerate:
            log_audit("QR token revoked", "bill", bill_id, f"Previous QR token revoked for bill {bill_id}.")
        conn.execute(
            """
            UPDATE bills
            SET qr_token = ?, qr_token_status = 'active', qr_token_created_at = ?,
                qr_token_used_at = NULL, qr_token_expires_at = ?
            WHERE bill_id = ?
            """,
            (token, now, expires_at, bill_id),
        )
    log_audit("QR token regenerated" if regenerate else "QR token created", "bill", bill_id, f"QR token active for bill {bill_id}.")
    return token


def mark_qr_token_used(bill_id: str) -> None:
    now = utc_now_text()
    with connect() as conn:
        bill = conn.execute("SELECT qr_token_status FROM bills WHERE bill_id = ?", (bill_id,)).fetchone()
        if not bill:
            return
        if bill["qr_token_status"] != "used":
            conn.execute(
                "UPDATE bills SET qr_token_status = 'used', qr_token_used_at = ? WHERE bill_id = ?",
                (now, bill_id),
            )
            log_audit("QR token used", "bill", bill_id, f"QR token used for bill {bill_id}.")


def expire_overdue_token(bill: dict) -> bool:
    expires_at = str(bill.get("qr_token_expires_at") or "").strip()
    status = str(bill.get("qr_token_status") or "active")
    if not expires_at or status != "active":
        return False
    try:
        expired = datetime.fromisoformat(expires_at) < datetime.utcnow()
    except ValueError:
        return False
    if expired:
        bill_id = str(bill.get("bill_id") or "")
        with connect() as conn:
            conn.execute("UPDATE bills SET qr_token_status = 'expired' WHERE bill_id = ?", (bill_id,))
        log_audit("QR token expired", "bill", bill_id, f"QR token expired for bill {bill_id}.")
        return True
    return False


def is_bill_stale(bill: dict) -> bool:
    if str(bill.get("status") or "") == CANCELLED:
        return True
    if int(bill.get("qr_stale") or 0) == 1:
        return True
    saved = str(bill.get("qr_signature") or "")
    return bool(saved and saved != bill_qr_signature(bill))


def bill_department(bill: dict) -> str:
    department = str(bill.get("department") or "").strip()
    if department:
        return department
    student_id = str(bill.get("student_id") or "").strip()
    if not student_id:
        return DEPARTMENT_UNKNOWN
    with connect() as conn:
        row = conn.execute("SELECT department FROM students WHERE student_id = ?", (student_id,)).fetchone()
    return str(row["department"]).strip() if row and row["department"] else DEPARTMENT_UNKNOWN


def is_student_department_unconfirmed(bill: dict) -> bool:
    return bill_department(bill) == DEPARTMENT_UNKNOWN


def department_unconfirmed_message() -> str:
    return "此學生部門尚未確認，請先完成學生資料確認後再處理帳單。"


def log_department_block(action: str, bill: dict) -> None:
    bill_id = str(bill.get("bill_id") or "")
    log_audit(
        "Department confirmation blocked",
        "bill",
        bill_id,
        f"{action} blocked for bill {bill_id}: student department is 待確認.",
    )


def generate_qr_for_bill(bill: dict) -> str:
    bill_id = str(bill.get("bill_id") or "").strip()
    if not bill_id:
        raise ValueError("QR Code 必須綁定唯一 Bill ID，不能產生通用 QR Code。")

    with connect() as conn:
        current = conn.execute("SELECT * FROM bills WHERE bill_id = ?", (bill_id,)).fetchone()
    if current:
        bill = dict(current)
    if is_student_department_unconfirmed(bill):
        log_department_block("QR generation", bill)
        raise ValueError(department_unconfirmed_message())

    signature = bill_qr_signature(bill)
    old_signature = str(bill.get("qr_signature") or "")
    was_stale = int(bill.get("qr_stale") or 0) == 1 or bool(old_signature and old_signature != signature)
    token = ensure_qr_token(bill, regenerate=was_stale)
    payload = parent_payment_url_for_token(token)
    if bill_id in payload or str(bill.get("student_name") or "") in payload or str(bill.get("amount") or "") in payload:
        raise ValueError("QR Code raw content 不可包含學生姓名、金額或 Bill ID。")

    QR_DIR.mkdir(exist_ok=True, parents=True)
    path = QR_DIR / f"{bill_id}.png"
    qrcode.make(payload).save(path)
    with connect() as conn:
        conn.execute(
            "UPDATE bills SET qr_path = ?, qr_signature = ?, qr_stale = 0 WHERE bill_id = ?",
            (str(path), signature, bill_id),
        )
    log_audit("QR regenerated" if was_stale else "QR generated", "bill", bill_id, f"QR generated for bill {bill_id}.")
    return str(path)


def regenerate_qr_for_bill(bill: dict) -> str:
    bill_id = str(bill.get("bill_id") or "").strip()
    if not bill_id:
        raise ValueError("QR Code 必須綁定唯一 Bill ID。")
    with connect() as conn:
        current = conn.execute("SELECT * FROM bills WHERE bill_id = ?", (bill_id,)).fetchone()
    if current:
        bill = dict(current)
    if is_student_department_unconfirmed(bill):
        log_department_block("QR regeneration", bill)
        raise ValueError(department_unconfirmed_message())
    token = ensure_qr_token(bill, regenerate=True)
    payload = parent_payment_url_for_token(token)
    QR_DIR.mkdir(exist_ok=True, parents=True)
    path = QR_DIR / f"{bill_id}.png"
    qrcode.make(payload).save(path)
    with connect() as conn:
        conn.execute(
            "UPDATE bills SET qr_path = ?, qr_signature = ?, qr_stale = 0 WHERE bill_id = ?",
            (str(path), bill_qr_signature(bill), bill_id),
        )
    log_audit("QR regenerated", "bill", bill_id, f"QR regenerated with a new token for bill {bill_id}.")
    return str(path)


def ensure_all_qr_codes() -> None:
    init_db()
    bills = read_df("SELECT * FROM bills")
    checked = 0
    repaired = 0
    for bill in bills.to_dict("records"):
        checked += 1
        if bill.get("status") == PAID:
            if bill.get("qr_token") and bill.get("qr_token_status") != "used":
                with connect() as conn:
                    conn.execute(
                        "UPDATE bills SET qr_token_status = 'used', qr_token_used_at = ? WHERE bill_id = ?",
                        (utc_now_text(), bill["bill_id"]),
                    )
                repaired += 1
            continue
        qr_path = bill.get("qr_path")
        missing = pd.isna(qr_path) or not qr_path or not Path(str(qr_path)).exists()
        stale = is_bill_stale(bill)
        if is_student_department_unconfirmed(bill):
            continue
        if stale:
            with connect() as conn:
                conn.execute("UPDATE bills SET qr_stale = 1 WHERE bill_id = ?", (bill["bill_id"],))
        if missing or stale:
            generate_qr_for_bill(bill)
            repaired += 1
    log_audit("QR token repair completed", "maintenance", None, f"QR token repair completed: {checked} bills checked, {repaired} repaired.")


def generate_receipt_pdf(bill_id: str) -> str:
    init_db()
    with connect() as conn:
        bill = conn.execute("SELECT * FROM bills WHERE bill_id = ?", (bill_id,)).fetchone()
    if not bill:
        raise ValueError(f"Bill not found: {bill_id}")
    bill = dict(bill)
    if bill["status"] != PAID:
        raise ValueError("只有已付款帳單可以產生收據。")
    normalize_bill_amounts(bill_id)
    with connect() as conn:
        fresh = conn.execute("SELECT total_amount, paid_amount, remaining_amount, payment_status FROM bills WHERE bill_id = ?", (bill_id,)).fetchone()
    if fresh and (int(fresh["paid_amount"] or 0) < int(fresh["total_amount"] or 0) or int(fresh["remaining_amount"] or 0) != 0):
        raise ValueError("只有全額付款確認後才能產生正式數位收據。")
    if fresh and fresh["payment_status"] in (PAYMENT_PARTIAL, PAYMENT_PENDING, PAYMENT_OVERPAID, PAYMENT_AMOUNT_REVIEW):
        raise ValueError("此帳單付款狀態仍需確認，不能產生正式數位收據。")
    if is_student_department_unconfirmed(bill):
        log_department_block("Receipt generation", bill)
        raise ValueError(department_unconfirmed_message())
    if is_bill_stale(bill):
        raise ValueError("已失效或已取消的帳單不能產生收據。")
    receipt_number_before = bill.get("receipt_number")
    path = _base_generate_receipt_pdf(bill_id)
    log_audit("Receipt regenerated" if receipt_number_before else "Receipt generated", "bill", bill_id, f"Receipt generated for bill {bill_id}.")
    return path


def mark_bill_paid(bill_id: str, payment_date: str, notes: str | None = None) -> None:
    with connect() as conn:
        bill = conn.execute("SELECT * FROM bills WHERE bill_id = ?", (bill_id,)).fetchone()
        if not bill:
            raise ValueError(f"Bill not found: {bill_id}")
        bill = dict(bill)
        if bill["status"] == PAID:
            log_audit("Duplicate transaction detected", "bill", bill_id, f"Duplicate payment warning for already paid bill {bill_id}.")
            return
        if is_student_department_unconfirmed(bill):
            log_department_block("Payment confirmation", bill)
            raise ValueError(department_unconfirmed_message())
        if bill["status"] in (PENDING, CANCELLED) or is_bill_stale(bill):
            raise ValueError("只有有效且尚未付款的帳單可由對帳自動標記為已付款。")
        conn.execute(
            """
            UPDATE bills
            SET status = ?, payment_status = ?, total_amount = COALESCE(total_amount, amount),
                paid_amount = COALESCE(total_amount, amount), remaining_amount = 0,
                payment_date = ?, last_payment_date = ?, notes = COALESCE(?, notes)
            WHERE bill_id = ?
            """,
            (PAID, PAYMENT_PAID, payment_date, payment_date, notes, bill_id),
        )
    log_audit("Payment auto matched", "bill", bill_id, f"Bill {bill_id} marked Paid on {payment_date}.")
    log_audit("Payment fully confirmed", "bill", bill_id, f"Bill {bill_id} fully confirmed on {payment_date}.")
    mark_qr_token_used(bill_id)
    generate_receipt_pdf(bill_id)


def create_bills(month: str, fee_item: str, amount: int, due_date: str, scope: str, selected_value: str, notes: str) -> int:
    if selected_value:
        students = read_df("SELECT * FROM students WHERE status = 'active' AND student_id = ?", (selected_value,))
        if students.empty:
            students = read_df("SELECT * FROM students WHERE status = 'active' AND class_name = ?", (selected_value,))
    elif scope == "單一學生":
        students = read_df("SELECT * FROM students WHERE status = 'active' AND student_id = ?", (selected_value,))
    elif scope == "單一班級":
        students = read_df("SELECT * FROM students WHERE status = 'active' AND class_name = ?", (selected_value,))
    else:
        students = read_df("SELECT * FROM students WHERE status = 'active'")

    created: list[tuple[str, str]] = []
    with connect() as conn:
        for student in students.to_dict("records"):
            exists = conn.execute(
                "SELECT 1 FROM bills WHERE student_id = ? AND month = ? AND fee_item = ? AND status != ?",
                (student["student_id"], month, fee_item, CANCELLED),
            ).fetchone()
            if exists:
                continue
            bill_id = next_bill_id(month)
            while conn.execute("SELECT 1 FROM bills WHERE bill_id = ?", (bill_id,)).fetchone():
                number = int(bill_id.split("-")[-1]) + 1
                bill_id = f"KG-{month.replace('-', '')}-{number:04d}"
            conn.execute(
                """
                INSERT INTO bills(
                    bill_id, student_id, student_name, class_name, parent_name, month, fee_item,
                    billing_month, amount, due_date, status, payment_status,
                    total_amount, paid_amount, remaining_amount, notes, qr_stale
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 1)
                """,
                (
                    bill_id,
                    student["student_id"],
                    student["student_name"],
                    student["class_name"],
                    student["parent_name"],
                    month,
                    fee_item,
                    month,
                    int(amount),
                    due_date,
                    UNPAID,
                    UNPAID,
                    int(amount),
                    int(amount),
                    notes,
                ),
            )
            created.append((bill_id, student["student_name"]))
    for bill_id, student_name in created:
        try:
            generate_qr_for_bill({"bill_id": bill_id})
        except Exception as exc:
            log_audit("QR generation skipped", "bill", bill_id, f"QR generation skipped for bill {bill_id}: {exc}")
        log_audit("Bill created", "bill", bill_id, f"Created bill {bill_id} for {student_name} amount {amount}.")
    return len(created)


def match_transaction(tx: dict) -> MatchResult:
    tx_id = str(tx.get("transaction_id") or "").strip()
    amount = int(float(tx.get("amount", 0)))
    note = str(tx.get("payment_note") or "").strip()
    payer_name = str(tx.get("payer_name") or "").strip()
    bill_id = _find_bill_id(note)
    bills = read_df("SELECT * FROM bills")

    if bill_id:
        matched = bills[bills["bill_id"] == bill_id]
        if len(matched) == 1:
            bill = matched.iloc[0].to_dict()
            if is_student_department_unconfirmed(bill):
                return MatchResult(tx_id, PENDING, "低", department_unconfirmed_message(), bill_id)
            if bill["status"] in (CANCELLED,) or is_bill_stale(bill):
                return MatchResult(tx_id, PENDING, "低", "帳單已取消或 QR 已失效，需人工確認。", bill_id)
            remaining = int(bill.get("remaining_amount") or bill.get("total_amount") or bill.get("amount") or 0) - int(bill.get("paid_amount") or 0 if bill.get("remaining_amount") is None else 0)
            if bill.get("remaining_amount") is not None:
                remaining = int(bill.get("remaining_amount") or 0)
            if amount == remaining:
                return MatchResult(tx_id, "Matched", "高", "Bill ID 與剩餘金額相符，將全額確認。", bill_id)
            if 0 < amount < remaining:
                return MatchResult(tx_id, "Matched", "高", "Bill ID 相符，將記錄為部分付款。", bill_id)
            if amount > remaining:
                return MatchResult(tx_id, "Matched", "中", "Bill ID 相符，但累計金額將超過帳單金額，需人工確認。", bill_id)
            return MatchResult(tx_id, PENDING, "低", "金額需確認。", bill_id)

    candidates = bills[(bills["status"] == UNPAID) & (bills["qr_stale"].fillna(0).astype(int) == 0)]
    name_matches = candidates[(candidates["amount"] == amount) & (candidates["student_name"].apply(lambda name: str(name) in note))]
    if len(name_matches) == 1:
        row = name_matches.iloc[0]
        return MatchResult(tx_id, "Matched", "中", "金額與學生姓名相符，已自動對帳；建議抽查。", row["bill_id"])
    if len(name_matches) > 1:
        return MatchResult(tx_id, PENDING, "低", "符合多張可能帳單，需人工確認。")

    payer_matches = candidates[(candidates["amount"] == amount) & (candidates["parent_name"].apply(lambda name: str(name) in payer_name or str(name) in note))]
    if len(payer_matches) == 1:
        row = payer_matches.iloc[0]
        return MatchResult(tx_id, PENDING, "中", "金額與家長姓名可能相符，需人工確認。", row["bill_id"])
    if len(candidates[candidates["amount"] == amount]) > 1:
        return MatchResult(tx_id, PENDING, "低", "只有金額相符且候選帳單多筆，需人工確認。")
    return MatchResult(tx_id, "Unmatched", "低", "找不到 Bill ID、學生姓名或金額相符的帳單。")


def import_and_reconcile(df: pd.DataFrame) -> pd.DataFrame:
    required = ["transaction_date", "amount", "payer_name", "payment_note", "transaction_id"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"CSV 缺少必要欄位：{', '.join(missing)}")
    log_audit("Bank CSV imported", "csv", None, f"Imported bank CSV with {len(df)} rows.")

    results = []
    batch_id = imported_batch_id()
    seen_transactions: set[str] = set()
    seen_bills: set[str] = set()
    for tx in df[required].to_dict("records"):
        tx_id = str(tx["transaction_id"]).strip()
        duplicate_warning = ""
        with connect() as conn:
            existing_tx = conn.execute("SELECT 1 FROM transactions WHERE transaction_id = ?", (tx_id,)).fetchone()

        if tx_id in seen_transactions or existing_tx or existing_payment_record(tx_id):
            duplicate_warning = "疑似重複付款：同一交易編號已匯入。"
        seen_transactions.add(tx_id)

        result = match_transaction(tx)
        if result.bill_id and result.bill_id in seen_bills:
            duplicate_warning = "疑似重複付款：同一帳單在本次匯入中重複出現。"
        if result.bill_id:
            seen_bills.add(result.bill_id)
        if result.status == DUPLICATE:
            duplicate_warning = result.message

        final_status = DUPLICATE if duplicate_warning else result.status
        warning = duplicate_warning or result.message
        if final_status == "Matched" and result.bill_id:
            bill_status, payment_message = apply_payment_to_bill(result.bill_id, tx, final_status, batch_id, result.message)
            warning = payment_message
            if bill_status == PAID:
                mark_qr_token_used(result.bill_id)
                log_audit("Payment fully confirmed", "bill", result.bill_id, f"Bill {result.bill_id} fully confirmed by reconciliation.")
                generate_receipt_pdf(result.bill_id)
                final_status = "Matched"
            elif bill_status == UNPAID:
                final_status = PAYMENT_PARTIAL
                log_audit("Partial payment recorded", "bill", result.bill_id, f"Partial payment recorded for bill {result.bill_id}.")
            elif bill_status == PENDING:
                final_status = PAYMENT_PENDING
        elif final_status == PENDING and result.bill_id:
            with connect() as conn:
                conn.execute(
                    "UPDATE bills SET status = ?, payment_status = ?, notes = COALESCE(notes, '') || ? WHERE bill_id = ? AND status != ?",
                    (PENDING, PAYMENT_PENDING, f"\n對帳待確認：{warning}", result.bill_id, PAID),
                )
            log_audit("Payment marked Pending Review", "bill", result.bill_id, warning)
        elif final_status == DUPLICATE:
            log_audit("Duplicate transaction detected", "transaction", tx_id, warning)
        elif final_status == "Unmatched":
            log_audit("Payment marked Pending Review", "transaction", tx_id, warning)

        with connect() as conn:
            conn.execute(
                """
                INSERT INTO transactions(
                    transaction_id, transaction_date, amount, payer_name, payment_note,
                    match_status, confidence, matched_bill_id, warning
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(transaction_id) DO UPDATE SET
                    match_status = excluded.match_status,
                    confidence = excluded.confidence,
                    matched_bill_id = excluded.matched_bill_id,
                    warning = excluded.warning
                """,
                (
                    tx_id,
                    str(tx["transaction_date"]).strip(),
                    int(float(tx["amount"])),
                    str(tx["payer_name"]).strip(),
                    str(tx["payment_note"]).strip(),
                    final_status,
                    result.confidence,
                    result.bill_id,
                    warning,
                ),
            )
        results.append({**tx, "match_status": final_status, "confidence": result.confidence, "matched_bill_id": result.bill_id or "", "warning": warning})
    return pd.DataFrame(results)
