"""Anomaly detection over reconciliation outcomes.

The original implementation emitted three anomaly types inline inside the
matching loop with hardcoded risk scores (0.92, 0.55, 0.88) and never produced
two of the five types its own model comment declared (``timing_anomaly`` and
``unrecognized_account``).

This module separates detection from matching and splits it the way the problem
actually divides:

**Rule-based** (deterministic, explainable, no training data needed) covers every
case where the definition of "wrong" is known in advance: a duplicate charge, an
amount outside tolerance, a settlement that never arrived, a transfer that
settled outside the expected window, a counterparty absent from both ledgers.
These are the majority of real reconciliation exceptions, and a rule is strictly
better than a model here because it is auditable and cannot drift.

**Statistical / ML** covers the residual: transactions that break no rule but do
not look like anything else in the population. ``IsolationForest`` is used for
this because it needs no labels, handles mixed-scale features, and returns a
continuous score that maps naturally onto a risk value. It runs only when there
is enough data for the result to mean anything.

Risk scores are derived from the evidence (relative amount, how far outside
tolerance, population percentile) rather than being constants, so ranking an
exception queue by risk actually orders the work.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from app.core.config import settings
from app.core.constants import AnomalyType, MatchStatus, Severity
from app.models.transaction import Transaction
from app.services.matching_engine import MatchDecision, date_bounds_for

logger = logging.getLogger(__name__)


@dataclass
class DetectedAnomaly:
    """A candidate anomaly, before persistence."""

    transaction_id: str
    anomaly_type: str
    severity: str
    risk_score: float
    explanation: str
    detector: str

    def key(self) -> Tuple[str, str]:
        return (self.transaction_id, self.anomaly_type)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _severity_for(risk: float) -> str:
    if risk >= 0.75:
        return Severity.HIGH.value
    if risk >= 0.45:
        return Severity.MEDIUM.value
    return Severity.LOW.value


def _amount_weight(amount_base: float, reference_amount: float) -> float:
    """Scale 0-1 by how large an amount is relative to the population median.

    Uses a log ratio so a transaction 10x the median is risky but not 10x as
    risky, which keeps the ranking usable when amounts span several orders of
    magnitude (as they do once INR and USD flows are normalised together).
    """
    if reference_amount <= 0 or amount_base <= 0:
        return 0.5
    ratio = amount_base / reference_amount
    return _clamp(0.5 + 0.25 * math.log10(max(ratio, 1e-6)))


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


# ---------------------------------------------------------------------------
# Rule-based detectors
# ---------------------------------------------------------------------------


def detect_duplicate_charges(
    transactions: Sequence[Transaction],
    amount_base_by_id: Dict[str, float],
    window_hours: int = 72,
) -> List[DetectedAnomaly]:
    """Flag repeated identical payments, and references reused across transfers.

    The previous key was ``(payer, amount, currency)`` with no time bound, which
    both missed duplicates that differed only by payee and flagged legitimate
    recurring payments months apart. This version requires the counterparty pair
    and the normalised amount to agree *within a time window*.
    """
    anomalies: List[DetectedAnomaly] = []
    amounts = [v for v in amount_base_by_id.values() if v > 0]
    median_amount = _median(amounts)

    groups: Dict[Tuple[str, str, str, float], List[Transaction]] = defaultdict(list)
    for tx in transactions:
        amount_base = amount_base_by_id.get(tx.transaction_id, 0.0)
        key = (
            (tx.payer or "").strip().lower(),
            (tx.payee or "").strip().lower(),
            (tx.currency or "").strip().upper(),
            round(amount_base, 2),
        )
        groups[key].append(tx)

    for (payer, _payee, currency, amount_base), members in groups.items():
        if len(members) < 2:
            continue

        ordered = sorted(
            members,
            key=lambda t: (t.created_at or datetime.min, t.transaction_id or ""),
        )
        first = ordered[0]

        for duplicate in ordered[1:]:
            gap: Optional[timedelta] = None
            if duplicate.created_at and first.created_at:
                gap = duplicate.created_at - first.created_at
                if gap > timedelta(hours=window_hours):
                    # Outside the window: treat as a legitimate recurring payment.
                    continue

            gap_text = (
                f"{gap.total_seconds() / 3600:.1f} hour(s) apart"
                if gap is not None
                else "with no reliable timestamps"
            )
            risk = _clamp(0.70 + 0.25 * _amount_weight(amount_base, median_amount))
            anomalies.append(
                DetectedAnomaly(
                    transaction_id=duplicate.transaction_id,
                    anomaly_type=AnomalyType.DUPLICATE_CHARGE.value,
                    severity=_severity_for(risk),
                    risk_score=round(risk, 3),
                    explanation=(
                        f"Possible duplicate payment: {currency} {duplicate.amount:,.2f} "
                        f"from '{duplicate.payer}' to '{duplicate.payee}' repeats "
                        f"transaction '{first.transaction_id}' {gap_text}. "
                        f"Normalised amount {amount_base:,.2f} "
                        f"{settings.fx.base_currency} and counterparty pair are identical."
                    ),
                    detector="rule:duplicate_charge",
                )
            )

    # Reference reuse across different transactions is a separate signal.
    by_reference: Dict[str, List[Transaction]] = defaultdict(list)
    for tx in transactions:
        if tx.reference_id:
            by_reference[str(tx.reference_id).strip().lower()].append(tx)

    for reference, members in by_reference.items():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=lambda t: t.transaction_id or "")
        for duplicate in ordered[1:]:
            risk = 0.6
            anomalies.append(
                DetectedAnomaly(
                    transaction_id=duplicate.transaction_id,
                    anomaly_type=AnomalyType.DUPLICATE_CHARGE.value,
                    severity=_severity_for(risk),
                    risk_score=risk,
                    explanation=(
                        f"Reference '{reference}' is used by {len(members)} transactions "
                        f"({', '.join(t.transaction_id for t in ordered[:5])}"
                        f"{', ...' if len(ordered) > 5 else ''}). A settlement reference is "
                        "expected to identify a single transfer."
                    ),
                    detector="rule:reference_reuse",
                )
            )

    return anomalies


def detect_from_decisions(
    decisions: Sequence[MatchDecision],
    amount_base_by_id: Dict[str, float],
    best_name_similarity: Dict[str, float],
) -> List[DetectedAnomaly]:
    """Derive anomalies from each match outcome.

    Covers amount_mismatch, missing_settlement, timing_anomaly,
    currency_mismatch and unrecognized_account. The last two were declared in
    the model but never produced by the old engine.
    """
    anomalies: List[DetectedAnomaly] = []
    amounts = [v for v in amount_base_by_id.values() if v > 0]
    median_amount = _median(amounts)
    base = settings.fx.base_currency
    config = settings.matching

    for decision in decisions:
        tx = decision.transaction
        tx_id = tx.transaction_id
        amount_base = amount_base_by_id.get(tx_id, 0.0)
        weight = _amount_weight(amount_base, median_amount)

        # 1. No match at all -> the settlement is missing.
        if decision.candidate is None:
            risk = _clamp(0.55 + 0.4 * weight)
            anomalies.append(
                DetectedAnomaly(
                    transaction_id=tx_id,
                    anomaly_type=AnomalyType.MISSING_SETTLEMENT.value,
                    severity=_severity_for(risk),
                    risk_score=round(risk, 3),
                    explanation=(
                        f"Transfer of {tx.currency} {tx.amount:,.2f} "
                        f"({amount_base:,.2f} {base}) from '{tx.payer}' to '{tx.payee}' has no "
                        f"corresponding bank settlement or ERP record within "
                        f"{config.amount_relative_tolerance:.1%} amount tolerance and a "
                        f"{config.date_window_days}-day window."
                    ),
                    detector="rule:missing_settlement",
                )
            )

        # 1b. Counterparty unknown to both ledgers. Checked whenever no match was
        # accepted, which includes the case where a weak candidate exists -- the
        # original code only checked it when there was no candidate at all, so
        # the type was never emitted in practice.
        if decision.candidate is None or decision.status == MatchStatus.FLAGGED.value:
            similarity = best_name_similarity.get(tx_id, 0.0)
            if similarity < config.name_similarity_floor:
                risk_unknown = _clamp(0.5 + 0.35 * weight)
                anomalies.append(
                    DetectedAnomaly(
                        transaction_id=tx_id,
                        anomaly_type=AnomalyType.UNRECOGNIZED_ACCOUNT.value,
                        severity=_severity_for(risk_unknown),
                        risk_score=round(risk_unknown, 3),
                        explanation=(
                            f"Neither '{tx.payer}' nor '{tx.payee}' resembles any counterparty "
                            f"in the bank or ERP ledgers (best name similarity "
                            f"{similarity:.0f}%, below the {config.name_similarity_floor:.0f}% "
                            f"floor). The account may be unregistered or the name may be "
                            f"mis-keyed."
                        ),
                        detector="rule:unrecognized_account",
                    )
                )

        if decision.candidate is None:
            continue

        score = decision.score
        if score is None:
            continue
        candidate = decision.candidate

        # 2. Matched or partially matched, but the amounts disagree materially.
        if not score.within_amount_tolerance and score.amount_delta_base > 0:
            relative = (
                score.amount_delta_base / amount_base if amount_base > 0 else 1.0
            )
            risk = _clamp(0.35 + 0.4 * weight + 0.25 * min(relative / 0.1, 1.0))
            anomalies.append(
                DetectedAnomaly(
                    transaction_id=tx_id,
                    anomaly_type=AnomalyType.AMOUNT_MISMATCH.value,
                    severity=_severity_for(risk),
                    risk_score=round(risk, 3),
                    explanation=(
                        f"Amount differs by {score.amount_delta_base:,.2f} {base} "
                        f"({relative:.2%}) between transfer {tx.currency} {tx.amount:,.2f} and "
                        f"{candidate.ledger_type} record '{candidate.ledger_id}' "
                        f"({candidate.currency} {candidate.amount:,.2f}). Exceeds the "
                        f"{config.amount_relative_tolerance:.1%} tolerance."
                    ),
                    detector="rule:amount_mismatch",
                )
            )

        # 3. Settled outside the expected window for that ledger type.
        if score.date_delta_days is not None and not score.within_date_tolerance:
            tolerance_days, window_days = date_bounds_for(candidate.ledger_type, config)
            risk = _clamp(
                0.3
                + 0.3 * weight
                + 0.3 * min(score.date_delta_days / max(window_days, 1), 1.0)
            )
            anomalies.append(
                DetectedAnomaly(
                    transaction_id=tx_id,
                    anomaly_type=AnomalyType.TIMING_ANOMALY.value,
                    severity=_severity_for(risk),
                    risk_score=round(risk, 3),
                    explanation=(
                        f"Settlement timing is outside tolerance: transfer dated "
                        f"{tx.created_at:%Y-%m-%d %H:%M} versus {candidate.ledger_type} record "
                        f"'{candidate.ledger_id}' dated "
                        f"{candidate.value_date:%Y-%m-%d %H:%M}, a gap of "
                        f"{score.date_delta_days:.1f} days against the "
                        f"{tolerance_days}-day {candidate.ledger_type} tolerance."
                    ),
                    detector="rule:timing_anomaly",
                )
            )

        # 4. Currency disagreement on an otherwise plausible match.
        if score.currency_mismatch:
            risk = _clamp(0.45 + 0.3 * weight)
            anomalies.append(
                DetectedAnomaly(
                    transaction_id=tx_id,
                    anomaly_type=AnomalyType.CURRENCY_MISMATCH.value,
                    severity=_severity_for(risk),
                    risk_score=round(risk, 3),
                    explanation=(
                        f"Transfer is denominated in {tx.currency} but matched "
                        f"{candidate.ledger_type} record '{candidate.ledger_id}' is in "
                        f"{candidate.currency}. Amounts agreed only after FX normalisation to "
                        f"{base}; confirm the booking currency is correct."
                    ),
                    detector="rule:currency_mismatch",
                )
            )

        # 5. A best candidate that is still too weak to accept.
        if decision.status == MatchStatus.FLAGGED.value:
            risk = _clamp(0.4 + 0.35 * weight)
            anomalies.append(
                DetectedAnomaly(
                    transaction_id=tx_id,
                    anomaly_type=AnomalyType.MISSING_SETTLEMENT.value,
                    severity=_severity_for(risk),
                    risk_score=round(risk, 3),
                    explanation=(
                        f"Closest candidate {candidate.ledger_type} '{candidate.ledger_id}' "
                        f"scored {score.total:.1f}%, below the "
                        f"{config.partial_threshold:.0f}% partial threshold. Treated as "
                        f"unsettled pending manual review."
                    ),
                    detector="rule:weak_candidate",
                )
            )

    return anomalies


# ---------------------------------------------------------------------------
# Statistical / unsupervised detection
# ---------------------------------------------------------------------------


def detect_statistical_outliers(
    decisions: Sequence[MatchDecision],
    amount_base_by_id: Dict[str, float],
    min_samples: int = 30,
    contamination: float = 0.05,
) -> List[DetectedAnomaly]:
    """Flag transactions that break no rule but are unlike the population.

    Uses ``IsolationForest`` on normalised, unlabelled features. This is the one
    place a model earns its keep: there is no rule that captures "this payment is
    structurally unusual for this payer at this hour at this size".

    Returns an empty list when there is too little data for the result to be
    meaningful, rather than producing confident noise.
    """
    if len(decisions) < min_samples:
        logger.debug(
            "Skipping statistical outlier detection: %d samples < %d minimum",
            len(decisions),
            min_samples,
        )
        return []

    try:
        import numpy as np
        from sklearn.ensemble import IsolationForest
    except ImportError:  # pragma: no cover
        logger.warning("scikit-learn unavailable; skipping statistical outlier detection")
        return []

    rows: List[List[float]] = []
    index: List[MatchDecision] = []

    for decision in decisions:
        tx = decision.transaction
        amount_base = amount_base_by_id.get(tx.transaction_id, 0.0)
        created = tx.created_at or datetime.utcnow()
        score_total = decision.score.total if decision.score else 0.0
        amount_delta = decision.score.amount_delta_base if decision.score else 0.0

        rows.append(
            [
                math.log10(max(amount_base, 1.0)),
                float(created.hour),
                float(created.weekday()),
                score_total,
                math.log10(max(amount_delta, 1.0)),
            ]
        )
        index.append(decision)

    features = np.asarray(rows, dtype=float)

    # Standardise so no single feature dominates by scale.
    mean = features.mean(axis=0)
    std = features.std(axis=0)
    std[std == 0] = 1.0
    standardised = (features - mean) / std

    model = IsolationForest(
        n_estimators=200,
        contamination=contamination,
        random_state=settings.forecast.random_seed,
        n_jobs=1,
    )
    model.fit(standardised)

    raw_scores = model.score_samples(standardised)
    predictions = model.predict(standardised)

    # score_samples: lower is more anomalous. Map to a 0-1 risk.
    lowest = float(raw_scores.min())
    highest = float(raw_scores.max())
    span = max(highest - lowest, 1e-9)

    anomalies: List[DetectedAnomaly] = []
    base = settings.fx.base_currency

    for decision, raw, prediction in zip(index, raw_scores, predictions):
        if prediction != -1:
            continue
        tx = decision.transaction
        normalised = 1.0 - (float(raw) - lowest) / span
        risk = _clamp(0.35 + 0.5 * normalised)
        amount_base = amount_base_by_id.get(tx.transaction_id, 0.0)

        anomalies.append(
            DetectedAnomaly(
                transaction_id=tx.transaction_id,
                anomaly_type=AnomalyType.STATISTICAL_OUTLIER.value,
                severity=_severity_for(risk),
                risk_score=round(risk, 3),
                explanation=(
                    f"Isolation Forest flagged this transfer as structurally unusual "
                    f"(isolation score {float(raw):.3f}). Value {amount_base:,.2f} {base}, "
                    f"booked {tx.created_at:%A %H:%M} with a match confidence of "
                    f"{decision.score.total if decision.score else 0:.1f}%. It breaks no "
                    f"explicit rule but does not resemble the rest of the population."
                ),
                detector="model:isolation_forest",
            )
        )

    logger.info(
        "Statistical detection flagged %d of %d transactions", len(anomalies), len(index)
    )
    return anomalies


def detect_all(
    decisions: Sequence[MatchDecision],
    amount_base_by_id: Dict[str, float],
    best_name_similarity: Dict[str, float],
    enable_statistical: bool = True,
) -> List[DetectedAnomaly]:
    """Run every detector and return the combined, de-duplicated findings."""
    transactions = [d.transaction for d in decisions]

    findings: List[DetectedAnomaly] = []
    findings.extend(detect_duplicate_charges(transactions, amount_base_by_id))
    findings.extend(
        detect_from_decisions(decisions, amount_base_by_id, best_name_similarity)
    )
    if enable_statistical:
        findings.extend(detect_statistical_outliers(decisions, amount_base_by_id))

    # Keep the highest-risk finding per (transaction, type).
    best: Dict[Tuple[str, str], DetectedAnomaly] = {}
    for finding in findings:
        existing = best.get(finding.key())
        if existing is None or finding.risk_score > existing.risk_score:
            best[finding.key()] = finding

    return sorted(
        best.values(), key=lambda a: (-a.risk_score, a.transaction_id, a.anomaly_type)
    )


__all__ = [
    "DetectedAnomaly",
    "detect_all",
    "detect_duplicate_charges",
    "detect_from_decisions",
    "detect_statistical_outliers",
]
