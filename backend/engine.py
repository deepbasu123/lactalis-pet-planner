"""
Stock projection engine — pure Python, no pandas.

All functions operate on plain lists/floats and are stateless.
"""
from __future__ import annotations


def project_sku(
    demand: list[float],
    receipts: list[float],
    opening: float,
    qa_weeks: int = 2,
) -> list[dict]:
    """
    Compute per-week stock projection for one SKU.

    receipts[i] is production planned in week i; it becomes available in week
    i + qa_weeks (i.e. we look back qa_weeks from the current week).

    Returns a list of dicts with keys: opening, recv, raw, close.
    """
    n = len(demand)
    results: list[dict] = []
    close_prev = opening

    for i in range(n):
        recv_idx = i - qa_weeks
        recv = receipts[recv_idx] if recv_idx >= 0 else 0.0
        raw = close_prev + recv - demand[i]
        close = max(0.0, raw)
        results.append(
            {
                "opening": close_prev,
                "recv": recv,
                "raw": raw,
                "close": close,
            }
        )
        close_prev = close

    return results


def cover(close: float, forward: list[float]) -> int:
    """
    Count whole forward weeks that close covers, capped at 3.

    cover = 0 if close <= 0 or close < d1
            1 if close < d1 + d2
            2 if close < d1 + d2 + d3
            3 otherwise
    """
    if close <= 0:
        return 0

    d = list(forward) + [0.0, 0.0, 0.0]  # pad so d[0..2] always exist

    if close < d[0]:
        return 0
    if close < d[0] + d[1]:
        return 1
    if close < d[0] + d[1] + d[2]:
        return 2
    return 3


def severity(
    close: float,
    weeks_until_shortfall: int,
    forward: list[float],
    demand_over_maxcover: float,
    reaction_window: int,
) -> int:
    """
    Return severity band 1..7 following spec ordering exactly:

    1  stock-out, within reaction window   (dark_red)
    2  stock-out, beyond reaction window   (red)
    7  over-cover past MLOR window         (black)
    3  close < d1                          (amber)
    4  close < d1 + d2                     (green)
    5  close < d1 + d2 + d3               (light_blue)
    6  otherwise                           (dark_blue)
    """
    # Stock-out tests first — a stock-out must never read as over-cover.
    if close <= 0:
        return 1 if weeks_until_shortfall <= reaction_window else 2

    # Over-cover — tested before healthy bands.
    if demand_over_maxcover > 0 and close > demand_over_maxcover:
        return 7

    d = list(forward) + [0.0, 0.0, 0.0]

    if close < d[0]:
        return 3
    if close < d[0] + d[1]:
        return 4
    if close < d[0] + d[1] + d[2]:
        return 5
    return 6


_COLOUR: dict[int, str] = {
    1: "dark_red",
    2: "red",
    3: "amber",
    4: "green",
    5: "light_blue",
    6: "dark_blue",
    7: "black",
}


def colour(sev: int) -> str:
    """Map severity 1..7 to canonical colour name."""
    return _COLOUR[sev]
