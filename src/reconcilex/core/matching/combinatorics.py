"""
Bounded Subset-Sum Solver for Bundled Payments.
Finds 1-to-N combinations where a single bank transaction settles multiple invoices.
"""

from itertools import combinations
from typing import List, Optional, Tuple
from reconcilex.core.models import InvoiceRecord


def find_bundled_combination(
    target_amount: float,
    candidate_invoices: List[InvoiceRecord],
    max_bundle_size: int = 4,
    tolerance: float = 0.01
) -> Optional[Tuple[List[InvoiceRecord], float]]:
    """
    Find a subset of candidate invoices (size 2 to max_bundle_size)
    whose sum equals target_amount within monetary tolerance.
    """
    if len(candidate_invoices) < 2:
        return None

    # Sort invoices by amount ascending for bounded search
    valid_candidates = [inv for inv in candidate_invoices if inv.total_amount <= target_amount + tolerance]

    for k in range(2, min(len(valid_candidates) + 1, max_bundle_size + 1)):
        for combo in combinations(valid_candidates, k):
            combo_sum = round(sum(inv.total_amount for inv in combo), 2)
            if abs(combo_sum - target_amount) <= tolerance:
                return list(combo), combo_sum

    return None
