"""Explicit product classification, separate from historical market metrics.

Inputs must come from reviewed product metadata, never ticker/name guesses.
FSC 2023-01-25 guidance, III-2 and III-4:
https://www.fsc.go.kr/po010101/79327
This is an explicit project policy based on that published guidance.
"""
from dataclasses import dataclass
from datetime import date

from app.domain import RiskError, three_years_before


@dataclass(frozen=True)
class GradingPolicy:
    kind: str  # STOCK, ETF, FUND
    stock_elevated: bool | None = None
    inception: date | None = None
    asset_grade: int | None = None  # Reviewed investment-asset/structure classification.
    leveraged_or_inverse: bool | None = None
    history_since: date | None = None  # Observed trading proves existence, not the exact inception date.


def product_grade(value: float, policy: GradingPolicy, as_of: date) -> int:
    # Local import avoids coupling the numerical estimator to metadata loading.
    from app.services.calculation import grade_for

    def missing():
        raise RiskError('GRADING_METADATA_REQUIRED', 'Verified product grading metadata is required.')

    if policy.kind == 'STOCK':
        if type(policy.stock_elevated) is not bool:
            missing()
        return 1 if policy.stock_elevated else 2
    if policy.kind not in {'ETF', 'FUND'}:
        missing()
    cutoff = three_years_before(as_of)
    proven_mature = policy.history_since is not None and policy.history_since <= cutoff
    if policy.inception is None and not proven_mature:
        missing()
    if policy.inception is not None and policy.inception > as_of:
        missing()
    if policy.kind == 'ETF' and type(policy.leveraged_or_inverse) is not bool:
        missing()
    if policy.inception is not None and policy.inception > cutoff:
        if type(policy.asset_grade) is not int or not 1 <= policy.asset_grade <= 6:
            missing()
        # The reviewed asset grade already includes any special payoff structure.
        return policy.asset_grade
    grade = grade_for(value)
    return max(1, grade - 1) if policy.kind == 'ETF' and policy.leveraged_or_inverse else grade
