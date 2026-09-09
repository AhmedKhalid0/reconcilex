"""
Digital PDF Invoice Parser using pdfplumber.
Extracts vector text, tables, amounts, dates, and metadata without LLM hallucination.
"""

from datetime import date, datetime
from pathlib import Path
import re
from typing import Optional, Tuple
import pdfplumber

from reconcilex.core.models import ExtractionMethod, InvoiceRecord


DATE_PATTERNS = [
    r"\b(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})\b",        # YYYY-MM-DD
    r"\b(\d{1,2}[-/.]\d{1,2}[-/.]\d{4})\b",        # DD-MM-YYYY or MM-DD-YYYY
    r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})\b",
]

AMOUNT_PATTERNS = [
    r"(?:Total|Grand Total|Total Due|Amount Due|Net Payable|المجموع|الإجمالي)[:\s]*(?:[\$€£]|USD|SAR|EUR|GBP|AED|ريال|درهم)?\s*([\d,]+\.\d{2})",
    r"(?:[\$€£]|USD|SAR|EUR|GBP|AED|ريال|درهم)\s*([\d,]+\.\d{2})\b",
    r"\b([\d,]+\.\d{2})\s*(?:USD|SAR|EUR|GBP|AED|ريال|درهم)\b",
]

TAX_PATTERNS = [
    r"(?:VAT|Tax|Sales Tax|ضريبة(?: القيمة المضافة)?)(?:\s*(?:/\s*Tax\s*)?\([^)]+\))?[:\s]*(?:[\$€£]|USD|SAR|EUR|GBP|AED|ريال|درهم)?\s*([\d,]+\.\d{2})",
]

INVOICE_NUM_PATTERNS = [
    r"(?:Invoice\s*(?:No|Number|#)|Inv\s*#|فاتورة\s*رقم)[:\s]*([A-Z0-9\-_]+)",
]


def parse_date(date_str: str) -> Optional[date]:
    """Parse common date formats into a standard date object."""
    date_str = date_str.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            pass
    return None


class PDFInvoiceParser:
    """Extracts structured invoice records from native digital PDF files."""

    @staticmethod
    def extract(file_path: Path) -> InvoiceRecord:
        """Parse a digital PDF and return a normalized InvoiceRecord."""
        full_text = ""
        tables_data = []

        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                full_text += text + "\n"
                extracted_tables = page.extract_tables()
                if extracted_tables:
                    tables_data.extend(extracted_tables)

        doc_id = PDFInvoiceParser._extract_invoice_number(full_text, file_path.stem)
        vendor_name = PDFInvoiceParser._extract_vendor(full_text, file_path.stem)
        inv_date = PDFInvoiceParser._extract_date(full_text) or date.today()
        total_amount, tax_amount = PDFInvoiceParser._extract_amounts(full_text)
        currency = PDFInvoiceParser._extract_currency(full_text)
        tax_id = PDFInvoiceParser._extract_tax_id(full_text)

        return InvoiceRecord(
            doc_id=doc_id,
            source_path=str(file_path),
            vendor_name=vendor_name,
            vendor_tax_id=tax_id,
            invoice_date=inv_date,
            subtotal=round(total_amount - tax_amount, 2) if total_amount > tax_amount else None,
            tax_amount=tax_amount,
            total_amount=total_amount,
            currency=currency,
            raw_text=full_text.strip(),
            extraction_method=ExtractionMethod.DIGITAL_PDF
        )

    @staticmethod
    def _extract_invoice_number(text: str, default: str) -> str:
        for pattern in INVOICE_NUM_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return default

    @staticmethod
    def _extract_vendor(text: str, fallback: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines[:5]:
            # Avoid generic words
            if not any(k in line.lower() for k in ["invoice", "tax", "bill to", "date", "فاتورة"]):
                # Clean line from punctuation
                candidate = re.sub(r"[^\w\s\.\-&]", "", line).strip()
                if len(candidate) > 2:
                    return candidate
        return fallback.replace("_", " ").title()

    @staticmethod
    def _extract_date(text: str) -> Optional[date]:
        for pattern in DATE_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                parsed = parse_date(match.group(1))
                if parsed:
                    return parsed
        return None

    @staticmethod
    def _extract_amounts(text: str) -> Tuple[float, float]:
        total = 0.0
        tax = 0.0

        for pattern in AMOUNT_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                # Take last match (typically totals are at the end of the page)
                raw_amt = matches[-1].replace(",", "")
                try:
                    total = float(raw_amt)
                    break
                except ValueError:
                    pass

        for pattern in TAX_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                raw_tax = match.group(1).replace(",", "")
                try:
                    tax = float(raw_tax)
                except ValueError:
                    pass

        return total, tax

    @staticmethod
    def _extract_currency(text: str) -> str:
        if "SAR" in text or "ريال" in text:
            return "SAR"
        if "EUR" in text or "€" in text:
            return "EUR"
        if "GBP" in text or "£" in text:
            return "GBP"
        if "AED" in text or "درهم" in text:
            return "AED"
        return "USD"

    @staticmethod
    def _extract_tax_id(text: str) -> Optional[str]:
        pattern = r"(?:Tax\s*ID(?:\s*/\s*VAT)?|VAT\s*(?:ID|No|Number)?|الرقم\s*الضريبي)[:\s]*([0-9A-Z]{6,25})"
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1) if match else None
