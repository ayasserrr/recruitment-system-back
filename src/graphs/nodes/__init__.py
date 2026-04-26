# LangGraph node functions — one sub-package per workflow.
# Each node is a pure function: (state: XState) -> XState
# Nodes call src/services/ gateways; they never call OpenAI or Qdrant directly.
