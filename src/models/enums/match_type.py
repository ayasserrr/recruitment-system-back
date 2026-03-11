import enum

class MatchType(str, enum.Enum):
    EXACT = "Exact"
    SEMANTIC = "Semantic"
    MISSING = "Missing"
