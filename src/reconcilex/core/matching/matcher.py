"""
Deterministic Reconciliation Matching Engine.
Executes high-speed, zero-hallucination multi-pass matching algorithms.
"""

from collections import defaultdict
from datetime import date
import re
from typing import Dict, List, Set, Tuple
from rapidfuzz import fuzz

from reconcilex.config import settings
from reconcilex.core.matching.combinatorics import find_bundled_combination
from reconcilex.core.models import (
    BankTransaction,
    InvoiceRecord,
    MatchResult,
    MatchStatus,
    MatchType,
    ReconciliationReport,
    ReconciliationSummary,
)


NOISE_WORDS = {
    "inc", "llc", "ltd", "corp", "corporation", "batch", "trf", "transfer",
    "wire", "less", "fee", "fees", "payments", "payment", "consolidated",
    "systems", "solutions", "sarl", "gmbh", "holding", "holdings", "services",
    "pay", "recharge", "direct", "debit", "ach", "chq", "check"
}


def _clean_financial_name(s: str) -> str:
    if not s:
        return ""
    tokens = re.findall(r"\b[A-Za-z0-9\u0600-\u06FF]{2,}\b", s.lower())
    filtered = [t for t in tokens if t not in NOISE_WORDS]
    return " ".join(filtered) if filtered else " ".join(tokens)


def _days_diff(d1: date, d2: date) -> int:
    return abs((d1 - d2).days)


def _calc_similarity(s1: str, s2: str) -> float:
    if not s1 or not s2:
        return 0.0
    c1 = s1.lower().strip()
    c2 = s2.lower().strip()

    raw_score = max(
        fuzz.token_set_ratio(c1, c2),
        fuzz.partial_ratio(c1, c2)
    )

    clean1 = _clean_financial_name(s1)
    clean2 = _clean_financial_name(s2)
    if clean1 and clean2:
        cleaned_score = max(
            fuzz.token_set_ratio(clean1, clean2),
            fuzz.partial_ratio(clean1, clean2)
        )
        return max(raw_score, cleaned_score)

    return raw_score


