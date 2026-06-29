"""
Feature Extractor for Knowledge Base Chunks

Reuses the existing UDTParser and UDAFVocabulary to extract structured
metadata from chunk content. Does NOT call any LLM (deterministic, fast,
and side-effect free).

Extracted fields:
- organ: Detected organ (thyroid / liver / heart) or "unknown".
- classifications: TI-RADS / LI-RADS labels detected in the chunk.
- measurements: Numeric measurement values with units.
- anatomical_locations: Anatomical location terms (e.g. right_lobe_upper).
- udt_coverage_rate: Fraction of recognized terms vs total tokens.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from app.knowledge.udt_parser import UDTParser
from app.knowledge.vocabulary import Organ

logger = structlog.get_logger("app.knowledge.feature_extractor")


@dataclass
class ExtractedFeatures:
    """Structured metadata extracted from a knowledge chunk.

    Attributes:
        organ: Detected organ (lowercase string) or "unknown".
        classifications: List of normalized TI-RADS / LI-RADS labels.
        measurements: List of dicts, e.g. [{"value": "1.2x0.8", "unit": "cm"}].
        anatomical_locations: List of standard anatomical location terms.
        udt_coverage_rate: Fraction of recognized terms in [0.0, 1.0].
    """
    organ: str = "unknown"
    classifications: list[str] = field(default_factory=list)
    measurements: list[dict] = field(default_factory=list)
    anatomical_locations: list[str] = field(default_factory=list)
    udt_coverage_rate: float = 0.0


class FeatureExtractor:
    """Extract structured features from a chunk's text using UDT parsing.

    Reuses UDTParser (which is already in production) to avoid duplicating
    vocabulary / pattern logic. Synchronous because UDT parsing is CPU-bound
    and fast.
    """

    def __init__(self, udt_parser: UDTParser | None = None):
        self.udt_parser = udt_parser or UDTParser()

    def extract(
        self,
        text: str,
        organ_hint: str | None = None,
    ) -> ExtractedFeatures:
        """Extract features from the given chunk text.

        Args:
            text: Raw chunk content.
            organ_hint: Optional organ hint (e.g. when caller already knows
                        the organ from the source document metadata).

        Returns:
            ExtractedFeatures with organ, classifications, measurements, etc.
        """
        if not text or not text.strip():
            return ExtractedFeatures(organ=organ_hint or "unknown")

        try:
            result = self.udt_parser.parse(text, organ=organ_hint)
        except Exception as e:  # pragma: no cover - defensive
            logger.error("udt_parse_failed_during_extraction", error=str(e))
            return ExtractedFeatures(organ=organ_hint or "unknown")

        classifications: list[str] = []
        measurements: list[dict] = []
        locations: list[str] = []

        for attr in result.attributes:
            category = attr.category
            value = attr.value
            raw = attr.raw_text
            if category in ("ti_rads", "li_rads"):
                if value not in classifications:
                    classifications.append(value)
            elif category == "measurement":
                # value is "1.2x0.8 cm" - split unit off
                parts = value.rsplit(" ", 1)
                if len(parts) == 2:
                    measurements.append({"value": parts[0], "unit": parts[1]})
                else:
                    measurements.append({"value": value, "unit": ""})
            elif category == "anatomical_location":
                if value not in locations:
                    locations.append(value)
            # Other categories (echogenicity, boundary, morphology,
            # vascularity, calcification, heart_*) are summarized into
            # a single coverage metric only, not stored individually,
            # to keep the index lean.

        organ = result.organ or organ_hint or "unknown"
        if isinstance(organ, Organ):
            organ = organ.value

        return ExtractedFeatures(
            organ=organ,
            classifications=classifications,
            measurements=measurements,
            anatomical_locations=locations,
            udt_coverage_rate=result.coverage_rate,
        )

    def features_to_metadata(self, features: ExtractedFeatures) -> dict:
        """Convert ExtractedFeatures to a plain dict for chunk metadata.

        Stable key ordering; safe to JSON-serialize.
        """
        return {
            "organ": features.organ,
            "classifications": list(features.classifications),
            "measurements": list(features.measurements),
            "anatomical_locations": list(features.anatomical_locations),
            "udt_coverage_rate": features.udt_coverage_rate,
        }
