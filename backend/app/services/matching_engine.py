"""Candidate-generation matching engine.

Replaces the previous brute-force approach in ``reconciliation_service``, which
compared every transaction against every bank row and then against every ERP row
with 1-2 fuzzy string calls per pair. That is O(n x m) Python-level work: at
10,000 transactions against 10,000 ledger rows it is 100 million comparisons.

Three defects in the old scoring are fixed here:

1. ``confidence = name*0.4 + amount*0.4 + ref_boost(40)`` capped a no-reference
   match at 80 while the ``matched`` threshold was 90, so a transaction could
   only ever be classified ``matched`` if its reference matched a ledger
   reference exactly. The weights did not sum to 100.
2. There was no date comparison at all, despite ``timing_anomaly`` being a
   declared anomaly type. Settlement lag could not be detected or tolerated.
3. A missing signal was scored as a zero rather than as "not applicable", so two
   records that legitimately had no reference on either side were penalised as
   though their references disagreed.

Design
------
*Blocking* narrows the candidate set before any expensive comparison:

  reference key (exact, normalised)  -> dict lookup
  amount window (+/- tolerance)      -> binary search over a sorted array
  fuzzy name (fallback only)         -> rapidfuzz C-level batch scan

*Scoring* produces an explainable 0-100 breakdown with applicable-weight
renormalisation.

*Global assignment* resolves contention: all candidate pairs are sorted by score
and assigned greedily, so the outcome does not depend on table row order. The
old loop assigned on a first-come basis, which made results non-deterministic
with respect to insertion order.
"""

from __future__ import annotations

import logging
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from rapidfuzz import fuzz, process

from app.core.config import MatchingSettings, settings
from app.core.constants import ConfidenceTier, LedgerType, MatchStatus
from app.models.bank_transaction import BankTransaction, ERPRecord
from app.models.transaction import Transaction
from app.services.fx_service import FXConverter

logger = logging.getLogger(__name__)

# Corporate suffixes and filler tokens stripped before name comparison so that
# "Amazon India Pvt Ltd", "AMAZON INDIA PVT. LTD" and "Amazon India" converge.
_LEGAL_SUFFIXES = {
    "pvt", "private", "ltd", "limited", "llc", "llp", "inc", "incorporated",
    "corp", "corporation", "co", "company", "gmbh", "ag", "sa", "nv", "bv",
    "plc", "pte", "oy", "ab", "as", "srl", "spa", "kk", "kg", "and", "the",
}

_NON_ALNUM = re.compile(r"[^a-z0-9\s]+")
_WHITESPACE = re.compile(r"\s+")
_REF_NOISE = re.compile(r"[^a-z0-9]+")


def normalize_name(value: Optional[str]) -> str:
    """Lower-case, strip punctuation and drop legal-form suffixes."""
    if not value:
        return ""
    text = _NON_ALNUM.sub(" ", str(value).lower())
    tokens = [t for t in _WHITESPACE.split(text) if t and t not in _LEGAL_SUFFIXES]
    return " ".join(tokens)


def normalize_reference(value: Optional[str]) -> str:
    """Reduce a reference to comparable form: ``REF-8801`` -> ``ref8801``."""
    if not value:
        return ""
    return _REF_NOISE.sub("", str(value).lower())


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass
class LedgerCandidate:
    """A bank or ERP row reduced to the unified shape the matcher compares.

    Normalising both ledgers into one representation is what allows a single
    scoring path instead of the two near-duplicate loops the old code had.
    """

    ledger_id: str
    ledger_type: str
    counterparty: str
    reference: Optional[str]
    amount: float
    currency: str
    amount_base: float
    value_date: Optional[datetime]
    normalized_name: str = ""
    normalized_reference: str = ""

    @classmethod
    def from_bank(cls, row: BankTransaction, converter: FXConverter) -> "LedgerCandidate":
        return cls(
            ledger_id=row.bank_statement_id,
            ledger_type=LedgerType.BANK.value,
            counterparty=row.counterparty or "",
            reference=row.reference_number,
            amount=float(row.amount or 0.0),
            currency=row.currency or converter.base_currency,
            amount_base=(
                float(row.amount_base)
                if row.amount_base is not None
                else converter.to_base(row.amount, row.currency)
            ),
            value_date=row.value_date,
            normalized_name=normalize_name(row.counterparty),
            normalized_reference=normalize_reference(row.reference_number),
        )

    @classmethod
    def from_erp(cls, row: ERPRecord, converter: FXConverter) -> "LedgerCandidate":
        return cls(
            ledger_id=row.erp_id,
            ledger_type=LedgerType.ERP.value,
            counterparty=row.customer_vendor_name or "",
            reference=row.invoice_number,
            amount=float(row.expected_amount or 0.0),
            currency=row.currency or converter.base_currency,
            amount_base=(
                float(row.amount_base)
                if row.amount_base is not None
                else converter.to_base(row.expected_amount, row.currency)
            ),
            value_date=row.posting_date,
            normalized_name=normalize_name(row.customer_vendor_name),
            normalized_reference=normalize_reference(row.invoice_number),
        )