class DeterministicMatcher:
    """Orchestrates multi-pass deterministic reconciliation matching."""

    def __init__(
        self,
        date_tolerance: int = None,
        relaxed_tolerance: int = None,
        vendor_threshold: float = None,
        fee_tolerance: float = None,
        max_bundle: int = None,
    ):
        self.date_tolerance = date_tolerance or settings.date_tolerance_days
        self.relaxed_tolerance = relaxed_tolerance or settings.relaxed_date_tolerance_days
        self.vendor_threshold = vendor_threshold or settings.vendor_similarity_threshold
        self.fee_tolerance = fee_tolerance or settings.fee_tolerance_amount
        self.max_bundle = max_bundle or settings.max_bundle_combinations

    def reconcile(
        self,
        invoices: List[InvoiceRecord],
        transactions: List[BankTransaction],
    ) -> ReconciliationReport:
        """Run all 4 deterministic passes and return a structured reconciliation report."""
        matched_pairs: List[MatchResult] = []
        ambiguous_items: List[MatchResult] = []

        used_invoices: Set[str] = set()
        used_transactions: Set[str] = set()

        # Build lookup indexes
        inv_map: Dict[str, InvoiceRecord] = {inv.doc_id: inv for inv in invoices}
        tx_map: Dict[str, BankTransaction] = {tx.tx_id: tx for tx in transactions}

        # -------------------------------------------------------------
        # PASS 1: Exact 1-to-1 Match (Amount equal, tight date, high similarity)
        # -------------------------------------------------------------
        for tx in transactions:
            if tx.tx_id in used_transactions:
                continue

            for inv in invoices:
                if inv.doc_id in used_invoices:
                    continue

                if inv.total_amount == tx.amount:
                    d_diff = _days_diff(inv.invoice_date, tx.tx_date)
                    if d_diff <= self.date_tolerance:
                        sim = _calc_similarity(inv.vendor_name, tx.counterparty)
                        if sim >= self.vendor_threshold:
                            used_invoices.add(inv.doc_id)
                            used_transactions.add(tx.tx_id)
                            matched_pairs.append(
                                MatchResult(
                                    match_status=MatchStatus.MATCHED,
                                    match_type=MatchType.EXACT_1TO1,
                                    invoice_ids=[inv.doc_id],
                                    tx_id=tx.tx_id,
                                    invoice_total=inv.total_amount,
                                    bank_amount=tx.amount,
                                    variance_amount=0.0,
                                    confidence_score=round(sim / 100.0, 2),
                                    rule_applied="P1_EXACT_AMOUNT_DATE_VENDOR",
                                    audit_reasoning=(
                                        f"Exact amount match ({inv.total_amount} {inv.currency}). "
                                        f"Cleared in {d_diff} days. Counterparty match score: {sim:.1f}%."
                                    ),
                                )
                            )
                            break

        # -------------------------------------------------------------
        # PASS 2: Relaxed Fuzzy 1-to-1 Match (Extended date, moderate vendor similarity)
        # -------------------------------------------------------------
        for tx in transactions:
            if tx.tx_id in used_transactions:
                continue

            best_match = None
            best_score = 0.0

            for inv in invoices:
                if inv.doc_id in used_invoices:
                    continue

                if inv.total_amount == tx.amount:
                    d_diff = _days_diff(inv.invoice_date, tx.tx_date)
                    if d_diff <= self.relaxed_tolerance:
                        sim = _calc_similarity(inv.vendor_name, tx.counterparty)
                        if sim >= (self.vendor_threshold - 10.0) and sim > best_score:
                            best_match = (inv, d_diff, sim)
                            best_score = sim

            if best_match:
                inv, d_diff, sim = best_match
                used_invoices.add(inv.doc_id)
                used_transactions.add(tx.tx_id)
                matched_pairs.append(
                    MatchResult(
                        match_status=MatchStatus.MATCHED,
                        match_type=MatchType.FUZZY_1TO1,
                        invoice_ids=[inv.doc_id],
                        tx_id=tx.tx_id,
                        invoice_total=inv.total_amount,
                        bank_amount=tx.amount,
                        variance_amount=0.0,
                        confidence_score=round(sim / 100.0, 2),
                        rule_applied="P2_RELAXED_WINDOW_MATCH",
                        audit_reasoning=(
                            f"Fuzzy 1-to-1 match. Amount: {inv.total_amount}. "
                            f"Cleared in {d_diff} days. Vendor similarity: {sim:.1f}%."
                        ),
                    )
                )

        # -------------------------------------------------------------
        # PASS 3: Bundled 1-to-N Matching (Combinatorial Subset-Sum)
        # -------------------------------------------------------------
        remaining_invoices = [inv for inv in invoices if inv.doc_id not in used_invoices]

        for tx in transactions:
            if tx.tx_id in used_transactions:
                continue

            # Group remaining invoices with similar counterparty
            candidate_pool = [
                inv for inv in remaining_invoices
                if inv.doc_id not in used_invoices
                and _days_diff(inv.invoice_date, tx.tx_date) <= settings.bundle_date_window_days
                and _calc_similarity(inv.vendor_name, tx.counterparty) >= (self.vendor_threshold - 15.0)
            ]

            combo_res = find_bundled_combination(
                target_amount=tx.amount,
                candidate_invoices=candidate_pool,
                max_bundle_size=self.max_bundle,
            )

            if combo_res:
                bundle_invs, bundle_sum = combo_res
                b_ids = [b.doc_id for b in bundle_invs]
                for b_id in b_ids:
                    used_invoices.add(b_id)
                used_transactions.add(tx.tx_id)

                matched_pairs.append(
                    MatchResult(
                        match_status=MatchStatus.MATCHED,
                        match_type=MatchType.BUNDLED_1TON,
                        invoice_ids=b_ids,
                        tx_id=tx.tx_id,
                        invoice_total=bundle_sum,
                        bank_amount=tx.amount,
                        variance_amount=0.0,
                        confidence_score=0.92,
                        rule_applied="P3_BUNDLED_SUBSET_SUM",
                        audit_reasoning=(
                            f"Bundled bulk payment resolving {len(b_ids)} invoices "
                            f"({', '.join(b_ids)}) matching bank disbursement of {tx.amount}."
                        ),
                    )
                )

        # -------------------------------------------------------------
        # PASS 4: Fee & FX Variance Tolerance Match
        # -------------------------------------------------------------
        for tx in transactions:
            if tx.tx_id in used_transactions:
                continue

            for inv in invoices:
                if inv.doc_id in used_invoices:
                    continue

                diff = abs(tx.amount - inv.total_amount)
                if 0.0 < diff <= self.fee_tolerance:
                    d_diff = _days_diff(inv.invoice_date, tx.tx_date)
                    if d_diff <= self.date_tolerance + 2:
                        sim = _calc_similarity(inv.vendor_name, tx.counterparty)
                        if sim >= self.vendor_threshold:
                            used_invoices.add(inv.doc_id)
                            used_transactions.add(tx.tx_id)
                            matched_pairs.append(
                                MatchResult(
                                    match_status=MatchStatus.MATCHED,
                                    match_type=MatchType.FEE_ADJUSTED,
                                    invoice_ids=[inv.doc_id],
                                    tx_id=tx.tx_id,
                                    invoice_total=inv.total_amount,
                                    bank_amount=tx.amount,
                                    variance_amount=round(tx.amount - inv.total_amount, 2),
                                    confidence_score=0.85,
                                    rule_applied="P4_FEE_TOLERANCE_VARIANCE",
                                    audit_reasoning=(
                                        f"Fee/deduction adjusted match. Invoice: {inv.total_amount}, "
                                        f"Bank: {tx.amount}. Variance of {diff:.2f} attributed to wire or processing fee."
                                    ),
                                )
                            )
                            break

        # Collect unlinked items
        unmatched_invoices = [inv for inv in invoices if inv.doc_id not in used_invoices]
        unmatched_txs = [tx for tx in transactions if tx.tx_id not in used_transactions]

        # Calculate KPIs
        total_inv_amount = round(sum(inv.total_amount for inv in invoices), 2)
        total_bank_amount = round(sum(tx.amount for tx in transactions), 2)
        matched_amount = round(sum(m.bank_amount for m in matched_pairs), 2)
        net_variance = round(total_bank_amount - total_inv_amount, 2)

        match_rate = round(
            (len(matched_pairs) / max(len(invoices), 1)) * 100.0, 1
        ) if invoices else 0.0

        summary = ReconciliationSummary(
            total_invoices=len(invoices),
            total_transactions=len(transactions),
            matched_count=len(matched_pairs),
            ambiguous_count=len(ambiguous_items),
            unmatched_invoices_count=len(unmatched_invoices),
            unmatched_transactions_count=len(unmatched_txs),
            match_rate_percentage=min(match_rate, 100.0),
            total_invoiced_amount=total_inv_amount,
            total_bank_amount=total_bank_amount,
            matched_amount=matched_amount,
            net_variance=net_variance,
        )

        return ReconciliationReport(
            summary=summary,
            matched_pairs=matched_pairs,
            ambiguous_items=ambiguous_items,
            unmatched_invoices=unmatched_invoices,
            unmatched_transactions=unmatched_txs,
        )
