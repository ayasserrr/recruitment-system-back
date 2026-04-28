"""
GPU / device selection utility.

Import DEVICE wherever a model needs to be pinned to a specific accelerator:

    from core.gpu import DEVICE
    model = SentenceTransformer("...", device=DEVICE)

Call log_device_info() once at worker startup to surface the active device
in the Celery log.
"""

import logging
import os

# Redirect all HuggingFace model downloads/loads to E: drive cache.
# Must be set before any sentence_transformers / transformers / FlagEmbedding import.
os.environ.setdefault("HF_HOME", "E:/huggingface_cache")
os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", "E:/huggingface_cache")
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", "E:/huggingface_cache")

import torch

logger = logging.getLogger(__name__)

# Single source of truth for device selection across all AI services.
DEVICE: str = "cuda" if torch.cuda.is_available() else "cpu"


def log_device_info() -> None:
    """Log which accelerator the worker is using at startup."""
    if DEVICE == "cuda":
        gpu_name = torch.cuda.get_device_name(0)
        logger.info("GPU Acceleration Active: Using %s", gpu_name)
    else:
        logger.info("GPU Not Found: Falling back to CPU")
