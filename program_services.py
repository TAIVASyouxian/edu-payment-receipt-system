from __future__ import annotations

from database import connect, read_df
from safety_services import CANCELLED, DEPARTMENT_UNKNOWN, UNPAID, generate_qr_for_bill, log_audit
from services import next_bill_id


def _amount_or_default(custom_amount: object, default_amount: object) -> int:
    try:
        if custom_amount is not None and custom_amount == custom_amount and str(custom_amount).strip() != "":
            value = int(float(custom_amount))
            if value > 0:
                return value
    except (TypeError, ValueError):
        pass
    return int(float(default_amount or 0))


def next_enrollment_id() -> str:
    with connect() as conn:
        row = conn.execute(
            "SELECT enrollment_id FROM enrollments WHERE enrollment_id LIKE 'ENR-%' ORDER BY enrollment_id DESC LIMIT 1"
        ).fetchone()
    number = int(row["enrollment_id"].split("-")[-1]) + 1 if row else 1
    return f"ENR-{number:05d}"


def active_enrollments_query(where: str = "", params: tuple = ()):
    sql = """
        SELECT
            enrollments.*,
            students.student_name,
            students.class_name,
            students.parent_name,
            students.department,
            students.status AS student_status,
            programs.program_name,
            programs.program_category,
            programs.default_fee_amount,
            programs.billing_cycle,
            programs.status AS program_status
        FROM enrollments
        JOIN students ON students.student_id = enrollments.student_id
        JOIN programs ON programs.program_id = enrollments.program_id
        WHERE enrollments.enrollment_status = 'active'
          AND students.status = 'active'
          AND programs.status = 'active'
          AND students.department != ?
    """
    full_params = (DEPARTMENT_UNKNOWN,) + params
    if where:
        sql += f" AND {where}"
    sql += " ORDER BY programs.program_category, programs.program_name, students.class_name, students.student_name"
    return read_df(sql, full_params)


def select_enrollments(scope: str, selected_value: str, selected_ids: list[str] | None = None):
    if scope == "one_student":
        return active_enrollments_query("students.student_id = ?", (selected_value,))
    if scope == "one_class":
        return active_enrollments_query("students.class_name = ?", (selected_value,))
    if scope == "one_program":
        return active_enrollments_query("programs.program_id = ?", (selected_value,))
    if scope == "one_category":
        return active_enrollments_query("programs.program_category = ?", (selected_value,))
    if scope == "selected_enrollments":
        ids = selected_ids or []
        if not ids:
            return active_enrollments_query("1 = 0")
        placeholders = ",".join("?" for _ in ids)
        return active_enrollments_query(f"enrollments.enrollment_id IN ({placeholders})", tuple(ids))
    return active_enrollments_query()


def create_bills_from_enrollments(
    billing_month: str,
    due_date: str,
    scope: str,
    selected_value: str = "",
    selected_enrollment_ids: list[str] | None = None,
    notes: str = "",
) -> int:
    enrollments = select_enrollments(scope, selected_value, selected_enrollment_ids)
    created: list[tuple[str, str]] = []

    with connect() as conn:
        for enrollment in enrollments.to_dict("records"):
            amount = _amount_or_default(enrollment.get("custom_fee_amount"), enrollment.get("default_fee_amount"))
            fee_item = enrollment["program_name"]
            exists = conn.execute(
                """
                SELECT 1 FROM bills
                WHERE enrollment_id = ? AND month = ? AND status != ?
                """,
                (enrollment["enrollment_id"], billing_month, CANCELLED),
            ).fetchone()
            if exists:
                continue

            bill_id = next_bill_id(billing_month)
            while conn.execute("SELECT 1 FROM bills WHERE bill_id = ?", (bill_id,)).fetchone():
                number = int(bill_id.split("-")[-1]) + 1
                bill_id = f"KG-{billing_month.replace('-', '')}-{number:04d}"

            conn.execute(
                """
                INSERT INTO bills(
                    bill_id, student_id, program_id, enrollment_id, student_name, class_name,
                    parent_name, month, billing_month, fee_item, amount, due_date, status,
                    payment_status, total_amount, paid_amount, remaining_amount, notes, qr_stale
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 1)
                """,
                (
                    bill_id,
                    enrollment["student_id"],
                    enrollment["program_id"],
                    enrollment["enrollment_id"],
                    enrollment["student_name"],
                    enrollment["class_name"],
                    enrollment["parent_name"],
                    billing_month,
                    billing_month,
                    fee_item,
                    amount,
                    due_date,
                    UNPAID,
                    UNPAID,
                    amount,
                    amount,
                    notes,
                ),
            )
            created.append((bill_id, enrollment["student_name"], fee_item))

    for bill_id, student_name, fee_item in created:
        try:
            generate_qr_for_bill({"bill_id": bill_id})
        except Exception as exc:
            log_audit("QR generation skipped", "bill", bill_id, f"QR generation skipped for bill {bill_id}: {exc}")
        log_audit("Bill created", "bill", bill_id, f"Created program bill {bill_id} for {student_name} / {fee_item}.")
    return len(created)
