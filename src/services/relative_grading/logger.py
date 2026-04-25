"""
Dual-output logger for the relative grading pipeline.

Format : {timestamp} | {level:<8} | {message}
Console : INFO and above
File    : DEBUG and above → logs/run_YYYY-MM-DD_HH-MM-SS.log (project root)
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_grading_logger(name: str = "relative_grading") -> logging.Logger:
    log_dir = _PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = log_dir / f"run_{ts}.log"

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    lg = logging.getLogger(name)
    lg.setLevel(logging.DEBUG)

    if not lg.handlers:
        # Console — INFO+
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        ch.setFormatter(formatter)
        lg.addHandler(ch)

        # File — DEBUG+
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(formatter)
        lg.addHandler(fh)

    return lg
