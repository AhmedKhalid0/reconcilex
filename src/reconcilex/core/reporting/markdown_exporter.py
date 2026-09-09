"""
Markdown Reconciliation Dossier Exporter.
Produces clean, auditable Markdown summaries for CLI outputs and MCP clients.
"""

from pathlib import Path
from reconcilex.core.models import ReconciliationReport


class MarkdownReportExporter:
    """Generates structured Markdown reconciliation summaries."""

    @staticmethod
    def export(report: ReconciliationReport, output_path: Path = None) -> str:
        s = report.summary
        md = []
        md.append("# 📊 ReconcileX Audit & Reconciliation Dossier")
        md.append(f"**Session ID:** `{report.session_id}` | **Generated:** {report.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n")

        md.append("## 📈 Executive Summary")
        md.append("| Metric | Value |")
        md.append("| :--- | :--- |")
        md.append(f"| **Match Rate** | **{s.match_rate_percentage:.1f}%** |")
        md.append(f"| **Matched Pairs** | {s.matched_count} |")
        md.append(f"| **Unmatched Invoices** | {s.unmatched_invoices_count} |")
        md.append(f"| **Unmatched Bank Transactions** | {s.unmatched_transactions_count} |")
        md.append(f"| **Total Invoiced Value** | ${s.total_invoiced_amount:,.2f} |")
        md.append(f"| **Total Cleared Bank Value** | ${s.total_bank_amount:,.2f} |")
        md.append(f"| **Net Reconciliation Variance** | ${s.net_variance:,.2f} |\n")

        md.append("## ✅ Reconciled Matches")
        if not report.matched_pairs:
            md.append("*No matched transactions found.*")
        else:
            md.append("| Match ID | Type | Invoices | Bank Tx | Amount | Conf. | Audit Reasoning |")
            md.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
            for m in report.matched_pairs:
                inv_str = ", ".join(f"`{i}`" for i in m.invoice_ids)
                md.append(
                    f"| `{m.match_id[:8]}` | {m.match_type.value} | {inv_str} | `{m.tx_id}` | "
                    f"${m.bank_amount:,.2f} | {m.confidence_score * 100:.0f}% | {m.audit_reasoning} |"
                )
            md.append("")

        if report.unmatched_invoices:
            md.append("## ⚠️ Unmatched Invoices (Action Required)")
            md.append("| Doc ID | Vendor | Date | Total | Tax ID | Source |")
            md.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
            for inv in report.unmatched_invoices:
                md.append(
                    f"| `{inv.doc_id}` | {inv.vendor_name} | {inv.invoice_date} | "
                    f"${inv.total_amount:,.2f} | {inv.vendor_tax_id or 'N/A'} | `{inv.source_path}` |"
                )
            md.append("")

        if report.unmatched_transactions:
            md.append("## 🚨 Unmatched Bank Line Items (Unsubstantiated Withdrawals/Deposits)")
            md.append("| Tx ID | Date | Counterparty / Narration | Amount | Direction | Ref |")
            md.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
            for tx in report.unmatched_transactions:
                md.append(
                    f"| `{tx.tx_id}` | {tx.tx_date} | {tx.counterparty} | "
                    f"${tx.amount:,.2f} | {tx.direction.value} | {tx.reference or 'N/A'} |"
                )
            md.append("")

        content = "\n".join(md)
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content, encoding="utf-8")
        return content
