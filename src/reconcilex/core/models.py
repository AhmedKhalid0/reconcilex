"""
Domain models and schemas for ReconcileX.
Built with Pydantic V2 for strict validation and serialization.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4
from pydantic import BaseModel, Field, field_validator


class ExtractionMethod(str, Enum):
    DIGITAL_PDF = "digital_pdf"
    LOCAL_OCR = "local_ocr"
    VLM_VISION = "vlm_vision"
    CSV_IMPORT = "csv_import"
    MANUAL = "manual"


class TransactionDirection(str, Enum):
    DEBIT = "DEBIT"    # Money out / payment to vendor
    CREDIT = "CREDIT"  # Money in / customer payment


class MatchStatus(str, Enum):
    MATCHED = "MATCHED"
    AMBIGUOUS = "AMBIGUOUS"
    UNMATCHED_INVOICE = "UNMATCHED_INVOICE"
    UNMATCHED_BANK = "UNMATCHED_BANK"


class MatchType(str, Enum):
    EXACT_1TO1 = "EXACT_1TO1"
    FUZZY_1TO1 = "FUZZY_1TO1"
    BUNDLED_1TON = "BUNDLED_1TON"
    FEE_ADJUSTED = "FEE_ADJUSTED"
    AGENT_RESOLVED = "AGENT_RESOLVED"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"


class InvoiceItem(BaseModel):
    description: str
    quantity: float = 1.0
    unit_price: float = 0.0
    amount: float = 0.0


class InvoiceRecord(BaseModel):
    doc_id: str = Field(..., description="Unique invoice ID or filename")
    source_path: str = Field(..., description="Path to source document")
    vendor_name: str = Field(..., description="Extracted vendor/supplier name")
    vendor_tax_id: Optional[str] = Field(default=None, description="Tax / VAT registration number")
    invoice_date: date = Field(..., description="Date on the invoice")
    due_date: Optional[date] = Field(default=None, description="Payment due date")
    subtotal: Optional[float] = Field(default=None, description="Subtotal before taxes")
    tax_amount: Optional[float] = Field(default=None, description="Tax / VAT amount")
    total_amount: float = Field(..., description="Total invoice liability amount")
    currency: str = Field(default="USD", description="Currency code (USD, SAR, EUR, etc.)")
    items: List[InvoiceItem] = Field(default_factory=list)
    raw_text: Optional[str] = Field(default=None, description="Extracted raw OCR / PDF text")
    extraction_method: ExtractionMethod = ExtractionMethod.DIGITAL_PDF

    @field_validator("total_amount", mode="before")
    @classmethod
    def round_total(cls, v: Any) -> float:
        return round(float(v), 2)


class BankTransaction(BaseModel):
    tx_id: str = Field(..., description="Unique transaction ID")
    tx_date: date = Field(..., description="Settlement / value date")
    counterparty: str = Field(..., description="Payee / description in statement")
    amount: float = Field(..., description="Absolute transaction value")
    direction: TransactionDirection = Field(default=TransactionDirection.DEBIT)
    balance: Optional[float] = Field(default=None)
    reference: Optional[str] = Field(default=None)
    raw_row: Optional[Dict[str, Any]] = Field(default=None)

    @field_validator("amount", mode="before")
    @classmethod
    def round_amount(cls, v: Any) -> float:
        return round(abs(float(v)), 2)


class MatchResult(BaseModel):
    match_id: str = Field(default_factory=lambda: str(uuid4()))
    match_status: MatchStatus = MatchStatus.MATCHED
    match_type: MatchType = MatchType.EXACT_1TO1
    invoice_ids: List[str] = Field(default_factory=list)
    tx_id: Optional[str] = None
    invoice_total: float = 0.0
    bank_amount: float = 0.0
    variance_amount: float = 0.0  # bank_amount - invoice_total
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    audit_reasoning: str = ""
    rule_applied: str = "P1_EXACT"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReconciliationSummary(BaseModel):
    total_invoices: int = 0
    total_transactions: int = 0
    matched_count: int = 0
    ambiguous_count: int = 0
    unmatched_invoices_count: int = 0
    unmatched_transactions_count: int = 0
    match_rate_percentage: float = 0.0
    total_invoiced_amount: float = 0.0
    total_bank_amount: float = 0.0
    matched_amount: float = 0.0
    net_variance: float = 0.0


class ReconciliationReport(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid4()))
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    summary: ReconciliationSummary
    matched_pairs: List[MatchResult] = Field(default_factory=list)
    ambiguous_items: List[MatchResult] = Field(default_factory=list)
    unmatched_invoices: List[InvoiceRecord] = Field(default_factory=list)
    unmatched_transactions: List[BankTransaction] = Field(default_factory=list)
