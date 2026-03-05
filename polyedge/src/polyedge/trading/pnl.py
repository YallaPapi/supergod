"""Canonical PnL calculations for all PolyEdge trading/backtesting code.

Every script that calculates profit/loss MUST use these functions
instead of inline math. This prevents the inconsistencies and bugs
found by the Codex review (double-multiply on wins, etc.)
"""


def calc_pnl(entry_price: float, won: bool) -> float:
    """Calculate profit/loss for a single trade.

    Args:
        entry_price: Price paid for the share (0.01 to 0.99)
        won: Whether the share's side resolved as the winner

    Returns:
        PnL in dollars. Positive = profit, negative = loss.
    """
    if won:
        return 1.0 - entry_price
    else:
        return -entry_price


def calc_roi(entry_price: float, won: bool) -> float:
    """Calculate return on investment for a single trade.

    Returns PnL divided by amount risked (entry_price).
    """
    if entry_price <= 0:
        return 0.0
    pnl = calc_pnl(entry_price, won)
    return pnl / entry_price


def calc_edge(win_rate: float, entry_price: float) -> float:
    """Calculate expected edge per dollar bet.

    This is the expected value: positive means profitable over many trades.

    Args:
        win_rate: Historical win rate as decimal (e.g., 0.87 for 87%)
        entry_price: Price you'd pay for the share

    Returns:
        Expected profit per trade. Positive = profitable.
    """
    # EV = P(win) * payout_on_win + P(lose) * payout_on_lose
    # EV = win_rate * (1 - entry_price) + (1 - win_rate) * (-entry_price)
    # EV = win_rate - win_rate*entry_price - entry_price + win_rate*entry_price
    # EV = win_rate - entry_price
    return win_rate - entry_price


def calc_breakeven(win_rate: float) -> float:
    """Calculate the breakeven entry price for a given win rate.

    At breakeven price, expected value is exactly zero.
    Buy BELOW this price for positive expected value.
    """
    return win_rate


def calc_kelly_fraction(win_rate: float, entry_price: float) -> float:
    """Calculate Kelly criterion bet fraction.

    Returns the optimal fraction of bankroll to bet.
    Returns 0 if no edge (negative or zero expected value).
    """
    edge = calc_edge(win_rate, entry_price)
    if edge <= 0 or entry_price <= 0 or entry_price >= 1:
        return 0.0
    # Kelly for binary outcome: f = (p * b - q) / b
    # where p = win_rate, q = 1-win_rate, b = odds ratio = (1-entry)/entry
    b = (1.0 - entry_price) / entry_price
    q = 1.0 - win_rate
    f = (win_rate * b - q) / b
    return max(0.0, f)
