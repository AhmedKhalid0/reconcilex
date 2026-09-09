"""
ReconcileX Accountant Web Dashboard & REST API.
Built with FastAPI, strictly adhering to the human-crafted-ui-ux design standards.
Zero Node.js runtime required; runs 100% out of the box.
"""

from datetime import datetime, timezone
import io
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
from typing import List, Optional
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from reconcilex import __version__
from reconcilex.config import settings
from reconcilex.core.agent.reconciler_agent import ReconcilerAgent
from reconcilex.core.extraction.ocr_engine import OCREngine
from reconcilex.core.extraction.pdf_parser import PDFInvoiceParser
from reconcilex.core.extraction.statement_parser import BankStatementParser
from reconcilex.core.matching.matcher import DeterministicMatcher
from reconcilex.core.models import (
    BankTransaction,
    InvoiceRecord,
    MatchResult,
    MatchStatus,
    MatchType,
    ReconciliationReport,
    ReconciliationSummary,
)
from reconcilex.core.reporting.excel_exporter import ExcelReportExporter
from reconcilex.core.reporting.journal_voucher import JournalVoucherGenerator
from reconcilex.core.reporting.markdown_exporter import MarkdownReportExporter
from reconcilex.core.tax_audit import TaxAuditor
from reconcilex.utils.sample_generator import SampleDataGenerator

logger = logging.getLogger("reconcilex.web")

app = FastAPI(
    title="ReconcileX",
    description="Privacy-First Hybrid Financial Reconciliation Platform",
    version=__version__,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store for web dashboard
_WEB_SESSION = {
    "invoices": {},
    "transactions": {},
    "report": None,
}


@app.get("/api/status")
async def get_status():
    report = _WEB_SESSION["report"]
    return {
        "status": "ready",
        "version": __version__,
        "llm_provider": settings.llm_provider,
        "invoices_loaded": len(_WEB_SESSION["invoices"]),
        "transactions_loaded": len(_WEB_SESSION["transactions"]),
        "has_active_report": report is not None,
        "summary": report.summary.model_dump() if report else None,
    }


@app.post("/api/load-sample")
async def load_sample():
    """Generate and load realistic sample dataset directly into session."""
    temp_dir = Path(tempfile.mkdtemp(prefix="reconcilex_sample_"))
    inv_dir, stmt_file = SampleDataGenerator.generate_all(temp_dir)

    _WEB_SESSION["invoices"].clear()
    _WEB_SESSION["transactions"].clear()

    # Load invoices
    for f in inv_dir.iterdir():
        if f.suffix.lower() == ".pdf":
            rec = PDFInvoiceParser.extract(f)
            _WEB_SESSION["invoices"][rec.doc_id] = rec
        elif f.suffix.lower() in [".png", ".jpg", ".jpeg"]:
            rec = OCREngine.extract_image_invoice(f)
            _WEB_SESSION["invoices"][rec.doc_id] = rec

    # Load statement
    tx_list = BankStatementParser.parse(stmt_file)
    for tx in tx_list:
        _WEB_SESSION["transactions"][tx.tx_id] = tx

    # Run auto-reconciliation
    matcher = DeterministicMatcher()
    report = matcher.reconcile(
        list(_WEB_SESSION["invoices"].values()),
        list(_WEB_SESSION["transactions"].values())
    )
    # Run agent resolution
    agent = ReconcilerAgent()
    report = agent.resolve_edge_cases(report)
    _WEB_SESSION["report"] = report

    return {
        "status": "success",
        "message": "Sample dataset generated and reconciled successfully!",
        "invoices_count": len(_WEB_SESSION["invoices"]),
        "transactions_count": len(_WEB_SESSION["transactions"]),
        "summary": report.summary.model_dump()
    }


@app.post("/api/upload")
async def upload_files(
    invoices: List[UploadFile] = File([]),
    statement: Optional[UploadFile] = File(None),
):
    """Upload invoice documents and bank statement files."""
    upload_dir = Path("./data/uploads") / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    upload_dir.mkdir(parents=True, exist_ok=True)

    loaded_inv_count = 0
    loaded_tx_count = 0

    for inv_file in invoices:
        if not inv_file.filename:
            continue
        dest = upload_dir / inv_file.filename
        with open(dest, "wb") as f:
            f.write(await inv_file.read())

        ext = dest.suffix.lower()
        if ext == ".pdf":
            try:
                rec = PDFInvoiceParser.extract(dest)
                _WEB_SESSION["invoices"][rec.doc_id] = rec
                loaded_inv_count += 1
            except Exception as e:
                logger.error(f"Failed to parse PDF {inv_file.filename}: {e}")
        elif ext in [".png", ".jpg", ".jpeg", ".webp"]:
            try:
                rec = OCREngine.extract_image_invoice(dest)
                _WEB_SESSION["invoices"][rec.doc_id] = rec
                loaded_inv_count += 1
            except Exception as e:
                logger.error(f"Failed to parse Image {inv_file.filename}: {e}")

    if statement and statement.filename:
        stmt_dest = upload_dir / statement.filename
        with open(stmt_dest, "wb") as f:
            f.write(await statement.read())
        try:
            tx_list = BankStatementParser.parse(stmt_dest)
            for tx in tx_list:
                _WEB_SESSION["transactions"][tx.tx_id] = tx
                loaded_tx_count += 1
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse bank statement: {e}")

    return {
        "status": "success",
        "invoices_loaded": len(_WEB_SESSION["invoices"]),
        "transactions_loaded": len(_WEB_SESSION["transactions"]),
        "new_invoices": loaded_inv_count,
        "new_transactions": loaded_tx_count,
    }


@app.post("/api/reconcile")
async def reconcile(
    tolerance_days: int = 3,
    vendor_threshold: float = 70.0,
    fee_tolerance: float = 25.0,
    max_bundle: int = 4,
    enable_ai: bool = True,
):
    """Run full reconciliation pipeline with configurable parameters."""
    invoices = list(_WEB_SESSION["invoices"].values())
    transactions = list(_WEB_SESSION["transactions"].values())

    if not invoices or not transactions:
        raise HTTPException(
            status_code=400,
            detail="Both invoices and bank transactions must be uploaded before reconciling."
        )

    matcher = DeterministicMatcher(
        date_tolerance=tolerance_days,
        vendor_threshold=vendor_threshold,
        fee_tolerance=fee_tolerance,
        max_bundle=max_bundle,
    )
    report = matcher.reconcile(invoices, transactions)

    if enable_ai and (report.unmatched_invoices or report.unmatched_transactions):
        agent = ReconcilerAgent()
        report = agent.resolve_edge_cases(report)

    _WEB_SESSION["report"] = report
    return {
        "status": "success",
        "summary": report.summary.model_dump(),
        "matched_count": len(report.matched_pairs),
        "unmatched_invoices_count": len(report.unmatched_invoices),
        "unmatched_transactions_count": len(report.unmatched_transactions),
    }


@app.get("/api/results")
async def get_results():
    report = _WEB_SESSION["report"]
    if not report:
        return {"report": None}
    return {"report": report.model_dump()}


class ResolveRequest(BaseModel):
    invoice_id: str
    tx_id: str
    reasoning: str


@app.post("/api/resolve")
async def manual_resolve(req: ResolveRequest):
    """Single 1-to-1 manual override."""
    report: ReconciliationReport = _WEB_SESSION["report"]
    if not report:
        raise HTTPException(status_code=400, detail="No active reconciliation report.")

    inv = _WEB_SESSION["invoices"].get(req.invoice_id)
    tx = _WEB_SESSION["transactions"].get(req.tx_id)
    if not inv or not tx:
        raise HTTPException(status_code=404, detail="Invoice or Transaction not found.")

    variance = round(tx.amount - inv.total_amount, 2)
    match_item = MatchResult(
        match_status=MatchStatus.MATCHED,
        match_type=MatchType.MANUAL_OVERRIDE,
        invoice_ids=[inv.doc_id],
        tx_id=tx.tx_id,
        invoice_total=inv.total_amount,
        bank_amount=tx.amount,
        variance_amount=variance,
        confidence_score=1.0,
        rule_applied="MANUAL_ACCOUNTANT_RESOLUTION",
        audit_reasoning=f"[Accountant Override] {req.reasoning}",
    )

    report.matched_pairs.append(match_item)
    report.unmatched_invoices = [i for i in report.unmatched_invoices if i.doc_id != req.invoice_id]
    report.unmatched_transactions = [t for t in report.unmatched_transactions if t.tx_id != req.tx_id]

    # Recalculate KPIs
    report.summary.matched_count = len(report.matched_pairs)
    report.summary.unmatched_invoices_count = len(report.unmatched_invoices)
    report.summary.unmatched_transactions_count = len(report.unmatched_transactions)
    report.summary.matched_amount = round(sum(m.bank_amount for m in report.matched_pairs), 2)
    if report.summary.total_invoices > 0:
        report.summary.match_rate_percentage = min(
            round((len(report.matched_pairs) / report.summary.total_invoices) * 100.0, 1),
            100.0
        )
    report.summary.net_variance = round(sum(m.variance_amount for m in report.matched_pairs), 2)

    return {"status": "success", "summary": report.summary.model_dump()}


class BatchResolveRequest(BaseModel):
    invoice_ids: List[str]
    tx_ids: List[str]
    reasoning: str


@app.post("/api/manual-batch-match")
async def manual_batch_match(req: BatchResolveRequest):
    """Batch manual match for Split-Ledger multi-selection."""
    report: ReconciliationReport = _WEB_SESSION["report"]
    if not report:
        raise HTTPException(status_code=400, detail="No active reconciliation report.")

    inv_list = [_WEB_SESSION["invoices"][iid] for iid in req.invoice_ids if iid in _WEB_SESSION["invoices"]]
    tx_list = [_WEB_SESSION["transactions"][tid] for tid in req.tx_ids if tid in _WEB_SESSION["transactions"]]

    if not inv_list and not tx_list:
        raise HTTPException(status_code=400, detail="Must provide at least one valid invoice or bank transaction.")

    inv_total = round(sum(i.total_amount for i in inv_list), 2)
    bank_total = round(sum(t.amount for t in tx_list), 2)
    variance = round(bank_total - inv_total, 2)
    primary_tx = ", ".join(t.tx_id for t in tx_list) if tx_list else "NONE"

    match_item = MatchResult(
        match_status=MatchStatus.MATCHED,
        match_type=MatchType.MANUAL_OVERRIDE,
        invoice_ids=[i.doc_id for i in inv_list],
        tx_id=primary_tx,
        invoice_total=inv_total,
        bank_amount=bank_total,
        variance_amount=variance,
        confidence_score=1.0,
        rule_applied="MANUAL_BATCH_SPLIT_LEDGER",
        audit_reasoning=f"[Split-Ledger Manual Match] {req.reasoning} ({len(inv_list)} invoices to {len(tx_list)} bank feeds)",
    )

    report.matched_pairs.append(match_item)
    matched_inv_ids = set(req.invoice_ids)
    matched_tx_ids = set(req.tx_ids)
    report.unmatched_invoices = [i for i in report.unmatched_invoices if i.doc_id not in matched_inv_ids]
    report.unmatched_transactions = [t for t in report.unmatched_transactions if t.tx_id not in matched_tx_ids]

    # Recalculate KPIs
    report.summary.matched_count = len(report.matched_pairs)
    report.summary.unmatched_invoices_count = len(report.unmatched_invoices)
    report.summary.unmatched_transactions_count = len(report.unmatched_transactions)
    report.summary.matched_amount = round(sum(m.bank_amount for m in report.matched_pairs), 2)
    if report.summary.total_invoices > 0:
        report.summary.match_rate_percentage = min(
            round((len(report.matched_pairs) / report.summary.total_invoices) * 100.0, 1),
            100.0
        )
    report.summary.net_variance = round(sum(m.variance_amount for m in report.matched_pairs), 2)

    return {
        "status": "success",
        "message": f"Successfully linked {len(inv_list)} invoice(s) with {len(tx_list)} bank feed(s)",
        "match": match_item.model_dump(),
        "summary": report.summary.model_dump()
    }


@app.get("/api/document/{doc_id}")
async def get_document_details(doc_id: str):
    """Retrieve full document details, line items, and run real-time Tax/VAT audit."""
    inv = _WEB_SESSION["invoices"].get(doc_id)
    if not inv:
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found.")

    tax_audit = TaxAuditor.audit_invoice(inv)
    return {
        "doc_id": inv.doc_id,
        "vendor_name": inv.vendor_name,
        "vendor_tax_id": inv.vendor_tax_id,
        "invoice_date": inv.invoice_date.isoformat(),
        "due_date": inv.due_date.isoformat() if inv.due_date else None,
        "subtotal": inv.subtotal,
        "tax_amount": inv.tax_amount,
        "total_amount": inv.total_amount,
        "currency": inv.currency,
        "items": [li.model_dump() for li in inv.items],
        "source_path": inv.source_path,
        "raw_text": inv.raw_text[:3000] if inv.raw_text else "",
        "tax_audit": tax_audit.model_dump(),
    }


@app.get("/api/analytics")
async def get_analytics():
    """Retrieve statistical distributions and settlement timeline for visualization."""
    report = _WEB_SESSION["report"]
    if not report:
        return {
            "status": "empty",
            "match_type_distribution": {},
            "settlement_timeline": [],
            "tax_compliance_summary": {"audited": 0, "compliant": 0, "issues": 0},
        }

    dist = {}
    for m in report.matched_pairs:
        m_type = m.match_type.value if hasattr(m.match_type, "value") else str(m.match_type)
        dist[m_type] = dist.get(m_type, 0) + 1

    timeline_events = []
    for inv in _WEB_SESSION["invoices"].values():
        timeline_events.append({
            "date": inv.invoice_date.isoformat(),
            "type": "INVOICE",
            "amount": inv.total_amount,
            "label": inv.vendor_name,
        })
    for tx in _WEB_SESSION["transactions"].values():
        timeline_events.append({
            "date": tx.tx_date.isoformat(),
            "type": "BANK_CLEARING",
            "amount": tx.amount,
            "label": tx.counterparty,
        })
    timeline_events.sort(key=lambda x: x["date"])

    compliant_count = 0
    issue_count = 0
    for inv in _WEB_SESSION["invoices"].values():
        t_audit = TaxAuditor.audit_invoice(inv)
        if t_audit.is_compliant:
            compliant_count += 1
        else:
            issue_count += 1

    return {
        "status": "ready",
        "match_type_distribution": dist,
        "settlement_timeline": timeline_events,
        "tax_compliance_summary": {
            "audited": len(_WEB_SESSION["invoices"]),
            "compliant": compliant_count,
            "issues": issue_count,
        }
    }


@app.get("/api/export/excel")
async def export_excel():
    report = _WEB_SESSION["report"]
    if not report:
        raise HTTPException(status_code=400, detail="No active reconciliation report to export.")

    out_file = Path("./data/exports/ReconcileX_Report.xlsx")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    ExcelReportExporter.export(report, out_file)

    with open(out_file, "rb") as f:
        content = f.read()

    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=ReconcileX_Audit_{datetime.now(timezone.utc).strftime('%Y%m%d')}.xlsx"}
    )