@dataclass
class ScoreBreakdown:
    """An explainable score: the components, their weights and the evidence."""

    total: float
    amount: float
    name: float
    reference: float
    date: float
    applicable_weight: float
    amount_delta_base: float
    date_delta_days: Optional[float]
    name_similarity: float
    reference_exact: bool
    currency_mismatch: bool
    within_amount_tolerance: bool
    within_date_tolerance: bool
    blocking_reason: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "total": self.total,
            "amount": self.amount,
            "name": self.name,
            "reference": self.reference,
            "date": self.date,
            "amount_delta_base": self.amount_delta_base,
            "date_delta_days": self.date_delta_days,
            "name_similarity": self.name_similarity,
            "reference_exact": self.reference_exact,
            "currency_mismatch": self.currency_mismatch,
            "within_amount_tolerance": self.within_amount_tolerance,
            "within_date_tolerance": self.within_date_tolerance,
            "blocking_reason": self.blocking_reason,
        }


@dataclass
class ScoredPair:
    transaction: Transaction
    candidate: LedgerCandidate
    score: ScoreBreakdown

    @property
    def total(self) -> float:
        return self.score.total


@dataclass
class MatchDecision:
    """The engine's verdict for one transaction."""

    transaction: Transaction
    candidate: Optional[LedgerCandidate]
    score: Optional[ScoreBreakdown]
    status: str
    tier: str
    remarks: str

    @property
    def is_matched(self) -> bool:
        return self.status == MatchStatus.MATCHED.value


@dataclass
class MatchingStats:
    """Counters that make the complexity improvement measurable."""

    transactions: int = 0
    ledger_records: int = 0
    candidates_generated: int = 0
    pairs_scored: int = 0
    fuzzy_fallback_used: int = 0
    assignments: int = 0
    blocking_reasons: Dict[str, int] = field(default_factory=dict)

    @property
    def naive_pair_count(self) -> int:
        """Pairs a full cross product would have required, for comparison."""
        return self.transactions * self.ledger_records

    @property
    def reduction_factor(self) -> float:
        if not self.pairs_scored:
            return float(self.naive_pair_count) or 0.0
        return round(self.naive_pair_count / self.pairs_scored, 2)

    def as_dict(self) -> Dict[str, object]:
        return {
            "transactions": self.transactions,
            "ledger_records": self.ledger_records,
            "candidates_generated": self.candidates_generated,
            "pairs_scored": self.pairs_scored,
            "naive_pairs_avoided": max(0, self.naive_pair_count - self.pairs_scored),
            "reduction_factor": self.reduction_factor,
            "fuzzy_fallback_used": self.fuzzy_fallback_used,
            "assignments": self.assignments,
            "blocking_reasons": dict(self.blocking_reasons),
        }


# ---------------------------------------------------------------------------
# Blocking index
# ---------------------------------------------------------------------------


