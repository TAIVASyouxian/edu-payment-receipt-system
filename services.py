from __future__ import annotations

import re
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from database import QR_DIR, RECEIPT_DIR, SAMPLE_BANK_CSV, connect, get_settings, init_db, read_df
from models import MatchResult


PAID = "Paid"
UNPAID = "Unpaid"
PENDING = "Pending Review"


def next_bill_id(month: str) -> str:
    compact = month.replace("-", "")
    prefix = f"KG-{compact}-"
    with connect() as conn:
        row = conn.execute(
            "SELECT bill_id FROM bills WHERE bill_id LIKE ? ORDER BY bill_id DESC LIMIT 1",
            (f"{prefix}%",),
        ).fetchone()
    number = int(row["bill_id"].split("-")[-1]) + 1 if row else 1
    return f"{prefix}{number:04d}"


def next_receipt_number(month: str | None = None) -> str:
    settings = get_settings()
    compact = (month or date.today().strftime("%Y-%m")).replace("-", "")
    prefix = f"{settings['receipt_prefix']}-{compact}-"
    with connect() as conn:
        row = conn.execute(
            "SELECT receipt_number FROM bills WHERE receipt_number LIKE ? ORDER BY receipt_number DESC LIMIT 1",
            (f"{prefix}%",),
        ).fetchone()
    number = int(row["receipt_number"].split("-")[-1]) + 1 if row and row["receipt_number"] else 1
    return f"{prefix}{number:04d}"


def payment_reference(bill: dict) -> str:
    return f"KINDERGARTEN_PAYMENT|bill_id={bill['bill_id']}"


def parent_payment_url(token: str) -> str:
    settings = get_settings()
    base_url = str(settings.get("payment_page_base_url") or "").strip().rstrip("/")
    if not base_url:
        raise ValueError("請先到管理設定填寫付款頁基礎網址，再產生正式 QR Code。")
    return f"{base_url}/?page=parent&token={token}"


def normalize_text(value: object) -> str:
    return "" if value is None else str(value).strip()


def mask_student_name(full_name: object) -> str:
    name = normalize_text(full_name)
    if not name:
        return "學生"
    if all("\u4e00" <= char <= "\u9fff" for char in name):
        if len(name) == 1:
            return f"{name}O"
        if len(name) == 2:
            return f"{name[0]}O"
        if len(name) == 3:
            return f"{name[0]}O{name[-1]}"
        return f"{name[:2]}O{name[-1]}"
    return f"{name[0]}{'*' * max(len(name) - 1, 4)}"


def parent_watermark(bill: dict, settings: dict | None = None) -> str:
    values = settings or get_settings()
    timestamp = pd.Timestamp.now().strftime("%Y/%m/%d %H:%M")
    department = bill.get("department") or bill.get("program_category") or ""
    course = bill.get("program_name") or bill.get("fee_item") or ""
    return "｜".join(
        [
            str(values.get("kindergarten_name") or ""),
            str(department or ""),
            str(bill.get("class_name") or ""),
            str(course or ""),
            mask_student_name(bill.get("student_name")),
            str(bill.get("bill_id") or ""),
            timestamp,
        ]
    )


def find_bill_id_in_note(note: str) -> str | None:
    match = re.search(r"KG-\d{6}-\d{4}", note or "")
    return match.group(0) if match else None


