# Kindergarten QR Payment & Digital Receipt System

Streamlit prototype for a private kindergarten monthly fee workflow:

- Student management
- Monthly bill creation
- QR payment instruction mock page
- CSV bank/payment statement import
- Automatic reconciliation
- Digital receipt PDF generation
- Admin settings

This is not an e-invoice system and does not process money. All payments are instructed to go directly to the kindergarten's existing official bank/payment account.

## Run

```powershell
& "C:\Users\USER\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m streamlit run app.py
```

The first run creates `data/kindergarten.db`, sample students, sample bills, QR images, receipt PDFs, and `data/sample_bank_statement.csv`.

## Sample CSV columns

```csv
transaction_date,amount,payer_name,payment_note,transaction_id
2026-05-11,8500,王小明家長,KG-202605-0001 王小明 五月學費,T20260511001
```
