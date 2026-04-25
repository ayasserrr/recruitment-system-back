"""Loads config.yaml from the project root into typed dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

# project root is 4 levels up from this file:
# src/services/relative_grading/grading_config.py → src/ → project root
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config.yaml"


@dataclass
class Weights:
    coverage: float = 0.60
    depth: float = 0.40


@dataclass
class Thresholds:
    semantic_fallback: float = 0.72
    tiebreak_margin: float = 0.05


@dataclass
class TiebreakerConfig:
    runs: int = 3
    temperature: float = 0.0


@dataclass
class Segments:
    pass_threshold: float = 0.50
    shortlist: float = 0.75


@dataclass
class GradingConfig:
    weights: Weights = field(default_factory=Weights)
    thresholds: Thresholds = field(default_factory=Thresholds)
    tiebreaker: TiebreakerConfig = field(default_factory=TiebreakerConfig)
    segments: Segments = field(default_factory=Segments)


def load_config(path: Optional[Path] = None) -> GradingConfig:
    p = path or _DEFAULT_CONFIG_PATH
    if not p.exists():
        return GradingConfig()
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    w = data.get("weights", {})
    t = data.get("thresholds", {})
    tb = data.get("tiebreaker", {})
    s = data.get("segments", {})

    return GradingConfig(
        weights=Weights(
            coverage=float(w.get("coverage", 0.60)),
            depth=float(w.get("depth", 0.40)),
        ),
        thresholds=Thresholds(
            semantic_fallback=float(t.get("semantic_fallback", 0.72)),
            tiebreak_margin=float(t.get("tiebreak_margin", 0.05)),
        ),
        tiebreaker=TiebreakerConfig(
            runs=int(tb.get("runs", 3)),
            temperature=float(tb.get("temperature", 0.0)),
        ),
        segments=Segments(
            pass_threshold=float(s.get("pass_threshold", 0.50)),
            shortlist=float(s.get("shortlist", 0.75)),
        ),
    )
