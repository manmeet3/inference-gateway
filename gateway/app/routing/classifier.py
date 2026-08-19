from app.providers.base import Message

# Heuristic complexity signals — upgradeable to a learned classifier later.
_COMPLEXITY_KEYWORDS = (
    "analyze",
    "explain",
    "debug",
    "refactor",
    "prove",
    "design",
    "architecture",
    "optimize",
    "algorithm",
    "implement",
    "compare",
    "evaluate",
    "step by step",
    "step-by-step",
    "write code",
)


class ComplexityClassifier:
    """Classifies a query as "simple" or "complex" from cheap heuristics:
    total user-message length and the presence of complexity keywords."""

    def __init__(self, complex_char_threshold: int) -> None:
        self._threshold = complex_char_threshold

    def classify(self, messages: list[Message]) -> str:
        text = " ".join(m.content for m in messages if m.role == "user").lower()
        if len(text) >= self._threshold:
            return "complex"
        if any(kw in text for kw in _COMPLEXITY_KEYWORDS):
            return "complex"
        return "simple"