class CandidateIndex:
    """Blocking structures over the ledger side of the match.

    Built once per run in O(m log m); each lookup is O(log m + k) where k is the
    number of candidates actually in range, instead of O(m) per transaction.
    """

    def __init__(self, candidates: Sequence[LedgerCandidate], config: MatchingSettings):
        self._config = config
        self._candidates: List[LedgerCandidate] = list(candidates)

        # Exact-reference index.
        self._by_reference: Dict[str, List[LedgerCandidate]] = {}
        for candidate in self._candidates:
            if candidate.normalized_reference:
                self._by_reference.setdefault(candidate.normalized_reference, []).append(
                    candidate
                )

        # Amount-sorted arrays for binary-search windowing.
        ordered = sorted(
            range(len(self._candidates)),
            key=lambda i: self._candidates[i].amount_base,
        )
        self._sorted_amounts: List[float] = [
            self._candidates[i].amount_base for i in ordered
        ]
        self._sorted_candidates: List[LedgerCandidate] = [
            self._candidates[i] for i in ordered
        ]

        # Parallel arrays for the rapidfuzz batch fallback.
        self._names: List[str] = [c.normalized_name for c in self._candidates]

    def __len__(self) -> int:
        return len(self._candidates)

    @property
    def candidates(self) -> List[LedgerCandidate]:
        return self._candidates

    def amount_tolerance(self, amount_base: float) -> float:
        """Absolute tolerance in base currency for a given amount."""
        return max(
            self._config.amount_absolute_tolerance,
            abs(amount_base) * self._config.amount_relative_tolerance,
        )

    def by_reference(self, normalized_reference: str) -> List[LedgerCandidate]:
        if not normalized_reference:
            return []
        return self._by_reference.get(normalized_reference, [])

    def by_amount_window(self, amount_base: float, expansion: float = 5.0) -> List[LedgerCandidate]:
        """Candidates whose base amount falls inside an expanded tolerance band.

        The band is widened beyond the strict tolerance so that near misses are
        still surfaced and can be classified ``partial`` with an
        ``amount_mismatch`` anomaly rather than silently disappearing.
        """
        window = self.amount_tolerance(amount_base) * max(1.0, expansion)
        low = amount_base - window
        high = amount_base + window
        start = bisect_left(self._sorted_amounts, low)
        end = bisect_right(self._sorted_amounts, high)
        return self._sorted_candidates[start:end]

    def by_fuzzy_name(self, normalized_names: Iterable[str], limit: int = 10) -> List[LedgerCandidate]:
        """Fallback blocking on counterparty similarity.

        Uses rapidfuzz's batch ``extract``, which runs the comparison loop in C
        rather than Python. Only invoked for transactions that produced no
        reference or amount candidates, so it does not reintroduce an O(n x m)
        Python loop on the common path.
        """
        if not self._names:
            return []

        found: Dict[str, LedgerCandidate] = {}
        for name in normalized_names:
            if not name:
                continue
            matches = process.extract(
                name,
                self._names,
                scorer=fuzz.token_set_ratio,
                limit=limit,
                score_cutoff=self._config.name_similarity_floor,
            )
            for _matched_name, _score, index in matches:
                candidate = self._candidates[index]
                found[candidate.ledger_id] = candidate
        return list(found.values())


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _score_amount(
    tx_amount_base: float, candidate_amount_base: float, tolerance: float
) -> Tuple[float, float, bool]:
    """Return (score 0-100, absolute delta, within_tolerance)."""
    delta = abs(tx_amount_base - candidate_amount_base)

    if delta <= 1e-9:
        return 100.0, 0.0, True

    tol = max(tolerance, 1e-9)
    ratio = delta / tol

    if ratio <= 1.0:
        # Inside tolerance: 100 down to 70.
        return 100.0 - 30.0 * ratio, delta, True

    # Outside tolerance: 70 decaying to 0 at five times the tolerance.
    score = max(0.0, 70.0 - 70.0 * (ratio - 1.0) / 4.0)
    return score, delta, False


def _score_name(tx_payer: str, tx_payee: str, candidate_name: str) -> float:
    """Best similarity between either side of the transfer and the counterparty.

    ``token_set_ratio`` is used rather than ``token_sort_ratio`` because it
    tolerates one side carrying extra tokens, which is the common real-world
    case ("Amazon India" vs "Amazon India Private Limited Mumbai").
    """
    if not candidate_name:
        return 0.0
    scores = [
        fuzz.token_set_ratio(tx_payer, candidate_name) if tx_payer else 0.0,
        fuzz.token_set_ratio(tx_payee, candidate_name) if tx_payee else 0.0,
    ]
    return float(max(scores))


