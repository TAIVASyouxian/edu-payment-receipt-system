# Current Stable Version Backup

Date: 2026-05-26  
Project: Kindergarten QR Payment & Digital Receipt System

## 1. Current Working Features

- Streamlit local prototype for payment record tracking and digital receipt generation.
- Student management with:
  - Student ID
  - Student name
  - Class name
  - Parent name
  - Contact
  - Status
  - V1 department confirmation field
- V1 department handling:
  - 幼兒園
  - 安親班
  - 待確認
- CSV student import with department auto-classification preview.
- Bill creation by:
  - All confirmed students
  - Department
  - Class
  - Single student
- Unique Bill ID per bill.
- QR Code generation tied to one bill and one Bill ID.
- Parent payment mockup page for bill confirmation.
- Bank/payment CSV import and reconciliation.
- Reconciliation safety:
  - Bill ID + matching amount can mark bill as Paid.
  - Bill ID + amount mismatch becomes Pending Review.
  - Duplicate transaction IDs are detected.
  - Already-paid bills are not processed again as new payments.
  - Amount-only matching does not automatically mark Paid.
- Digital receipt generation only for Paid bills.
- Receipt issue date, payment date, and due date are stored/displayed separately.
- Admin settings for:
  - Kindergarten name
  - Address
  - Phone
  - Receipt prefix
  - Bank/payment account display text
  - Receipt footer text
  - Responsible person
- Audit log for key actions.
- Backup/export support.
- Legacy safety for students whose department is still 待確認:
  - Batch bill creation excludes them.
  - Existing bills show a warning.
  - QR regeneration is blocked.
  - Receipt generation is blocked.
  - Blocked actions are recorded in audit log.

## 2. Known Limitations

- This is a local V1 prototype, not a production deployment.
- This is not an e-invoice system.
- No real bank API, payment API, webhook, credit card processing, or parent login is implemented.
- The current model still uses a single V1 `department` field.
- The multi-program/enrollment model is not implemented yet.
- Partial payments, grace periods, promised payment dates, overpayments, and refund/apply-to-next-month handling are not implemented yet.
- Bank CSV import currently expects the prototype sample CSV format.
- Real bank column mapping and flexible date/amount parsing are not implemented yet.
- Existing SQLite data may include development/test records, generated QR images, and generated receipt samples.
- Some old records may retain historical values from previous test runs.
- Streamlit may show `use_container_width` deprecation warnings; these do not currently block the app.

## 3. Current Database Schema

SQLite database path:

```text
data/kindergarten.db
```

### students

| Column | Type | Notes |
|---|---|---|
| student_id | TEXT | Primary key |
| student_name | TEXT | Required |
| class_name | TEXT | Required |
| parent_name | TEXT | Required |
| contact | TEXT | Optional |
| status | TEXT | Default: active |
| created_at | TEXT | Default: CURRENT_TIMESTAMP |
| department | TEXT | Default: 待確認 |

### bills

| Column | Type | Notes |
|---|---|---|
| bill_id | TEXT | Primary key |
| student_id | TEXT | Required |
| student_name | TEXT | Required snapshot |
| class_name | TEXT | Required snapshot |
| parent_name | TEXT | Required snapshot |
| month | TEXT | Billing month |
| fee_item | TEXT | Fee item |
| amount | INTEGER | Bill amount |
| due_date | TEXT | Payment deadline |
| status | TEXT | Default: Unpaid |
| payment_date | TEXT | Actual payment date after reconciliation/admin confirmation |
| receipt_number | TEXT | Receipt number |
| notes | TEXT | Admin notes |
| qr_path | TEXT | Internal local QR path, hidden from user UI |
| receipt_path | TEXT | Internal local receipt path, hidden from user UI |
| created_at | TEXT | Default: CURRENT_TIMESTAMP |
| receipt_issue_date | TEXT | Date receipt PDF was generated/regenerated |
| qr_signature | TEXT | Used to detect stale QR data |
| qr_stale | INTEGER | 0/1 flag |

### transactions

