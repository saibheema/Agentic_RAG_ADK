from __future__ import annotations

import re
from dataclasses import dataclass, field


EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


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

        if "ssn" in lowered:
            for found in set(SSN_RE.findall(masked)):
                masked = masked.replace(found, self._tokenize(found, "SSN"))

        if "name" in lowered:
            name_like = re.findall(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b", masked)
            for found in set(name_like):
                masked = masked.replace(found, self._tokenize(found, "PERSON"))

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