@app.get("/api/export/markdown")
async def export_markdown():
    report = _WEB_SESSION["report"]
    if not report:
        raise HTTPException(status_code=400, detail="No active report to export.")

    md = MarkdownReportExporter.export(report)
    return Response(
        content=md,
        media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename=ReconcileX_Audit_{datetime.now(timezone.utc).strftime('%Y%m%d')}.md"}
    )


@app.get("/api/export/journal-voucher")
async def export_journal_voucher():
    report = _WEB_SESSION["report"]
    if not report:
        raise HTTPException(status_code=400, detail="No active reconciliation report to generate journal vouchers.")

    entries = JournalVoucherGenerator.generate_voucher(report)
    csv_content = JournalVoucherGenerator.export_csv(entries)

    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=ReconcileX_Journal_Voucher_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"}
    )


@app.get("/", response_class=HTMLResponse)
async def dashboard_view():
    return HTML_DASHBOARD_TEMPLATE


# ==============================================================================
# Human-Crafted Financial UI Template (Light Default + Dark Toggle, Zero AI Slop)
# ==============================================================================
HTML_DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ReconcileX | Financial Reconciliation Platform</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root {
      --bg-base: #f8fafc;
      --bg-surface: #ffffff;
      --bg-muted: #f1f5f9;
      --border: #e2e8f0;
      --border-focus: #cbd5e1;
      --text-primary: #0f172a;
      --text-secondary: #475569;
      --text-muted: #94a3b8;
      --accent: #2563eb;
      --accent-hover: #1d4ed8;
      --accent-subtle: #eff6ff;
      --success: #16a34a;
      --success-bg: #f0fdf4;
      --success-border: #bbf7d0;
      --warning: #ca8a04;
      --warning-bg: #fefce8;
      --warning-border: #fef08a;
      --danger: #dc2626;
      --danger-bg: #fef2f2;
      --danger-border: #fecaca;
      --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
      --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.07), 0 2px 4px -2px rgba(0, 0, 0, 0.05);
      --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -4px rgba(0, 0, 0, 0.1);
      --radius: 8px;
    }

    [data-theme="dark"] {
      --bg-base: #09090b;
      --bg-surface: #121214;
      --bg-muted: #18181b;
      --border: #27272a;
      --border-focus: #3f3f46;
      --text-primary: #f4f4f5;
      --text-secondary: #a1a1aa;
      --text-muted: #71717a;
      --accent: #3b82f6;
      --accent-hover: #2563eb;
      --accent-subtle: rgba(59, 130, 246, 0.1);
      --success: #22c55e;
      --success-bg: rgba(34, 197, 94, 0.1);
      --success-border: rgba(34, 197, 94, 0.2);
      --warning: #eab308;
      --warning-bg: rgba(234, 179, 8, 0.1);
      --warning-border: rgba(234, 179, 8, 0.2);
      --danger: #ef4444;
      --danger-bg: rgba(239, 68, 68, 0.1);
      --danger-border: rgba(239, 68, 68, 0.2);
      --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.4);
      --shadow-md: 0 4px 12px 0 rgba(0, 0, 0, 0.4);
      --shadow-lg: 0 12px 24px 0 rgba(0, 0, 0, 0.5);
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background-color: var(--bg-base);
      color: var(--text-primary);
      line-height: 1.5;
      transition: background-color 0.2s, color 0.2s;
      min-height: 100vh;
      overflow-x: hidden;
    }

    .num {
      font-family: 'JetBrains Mono', monospace;
      font-variant-numeric: tabular-nums;
    }

    /* Navigation */
    header {
      background-color: var(--bg-surface);
      border-bottom: 1px solid var(--border);
      position: sticky;
      top: 0;
      z-index: 40;
      padding: 0.75rem 1.5rem;
    }
    .header-inner {
      max-width: 1440px;
      margin: 0 auto;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      text-decoration: none;
      color: var(--text-primary);
    }
    .brand-icon {
      width: 34px;
      height: 34px;
      border-radius: var(--radius);
      background: var(--accent);
      color: #fff;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 700;
      font-size: 1.05rem;
    }
    .brand-title {
      font-weight: 700;
      font-size: 1.15rem;
      letter-spacing: -0.02em;
    }
    .brand-tag {
      font-size: 0.75rem;
      font-weight: 600;
      padding: 2px 6px;
      background: var(--bg-muted);
      color: var(--text-secondary);
      border-radius: 4px;
      border: 1px solid var(--border);
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 0.6rem;
    }

    /* Buttons */
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 0.4rem;
      padding: 0.5rem 0.9rem;
      border-radius: var(--radius);
      font-size: 0.85rem;
      font-weight: 500;
      cursor: pointer;
      border: 1px solid transparent;
      transition: all 0.15s ease;
      text-decoration: none;
    }
    .btn-primary {
      background-color: var(--accent);
      color: #ffffff;
    }
    .btn-primary:hover { background-color: var(--accent-hover); }
    .btn-secondary {
      background-color: var(--bg-surface);
      color: var(--text-secondary);
      border-color: var(--border);
    }
    .btn-secondary:hover {
      background-color: var(--bg-muted);
      color: var(--text-primary);
      border-color: var(--border-focus);
    }
    .btn-sm { padding: 0.25rem 0.55rem; font-size: 0.8rem; }

    /* Layout */
    .container {
      max-width: 1440px;
      margin: 1.5rem auto;
      padding: 0 1.5rem 5rem;
    }

    /* Cards */
    .card {
      background-color: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      box-shadow: var(--shadow-sm);
      overflow: hidden;
      margin-bottom: 1.5rem;
    }
    .card-header {
      padding: 0.9rem 1.25rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .card-title {
      font-size: 0.95rem;
      font-weight: 600;
      color: var(--text-primary);
    }
    .card-body { padding: 1.25rem; }

    /* KPI Grid */
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
    }
    .kpi-card {
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 1.2rem;
      box-shadow: var(--shadow-sm);
    }
    .kpi-label {
      font-size: 0.72rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-muted);
      margin-bottom: 0.35rem;
    }
    .kpi-value {
      font-size: 1.65rem;
      font-weight: 700;
      color: var(--text-primary);
    }
    .kpi-sub {
      font-size: 0.8rem;
      color: var(--text-secondary);
      margin-top: 0.25rem;
    }

    /* Charts Row */
    .charts-grid {
      display: grid;
      grid-template-columns: 1fr 2fr;
      gap: 1rem;
      margin-bottom: 1.5rem;
    }
    @media (max-width: 900px) {
      .charts-grid { grid-template-columns: 1fr; }
    }

    /* Upload & Setup Zone */
    .setup-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1.25rem;
      margin-bottom: 1.25rem;
    }
    .drop-zone {
      border: 2px dashed var(--border);
      border-radius: var(--radius);
      padding: 1.5rem;
      text-align: center;
      background: var(--bg-muted);
      cursor: pointer;
      transition: all 0.2s;
    }
    .drop-zone:hover {
      border-color: var(--accent);
      background: var(--accent-subtle);
    }
    .drop-icon { font-size: 1.8rem; margin-bottom: 0.4rem; }
    .drop-title { font-weight: 600; font-size: 0.9rem; margin-bottom: 0.2rem; }
    .drop-desc { font-size: 0.78rem; color: var(--text-muted); }

    /* Tables */
    .table-container {
      overflow-x: auto;
      border-radius: var(--radius);
      border: 1px solid var(--border);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      text-align: left;
      font-size: 0.85rem;
    }
    th {
      background: var(--bg-muted);
      color: var(--text-secondary);
      font-weight: 600;
      padding: 0.7rem 0.9rem;
      border-bottom: 1px solid var(--border);
      white-space: nowrap;
    }
    td {
      padding: 0.7rem 0.9rem;
      border-bottom: 1px solid var(--border);
      color: var(--text-primary);
    }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background-color: var(--bg-muted); }

    /* Badges */
    .badge {
      display: inline-flex;
      align-items: center;
      padding: 2px 7px;
      border-radius: 9999px;
      font-size: 0.72rem;
      font-weight: 600;
      white-space: nowrap;
    }
    .badge-success { background: var(--success-bg); color: var(--success); border: 1px solid var(--success-border); }
    .badge-warning { background: var(--warning-bg); color: var(--warning); border: 1px solid var(--warning-border); }
    .badge-danger { background: var(--danger-bg); color: var(--danger); border: 1px solid var(--danger-border); }
    .badge-info { background: var(--accent-subtle); color: var(--accent); border: 1px solid var(--border-focus); }
    .badge-neutral { background: var(--bg-muted); color: var(--text-secondary); border: 1px solid var(--border); }

    /* Navigation Tabs */
    .nav-tabs {
      display: flex;
      gap: 0.4rem;
      border-bottom: 1px solid var(--border);
      margin-bottom: 1.25rem;
      overflow-x: auto;
    }
    .tab-btn {
      padding: 0.6rem 1rem;
      font-size: 0.85rem;
      font-weight: 500;
      color: var(--text-secondary);
      border: none;
      background: none;
      border-bottom: 2px solid transparent;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      white-space: nowrap;
    }
    .tab-btn:hover { color: var(--text-primary); }
    .tab-btn.active {
      color: var(--accent);
      border-bottom-color: var(--accent);
      font-weight: 600;
    }
    .tab-badge {
      background: var(--bg-muted);
      font-size: 0.75rem;
      padding: 1px 6px;
      border-radius: 9999px;
    }

    /* Interactive Split Ledger */
    .split-ledger-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1.25rem;
    }
    @media (max-width: 960px) {
      .split-ledger-grid { grid-template-columns: 1fr; }
    }
    .ledger-pane {
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      display: flex;
      flex-direction: column;
      height: 540px;
    }
    .ledger-pane-header {
      padding: 0.75rem 1rem;
      background: var(--bg-muted);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .ledger-pane-body {
      flex: 1;
      overflow-y: auto;
      padding: 0;
    }
    .ledger-item {
      padding: 0.65rem 0.9rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      gap: 0.75rem;
      cursor: pointer;
      transition: background 0.15s;
    }
    .ledger-item:hover {
      background: var(--bg-muted);
    }
    .ledger-item.selected {
      background: var(--accent-subtle);
      border-left: 3px solid var(--accent);
    }

    /* Floating Action Match Dock */
    .floating-dock {
      position: fixed;
      bottom: 1.5rem;
      left: 50%;
      transform: translateX(-50%);
      background: var(--bg-surface);
      border: 1px solid var(--border);
      box-shadow: var(--shadow-lg);
      border-radius: 9999px;
      padding: 0.6rem 1.25rem;
      display: none;
      align-items: center;
      gap: 1.25rem;
      z-index: 50;
    }
    .dock-stat {
      display: flex;
      flex-direction: column;
      align-items: center;
    }
    .dock-stat-label {
      font-size: 0.7rem;
      color: var(--text-muted);
      font-weight: 600;
      text-transform: uppercase;
    }
    .dock-stat-val {
      font-size: 0.95rem;
      font-weight: 700;
    }

    /* Slide-out Inspector Drawer */
    .drawer-overlay {
      position: fixed;
      top: 0;
      left: 0;
      width: 100vw;
      height: 100vh;
      background: rgba(0, 0, 0, 0.4);
      z-index: 60;
      display: none;
    }
    .drawer {
      position: fixed;
      top: 0;
      right: -520px;
      width: 500px;
      max-width: 90vw;
      height: 100vh;
      background: var(--bg-surface);
      box-shadow: var(--shadow-lg);
      z-index: 65;
      transition: right 0.25s ease-in-out;
      display: flex;
      flex-direction: column;
    }
    .drawer.open { right: 0; }
    .drawer-header {
      padding: 1.25rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .drawer-body {
      flex: 1;
      overflow-y: auto;
      padding: 1.25rem;
    }

    /* Studio Sliders */
    .slider-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 1rem;
      padding-bottom: 1rem;
      border-bottom: 1px solid var(--border);
    }
    .slider-info {
      max-width: 60%;
    }
    .slider-title {
      font-size: 0.9rem;
      font-weight: 600;
    }
    .slider-desc {
      font-size: 0.78rem;
      color: var(--text-muted);
    }
    .slider-ctrl {
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }

    /* Modal */
    .modal-overlay {
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0, 0, 0, 0.4);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 70;
    }
    .modal {
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      box-shadow: var(--shadow-lg);
      width: 500px;
      max-width: 90vw;
      overflow: hidden;
    }
    .modal-header {
      padding: 1rem 1.25rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .modal-body { padding: 1.25rem; }
    .modal-footer {
      padding: 0.75rem 1.25rem;
      border-top: 1px solid var(--border);
      background: var(--bg-muted);
      display: flex;
      justify-content: flex-end;
      gap: 0.5rem;
    }
    .form-group { margin-bottom: 1rem; }
    .form-label {
      display: block;
      font-size: 0.8rem;
      font-weight: 600;
      margin-bottom: 0.35rem;
      color: var(--text-secondary);
    }
    .form-input {
      width: 100%;
      padding: 0.5rem 0.75rem;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      background: var(--bg-surface);
      color: var(--text-primary);
      font-size: 0.85rem;
    }
  </style>
</head>
<body>

  <!-- Navigation Header -->
  <header>
    <div class="header-inner">
      <a href="/" class="brand">
        <div class="brand-icon">RX</div>
        <div>
          <span class="brand-title">ReconcileX</span>
          <span class="brand-tag">v0.1.0</span>
        </div>
      </a>
      <div class="header-actions">
        <button class="btn btn-secondary btn-sm" onclick="loadSampleData()">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          Load Demo Dataset
        </button>
        <button class="btn btn-primary btn-sm" onclick="triggerReconciliation()">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
          Re-Run Engine
        </button>
        <button class="btn btn-secondary btn-sm" onclick="exportReport('excel')" title="Download Styled XLSX">
          Excel
        </button>
        <button class="btn btn-secondary btn-sm" onclick="exportReport('journal-voucher')" title="Download ERP Double-Entry Journal Voucher CSV">
          ERP JV (CSV)
        </button>
        <button class="btn btn-secondary btn-sm" onclick="exportReport('markdown')" title="Download Audit Markdown">
          Markdown
        </button>
        <button class="btn btn-secondary btn-sm" onclick="toggleTheme()" id="theme-btn" title="Toggle Light/Dark Theme">
          Theme
        </button>
      </div>
    </div>
  </header>

  <main class="container">

    <!-- KPI Summary Grid -->
    <div class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-label">Reconciliation Rate</div>
        <div class="kpi-value num" id="kpi-rate">0.0%</div>
        <div class="kpi-sub" id="kpi-rate-sub">0 of 0 matched</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Invoiced AP Total</div>
        <div class="kpi-value num" id="kpi-invoiced">$0.00</div>
        <div class="kpi-sub" id="kpi-invoiced-count">0 invoices loaded</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Bank Cleared Total</div>
        <div class="kpi-value num" id="kpi-bank">$0.00</div>
        <div class="kpi-sub" id="kpi-bank-count">0 cleared lines</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Net Variance</div>
        <div class="kpi-value num" id="kpi-variance">$0.00</div>
        <div class="kpi-sub" id="kpi-variance-sub">Unbalanced delta</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Tax & Audit Integrity</div>
        <div class="kpi-value num" id="kpi-tax-health">100%</div>
        <div class="kpi-sub" id="kpi-tax-health-sub">0 VAT anomalies</div>
      </div>
    </div>

    <!-- Analytics Charts Row -->
    <div class="charts-grid" id="charts-wrapper" style="display: none;">
      <div class="card" style="margin-bottom: 0;">
        <div class="card-header">
          <span class="card-title">Reconciliation Method Mix</span>
        </div>
        <div class="card-body" style="height: 220px; display: flex; align-items: center; justify-content: center;">
          <canvas id="chart-method-mix"></canvas>
        </div>
      </div>
      <div class="card" style="margin-bottom: 0;">
        <div class="card-header">
          <span class="card-title">Settlement Timeline & Cash Flow</span>
        </div>
        <div class="card-body" style="height: 220px;">
          <canvas id="chart-settlement-timeline"></canvas>
        </div>
      </div>
    </div>

    <!-- Document Ingestion Drop Zones -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">Document & Statement Ingestion</span>
        <span id="upload-status" style="font-size: 0.8rem; color: var(--text-muted);">Ready</span>
      </div>
      <div class="card-body">
        <div class="setup-grid">
          <div class="drop-zone" onclick="document.getElementById('file-invoices').click()">
            <div class="drop-icon">📄</div>
            <div class="drop-title">Upload Invoices (PDF, PNG, JPG)</div>
            <div class="drop-desc">Local parser with offline OCR & Arabic support</div>
            <input type="file" id="file-invoices" multiple accept=".pdf,.png,.jpg,.jpeg" style="display:none;" onchange="handleInvoicesUpload(this.files)">
          </div>
          <div class="drop-zone" onclick="document.getElementById('file-statement').click()">
            <div class="drop-icon">📊</div>
            <div class="drop-title">Upload Bank Statement (CSV, XLSX)</div>
            <div class="drop-desc">Auto-detects columns, dates, amounts, and fees</div>
            <input type="file" id="file-statement" accept=".csv,.xlsx,.xls" style="display:none;" onchange="handleStatementUpload(this.files[0])">
          </div>
        </div>
      </div>
    </div>

    <!-- Main Navigation Tabs -->
    <div class="card">
      <div class="card-body" style="padding-bottom: 0.5rem;">
        <div class="nav-tabs">
          <button class="tab-btn active" onclick="switchTab('tab-matches', this)">
            Matched Pairs <span class="tab-badge num" id="count-matches">0</span>
          </button>
          <button class="tab-btn" onclick="switchTab('tab-split-ledger', this)">
            ⚡ Interactive Split-Ledger <span class="tab-badge num" id="count-split-total">0</span>
          </button>
          <button class="tab-btn" onclick="switchTab('tab-unmatched-inv', this)">
            Unmatched Invoices <span class="tab-badge num" id="count-unmatched-inv">0</span>
          </button>
          <button class="tab-btn" onclick="switchTab('tab-unmatched-tx', this)">
            Unmatched Bank Feeds <span class="tab-badge num" id="count-unmatched-tx">0</span>
          </button>
          <button class="tab-btn" onclick="switchTab('tab-tax-compliance', this)">
            Tax & ZATCA Audit <span class="tab-badge num" id="count-tax-audited">0</span>
          </button>
          <button class="tab-btn" onclick="switchTab('tab-rules-studio', this)">
            ⚙️ Parameter Studio
          </button>
        </div>

        <!-- Tab 1: Matched Pairs Table -->
        <div id="tab-matches">
          <div class="table-container">
            <table>
              <thead>
                <tr>
                  <th>Status & Type</th>
                  <th>Invoice Doc(s)</th>
                  <th>Bank Tx ID</th>
                  <th>Invoice Total</th>
                  <th>Bank Amount</th>
                  <th>Variance</th>
                  <th>Confidence</th>
                  <th>Reasoning / Audit Trail</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody id="matches-tbody">
                <tr><td colspan="9" style="text-align: center; color: var(--text-muted); padding: 2rem;">No reconciliation run yet. Load sample data or upload documents.</td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Tab 2: Dual Split-Ledger Workspace -->
        <div id="tab-split-ledger" style="display: none;">
          <div style="margin-bottom: 0.75rem; font-size: 0.82rem; color: var(--text-secondary); display: flex; justify-content: space-between; align-items: center;">
            <span>Check items on both sides to simulate and manually balance 1-to-1, 1-to-N, or N-to-1 clearing entries.</span>
            <button class="btn btn-secondary btn-sm" onclick="clearSplitSelections()">Clear Selection</button>
          </div>
          <div class="split-ledger-grid">
            <!-- Left Pane: Open AP Invoices -->
            <div class="ledger-pane">
              <div class="ledger-pane-header">
                <span style="font-weight: 600; font-size: 0.85rem;">AP Invoices (Accounts Payable)</span>
                <input type="text" placeholder="Filter invoices..." id="split-filter-inv" oninput="filterSplitLedger()" style="padding: 2px 6px; font-size: 0.75rem; border: 1px solid var(--border); border-radius: 4px;">
              </div>
              <div class="ledger-pane-body" id="split-inv-list">
                <div style="padding: 2rem; text-align: center; color: var(--text-muted);">No open invoices available.</div>
              </div>
            </div>
            <!-- Right Pane: Open Bank Transactions -->
            <div class="ledger-pane">
              <div class="ledger-pane-header">
                <span style="font-weight: 600; font-size: 0.85rem;">Bank Statement Feed (Cash/Bank)</span>
                <input type="text" placeholder="Filter bank feeds..." id="split-filter-tx" oninput="filterSplitLedger()" style="padding: 2px 6px; font-size: 0.75rem; border: 1px solid var(--border); border-radius: 4px;">
              </div>
              <div class="ledger-pane-body" id="split-tx-list">
                <div style="padding: 2rem; text-align: center; color: var(--text-muted);">No open bank transactions available.</div>
              </div>
            </div>
          </div>
        </div>

        <!-- Tab 3: Unmatched Invoices -->
        <div id="tab-unmatched-inv" style="display: none;">
          <div class="table-container">
            <table>
              <thead>
                <tr>
                  <th>Doc ID</th>
                  <th>Vendor Name</th>
                  <th>Date</th>
                  <th>Total Amount</th>
                  <th>Tax ID</th>
                  <th>Source File</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody id="unmatched-inv-tbody">
                <tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 2rem;">No unmatched invoices.</td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Tab 4: Unmatched Bank Feeds -->
        <div id="tab-unmatched-tx" style="display: none;">
          <div class="table-container">
            <table>
              <thead>
                <tr>
                  <th>Tx ID</th>
                  <th>Date</th>
                  <th>Counterparty / Narration</th>
                  <th>Amount</th>
                  <th>Direction</th>
                  <th>Reference</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody id="unmatched-tx-tbody">
                <tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 2rem;">No unmatched bank transactions.</td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Tab 5: Tax & ZATCA Audit -->
        <div id="tab-tax-compliance" style="display: none;">
          <div style="margin-bottom: 0.75rem; font-size: 0.82rem; color: var(--text-secondary);">
            Automated statutory tax audit inspecting Standard VAT (15% GCC / 14% Egypt), mathematical consistency, and SHA-256 tamper-proof fingerprints.
          </div>
          <div class="table-container">
            <table>
              <thead>
                <tr>
                  <th>Doc ID</th>
                  <th>Vendor Name</th>
                  <th>Tax ID</th>
                  <th>Subtotal</th>
                  <th>Tax Declared</th>
                  <th>Total Amount</th>
                  <th>Calculated VAT %</th>
                  <th>Compliance Status</th>
                  <th>SHA-256 Fingerprint</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody id="tax-audit-tbody">
                <tr><td colspan="10" style="text-align: center; color: var(--text-muted); padding: 2rem;">No documents audited yet.</td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Tab 6: Parameter Studio -->
        <div id="tab-rules-studio" style="display: none;">
          <div style="max-width: 720px; padding: 1rem 0;">
            <div class="slider-row">
              <div class="slider-info">
                <div class="slider-title">Date Tolerance Window</div>
                <div class="slider-desc">Maximum allowable calendar days between invoice date and bank clearing date.</div>
              </div>
              <div class="slider-ctrl">
                <input type="range" id="param-date-tol" min="0" max="14" value="3" oninput="document.getElementById('lbl-date-tol').innerText = this.value + ' days'">
                <span class="num font-semibold" id="lbl-date-tol" style="width: 60px;">3 days</span>
              </div>
            </div>

            <div class="slider-row">
              <div class="slider-info">
                <div class="slider-title">Vendor Fuzzy Strictness Threshold</div>
                <div class="slider-desc">Minimum RapidFuzz token similarity score to accept commercial trading aliases.</div>
              </div>
              <div class="slider-ctrl">
                <input type="range" id="param-vendor-thresh" min="50" max="95" value="70" oninput="document.getElementById('lbl-vendor-thresh').innerText = this.value + '%'">
                <span class="num font-semibold" id="lbl-vendor-thresh" style="width: 60px;">70%</span>
              </div>
            </div>

            <div class="slider-row">
              <div class="slider-info">
                <div class="slider-title">Bank Wire Fee Maximum Tolerance</div>
                <div class="slider-desc">Maximum allowable variance absorbed as financial intermediary transaction fee.</div>
              </div>
              <div class="slider-ctrl">
                <input type="range" id="param-fee-tol" min="0" max="100" value="25" oninput="document.getElementById('lbl-fee-tol').innerText = '$' + this.value">
                <span class="num font-semibold" id="lbl-fee-tol" style="width: 60px;">$25</span>
              </div>
            </div>

            <div class="slider-row">
              <div class="slider-info">
                <div class="slider-title">Subset-Sum Bundle Max Items</div>
                <div class="slider-desc">Maximum depth for 1-to-N combinatoric payment clearing searches.</div>
              </div>
              <div class="slider-ctrl">
                <input type="range" id="param-bundle-max" min="2" max="6" value="4" oninput="document.getElementById('lbl-bundle-max').innerText = this.value + ' items'">
                <span class="num font-semibold" id="lbl-bundle-max" style="width: 60px;">4 items</span>
              </div>
            </div>

            <div style="margin-top: 1.5rem; display: flex; gap: 0.75rem;">
              <button class="btn btn-primary" onclick="triggerReconciliationFromStudio()">Apply Parameters & Re-Run</button>
              <button class="btn btn-secondary" onclick="resetStudioDefaults()">Reset Defaults</button>
            </div>
          </div>
        </div>

      </div>
    </div>

  </main>

  <!-- Floating Action Match Dock (Split Ledger) -->
  <div class="floating-dock" id="floating-dock">
    <div class="dock-stat">
      <span class="dock-stat-label">Selected AP (<span id="dock-count-ap">0</span>)</span>
      <span class="dock-stat-val num" id="dock-sum-ap">$0.00</span>
    </div>
    <div style="color: var(--text-muted); font-size: 1.2rem;">vs</div>
    <div class="dock-stat">
      <span class="dock-stat-label">Selected Bank (<span id="dock-count-tx">0</span>)</span>
      <span class="dock-stat-val num" id="dock-sum-tx">$0.00</span>
    </div>
    <div style="border-left: 1px solid var(--border); height: 28px;"></div>
    <div class="dock-stat">
      <span class="dock-stat-label">Net Delta</span>
      <span class="dock-stat-val num" id="dock-delta">$0.00</span>
    </div>
    <span class="badge" id="dock-status-badge">Balanced</span>
    <button class="btn btn-primary btn-sm" onclick="openBatchModal()">Reconcile Selection</button>
    <button class="btn btn-secondary btn-sm" onclick="clearSplitSelections()">✕</button>
  </div>

  <!-- Slide-Out Forensic Document Inspector Drawer -->
  <div class="drawer-overlay" id="drawer-overlay" onclick="closeDrawer()"></div>
  <div class="drawer" id="doc-drawer">
    <div class="drawer-header">
      <div>
        <h3 style="font-size: 1rem; font-weight: 700;" id="drawer-vendor">Vendor Name</h3>
        <span class="num" style="font-size: 0.8rem; color: var(--text-muted);" id="drawer-doc-id">DOC-ID</span>
      </div>
      <button onclick="closeDrawer()" style="background:none; border:none; cursor:pointer; font-size: 1.4rem; color: var(--text-secondary);">&times;</button>
    </div>
    <div class="drawer-body">
      <!-- Tax Verification Badge -->
      <div id="drawer-tax-badge-wrap" style="margin-bottom: 1rem;"></div>

      <!-- Financial Totals Table -->
      <div style="background: var(--bg-muted); border-radius: var(--radius); padding: 0.9rem; margin-bottom: 1rem;">
        <div style="display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 0.25rem;">
          <span style="color: var(--text-secondary);">Invoice Date:</span>
          <span class="num font-semibold" id="drawer-date">2026-01-01</span>
        </div>
        <div style="display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 0.25rem;">
          <span style="color: var(--text-secondary);">Subtotal Amount:</span>
          <span class="num" id="drawer-subtotal">$0.00</span>
        </div>
        <div style="display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 0.25rem;">
          <span style="color: var(--text-secondary);">Tax (VAT):</span>
          <span class="num" id="drawer-tax">$0.00</span>
        </div>
        <div style="display: flex; justify-content: space-between; font-size: 1rem; font-weight: 700; border-top: 1px solid var(--border); padding-top: 0.4rem; margin-top: 0.4rem;">
          <span>Grand Total:</span>
          <span class="num" id="drawer-total" style="color: var(--accent);">$0.00</span>
        </div>
      </div>

      <!-- SHA-256 Fingerprint -->
      <div style="margin-bottom: 1.25rem;">
        <div style="font-size: 0.72rem; font-weight: 600; text-transform: uppercase; color: var(--text-muted); margin-bottom: 0.25rem;">Audit SHA-256 Fingerprint</div>
        <div class="num" id="drawer-hash" style="font-size: 0.75rem; word-break: break-all; background: var(--bg-muted); padding: 0.4rem 0.6rem; border-radius: 4px; border: 1px solid var(--border);"></div>
      </div>

      <!-- Line Items Section -->
      <div style="margin-bottom: 1.25rem;">
        <div style="font-size: 0.85rem; font-weight: 600; margin-bottom: 0.5rem;">Line Items Breakdown</div>
        <div class="table-container">
          <table>
            <thead>
              <tr>
                <th>Description</th>
                <th>Qty</th>
                <th>Price</th>
                <th>Total</th>
              </tr>
            </thead>
            <tbody id="drawer-line-items-body">
              <tr><td colspan="4" style="text-align: center; color: var(--text-muted);">No line items.</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Source Text Preview -->
      <div>
        <div style="font-size: 0.85rem; font-weight: 600; margin-bottom: 0.35rem;">Raw Extracted OCR Text</div>
        <pre id="drawer-raw-text" class="num" style="font-size: 0.75rem; background: var(--bg-muted); padding: 0.75rem; border-radius: var(--radius); max-height: 180px; overflow-y: auto; white-space: pre-wrap; color: var(--text-secondary); border: 1px solid var(--border);"></pre>
      </div>
    </div>
  </div>

  <!-- Manual Single/Batch Resolution Modal -->
  <div class="modal-overlay" id="resolve-modal">
    <div class="modal">
      <div class="modal-header">
        <h3 style="font-size: 0.95rem; font-weight: 600;">Manual Reconciliation Override</h3>
        <button onclick="closeModal()" style="background:none; border:none; cursor:pointer; font-size: 1.25rem; color: var(--text-secondary);">&times;</button>
      </div>
      <div class="modal-body">
        <div class="form-group">
          <label class="form-label">Selected Invoice(s):</label>
          <input type="text" id="modal-invoice-id" class="form-input num" readonly>
        </div>
        <div class="form-group">
          <label class="form-label">Link to Bank Transaction(s):</label>
          <input type="text" id="modal-tx-display" class="form-input num" readonly>
        </div>
        <div class="form-group">
          <label class="form-label">Audit Justification Note:</label>
          <textarea id="modal-reasoning" class="form-input" rows="3" placeholder="Enter reason (e.g., vendor trading name mismatch, bank wire fee deduction verified)..."></textarea>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
        <button class="btn btn-primary" onclick="submitBatchResolution()">Approve & Settle Match</button>
      </div>
    </div>
  </div>

  <script>
    let activeReport = null;
    let selectedInvIds = new Set();
    let selectedTxIds = new Set();
    let methodMixChart = null;
    let timelineChart = null;

    // Theme Management
    function initTheme() {
      const saved = localStorage.getItem('rx-theme') || 'light';
      document.documentElement.setAttribute('data-theme', saved);
    }
    function toggleTheme() {
      const current = document.documentElement.getAttribute('data-theme') || 'light';
      const next = current === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('rx-theme', next);
    }
    initTheme();

    // Tab Navigation
    function switchTab(tabId, btn) {
      document.getElementById('tab-matches').style.display = 'none';
      document.getElementById('tab-split-ledger').style.display = 'none';
      document.getElementById('tab-unmatched-inv').style.display = 'none';
      document.getElementById('tab-unmatched-tx').style.display = 'none';
      document.getElementById('tab-tax-compliance').style.display = 'none';
      document.getElementById('tab-rules-studio').style.display = 'none';
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));

      document.getElementById(tabId).style.display = 'block';
      btn.classList.add('active');
    }

    // Load Demo Data
    async function loadSampleData() {
      document.getElementById('upload-status').innerText = 'Generating & reconciling sample dataset...';
      try {
        const res = await fetch('/api/load-sample', { method: 'POST' });
        const data = await res.json();
        if (data.status === 'success') {
          document.getElementById('upload-status').innerText = 'Demo dataset loaded successfully!';
          await fetchResults();
          await fetchAnalytics();
        }
      } catch (err) {
        alert('Error loading sample dataset: ' + err);
      }
    }

    // Handle Uploads
    async function handleInvoicesUpload(files) {
      const formData = new FormData();
      for (let i = 0; i < files.length; i++) {
        formData.append('invoices', files[i]);
      }
      document.getElementById('upload-status').innerText = `Uploading ${files.length} invoice(s)...`;
      const res = await fetch('/api/upload', { method: 'POST', body: formData });
      const data = await res.json();
      document.getElementById('upload-status').innerText = `Loaded ${data.invoices_loaded} invoices total.`;
      fetchResults();
    }

    async function handleStatementUpload(file) {
      const formData = new FormData();
      formData.append('statement', file);
      document.getElementById('upload-status').innerText = `Uploading statement ${file.name}...`;
      const res = await fetch('/api/upload', { method: 'POST', body: formData });
      const data = await res.json();
      document.getElementById('upload-status').innerText = `Loaded statement (${data.transactions_loaded} transactions total).`;
      fetchResults();
    }

    // Trigger Reconciliation
    async function triggerReconciliation() {
      document.getElementById('upload-status').innerText = 'Running deterministic matching engine...';
      try {
        const res = await fetch('/api/reconcile', { method: 'POST' });
        const data = await res.json();
        if (data.status === 'success') {
          document.getElementById('upload-status').innerText = 'Reconciliation completed successfully.';
          await fetchResults();
          await fetchAnalytics();
        }
      } catch (err) {
        alert('Reconciliation failed: ' + err);
      }
    }

    async function triggerReconciliationFromStudio() {
      const dateTol = document.getElementById('param-date-tol').value;
      const vendorThresh = document.getElementById('param-vendor-thresh').value;
      const feeTol = document.getElementById('param-fee-tol').value;
      const bundleMax = document.getElementById('param-bundle-max').value;

      document.getElementById('upload-status').innerText = 'Re-running engine with custom parameters...';
      try {
        const url = `/api/reconcile?tolerance_days=${dateTol}&vendor_threshold=${vendorThresh}&fee_tolerance=${feeTol}&max_bundle=${bundleMax}`;
        const res = await fetch(url, { method: 'POST' });
        const data = await res.json();
        if (data.status === 'success') {
          document.getElementById('upload-status').innerText = 'Parameters applied and report updated.';
          await fetchResults();
          await fetchAnalytics();
          switchTab('tab-matches', document.querySelector('.tab-btn'));
        }
      } catch (err) {
        alert('Parameter execution failed: ' + err);
      }
    }

    function resetStudioDefaults() {
      document.getElementById('param-date-tol').value = 3;
      document.getElementById('lbl-date-tol').innerText = '3 days';
      document.getElementById('param-vendor-thresh').value = 70;
      document.getElementById('lbl-vendor-thresh').innerText = '70%';
      document.getElementById('param-fee-tol').value = 25;
      document.getElementById('lbl-fee-tol').innerText = '$25';
      document.getElementById('param-bundle-max').value = 4;
      document.getElementById('lbl-bundle-max').innerText = '4 items';
    }

    // Fetch and Render Results
    async function fetchResults() {
      const res = await fetch('/api/results');
      const data = await res.json();
      if (!data.report) return;
      activeReport = data.report;
      renderReport(activeReport);
      renderSplitLedger();
      renderTaxComplianceTable();
    }

    function renderReport(report) {
      const s = report.summary;
      document.getElementById('kpi-rate').innerText = `${s.match_rate_percentage.toFixed(1)}%`;
      document.getElementById('kpi-rate-sub').innerText = `${s.matched_count} of ${s.total_invoices} matched`;
      document.getElementById('kpi-invoiced').innerText = `$${s.total_invoiced_amount.toLocaleString(undefined, {minimumFractionDigits: 2})}`;
      document.getElementById('kpi-invoiced-count').innerText = `${s.total_invoices} invoices loaded`;
      document.getElementById('kpi-bank').innerText = `$${s.total_bank_amount.toLocaleString(undefined, {minimumFractionDigits: 2})}`;
      document.getElementById('kpi-bank-count').innerText = `${s.total_transactions} cleared lines`;
      document.getElementById('kpi-variance').innerText = `$${s.net_variance.toLocaleString(undefined, {minimumFractionDigits: 2})}`;

      document.getElementById('count-matches').innerText = report.matched_pairs.length;
      document.getElementById('count-unmatched-inv').innerText = report.unmatched_invoices.length;
      document.getElementById('count-unmatched-tx').innerText = report.unmatched_transactions.length;
      document.getElementById('count-split-total').innerText = report.unmatched_invoices.length + report.unmatched_transactions.length;

      // Render Matches Table
      const mBody = document.getElementById('matches-tbody');
      if (report.matched_pairs.length === 0) {
        mBody.innerHTML = '<tr><td colspan="9" style="text-align:center; padding: 2rem;">No matches found.</td></tr>';
      } else {
        mBody.innerHTML = report.matched_pairs.map(m => {
          let badgeCls = 'badge-success';
          if (m.match_type === 'FEE_ADJUSTED' || m.match_type === 'BANK_FEE_TOLERANCE') badgeCls = 'badge-warning';
          if (m.match_type === 'AGENT_RESOLVED') badgeCls = 'badge-info';
          if (m.match_type === 'ONE_TO_MANY_BUNDLE') badgeCls = 'badge-neutral';

          const firstDocId = m.invoice_ids[0] || '';
          return `
            <tr>
              <td><span class="badge ${badgeCls}">${m.match_type}</span></td>
              <td class="num">${m.invoice_ids.join(', ')}</td>
              <td class="num">${m.tx_id || 'N/A'}</td>
              <td class="num">$${m.invoice_total.toFixed(2)}</td>
              <td class="num">$${m.bank_amount.toFixed(2)}</td>
              <td class="num" style="color: ${m.variance_amount !== 0 ? 'var(--danger)' : 'inherit'}">$${m.variance_amount.toFixed(2)}</td>
              <td class="num">${(m.confidence_score * 100).toFixed(0)}%</td>
              <td style="font-size: 0.8rem; color: var(--text-secondary); max-width: 280px;">${m.audit_reasoning}</td>
              <td>
                ${firstDocId ? `<button class="btn btn-secondary btn-sm" onclick="inspectDocument('${firstDocId}')">Inspect</button>` : ''}
              </td>
            </tr>
          `;
        }).join('');
      }

      // Render Unmatched Invoices
      const invBody = document.getElementById('unmatched-inv-tbody');
      if (report.unmatched_invoices.length === 0) {
        invBody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding: 2rem;">All invoices reconciled!</td></tr>';
      } else {
        invBody.innerHTML = report.unmatched_invoices.map(inv => `
          <tr>
            <td class="num font-semibold">${inv.doc_id}</td>
            <td>${inv.vendor_name}</td>
            <td class="num">${inv.invoice_date}</td>
            <td class="num">$${inv.total_amount.toFixed(2)}</td>
            <td class="num">${inv.vendor_tax_id || 'N/A'}</td>
            <td style="font-size:0.8rem; color:var(--text-muted);">${inv.source_path.split(/[\\\\/]/).pop()}</td>
            <td>
              <button class="btn btn-secondary btn-sm" onclick="inspectDocument('${inv.doc_id}')">Inspect</button>
            </td>
          </tr>
        `).join('');
      }

      // Render Unmatched Bank Feeds
      const txBody = document.getElementById('unmatched-tx-tbody');
      if (report.unmatched_transactions.length === 0) {
        txBody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding: 2rem;">All bank feeds cleared!</td></tr>';
      } else {
        txBody.innerHTML = report.unmatched_transactions.map(tx => `
          <tr>
            <td class="num font-semibold">${tx.tx_id}</td>
            <td class="num">${tx.tx_date}</td>
            <td>${tx.counterparty}</td>
            <td class="num">$${tx.amount.toFixed(2)}</td>
            <td><span class="badge ${tx.direction === 'DEBIT' ? 'badge-danger' : 'badge-success'}">${tx.direction}</span></td>
            <td class="num">${tx.reference || 'N/A'}</td>
            <td>
              <span class="badge badge-warning">Unsubstantiated</span>
            </td>
          </tr>
        `).join('');
      }
    }

    // Split Ledger Workspace Rendering
    function renderSplitLedger() {
      if (!activeReport) return;
      const invList = document.getElementById('split-inv-list');
      const txList = document.getElementById('split-tx-list');

      // Left: Open Invoices
      if (activeReport.unmatched_invoices.length === 0) {
        invList.innerHTML = '<div style="padding: 2rem; text-align: center; color: var(--text-muted);">All invoices are reconciled.</div>';
      } else {
        invList.innerHTML = activeReport.unmatched_invoices.map(inv => {
          const isChecked = selectedInvIds.has(inv.doc_id);
          return `
            <div class="ledger-item ${isChecked ? 'selected' : ''}" onclick="toggleSelectInv('${inv.doc_id}')" data-text="${inv.vendor_name.toLowerCase()} ${inv.doc_id.toLowerCase()}">
              <input type="checkbox" ${isChecked ? 'checked' : ''} onclick="event.stopPropagation(); toggleSelectInv('${inv.doc_id}')">
              <div style="flex: 1;">
                <div style="font-weight: 600; font-size: 0.85rem;">${inv.vendor_name}</div>
                <div class="num" style="font-size: 0.75rem; color: var(--text-muted);">${inv.doc_id} • ${inv.invoice_date}</div>
              </div>
              <div style="text-align: right;">
                <div class="num font-semibold" style="font-size: 0.9rem;">$${inv.total_amount.toFixed(2)}</div>
                <button class="btn btn-secondary btn-sm" style="padding: 1px 5px; font-size: 0.72rem; margin-top: 2px;" onclick="event.stopPropagation(); inspectDocument('${inv.doc_id}')">Inspect</button>
              </div>
            </div>
          `;
        }).join('');
      }

      // Right: Open Bank Transactions
      if (activeReport.unmatched_transactions.length === 0) {
        txList.innerHTML = '<div style="padding: 2rem; text-align: center; color: var(--text-muted);">All bank feeds are cleared.</div>';
      } else {
        txList.innerHTML = activeReport.unmatched_transactions.map(tx => {
          const isChecked = selectedTxIds.has(tx.tx_id);
          return `
            <div class="ledger-item ${isChecked ? 'selected' : ''}" onclick="toggleSelectTx('${tx.tx_id}')" data-text="${tx.counterparty.toLowerCase()} ${tx.tx_id.toLowerCase()}">
              <input type="checkbox" ${isChecked ? 'checked' : ''} onclick="event.stopPropagation(); toggleSelectTx('${tx.tx_id}')">
              <div style="flex: 1;">
                <div style="font-weight: 600; font-size: 0.85rem;">${tx.counterparty}</div>
                <div class="num" style="font-size: 0.75rem; color: var(--text-muted);">${tx.tx_id} • ${tx.tx_date}</div>
              </div>
              <div style="text-align: right;">
                <div class="num font-semibold" style="font-size: 0.9rem;">$${tx.amount.toFixed(2)}</div>
                <span class="badge ${tx.direction === 'DEBIT' ? 'badge-danger' : 'badge-success'}" style="font-size: 0.65rem;">${tx.direction}</span>
              </div>
            </div>
          `;
        }).join('');
      }

      updateFloatingDock();
    }

    function toggleSelectInv(docId) {
      if (selectedInvIds.has(docId)) selectedInvIds.delete(docId);
      else selectedInvIds.add(docId);
      renderSplitLedger();
    }

    function toggleSelectTx(txId) {
      if (selectedTxIds.has(txId)) selectedTxIds.delete(txId);
      else selectedTxIds.add(txId);
      renderSplitLedger();
    }

    function clearSplitSelections() {
      selectedInvIds.clear();
      selectedTxIds.clear();
      renderSplitLedger();
    }

    function filterSplitLedger() {
      const qInv = (document.getElementById('split-filter-inv').value || '').toLowerCase();
      const qTx = (document.getElementById('split-filter-tx').value || '').toLowerCase();

      document.querySelectorAll('#split-inv-list .ledger-item').forEach(el => {
        const text = el.getAttribute('data-text') || '';
        el.style.display = text.includes(qInv) ? 'flex' : 'none';
      });
      document.querySelectorAll('#split-tx-list .ledger-item').forEach(el => {
        const text = el.getAttribute('data-text') || '';
        el.style.display = text.includes(qTx) ? 'flex' : 'none';
      });
    }

    function updateFloatingDock() {
      const dock = document.getElementById('floating-dock');
      if (selectedInvIds.size === 0 && selectedTxIds.size === 0) {
        dock.style.display = 'none';
        return;
      }
      dock.style.display = 'flex';

      let sumAp = 0;
      let sumTx = 0;

      if (activeReport) {
        activeReport.unmatched_invoices.forEach(i => {
          if (selectedInvIds.has(i.doc_id)) sumAp += i.total_amount;
        });
        activeReport.unmatched_transactions.forEach(t => {
          if (selectedTxIds.has(t.tx_id)) sumTx += t.amount;
        });
      }

      const delta = sumTx - sumAp;
      document.getElementById('dock-count-ap').innerText = selectedInvIds.size;
      document.getElementById('dock-sum-ap').innerText = `$${sumAp.toFixed(2)}`;
      document.getElementById('dock-count-tx').innerText = selectedTxIds.size;
      document.getElementById('dock-sum-tx').innerText = `$${sumTx.toFixed(2)}`;
      document.getElementById('dock-delta').innerText = `$${delta.toFixed(2)}`;

      const badge = document.getElementById('dock-status-badge');
      if (Math.abs(delta) < 0.01) {
        badge.className = 'badge badge-success';
        badge.innerText = 'Balanced ($0.00)';
      } else if (Math.abs(delta) <= 25.0) {
        badge.className = 'badge badge-warning';
        badge.innerText = 'Fee Absorbable';
      } else {
        badge.className = 'badge badge-danger';
        badge.innerText = 'Variance Alert';
      }
    }

    // Modal Handling for Split-Ledger
    function openBatchModal() {
      if (selectedInvIds.size === 0 && selectedTxIds.size === 0) return;
      document.getElementById('modal-invoice-id').value = Array.from(selectedInvIds).join(', ') || '(None)';
      document.getElementById('modal-tx-display').value = Array.from(selectedTxIds).join(', ') || '(None)';
      document.getElementById('modal-reasoning').value = 'Accountant verified via Split-Ledger clearing.';
      document.getElementById('resolve-modal').style.display = 'flex';
    }

    function closeModal() {
      document.getElementById('resolve-modal').style.display = 'none';
    }

    async function submitBatchResolution() {
      const invIds = Array.from(selectedInvIds);
      const txIds = Array.from(selectedTxIds);
      const reasoning = document.getElementById('modal-reasoning').value || 'Manual accountant clearing.';

      try {
        const res = await fetch('/api/manual-batch-match', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ invoice_ids: invIds, tx_ids: txIds, reasoning: reasoning })
        });
        const data = await res.json();
        if (data.status === 'success') {
          closeModal();
          clearSplitSelections();
          await fetchResults();
          await fetchAnalytics();
        } else {
          alert('Resolution failed: ' + data.detail);
        }
      } catch (err) {
        alert('Resolution error: ' + err);
      }
    }

    // Document Inspector Drawer
    async function inspectDocument(docId) {
      try {
        const res = await fetch(`/api/document/${encodeURIComponent(docId)}`);
        const data = await res.json();
        if (res.status !== 200) {
          alert('Could not retrieve document: ' + data.detail);
          return;
        }

        document.getElementById('drawer-vendor').innerText = data.vendor_name;
        document.getElementById('drawer-doc-id').innerText = data.doc_id;
        document.getElementById('drawer-date').innerText = data.invoice_date;
        document.getElementById('drawer-subtotal').innerText = `$${(data.subtotal || 0).toFixed(2)}`;
        document.getElementById('drawer-tax').innerText = `$${(data.tax_amount || 0).toFixed(2)}`;
        document.getElementById('drawer-total').innerText = `$${data.total_amount.toFixed(2)} ${data.currency}`;
        document.getElementById('drawer-hash').innerText = data.tax_audit.sha256_fingerprint;
        document.getElementById('drawer-raw-text').innerText = data.raw_text || '(No OCR text available)';

        // Compliance badge
        const badgeWrap = document.getElementById('drawer-tax-badge-wrap');
        const audit = data.tax_audit;
        if (audit.is_compliant) {
          badgeWrap.innerHTML = `
            <div style="background: var(--success-bg); border: 1px solid var(--success-border); border-radius: var(--radius); padding: 0.6rem 0.8rem; display: flex; align-items: center; justify-content: space-between;">
              <span style="color: var(--success); font-weight: 600; font-size: 0.8rem;">✓ ${audit.status_label}</span>
              <span class="num" style="font-size: 0.75rem; color: var(--text-secondary);">Rate: ${(audit.detected_rate_percent).toFixed(1)}%</span>
            </div>
          `;
        } else {
          badgeWrap.innerHTML = `
            <div style="background: var(--danger-bg); border: 1px solid var(--danger-border); border-radius: var(--radius); padding: 0.6rem 0.8rem;">
              <div style="color: var(--danger); font-weight: 600; font-size: 0.8rem;">⚠ Tax Discrepancy Flagged</div>
              <div style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.2rem;">${audit.audit_notes}</div>
            </div>
          `;
        }

        // Line Items
        const liBody = document.getElementById('drawer-line-items-body');
        const items = data.items || [];
        if (items.length === 0) {
          liBody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-muted);">No line items extracted.</td></tr>';
        } else {
          liBody.innerHTML = items.map(li => `
            <tr>
              <td>${li.description}</td>
              <td class="num">${li.quantity}</td>
              <td class="num">$${li.unit_price.toFixed(2)}</td>
              <td class="num font-semibold">$${li.amount.toFixed(2)}</td>
            </tr>
          `).join('');
        }

        document.getElementById('drawer-overlay').style.display = 'block';
        document.getElementById('doc-drawer').classList.add('open');
      } catch (err) {
        alert('Failed to inspect document: ' + err);
      }
    }

    function closeDrawer() {
      document.getElementById('doc-drawer').classList.remove('open');
      document.getElementById('drawer-overlay').style.display = 'none';
    }

    // Tax Compliance Table in Tab 5
    async function renderTaxComplianceTable() {
      if (!activeReport) return;
      const tbody = document.getElementById('tax-audit-tbody');
      tbody.innerHTML = '<tr><td colspan="10" style="text-align: center; padding: 1.5rem;">Auditing documents in real-time...</td></tr>';

      try {
        const rows = [];
        let compliantCount = 0;
        let issueCount = 0;

        for (const inv of activeReport.unmatched_invoices.concat(
          activeReport.matched_pairs.flatMap(m => m.invoice_ids.map(id => ({ doc_id: id, vendor_name: 'Matched Vendor' })))
        )) {
          // Fetch audit for each
          const docId = inv.doc_id;
          try {
            const res = await fetch(`/api/document/${encodeURIComponent(docId)}`);
            if (res.status === 200) {
              const doc = await res.json();
              const a = doc.tax_audit;
              if (a.is_compliant) compliantCount++; else issueCount++;

              rows.push(`
                <tr>
                  <td class="num font-semibold">${doc.doc_id}</td>
                  <td>${doc.vendor_name}</td>
                  <td class="num">${doc.vendor_tax_id || 'N/A'}</td>
                  <td class="num">$${(doc.subtotal || 0).toFixed(2)}</td>
                  <td class="num">$${(doc.tax_amount || 0).toFixed(2)}</td>
                  <td class="num font-semibold">$${doc.total_amount.toFixed(2)}</td>
                  <td class="num">${(a.detected_rate_percent).toFixed(1)}%</td>
                  <td>
                    <span class="badge ${a.is_compliant ? 'badge-success' : 'badge-danger'}">
                      ${a.is_compliant ? '✓ Valid' : '⚠ Flagged'}
                    </span>
                  </td>
                  <td class="num" style="font-size: 0.72rem; color: var(--text-muted);">${a.sha256_fingerprint.substring(0, 16)}...</td>
                  <td><button class="btn btn-secondary btn-sm" onclick="inspectDocument('${doc.doc_id}')">Inspect</button></td>
                </tr>
              `);
            }
          } catch(e) {}
        }

        document.getElementById('count-tax-audited').innerText = rows.length;
        const healthPercent = rows.length > 0 ? ((compliantCount / rows.length) * 100).toFixed(0) : 100;
        document.getElementById('kpi-tax-health').innerText = `${healthPercent}%`;
        document.getElementById('kpi-tax-health-sub').innerText = `${issueCount} VAT anomalies`;

        if (rows.length === 0) {
          tbody.innerHTML = '<tr><td colspan="10" style="text-align: center; color: var(--text-muted); padding: 2rem;">No documents available.</td></tr>';
        } else {
          tbody.innerHTML = rows.join('');
        }
      } catch (err) {
        tbody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: var(--danger); padding: 1.5rem;">Audit inspection error: ${err}</td></tr>`;
      }
    }

    // Fetch and Render Analytics Charts
    async function fetchAnalytics() {
      try {
        const res = await fetch('/api/analytics');
        const data = await res.json();
        if (data.status === 'empty') return;

        document.getElementById('charts-wrapper').style.display = 'grid';

        // 1. Method Mix Doughnut Chart
        const mixLabels = Object.keys(data.match_type_distribution);
        const mixValues = Object.values(data.match_type_distribution);

        if (methodMixChart) methodMixChart.destroy();
        const ctx1 = document.getElementById('chart-method-mix').getContext('2d');
        methodMixChart = new Chart(ctx1, {
          type: 'doughnut',
          data: {
            labels: mixLabels,
            datasets: [{
              data: mixValues,
              backgroundColor: ['#2563eb', '#16a34a', '#ca8a04', '#9333ea', '#0d9488', '#ea580c'],
              borderWidth: 0,
            }]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 10 } } }
            },
            cutout: '68%'
          }
        });

        // 2. Settlement Timeline Chart
        const timeline = data.settlement_timeline;
        const timelineLabels = timeline.map(t => t.date);
        const invoicePoints = timeline.map(t => t.type === 'INVOICE' ? t.amount : 0);
        const bankPoints = timeline.map(t => t.type === 'BANK_CLEARING' ? t.amount : 0);

        if (timelineChart) timelineChart.destroy();
        const ctx2 = document.getElementById('chart-settlement-timeline').getContext('2d');
        timelineChart = new Chart(ctx2, {
          type: 'bar',
          data: {
            labels: timelineLabels,
            datasets: [
              { label: 'AP Invoiced', data: invoicePoints, backgroundColor: 'rgba(37, 99, 235, 0.65)' },
              { label: 'Bank Cleared', data: bankPoints, backgroundColor: 'rgba(22, 163, 74, 0.65)' }
            ]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
              x: { ticks: { font: { size: 9 } } },
              y: { ticks: { font: { size: 10 } } }
            },
            plugins: {
              legend: { position: 'top', labels: { boxWidth: 10, font: { size: 10 } } }
            }
          }
        });
      } catch (e) {
        console.error('Analytics error:', e);
      }
    }

    function exportReport(format) {
      window.location.href = `/api/export/${format}`;
    }

    // Page initialization check
    fetch('/api/status').then(r => r.json()).then(d => {
      if (d.has_active_report) {
        fetchResults();
        fetchAnalytics();
      }
    });
  </script>
</body>
</html>
"""
