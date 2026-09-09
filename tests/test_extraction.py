"""
Unit tests for Ingestion and Parsing Engines.
"""

from datetime import date
from pathlib import Path
import pytest
from reconcilex.core.extraction.pdf_parser import PDFInvoiceParser
from reconcilex.core.extraction.statement_parser import BankStatementParser
from reconcilex.utils.sample_generator import SampleDataGenerator


@pytest.fixture(scope="module")
def sample_data():
    base_dir = Path("./data/test_tmp/extraction_tests")
    base_dir.mkdir(parents=True, exist_ok=True)
    inv_dir, stmt_file = SampleDataGenerator.generate_all(base_dir)
    return inv_dir, stmt_file


def test_pdf_invoice_extraction(sample_data):
    inv_dir, _ = sample_data
    acme_pdf = inv_dir / "INV-2026-001_Acme.pdf"
    assert acme_pdf.exists()

    record = PDFInvoiceParser.extract(acme_pdf)
    assert record.doc_id == "INV-2026-001"
    assert "Acme" in record.vendor_name
    assert record.total_amount == 1450.00
    assert record.invoice_date == date(2026, 2, 15)
    assert record.currency == "USD"
    assert record.vendor_tax_id is not None or "US98214410" in (record.vendor_tax_id or "")


def test_bank_statement_csv_parsing(sample_data):
    _, stmt_file = sample_data
    assert stmt_file.exists()

    transactions = BankStatementParser.parse(stmt_file)
    assert len(transactions) >= 6

    tx1 = transactions[0]
    assert tx1.amount == 1450.00
    assert "ACME" in tx1.counterparty
    assert tx1.tx_date == date(2026, 2, 16)
    assert tx1.direction.value == "DEBIT"
