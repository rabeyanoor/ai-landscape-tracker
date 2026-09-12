"""
Deterministic entity canonicalizer: maps messy raw names ("OpenAI, Inc.",
"Open AI") onto a fixed canonical seed list via normalization + fuzzy
matching, and keeps an audit trail of every raw -> canonical decision for the
"Entity Mapping Log" export tab.
"""
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from loguru import logger
from rapidfuzz import fuzz, process

CANONICAL_SEED_LIST = [
    "OpenAI", "Anthropic", "Google DeepMind", "Meta AI", "Mistral AI",
    "Cohere", "Stability AI", "Hugging Face", "xAI", "Perplexity AI",
    "Inflection AI", "Adept", "Runway", "ElevenLabs", "Midjourney",
]

_LEGAL_SUFFIXES_RE = re.compile(r"\b(inc\.?|llc|ltd\.?|corp\.?|co\.?|gmbh|s\.a\.|plc)\b\.?", re.IGNORECASE)
_NON_WORD_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(name: str) -> str:
    cleaned = _LEGAL_SUFFIXES_RE.sub("", name.strip().lower())
    cleaned = _NON_WORD_RE.sub("", cleaned)
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


@dataclass
class MappingLogEntry:
    raw_name: str
    canonical_name: str
    match_score: float
    matched_seed: bool


class EntityResolver:
    def __init__(self, seed_list: Optional[List[str]] = None, threshold: float = 85.0):
        self.seed_list = seed_list or CANONICAL_SEED_LIST
        self._normalized_seed = {_normalize(s): s for s in self.seed_list}
        self.threshold = threshold
        self.log: List[MappingLogEntry] = []

    def resolve(self, raw_name: str) -> str:
        normalized = _normalize(raw_name)

        if normalized in self._normalized_seed:
            canonical = self._normalized_seed[normalized]
            self.log.append(MappingLogEntry(raw_name, canonical, 100.0, True))
            return canonical

        match = process.extractOne(normalized, self._normalized_seed.keys(), scorer=fuzz.WRatio)
        if match and match[1] >= self.threshold:
            canonical = self._normalized_seed[match[0]]
            self.log.append(MappingLogEntry(raw_name, canonical, match[1], True))
            return canonical

        # No confident match: keep the raw name as its own canonical entity so
        # it still surfaces downstream, but flag it unmatched for review.
        fallback = raw_name.strip()
        self.log.append(MappingLogEntry(raw_name, fallback, match[1] if match else 0.0, False))
        logger.debug(f"entity_resolver: no confident match for '{raw_name}' (best={match})")
        return fallback

    def resolve_batch(self, raw_names: List[str]) -> Dict[str, str]:
        return {name: self.resolve(name) for name in raw_names}

    def log_as_rows(self) -> List[dict]:
        return [
            {
                "raw_name": e.raw_name,
                "canonical_name": e.canonical_name,
                "match_score": round(e.match_score, 1),
                "matched_seed": e.matched_seed,
            }
            for e in self.log
        ]
