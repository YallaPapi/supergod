"""Classify Polymarket markets into categories based on question text."""
import re

# Order matters — first match wins
_RULES: list[tuple[str, list[str]]] = [
    ("crypto_updown", [r"up or down"]),
    ("sports_ou", [
        r"o/u \d", r"over/under", r"total (sets|games|points|goals|runs|maps|aces|corners)",
    ]),
    ("sports_spread", [r"spread:", r"handicap"]),
    ("sports_props", [
        r"win by ko", r"win by tko", r"win by (decision|submission|stoppage)",
        r"end in a draw", r"fight to go the distance", r"method of victory",
        r"power slap", r"will .* win by",
    ]),
    ("sports_winner", [
        r" vs\.? .*(win|\?)", r"will .* win on 20\d\d",
        r"will .* beat ", r"(game|match) winner",
    ]),
    ("economics", [
        r"fed (increase|decrease|cut|raise|hold)", r"interest rate",
        r"\bgdp\b", r"unemployment rate", r"inflation rate", r"\bcpi\b",
        r"jobs report", r"nonfarm", r"trade deficit", r"treasury yield",
    ]),
    ("politics_us", [
        r"\b(trump|biden|harris|desantis|pence|obama|rfk|vivek|haley|newsom)\b",
        r"\b(republican|democrat|gop|dnc|rnc)\b",
        r"\b(senate|congress|house of rep|supreme court|scotus)\b",
        r"presidential (election|nomination|primary|inauguration)",
        r"executive order", r"impeach",
    ]),
    ("politics_intl", [
        r"\b(parliament|prime minister|brexit|eu |nato )\b",
        r"\b(macron|trudeau|modi|putin|zelensky|netanyahu|starmer|sunak)\b",
        r"(general election|referendum)",
    ]),
    ("entertainment", [
        r"\b(album|grammy|oscar|emmy|golden globe|billboard|spotify|netflix)\b",
        r"\b(box office|streaming|#1 hit|chart|imdb|rotten tomatoes)\b",
        r"\b(movie|film|tv show|series|season \d)\b",
    ]),
    ("crypto_other", [
        r"\b(bitcoin|ethereum|btc|eth|solana|doge|xrp|crypto|token|defi|nft)\b",
        r"\b(market cap|halving|stablecoin|altcoin)\b",
    ]),
    ("science_tech", [
        r"\b(spacex|nasa|mars|moon landing)\b",
        r"\b(artificial intelligence|openai|chatgpt|deepmind)\b",
        r"\b(climate|vaccine|fda approval|who |pandemic|outbreak)\b",
    ]),
]

_COMPILED: list[tuple[str, list[re.Pattern]]] = [
    (cat, [re.compile(p, re.IGNORECASE) for p in patterns])
    for cat, patterns in _RULES
]


def classify_market(question: str) -> str:
    """Return a category string for the given market question."""
    for cat, patterns in _COMPILED:
        for pat in patterns:
            if pat.search(question):
                return cat
    return "other"
