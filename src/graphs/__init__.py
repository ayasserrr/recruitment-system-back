# src/graphs/ — LangGraph workflow package
#
# Structure:
#   states/   — TypedDict state definitions (one per workflow)
#   nodes/    — Node functions (one sub-package per workflow)
#   runners/  — Compiled graph entry points called by Celery tasks
#
# Convention:
#   • Every node: (state: XState) -> XState — returns {**state, key: value}
#   • Every node checks state.get("error") and short-circuits to END on error
#   • Every node that needs DB access opens its own SessionLocal() / try/finally
#   • Controllers (not nodes) own status transitions; nodes write data rows only
