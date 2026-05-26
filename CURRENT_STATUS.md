# Current Project Status

Date: 2026-05-26  
Project: Kindergarten QR Payment & Digital Receipt System

## 1. Current Working Features

- Streamlit prototype for kindergarten / after-school payment records, QR bill confirmation, CSV reconciliation, and digital receipt generation.
- Student management with department confirmation support.
- Program / course management with user-friendly Traditional Chinese dropdowns:
  - 幼兒園
  - 安親班
  - 才藝班
  - 其他
- Common course/service templates with auto-filled Program ID, category, default fee, and billing cycle.
- Custom course/service item creation for non-standard fees.
- Program editing and disabling. Disabled programs remain available to old bills/enrollments but are not used for new active selections.
- Enrollment management:
  - One student can enroll in multiple programs.
  - Enrollment can use the default program fee or a custom fee override.
- Bill creation by:
  - All active enrollments
  - Department
  - Course/service item
  - Program category
  - Class
  - Single student
  - Selected enrollments
- Multiple bills per student per month are supported when the student has multiple active enrollments.
- Unique Bill ID per bill.
- QR Code generation tied to one bill and one Bill ID.
- Parent payment mockup page for bill confirmation.
- Bank/payment CSV import and reconciliation.
- Reconciliation safety:
  - Bill ID + matching amount can mark payment as matched.
  - Partial payment can be recorded and accumulated.
  - Duplicate transaction IDs are detected and not posted twice.
  - Already-paid bills are not processed again as a new payment.
  - Amount-only matching does not automatically mark a bill paid.
- Payment records table for imported/recorded payments.
- Payment arrangements table for grace period and promised payment date notes.
- Digital receipt generation only after the bill is fully paid.
- Receipt issue date, payment date, and due date are stored/displayed separately.
- Admin settings for kindergarten name, address, phone, receipt prefix, bank/payment display text, receipt footer text, and responsible person.
- Audit log for key actions, including program changes, enrollments, payment imports, arrangements, QR generation, and receipts.
- Backup/export support.
- Responsibility notice is preserved:
  - This is not an e-invoice system.
  - All payments go directly to the kindergarten official account.
  - The developer does not receive, store, manage, or process money.

## 2. Latest Changelog

- Improved Program / Course UI for non-technical admin users.
- Added department-first course selection:
  - 幼兒園
  - 安親班
  - 才藝班
  - 其他
- Course dropdowns are filtered by selected department.
- Added common course templates such as:
  - 幼兒園月費
  - 一般安親班
  - 安親兒童美語
  - 兒童美語
  - 書法班
  - 交通費
  - 材料費
- Common courses auto-fill:
  - Program ID
  - Program category
  - Default fee amount
  - Billing cycle
- Billing cycle now displays in Traditional Chinese:
  - monthly -> 月繳
  - one-time -> 單次
  - semester -> 學期
  - per-class -> 依堂數
- Added optional custom course/service item path.
- Added quick edit area for existing courses.
- Enrollment flow now uses department -> filtered course/service item.
- Bill creation flow now supports department-filtered course and enrollment selections.
- Added audit log events:
  - Program created
  - Program edited
  - Program disabled
  - Enrollment created
  - Custom fee override used

## 3. Modified Files

- `app.py`
  - Added course templates, billing cycle labels, department filters, improved program UI, improved enrollment flow, and department-filtered bill creation.
- `database.py`
  - Updated default program/course seed data for common kindergarten, after-school, talent, and other service items.
  - Added a small migration correction for the default 書法班 fee.
- `CURRENT_STATUS.md`
  - Updated to reflect the current working version and latest changes.

## 4. Database / Schema Changes

No new tables were added in the latest UI change.

Existing relevant tables:

### programs

| Column | Purpose |
|---|---|
| program_id | Program/course/service ID |
| program_name | Program/course/service name |
| program_category | Category such as 幼兒園, 安親班, 兒童美語, 美術 |
| default_fee_amount | Default fee amount |
| billing_cycle | Stored internally as monthly, one-time, semester, or per-class |
| status | active / inactive |
| notes | Admin notes |

### enrollments

| Column | Purpose |
|---|---|
| enrollment_id | Enrollment ID |
| student_id | Linked student |
| program_id | Linked program/course/service item |
| start_date | Enrollment start date |
| end_date | Optional end date |
| enrollment_status | active / paused / ended |
| custom_fee_amount | Optional custom fee override |
| notes | Admin notes |

### bills