def _score_reference(tx_reference: str, candidate_reference: str) -> Tuple[float, bool]:
    """Return (score 0-100, is_exact). Both-missing is handled by the caller."""
    if not tx_reference or not candidate_reference:
        return 0.0, False
    if tx_reference == candidate_reference:
        return 100.0, True
    if tx_reference in candidate_reference or candidate_reference in tx_reference:
        return 75.0, False
    return float(fuzz.ratio(tx_reference, candidate_reference)) * 0.5, False


def date_bounds_for(ledger_type: str, config: MatchingSettings) -> Tuple[int, int]:
    """Return (tolerance_days, window_days) appropriate to the ledger.

    Bank settlement follows the transfer by hours or a couple of days. An ERP
    invoice can legitimately precede payment by weeks under normal credit terms,
    so applying the bank tolerance to ERP rows produces false timing anomalies.
    """
    if ledger_type == LedgerType.ERP.value:
        return config.erp_date_tolerance_days, config.erp_date_window_days
    return config.date_tolerance_days, config.date_window_days


def _score_date(
    tx_date: Optional[datetime],
    candidate_date: Optional[datetime],
    config: MatchingSettings,
    ledger_type: str = LedgerType.BANK.value,
) -> Tuple[float, Optional[float], bool]:
    """Return (score 0-100, delta in days, within_tolerance)."""
    if tx_date is None or candidate_date is None:
        return 0.0, None, True

    tolerance_days, window_days = date_bounds_for(ledger_type, config)
    delta_days = abs((tx_date - candidate_date).total_seconds()) / 86400.0

    if delta_days <= tolerance_days:
        return 100.0, delta_days, True

    if delta_days >= window_days:
        return 0.0, delta_days, False

    span = max(window_days - tolerance_days, 1e-9)
    decayed = 100.0 * (1.0 - (delta_days - tolerance_days) / span)
    return max(0.0, decayed), delta_days, False


