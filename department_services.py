from __future__ import annotations

from database import connect, read_df
from safety_services import CANCELLED, DEPARTMENT_UNKNOWN, UNPAID, generate_qr_for_bill, log_audit
from services import next_bill_id


def create_bills(month: str, fee_item: str, amount: int, due_date: str, scope: str, selected_value: str, notes: str) -> int:
    if scope == "Department":
        students = read_df(
            "SELECT * FROM students WHERE status = 'active' AND department = ? AND department != ?",
            (selected_value, DEPARTMENT_UNKNOWN),
        )
    elif selected_value:
        students = read_df(
            "SELECT * FROM students WHERE status = 'active' AND student_id = ? AND department != ?",
            (selected_value, DEPARTMENT_UNKNOWN),
        )
        if students.empty:
            students = read_df(
                "SELECT * FROM students WHERE status = 'active' AND class_name = ? AND department != ?",
                (selected_value, DEPARTMENT_UNKNOWN),
            )
    else:
        students = read_df(
            "SELECT * FROM students WHERE status = 'active' AND department != ?",
            (DEPARTMENT_UNKNOWN,),
        )

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
