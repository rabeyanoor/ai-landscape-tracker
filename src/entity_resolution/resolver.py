"""
Entity resolution: collapses name variants like
  "OpenAI, Inc." / "Open AI" / "OpenAI" / "openai.com"
into one canonical name: "OpenAI".

Strategy (cheap -> expensive, in this order):
  1. Normalization: lowercase, strip legal suffixes (Inc., LLC, Ltd, Co.,
     Corp.), strip punctuation/extra whitespace.
  2. Exact match on the normalized string against names already seen.
  3. Fuzzy match (RapidFuzz token_sort_ratio) against the canonical registry;
     if similarity >= FUZZY_THRESHOLD, merge into the existing canonical name.
  4. LLM tie-breaker: only invoked for the ambiguous middle band
     (LLM_BAND_LOW <= score < FUZZY_THRESHOLD) where fuzzy matching alone
     isn't confident enough -- keeps LLM calls cheap since most names
     resolve at step 2 or 3 without ever touching the network.

Every decision -- including "no merge, new canonical entity created" -- is
logged as an EntityMappingLogEntry so the whole process is auditable
(this becomes the "Entity Mapping Log" tab in the final Google Sheet).
"""
import re
import logging
from dataclasses import dataclass, field
from typing import Optional

from rapidfuzz import fuzz, process

logger = logging.getLogger("entity_resolver")

FUZZY_THRESHOLD = 90       # >= this score -> auto-merge, no LLM needed
LLM_BAND_LOW = 75          # below this -> treat as a genuinely new entity, no LLM call

_LEGAL_SUFFIXES = re.compile(
    r"\b(inc\.?|llc|ltd\.?|corp\.?|co\.?|gmbh|s\.a\.|plc|pvt\.?\s?ltd\.?)\b",
    re.IGNORECASE,
)
_PUNCT_WS = re.compile(r"[^\w\s]|_")
_MULTI_WS = re.compile(r"\s+")


def normalize(name: str) -> str:
    if not name:
        return ""
    text = name.lower()
    text = _LEGAL_SUFFIXES.sub("", text)
    text = _PUNCT_WS.sub(" ", text)
    text = _MULTI_WS.sub(" ", text).strip()
    return text


@dataclass
class MappingLogEntry:
    raw_name: str
    canonical_name: str
    entity_type: str
    match_method: str   # "exact" | "fuzzy" | "llm" | "new"
    confidence: float


class EntityResolver:
    def __init__(self, entity_type: str = "startup"):
        self.entity_type = entity_type
        # normalized_name -> canonical_name
        self._normalized_to_canonical: dict[str, str] = {}
        # canonical_name -> the "nicest" original spelling seen (used for display)
        self._canonical_display: dict[str, str] = {}
        self.log: list[MappingLogEntry] = field(default_factory=list)
        self.log = []

    def _pick_display_name(self, existing: str, candidate: str) -> str:
        """Prefer the shorter, title-cased-looking version as the canonical
        display form -- e.g. "OpenAI" over "OpenAI, Inc." or "open ai"."""
        def score(n):
            return (len(n), not any(c.isupper() for c in n))
        return existing if score(normalize(existing)) <= score(normalize(candidate)) else candidate

    def resolve(self, raw_name: str, llm_tiebreaker=None) -> str:
        """Returns the canonical name for raw_name, updating internal state
        and the audit log. `llm_tiebreaker`, if provided, is a sync callable
        (raw_name, candidate_canonical) -> bool used only for the ambiguous band."""
        if not raw_name or not raw_name.strip():
            return raw_name

        norm = normalize(raw_name)

        # Step 2: exact match on normalized string
        if norm in self._normalized_to_canonical:
            canonical = self._normalized_to_canonical[norm]
            self.log.append(MappingLogEntry(raw_name, canonical, self.entity_type, "exact", 1.0))
            return canonical

        # Step 3: fuzzy match against existing canonical registry
        if self._normalized_to_canonical:
            best_match, score, _ = process.extractOne(
                norm, self._normalized_to_canonical.keys(), scorer=fuzz.token_sort_ratio
            ) or (None, 0, None)

            if best_match and score >= FUZZY_THRESHOLD:
                canonical = self._normalized_to_canonical[best_match]
                self._normalized_to_canonical[norm] = canonical
                self.log.append(MappingLogEntry(raw_name, canonical, self.entity_type, "fuzzy", score / 100))
                return canonical

            # Step 4: ambiguous band -> optional LLM tie-break
            if best_match and LLM_BAND_LOW <= score < FUZZY_THRESHOLD and llm_tiebreaker:
                candidate_canonical = self._normalized_to_canonical[best_match]
                if llm_tiebreaker(raw_name, candidate_canonical):
                    self._normalized_to_canonical[norm] = candidate_canonical
                    self.log.append(
                        MappingLogEntry(raw_name, candidate_canonical, self.entity_type, "llm", score / 100)
                    )
                    return candidate_canonical

        # No match found -> this raw_name becomes its own new canonical entity
        canonical = raw_name.strip()
        self._normalized_to_canonical[norm] = canonical
        self._canonical_display[canonical] = canonical
        self.log.append(MappingLogEntry(raw_name, canonical, self.entity_type, "new", 1.0))
        return canonical

    def resolve_batch(self, raw_names: list[str], llm_tiebreaker=None) -> dict[str, str]:
        return {name: self.resolve(name, llm_tiebreaker) for name in raw_names}
