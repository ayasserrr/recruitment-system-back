from collections import defaultdict
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from models.db.generated_assessment_question import GeneratedAssessmentQuestion
from models.schemas.admin_schema import MissingKnowledgeItem, MissingKnowledgeResponse

_HIGH_PRIORITY_THRESHOLD = 5


def get_missing_knowledge_report(db: Session) -> MissingKnowledgeResponse:
    """
    Scans generated_assessment_questions for rows where concept_id IS NULL.
    Those rows were generated using general LLM knowledge because the tool
    had no match in the knowledge DB.

    Returns a structured report grouped by tool_name, ordered by occurrence
    count descending so the highest-priority gaps appear first.
    """
    # ── Aggregate: count + latest date per tool ───────────────────────────────
    agg_rows = (
        db.query(
            GeneratedAssessmentQuestion.tool_name,
            func.count().label("occurrence_count"),
            func.max(GeneratedAssessmentQuestion.created_at).label("last_requested_at"),
        )
        .filter(GeneratedAssessmentQuestion.concept_id.is_(None))
        .group_by(GeneratedAssessmentQuestion.tool_name)
        .order_by(func.count().desc())
        .all()
    )

    if not agg_rows:
        return MissingKnowledgeResponse(
            total_missing_tools=0,
            high_priority_count=0,
            generated_at=datetime.utcnow(),
            items=[],
        )

    # ── Collect distinct jr_ids per tool in one extra query ───────────────────
    jr_rows = (
        db.query(
            GeneratedAssessmentQuestion.tool_name,
            GeneratedAssessmentQuestion.jr_id,
        )
        .filter(GeneratedAssessmentQuestion.concept_id.is_(None))
        .distinct()
        .all()
    )

    jr_map: dict[str, list[int]] = defaultdict(list)
    for row in jr_rows:
        jr_map[row.tool_name].append(row.jr_id)

    # ── Build response items ──────────────────────────────────────────────────
    items: list[MissingKnowledgeItem] = []
    for row in agg_rows:
        count = row.occurrence_count
        items.append(
            MissingKnowledgeItem(
                tool_name=row.tool_name,
                occurrence_count=count,
                last_requested_at=row.last_requested_at,
                affected_jrs=sorted(jr_map.get(row.tool_name, [])),
                high_priority=count > _HIGH_PRIORITY_THRESHOLD,
            )
        )

    high_priority_count = sum(1 for item in items if item.high_priority)

    return MissingKnowledgeResponse(
        total_missing_tools=len(items),
        high_priority_count=high_priority_count,
        generated_at=datetime.utcnow(),
        items=items,
    )
