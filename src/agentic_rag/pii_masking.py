from __future__ import annotations

import re
from dataclasses import dataclass, field


EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# Phone: matches +1-800-555-1234 / (800) 555-1234 / 800.555.1234 / 8005551234
PHONE_RE = re.compile(
    r"(\+?1[\s.\-]?)?(\(?\d{3}\)?[\s.\-]?)\d{3}[\s.\-]?\d{4}"
)

# Column name fragments that indicate the value is a direct-contact field.
# Only columns matching these keywords have phone/email masking applied.
_CONTACT_COLUMN_KEYWORDS = (
    "email", "phone", "mobile", "cell", "fax", "contact", "tel",
)


@dataclass(slots=True)
class PIIMasker:
    use_presidio: bool = False
    token_counters: dict[str, int] = field(default_factory=dict)
    token_map: dict[str, str] = field(default_factory=dict)

    def _next_token(self, label: str) -> str:
        count = self.token_counters.get(label, 0) + 1
        self.token_counters[label] = count
        return f"{label}_{count}"

    def _tokenize(self, value: str, label: str) -> str:
        existing = self.token_map.get(value)
        if existing:
            return existing
        token = self._next_token(label)
        self.token_map[value] = token
        return token

    def _mask_with_fallback(self, text: str, pii_rules: list[str]) -> str:
        masked = text
        lowered = {rule.lower() for rule in pii_rules}

        if "email" in lowered:
            for found in set(EMAIL_RE.findall(masked)):
                masked = masked.replace(found, self._tokenize(found, "EMAIL"))

        if "phone" in lowered:
            for m in list(PHONE_RE.finditer(masked)):
                raw = m.group(0).strip()
                if raw:
                    masked = masked.replace(raw, self._tokenize(raw, "PHONE"))

        return masked

    def _mask_with_presidio(self, text: str) -> str:
        from presidio_analyzer import AnalyzerEngine
        from presidio_anonymizer import AnonymizerEngine

        analyzer = AnalyzerEngine()
        anonymizer = AnonymizerEngine()
        results = analyzer.analyze(text=text, language="en")
        anonymized = anonymizer.anonymize(text=text, analyzer_results=results)
        return anonymized.text

    def mask_text(self, text: str, pii_rules: list[str]) -> str:
        if not text:
            return text

        if self.use_presidio:
            try:
                return self._mask_with_presidio(text)
            except Exception:
                return self._mask_with_fallback(text, pii_rules)

        return self._mask_with_fallback(text, pii_rules)


def is_contact_column(column_name: str) -> bool:
    """Return True only if the column name suggests it holds contact info."""
    col = column_name.lower()
    return any(kw in col for kw in _CONTACT_COLUMN_KEYWORDS)
