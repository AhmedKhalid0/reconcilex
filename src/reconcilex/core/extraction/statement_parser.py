"""
Bank Statement Parser for CSV, TSV, and Excel (XLSX/XLS).
Features intelligent schema detection and column mapping for multi-bank support.
"""

from datetime import date, datetime
from pathlib import Path
from typing import List, Optional
import pandas as pd

from reconcilex.core.models import BankTransaction, TransactionDirection


DATE_SYNONYMS = ["date", "trans_date", "transaction_date", "posting_date", "value_date", "valuta", "التاريخ", "تاريخ"]
DESC_SYNONYMS = ["description", "narration", "payee", "party", "memo", "details", "particulars", "counterparty", "البيان", "الوصف", "الطرف الثاني"]
AMOUNT_SYNONYMS = ["amount", "net", "net_amount", "total", "المبلغ", "القيمة"]
DEBIT_SYNONYMS = ["debit", "withdrawal", "outflow", "paid_out", "dr", "مدين", "سحب"]
CREDIT_SYNONYMS = ["credit", "deposit", "inflow", "paid_in", "cr", "دائن", "إيداع"]
BALANCE_SYNONYMS = ["balance", "ending_balance", "ledger_balance", "الرصيد"]
REF_SYNONYMS = ["ref", "reference", "txn_id", "transaction_id", "cheque_no", "check_num", "المرجع", "رقم العملية"]


def _match_column(columns: List[str], synonyms: List[str]) -> Optional[str]:
    for col in columns:
        cleaned = col.strip().lower().replace(" ", "_").replace("-", "_")
        for syn in synonyms:
            if syn in cleaned:
                return col
    return None


def _parse_date_cell(val: any) -> Optional[date]:
    if pd.isna(val):
        return None
    if isinstance(val, (datetime, pd.Timestamp)):
        return val.date()
    if isinstance(val, date):
        return val
    val_str = str(val).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d %b %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(val_str, fmt).date()
        except ValueError:
            pass
    return None


class BankStatementParser:
    """Parses bank statement files into standardized BankTransaction records."""

    @staticmethod
    def parse(file_path: Path) -> List[BankTransaction]:
        """Read CSV or Excel statement and return list of BankTransactions."""
        ext = file_path.suffix.lower()
        if ext in [".xlsx", ".xls"]:
            df = pd.read_excel(file_path)
        elif ext in [".tsv"]:
            df = pd.read_csv(file_path, sep="\t")
        else:
            # Default to CSV, auto-detect delimiter
            try:
                df = pd.read_csv(file_path)
            except Exception:
                df = pd.read_csv(file_path, sep=";")

        columns = list(df.columns)
        date_col = _match_column(columns, DATE_SYNONYMS)
        desc_col = _match_column(columns, DESC_SYNONYMS)
        amt_col = _match_column(columns, AMOUNT_SYNONYMS)
        debit_col = _match_column(columns, DEBIT_SYNONYMS)
        credit_col = _match_column(columns, CREDIT_SYNONYMS)
        bal_col = _match_column(columns, BALANCE_SYNONYMS)
        ref_col = _match_column(columns, REF_SYNONYMS)

        if not date_col:
            raise ValueError(f"Could not identify Date column in bank statement: {columns}")
        if not desc_col:
            raise ValueError(f"Could not identify Description/Counterparty column: {columns}")
        if not amt_col and not (debit_col or credit_col):
            raise ValueError(f"Could not identify Amount or Debit/Credit columns: {columns}")

        transactions: List[BankTransaction] = []

        for idx, row in df.iterrows():
            parsed_date = _parse_date_cell(row[date_col])
            if not parsed_date:
                continue

            counterparty = str(row[desc_col]).strip() if pd.notna(row[desc_col]) else "Unknown"

            # Determine amount and direction
            direction = TransactionDirection.DEBIT
            amount = 0.0

            if debit_col and pd.notna(row[debit_col]) and float(str(row[debit_col]).replace(",", "")) > 0:
                amount = float(str(row[debit_col]).replace(",", ""))
                direction = TransactionDirection.DEBIT
            elif credit_col and pd.notna(row[credit_col]) and float(str(row[credit_col]).replace(",", "")) > 0:
                amount = float(str(row[credit_col]).replace(",", ""))
                direction = TransactionDirection.CREDIT
            elif amt_col and pd.notna(row[amt_col]):
                raw_amt = float(str(row[amt_col]).replace(",", ""))
                if raw_amt < 0:
                    amount = abs(raw_amt)
                    direction = TransactionDirection.DEBIT
                else:
                    amount = raw_amt
                    direction = TransactionDirection.CREDIT

            if amount == 0.0:
                continue

            balance = None
            if bal_col and pd.notna(row[bal_col]):
                try:
                    balance = float(str(row[bal_col]).replace(",", ""))
                except ValueError:
                    pass

            ref = None
            if ref_col and pd.notna(row[ref_col]):
                ref = str(row[ref_col]).strip()

            tx_id = ref if ref else f"TXN-{file_path.stem[:4]}-{idx + 1:04d}"

            transactions.append(
                BankTransaction(
                    tx_id=tx_id,
                    tx_date=parsed_date,
                    counterparty=counterparty,
                    amount=round(amount, 2),
                    direction=direction,
                    balance=balance,
                    reference=ref,
                    raw_row=row.to_dict()
                )
            )

        return transactions
