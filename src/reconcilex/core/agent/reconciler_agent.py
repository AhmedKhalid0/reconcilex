"""
Agentic Reconciliation Reasoner.
Interrogates unmatched items, resolves counterparty aliases, and audits edge cases.
"""

from typing import List, Tuple
from rapidfuzz import fuzz

from reconcilex.core.agent.providers import BaseLLMProvider, get_llm_provider
from reconcilex.core.models import (
    BankTransaction,
    InvoiceRecord,
    MatchResult,
    MatchStatus,
    MatchType,
    ReconciliationReport,
)


class ReconcilerAgent:
    """Agentic intermediary resolving non-trivial financial discrepancies."""

    def __init__(self, provider: BaseLLMProvider = None):
        self.provider = provider or get_llm_provider()

    def resolve_edge_cases(
        self,
        report: ReconciliationReport,
    ) -> ReconciliationReport:
        """
        Analyze remaining unmatched invoices and bank transactions.
        Attempts semantic resolution for ambiguous counterparties and fee spreads.
        """
        new_matches: List[MatchResult] = []
        still_unmatched_invoices: List[InvoiceRecord] = []
        resolved_inv_ids = set()
        resolved_tx_ids = set()

        for tx in report.unmatched_transactions:
            matched_inv = None
            best_reasoning = ""
            best_confidence = 0.0

            for inv in report.unmatched_invoices:
                if inv.doc_id in resolved_inv_ids:
                    continue

                # Case 1: Exact amount, but semantic abbreviation in counterparty
                if inv.total_amount == tx.amount:
                    sim = fuzz.token_set_ratio(inv.vendor_name.lower(), tx.counterparty.lower())
                    # If similarity is moderate, ask agent to verify semantic equivalence
                    if sim >= 40.0:
                        prompt = (
                            f"Evaluate if this bank statement line matches the invoice record:\n"
                            f"Bank Line: Counterparty='{tx.counterparty}', Date='{tx.tx_date}', Amount={tx.amount}\n"
                            f"Invoice: Vendor='{inv.vendor_name}', Date='{inv.invoice_date}', Amount={inv.total_amount}\n"
                            f"Respond strictly in JSON with keys: 'is_match' (boolean), 'confidence' (float 0-1), 'justification' (string)."
                        )
                        system = (
                            "You are a Senior Forensic Auditor. Identify trading aliases, DBA names, payment gateway prefixes "
                            "(e.g., Stripe, Square, PayPal), and regional corporate acronyms. Only confirm matches with sound justification."
                        )
                        try:
                            res = self.provider.generate_json(prompt, system)
                            if res.get("is_match", False) and float(res.get("confidence", 0.0)) >= 0.70:
                                matched_inv = inv
                                best_reasoning = res.get("justification", "Agent verified counterparty alias.")
                                best_confidence = float(res.get("confidence", 0.85))
                                break
                        except Exception:
                            # Fallback token check
                            if sim >= 55.0:
                                matched_inv = inv
                                best_reasoning = f"Semantic counterparty alias resolution (fuzzy token score: {sim:.1f}%)."
                                best_confidence = 0.75
                                break

            if matched_inv:
                resolved_inv_ids.add(matched_inv.doc_id)
                resolved_tx_ids.add(tx.tx_id)
                new_matches.append(
                    MatchResult(
                        match_status=MatchStatus.MATCHED,
                        match_type=MatchType.AGENT_RESOLVED,
                        invoice_ids=[matched_inv.doc_id],
                        tx_id=tx.tx_id,
                        invoice_total=matched_inv.total_amount,
                        bank_amount=tx.amount,
                        variance_amount=0.0,
                        confidence_score=best_confidence,
                        rule_applied="AGENT_SEMANTIC_REASONING",
                        audit_reasoning=f"[AI Auditor] {best_reasoning}",
                    )
                )

        # Update report items
        report.matched_pairs.extend(new_matches)
        report.unmatched_invoices = [
            inv for inv in report.unmatched_invoices if inv.doc_id not in resolved_inv_ids
        ]
        report.unmatched_transactions = [
            tx for tx in report.unmatched_transactions if tx.tx_id not in resolved_tx_ids
        ]

        # Recalculate summary metrics
        report.summary.matched_count = len(report.matched_pairs)
        report.summary.unmatched_invoices_count = len(report.unmatched_invoices)
        report.summary.unmatched_transactions_count = len(report.unmatched_transactions)
        report.summary.matched_amount = round(sum(m.bank_amount for m in report.matched_pairs), 2)
        if report.summary.total_invoices > 0:
            report.summary.match_rate_percentage = min(
                round((len(report.matched_pairs) / report.summary.total_invoices) * 100.0, 1),
                100.0
            )

        return report
