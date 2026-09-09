"""
Model Context Protocol (MCP) Server for ReconcileX.
Exposes standard financial reconciliation tools over stdio JSON-RPC.
"""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer

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
)
from reconcilex.core.reporting.excel_exporter import ExcelReportExporter
from reconcilex.core.reporting.markdown_exporter import MarkdownReportExporter

logger = logging.getLogger("reconcilex.mcp")

# Global in-memory state for active MCP session
_INVOICES: Dict[str, InvoiceRecord] = {}
_TRANSACTIONS: Dict[str, BankTransaction] = {}
_ACTIVE_REPORT: Optional[ReconciliationReport] = None


def create_mcp_server() -> MCPServer:
    """Instantiate and configure the ReconcileX MCP Server."""
    server = MCPServer("ReconcileX")

    @server.tool()
    def ingest_sources(paths: List[str]) -> str:
        """
        Scan directories or file paths to ingest invoices (PDF, images) and bank statements (CSV, XLSX).
        Extracts structured data and stores records in active reconciliation session.
        """
        global _INVOICES, _TRANSACTIONS, _ACTIVE_REPORT
        new_invoices = 0
        new_txs = 0
        errors = []

        for p_str in paths:
            p = Path(p_str).resolve()
            if not p.exists():
                errors.append(f"Path does not exist: {p_str}")
                continue

            files_to_process = [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file()]

            for f in files_to_process:
                ext = f.suffix.lower()
                try:
                    if ext in [".csv", ".xlsx", ".xls", ".tsv"]:
                        tx_list = BankStatementParser.parse(f)
                        for tx in tx_list:
                            _TRANSACTIONS[tx.tx_id] = tx
                            new_txs += 1
                    elif ext in [".pdf"]:
                        inv = PDFInvoiceParser.extract(f)
                        _INVOICES[inv.doc_id] = inv
                        new_invoices += 1
                    elif ext in [".png", ".jpg", ".jpeg", ".webp"]:
                        inv = OCREngine.extract_image_invoice(f)
                        _INVOICES[inv.doc_id] = inv
                        new_invoices += 1
                except Exception as ex:
                    errors.append(f"Error processing {f.name}: {str(ex)}")

        res = {
            "status": "success",
            "ingested_invoices_count": len(_INVOICES),
            "ingested_transactions_count": len(_TRANSACTIONS),
            "new_invoices_added": new_invoices,
            "new_transactions_added": new_txs,
            "errors": errors
        }
        return json.dumps(res, indent=2)

    @server.tool()
    def run_deterministic_match(
        tolerance_days: int = 3,
        fee_tolerance: float = 25.0
    ) -> str:
        """
        Run the 4-pass deterministic matching engine across all ingested invoices and bank feeds.
        Guarantees zero mathematical hallucination.
        """
        global _ACTIVE_REPORT
        if not _INVOICES and not _TRANSACTIONS:
            return json.dumps({"status": "error", "message": "No invoices or transactions ingested. Call ingest_sources first."})

        matcher = DeterministicMatcher(
            date_tolerance=tolerance_days,
            fee_tolerance=fee_tolerance
        )
        _ACTIVE_REPORT = matcher.reconcile(
            invoices=list(_INVOICES.values()),
            transactions=list(_TRANSACTIONS.values())
        )

        s = _ACTIVE_REPORT.summary
        res = {
            "status": "success",
            "match_rate_percentage": s.match_rate_percentage,
            "matched_count": s.matched_count,
            "unmatched_invoices_count": s.unmatched_invoices_count,
            "unmatched_transactions_count": s.unmatched_transactions_count,
            "total_invoiced_amount": s.total_invoiced_amount,
            "total_bank_amount": s.total_bank_amount,
            "net_variance": s.net_variance,
            "summary": s.model_dump()
        }
        return json.dumps(res, indent=2)

    @server.tool()
    def get_unmatched_records() -> str:
        """
        Retrieve lists of unlinked invoices and unmatched bank lines for agentic inspection.
        """
        global _ACTIVE_REPORT
        if not _ACTIVE_REPORT:
            return json.dumps({"status": "error", "message": "No reconciliation report available. Run run_deterministic_match first."})

        res = {
            "unmatched_invoices": [inv.model_dump() for inv in _ACTIVE_REPORT.unmatched_invoices],
            "unmatched_transactions": [tx.model_dump() for tx in _ACTIVE_REPORT.unmatched_transactions]
        }
        return json.dumps(res, indent=2, default=str)

    @server.tool()
    def resolve_ambiguity(
        invoice_id: str,
        tx_id: str,
        reasoning: str,
        fee_amount: float = 0.0
    ) -> str:
        """
        Link an ambiguous invoice to a bank transaction with verifiable audit justification.
        """
        global _ACTIVE_REPORT, _INVOICES, _TRANSACTIONS
        if not _ACTIVE_REPORT:
            return json.dumps({"status": "error", "message": "No active reconciliation report."})

        inv = _INVOICES.get(invoice_id)
        tx = _TRANSACTIONS.get(tx_id)

        if not inv:
            return json.dumps({"status": "error", "message": f"Invoice ID {invoice_id} not found."})
        if not tx:
            return json.dumps({"status": "error", "message": f"Transaction ID {tx_id} not found."})

        variance = round(tx.amount - inv.total_amount, 2)
        match_item = MatchResult(
            match_status=MatchStatus.MATCHED,
            match_type=MatchType.AGENT_RESOLVED if "agent" in reasoning.lower() else MatchType.MANUAL_OVERRIDE,
            invoice_ids=[inv.doc_id],
            tx_id=tx.tx_id,
            invoice_total=inv.total_amount,
            bank_amount=tx.amount,
            variance_amount=variance,
            confidence_score=0.90,
            rule_applied="AGENT_DISAMBIGUATION",
            audit_reasoning=reasoning
        )

        _ACTIVE_REPORT.matched_pairs.append(match_item)
        _ACTIVE_REPORT.unmatched_invoices = [i for i in _ACTIVE_REPORT.unmatched_invoices if i.doc_id != invoice_id]
        _ACTIVE_REPORT.unmatched_transactions = [t for t in _ACTIVE_REPORT.unmatched_transactions if t.tx_id != tx_id]

        _ACTIVE_REPORT.summary.matched_count = len(_ACTIVE_REPORT.matched_pairs)
        _ACTIVE_REPORT.summary.unmatched_invoices_count = len(_ACTIVE_REPORT.unmatched_invoices)
        _ACTIVE_REPORT.summary.unmatched_transactions_count = len(_ACTIVE_REPORT.unmatched_transactions)

        return json.dumps({"status": "success", "match": match_item.model_dump()}, default=str)

    @server.tool()
    def export_reconciliation_report(format: str = "excel") -> str:
        """
        Export the full reconciliation report to Excel (.xlsx) or Markdown (.md) with audit trails.
        """
        global _ACTIVE_REPORT
        if not _ACTIVE_REPORT:
            return json.dumps({"status": "error", "message": "No active report to export."})

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        export_dir = Path("./data/exports")
        export_dir.mkdir(parents=True, exist_ok=True)

        if format.lower() == "markdown":
            out_file = export_dir / f"reconciliation_report_{timestamp}.md"
            content = MarkdownReportExporter.export(_ACTIVE_REPORT, out_file)
            return json.dumps({"status": "success", "file_path": str(out_file), "content": content})
        else:
            out_file = export_dir / f"reconciliation_report_{timestamp}.xlsx"
            ExcelReportExporter.export(_ACTIVE_REPORT, out_file)
            return json.dumps({"status": "success", "file_path": str(out_file), "format": "excel"})

    return server


def run_server():
    """Launch the MCP server in stdio mode."""
    server = create_mcp_server()
    server.run()


if __name__ == "__main__":
    run_server()