def score_pair(
    transaction: Transaction,
    candidate: LedgerCandidate,
    tx_amount_base: float,
    tx_normalized_payer: str,
    tx_normalized_payee: str,
    tx_normalized_reference: str,
    tolerance: float,
    config: MatchingSettings,
    blocking_reason: str = "",
) -> ScoreBreakdown:
    """Score one transaction/candidate pair on a 0-100 scale.

    Only *applicable* components contribute. If neither side carries a reference,
    the reference weight is removed from the denominator rather than scored zero,
    so a pair cannot be punished for data that does not exist on either record.
    """
    amount_score, amount_delta, within_amount = _score_amount(
        tx_amount_base, candidate.amount_base, tolerance
    )
    name_score = _score_name(tx_normalized_payer, tx_normalized_payee, candidate.normalized_name)
    reference_score, reference_exact = _score_reference(
        tx_normalized_reference, candidate.normalized_reference
    )
    date_score, date_delta, within_date = _score_date(
        transaction.created_at, candidate.value_date, config, candidate.ledger_type
    )

    components: List[Tuple[float, float]] = [
        (amount_score, config.weight_amount),
        (name_score, config.weight_name),
    ]

    # Reference applies only when both sides have one.
    reference_applicable = bool(tx_normalized_reference and candidate.normalized_reference)
    if reference_applicable:
        components.append((reference_score, config.weight_reference))

    # Date applies only when both sides carry a timestamp.
    date_applicable = transaction.created_at is not None and candidate.value_date is not None
    if date_applicable:
        components.append((date_score, config.weight_date))

    applicable_weight = sum(weight for _score, weight in components)
    if applicable_weight <= 0:
        total = 0.0
    else:
        total = sum(score * weight for score, weight in components) / applicable_weight

    tx_currency = (transaction.currency or "").strip().upper()
    candidate_currency = (candidate.currency or "").strip().upper()

    return ScoreBreakdown(
        total=round(min(100.0, max(0.0, total)), 2),
        amount=round(amount_score, 2),
        name=round(name_score, 2),
        reference=round(reference_score, 2) if reference_applicable else 0.0,
        date=round(date_score, 2) if date_applicable else 0.0,
        applicable_weight=applicable_weight,
        amount_delta_base=round(amount_delta, 4),
        date_delta_days=round(date_delta, 3) if date_delta is not None else None,
        name_similarity=round(name_score, 2),
        reference_exact=reference_exact,
        currency_mismatch=bool(tx_currency and candidate_currency and tx_currency != candidate_currency),
        within_amount_tolerance=within_amount,
        within_date_tolerance=within_date,
        blocking_reason=blocking_reason,
    )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class MatchingEngine:
    """Generates candidates, scores them, and resolves a global assignment."""

    def __init__(
        self,
        converter: FXConverter,
        config: Optional[MatchingSettings] = None,
    ) -> None:
        self.converter = converter
        self.config = config or settings.matching
        self.config.validate()
        self.stats = MatchingStats()

    # -- candidate generation -------------------------------------------

    def build_index(
        self,
        bank_records: Sequence[BankTransaction],
        erp_records: Sequence[ERPRecord],
    ) -> CandidateIndex:
        candidates: List[LedgerCandidate] = [
            LedgerCandidate.from_bank(row, self.converter) for row in bank_records
        ]
        candidates.extend(
            LedgerCandidate.from_erp(row, self.converter) for row in erp_records
        )
        self.stats.ledger_records = len(candidates)
        return CandidateIndex(candidates, self.config)

    def candidates_for(
        self,
        transaction: Transaction,
        tx_amount_base: float,
        tx_normalized_reference: str,
        tx_normalized_payer: str,
        tx_normalized_payee: str,
        index: CandidateIndex,
    ) -> List[Tuple[LedgerCandidate, str]]:
        """Block in a bounded candidate set, tagged with why it was selected."""
        selected: Dict[str, Tuple[LedgerCandidate, str]] = {}

        for candidate in index.by_reference(tx_normalized_reference):
            selected[candidate.ledger_id] = (candidate, "reference")

        for candidate in index.by_amount_window(tx_amount_base):
            if candidate.ledger_id not in selected:
                selected[candidate.ledger_id] = (candidate, "amount_window")

        if not selected:
            fallback = index.by_fuzzy_name(
                (tx_normalized_payer, tx_normalized_payee),
                limit=self.config.max_candidates_per_transaction,
            )
            if fallback:
                self.stats.fuzzy_fallback_used += 1
            for candidate in fallback:
                selected.setdefault(candidate.ledger_id, (candidate, "fuzzy_name"))

        results = list(selected.values())

        # Bound the work per transaction. Keep the closest by amount, which is
        # the cheapest meaningful proxy before full scoring.
        limit = self.config.max_candidates_per_transaction
        if len(results) > limit:
            results.sort(key=lambda item: abs(item[0].amount_base - tx_amount_base))
            results = results[:limit]

        for _candidate, reason in results:
            self.stats.blocking_reasons[reason] = (
                self.stats.blocking_reasons.get(reason, 0) + 1
            )
        self.stats.candidates_generated += len(results)
        return results

    # -- scoring ---------------------------------------------------------

    def score_transactions(
        self,
        transactions: Sequence[Transaction],
        index: CandidateIndex,
    ) -> Tuple[List[ScoredPair], Dict[str, float]]:
        """Score every blocked pair. Returns the pairs and per-transaction best name similarity."""
        self.stats.transactions = len(transactions)
        pairs: List[ScoredPair] = []
        best_name_similarity: Dict[str, float] = {}

        for transaction in transactions:
            tx_amount_base = self.transaction_amount_base(transaction)
            tx_reference = normalize_reference(transaction.reference_id)
            tx_payer = normalize_name(transaction.payer)
            tx_payee = normalize_name(transaction.payee)
            tolerance = index.amount_tolerance(tx_amount_base)

            best_name_similarity.setdefault(transaction.transaction_id, 0.0)

            for candidate, reason in self.candidates_for(
                transaction, tx_amount_base, tx_reference, tx_payer, tx_payee, index
            ):
                breakdown = score_pair(
                    transaction,
                    candidate,
                    tx_amount_base,
                    tx_payer,
                    tx_payee,
                    tx_reference,
                    tolerance,
                    self.config,
                    blocking_reason=reason,
                )
                self.stats.pairs_scored += 1

                if breakdown.name_similarity > best_name_similarity[transaction.transaction_id]:
                    best_name_similarity[transaction.transaction_id] = breakdown.name_similarity

                if breakdown.total >= self.config.candidate_floor:
                    pairs.append(ScoredPair(transaction, candidate, breakdown))

        return pairs, best_name_similarity

    def transaction_amount_base(self, transaction: Transaction) -> float:
        """Base-currency amount, preferring the persisted normalised value."""
        if transaction.amount_base is not None:
            return float(transaction.amount_base)
        return self.converter.to_base(transaction.amount, transaction.currency)

    # -- assignment ------------------------------------------------------

    def assign(self, pairs: Sequence[ScoredPair]) -> Dict[str, ScoredPair]:
        """Resolve contention globally rather than in row order.

        Greedy maximum-weight matching over the candidate graph: sort every
        scored edge by descending score and accept an edge when neither endpoint
        is already used. Ties break on the identifiers, so the result is fully
        deterministic for a given dataset regardless of insertion order.
        """
        ordered = sorted(
            pairs,
            key=lambda p: (
                -p.total,
                p.transaction.transaction_id or "",
                p.candidate.ledger_id or "",
            ),
        )

        assigned: Dict[str, ScoredPair] = {}
        used_ledger_ids: set[str] = set()

        for pair in ordered:
            tx_id = pair.transaction.transaction_id
            if tx_id in assigned:
                continue
            if pair.candidate.ledger_id in used_ledger_ids:
                continue
            assigned[tx_id] = pair
            used_ledger_ids.add(pair.candidate.ledger_id)

        self.stats.assignments = len(assigned)
        return assigned

    # -- classification --------------------------------------------------

    def classify(
        self, transaction: Transaction, pair: Optional[ScoredPair]
    ) -> MatchDecision:
        """Turn a score into a status, a tier and a human-readable explanation."""
        if pair is None:
            return MatchDecision(
                transaction=transaction,
                candidate=None,
                score=None,
                status=MatchStatus.UNMATCHED.value,
                tier=ConfidenceTier.LOW.value,
                remarks=(
                    "No bank statement or ERP record was found within the configured "
                    f"amount tolerance ({self.config.amount_relative_tolerance:.1%}) or "
                    f"{self.config.date_window_days}-day settlement window."
                ),
            )

        breakdown = pair.score
        candidate = pair.candidate
        total = breakdown.total

        evidence = self._describe_evidence(breakdown, candidate)

        # Corroboration gate: an amount agreement on its own is weak evidence,
        # because unrelated payments frequently share a value. Require either a
        # credible reference or a credible counterparty name before accepting.
        if self.config.require_corroboration and not self._is_corroborated(breakdown):
            return MatchDecision(
                transaction=transaction,
                candidate=candidate,
                score=breakdown,
                status=MatchStatus.FLAGGED.value,
                tier=ConfidenceTier.LOW.value,
                remarks=(
                    f"Amounts agree with {candidate.ledger_type} record "
                    f"'{candidate.ledger_id}' ({total:.1f}% composite) but no identifying "
                    f"evidence corroborates it: reference similarity "
                    f"{breakdown.reference:.0f}% and counterparty similarity "
                    f"{breakdown.name_similarity:.0f}% are both below the required "
                    f"{self.config.name_similarity_floor:.0f}% floor. Held for manual "
                    f"review rather than auto-matched on amount alone. {evidence}"
                ),
            )

        if total >= self.config.matched_threshold:
            return MatchDecision(
                transaction=transaction,
                candidate=candidate,
                score=breakdown,
                status=MatchStatus.MATCHED.value,
                tier=ConfidenceTier.HIGH.value,
                remarks=(
                    f"High-confidence match ({total:.1f}%) to {candidate.ledger_type} "
                    f"record '{candidate.ledger_id}'. {evidence}"
                ),
            )

        if total >= self.config.partial_threshold:
            return MatchDecision(
                transaction=transaction,
                candidate=candidate,
                score=breakdown,
                status=MatchStatus.PARTIAL.value,
                tier=ConfidenceTier.MEDIUM.value,
                remarks=(
                    f"Partial match ({total:.1f}%) to {candidate.ledger_type} record "
                    f"'{candidate.ledger_id}'. {evidence}"
                ),
            )

        return MatchDecision(
            transaction=transaction,
            candidate=candidate,
            score=breakdown,
            status=MatchStatus.FLAGGED.value,
            tier=ConfidenceTier.LOW.value,
            remarks=(
                f"Best available candidate {candidate.ledger_type} '{candidate.ledger_id}' "
                f"scored only {total:.1f}%, below the {self.config.partial_threshold:.0f}% "
                f"partial-match threshold. {evidence} Requires manual review."
            ),
        )

    def _is_corroborated(self, breakdown: ScoreBreakdown) -> bool:
        """True when some identifier, not just the amount, supports the pair."""
        if breakdown.reference_exact:
            return True
        if breakdown.reference >= self.config.reference_corroboration_floor:
            return True
        return breakdown.name_similarity >= self.config.name_similarity_floor

    def _describe_evidence(
        self, breakdown: ScoreBreakdown, candidate: LedgerCandidate
    ) -> str:
        """Build the explanation string from the score components."""
        parts: List[str] = []

        if breakdown.reference_exact:
            parts.append("reference matched exactly")
        elif breakdown.reference > 0:
            parts.append(f"reference similarity {breakdown.reference:.0f}%")

        if breakdown.amount_delta_base <= 1e-9:
            parts.append(f"amount identical after FX normalisation ({self.converter.base_currency})")
        else:
            parts.append(
                f"amount differs by {breakdown.amount_delta_base:,.2f} "
                f"{self.converter.base_currency}"
                + ("" if breakdown.within_amount_tolerance else " (outside tolerance)")
            )

        parts.append(f"counterparty similarity {breakdown.name_similarity:.0f}%")

        if breakdown.date_delta_days is not None:
            parts.append(
                f"settled {breakdown.date_delta_days:.1f} day(s) apart"
                + ("" if breakdown.within_date_tolerance else " (outside window)")
            )

        if breakdown.currency_mismatch:
            parts.append(f"currency differs (ledger is {candidate.currency})")

        if breakdown.blocking_reason:
            parts.append(f"selected via {breakdown.blocking_reason} blocking")

        return "Evidence: " + "; ".join(parts) + "."

    # -- orchestration ---------------------------------------------------

    def run(
        self,
        transactions: Sequence[Transaction],
        bank_records: Sequence[BankTransaction],
        erp_records: Sequence[ERPRecord],
    ) -> Tuple[List[MatchDecision], Dict[str, float], CandidateIndex]:
        """Full matching pass. Returns decisions, name-similarity map and index."""
        index = self.build_index(bank_records, erp_records)
        pairs, best_name_similarity = self.score_transactions(transactions, index)
        assigned = self.assign(pairs)

        decisions = [
            self.classify(transaction, assigned.get(transaction.transaction_id))
            for transaction in transactions
        ]

        logger.info(
            "Matching complete: %d transactions x %d ledger records; "
            "scored %d pairs instead of %d (%.1fx reduction)",
            self.stats.transactions,
            self.stats.ledger_records,
            self.stats.pairs_scored,
            self.stats.naive_pair_count,
            self.stats.reduction_factor if self.stats.pairs_scored else 0.0,
        )
        return decisions, best_name_similarity, index


__all__ = [
    "CandidateIndex",
    "LedgerCandidate",
    "MatchDecision",
    "MatchingEngine",
    "MatchingStats",
    "ScoreBreakdown",
    "ScoredPair",
    "normalize_name",
    "normalize_reference",
    "score_pair",
]