| Column | Type | Notes |
|---|---|---|
| transaction_id | TEXT | Primary key |
| transaction_date | TEXT | Required |
| amount | INTEGER | Required |
| payer_name | TEXT | Optional |
| payment_note | TEXT | Optional |
| match_status | TEXT | Required |
| confidence | TEXT | Match confidence |
| matched_bill_id | TEXT | Matched bill if any |
| warning | TEXT | Reconciliation message |
| imported_at | TEXT | Default: CURRENT_TIMESTAMP |

### settings

| Column | Type | Notes |
|---|---|---|
| key | TEXT | Primary key |
| value | TEXT | Required |

### audit_logs

| Column | Type | Notes |
|---|---|---|
| id | INTEGER | Primary key |
| event_type | TEXT | Required |
| entity_type | TEXT | Optional |
| entity_id | TEXT | Optional |
| message | TEXT | Required |
| created_at | TEXT | Default: CURRENT_TIMESTAMP |

### sqlite_sequence

Internal SQLite table used for autoincrement values.

## 4. How To Run The App

Install dependencies:

```powershell
pip install -r requirements.txt
```

Run with Streamlit:

```powershell
streamlit run app.py --server.port 8501
```

Or run:

```powershell
.\run_app.ps1
```

Open:

```text
http://127.0.0.1:8501
```

## 5. How To Test The Core Flow

1. Open the app and confirm the dashboard loads.
2. Go to Student Management.
3. Add or edit a student.
4. Confirm the student department is not 待確認 before creating a bill.
5. Go to Bill Management.
6. Create a bill for a confirmed student, class, or department.
7. Confirm the bill has a unique Bill ID.
8. Confirm the QR Code is generated and tied to that Bill ID.
9. Open the parent payment mockup page and verify:
   - Student name
   - Class
   - Fee item
   - Amount
   - Due date
   - Bill ID
   - Payment reference text
10. Go to CSV Import and upload the sample bank statement CSV.
11. Confirm exact Bill ID + amount matches can mark bills Paid.
12. Confirm amount mismatches become Pending Review.
13. Confirm duplicate transactions are detected.
14. Go to Digital Receipts.
15. Confirm only Paid bills can generate PDF receipts.
16. Confirm unpaid, pending review, cancelled, stale, or 待確認 student bills cannot generate receipts.
17. Review Audit Log after bill creation, reconciliation, blocked actions, and receipt generation.

## 6. Pending Backlog

Do not implement these all at once. Recommended order:

1. Real bank CSV adaptation:
   - Column mapping
   - Different date formats
   - Different amount formats

2. Nordic-style, family-friendly wording pass:
   - Avoid debt-collection tone
   - Use calm, respectful payment wording
   - Emphasize clarity, dignity, and communication

3. Multi-program model:
   - `programs` table
   - `enrollments` table
   - Program/service item management
   - One student can enroll in multiple programs
   - Bill creation by program, category, enrollment, student, or class

4. Program examples:
   - 幼兒園部
   - 一般平日安親班
   - 安親兒童美語
   - 只上兒童美語但沒有參加安親
   - 假日才藝班
   - 安親美術班
   - 書法班

5. Flexible payment handling:
   - `payment_records` table
   - Partial payment
   - Paid amount
   - Remaining amount
   - Grace period
   - Promised payment date
   - Parent contacted kindergarten
   - Temporary reminder pause
   - Overpayment handling
   - Refund or apply-to-next-month note

6. Human-centered payment statuses:
   - 未付款
   - 已付款
   - 部分付款
   - 寬限期中
   - 已約定補繳日
   - 家長已聯繫園方
   - 待對帳確認
   - 金額需確認
   - 溢付款需處理
   - 暫緩提醒
   - 取消帳單
   - 作廢/更正

7. Dashboard and parent page enhancements:
   - 今日待處理事項
   - 待確認繳費
   - 收據產生狀態
   - 家長溝通備註
   - 寬限期中
   - 已約定補繳
   - 部分付款

8. Migration from department confirmation to program/enrollment confirmation.

## Responsibility Notice

This system is for payment tracking and digital receipt generation only.
All payments go directly to the kindergarten official account.
The system developer does not receive, store, manage, or process money.
Accounting, tax, refund, and legal receipt content must be confirmed by the kindergarten and its accountant.
V1 does not connect to real banking or payment APIs.
