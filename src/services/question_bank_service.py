"""
Compatibility shim — delegates to knowledge_base_service.
Import from knowledge_base_service directly for new code.
"""
from services.knowledge_base_service import (  # noqa: F401
    load_candidate_gaps_sync,
    get_all_five_questions,
    get_gap_targeted_question,
    get_targeted_questions,
    seed_knowledge_base,
    compute_and_store_embeddings,
)
