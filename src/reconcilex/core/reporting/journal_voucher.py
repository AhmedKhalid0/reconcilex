"""
Automated Double-Entry Journal Voucher (JV) Generator for Enterprise ERPs.
Generates balanced accounting entries for wire fees, variances, and unmatched items.
Compliant with ERPNext, Odoo, QuickBooks, and Xero.
"""

import csv
import io
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field

from reconcilex.core.models import MatchResult, MatchType, ReconciliationReport, TransactionDirection


class JournalEntryLine(BaseModel):
    posting_date: str
    voucher_type: str = "Journal Entry"
    account_code: str
    account_name: str
    debit: float = 0.0
    credit: float = 0.0
    counterparty: str = ""
    narration: str = ""
    reference: str = ""


class JournalVoucherReport(BaseModel):
    lines: List[JournalEntryLine] = Field(default_factory=list)
    total_debit: float = 0.0
    total_credit: float = 0.0
    is_balanced: bool = True


class JournalVoucherGenerator:
    """Generates balanced double-entry vouchers for GL adjustments."""

    BANK_ACCOUNT_CODE = "1010"
    BANK_ACCOUNT_NAME = "Cash at Bank - Operating"
    FEES_ACCOUNT_CODE = "5210"
    FEES_ACCOUNT_NAME = "Bank & Wire Transfer Fees Expense"
    SUSPENSE_ACCOUNT_CODE = "9999"
    SUSPENSE_ACCOUNT_NAME = "Suspense & Unreconciled Outflows"

    @classmethod
    def generate_voucher(cls, report: ReconciliationReport) -> JournalVoucherReport:
        lines: List[JournalEntryLine] = []
        gen_date = report.generated_at.strftime("%Y-%m-%d")

        # 1. Generate entries for fee-adjusted matches
        for m in report.matched_pairs:
            if m.match_type == MatchType.FEE_ADJUSTED and m.variance_amount != 0.0:
                fee_amt = round(abs(m.variance_amount), 2)
                ref_str = f"MATCH-{m.match_id[:8]} (TX:{m.tx_id})"

                # Debit: Bank Fee Expense
                lines.append(
                    JournalEntryLine(
                        posting_date=gen_date,
                        account_code=cls.FEES_ACCOUNT_CODE,
                        account_name=cls.FEES_ACCOUNT_NAME,
                        debit=fee_amt,
                        credit=0.0,
                        counterparty="Bank Processing Charge",
                        narration=f"Wire/merchant fee deduction on {ref_str}",
                        reference=m.tx_id or "N/A"
                    )
                )
                # Credit: Bank Account
                lines.append(
                    JournalEntryLine(
                        posting_date=gen_date,
                        account_code=cls.BANK_ACCOUNT_CODE,
                        account_name=cls.BANK_ACCOUNT_NAME,
                        debit=0.0,
                        credit=fee_amt,
                        counterparty="Operating Bank Account",
                        narration=f"Cash outflow for fee on {ref_str}",
                        reference=m.tx_id or "N/A"
                    )
                )

        # 2. Generate suspense entries for unsubstantiated bank withdrawals
        for tx in report.unmatched_transactions:
            if tx.direction == TransactionDirection.DEBIT:
                amt = round(tx.amount, 2)
                ref_str = tx.reference or tx.tx_id

                # Debit: Suspense / Unreconciled
                lines.append(
                    JournalEntryLine(
                        posting_date=tx.tx_date.strftime("%Y-%m-%d"),
                        account_code=cls.SUSPENSE_ACCOUNT_CODE,
                        account_name=cls.SUSPENSE_ACCOUNT_NAME,
                        debit=amt,
                        credit=0.0,
                        counterparty=tx.counterparty,
                        narration=f"Unreconciled bank disbursement: {tx.counterparty}",
                        reference=ref_str
                    )
                )
                # Credit: Bank Account
                lines.append(
                    JournalEntryLine(
                        posting_date=tx.tx_date.strftime("%Y-%m-%d"),
                        account_code=cls.BANK_ACCOUNT_CODE,
                        account_name=cls.BANK_ACCOUNT_NAME,
                        debit=0.0,
                        credit=amt,
                        counterparty="Operating Bank Account",
                        narration=f"Cash withdrawal cleared at bank ({tx.counterparty})",
                        reference=ref_str
                    )
                )

        total_deb = round(sum(line.debit for line in lines), 2)
        total_cred = round(sum(line.credit for line in lines), 2)
        is_balanced = abs(total_deb - total_cred) < 0.01

        return JournalVoucherReport(
            lines=lines,
            total_debit=total_deb,
            total_credit=total_cred,
            is_balanced=is_balanced
        )

    @classmethod
    def export_csv(cls, voucher: JournalVoucherReport, output_path: Optional[Path] = None) -> str:
        """Export journal voucher to CSV formatted for ERPNext, Odoo, and QuickBooks."""
        out = io.StringIO()
        fieldnames = [
            "Posting Date", "Voucher Type", "Account Code", "Account Name",
            "Debit", "Credit", "Counterparty", "Narration", "Reference"
        ]
        writer = csv.DictWriter(out, fieldnames=fieldnames)
        writer.writeheader()

        for line in voucher.lines:
            writer.writerow({
                "Posting Date": line.posting_date,
                "Voucher Type": line.voucher_type,
                "Account Code": line.account_code,
                "Account Name": line.account_name,
                "Debit": f"{line.debit:.2f}",
                "Credit": f"{line.credit:.2f}",
                "Counterparty": line.counterparty,
                "Narration": line.narration,
                "Reference": line.reference,
            })

        csv_content = out.getvalue()
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(csv_content, encoding="utf-8")

        return csv_content

    # Alias for backward compatibility
    generate = generate_voucher