Relevant program/payment fields include:

| Column | Purpose |
|---|---|
| program_id | Linked program/course/service item |
| enrollment_id | Linked enrollment if created from enrollment |
| billing_month | Program bill month |
| total_amount | Total bill amount |
| paid_amount | Cumulative paid amount |
| remaining_amount | Remaining amount |
| grace_until_date | Optional grace period date |
| last_payment_date | Last payment date |
| payment_status | Human-readable payment status |

### payment_records

Stores imported or recorded payment entries.

### payment_arrangements

Stores grace period, promised payment date, and arrangement notes.

### audit_logs

Stores system action records.

## 5. Behavior Changed

- Admin users no longer need to manually type Program ID for common courses.
- Admin users select department first, then see only relevant course/service items.
- Billing cycle is displayed in Traditional Chinese while internal stored values remain English.
- Course setup is easier for standard use cases but still allows advanced/custom courses.
- Enrollment and bill creation now use the same department/course filtering pattern.
- Existing QR, reconciliation, receipt, payment safety, admin settings, and audit behavior remain unchanged.

## 6. How To Run The App

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

## 7. How To Test The Latest Course UI

1. Open the app.
2. Go to `課程與收費項目管理`.
3. Select department `幼兒園`.
4. Confirm the course dropdown shows kindergarten-related items only.
5. Select `幼兒園月費`.
6. Confirm the form auto-fills:
   - Program ID: `PRG-KG-MONTHLY`
   - Category: `幼兒園`
   - Default fee: `8500`
   - Billing cycle: `月繳`
7. Select department `安親班`.
8. Confirm the dropdown shows after-school items such as `一般安親班`, `安親兒童美語`, and `安親美術班`.
9. Select department `才藝班`.
10. Confirm the dropdown shows items such as `兒童美語`, `美術班`, `書法班`, and `假日才藝班`.
11. Select `自訂課程 / 自訂收費項目`.
12. Confirm manual fields appear for Program ID, course name, category, default fee, billing cycle, and notes.
13. Save a test program and confirm it appears in the course table.
14. Use `快速編輯現有課程` to adjust a default fee or disable a course.
15. Confirm old bills/enrollments are not deleted when a course is disabled.

## 8. How To Test Enrollment And Bills

1. Go to `學生課程報名管理`.
2. Select a student.
3. Select department `安親班`.
4. Select `一般安親班`.
5. Confirm the default fee is shown.
6. Optionally enter a custom fee override.
7. Save the enrollment.
8. Go to `繳費帳單`.
9. In `依課程報名建立帳單`, select a billing month and due date.
10. Select a department and create bills by:
    - 部門
    - 單一課程
    - 課程類別
    - 班級
    - 單一學生
    - 指定報名
11. Confirm each generated bill has a unique Bill ID and QR Code.
12. Continue the existing CSV reconciliation and receipt tests:
    - Partial payment should not generate final receipt.
    - Duplicate transaction should not double-post.
    - Fully paid bill can generate receipt.

## 9. Known Limitations

- This is still a prototype, not a production accounting system.
- Streamlit Community Cloud can run the demo, but SQLite local storage is not ideal for formal production data persistence.
- Existing old sample programs may still remain in the database because seed data uses insert-if-missing and does not delete old records.
- The department filter is based on course name/category inference, not a dedicated department column in `programs`.
- Real bank CSV formats still need a mapping/import setup for production use.
- No real payment gateway, bank API, webhook, credit card processing, or parent login is implemented.
- Streamlit may show `use_container_width` deprecation warnings; they do not currently block the app.

## 10. Pending Backlog

Recommended next implementation order:

1. Real bank CSV mapping:
   - Column mapping
   - Different date formats
   - Different amount formats
2. Stronger production data persistence:
   - Cloud database
   - Backup policy
   - Admin-only access
3. Parent-facing refinements:
   - More friendly status-specific text
   - Receipt download flow after confirmation
4. More complete payment arrangement workflow:
   - Parent contacted kindergarten
   - Reminder pause
   - Refund / apply-to-next-month note
5. Audit log UI improvements:
   - Filters
   - Export
   - Better display labels

## Responsibility Notice

This system is for payment tracking and digital receipt generation only.
All payments go directly to the kindergarten official account.
The system developer does not receive, store, manage, or process money.
Accounting, tax, refund, and legal receipt content must be confirmed by the kindergarten and its accountant.
This system is not an e-invoice system.
V1 does not connect to real banking or payment APIs.
