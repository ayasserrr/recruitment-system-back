# Graph runner entry points.
# Each runner builds and compiles a LangGraph, then invokes it with an
# initial state derived from the DB.  Runners are called by Celery tasks —
# never by routes or other services directly.
