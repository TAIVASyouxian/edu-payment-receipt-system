from __future__ import annotations

from datetime import date
import html
import io
from pathlib import Path
import zipfile

import pandas as pd
import streamlit as st

from database import RECEIPT_DIR, connect, get_settings, read_df, save_settings, seed_sample_data
from services import PAID, PENDING, UNPAID, mask_student_name, parent_watermark, payment_reference, sample_bank_statement_path
from safety_services import (
    AFTER_SCHOOL,
    CANCELLED,
    DEPARTMENT_UNKNOWN,
    KINDERGARTEN,
    classify_department,
    classify_existing_students,
    ensure_all_qr_codes,
    expire_overdue_token,
    generate_qr_for_bill,
    generate_receipt_pdf,
    import_and_reconcile,
    department_unconfirmed_message,
    is_bill_stale,
    is_student_department_unconfirmed,
    log_audit,
    mark_bill_paid,
    parent_payment_url_for_token,
    regenerate_qr_for_bill,
)
from department_services import create_bills
from program_services import active_enrollments_query, create_bills_from_enrollments, next_enrollment_id
from payment_services import (
    ARRANGEMENT_STATUSES,
    PAYMENT_PAID,
    PAYMENT_UNPAID,
    payment_history,
    upsert_payment_arrangement,
)


st.set_page_config(
    page_title="Kindergarten QR Payment & Digital Receipt System",
    page_icon="🏫",
    layout="wide",
)


CSS = """
<style>
    :root {
        --nordic-bg: #f7f5f0;
        --nordic-surface: #fffdf8;
        --nordic-line: #ddd8cf;
        --nordic-text: #25302f;
        --nordic-muted: #66736f;
        --nordic-sage: #7f9a8c;
        --nordic-sky: #dbe8ea;
        --nordic-cream: #f2eadf;
        --nordic-amber: #ead8b4;
        --nordic-critical: #c47f74;
    }
    .stApp { background: var(--nordic-bg); color: var(--nordic-text); }
    .main .block-container { padding-top: 1.6rem; max-width: 1180px; }
    h1, h2, h3 { letter-spacing: 0; color: var(--nordic-text); font-weight: 650; }
    .metric-card {
        background: var(--nordic-surface);
        border: 1px solid var(--nordic-line);
        border-radius: 12px;
        padding: 16px 18px;
        box-shadow: 0 8px 22px rgba(44, 52, 49, 0.04);
    }
    .metric-label { color: var(--nordic-muted); font-size: 14px; margin-bottom: 8px; }
    .metric-value { color: var(--nordic-text); font-size: 26px; font-weight: 700; }
    .info-panel {
        background: #eef4f1;
        border-left: 4px solid var(--nordic-sage);
        border-radius: 12px;
        padding: 14px 16px;
        color: #34403d;
        line-height: 1.65;
    }
    .calm-section {
        background: var(--nordic-surface);
        border: 1px solid var(--nordic-line);
        border-radius: 12px;
        padding: 16px 18px;
        margin: 12px 0;
    }
    .calm-section h4 { margin: 0 0 8px 0; color: var(--nordic-text); }
    .calm-note { color: var(--nordic-muted); line-height: 1.7; }
    .badge {
        display: inline-block;
        padding: 5px 10px;
        border-radius: 999px;
        font-size: 12px;
        font-weight: 650;
    }
    .paid { background: #dfeee6; color: #3e6f58; }
    .unpaid { background: #ece7df; color: #655f56; }
    .pending { background: #f1e2c6; color: #816331; }
    .danger { background: #f1d9d5; color: #8d4f47; }
    .receipt-preview {
        background: var(--nordic-surface);
        border: 1px solid var(--nordic-line);
        border-radius: 12px;
        padding: 28px;
        color: var(--nordic-text);
        max-width: 860px;
        margin: 0 auto;
    }
    .receipt-header {
        border-bottom: 2px solid #334155;
        padding-bottom: 14px;
        margin-bottom: 18px;
        text-align: center;
    }
    .receipt-title { font-size: 24px; font-weight: 800; margin-bottom: 6px; }
    .receipt-subtitle { font-size: 16px; color: #475569; }
    .receipt-grid {
        display: grid;
        grid-template-columns: 150px 1fr 150px 1fr;
        border-top: 1px solid #cbd5e1;
        border-left: 1px solid #cbd5e1;
        margin-top: 14px;
    }
    .receipt-label, .receipt-value {
        border-right: 1px solid #cbd5e1;
        border-bottom: 1px solid #cbd5e1;
        padding: 10px 12px;
        min-height: 42px;
    }
    .receipt-label { background: #f8fafc; color: #475569; font-weight: 700; }
    .receipt-value { background: #ffffff; }
    .seal-box {
        width: 110px;
        height: 110px;
        border: 1px solid #b91c1c;
        color: #b91c1c;
        display: flex;
        align-items: center;
        justify-content: center;
        margin-left: auto;
        margin-top: 22px;
        font-weight: 700;
    }
    .parent-detail-grid {
        display: grid;
        grid-template-columns: 1fr;
        gap: 10px;
        margin: 14px 0 12px 0;
    }
    .parent-detail-card {
        background: #fffdf8;
        border: 1px solid #d7d1c6;
        border-radius: 12px;
        padding: 12px 14px;
        box-shadow: 0 6px 18px rgba(44, 52, 49, 0.05);
    }
    .parent-detail-label {
        color: #5B3A8E;
        font-size: 14px;
        font-weight: 700;
        margin-bottom: 6px;
    }
    .parent-detail-value {
        color: #263238;
        font-size: 21px;
        font-weight: 800;
        line-height: 1.25;
        overflow-wrap: anywhere;
    }
    .parent-page-shell {
        max-width: 460px;
        margin: 0 auto;
        padding: 0 6px 24px 6px;
    }
    .parent-header-card {
        background: #fffdf8;
        border: 1px solid #d7d1c6;
        border-radius: 14px;
        padding: 16px 16px;
        margin: 6px 0 12px 0;
        color: #263238;
        box-shadow: 0 8px 20px rgba(44, 52, 49, 0.05);
    }
    .parent-page-title {
        font-size: 30px;
        line-height: 1.18;
        font-weight: 850;
        color: #263238;
        margin: 0 0 10px 0;
    }
    .parent-watermark {
        color: #607D8B;
        font-size: 12px;
        line-height: 1.55;
        overflow-wrap: anywhere;
    }
    .parent-safety-notice {
        background: #f4e7b8;
        border: 1px solid #e2cb87;
        border-radius: 12px;
        padding: 13px 15px;
        margin: 12px 0;
        color: #4B2E83;
        font-weight: 750;
        line-height: 1.65;
    }
    .parent-reminder-card {
        background: #eef4f1;
        border: 1px solid #cfddd6;
        border-radius: 12px;
        padding: 13px 15px;
        margin: 12px 0;
        color: #263238;
        font-size: 16px;
        line-height: 1.72;
    }
    .parent-summary {
        background: #fffdf8;
        border: 1px solid #d7d1c6;
        border-radius: 12px;
        padding: 14px 16px;
        margin: 10px 0 16px 0;
        color: #263238;
        line-height: 1.7;
    }
    .parent-summary strong { color: #5B3A8E; font-weight: 800; }
    .parent-payment-section {
        background: #fffdf8;
        border: 1px solid #d7d1c6;
        border-radius: 12px;
        padding: 14px 16px;
        margin: 14px 0;
        color: #263238;
        line-height: 1.7;
    }
    .parent-payment-section h3 {
        font-size: 20px;
        margin: 0 0 10px 0;
        color: #263238;
    }
    @media (max-width: 640px) {
        .parent-detail-grid {
            grid-template-columns: 1fr;
            gap: 10px;
        }
        .parent-detail-card {
            padding: 13px 14px;
        }
        .parent-detail-label {
            font-size: 13px;
        }
        .parent-detail-value {
            font-size: 22px;
        }
    }
    @media (min-width: 700px) {
        .parent-detail-grid {
            grid-template-columns: repeat(3, minmax(0, 1fr));
        }
        .parent-page-shell {
            max-width: 480px;
        }
    }
    @media (max-width: 600px) {
        .main .block-container {
            padding-left: 0.85rem;
            padding-right: 0.85rem;
        }
        .parent-page-title {
            font-size: 30px;
        }
        .parent-reminder-card,
        .parent-safety-notice,
        .parent-summary,
        .parent-payment-section {
            font-size: 16px;
        }
        .stDownloadButton button {
            width: 100%;
            margin-bottom: 8px;
        }
    }
</style>
"""


STATUS_LABELS = {
    PAID: "已付款",
    UNPAID: "尚未完成繳費",
    PENDING: "待對帳確認",
    CANCELLED: "取消帳單",
    "Matched": "已配對",
    "Unmatched": "需協助確認",
    "Duplicate": "疑似重複付款",
}

PAYMENT_STATUS_LABELS = {
    None: PAYMENT_UNPAID,
    "": PAYMENT_UNPAID,
    PAID: PAYMENT_PAID,
    UNPAID: PAYMENT_UNPAID,
    PENDING: "待對帳確認",
}

BILLING_CYCLE_LABELS = {
    "monthly": "月繳",
    "one-time": "單次",
    "semester": "學期",
    "per-class": "依堂數",
}

BILLING_CYCLE_OPTIONS = list(BILLING_CYCLE_LABELS.keys())

PROGRAM_DEPARTMENTS = ["幼兒園", "安親班", "才藝班", "其他"]

