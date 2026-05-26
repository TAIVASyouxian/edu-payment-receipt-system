from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from database import connect, init_db, read_df
from services import PAID, PENDING, UNPAID


PAYMENT_UNPAID = "未付款"
PAYMENT_PAID = "已付款"
PAYMENT_PARTIAL = "部分付款"
PAYMENT_GRACE = "寬限期中"
PAYMENT_PROMISED = "已約定補繳日"
PAYMENT_PARENT_CONTACTED = "家長已聯繫園方"
PAYMENT_PENDING = "待對帳確認"
PAYMENT_AMOUNT_REVIEW = "金額需確認"
PAYMENT_OVERPAID = "溢付款需處理"
PAYMENT_PAUSE_REMINDER = "暫緩提醒"
PAYMENT_CANCELLED = "取消帳單"
PAYMENT_VOIDED = "作廢/更正"

ARRANGEMENT_STATUSES = [
    "無特殊安排",
    PAYMENT_GRACE,
    PAYMENT_PROMISED,
    PAYMENT_PARENT_CONTACTED,
    PAYMENT_PAUSE_REMINDER,
]


def payment_id() -> str:
    return f"PAY-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6].upper()}"


def arrangement_id() -> str:
    return f"ARR-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6].upper()}"


def imported_batch_id() -> str:
    return f"BATCH-{datetime.now().strftime('%Y%m%d%H%M%S')}"


def normalize_bill_amounts(bill_id: str) -> None:
    with connect() as conn:
        bill = conn.execute("SELECT amount, total_amount, paid_amount FROM bills WHERE bill_id = ?", (bill_id,)).fetchone()
        if not bill:
            return
        total = int(bill["total_amount"] if bill["total_amount"] is not None else bill["amount"])
        paid = int(bill["paid_amount"] or 0)
        remaining = max(total - paid, 0)
        conn.execute(
            """
            UPDATE bills
            SET total_amount = ?, paid_amount = ?, remaining_amount = ?
            WHERE bill_id = ?
            """,
            (total, paid, remaining, bill_id),
        )


def existing_payment_record(transaction_id: str) -> bool:
    if not transaction_id:
        return False
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM payment_records WHERE transaction_id = ?", (transaction_id,)).fetchone()
    return bool(row)


def payment_history(bill_id: str):
    return read_df("SELECT * FROM payment_records WHERE bill_id = ? ORDER BY transaction_date, created_at", (bill_id,))


def add_payment_record(
    bill_id: str,
    transaction_id: str,
    transaction_date: str,
    amount: int,
    payer_name: str,
    payment_note: str,
    match_status: str,
    batch_id: str,
    notes: str,
) -> str:
    pid = payment_id()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO payment_records(
                payment_id, bill_id, transaction_id, transaction_date, amount,
                payer_name, payment_note, match_status, imported_batch_id, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (pid, bill_id, transaction_id, transaction_date, int(amount), payer_name, payment_note, match_status, batch_id, notes),
        )
    return pid


