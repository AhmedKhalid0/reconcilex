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
from reconcilex.core.reporting.markdown_exporter import MarkdownReportExporter
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
    fee_tolerance: float = 25.0,
    enable_ai: bool = True,
):
    """Run full reconciliation pipeline."""
    invoices = list(_WEB_SESSION["invoices"].values())
    transactions = list(_WEB_SESSION["transactions"].values())

    if not invoices or not transactions:
        raise HTTPException(
            status_code=400,
            detail="Both invoices and bank transactions must be uploaded before reconciling."
        )

    matcher = DeterministicMatcher(
        date_tolerance=tolerance_days,
        fee_tolerance=fee_tolerance
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

    return {"status": "success", "summary": report.summary.model_dump()}


@app.get("/api/export/excel")
async def export_excel():
    report = _WEB_SESSION["report"]
    if not report:
        raise HTTPException(status_code=400, detail="No active reconciliation report to export.")

    out_file = Path("./data/exports/ReconcileX_Report.xlsx")
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


@app.get("/", response_class=HTMLResponse)
async def dashboard_view():
    return HTML_DASHBOARD_TEMPLATE


# ==============================================================================
# Human-Crafted Eye-Comfort UI Template (Light Default + Dark Toggle, Zero AI Slop)
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
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background-color: var(--bg-base);
      color: var(--text-primary);
      line-height: 1.5;
      transition: background-color 0.2s, color 0.2s;
      min-height: 100vh;
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
      z-index: 50;
      padding: 0.75rem 1.5rem;
    }
    .header-inner {
      max-width: 1400px;
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
      width: 32px;
      height: 32px;
      border-radius: var(--radius);
      background: var(--accent);
      color: #fff;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 700;
      font-size: 1rem;
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
      gap: 0.75rem;
    }

    /* Buttons */
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 0.5rem;
      padding: 0.5rem 1rem;
      border-radius: var(--radius);
      font-size: 0.875rem;
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
    .btn-sm { padding: 0.25rem 0.6rem; font-size: 0.8rem; }

    /* Layout */
    .container {
      max-width: 1400px;
      margin: 1.5rem auto;
      padding: 0 1.5rem 3rem;
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
      padding: 1rem 1.25rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .card-title {
      font-size: 1rem;
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
      padding: 1.25rem;
      box-shadow: var(--shadow-sm);
    }
    .kpi-label {
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-muted);
      margin-bottom: 0.35rem;
    }
    .kpi-value {
      font-size: 1.75rem;
      font-weight: 700;
      color: var(--text-primary);
    }
    .kpi-sub {
      font-size: 0.8rem;
      color: var(--text-secondary);
      margin-top: 0.25rem;
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
    .drop-icon { font-size: 2rem; margin-bottom: 0.5rem; }
    .drop-title { font-weight: 600; font-size: 0.95rem; margin-bottom: 0.25rem; }
    .drop-desc { font-size: 0.8rem; color: var(--text-muted); }

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
      font-size: 0.875rem;
    }
    th {
      background: var(--bg-muted);
      color: var(--text-secondary);
      font-weight: 600;
      padding: 0.75rem 1rem;
      border-bottom: 1px solid var(--border);
      white-space: nowrap;
    }
    td {
      padding: 0.75rem 1rem;
      border-bottom: 1px solid var(--border);
      color: var(--text-primary);
    }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background-color: var(--bg-muted); }

    /* Badges */
    .badge {
      display: inline-flex;
      align-items: center;
      padding: 2px 8px;
      border-radius: 9999px;
      font-size: 0.75rem;
      font-weight: 600;
      white-space: nowrap;
    }
    .badge-success { background: var(--success-bg); color: var(--success); border: 1px solid var(--success-border); }
    .badge-warning { background: var(--warning-bg); color: var(--warning); border: 1px solid var(--warning-border); }
    .badge-danger { background: var(--danger-bg); color: var(--danger); border: 1px solid var(--danger-border); }
    .badge-info { background: var(--accent-subtle); color: var(--accent); border: 1px solid var(--border); }

    /* Tabs */
    .tabs {
      display: flex;
      gap: 0.5rem;
      border-bottom: 1px solid var(--border);
      padding: 0 1.25rem;
      background: var(--bg-surface);
    }
    .tab-btn {
      padding: 0.75rem 1rem;
      font-size: 0.875rem;
      font-weight: 500;
      color: var(--text-secondary);
      background: none;
      border: none;
      border-bottom: 2px solid transparent;
      cursor: pointer;
    }
    .tab-btn.active {
      color: var(--accent);
      border-bottom-color: var(--accent);
      font-weight: 600;
    }

    /* Modal */
    .modal-overlay {
      display: none;
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0, 0, 0, 0.4);
      backdrop-filter: blur(2px);
      z-index: 100;
      align-items: center;
      justify-content: center;
    }
    .modal {
      background: var(--bg-surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      width: 90%;
      max-width: 550px;
      box-shadow: var(--shadow-md);
      overflow: hidden;
    }
    .modal-header {
      padding: 1rem 1.25rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .modal-body { padding: 1.25rem; }
    .modal-footer {
      padding: 0.75rem 1.25rem;
      border-top: 1px solid var(--border);
      display: flex;
      justify-content: flex-end;
      gap: 0.5rem;
      background: var(--bg-muted);
    }

    /* Form controls */
    .form-group { margin-bottom: 1rem; }
    .form-label { display: block; font-size: 0.8rem; font-weight: 600; margin-bottom: 0.35rem; }
    .form-input {
      width: 100%;
      padding: 0.5rem 0.75rem;
      border-radius: var(--radius);
      border: 1px solid var(--border);
      background: var(--bg-surface);
      color: var(--text-primary);
      font-size: 0.875rem;
    }
    .form-input:focus { outline: none; border-color: var(--accent); }

    /* Alert */
    .alert {
      padding: 0.75rem 1rem;
      border-radius: var(--radius);
      font-size: 0.875rem;
      margin-bottom: 1rem;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .alert-success { background: var(--success-bg); color: var(--success); border: 1px solid var(--success-border); }
  </style>
</head>
<body>

  <header>
    <div class="header-inner">
      <div class="brand">
        <div class="brand-icon">RX</div>
        <div>
          <span class="brand-title">ReconcileX</span>
          <span class="brand-tag">v1.0</span>
        </div>
      </div>
      <div class="header-actions">
        <button class="btn btn-secondary btn-sm" onclick="loadSampleData()">
          ⚡ Load Demo Dataset
        </button>
        <button class="btn btn-secondary btn-sm" onclick="exportReport('excel')">
          📊 Export Excel (.xlsx)
        </button>
        <button class="btn btn-secondary btn-sm" onclick="exportReport('markdown')">
          📝 Export Markdown
        </button>
        <button class="btn btn-secondary btn-sm" id="theme-toggle" onclick="toggleTheme()">
          🌓 Mode
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
        <div class="kpi-label">Total Invoiced</div>
        <div class="kpi-value num" id="kpi-invoiced">$0.00</div>
        <div class="kpi-sub" id="kpi-invoiced-count">0 invoices loaded</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Total Cleared (Bank)</div>
        <div class="kpi-value num" id="kpi-bank">$0.00</div>
        <div class="kpi-sub" id="kpi-bank-count">0 transactions</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Net Variance</div>
        <div class="kpi-value num" id="kpi-variance">$0.00</div>
        <div class="kpi-sub">Ledger balance diff</div>
      </div>
    </div>

    <!-- Upload & Action Bar -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">1. Document Ingestion & Bank Statement</div>
        <div id="upload-status" style="font-size: 0.85rem; color: var(--text-muted);">Ready to ingest</div>
      </div>
      <div class="card-body">
        <div class="setup-grid">
          <div class="drop-zone" onclick="document.getElementById('file-invoices').click()">
            <div class="drop-icon">📁</div>
            <div class="drop-title">Upload Invoices (PDFs & Scans)</div>
            <div class="drop-desc">Select invoice files or folder. Arabic and English receipts supported.</div>
            <input type="file" id="file-invoices" multiple accept=".pdf,.png,.jpg,.jpeg,.webp" style="display:none" onchange="handleInvoicesUpload(this.files)">
          </div>
          <div class="drop-zone" onclick="document.getElementById('file-statement').click()">
            <div class="drop-icon">🏦</div>
            <div class="drop-title">Upload Bank Statement (CSV / Excel)</div>
            <div class="drop-desc">Supports multi-bank CSV, TSV, and XLSX feeds with auto column detection.</div>
            <input type="file" id="file-statement" accept=".csv,.xlsx,.xls,.tsv" style="display:none" onchange="handleStatementUpload(this.files[0])">
          </div>
        </div>
        <div style="display: flex; justify-content: flex-end; gap: 0.75rem;">
          <button class="btn btn-primary" onclick="triggerReconciliation()">
            🚀 Run Deterministic Reconciliation Engine
          </button>
        </div>
      </div>
    </div>

    <!-- Reconciliation Results Section -->
    <div class="card">
      <div class="tabs">
        <button class="tab-btn active" onclick="switchTab('tab-matches', this)">Reconciled Matches (<span id="count-matches">0</span>)</button>
        <button class="tab-btn" onclick="switchTab('tab-unmatched-inv', this)">Unmatched Invoices (<span id="count-unmatched-inv">0</span>)</button>
        <button class="tab-btn" onclick="switchTab('tab-unmatched-tx', this)">Unmatched Bank Feeds (<span id="count-unmatched-tx">0</span>)</button>
      </div>

      <div class="card-body">
        <!-- Tab 1: Matches -->
        <div id="tab-matches">
          <div class="table-container">
            <table>
              <thead>
                <tr>
                  <th>Match Type</th>
                  <th>Invoice ID(s)</th>
                  <th>Bank Tx ID</th>
                  <th>Invoice Sum</th>
                  <th>Cleared Amount</th>
                  <th>Variance</th>
                  <th>Confidence</th>
                  <th>Audit Reasoning</th>
                </tr>
              </thead>
              <tbody id="matches-tbody">
                <tr><td colspan="8" style="text-align: center; color: var(--text-muted); padding: 2rem;">No reconciliation run yet. Load sample data or upload documents.</td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Tab 2: Unmatched Invoices -->
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

        <!-- Tab 3: Unmatched Bank Feeds -->
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
                  <th>Action</th>
                </tr>
              </thead>
              <tbody id="unmatched-tx-tbody">
                <tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 2rem;">No unmatched bank transactions.</td></tr>
              </tbody>
            </table>
          </div>
        </div>

      </div>
    </div>

  </main>

  <!-- Manual Resolution Modal -->
  <div class="modal-overlay" id="resolve-modal">
    <div class="modal">
      <div class="modal-header">
        <h3 style="font-size: 1rem; font-weight: 600;">Manual Reconciliation Override</h3>
        <button onclick="closeModal()" style="background:none; border:none; cursor:pointer; font-size: 1.25rem;">&times;</button>
      </div>
      <div class="modal-body">
        <div class="form-group">
          <label class="form-label">Selected Invoice ID:</label>
          <input type="text" id="modal-invoice-id" class="form-input" readonly>
        </div>
        <div class="form-group">
          <label class="form-label">Link to Bank Transaction ID:</label>
          <select id="modal-tx-select" class="form-input"></select>
        </div>
        <div class="form-group">
          <label class="form-label">Audit Justification Note:</label>
          <textarea id="modal-reasoning" class="form-input" rows="3" placeholder="Enter reason (e.g., vendor trading name mismatch, verified with accounts payable)..."></textarea>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
        <button class="btn btn-primary" onclick="submitResolution()">Approve Match</button>
      </div>
    </div>
  </div>

  <script>
    let activeReport = null;

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
      document.getElementById('tab-unmatched-inv').style.display = 'none';
      document.getElementById('tab-unmatched-tx').style.display = 'none';
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
          fetchResults();
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
    }

    async function handleStatementUpload(file) {
      const formData = new FormData();
      formData.append('statement', file);
      document.getElementById('upload-status').innerText = `Uploading statement ${file.name}...`;
      const res = await fetch('/api/upload', { method: 'POST', body: formData });
      const data = await res.json();
      document.getElementById('upload-status').innerText = `Loaded statement (${data.transactions_loaded} transactions total).`;
    }

    // Trigger Reconciliation
    async function triggerReconciliation() {
      document.getElementById('upload-status').innerText = 'Running deterministic matching engine...';
      try {
        const res = await fetch('/api/reconcile', { method: 'POST' });
        const data = await res.json();
        if (data.status === 'success') {
          document.getElementById('upload-status').innerText = 'Reconciliation completed successfully.';
          fetchResults();
        }
      } catch (err) {
        alert('Reconciliation failed: ' + err);
      }
    }

    // Fetch and Render Results
    async function fetchResults() {
      const res = await fetch('/api/results');
      const data = await res.json();
      if (!data.report) return;
      activeReport = data.report;
      renderReport(activeReport);
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

      // Render Matches Table
      const mBody = document.getElementById('matches-tbody');
      if (report.matched_pairs.length === 0) {
        mBody.innerHTML = '<tr><td colspan="8" style="text-align:center; padding: 2rem;">No matches found.</td></tr>';
      } else {
        mBody.innerHTML = report.matched_pairs.map(m => {
          let badgeCls = 'badge-success';
          if (m.match_type === 'FEE_ADJUSTED') badgeCls = 'badge-warning';
          if (m.match_type === 'AGENT_RESOLVED') badgeCls = 'badge-info';

          return `
            <tr>
              <td><span class="badge ${badgeCls}">${m.match_type}</span></td>
              <td class="num">${m.invoice_ids.join(', ')}</td>
              <td class="num">${m.tx_id || 'N/A'}</td>
              <td class="num">$${m.invoice_total.toFixed(2)}</td>
              <td class="num">$${m.bank_amount.toFixed(2)}</td>
              <td class="num" style="color: ${m.variance_amount !== 0 ? 'var(--danger)' : 'inherit'}">$${m.variance_amount.toFixed(2)}</td>
              <td class="num">${(m.confidence_score * 100).toFixed(0)}%</td>
              <td style="font-size: 0.82rem; color: var(--text-secondary); max-width: 320px;">${m.audit_reasoning}</td>
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
              <button class="btn btn-secondary btn-sm" onclick="openResolveModal('${inv.doc_id}')">Resolve...</button>
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

    // Modal
    function openResolveModal(docId) {
      document.getElementById('modal-invoice-id').value = docId;
      const select = document.getElementById('modal-tx-select');
      select.innerHTML = '';
      if (activeReport && activeReport.unmatched_transactions) {
        activeReport.unmatched_transactions.forEach(t => {
          const opt = document.createElement('option');
          opt.value = t.tx_id;
          opt.innerText = `${t.tx_id} - ${t.counterparty} ($${t.amount.toFixed(2)})`;
          select.appendChild(opt);
        });
      }
      document.getElementById('resolve-modal').style.display = 'flex';
    }

    function closeModal() {
      document.getElementById('resolve-modal').style.display = 'none';
    }

    async function submitResolution() {
      const invoiceId = document.getElementById('modal-invoice-id').value;
      const txId = document.getElementById('modal-tx-select').value;
      const reasoning = document.getElementById('modal-reasoning').value || 'Manual reconciliation approved by accountant.';

      const res = await fetch('/api/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ invoice_id: invoiceId, tx_id: txId, reasoning: reasoning })
      });
      const data = await res.json();
      if (data.status === 'success') {
        closeModal();
        fetchResults();
      }
    }

    function exportReport(format) {
      window.location.href = `/api/export/${format}`;
    }

    // Check status on page load
    fetch('/api/status').then(r => r.json()).then(d => {
      if (d.has_active_report) {
        fetchResults();
      }
    });
  </script>
</body>
</html>
"""