def log_audit(event_type: str, entity_type: str | None, entity_id: str | None, message: str) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO audit_logs(event_type, entity_type, entity_id, message)
            VALUES (?, ?, ?, ?)
            """,
            (event_type, entity_type, entity_id, message),
        )


def generate_qr_for_bill(bill: dict) -> str:
    bill_id = normalize_text(bill.get("bill_id"))
    if not bill_id:
        raise ValueError("QR Code 必須綁定唯一 Bill ID。")
    token = normalize_text(bill.get("qr_token"))
    if not token:
        token = secrets.token_urlsafe(32)
        expires_at = bill.get("due_date")
        if not expires_at:
            try:
                days = int(get_settings().get("default_qr_token_valid_days") or 14)
            except ValueError:
                days = 14
            expires_at = (datetime.utcnow() + timedelta(days=days)).isoformat(timespec="seconds")
        with connect() as conn:
            while conn.execute("SELECT 1 FROM bills WHERE qr_token = ?", (token,)).fetchone():
                token = secrets.token_urlsafe(32)
            conn.execute(
                """
                UPDATE bills
                SET qr_token = ?, qr_token_status = 'active', qr_token_created_at = ?,
                    qr_token_expires_at = ?
                WHERE bill_id = ?
                """,
                (token, datetime.utcnow().isoformat(timespec="seconds"), str(expires_at), bill_id),
            )
    payload = parent_payment_url(token)
    if bill_id in payload or normalize_text(bill.get("student_name")) in payload or normalize_text(bill.get("amount")) in payload:
        raise ValueError("QR Code raw content 不可包含學生姓名、金額或 Bill ID。")
    QR_DIR.mkdir(exist_ok=True, parents=True)
    path = QR_DIR / f"{bill_id}.png"
    qrcode.make(payload).save(path)
    with connect() as conn:
        conn.execute("UPDATE bills SET qr_path = ? WHERE bill_id = ?", (str(path), bill_id))
    return str(path)


def ensure_all_qr_codes() -> None:
    bills = read_df("SELECT * FROM bills")
    for bill in bills.to_dict("records"):
        qr_path = bill.get("qr_path")
        if pd.isna(qr_path) or not qr_path or not Path(str(qr_path)).exists():
            generate_qr_for_bill(bill)


def create_bills(month: str, fee_item: str, amount: int, due_date: str, scope: str, selected_value: str, notes: str) -> int:
    if scope == "單一學生":
        students = read_df("SELECT * FROM students WHERE status = 'active' AND student_id = ?", (selected_value,))
    elif scope == "單一班級":
        students = read_df("SELECT * FROM students WHERE status = 'active' AND class_name = ?", (selected_value,))
    else:
        students = read_df("SELECT * FROM students WHERE status = 'active'")

    created = 0
    with connect() as conn:
        for student in students.to_dict("records"):
            exists = conn.execute(
                """
                SELECT 1 FROM bills
                WHERE student_id = ? AND month = ? AND fee_item = ?
                """,
                (student["student_id"], month, fee_item),
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
                    total_amount, paid_amount, remaining_amount, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
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
            created += 1
    ensure_all_qr_codes()
    return created


def sample_bank_statement_path() -> str:
    return str(SAMPLE_BANK_CSV)


def register_pdf_font() -> str:
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("MSung-Light"))
        return "MSung-Light"
    except Exception:
        return "Helvetica"


def _draw_cell(c: canvas.Canvas, font: str, x: float, top: float, w: float, h: float, text: str, fill: str = "#FFFFFF", size: float = 10.2) -> None:
    c.setFillColor(colors.HexColor(fill))
    c.rect(x, top - h, w, h, fill=1, stroke=0)
    c.setStrokeColor(colors.HexColor("#CBD5E1"))
    c.rect(x, top - h, w, h, fill=0, stroke=1)
    c.setFillColor(colors.HexColor("#475569" if fill == "#F8FAFC" else "#111827"))
    c.setFont(font, size)
    c.drawString(x + 3 * mm, top - 7 * mm, str(text or ""))


def generate_receipt_pdf(bill_id: str) -> str:
    receipt_issue_date = date.today().isoformat()
    with connect() as conn:
        bill = conn.execute(
            """
            SELECT bills.*, COALESCE(students.department, '') AS department,
                   programs.program_name, programs.program_category
            FROM bills
            LEFT JOIN students ON bills.student_id = students.student_id
            LEFT JOIN programs ON bills.program_id = programs.program_id
            WHERE bills.bill_id = ?
            """,
            (bill_id,),
        ).fetchone()
        if not bill:
            raise ValueError(f"Bill not found: {bill_id}")
        bill = dict(bill)
        if bill["status"] != PAID:
            raise ValueError("Receipt can only be generated after the bill is marked Paid.")
        if not bill["receipt_number"]:
            receipt_number = next_receipt_number(bill["month"])
            conn.execute(
                "UPDATE bills SET receipt_number = ?, receipt_issue_date = ? WHERE bill_id = ?",
                (receipt_number, receipt_issue_date, bill_id),
            )
            bill["receipt_number"] = receipt_number
        else:
            conn.execute(
                "UPDATE bills SET receipt_issue_date = ? WHERE bill_id = ?",
                (receipt_issue_date, bill_id),
            )
        bill["receipt_issue_date"] = receipt_issue_date

    settings = get_settings()
    watermark = parent_watermark(bill, settings)
    RECEIPT_DIR.mkdir(exist_ok=True, parents=True)
    path = RECEIPT_DIR / f"{bill['receipt_number']}.pdf"
    font = register_pdf_font()
    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    margin = 22 * mm
    table_w = width - margin * 2
    label_w = 34 * mm
    value_w = (table_w - label_w * 2) / 2
    row_h = 11 * mm
    payment_method = "園方官方帳戶轉帳/匯款"

    c.setTitle(f"{settings['kindergarten_name']} {bill['receipt_number']}")
    c.setFillColor(colors.HexColor("#F8FAFC"))
    c.rect(0, 0, width, height, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.roundRect(margin, 18 * mm, table_w, height - 36 * mm, 4 * mm, fill=1, stroke=0)

    logo_w, logo_h = 34 * mm, 18 * mm
    logo_x = (width - logo_w) / 2
    logo_y = height - 38 * mm
    c.setStrokeColor(colors.HexColor("#94A3B8"))
    c.setDash(3, 3)
    c.rect(logo_x, logo_y, logo_w, logo_h, fill=0, stroke=1)
    c.setDash()
    c.setFillColor(colors.HexColor("#64748B"))
    c.setFont(font, 9)
    c.drawCentredString(width / 2, logo_y + 7 * mm, "Logo 預留")

    c.setFillColor(colors.HexColor("#111827"))
    c.setFont(font, 20)
    c.drawCentredString(width / 2, height - 48 * mm, settings["kindergarten_name"])
    c.setFont(font, 15)
    c.drawCentredString(width / 2, height - 58 * mm, "數位收據")
    c.setFont(font, 9)
    c.setFillColor(colors.HexColor("#475569"))
    c.drawCentredString(width / 2, height - 66 * mm, f"{settings['address']}  |  電話：{settings['contact_phone']}")

    c.setStrokeColor(colors.HexColor("#334155"))
    c.setLineWidth(1.2)
    c.line(margin + 8 * mm, height - 73 * mm, width - margin - 8 * mm, height - 73 * mm)

    table_x = margin + 8 * mm
    y = height - 86 * mm

    def row(top: float, left_label: str, left_value: str, right_label: str, right_value: str) -> float:
        _draw_cell(c, font, table_x, top, label_w, row_h, left_label, "#F8FAFC")
        _draw_cell(c, font, table_x + label_w, top, value_w, row_h, left_value)
        _draw_cell(c, font, table_x + label_w + value_w, top, label_w, row_h, right_label, "#F8FAFC")
        _draw_cell(c, font, table_x + label_w * 2 + value_w, top, value_w, row_h, right_value)
        return top - row_h

    y = row(y, "收據號碼", bill["receipt_number"], "帳單編號", bill["bill_id"])
    y = row(y, "收據開立日期", receipt_issue_date, "付款日期", bill["payment_date"] or "")
    y = row(y, "部門", bill.get("department") or "", "班級", bill["class_name"])
    y = row(y, "課程", bill.get("program_name") or bill["fee_item"], "學生姓名", mask_student_name(bill["student_name"]))
    y = row(y, "收費項目", bill["fee_item"], "金額", f"NT$ {int(bill['amount']):,}")
    y = row(y, "付款方式", payment_method, "繳費期限", bill["due_date"])

    notes_h = 22 * mm
    _draw_cell(c, font, table_x, y, label_w, notes_h, "備註", "#F8FAFC")
    _draw_cell(c, font, table_x + label_w, y, table_w - 16 * mm - label_w, notes_h, "")
    y -= notes_h + 12 * mm

    seal_size = 28 * mm
    seal_x = width - margin - 8 * mm - seal_size
    seal_y = y - seal_size + 4 * mm
    c.setStrokeColor(colors.HexColor("#B91C1C"))
    c.setLineWidth(1)
    c.rect(seal_x, seal_y, seal_size, seal_size, fill=0, stroke=1)
    c.setFillColor(colors.HexColor("#B91C1C"))
    c.setFont(font, 14)
    c.drawCentredString(seal_x + seal_size / 2, seal_y + seal_size / 2 - 2 * mm, "園方章")

    note_y = 48 * mm
    c.setStrokeColor(colors.HexColor("#CBD5E1"))
    c.setFillColor(colors.HexColor("#F8FAFC"))
    c.roundRect(margin + 8 * mm, note_y, table_w - 16 * mm, 32 * mm, 3 * mm, fill=1, stroke=1)
    c.setFillColor(colors.HexColor("#111827"))
    c.setFont(font, 10.5)
    c.drawString(margin + 13 * mm, note_y + 22 * mm, "本收據為園方繳費紀錄憑證，非統一發票。")
    c.setFillColor(colors.HexColor("#475569"))
    c.setFont(font, 9)
    c.drawString(margin + 13 * mm, note_y + 14 * mm, settings["receipt_footer_text"])
    c.drawString(margin + 13 * mm, note_y + 7 * mm, "本系統僅協助園方記錄付款與產生收據，款項均直接進入園方官方帳戶。")

    c.setFillColor(colors.HexColor("#111827"))
    c.setFont(font, 10)
    c.drawRightString(width - margin - 8 * mm, 31 * mm, f"經手人：{settings['responsible_person']}")
    c.setFillColor(colors.HexColor("#94A3B8"))
    c.setFont(font, 7)
    c.drawCentredString(width / 2, 12 * mm, watermark)
    c.save()

    with connect() as conn:
        conn.execute(
            "UPDATE bills SET receipt_path = ?, receipt_issue_date = ? WHERE bill_id = ?",
            (str(path), receipt_issue_date, bill_id),
        )
    return str(path)


def mark_bill_paid(bill_id: str, payment_date: str, notes: str | None = None) -> None:
    with connect() as conn:
        bill = conn.execute("SELECT status FROM bills WHERE bill_id = ?", (bill_id,)).fetchone()
        if not bill:
            raise ValueError(f"Bill not found: {bill_id}")
        if bill["status"] == PAID:
            log_audit("Pending review", "bill", bill_id, f"Duplicate payment warning for already paid bill {bill_id}.")
            return
        conn.execute(
            """
            UPDATE bills
            SET status = ?, payment_status = ?, payment_date = ?, notes = COALESCE(?, notes)
            WHERE bill_id = ?
            """,
            (PAID, PAID, payment_date, notes, bill_id),
        )
    log_audit("Auto matched payment", "bill", bill_id, f"Bill {bill_id} marked Paid on {payment_date}.")
    generate_receipt_pdf(bill_id)


def match_transaction(tx: dict) -> MatchResult:
    tx_id = normalize_text(tx.get("transaction_id"))
    amount = int(float(tx.get("amount", 0)))
    note = normalize_text(tx.get("payment_note"))
    payer_name = normalize_text(tx.get("payer_name"))
    bill_id = find_bill_id_in_note(note)
    bills = read_df("SELECT * FROM bills")

    if bill_id:
        bill_matches = bills[bills["bill_id"] == bill_id]
        if len(bill_matches) == 1:
            bill = bill_matches.iloc[0]
            if bill["status"] == PAID:
                return MatchResult(tx_id, PENDING, "低", "疑似重複付款：此帳單已付款。", bill_id)
            if int(bill["amount"]) == amount:
                return MatchResult(tx_id, "Matched", "高", "Bill ID 與金額相符。", bill_id)
            return MatchResult(tx_id, PENDING, "中", "Bill ID 存在但金額不一致，需人工確認。", bill_id)

    candidates = bills[bills["status"] == UNPAID]
    name_matches = candidates[(candidates["amount"] == amount) & (candidates["student_name"].apply(lambda name: str(name) in note))]
    if len(name_matches) == 1:
        row = name_matches.iloc[0]
        return MatchResult(tx_id, "Matched", "中", "金額與學生姓名相符；建議抽查。", row["bill_id"])
    if len(name_matches) > 1:
        return MatchResult(tx_id, PENDING, "低", "符合多張可能帳單，需人工確認。")

    payer_matches = candidates[(candidates["amount"] == amount) & (candidates["parent_name"].apply(lambda name: str(name) in payer_name or str(name) in note))]
    if len(payer_matches) == 1:
        row = payer_matches.iloc[0]
        return MatchResult(tx_id, PENDING, "中", "金額與家長姓名可能相符，需人工確認。", row["bill_id"])
    return MatchResult(tx_id, "Unmatched", "低", "找不到可自動對應的帳單。")


def import_and_reconcile(df: pd.DataFrame) -> pd.DataFrame:
    required = ["transaction_date", "amount", "payer_name", "payment_note", "transaction_id"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"CSV 缺少必要欄位：{', '.join(missing)}")

    results = []
    for tx in df[required].to_dict("records"):
        result = match_transaction(tx)
        if result.status == "Matched" and result.bill_id:
            mark_bill_paid(result.bill_id, str(tx["transaction_date"]).strip(), result.message)
        elif result.status == PENDING and result.bill_id:
            with connect() as conn:
                conn.execute(
                    "UPDATE bills SET status = ?, payment_status = ?, notes = COALESCE(notes, '') || ? WHERE bill_id = ? AND status != ?",
                    (PENDING, PENDING, f"\n對帳待確認：{result.message}", result.bill_id, PAID),
                )

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
                    normalize_text(tx["transaction_id"]),
                    normalize_text(tx["transaction_date"]),
                    int(float(tx["amount"])),
                    normalize_text(tx["payer_name"]),
                    normalize_text(tx["payment_note"]),
                    result.status,
                    result.confidence,
                    result.bill_id,
                    result.message,
                ),
            )
        results.append({**tx, "match_status": result.status, "confidence": result.confidence, "matched_bill_id": result.bill_id or "", "warning": result.message})
    return pd.DataFrame(results)
