"""
Synthetic Dataset Generator for ReconcileX Testing.
Produces realistic digital PDFs, bilingual Arabic/English receipt images,
and bank statement CSVs covering exact, fuzzy, bundled, and fee edge cases.
"""

from datetime import date
from pathlib import Path
from typing import Tuple
from PIL import Image, ImageDraw, ImageFont
import pandas as pd
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


class SampleDataGenerator:
    """Generates synthetic test documents and bank feeds for end-to-end audit testing."""

    @classmethod
    def generate_all(cls, base_dir: Path) -> Tuple[Path, Path]:
        invoices_dir = base_dir / "invoices"
        invoices_dir.mkdir(parents=True, exist_ok=True)
        stmt_dir = base_dir / "statements"
        stmt_dir.mkdir(parents=True, exist_ok=True)

        # 1. Generate Digital PDF Invoices
        cls._create_pdf_invoice(
            invoices_dir / "INV-2026-001_Acme.pdf",
            doc_id="INV-2026-001",
            vendor="Acme Cloud Hosting LLC",
            inv_date="2026-02-15",
            tax_id="US98214410",
            total="1,450.00",
            tax="188.47",
            currency="USD"
        )
        cls._create_pdf_invoice(
            invoices_dir / "INV-2026-002_Vercel.pdf",
            doc_id="INV-2026-002",
            vendor="Vercel Platforms Inc",
            inv_date="2026-02-18",
            tax_id="US77102941",
            total="220.00",
            tax="28.70",
            currency="USD"
        )
        cls._create_pdf_invoice(
            invoices_dir / "INV-2026-003_AWS.pdf",
            doc_id="INV-2026-003",
            vendor="AWS Infrastructure EMEA SARL",
            inv_date="2026-02-20",
            tax_id="LU20260192",
            total="3,210.50",
            tax="418.76",
            currency="USD"
        )
        # Bundled pair
        cls._create_pdf_invoice(
            invoices_dir / "INV-2026-004_Datadog_Part1.pdf",
            doc_id="INV-2026-004",
            vendor="Datadog Monitoring Systems",
            inv_date="2026-02-10",
            tax_id="US33190288",
            total="800.00",
            tax="104.35",
            currency="USD"
        )
        cls._create_pdf_invoice(
            invoices_dir / "INV-2026-005_Datadog_Part2.pdf",
            doc_id="INV-2026-005",
            vendor="Datadog Monitoring Systems",
            inv_date="2026-02-12",
            tax_id="US33190288",
            total="450.00",
            tax="58.70",
            currency="USD"
        )
        # Wire fee case ($5,000 invoice, $4,980 cleared)
        cls._create_pdf_invoice(
            invoices_dir / "INV-2026-006_Freight.pdf",
            doc_id="INV-2026-006",
            vendor="Global Freight Logistics Ltd",
            inv_date="2026-02-22",
            tax_id="GB88192033",
            total="5,000.00",
            tax="652.17",
            currency="USD"
        )
        # Unmatched invoice (issued, uncollected/unpaid)
        cls._create_pdf_invoice(
            invoices_dir / "INV-2026-007_Oracle_Unpaid.pdf",
            doc_id="INV-2026-007",
            vendor="Oracle Database Systems Corp",
            inv_date="2026-02-25",
            tax_id="US10293847",
            total="4,500.00",
            tax="586.95",
            currency="USD"
        )

        # 2. Generate Bilingual Scanned Image Invoice (English + Arabic)
        cls._create_bilingual_image_invoice(
            invoices_dir / "INV-2026-008_STC_Bilingual.png",
            doc_id="INV-2026-008",
            vendor_en="Saudi Telecom Solutions",
            vendor_ar="شركة الاتصالات السعودية",
            inv_date="2026-02-14",
            total="750.00"
        )

        # 3. Generate Bank Statement CSV
        stmt_file = stmt_dir / "bank_statement_feb_2026.csv"
        cls._create_bank_statement_csv(stmt_file)

        return invoices_dir, stmt_file

    @staticmethod
    def _create_pdf_invoice(path: Path, doc_id: str, vendor: str, inv_date: str, tax_id: str, total: str, tax: str, currency: str):
        c = canvas.Canvas(str(path), pagesize=letter)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(50, 750, vendor)

        c.setFont("Helvetica", 10)
        c.drawString(50, 730, f"Tax ID / VAT: {tax_id}")
        c.drawString(50, 715, "100 Enterprise Way, Suite 400")

        c.setFont("Helvetica-Bold", 12)
        c.drawString(400, 750, "TAX INVOICE")
        c.setFont("Helvetica", 10)
        c.drawString(400, 730, f"Invoice Number: {doc_id}")
        c.drawString(400, 715, f"Date: {inv_date}")

        # Table Header
        c.line(50, 680, 550, 680)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(60, 665, "Description")
        c.drawString(350, 665, "Quantity")
        c.drawString(480, 665, "Amount")
        c.line(50, 655, 550, 655)

        # Line item
        c.setFont("Helvetica", 10)
        c.drawString(60, 635, f"Professional Cloud Services ({doc_id})")
        c.drawString(360, 635, "1.0")
        c.drawString(480, 635, f"{currency} {total}")

        # Totals
        c.line(350, 600, 550, 600)
        c.drawString(360, 580, "VAT / Tax (15%):")
        c.drawString(480, 580, f"{currency} {tax}")
        c.setFont("Helvetica-Bold", 11)
        c.drawString(360, 555, "Total Due:")
        c.drawString(480, 555, f"{currency} {total}")
        c.line(350, 545, 550, 545)

        c.save()

    @staticmethod
    def _create_bilingual_image_invoice(path: Path, doc_id: str, vendor_en: str, vendor_ar: str, inv_date: str, total: str):
        img = Image.new("RGB", (800, 600), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)

        # Draw decorative invoice header
        draw.rectangle([(30, 30), (770, 570)], outline=(200, 205, 215), width=2)
        draw.rectangle([(30, 30), (770, 100)], fill=(245, 247, 250))

        # Text simulation
        draw.text((50, 45), vendor_en, fill=(20, 30, 50))
        draw.text((50, 65), vendor_ar, fill=(60, 70, 90))

        draw.text((550, 45), "TAX INVOICE / فاتورة ضريبية", fill=(20, 30, 50))
        draw.text((550, 65), f"Invoice Number: {doc_id}", fill=(60, 70, 90))
        draw.text((550, 80), f"Date: {inv_date}", fill=(60, 70, 90))

        # Items
        draw.line([(50, 140), (750, 140)], fill=(180, 185, 195), width=1)
        draw.text((60, 155), "Service Subscription / اشتراك خدمات اتصالات", fill=(30, 40, 60))
        draw.text((650, 155), f"${total}", fill=(30, 40, 60))
        draw.line([(50, 180), (750, 180)], fill=(180, 185, 195), width=1)

        # Grand Total
        draw.rectangle([(480, 220), (750, 270)], fill=(240, 245, 255), outline=(180, 200, 230))
        draw.text((500, 235), "Total Due / المجموع الكلي:", fill=(15, 23, 42))
        draw.text((660, 235), f"${total}", fill=(15, 23, 42))

        # Stamp
        draw.ellipse([(600, 400), (720, 520)], outline=(16, 185, 129), width=3)
        draw.text((625, 450), "PAID / مسدد", fill=(16, 185, 129))

        img.save(path)

    @staticmethod
    def _create_bank_statement_csv(path: Path):
        data = [
            {
                "Posting Date": "2026-02-16",
                "Description": "STRIPE *ACME CLOUD HOSTING",
                "Debit": 1450.00,
                "Credit": 0.0,
                "Balance": 84550.00,
                "Reference": "TXN-882101"
            },
            {
                "Posting Date": "2026-02-19",
                "Description": "VERCEL PLATFORMS INC PAYMENTS",
                "Debit": 220.00,
                "Credit": 0.0,
                "Balance": 84330.00,
                "Reference": "TXN-882102"
            },
            {
                "Posting Date": "2026-02-22",
                "Description": "AMAZON WEB SERVICES AWS EMEA",
                "Debit": 3210.50,
                "Credit": 0.0,
                "Balance": 81119.50,
                "Reference": "TXN-882103"
            },
            {
                "Posting Date": "2026-02-15",
                "Description": "DATADOG INC BATCH CONSOLIDATED",
                "Debit": 1250.00,
                "Credit": 0.0,
                "Balance": 79869.50,
                "Reference": "TXN-882104"
            },
            {
                "Posting Date": "2026-02-24",
                "Description": "GLOBAL FREIGHT WIRE TRF LESS $20 FEE",
                "Debit": 4980.00,
                "Credit": 0.0,
                "Balance": 74889.50,
                "Reference": "TXN-882105"
            },
            {
                "Posting Date": "2026-02-16",
                "Description": "STC PAY RECHARGE - SAUDI TELECOM",
                "Debit": 750.00,
                "Credit": 0.0,
                "Balance": 74139.50,
                "Reference": "TXN-882106"
            },
            {
                "Posting Date": "2026-02-27",
                "Description": "BANK CHARGE / UNMATCHED ATM WITHDRAWAL",
                "Debit": 300.00,
                "Credit": 0.0,
                "Balance": 73839.50,
                "Reference": "TXN-882107"
            }
        ]
        df = pd.DataFrame(data)
        df.to_csv(path, index=False)
