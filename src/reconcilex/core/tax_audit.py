"""
Automated Tax & ZATCA / VAT Compliance Auditor.
Performs mathematical verification on invoice taxes and generates cryptographic audit hashes.
"""

from decimal import Decimal
import hashlib
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel

from reconcilex.core.models import InvoiceRecord


class TaxAuditResult(BaseModel):
    doc_id: str
    is_compliant: bool
    detected_rate_percent: float
    expected_tax: float
    actual_tax: float
    variance: float
    status_label: str
    sha256_fingerprint: str
    audit_notes: str


def compute_file_sha256(file_path: Path) -> str:
    """Generate SHA-256 cryptographic hash of document file."""
    if not file_path.exists():
        return "FILE_NOT_FOUND"
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class TaxAuditor:
    """Verifies invoice tax calculations against regional tax rules (ZATCA, GCC, Egypt, etc.)."""

    REGIONAL_RATES = {
        "SAR": 0.15,  # Saudi Arabia ZATCA 15%
        "AED": 0.05,  # UAE 5%
        "EGP": 0.14,  # Egypt 14%
        "GBP": 0.20,  # UK 20%
        "EUR": 0.20,  # Standard EU benchmark 20%
        "USD": 0.00,  # US Sales tax varies by state (default benchmark)
    }

    @classmethod
    def audit_invoice(
        cls,
        invoice: InvoiceRecord,
        default_rate: float = 0.15,
        tolerance: float = 0.05
    ) -> TaxAuditResult:
        """Inspect and mathematically verify an invoice's tax calculation."""
        file_path = Path(invoice.source_path)
        sha256_hash = compute_file_sha256(file_path) if file_path.exists() else hashlib.sha256(invoice.doc_id.encode()).hexdigest()

        rate = cls.REGIONAL_RATES.get(invoice.currency.upper(), default_rate)
        actual_tax = round(invoice.tax_amount or 0.0, 2)
        total = invoice.total_amount

        # Calculate expected tax
        if invoice.subtotal and invoice.subtotal > 0:
            expected_tax = round(invoice.subtotal * rate, 2)
        else:
            # Derive subtotal from total assuming rate: Total = Subtotal * (1 + rate)
            expected_subtotal = total / (1.0 + rate)
            expected_tax = round(total - expected_subtotal, 2)

        if actual_tax == 0.0:
            # Exempt or zero-rated invoice
            return TaxAuditResult(
                doc_id=invoice.doc_id,
                is_compliant=True,
                detected_rate_percent=0.0,
                expected_tax=0.0,
                actual_tax=0.0,
                variance=0.0,
                status_label="ZERO_RATED_OR_EXEMPT",
                sha256_fingerprint=sha256_hash,
                audit_notes="Zero tax recorded or tax exempt transaction."
            )

        variance = round(abs(actual_tax - expected_tax), 2)
        is_compliant = variance <= tolerance

        if is_compliant:
            status_label = f"COMPLIANT_{int(rate * 100)}PCT_VAT"
            notes = f"Verified: Math matches {int(rate * 100)}% standard VAT rate (actual: {actual_tax}, expected: {expected_tax})."
        else:
            status_label = "TAX_DISCREPANCY_FLAGGED"
            notes = (
                f"Tax Warning: Calculated tax ({actual_tax}) differs from expected "
                f"{int(rate * 100)}% VAT ({expected_tax}) by {variance:.2f} {invoice.currency}."
            )

        return TaxAuditResult(
            doc_id=invoice.doc_id,
            is_compliant=is_compliant,
            detected_rate_percent=round(rate * 100, 1),
            expected_tax=expected_tax,
            actual_tax=actual_tax,
            variance=variance,
            status_label=status_label,
            sha256_fingerprint=sha256_hash,
            audit_notes=notes
        )
