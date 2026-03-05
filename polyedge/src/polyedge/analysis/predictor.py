"""Generate predictions for markets based on accumulated factors."""


def make_prediction(
    factors: list[dict],
    current_yes_price: float,
    factor_weights: dict[str, float] | None = None,
) -> dict:
    if not factors:
        return {
            "predicted_outcome": "YES" if current_yes_price > 0.5 else "NO",
            "confidence": 0.3,
            "factor_categories": [],
        }

    weights = factor_weights or {}
    yes_score = 0.0
    no_score = 0.0
    categories_used = set()

    for f in factors:
        cat = f.get("category", "unknown")
        conf = f.get("confidence", 0.5)
        cat_weight = weights.get(cat, 1.0)
        vote_strength = conf * cat_weight

        value = str(f.get("value", "")).lower()
        yes_signals = ["yes", "bullish", "positive", "up", "likely", "probable", "true", "support"]
        no_signals = ["no", "bearish", "negative", "down", "unlikely", "improbable", "false", "oppose"]

        if any(sig in value for sig in yes_signals):
            yes_score += vote_strength
        elif any(sig in value for sig in no_signals):
            no_score += vote_strength
        else:
            if current_yes_price > 0.5:
                yes_score += vote_strength * 0.1
            else:
                no_score += vote_strength * 0.1

        categories_used.add(cat)

    total = yes_score + no_score
    if total == 0:
        confidence = 0.3
        outcome = "YES" if current_yes_price > 0.5 else "NO"
    else:
        yes_pct = yes_score / total
        outcome = "YES" if yes_pct > 0.5 else "NO"
        confidence = abs(yes_pct - 0.5) * 2

    return {
        "predicted_outcome": outcome,
        "confidence": round(confidence, 3),
        "factor_categories": sorted(categories_used),
    }