COURSE_TEMPLATES = {
    "幼兒園": [
        {"program_id": "PRG-KG-MONTHLY", "program_name": "幼兒園月費", "program_category": "幼兒園", "default_fee_amount": 8500, "billing_cycle": "monthly"},
        {"program_id": "PRG-KG-REGISTRATION", "program_name": "幼兒園註冊費", "program_category": "幼兒園", "default_fee_amount": 12000, "billing_cycle": "semester"},
        {"program_id": "PRG-KG-MATERIALS", "program_name": "幼兒園材料費", "program_category": "材料", "default_fee_amount": 1500, "billing_cycle": "semester"},
        {"program_id": "PRG-KG-TRANSPORT", "program_name": "幼兒園交通費", "program_category": "交通", "default_fee_amount": 2000, "billing_cycle": "monthly"},
        {"program_id": "PRG-KG-ACTIVITY", "program_name": "幼兒園活動費", "program_category": "幼兒園", "default_fee_amount": 800, "billing_cycle": "one-time"},
        {"program_id": "PRG-KG-OTHER", "program_name": "幼兒園其他費用", "program_category": "其他", "default_fee_amount": 0, "billing_cycle": "one-time"},
    ],
    "安親班": [
        {"program_id": "PRG-AFTERSCHOOL", "program_name": "一般安親班", "program_category": "安親班", "default_fee_amount": 8000, "billing_cycle": "monthly"},
        {"program_id": "PRG-AFTERSCHOOL-ENGLISH", "program_name": "安親兒童美語", "program_category": "安親班", "default_fee_amount": 3000, "billing_cycle": "monthly"},
        {"program_id": "PRG-AFTERSCHOOL-ART", "program_name": "安親美術班", "program_category": "安親班", "default_fee_amount": 2600, "billing_cycle": "monthly"},
        {"program_id": "PRG-AFTERSCHOOL-CALLIGRAPHY", "program_name": "安親書法班", "program_category": "安親班", "default_fee_amount": 2500, "billing_cycle": "monthly"},
        {"program_id": "PRG-AFTERSCHOOL-SNACK", "program_name": "安親點心費", "program_category": "安親班", "default_fee_amount": 800, "billing_cycle": "monthly"},
        {"program_id": "PRG-AFTERSCHOOL-TRANSPORT", "program_name": "安親交通費", "program_category": "交通", "default_fee_amount": 2000, "billing_cycle": "monthly"},
        {"program_id": "PRG-AFTERSCHOOL-VACATION", "program_name": "寒暑假安親", "program_category": "安親班", "default_fee_amount": 9000, "billing_cycle": "monthly"},
        {"program_id": "PRG-AFTERSCHOOL-OTHER", "program_name": "安親其他費用", "program_category": "其他", "default_fee_amount": 0, "billing_cycle": "one-time"},
    ],
    "才藝班": [
        {"program_id": "PRG-ENGLISH", "program_name": "兒童美語", "program_category": "兒童美語", "default_fee_amount": 3000, "billing_cycle": "monthly"},
        {"program_id": "PRG-ART", "program_name": "美術班", "program_category": "美術", "default_fee_amount": 2600, "billing_cycle": "monthly"},
        {"program_id": "PRG-CALLIGRAPHY", "program_name": "書法班", "program_category": "書法", "default_fee_amount": 2500, "billing_cycle": "monthly"},
        {"program_id": "PRG-MUSIC", "program_name": "音樂班", "program_category": "才藝班", "default_fee_amount": 3000, "billing_cycle": "monthly"},
        {"program_id": "PRG-WEEKEND-ART", "program_name": "假日美術班", "program_category": "假日才藝", "default_fee_amount": 2800, "billing_cycle": "monthly"},
        {"program_id": "PRG-WEEKEND-TALENT", "program_name": "假日才藝班", "program_category": "假日才藝", "default_fee_amount": 2800, "billing_cycle": "monthly"},
        {"program_id": "PRG-TALENT-OTHER", "program_name": "其他才藝課程", "program_category": "才藝班", "default_fee_amount": 0, "billing_cycle": "monthly"},
    ],
    "其他": [
        {"program_id": "PRG-TRANSPORT", "program_name": "交通費", "program_category": "交通", "default_fee_amount": 2000, "billing_cycle": "monthly"},
        {"program_id": "PRG-MATERIALS", "program_name": "材料費", "program_category": "材料", "default_fee_amount": 1000, "billing_cycle": "semester"},
        {"program_id": "PRG-ACTIVITY", "program_name": "活動費", "program_category": "其他", "default_fee_amount": 800, "billing_cycle": "one-time"},
        {"program_id": "PRG-ADJUSTMENT", "program_name": "補收費用", "program_category": "其他", "default_fee_amount": 0, "billing_cycle": "one-time"},
        {"program_id": "PRG-OTHER", "program_name": "其他費用", "program_category": "其他", "default_fee_amount": 0, "billing_cycle": "one-time"},
    ],
}

CUSTOM_COURSE_LABEL = "自訂課程 / 自訂收費項目"


def money(value: object) -> str:
    return f"NT$ {int(float(value or 0)):,}"


def status_badge(status: str) -> str:
    cls = "paid" if status in [PAID, "Matched"] else "pending" if status in [PENDING, "Duplicate", "Unmatched"] else "danger" if status in [CANCELLED] else "unpaid"
    return f'<span class="badge {cls}">{STATUS_LABELS.get(status, status)}</span>'


