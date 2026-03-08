"""Rule-based market predictor. Replaces the old keyword-matching predictor."""
import json
import logging
from typing import Optional

log = logging.getLogger(__name__)


class Prediction:
    """A prediction for a single market."""
    def __init__(self, market_id: str, side: str, confidence: float, edge: float,
                 matching_rules: list[dict], entry_price: float):
        self.market_id = market_id
        self.side = side  # "YES" or "NO"
        self.confidence = confidence  # 0.0 to 1.0
        self.edge = edge  # expected edge at entry price
        self.matching_rules = matching_rules
        self.entry_price = entry_price

    def to_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "side": self.side,
            "confidence": round(self.confidence, 4),
            "edge": round(self.edge, 4),
            "entry_price": round(self.entry_price, 4),
            "num_rules": len(self.matching_rules),
            "matching_rules": [r["name"] for r in self.matching_rules],
        }


def check_rule_conditions(rule: dict, features: dict[str, float]) -> bool:
    """
    Check if a rule's conditions are met given current features.

    Handles different rule types:
    - single_threshold: {"feature": "vix_close", "op": ">", "value": 25}
    - two_feature: {"features": [{"feature": "x", "op": ">", "value": 1}, ...]}
    - ngram: {"ngram": "between", "n": 1} -- check ngram_between in features
    - decision_tree: {"path": [{"feature": "x", "op": ">", "value": 1}, ...]}
    - logistic_regression: {"feature": "x", "coefficient": 0.5, "direction": "positive"}
    - combined: {"ngram": "between", "feature": "x", "op": ">", "value": 1}
    """
    try:
        conditions = (
            json.loads(rule["conditions_json"])
            if isinstance(rule["conditions_json"], str)
            else rule["conditions_json"]
        )
    except (json.JSONDecodeError, TypeError):
        return False

    rule_type = rule.get("rule_type", "")

    if rule_type == "single_threshold":
        return _check_threshold(conditions, features)

    elif rule_type == "two_feature":
        feature_conditions = conditions.get("features", [])
        return all(_check_threshold(c, features) for c in feature_conditions)

    elif rule_type == "ngram":
        ngram = conditions.get("ngram", "")
        key = f"ngram_{ngram.replace(' ', '_')}"
        return features.get(key, 0.0) > 0.5

    elif rule_type == "decision_tree":
        path = conditions.get("path", [])
        return all(_check_threshold(c, features) for c in path)

    elif rule_type == "logistic_regression":
        # For logreg rules, check if the feature value supports the direction
        feat = conditions.get("feature", "")
        direction = conditions.get("direction", "positive")
        val = features.get(feat, 0.0)
        if direction == "positive":
            return val > 0
        else:
            return val < 0

    elif rule_type == "combined":
        # Check both ngram and feature condition
        ngram = conditions.get("ngram", "")
        key = f"ngram_{ngram.replace(' ', '_')}"
        ngram_present = features.get(key, 0.0) > 0.5
        if not ngram_present:
            return False
        return _check_threshold(conditions, features)

    return False


def _check_threshold(condition: dict, features: dict[str, float]) -> bool:
    """Check a single threshold condition."""
    feat = condition.get("feature", "")
    op = condition.get("op", ">")
    threshold = condition.get("value", 0)

    val = features.get(feat)
    if val is None:
        return False

    if op == ">":
        return val > threshold
    elif op == ">=":
        return val >= threshold
    elif op == "<":
        return val < threshold
    elif op == "<=":
        return val <= threshold
    elif op == "==":
        return val == threshold
    return False


def predict_market(
    market_id: str,
    yes_price: float,
    no_price: float,
    features: dict[str, float],
    active_rules: list[dict],
    min_edge: float = 0.02,
    min_confidence: float = 0.1,
) -> Optional[Prediction]:
    """
    Generate a prediction for a single market.

    1. Find all rules whose conditions match current features
    2. For each matching rule, calculate edge at current price
    3. Combine rules weighted by (sample_size * edge)
    4. Output the highest-edge prediction if it passes thresholds

    Args:
        market_id: market identifier
        yes_price: current YES share price
        no_price: current NO share price
        features: {feature_name: value} -- all daily + question + ngram features
        active_rules: list of rule dicts from DB
        min_edge: minimum edge to generate prediction
        min_confidence: minimum confidence to generate prediction

    Returns:
        Prediction object or None if no rules match with sufficient edge
    """
    # Find matching rules
    yes_votes: list[tuple[float, float, dict]] = []  # (weight, edge, rule)
    no_votes: list[tuple[float, float, dict]] = []

    for rule in active_rules:
        if not rule.get("active", True):
            continue

        if not check_rule_conditions(rule, features):
            continue

        side = rule["predicted_side"]
        entry_price = yes_price if side == "YES" else no_price
        edge = rule["breakeven_price"] - entry_price

        if edge <= 0:
            continue  # No edge at current price

        weight = rule["sample_size"] * edge

        if side == "YES":
            yes_votes.append((weight, edge, rule))
        else:
            no_votes.append((weight, edge, rule))

    if not yes_votes and not no_votes:
        return None

    # Calculate weighted confidence for each side
    yes_total_weight = sum(w for w, _, _ in yes_votes)
    no_total_weight = sum(w for w, _, _ in no_votes)

    # Decide side
    if yes_total_weight > no_total_weight:
        side = "YES"
        entry_price = yes_price
        votes = yes_votes
        total_weight = yes_total_weight
        opposing_weight = no_total_weight
    elif no_total_weight > 0:
        side = "NO"
        entry_price = no_price
        votes = no_votes
        total_weight = no_total_weight
        opposing_weight = yes_total_weight
    else:
        return None

    # Confidence: net weight ratio
    net_weight = total_weight - opposing_weight
    confidence = (
        net_weight / (total_weight + opposing_weight)
        if (total_weight + opposing_weight) > 0
        else 0
    )
    confidence = max(0.0, min(1.0, confidence))

    # Average edge weighted by sample size
    avg_edge = (
        sum(w * e for w, e, _ in votes) / total_weight if total_weight > 0 else 0
    )

    if avg_edge < min_edge or confidence < min_confidence:
        return None

    matching_rules = [r for _, _, r in votes]

    return Prediction(
        market_id=market_id,
        side=side,
        confidence=confidence,
        edge=avg_edge,
        matching_rules=matching_rules,
        entry_price=entry_price,
    )