def apply_payment_to_bill(bill_id: str, tx: dict, match_status: str, batch_id: str, notes: str) -> tuple[str, str]:
    init_db()
    transaction_id = str(tx.get("transaction_id") or "").strip()
    transaction_date = str(tx.get("transaction_date") or "").strip()
    amount = int(float(tx.get("amount") or 0))
    payer_name = str(tx.get("payer_name") or "").strip()
    payment_note = str(tx.get("payment_note") or "").strip()

    with connect() as conn:
        bill = conn.execute("SELECT * FROM bills WHERE bill_id = ?", (bill_id,)).fetchone()
        if not bill:
            return PENDING, "找不到帳單，需人工確認。"
        bill = dict(bill)
        total = int(bill.get("total_amount") or bill.get("amount") or 0)
        paid_before = int(bill.get("paid_amount") or 0)
        remaining_before = max(total - paid_before, 0)

    add_payment_record(bill_id, transaction_id, transaction_date, amount, payer_name, payment_note, match_status, batch_id, notes)
    paid_after = paid_before + amount
    remaining_after = max(total - paid_after, 0)

    if amount <= 0:
        status = PENDING
        payment_status = PAYMENT_AMOUNT_REVIEW
        message = "金額需確認，已記錄付款資料但不自動完成帳單。"
        event = "Payment amount review"
    elif paid_after < total:
        status = UNPAID
        payment_status = PAYMENT_PARTIAL
        message = f"已記錄部分付款，尚餘 NT$ {remaining_after:,}。"
        event = "Partial payment imported"
    elif paid_after == total:
        status = PAID
        payment_status = PAYMENT_PAID
        message = "款項已全額確認。"
        event = "Bill fully paid"
    else:
        status = PENDING
        payment_status = PAYMENT_OVERPAID
        message = f"付款累計超過帳單金額 NT$ {paid_after - total:,}，需人工確認。"
        event = "Overpayment detected"

    with connect() as conn:
        conn.execute(
            """
            UPDATE bills
            SET total_amount = ?, paid_amount = ?, remaining_amount = ?, status = ?,
                payment_status = ?, last_payment_date = ?, payment_date = CASE WHEN ? = ? THEN ? ELSE payment_date END,
                notes = COALESCE(notes, '') || ?
            WHERE bill_id = ?
            """,
            (
                total,
                paid_after,
                remaining_after,
                status,
                payment_status,
                transaction_date,
                status,
                PAID,
                transaction_date,
                f"\n付款紀錄：{message}",
                bill_id,
            ),
        )
    from safety_services import log_audit

    log_audit(event, "bill", bill_id, message)
    return status, message


def upsert_payment_arrangement(
    bill_id: str,
    arrangement_status: str,
    promised_payment_date: str | None,
    grace_until_date: str | None,
    arrangement_note: str,
    handled_by: str,
) -> str:
    aid = arrangement_id()
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        existing = conn.execute(
            "SELECT arrangement_id FROM payment_arrangements WHERE bill_id = ? ORDER BY created_at DESC LIMIT 1",
            (bill_id,),
        ).fetchone()
        if existing:
            aid = existing["arrangement_id"]
            conn.execute(
                """
                UPDATE payment_arrangements
                SET arrangement_status = ?, promised_payment_date = ?, grace_until_date = ?,
                    arrangement_note = ?, handled_by = ?, updated_at = ?
                WHERE arrangement_id = ?
                """,
                (arrangement_status, promised_payment_date, grace_until_date, arrangement_note, handled_by, now, aid),
            )
        else:
            conn.execute(
                """
                INSERT INTO payment_arrangements(
                    arrangement_id, bill_id, arrangement_status, promised_payment_date,
                    grace_until_date, arrangement_note, handled_by, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (aid, bill_id, arrangement_status, promised_payment_date, grace_until_date, arrangement_note, handled_by, now),
            )

        mapped_status = None if arrangement_status == "無特殊安排" else arrangement_status
        if mapped_status:
            conn.execute(
                "UPDATE bills SET payment_status = ?, grace_until_date = COALESCE(?, grace_until_date) WHERE bill_id = ? AND status != ?",
                (mapped_status, grace_until_date, bill_id, PAID),
            )
        elif grace_until_date:
            conn.execute(
                "UPDATE bills SET grace_until_date = ? WHERE bill_id = ? AND status != ?",
                (grace_until_date, bill_id, PAID),
            )

    from safety_services import log_audit

    log_audit("Payment arrangement edited", "bill", bill_id, f"{arrangement_status}: {arrangement_note}")
    if grace_until_date:
        log_audit("Grace period added", "bill", bill_id, f"Grace until {grace_until_date}.")
    if promised_payment_date:
        log_audit("Promised payment date added", "bill", bill_id, f"Promised payment date {promised_payment_date}.")
    return aid