def table_status_chinese(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ["status", "match_status"]:
        if col in out.columns:
            out[col] = out[col].replace(STATUS_LABELS)
    return out


def billing_cycle_label(value: object) -> str:
    return BILLING_CYCLE_LABELS.get(str(value or ""), str(value or ""))


def template_by_name(department: str, course_name: str) -> dict:
    for item in COURSE_TEMPLATES.get(department, []):
        if item["program_name"] == course_name:
            return item.copy()
    return {}


def infer_program_department(row: object) -> str:
    name = str(getattr(row, "program_name", "") or row.get("program_name", "") if isinstance(row, dict) else "")
    category = str(getattr(row, "program_category", "") or row.get("program_category", "") if isinstance(row, dict) else "")
    if name.startswith("幼兒園") or category == "幼兒園":
        return "幼兒園"
    if name.startswith("安親") or "安親" in name or category == "安親班":
        return "安親班"
    if category in ["兒童美語", "美術", "書法", "假日才藝", "才藝班"] or any(keyword in name for keyword in ["美語", "美術", "書法", "音樂", "才藝"]):
        return "才藝班"
    return "其他"


def filter_programs_by_department(programs: pd.DataFrame, department: str) -> pd.DataFrame:
    if programs.empty:
        return programs
    out = programs.copy()
    out["_department"] = out.apply(lambda row: infer_program_department(row.to_dict()), axis=1)
    return out[out["_department"] == department].drop(columns=["_department"])


def render_metric(label: str, value: object) -> None:
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">{label}</div>
            <div class="metric-value">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_responsibility_panel() -> None:
    st.markdown(
        """
        <div class="info-panel">
        本系統僅用於繳費紀錄追蹤與數位收據產生，不是電子發票系統。所有款項皆直接進入園方官方帳戶，
        系統開發者不收取、代收、保管或處理任何款項。會計、稅務、退款與收據內容仍需由園方及會計專業人員確認。
        V1 不串接真實銀行或支付 API。
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_calm_section(title: str, body: str) -> None:
    st.markdown(
        f"""
        <div class="calm-section">
            <h4>{title}</h4>
            <div class="calm-note">{body}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def dashboard_page() -> None:
    st.title("儀表板")
    render_responsibility_panel()

    students = read_df("SELECT * FROM students")
    bills = read_df(
        """
        SELECT bills.*, COALESCE(students.department, '待確認') AS department,
               programs.program_name, programs.program_category
        FROM bills
        LEFT JOIN students ON bills.student_id = students.student_id
        LEFT JOIN programs ON bills.program_id = programs.program_id
        """
    )
    tx = read_df("SELECT * FROM transactions ORDER BY imported_at DESC LIMIT 8")

    today = date.today().isoformat()
    pending_payment_count = len(bills[bills["payment_status"].isin(["待對帳確認", "金額需確認", "溢付款需處理"])]) if not bills.empty and "payment_status" in bills.columns else 0
    partial_count = len(bills[bills["payment_status"] == "部分付款"]) if not bills.empty and "payment_status" in bills.columns else 0
    grace_count = len(bills[bills["payment_status"] == "寬限期中"]) if not bills.empty and "payment_status" in bills.columns else 0
    promised_count = len(bills[bills["payment_status"] == "已約定補繳日"]) if not bills.empty and "payment_status" in bills.columns else 0
    receipt_ready_count = len(bills[(bills["status"] == PAID) & (bills["receipt_number"].isna())]) if not bills.empty else 0
    communication_count = len(bills[bills["payment_status"].isin(["家長已聯繫園方", "暫緩提醒"])]) if not bills.empty and "payment_status" in bills.columns else 0

    st.subheader("今日待處理事項")
    t1, t2, t3, t4 = st.columns(4)
    with t1:
        render_metric("待確認繳費", pending_payment_count)
    with t2:
        render_metric("收據待產生", receipt_ready_count)
    with t3:
        render_metric("寬限期中", grace_count)
    with t4:
        render_metric("已約定補繳", promised_count)

    st.subheader("家長溝通備註")
    render_calm_section(
        "溝通中的紀錄",
        f"目前有 {communication_count} 筆紀錄標示為家長已聯繫園方或暫緩提醒。這些狀態用於協助行政追蹤，不作為壓力標籤。",
    )

    st.subheader("繳費紀錄摘要")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        render_metric("學生數", len(students))
    with c2:
        render_metric("本月帳單數", len(bills))
    with c3:
        render_metric("已付款帳單", len(bills[bills["status"] == PAID]) if not bills.empty else 0)
    with c4:
        pending_students = len(students[students["department"] == DEPARTMENT_UNKNOWN]) if not students.empty and "department" in students.columns else 0
        render_metric("待確認分類", pending_students)

    p1, p2 = st.columns(2)
    with p1:
        render_calm_section("部分付款", f"目前有 {partial_count} 筆帳單已記錄部分付款，待全額確認後再產生正式數位收據。")
    with p2:
        render_calm_section("待確認繳費", f"目前有 {pending_payment_count} 筆款項需要園方協助確認，請避免重複處理。")

    st.subheader("部門摘要")
    rows = []
    for department in [KINDERGARTEN, AFTER_SCHOOL, DEPARTMENT_UNKNOWN]:
        dept_students = students[students["department"] == department] if not students.empty and "department" in students.columns else pd.DataFrame()
        dept_bills = bills[bills["department"] == department] if not bills.empty else pd.DataFrame()
        rows.append(
            {
                "部門": department,
                "學生數": len(dept_students),
                "帳單數": len(dept_bills),
                "已付款": len(dept_bills[dept_bills["status"] == PAID]) if not dept_bills.empty else 0,
                "待對帳確認": len(dept_bills[dept_bills["status"] == PENDING]) if not dept_bills.empty else 0,
                "預計金額": money(dept_bills["amount"].sum()) if not dept_bills.empty else money(0),
                "已確認金額": money(dept_bills[dept_bills["status"] == PAID]["amount"].sum()) if not dept_bills.empty else money(0),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    st.subheader("課程帳單摘要")
    program_bills = bills[bills["program_id"].notna()] if not bills.empty and "program_id" in bills.columns else pd.DataFrame()
    if program_bills.empty:
        st.info("尚無課程報名產生的帳單。")
    else:
        summary_rows = []
        grouped = program_bills.groupby(["program_category", "program_name", "month"], dropna=False)
        for (category, program_name, month), group in grouped:
            paid = group[group["status"] == PAID]
            unpaid = group[group["status"] == UNPAID]
            summary_rows.append(
                {
                    "課程類別": category or "",
                    "課程": program_name or "",
                    "月份": month,
                    "帳單數": len(group),
                    "已付款": len(paid),
                    "尚未完成繳費": len(unpaid),
                    "預計金額": money(group["amount"].sum()),
                    "已確認金額": money(paid["amount"].sum()),
                }
            )
        st.dataframe(pd.DataFrame(summary_rows), hide_index=True, use_container_width=True)

    st.subheader("最近對帳紀錄")
    if tx.empty:
        st.info("尚無匯入交易紀錄。")
    else:
        display = table_status_chinese(tx[["transaction_date", "amount", "payer_name", "payment_note", "match_status", "confidence", "matched_bill_id", "warning"]])
        display = display.rename(
            columns={
                "transaction_date": "交易日期",
                "amount": "金額",
                "payer_name": "付款人",
                "payment_note": "付款備註",
                "match_status": "對帳狀態",
                "confidence": "信心度",
                "matched_bill_id": "配對帳單",
                "warning": "系統提示",
            }
        )
        st.dataframe(display, hide_index=True, use_container_width=True)


def students_page() -> None:
    st.title("學生管理")
    st.info("目前 V1 保留部門分類欄位供帳單建立使用。若分類信心度低，系統會標記為待確認，避免未經確認就產生帳單。")

    with st.expander("新增學生", expanded=True):
        with st.form("add_student"):
            c1, c2, c3 = st.columns(3)
            student_id = c1.text_input("Student ID", placeholder="S006")
            student_name = c2.text_input("學生姓名")
            class_name = c3.text_input("班級")
            c4, c5, c6 = st.columns(3)
            parent_name = c4.text_input("家長姓名")
            contact = c5.text_input("家長電話或 Email（選填）")
            inferred = classify_department(class_name=class_name)
            department_options = list(dict.fromkeys([inferred["department"], KINDERGARTEN, AFTER_SCHOOL, DEPARTMENT_UNKNOWN]))
            department = c6.selectbox("部門", department_options)
            status = st.selectbox("狀態", ["active", "inactive"], format_func=lambda x: "啟用" if x == "active" else "停用")
            st.caption(f"系統建議：{inferred['department']} / 信心度：{inferred['confidence']} / 原因：{inferred['reason']}")
            if st.form_submit_button("新增"):
                if not student_id or not student_name or not class_name or not parent_name:
                    st.error("請填寫 Student ID、學生姓名、班級與家長姓名。")
                else:
                    with connect() as conn:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO students(student_id, student_name, class_name, department, parent_name, contact, status)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (student_id, student_name, class_name, department, parent_name, contact, status),
                        )
                    st.success("學生資料已儲存。")
                    st.rerun()

    with st.expander("CSV 匯入學生與分類預覽"):
        st.caption("必要欄位：student_id, student_name, class_name, parent_name。選填欄位：contact, status, department, fee_item。")
        upload = st.file_uploader("上傳學生 CSV", type=["csv"], key="student_csv")
        if upload:
            df = pd.read_csv(upload)
            required = ["student_id", "student_name", "class_name", "parent_name"]
            missing = [col for col in required if col not in df.columns]
            if missing:
                st.error(f"CSV 缺少必要欄位：{', '.join(missing)}")
            else:
                preview = df.copy()
                for optional in ["contact", "status", "department", "fee_item"]:
                    if optional not in preview.columns:
                        preview[optional] = "" if optional != "status" else "active"
                classifications = preview.apply(
                    lambda row: classify_department(row.get("class_name"), row.get("fee_item"), row.get("department")),
                    axis=1,
                )
                preview["inferred_department"] = [item["department"] for item in classifications]
                preview["confidence"] = [item["confidence"] for item in classifications]
                preview["classification_reason"] = [item["reason"] for item in classifications]
                preview["manual_department"] = preview["inferred_department"]
                if (preview["confidence"] == "低").any():
                    st.warning("部分資料分類信心度低，請在儲存前人工確認。")
                edited = st.data_editor(
                    preview[
                        [
                            "student_id",
                            "student_name",
                            "class_name",
                            "parent_name",
                            "contact",
                            "status",
                            "inferred_department",
                            "confidence",
                            "classification_reason",
                            "manual_department",
                        ]
                    ],
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "manual_department": st.column_config.SelectboxColumn("人工確認部門", options=[KINDERGARTEN, AFTER_SCHOOL, DEPARTMENT_UNKNOWN]),
                        "status": st.column_config.SelectboxColumn("狀態", options=["active", "inactive"]),
                    },
                )
                if st.button("確認匯入學生"):
                    with connect() as conn:
                        conn.executemany(
                            """
                            INSERT OR REPLACE INTO students(student_id, student_name, class_name, department, parent_name, contact, status)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            edited[["student_id", "student_name", "class_name", "manual_department", "parent_name", "contact", "status"]].fillna("").values.tolist(),
                        )
                    st.success(f"已匯入 {len(edited)} 筆學生資料。")
                    st.rerun()

    students = read_df("SELECT * FROM students ORDER BY department, class_name, student_id")
    department_filter = st.selectbox("部門篩選", ["全部", KINDERGARTEN, AFTER_SCHOOL, DEPARTMENT_UNKNOWN])
    if department_filter != "全部" and not students.empty:
        students = students[students["department"] == department_filter]

    st.subheader("學生列表")
    if students.empty:
        st.info("尚無學生資料。")
        return
    edited = st.data_editor(
        students[["student_id", "student_name", "class_name", "department", "parent_name", "contact", "status"]],
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        column_config={
            "department": st.column_config.SelectboxColumn("部門", options=[KINDERGARTEN, AFTER_SCHOOL, DEPARTMENT_UNKNOWN]),
            "status": st.column_config.SelectboxColumn("狀態", options=["active", "inactive"]),
        },
    )
    if st.button("儲存學生更新"):
        with connect() as conn:
            for row in edited.to_dict("records"):
                conn.execute(
                    """
                    UPDATE students
                    SET student_name = ?, class_name = ?, department = ?, parent_name = ?, contact = ?, status = ?
                    WHERE student_id = ?
                    """,
                    (row["student_name"], row["class_name"], row["department"], row["parent_name"], row["contact"], row["status"], row["student_id"]),
                )
        st.success("學生資料已更新。")
        st.rerun()


def programs_page() -> None:
    st.title("課程與收費項目管理")
    programs = read_df("SELECT * FROM programs ORDER BY program_category, program_name")

    with st.expander("新增課程與收費項目", expanded=True):
        selected_department = st.selectbox("部門", PROGRAM_DEPARTMENTS, key="program_department")
        course_names = [item["program_name"] for item in COURSE_TEMPLATES[selected_department]] + [CUSTOM_COURSE_LABEL]
        selected_course = st.selectbox("課程 / 收費項目", course_names, key="program_course_template")
        template = template_by_name(selected_department, selected_course)
        is_custom = selected_course == CUSTOM_COURSE_LABEL

        if not is_custom:
            st.caption("常用課程會自動帶入課程 ID、類別、預設金額與收費週期。需要調整時可修改金額或到進階設定調整 ID。")

        with st.form("program_form"):
            if is_custom:
                c1, c2 = st.columns(2)
                program_id = c1.text_input("課程 ID", placeholder="PRG-CUSTOM")
                program_name = c2.text_input("課程 / 服務名稱")
                c3, c4, c5 = st.columns(3)
                program_category = c3.selectbox("類別", ["幼兒園", "安親班", "兒童美語", "假日才藝", "美術", "書法", "交通", "材料", "其他"])
                default_fee_amount = c4.number_input("預設金額", min_value=0, value=0, step=100)
                billing_cycle = c5.selectbox("收費週期", BILLING_CYCLE_OPTIONS, format_func=billing_cycle_label)
            else:
                program_id = template["program_id"]
                program_name = template["program_name"]
                program_category = template["program_category"]
                c1, c2, c3, c4 = st.columns(4)
                c1.text_input("課程 ID", value=program_id, disabled=True)
                c2.text_input("課程 / 服務名稱", value=program_name, disabled=True)
                c3.text_input("類別", value=program_category, disabled=True)
                default_fee_amount = c4.number_input("預設金額", min_value=0, value=int(template["default_fee_amount"]), step=100)
                billing_cycle = st.selectbox(
                    "收費週期",
                    BILLING_CYCLE_OPTIONS,
                    index=BILLING_CYCLE_OPTIONS.index(template["billing_cycle"]),
                    format_func=billing_cycle_label,
                )
                with st.expander("進階設定：手動調整課程 ID", expanded=False):
                    program_id = st.text_input("課程 ID（進階）", value=program_id)

            status = st.selectbox("狀態", ["active", "inactive"], format_func=lambda x: "啟用" if x == "active" else "停用")
            notes = st.text_area("備註", height=80)
            if st.form_submit_button("儲存課程"):
                if not program_id or not program_name:
                    st.error("請填寫課程 ID 與課程 / 服務名稱。")
                else:
                    with connect() as conn:
                        existing = conn.execute("SELECT status FROM programs WHERE program_id = ?", (program_id,)).fetchone()
                        conn.execute(
                            """
                            INSERT INTO programs(program_id, program_name, program_category, default_fee_amount, billing_cycle, status, notes)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(program_id) DO UPDATE SET
                                program_name = excluded.program_name,
                                program_category = excluded.program_category,
                                default_fee_amount = excluded.default_fee_amount,
                                billing_cycle = excluded.billing_cycle,
                                status = excluded.status,
                                notes = excluded.notes
                            """,
                            (program_id, program_name, program_category, int(default_fee_amount), billing_cycle, status, notes),
                        )
                    if not existing:
                        log_audit("Program created", "program", program_id, f"Created program {program_name}.")
                    elif status == "inactive" and existing["status"] != "inactive":
                        log_audit("Program disabled", "program", program_id, f"Disabled program {program_name}.")
                    else:
                        log_audit("Program edited", "program", program_id, f"Edited program {program_name}.")
                    st.success("課程資料已儲存。")
                    st.rerun()

    if programs.empty:
        st.info("尚無課程資料。")
        return
    display = programs[["program_id", "program_name", "program_category", "default_fee_amount", "billing_cycle", "status", "notes"]].rename(
        columns={
            "program_id": "課程 ID",
            "program_name": "課程 / 服務名稱",
            "program_category": "類別",
            "default_fee_amount": "預設金額",
            "billing_cycle": "收費週期",
            "status": "狀態",
            "notes": "備註",
        }
    )
    display["收費週期"] = display["收費週期"].apply(billing_cycle_label)
    display["狀態"] = display["狀態"].replace({"active": "啟用", "inactive": "停用"})
    st.dataframe(display, hide_index=True, use_container_width=True)

    with st.expander("快速編輯現有課程", expanded=False):
        editable = st.data_editor(
            programs[["program_id", "program_name", "program_category", "default_fee_amount", "billing_cycle", "status", "notes"]],
            hide_index=True,
            use_container_width=True,
            disabled=["program_id"],
            column_config={
                "program_id": "課程 ID",
                "program_name": "課程 / 服務名稱",
                "program_category": st.column_config.SelectboxColumn("類別", options=["幼兒園", "安親班", "兒童美語", "假日才藝", "美術", "書法", "交通", "材料", "其他"]),
                "default_fee_amount": st.column_config.NumberColumn("預設金額", min_value=0, step=100),
                "billing_cycle": st.column_config.SelectboxColumn("收費週期", options=BILLING_CYCLE_OPTIONS),
                "status": st.column_config.SelectboxColumn("狀態", options=["active", "inactive"]),
                "notes": "備註",
            },
        )
        if st.button("儲存課程編輯"):
            original = programs.set_index("program_id").to_dict("index")
            with connect() as conn:
                for row in editable.to_dict("records"):
                    old = original.get(row["program_id"], {})
                    conn.execute(
                        """
                        UPDATE programs
                        SET program_name = ?, program_category = ?, default_fee_amount = ?,
                            billing_cycle = ?, status = ?, notes = ?
                        WHERE program_id = ?
                        """,
                        (
                            row["program_name"],
                            row["program_category"],
                            int(row["default_fee_amount"] or 0),
                            row["billing_cycle"],
                            row["status"],
                            row["notes"] if pd.notna(row["notes"]) else "",
                            row["program_id"],
                        ),
                    )
                    if old and row["status"] == "inactive" and old.get("status") != "inactive":
                        log_audit("Program disabled", "program", row["program_id"], f"Disabled program {row['program_name']}.")
                    elif old and any(str(row[key]) != str(old.get(key, "")) for key in ["program_name", "program_category", "default_fee_amount", "billing_cycle", "status", "notes"]):
                        log_audit("Program edited", "program", row["program_id"], f"Edited program {row['program_name']}.")
            st.success("課程編輯已儲存。")
            st.rerun()


def enrollments_page() -> None:
    st.title("學生課程報名管理")
    students = read_df("SELECT * FROM students WHERE status = 'active' ORDER BY class_name, student_name")
    programs = read_df("SELECT * FROM programs WHERE status = 'active' ORDER BY program_category, program_name")

    with st.expander("新增報名紀錄", expanded=True):
        if students.empty or programs.empty:
            st.info("請先建立啟用中的學生與課程。")
        else:
            selected_department = st.selectbox("部門", PROGRAM_DEPARTMENTS, key="enrollment_department")
            filtered_programs = filter_programs_by_department(programs, selected_department)
            if filtered_programs.empty:
                st.info("此部門目前沒有啟用中的課程，請先到「課程與收費項目管理」新增課程。")
            else:
                with st.form("enrollment_form"):
                    student_options = {f"{row.student_name}（{row.student_id} / {row.class_name}）": row.student_id for row in students.itertuples()}
                    program_options = {f"{row.program_name}（{row.program_category} / {money(row.default_fee_amount)}）": row.program_id for row in filtered_programs.itertuples()}
                    program_defaults = {row.program_id: int(row.default_fee_amount or 0) for row in filtered_programs.itertuples()}
                    c1, c2 = st.columns(2)
                    selected_student = c1.selectbox("學生", list(student_options.keys()))
                    selected_program = c2.selectbox("課程 / 服務項目", list(program_options.keys()))
                    selected_program_id = program_options[selected_program]
                    st.caption(f"課程預設金額：{money(program_defaults[selected_program_id])}。如需個別調整，可填寫自訂金額。")
                    c3, c4, c5 = st.columns(3)
                    start_date = c3.date_input("開始日期", value=date.today()).isoformat()
                    end_date_value = c4.date_input("結束日期（選填，若不用可留今天後手動清空）", value=date.today()).isoformat()
                    use_end_date = c4.checkbox("設定結束日期", value=False)
                    enrollment_status = c5.selectbox("報名狀態", ["active", "paused", "ended"], format_func=lambda x: {"active": "進行中", "paused": "暫停", "ended": "已結束"}[x])
                    custom_fee_amount = st.number_input("自訂金額（0 代表使用課程預設金額）", min_value=0, value=0, step=100)
                    notes = st.text_area("備註", height=80)
                    if st.form_submit_button("建立報名"):
                        enrollment_id = next_enrollment_id()
                        with connect() as conn:
                            conn.execute(
                                """
                                INSERT INTO enrollments(
                                    enrollment_id, student_id, program_id, start_date, end_date,
                                    enrollment_status, custom_fee_amount, notes
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    enrollment_id,
                                    student_options[selected_student],
                                    selected_program_id,
                                    start_date,
                                    end_date_value if use_end_date else None,
                                    enrollment_status,
                                    int(custom_fee_amount) if custom_fee_amount else None,
                                    notes,
                                ),
                            )
                        log_audit("Enrollment created", "enrollment", enrollment_id, f"Created enrollment {enrollment_id}.")
                        if custom_fee_amount:
                            log_audit("Custom fee override used", "enrollment", enrollment_id, f"Custom fee {int(custom_fee_amount)} used for enrollment {enrollment_id}.")
                        st.success("報名紀錄已建立。")
                        st.rerun()

    enrollments = read_df(
        """
        SELECT enrollments.*, students.student_name, students.class_name, programs.program_name,
               programs.program_category, programs.default_fee_amount
        FROM enrollments
        JOIN students ON students.student_id = enrollments.student_id
        JOIN programs ON programs.program_id = enrollments.program_id
        ORDER BY enrollments.created_at DESC
        """
    )
    if enrollments.empty:
        st.info("尚無報名紀錄。")
        return
    edited = st.data_editor(
        enrollments[
            [
                "enrollment_id",
                "student_id",
                "student_name",
                "class_name",
                "program_id",
                "program_name",
                "program_category",
                "start_date",
                "end_date",
                "enrollment_status",
                "custom_fee_amount",
                "notes",
            ]
        ],
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        column_config={
            "enrollment_status": st.column_config.SelectboxColumn("報名狀態", options=["active", "paused", "ended"]),
        },
    )
    if st.button("儲存報名狀態與備註"):
        with connect() as conn:
            for row in edited.to_dict("records"):
                conn.execute(
                    """
                    UPDATE enrollments
                    SET start_date = ?, end_date = ?, enrollment_status = ?, custom_fee_amount = ?, notes = ?
                    WHERE enrollment_id = ?
                    """,
                    (
                        row["start_date"],
                        row["end_date"] if pd.notna(row["end_date"]) else None,
                        row["enrollment_status"],
                        int(row["custom_fee_amount"]) if pd.notna(row["custom_fee_amount"]) and row["custom_fee_amount"] != "" else None,
                        row["notes"] if pd.notna(row["notes"]) else "",
                        row["enrollment_id"],
                    ),
                )
        st.success("報名紀錄已更新。")
        st.rerun()


def bills_page() -> None:
    st.title("繳費帳單管理")
    students = read_df("SELECT * FROM students WHERE status = 'active' ORDER BY department, class_name, student_name")
    programs = read_df("SELECT * FROM programs WHERE status = 'active' ORDER BY program_category, program_name")
    active_enrollments = active_enrollments_query()
    bills = read_df(
        """
        SELECT bills.*, COALESCE(students.department, '待確認') AS department,
               programs.program_name, programs.program_category
        FROM bills
        LEFT JOIN students ON bills.student_id = students.student_id
        LEFT JOIN programs ON bills.program_id = programs.program_id
        ORDER BY bills.created_at DESC
        """
    )

    with st.expander("依課程報名建立帳單", expanded=True):
        if active_enrollments.empty:
            st.info("尚無可建立帳單的啟用中報名紀錄。請先到「學生課程報名管理」建立報名。")
        else:
            with st.form("create_program_bills"):
                c1, c2, c3 = st.columns(3)
                billing_month = c1.text_input("帳單月份", value=date.today().strftime("%Y-%m"))
                due_date_program = c2.date_input("繳費期限", value=date.today(), key="program_due_date").isoformat()
                scope_label = c3.selectbox("建立範圍", ["所有啟用中報名", "部門", "單一課程", "課程類別", "班級", "單一學生", "指定報名"])
                selected_department = st.selectbox("部門篩選", PROGRAM_DEPARTMENTS, key="bill_program_department")
                filtered_programs = filter_programs_by_department(programs, selected_department)
                filtered_enrollments = active_enrollments.copy()
                if not active_enrollments.empty:
                    filtered_enrollments["_department"] = filtered_enrollments.apply(lambda row: infer_program_department(row.to_dict()), axis=1)
                    filtered_enrollments = filtered_enrollments[filtered_enrollments["_department"] == selected_department].drop(columns=["_department"])

                selected_value = ""
                selected_enrollment_ids: list[str] = []
                scope_map = {
                    "所有啟用中報名": "all_active_enrollments",
                    "部門": "one_category",
                    "單一課程": "one_program",
                    "課程類別": "one_category",
                    "班級": "one_class",
                    "單一學生": "one_student",
                    "指定報名": "selected_enrollments",
                }
                scope = scope_map[scope_label]
                if scope_label == "部門":
                    scope = "selected_enrollments"
                    selected_enrollment_ids = filtered_enrollments["enrollment_id"].dropna().tolist()
                    st.caption(f"將為「{selected_department}」的所有啟用中報名建立帳單。")
                if scope_label == "單一課程":
                    if filtered_programs.empty:
                        st.info("此部門目前沒有啟用中的課程。")
                    else:
                        options = {f"{row.program_name}（{row.program_category}）": row.program_id for row in filtered_programs.itertuples()}
                        selected_label = st.selectbox("課程", list(options.keys()))
                        selected_value = options[selected_label]
                elif scope_label == "課程類別":
                    categories = sorted(filtered_programs["program_category"].dropna().unique().tolist()) if not filtered_programs.empty else []
                    selected_value = st.selectbox("課程類別", categories)
                elif scope_label == "班級":
                    selected_value = st.selectbox("班級", sorted(students["class_name"].dropna().unique().tolist()))
                elif scope_label == "單一學生":
                    options = {f"{row.student_name}（{row.student_id} / {row.class_name}）": row.student_id for row in students.itertuples()}
                    selected_label = st.selectbox("學生", list(options.keys()))
                    selected_value = options[selected_label]
                elif scope_label == "指定報名":
                    enrollment_options = {
                        f"{row.enrollment_id} - {row.student_name} / {row.program_name}": row.enrollment_id
                        for row in filtered_enrollments.itertuples()
                    }
                    selected_labels = st.multiselect("報名紀錄", list(enrollment_options.keys()))
                    selected_enrollment_ids = [enrollment_options[label] for label in selected_labels]
                notes = st.text_area("帳單備註", height=80, key="program_bill_notes")
                if st.form_submit_button("依報名產生帳單與 QR Code"):
                    if scope in ["one_program", "one_category"] and not selected_value:
                        st.error("請先選擇課程或類別。")
                    elif scope in ["selected_enrollments"] and not selected_enrollment_ids:
                        st.error("請至少選擇一筆符合部門篩選的報名紀錄。")
                    else:
                        count = create_bills_from_enrollments(
                            billing_month,
                            due_date_program,
                            scope,
                            selected_value,
                            selected_enrollment_ids,
                            notes,
                        )
                        st.success(f"已建立 {count} 筆課程帳單。")
                        st.rerun()

    with st.expander("建立傳統月費帳單", expanded=False):
        if not students.empty and len(students[students["department"] == DEPARTMENT_UNKNOWN]) > 0:
            st.warning("有學生部門仍為待確認，系統不會將這些學生納入批次帳單。")
        with st.form("create_bills"):
            c1, c2, c3, c4 = st.columns(4)
            month = c1.text_input("月份", value=date.today().strftime("%Y-%m"))
            fee_item = c2.text_input("收費項目", value="月費")
            amount = c3.number_input("金額", min_value=0, value=8500, step=100)
            due_date = c4.date_input("繳費期限", value=date.today()).isoformat()
            c5, c6 = st.columns(2)
            scope_label = c5.selectbox("建立範圍", ["全部學生", "部門", "班級", "單一學生"])
            selected_value = ""
            scope = "全部學生"
            eligible = students[students["department"] != DEPARTMENT_UNKNOWN] if not students.empty and "department" in students.columns else students
            if scope_label == "部門":
                scope = "Department"
                selected_value = c6.selectbox("部門", [KINDERGARTEN, AFTER_SCHOOL])
            elif scope_label == "班級":
                scope = "單一班級"
                selected_value = c6.selectbox("班級", sorted(eligible["class_name"].dropna().unique().tolist()) if not eligible.empty else [])
            elif scope_label == "單一學生":
                scope = "單一學生"
                options = {f"{row.student_name}（{row.student_id} / {row.department}）": row.student_id for row in eligible.itertuples()}
                selected_label = c6.selectbox("學生", list(options.keys())) if options else ""
                selected_value = options.get(selected_label, "")
            notes = st.text_area("備註", height=80)
            if st.form_submit_button("產生帳單與 QR Code"):
                if scope_label in ["班級", "單一學生"] and not selected_value:
                    st.error("請選擇有效的班級或學生。")
                else:
                    count = create_bills(month, fee_item, int(amount), due_date, scope, selected_value, notes)
                    st.success(f"已建立 {count} 筆帳單。")
                    st.rerun()

    if bills.empty:
        st.info("尚無帳單。")
        return

    display = bills[
        [
            "bill_id",
            "department",
            "program_category",
            "program_name",
            "student_name",
            "class_name",
            "month",
            "fee_item",
            "total_amount",
            "paid_amount",
            "remaining_amount",
            "due_date",
            "grace_until_date",
            "payment_status",
            "last_payment_date",
            "receipt_number",
            "notes",
        ]
    ].rename(
        columns={
            "bill_id": "帳單編號",
            "department": "部門",
            "program_category": "課程類別",
            "program_name": "課程",
            "student_name": "學生姓名",
            "class_name": "班級",
            "month": "月份",
            "fee_item": "收費項目",
            "total_amount": "帳單金額",
            "paid_amount": "已記錄金額",
            "remaining_amount": "尚待確認金額",
            "due_date": "繳費期限",
            "grace_until_date": "寬限日期",
            "payment_status": "付款狀態",
            "last_payment_date": "最近付款日期",
            "receipt_number": "收據號碼",
            "notes": "備註",
        }
    )
    display["付款狀態"] = display["付款狀態"].replace(PAYMENT_STATUS_LABELS)
    display["資料確認狀態"] = display["部門"].apply(lambda value: "需確認" if value == DEPARTMENT_UNKNOWN else "已確認")
    st.dataframe(display, hide_index=True, use_container_width=True)

    st.subheader("帳單 QR Code 與付款資訊")
    settings = get_settings()
    payment_base_url = str(settings.get("payment_page_base_url") or "").strip().rstrip("/")
    bill_id = st.selectbox("選擇帳單", bills["bill_id"].tolist(), format_func=lambda x: f"{x} - {bills[bills['bill_id'] == x].iloc[0]['student_name']}")
    bill = bills[bills["bill_id"] == bill_id].iloc[0].to_dict()
    department_unconfirmed = is_student_department_unconfirmed(bill)
    if department_unconfirmed:
        st.warning(department_unconfirmed_message())
    if not payment_base_url and bill["status"] != PAID:
        st.warning("請先到管理設定填寫付款頁基礎網址，再產生正式 QR Code。")
    if bill["status"] == PAID:
        st.info("此帳單已完成繳費確認，QR 連結不再用於付款。")
    elif payment_base_url and not department_unconfirmed and (not bill.get("qr_path") or pd.isna(bill.get("qr_path")) or not Path(str(bill["qr_path"])).exists()):
        bill["qr_path"] = generate_qr_for_bill(bill)

    c1, c2 = st.columns([1, 2])
    with c1:
        if bill["status"] == PAID:
            st.info("已付款帳單不顯示付款 QR Code。")
        elif department_unconfirmed:
            st.info("學生部門確認前，系統不提供 QR Code 重新產生或顯示。")
        elif not payment_base_url:
            st.info("尚未設定付款頁基礎網址，因此不顯示正式 QR Code。")
        elif bill.get("qr_path") and not pd.isna(bill.get("qr_path")) and Path(str(bill["qr_path"])).exists():
            st.image(bill["qr_path"], caption="QR Code 已綁定一次性安全連結", width=230)
            st.caption(f"Token 狀態：{bill.get('qr_token_status') or 'active'}")
        else:
            st.info("尚未產生 QR Code。")
    with c2:
        st.markdown(f"**部門：** {bill.get('department', DEPARTMENT_UNKNOWN)}")
        if bill.get("program_name") and not pd.isna(bill.get("program_name")):
            st.markdown(f"**課程：** {bill.get('program_name')} / {bill.get('program_category')}")
        st.markdown(f"**帳單編號：** `{bill['bill_id']}`")
        st.markdown(f"**學生：** {bill['student_name']} / {bill['class_name']}")
        st.markdown(f"**收費項目：** {bill['fee_item']}")
        st.markdown(f"**帳單金額：** {money(bill.get('total_amount') or bill['amount'])}")
        st.markdown(f"**已記錄金額：** {money(bill.get('paid_amount') or 0)}")
        st.markdown(f"**尚待確認金額：** {money(bill.get('remaining_amount') if pd.notna(bill.get('remaining_amount')) else bill.get('amount'))}")
        st.markdown(f"**繳費期限：** {bill['due_date']}")
        st.markdown(f"**付款狀態：** {bill.get('payment_status') or STATUS_LABELS.get(bill['status'], bill['status'])}")
        st.markdown("**付款備註 / 轉帳附言**")
        st.code(payment_reference(bill), language="text")
        qr_token = str(bill.get("qr_token") or "").strip()
        if payment_base_url and qr_token:
            st.markdown("**管理端 QR 內容預覽**")
            st.code(parent_payment_url_for_token(qr_token), language="text")
        elif not payment_base_url:
            st.caption("設定付款頁基礎網址後，這裡會顯示 QR 實際編碼內容。")
        if st.button("重新產生 QR Code", disabled=department_unconfirmed or bill["status"] == PAID or not payment_base_url):
            try:
                regenerate_qr_for_bill(bill)
                st.success("QR Code 已重新產生，舊連結已失效。")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    st.subheader("付款更正 / 誤付款備註")
    with st.form("payment_correction_form"):
        note_type = st.selectbox("備註類型", ["誤付款", "重複付款", "金額錯誤", "家長備註錯誤", "人工更正紀錄", "退款/抵下月備註"])
        status_map = {"尚未完成繳費": UNPAID, "待對帳確認": PENDING, "已付款": PAID, "取消帳單": CANCELLED}
        reverse_status = {UNPAID: "尚未完成繳費", PENDING: "待對帳確認", PAID: "已付款", CANCELLED: "取消帳單"}
        new_status_label = st.selectbox("調整付款狀態", list(status_map.keys()), index=list(status_map.keys()).index(reverse_status.get(bill["status"], "待對帳確認")))
        new_payment_date = st.date_input("付款日期（僅已付款時使用）", value=date.today()).isoformat()
        correction_note = st.text_area("行政備註", height=100)
        if st.form_submit_button("儲存更正"):
            new_status = status_map[new_status_label]
            note_to_append = f"\n{note_type}（{date.today().isoformat()}）：{correction_note or '無'}"
            if new_status == PAID:
                try:
                    mark_bill_paid(bill["bill_id"], new_payment_date, (bill.get("notes") or "") + note_to_append)
                    log_audit("Manual correction made", "bill", bill["bill_id"], f"{note_type}: marked paid by admin.")
                    st.success("帳單已標記為已付款，系統會依規則產生或更新收據。")
                except Exception as exc:
                    st.error(str(exc))
            else:
                with connect() as conn:
                    conn.execute(
                        "UPDATE bills SET status = ?, payment_status = ?, payment_date = NULL, notes = COALESCE(notes, '') || ? WHERE bill_id = ?",
                        (new_status, new_status, note_to_append, bill["bill_id"]),
                    )
                log_audit("Manual correction made", "bill", bill["bill_id"], f"{note_type}: status changed to {new_status}.")
                st.success("已儲存更正紀錄。")
            st.rerun()

    st.subheader("付款紀錄")
    history = payment_history(bill["bill_id"])
    if history.empty:
        st.info("尚無付款紀錄。部分付款會先顯示在這裡，全額確認後才會產生正式數位收據。")
    else:
        history_display = history[
            ["payment_id", "transaction_id", "transaction_date", "amount", "payer_name", "payment_note", "match_status", "notes"]
        ].rename(
            columns={
                "payment_id": "付款紀錄 ID",
                "transaction_id": "交易編號",
                "transaction_date": "交易日期",
                "amount": "金額",
                "payer_name": "付款人",
                "payment_note": "付款備註",
                "match_status": "對帳狀態",
                "notes": "備註",
            }
        )
        st.dataframe(history_display, hide_index=True, use_container_width=True)

    st.subheader("繳費安排紀錄")
    arrangements = read_df("SELECT * FROM payment_arrangements WHERE bill_id = ? ORDER BY created_at DESC", (bill["bill_id"],))
    latest = arrangements.iloc[0].to_dict() if not arrangements.empty else {}
    with st.form("payment_arrangement_form"):
        arrangement_status = st.selectbox(
            "安排狀態",
            ARRANGEMENT_STATUSES,
            index=ARRANGEMENT_STATUSES.index(latest.get("arrangement_status")) if latest.get("arrangement_status") in ARRANGEMENT_STATUSES else 0,
        )
        c1, c2, c3 = st.columns(3)
        promised_payment_date = c1.date_input("約定補繳日", value=date.today()).isoformat()
        use_promised = c1.checkbox("設定約定補繳日", value=bool(latest.get("promised_payment_date")))
        grace_until_date = c2.date_input("寬限至", value=date.today()).isoformat()
        use_grace = c2.checkbox("設定寬限日期", value=bool(latest.get("grace_until_date")))
        handled_by = c3.text_input("處理人員", value=latest.get("handled_by") or "")
        arrangement_note = st.text_area("安排備註", value=latest.get("arrangement_note") or "", height=80)
        if st.form_submit_button("儲存繳費安排"):
            upsert_payment_arrangement(
                bill["bill_id"],
                arrangement_status,
                promised_payment_date if use_promised else None,
                grace_until_date if use_grace else None,
                arrangement_note,
                handled_by,
            )
            st.success("繳費安排已儲存。")
            st.rerun()
    if not arrangements.empty:
        st.dataframe(
            arrangements.rename(
                columns={
                    "arrangement_id": "安排 ID",
                    "arrangement_status": "安排狀態",
                    "promised_payment_date": "約定補繳日",
                    "grace_until_date": "寬限至",
                    "arrangement_note": "備註",
                    "handled_by": "處理人員",
                    "created_at": "建立時間",
                    "updated_at": "更新時間",
                }
            ),
            hide_index=True,
            use_container_width=True,
        )

    st.subheader("Audit Log")
    audit = read_df("SELECT event_type, entity_type, entity_id, message, created_at FROM audit_logs ORDER BY created_at DESC LIMIT 20")
    if audit.empty:
        st.info("尚無 audit log。")
    else:
        st.dataframe(
            audit.rename(columns={"event_type": "事件", "entity_type": "類型", "entity_id": "編號", "message": "訊息", "created_at": "時間"}),
            hide_index=True,
            use_container_width=True,
        )


def parent_bill_display(bill: dict) -> dict[str, str]:
    settings = get_settings()
    course = bill.get("program_name") if bill.get("program_name") and not pd.isna(bill.get("program_name")) else bill.get("fee_item")
    return {
        "school": settings["kindergarten_name"],
        "department": str(bill.get("department") or bill.get("program_category") or ""),
        "class_name": str(bill.get("class_name") or ""),
        "course": str(course or ""),
        "student": mask_student_name(bill.get("student_name")),
        "bill_id": str(bill.get("bill_id") or ""),
        "amount": money(bill.get("total_amount") or bill.get("amount")),
        "watermark": parent_watermark(bill, settings),
    }


def parent_record_text(bill: dict, record_type: str) -> bytes:
    display = parent_bill_display(bill)
    lines = [
        display["school"],
        record_type,
        "",
        f"部門：{display['department']}",
        f"班級：{display['class_name']}",
        f"課程 / 項目：{display['course']}",
        f"學生：{display['student']}",
        f"帳單編號：{display['bill_id']}",
        f"金額：{display['amount']}",
        f"付款狀態：{bill.get('payment_status') or STATUS_LABELS.get(bill.get('status'), bill.get('status'))}",
        f"付款確認時間：{bill.get('payment_date') or bill.get('last_payment_date') or ''}",
        f"收據產生時間：{bill.get('receipt_issue_date') or ''}",
        f"收據號碼：{bill.get('receipt_number') or ''}",
        "",
        "本文件供家長留存與園方對帳確認使用。",
        f"浮水印：{display['watermark']}",
    ]
    return "\n".join(lines).encode("utf-8-sig")


def render_parent_detail_cards(display: dict[str, str]) -> None:
    st.markdown('<div class="parent-detail-grid">', unsafe_allow_html=True)
    columns = st.columns(3)
    cards = [("學生", display["student"]), ("班級", display["class_name"]), ("金額", display["amount"])]
    for column, (label, value) in zip(columns, cards):
        with column:
            st.markdown(
                f"""
                <div class="parent-detail-card">
                    <div class="parent-detail-label">{html.escape(label)}</div>
                    <div class="parent-detail-value">{html.escape(str(value))}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    st.markdown("</div>", unsafe_allow_html=True)


def parent_header_card(display: dict[str, str]) -> str:
    return f"""
    <div class="parent-header-card">
        <div class="parent-page-title">家長繳費資訊確認</div>
        <div class="parent-watermark">{html.escape(display["watermark"])}</div>
    </div>
    """


def parent_reminder_card() -> str:
    return """
    <div class="parent-reminder-card">
        此頁面僅供家長確認繳費資訊使用。若您已付款，可能尚在對帳中；
        若近期需要繳費時間安排，請與園方聯繫，我們會協助確認。
    </div>
    """


def parent_payment_page() -> None:
    token = st.query_params.get("token")
    if not token:
        st.markdown('<div class="parent-page-shell">', unsafe_allow_html=True)
        st.markdown('<div class="parent-header-card"><div class="parent-page-title">家長繳費資訊確認</div></div>', unsafe_allow_html=True)
        st.info("請從園方提供的 QR Code 或繳費連結開啟。")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    bills = read_df(
        """
        SELECT bills.*, COALESCE(students.department, '') AS department,
               programs.program_name, programs.program_category
        FROM bills
        LEFT JOIN students ON bills.student_id = students.student_id
        LEFT JOIN programs ON bills.program_id = programs.program_id
        WHERE bills.qr_token = ?
        """,
        (token or "",),
    )
    if bills.empty:
        log_audit("invalid QR token access", "qr_token", token or "", "Invalid QR token accessed.")
        st.markdown('<div class="parent-page-shell">', unsafe_allow_html=True)
        st.markdown('<div class="parent-header-card"><div class="parent-page-title">家長繳費資訊確認</div></div>', unsafe_allow_html=True)
        st.error("此繳費連結已失效，請聯繫園方重新確認。")
        st.markdown("</div>", unsafe_allow_html=True)
        return
    bill = bills.iloc[0].to_dict()
    settings = get_settings()
    display = parent_bill_display(bill)
    token_status = str(bill.get("qr_token_status") or "active")
    expired_now = expire_overdue_token(bill)

    st.markdown('<div class="parent-page-shell">', unsafe_allow_html=True)
    st.markdown(parent_header_card(display), unsafe_allow_html=True)
    st.markdown(
        '<div class="parent-safety-notice">為保護資料安全，請勿轉傳此繳費連結或 QR Code。</div>',
        unsafe_allow_html=True,
    )
    st.markdown(parent_reminder_card(), unsafe_allow_html=True)

    if bill["status"] == PAID:
        log_audit("Paid bill QR accessed again", "bill", bill["bill_id"], f"Paid bill QR accessed again for {bill['bill_id']}.")
        st.success("此帳單已完成繳費確認，請勿重複付款。")
    elif expired_now or token_status in ["expired", "revoked"]:
        log_audit("Invalid or expired QR token accessed", "bill", bill["bill_id"], f"Inactive QR token accessed for {bill['bill_id']}.")
        st.error("此繳費連結已失效，請聯繫園方重新確認。")
        return
    elif token_status != "active":
        log_audit("blocked duplicate QR access", "bill", bill["bill_id"], f"Blocked QR access for token status {token_status}.")
        st.error("此繳費連結已失效，請聯繫園方重新確認。")
        return
    elif bill["status"] == CANCELLED or is_bill_stale(bill):
        st.error("此繳費連結已失效，請聯繫園方重新確認。")
        return
    else:
        log_audit("Parent payment page opened", "bill", bill["bill_id"], f"Parent payment page opened for {bill['bill_id']}.")

    if is_student_department_unconfirmed(bill):
        st.warning("此帳單資料仍在園方確認中，請先向園方確認最新繳費資訊。")

    payment_status = bill.get("payment_status") or STATUS_LABELS.get(bill["status"], bill["status"])
    if bill["status"] == PAID:
        log_audit("Confirmed payment page opened", "bill", bill["bill_id"], f"Confirmed payment page opened for {bill['bill_id']}.")
        st.write(f"付款確認時間：{bill.get('payment_date') or bill.get('last_payment_date') or '已確認'}")
        st.write(f"收據產生時間：{bill.get('receipt_issue_date') or '收據產生中'}")
        st.write(f"收據號碼：{bill.get('receipt_number') or '收據產生中'}")
    elif payment_status == "部分付款":
        st.info("系統已記錄部分付款。待款項全額確認後，將產生正式數位收據。")
    elif payment_status in ["寬限期中", "已約定補繳日"]:
        st.info("園方已記錄您的繳費安排，請依約定時間完成即可。如需調整，請與園方聯繫。")
    elif bill["status"] == PENDING or payment_status in ["待對帳確認", "金額需確認", "溢付款需處理"]:
        st.info("您的繳費資訊已送出，園方將於對帳後更新付款狀態。若您已完成轉帳，請保留交易紀錄以利查詢。")
    else:
        st.info("此項目目前尚未完成繳費確認。若您已付款，可能尚在對帳中；若需要延後繳費，請與園方聯繫。")

    render_parent_detail_cards(display)
    st.markdown(
        f"""
        <div class="parent-summary">
            <div><strong>課程 / 項目：</strong>{html.escape(display['course'])}</div>
            <div><strong>部門：</strong>{html.escape(display['department'])}</div>
            <div><strong>帳單編號：</strong>{html.escape(str(bill['bill_id']))}</div>
            <div><strong>繳費期限：</strong>{html.escape(str(bill['due_date']))}</div>
            <div><strong>狀態：</strong>{html.escape(str(payment_status))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="parent-safety-notice">請確認學生、班級、金額與帳單編號正確後再付款。</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <div class="parent-payment-section">
            <h3>付款說明</h3>
            <div>{html.escape(settings["bank_account_text"])}</div>
            <div style="margin-top:10px;">請透過幼兒園官方帳戶完成付款。本系統不處理、不代收、不保管任何款項。</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("**付款備註 / 轉帳附言**")
    st.code(payment_reference(bill), language="text")

    if bill["status"] == PAID:
        if not bill.get("receipt_path") or pd.isna(bill.get("receipt_path")) or not Path(str(bill.get("receipt_path"))).exists():
            try:
                generate_receipt_pdf(bill["bill_id"])
                refreshed = read_df("SELECT * FROM bills WHERE bill_id = ?", (bill["bill_id"],))
                if not refreshed.empty:
                    bill.update(refreshed.iloc[0].to_dict())
            except Exception:
                pass
        st.subheader("下載留存紀錄")
        if st.download_button("下載繳費明細", data=parent_record_text(bill, "繳費明細"), file_name=f"{bill['bill_id']}_payment_detail.txt", mime="text/plain"):
            log_audit("Parent downloads payment details", "bill", bill["bill_id"], f"Parent downloaded payment details for {bill['bill_id']}.")
        receipt_path = bill.get("receipt_path")
        if receipt_path and not pd.isna(receipt_path) and Path(str(receipt_path)).exists():
            if st.download_button("下載電子收據 PDF", data=Path(str(receipt_path)).read_bytes(), file_name=f"{bill.get('receipt_number') or bill['bill_id']}.pdf", mime="application/pdf"):
                log_audit("Parent downloads receipt PDF", "bill", bill["bill_id"], f"Parent downloaded receipt PDF for {bill['bill_id']}.")
        if st.download_button("下載對帳確認紀錄", data=parent_record_text(bill, "對帳確認紀錄"), file_name=f"{bill['bill_id']}_reconciliation_confirmation.txt", mime="text/plain"):
            log_audit("Parent downloads reconciliation confirmation record", "bill", bill["bill_id"], f"Parent downloaded reconciliation confirmation for {bill['bill_id']}.")
    st.markdown("</div>", unsafe_allow_html=True)


def reconciliation_page() -> None:
    st.title("CSV 匯入與自動對帳")
    st.caption("CSV 欄位：transaction_date, amount, payer_name, payment_note, transaction_id")
    st.download_button(
        "下載範例 CSV",
        data=Path(sample_bank_statement_path()).read_bytes(),
        file_name="sample_bank_statement.csv",
        mime="text/csv",
    )
    upload = st.file_uploader("上傳銀行/支付紀錄 CSV", type=["csv"])
    if upload:
        df = pd.read_csv(upload)
        st.subheader("匯入預覽")
        st.dataframe(df, hide_index=True, use_container_width=True)
        if st.button("開始對帳"):
            try:
                result = import_and_reconcile(df)
                display = table_status_chinese(result).rename(
                    columns={
                        "transaction_date": "交易日期",
                        "amount": "金額",
                        "payer_name": "付款人",
                        "payment_note": "付款備註",
                        "transaction_id": "交易編號",
                        "match_status": "對帳狀態",
                        "confidence": "信心度",
                        "matched_bill_id": "配對帳單",
                        "warning": "系統提示",
                    }
                )
                st.success("對帳完成。")
                st.dataframe(display, hide_index=True, use_container_width=True)
            except Exception as exc:
                st.error(str(exc))


def build_receipt_backup_zip() -> bytes:
    buffer = io.BytesIO()
    bills = read_df("SELECT * FROM bills")
    receipts = bills[bills["receipt_number"].notna()] if not bills.empty else bills
    transactions = read_df("SELECT * FROM transactions")
    payment_records = read_df("SELECT * FROM payment_records")
    payment_arrangements = read_df("SELECT * FROM payment_arrangements")
    programs = read_df("SELECT * FROM programs")
    enrollments = read_df("SELECT * FROM enrollments")
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        safe_bills = bills.drop(columns=[col for col in ["qr_path", "receipt_path"] if col in bills.columns], errors="ignore")
        safe_receipts = receipts.drop(columns=[col for col in ["qr_path", "receipt_path"] if col in receipts.columns], errors="ignore")
        zf.writestr("bills.csv", safe_bills.to_csv(index=False, encoding="utf-8-sig"))
        zf.writestr("receipts.csv", safe_receipts.to_csv(index=False, encoding="utf-8-sig"))
        zf.writestr("transactions.csv", transactions.to_csv(index=False, encoding="utf-8-sig"))
        zf.writestr("payment_records.csv", payment_records.to_csv(index=False, encoding="utf-8-sig"))
        zf.writestr("payment_arrangements.csv", payment_arrangements.to_csv(index=False, encoding="utf-8-sig"))
        zf.writestr("programs.csv", programs.to_csv(index=False, encoding="utf-8-sig"))
        zf.writestr("enrollments.csv", enrollments.to_csv(index=False, encoding="utf-8-sig"))
    return buffer.getvalue()


def receipt_preview_html(bill: dict) -> str:
    settings = get_settings()
    return f"""
    <div class="receipt-preview">
        <div class="receipt-header">
            <div class="receipt-title">{settings['kindergarten_name']}</div>
            <div class="receipt-subtitle">數位收據</div>
            <div>{settings['address']} ｜ 電話：{settings['contact_phone']}</div>
        </div>
        <div class="receipt-grid">
            <div class="receipt-label">收據號碼</div><div class="receipt-value">{bill.get('receipt_number') or '尚未產生'}</div>
            <div class="receipt-label">收據開立日期</div><div class="receipt-value">{bill.get('receipt_issue_date') or '尚未產生'}</div>
            <div class="receipt-label">付款日期</div><div class="receipt-value">{bill.get('payment_date') or ''}</div>
            <div class="receipt-label">繳費期限</div><div class="receipt-value">{bill.get('due_date') or ''}</div>
            <div class="receipt-label">學生姓名</div><div class="receipt-value">{mask_student_name(bill.get('student_name'))}</div>
            <div class="receipt-label">班級</div><div class="receipt-value">{bill.get('class_name') or ''}</div>
            <div class="receipt-label">課程</div><div class="receipt-value">{bill.get('program_name') if bill.get('program_name') and not pd.isna(bill.get('program_name')) else bill.get('fee_item') or ''}</div>
            <div class="receipt-label">課程類別</div><div class="receipt-value">{bill.get('program_category') if bill.get('program_category') and not pd.isna(bill.get('program_category')) else ''}</div>
            <div class="receipt-label">家長姓名</div><div class="receipt-value">{bill.get('parent_name') or ''}</div>
            <div class="receipt-label">收費項目</div><div class="receipt-value">{bill.get('fee_item') or ''}</div>
            <div class="receipt-label">金額</div><div class="receipt-value">{money(bill.get('amount'))}</div>
            <div class="receipt-label">付款方式</div><div class="receipt-value">園方官方帳戶轉帳/匯款</div>
            <div class="receipt-label">備註</div><div class="receipt-value">{bill.get('notes') or ''}</div>
            <div class="receipt-label">經手人</div><div class="receipt-value">{settings['responsible_person']}</div>
        </div>
        <div class="seal-box">園方章</div>
        <p><strong>本文件為數位收據，非統一發票。</strong></p>
        <p>本收據供家長留存與園方對帳使用。</p>
        <p class="calm-note">{parent_watermark(bill, settings)}</p>
    </div>
    """


def receipts_page() -> None:
    st.title("數位收據")
    st.info("只有已付款帳單可以產生收據，未付款或待確認帳單不得開立收據。")
    render_responsibility_panel()

    bills = read_df(
        """
        SELECT bills.*, COALESCE(students.department, '待確認') AS department,
               programs.program_name, programs.program_category
        FROM bills
        LEFT JOIN students ON bills.student_id = students.student_id
        LEFT JOIN programs ON bills.program_id = programs.program_id
        ORDER BY bills.created_at DESC
        """
    )
    if bills.empty:
        st.info("尚無帳單。")
        return

    c1, c2, c3, c4 = st.columns(4)
    keyword = c1.text_input("搜尋學生 / 收據號碼 / 帳單編號")
    month = c2.selectbox("月份", ["全部"] + sorted(bills["month"].dropna().unique().tolist()))
    status = c3.selectbox("付款狀態", ["全部", PAID, UNPAID, PENDING], format_func=lambda x: STATUS_LABELS.get(x, x))
    c4.download_button("匯出備份 ZIP", data=build_receipt_backup_zip(), file_name=f"kindergarten_backup_{date.today().isoformat()}.zip", mime="application/zip")

    filtered = bills.copy()
    if keyword:
        mask = (
            filtered["student_name"].astype(str).str.contains(keyword, case=False, na=False)
            | filtered["bill_id"].astype(str).str.contains(keyword, case=False, na=False)
            | filtered["receipt_number"].astype(str).str.contains(keyword, case=False, na=False)
        )
        filtered = filtered[mask]
    if month != "全部":
        filtered = filtered[filtered["month"] == month]
    if status != "全部":
        filtered = filtered[filtered["status"] == status]

    display = filtered[
        ["bill_id", "student_name", "class_name", "program_name", "month", "fee_item", "amount", "status", "payment_date", "receipt_issue_date", "receipt_number"]
    ].rename(
        columns={
            "bill_id": "帳單編號",
            "student_name": "學生姓名",
            "class_name": "班級",
            "program_name": "課程",
            "month": "月份",
            "fee_item": "收費項目",
            "amount": "金額",
            "status": "付款狀態",
            "payment_date": "付款日期",
            "receipt_issue_date": "收據開立日期",
            "receipt_number": "收據號碼",
        }
    )
    display["付款狀態"] = display["付款狀態"].replace(STATUS_LABELS)
    display["收據狀態"] = display["收據號碼"].apply(lambda value: "已產生" if pd.notna(value) and str(value) else "尚未產生")
    display["資料確認狀態"] = filtered["department"].apply(lambda value: "需確認" if value == DEPARTMENT_UNKNOWN else "已確認").values
    st.dataframe(display, hide_index=True, use_container_width=True)

    bill_id = st.selectbox("預覽 / 下載收據", filtered["bill_id"].tolist(), format_func=lambda x: f"{x} - {filtered[filtered['bill_id'] == x].iloc[0]['student_name']}")
    bill = filtered[filtered["bill_id"] == bill_id].iloc[0].to_dict()
    if is_student_department_unconfirmed(bill):
        st.warning(department_unconfirmed_message())
        st.markdown(receipt_preview_html(bill), unsafe_allow_html=True)
        return
    if bill["status"] != PAID:
        st.warning("此帳單尚未完成繳費確認，因此不能產生正式數位收據。")
        st.markdown(receipt_preview_html(bill), unsafe_allow_html=True)
        return

    c1, c2 = st.columns(2)
    with c1:
        if st.button("產生 / 重新產生收據 PDF"):
            try:
                path = generate_receipt_pdf(bill_id)
                st.success("收據 PDF 已產生。")
                bill["receipt_path"] = path
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    with c2:
        receipt_path = bill.get("receipt_path")
        if receipt_path and not pd.isna(receipt_path) and Path(str(receipt_path)).exists():
            st.download_button(
                "下載 PDF 收據",
                data=Path(str(receipt_path)).read_bytes(),
                file_name=f"{bill.get('receipt_number') or bill_id}.pdf",
                mime="application/pdf",
            )
        else:
            st.info("尚未產生 PDF。")

    st.markdown(receipt_preview_html(bill), unsafe_allow_html=True)


def settings_page() -> None:
    st.title("管理設定")
    settings = get_settings()
    with st.form("settings"):
        kindergarten_name = st.text_input("園所名稱", settings["kindergarten_name"])
        address = st.text_input("地址", settings["address"])
        contact_phone = st.text_input("聯絡電話", settings["contact_phone"])
        receipt_prefix = st.text_input("收據編號前綴", settings["receipt_prefix"])
        bank_account_text = st.text_area("銀行 / 支付帳戶顯示文字", settings["bank_account_text"], height=100)
        receipt_footer_text = st.text_area("收據頁尾文字", settings["receipt_footer_text"], height=80)
        responsible_person = st.text_input("經手人 / 負責人顯示文字", settings["responsible_person"])
        payment_page_base_url = st.text_input("付款頁 Base URL（Streamlit Cloud 網址，正式 QR Code 必填）", settings.get("payment_page_base_url", ""))
        privacy_mode = st.selectbox(
            "隱私模式",
            ["standard", "admin_full"],
            index=["standard", "admin_full"].index(settings.get("privacy_mode", "standard")) if settings.get("privacy_mode", "standard") in ["standard", "admin_full"] else 0,
            format_func=lambda value: "標準隱私模式（家長端遮蔽姓名）" if value == "standard" else "管理端完整檢視",
        )
        default_qr_token_valid_days = st.number_input("預設 QR token 有效天數（無繳費期限時使用）", min_value=1, max_value=90, value=int(settings.get("default_qr_token_valid_days", "14") or 14), step=1)
        st.file_uploader("Logo 上傳預留欄位（V1 僅預留，不儲存）", type=["png", "jpg", "jpeg"])
        if st.form_submit_button("儲存設定"):
            old_privacy_mode = settings.get("privacy_mode", "standard")
            save_settings(
                {
                    "kindergarten_name": kindergarten_name,
                    "address": address,
                    "contact_phone": contact_phone,
                    "receipt_prefix": receipt_prefix,
                    "bank_account_text": bank_account_text,
                    "receipt_footer_text": receipt_footer_text,
                    "responsible_person": responsible_person,
                    "payment_page_base_url": payment_page_base_url,
                    "privacy_mode": privacy_mode,
                    "default_qr_token_valid_days": str(default_qr_token_valid_days),
                }
            )
            if old_privacy_mode != privacy_mode:
                log_audit("Admin switches between masked/full-name view", "settings", "privacy_mode", f"Privacy mode changed from {old_privacy_mode} to {privacy_mode}.")
            st.success("設定已儲存。")
            st.rerun()
    st.subheader("系統維護")
    st.caption("這個動作會檢查帳單 QR token 與缺漏 QR 圖檔。為避免 SQLite 在 Streamlit rerun 時被重複寫入，系統不會在每次啟動時自動執行。")
    if st.button("修復 / 補齊 QR Token"):
        try:
            ensure_all_qr_codes()
            st.success("QR Token 修復 / 補齊已完成。")
        except Exception as exc:
            st.error(str(exc))
    st.download_button("匯出帳單、收據與付款紀錄備份", data=build_receipt_backup_zip(), file_name=f"kindergarten_backup_{date.today().isoformat()}.zip", mime="application/zip")
    render_responsibility_panel()


def main() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    seed_sample_data()
    classify_existing_students()

    page_options = {
        "儀表板": dashboard_page,
        "學生管理": students_page,
        "課程與收費項目管理": programs_page,
        "學生課程報名管理": enrollments_page,
        "繳費帳單": bills_page,
        "家長繳費頁 Mockup": parent_payment_page,
        "CSV 匯入與自動對帳": reconciliation_page,
        "數位收據": receipts_page,
        "管理設定": settings_page,
    }
    query_page = st.query_params.get("page")
    default_page = "家長繳費頁 Mockup" if query_page == "parent" else "儀表板"
    with st.sidebar:
        st.header("Kindergarten QR Payment")
        page = st.radio("功能選單", list(page_options.keys()), index=list(page_options.keys()).index(default_page))
        st.divider()
        st.caption("V1：繳費紀錄、QR 資訊頁、CSV 對帳、數位收據。")
    page_options[page]()


if __name__ == "__main__":
    main()
