"""
Knowledge Base Service
══════════════════════════════════════════════════════════════════════════════
DB-backed question bank that replaces the static question_bank_service.py.

Tables used:
  kb_topics              — topic taxonomy (self-referential hierarchy)
  kb_questions           — question bank with JSONB metadata
  kb_question_embeddings — precomputed all-MiniLM-L6-v2 embeddings (384-dim)
  jr_question_selections — per-candidate audit log

Public API (zero LLM):
  seed_knowledge_base()             — populate topics + questions on first run
  compute_and_store_embeddings()    — precompute MiniLM embeddings for all questions
  get_targeted_questions(app_id)    — return 5 personalised questions for an interview
  load_candidate_gaps_sync(app_id)  — load gap data dict for an application
  get_gap_targeted_question(...)    — single best gap-targeted question (Q3)
  get_all_five_questions(...)       — full 5-question set for an interview
  log_question_selection(...)       — write audit row to jr_question_selections
"""
from __future__ import annotations

import json
import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

_HF_CACHE = "E:/huggingface_cache"
_MODEL_ID  = "sentence-transformers/all-MiniLM-L6-v2"

# ── Lazy MiniLM encoder singleton ─────────────────────────────────────────────
_encoder = None


def _get_encoder():
    global _encoder
    if _encoder is not None:
        return _encoder
    try:
        from sentence_transformers import SentenceTransformer
        _encoder = SentenceTransformer(_MODEL_ID, cache_folder=_HF_CACHE)
        logger.info("[kb] MiniLM encoder loaded.")
    except Exception as exc:
        logger.warning("[kb] Sentence-transformer unavailable: %s", exc)
    return _encoder


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    ma  = math.sqrt(sum(x * x for x in a))
    mb  = math.sqrt(sum(x * x for x in b))
    return dot / (ma * mb) if ma and mb else 0.0


def _encode_texts(texts: list[str]) -> list[list[float]]:
    enc = _get_encoder()
    if enc is None:
        return []
    try:
        vecs = enc.encode(texts, convert_to_numpy=True, show_progress_bar=False)
        return [v.tolist() for v in vecs]
    except Exception as exc:
        logger.warning("[kb] encode failed: %s", exc)
        return []


# ══════════════════════════════════════════════════════════════════════════════
# Seed data — structured question definitions
# ══════════════════════════════════════════════════════════════════════════════

_TOPICS = [
    {"name": "architecture",  "display_name": "System Architecture & Design"},
    {"name": "database",      "display_name": "Database & Storage"},
    {"name": "scaling",       "display_name": "Scaling & Performance"},
    {"name": "security",      "display_name": "Security & Auth"},
    {"name": "devops",        "display_name": "DevOps & Cloud"},
    {"name": "api_design",    "display_name": "APIs & Integration"},
    {"name": "algorithms",    "display_name": "Algorithms & Problem-Solving"},
    {"name": "behavioral",    "display_name": "Behavioral / STAR"},
]

# Each entry: (topic_name, difficulty, round_hint, context_tags, ideal_points, expert_terms, question_text)
_QUESTIONS: list[tuple] = [
    # ── Architecture ──────────────────────────────────────────────────────────
    ("architecture", "senior", 1, ["architecture", "system design", "components"],
     ["identifies clear component boundaries", "explains communication patterns", "mentions trade-offs"],
     ["eventual consistency", "loose coupling", "bounded context"],
     "Describe the high-level architecture of the most complex system you have built. "
     "What were the main components and how did they communicate?"),

    ("architecture", "senior", None, ["monolith", "microservices", "trade-offs"],
     ["compares coupling/deployment complexity", "mentions team size impact", "discusses data boundaries"],
     ["strangler fig", "domain-driven design", "service mesh"],
     "How did you decide between a monolithic and a microservices architecture for your last major project? "
     "What trade-offs drove that decision?"),

    ("architecture", "senior", None, ["system design", "url shortener", "scalability"],
     ["estimates QPS correctly", "mentions hashing", "covers DB choice"],
     ["consistent hashing", "bloom filter", "read replicas"],
     "Walk me through how you would design a URL-shortening service that must handle "
     "10 000 requests per second with sub-50 ms latency."),

    ("architecture", "mid", None, ["coupling", "cohesion", "services"],
     ["defines clear interfaces", "mentions dependency inversion", "talks about contracts"],
     ["hexagonal architecture", "anti-corruption layer", "event-driven"],
     "How do you ensure loose coupling and high cohesion when splitting a large codebase into services?"),

    ("architecture", "senior", None, ["distributed systems", "resilience", "partial failure"],
     ["mentions retries with backoff", "circuit breaker", "idempotency"],
     ["bulkhead", "chaos engineering", "SLO vs SLA"],
     "What strategies have you used to make distributed systems resilient to partial failures?"),

    ("architecture", "mid", None, ["api gateway", "microservices", "routing"],
     ["describes auth offloading", "mentions rate limiting", "explains routing"],
     ["BFF pattern", "API composition", "service discovery"],
     "Explain the role of an API gateway in a microservices architecture and how you have used one in practice."),

    ("architecture", "senior", None, ["real-time", "notifications", "scale"],
     ["mentions WebSockets or SSE", "covers fan-out problem", "discusses persistence"],
     ["pub/sub", "long polling vs WebSocket", "message fanout"],
     "How would you architect a real-time notification system for millions of concurrent users?"),

    ("architecture", "mid", None, ["api versioning", "backward compatibility"],
     ["mentions URL versioning or headers", "considers deprecation lifecycle"],
     ["semantic versioning", "sunset headers", "contract testing"],
     "What is your approach to versioning APIs that are consumed by external clients?"),

    # ── Database ──────────────────────────────────────────────────────────────
    ("database", "mid", None, ["sql", "nosql", "storage choice"],
     ["compares ACID vs BASE", "mentions workload pattern", "considers query complexity"],
     ["document model vs relational", "write-heavy vs read-heavy", "normalization"],
     "How did you choose between relational and non-relational storage in a recent project? "
     "Walk through the specific factors."),

    ("database", "mid", None, ["slow query", "query optimization", "production"],
     ["uses EXPLAIN / EXPLAIN ANALYZE", "identifies missing index", "checks query plan"],
     ["index selectivity", "query planner", "covering index"],
     "Describe a situation where you diagnosed and resolved a slow database query in production. "
     "What tools and process did you use?"),

    ("database", "senior", None, ["sharding", "partitioning", "scale"],
     ["explains shard key selection", "discusses hot spot risk", "mentions cross-shard queries"],
     ["consistent hashing", "range vs hash sharding", "global secondary index"],
     "How have you implemented database sharding or partitioning? "
     "What problems did it solve and what new challenges did it introduce?"),

    ("database", "mid", None, ["locking", "optimistic", "pessimistic", "concurrency"],
     ["describes version column for optimistic", "explains SELECT FOR UPDATE", "discusses trade-offs"],
     ["MVCC", "phantom reads", "serializable isolation"],
     "Explain the difference between optimistic and pessimistic locking and when you have applied each."),

    ("database", "mid", None, ["migrations", "zero downtime", "deployment"],
     ["mentions expand-contract pattern", "backward-compatible steps", "rollback strategy"],
     ["online schema change", "gh-ost", "blue-green deployment"],
     "How do you manage database schema migrations in a zero-downtime deployment pipeline?"),

    ("database", "senior", None, ["multi-tenant", "data model", "saas"],
     ["discusses isolation levels", "schema-per-tenant vs shared schema", "covers security"],
     ["row-level security", "tenant discriminator", "connection pooling per tenant"],
     "Walk me through how you would design a data model for a multi-tenant SaaS application."),

    ("database", "senior", None, ["CAP theorem", "distributed", "consistency"],
     ["correctly defines C, A, P", "applies to real system choice", "mentions CP vs AP"],
     ["PACELC", "linearizability", "eventual consistency"],
     "What is the CAP theorem and how has it influenced an architectural decision you made?"),

    ("database", "mid", None, ["read replicas", "connection pooling", "caching", "throughput"],
     ["describes lag monitoring", "explains PgBouncer or similar", "covers cache-aside pattern"],
     ["replication lag", "pgBouncer", "read-your-writes consistency"],
     "Describe your experience with read replicas, connection pooling, or query caching to scale database throughput."),

    # ── Scaling ───────────────────────────────────────────────────────────────
    ("scaling", "mid", None, ["performance", "bottleneck", "profiling"],
     ["uses profiling tools", "identifies root cause", "measures before and after"],
     ["flame graph", "percentile latency", "amdahl's law"],
     "Describe a performance bottleneck you identified and fixed in a production system. "
     "What was your diagnostic approach?"),

    ("scaling", "mid", None, ["caching", "invalidation", "distributed cache"],
     ["describes cache-aside or write-through", "discusses TTL strategy", "handles stampede"],
     ["dog-pile effect", "cache stampede", "stale-while-revalidate"],
     "How have you implemented caching in a distributed system? How did you handle cache invalidation?"),

    ("scaling", "senior", None, ["autoscaling", "horizontal", "stateless"],
     ["explains stateless design requirement", "mentions sticky sessions issue", "covers readiness probes"],
     ["HPA", "KEDA", "graceful shutdown"],
     "Explain how horizontal auto-scaling works and what application design constraints it imposes on stateful services."),

    ("scaling", "senior", None, ["latency", "p99", "optimization"],
     ["establishes baseline", "identifies critical path", "mentions async offloading"],
     ["tail latency", "critical path analysis", "connection pool tuning"],
     "How would you reduce the p99 latency of a critical API endpoint from 2 s to under 200 ms?"),

    ("scaling", "mid", None, ["load testing", "performance testing", "k6", "locust"],
     ["mentions tool used", "describes ramp-up strategy", "tracks meaningful metrics"],
     ["throughput vs latency tradeoff", "SLO target", "saturation point"],
     "Describe your experience with load testing. What tools did you use, what metrics did you target, and what did you find?"),

    ("scaling", "senior", None, ["back-pressure", "message queue", "throughput"],
     ["describes consumer throttling", "mentions dead letter queue", "covers flow control"],
     ["reactive streams", "credit-based flow control", "consumer lag"],
     "How do you handle back-pressure in a high-throughput message-processing pipeline?"),

    ("scaling", "mid", None, ["memory", "optimization", "long-running"],
     ["mentions object pooling", "discusses GC tuning", "covers leak detection"],
     ["heap profiler", "arena allocator", "reference counting"],
     "What strategies have you used to optimise memory usage in a long-running service?"),

    ("scaling", "mid", None, ["cdn", "edge caching", "origin"],
     ["describes cache-control headers", "mentions purge strategy", "covers geo-distribution"],
     ["stale-while-revalidate", "surrogate keys", "CDN shield"],
     "How do you use CDN and edge caching to reduce origin load while keeping content fresh?"),

    # ── Security ──────────────────────────────────────────────────────────────
    ("security", "mid", None, ["auth", "authorization", "oauth", "jwt"],
     ["distinguishes authn vs authz", "mentions token validation", "covers inter-service auth"],
     ["PKCE", "zero-trust", "mTLS"],
     "How have you implemented authentication and authorisation in a multi-service application? "
     "Which protocols or patterns did you use?"),

    ("security", "mid", None, ["vulnerability", "security bug", "remediation"],
     ["describes discovery process", "explains impact assessment", "covers disclosure steps"],
     ["CVE", "CVSS score", "responsible disclosure"],
     "Describe a security vulnerability you discovered in your code or infrastructure. How did you remediate it?"),

    ("security", "mid", None, ["secrets", "api keys", "vault", "credentials"],
     ["mentions secrets manager", "discusses rotation", "covers CI/CD secret injection"],
     ["HashiCorp Vault", "AWS Secrets Manager", "SOPS"],
     "How do you manage secrets, API keys, and environment-specific credentials in a production deployment pipeline?"),

    ("security", "mid", None, ["input validation", "injection", "rest api"],
     ["mentions schema validation", "discusses parameterised queries", "covers sanitisation"],
     ["OWASP top 10", "parameterized queries", "allowlist validation"],
     "What is your approach to input validation and preventing injection attacks in a REST API?"),

    ("security", "mid", None, ["jwt", "token", "security"],
     ["explains header.payload.sig structure", "mentions expiry and rotation", "covers theft mitigation"],
     ["token refresh rotation", "audience claim", "revocation list"],
     "Explain how JWTs work and what their security limitations are. How do you mitigate token theft?"),

    ("security", "senior", None, ["rbac", "permissions", "access control"],
     ["describes role hierarchy", "explains policy enforcement", "covers audit logging"],
     ["ABAC vs RBAC", "policy-as-code", "OPA"],
     "How have you implemented role-based access control (RBAC) in a complex application?"),

    ("security", "senior", None, ["defense in depth", "cloud", "web app"],
     ["describes layered controls", "mentions WAF/DDoS", "covers logging and alerting"],
     ["zero trust", "network segmentation", "security posture"],
     "What does a defence-in-depth security strategy look like for a cloud-hosted web application?"),

    ("security", "mid", None, ["microservices", "inter-service", "communication"],
     ["mentions mTLS or service mesh", "discusses API keys vs tokens", "covers network policies"],
     ["mTLS", "service mesh", "SPIFFE/SPIRE"],
     "How do you implement secure inter-service communication in a microservices environment?"),

    # ── DevOps ────────────────────────────────────────────────────────────────
    ("devops", "mid", None, ["ci/cd", "pipeline", "deployment"],
     ["describes stage order", "mentions test gates", "covers rollback triggers"],
     ["deployment frequency", "DORA metrics", "trunk-based development"],
     "Describe the CI/CD pipeline you set up or improved most recently. "
     "What stages did it include and how did it reduce deployment risk?"),

    ("devops", "mid", None, ["docker", "kubernetes", "containers"],
     ["describes image layering", "mentions resource limits", "covers health probes"],
     ["OCI spec", "pod disruption budget", "init containers"],
     "How have you used Docker and container orchestration (Kubernetes or equivalent) in a production environment?"),

    ("devops", "senior", None, ["blue-green", "canary", "deployment strategy"],
     ["explains traffic splitting", "describes rollback trigger", "covers feature flags"],
     ["weighted routing", "progressive delivery", "Argo Rollouts"],
     "Walk me through how you would implement a blue-green or canary deployment for a critical service."),

    ("devops", "mid", None, ["infrastructure as code", "terraform", "iac"],
     ["mentions state management", "discusses module reuse", "covers drift detection"],
     ["state lock", "plan-apply workflow", "Atlantis"],
     "What is your approach to infrastructure-as-code? Which tools have you used and what problems did they solve?"),

    ("devops", "mid", None, ["observability", "logging", "metrics", "tracing"],
     ["describes structured logging", "mentions SLO/SLA", "covers distributed tracing"],
     ["OpenTelemetry", "RED method", "exemplars"],
     "How do you design observability into a new service — what logs, metrics, and traces do you instrument from day one?"),

    ("devops", "mid", None, ["incident", "on-call", "post-mortem"],
     ["describes detection to mitigation timeline", "mentions blameless retro", "covers action items"],
     ["MTTR", "blameless post-mortem", "runbook"],
     "Describe how you have handled a production incident from detection through post-mortem."),

    ("devops", "mid", None, ["cloud", "managed services", "aws", "azure", "gcp"],
     ["identifies operational savings", "discusses lock-in risk", "covers SLA dependence"],
     ["shared responsibility model", "managed vs self-hosted", "vendor lock-in"],
     "How have you used cloud-managed services (e.g., queues, object storage, managed databases) to reduce operational burden?"),

    ("devops", "mid", None, ["environment promotion", "dev staging production", "configuration"],
     ["mentions parity principle", "discusses config injection", "covers promotion gates"],
     ["12-factor app", "environment parity", "GitOps"],
     "How do you manage environment promotion (dev → staging → production) while maintaining configuration parity?"),

    # ── API Design ────────────────────────────────────────────────────────────
    ("api_design", "mid", None, ["rest", "api design", "backward compatibility"],
     ["mentions versioning strategy", "covers additive-only changes", "discusses deprecation"],
     ["Postel's law", "API lifecycle", "consumer-driven contracts"],
     "How do you design RESTful APIs that remain backward-compatible as requirements evolve?"),

    ("api_design", "mid", None, ["rest", "graphql", "comparison"],
     ["explains N+1 problem", "discusses over-fetching", "covers real use cases"],
     ["persisted queries", "DataLoader", "schema stitching"],
     "Compare REST and GraphQL — in which situations have you chosen each and why?"),

    ("api_design", "mid", None, ["rate limiting", "throttling", "circuit breaker"],
     ["describes token bucket or leaky bucket", "covers 429 retry-after", "mentions Hystrix/Resilience4j"],
     ["token bucket", "circuit breaker state machine", "exponential backoff"],
     "How have you implemented rate-limiting, throttling, or circuit-breaking in a service you built?"),

    ("api_design", "senior", None, ["idempotency", "financial", "transactions"],
     ["describes idempotency key pattern", "covers retry safety", "mentions at-least-once delivery"],
     ["idempotency key", "exactly-once semantics", "saga pattern"],
     "Describe how you handle idempotency in APIs that process financial or state-changing transactions."),

    ("api_design", "mid", None, ["api documentation", "contract testing", "openapi"],
     ["mentions OpenAPI/Swagger", "discusses Pact or schema contracts", "covers generated clients"],
     ["consumer-driven contracts", "schema registry", "Pact"],
     "What is your approach to API documentation and contract testing between producer and consumer services?"),

    ("api_design", "mid", None, ["webhooks", "event-driven", "third-party"],
     ["describes retry with exponential backoff", "covers signature verification", "mentions fanout"],
     ["HMAC signature", "event schema versioning", "dead letter queue"],
     "How do you design webhook or event-driven integrations with third-party systems?"),

    ("api_design", "mid", None, ["streaming", "sse", "server-sent events"],
     ["compares SSE vs WebSocket vs polling", "discusses backpressure", "covers reconnection"],
     ["chunked transfer encoding", "back-pressure", "grpc streaming"],
     "Describe your experience building or consuming streaming APIs or server-sent events."),

    # ── Algorithms / Problem-Solving ──────────────────────────────────────────
    ("algorithms", "senior", 4, ["scale", "bottleneck", "100x load"],
     ["identifies capacity constraints first", "prioritises highest-impact fix", "validates with math"],
     ["back-of-envelope estimation", "Amdahl's law", "saturation analysis"],
     "If you had to redesign your main project to handle 100× the current load, "
     "what is the first bottleneck you would tackle and how?"),

    ("algorithms", "mid", 4, ["on-call", "error rate", "debugging"],
     ["follows structured runbook", "checks metrics/logs/traces", "communicates status"],
     ["golden signals", "5 whys", "MECE decomposition"],
     "You are on-call and receive an alert that a critical endpoint has 10× its normal error rate. "
     "Walk me through your investigation and resolution steps."),

    ("algorithms", "senior", 4, ["distributed rate limiter", "stateless", "redis"],
     ["uses token bucket in Redis", "handles clock skew", "covers race conditions"],
     ["sliding window log", "Lua atomicity", "distributed counter"],
     "How would you implement a distributed rate limiter that works correctly across multiple stateless application instances?"),

    ("algorithms", "senior", 4, ["duplicate processing", "message queue", "idempotency"],
     ["idempotency key per message", "deduplication window", "outbox pattern"],
     ["exactly-once vs at-least-once", "outbox pattern", "idempotent consumer"],
     "Describe an approach to detect and prevent duplicate processing in a distributed message queue consumer."),

    ("algorithms", "senior", 4, ["job scheduling", "priority queue", "distributed"],
     ["covers job queue with priority lanes", "discusses executor model", "handles retries"],
     ["leaky bucket scheduler", "fair-share scheduling", "work stealing"],
     "How would you design a job-scheduling system that must handle millions of tasks with different priorities and deadlines?"),

    ("algorithms", "senior", 4, ["search", "relevance", "typo tolerance"],
     ["describes inverted index", "covers edit-distance for typos", "discusses ranking signals"],
     ["BM25", "edit distance", "ngram tokenization"],
     "Walk me through how you would build a search feature with typo tolerance and relevance ranking "
     "without a third-party search engine."),

    ("algorithms", "senior", 4, ["mobile", "offline sync", "conflict resolution"],
     ["describes conflict resolution strategy", "covers delta sync", "handles clock skew"],
     ["CRDTs", "last-write-wins", "vector clocks"],
     "How would you approach building a data-synchronisation mechanism between an offline mobile app and a cloud backend?"),

    ("algorithms", "senior", 4, ["anomaly detection", "real-time", "financial transactions"],
     ["describes statistical baseline", "covers streaming windowing", "discusses false positive budget"],
     ["z-score", "EWMA", "isolation forest"],
     "Design a system that detects anomalies in a real-time stream of financial transactions "
     "with low false-positive rate."),

    # ── Behavioral ────────────────────────────────────────────────────────────
    ("behavioral", "mid", 5, ["conflict", "disagreement", "technical decision"],
     ["uses STAR structure", "shows empathy", "describes outcome"],
     ["principled negotiation", "data-driven argument", "compromise"],
     "Describe a time a technical decision you made was challenged by a teammate or manager. "
     "How did you resolve the disagreement?"),

    ("behavioral", "mid", 5, ["debugging", "root cause", "complex bug"],
     ["describes systematic approach", "mentions isolation strategy", "covers tools used"],
     ["divide and conquer", "scientific method", "rubber duck debugging"],
     "Tell me about the most technically complex bug you have debugged. "
     "How did you approach finding the root cause?"),

    ("behavioral", "mid", 5, ["learning", "new technology", "deadline"],
     ["describes learning strategy", "shows time management", "delivers result"],
     ["time-boxed learning", "spike", "build vs buy"],
     "Give an example of a project where you had to learn a new technology quickly under tight deadline pressure."),

    ("behavioral", "mid", 5, ["risk", "communication", "technical risk"],
     ["identifies risk proactively", "quantifies impact", "suggests mitigation"],
     ["risk register", "FMEA", "stakeholder communication"],
     "Describe a situation where you identified a significant technical risk in a project plan. "
     "How did you communicate and mitigate it?"),

    ("behavioral", "senior", 5, ["refactoring", "legacy code", "strategy"],
     ["describes strangler fig or incremental approach", "mentions test coverage first", "covers risk management"],
     ["strangler fig pattern", "characterisation tests", "technical debt quadrant"],
     "Tell me about a time you had to refactor a large, poorly written codebase. "
     "What was your strategy and what did you learn?"),

    ("behavioral", "mid", 5, ["trade-offs", "priorities", "difficult choice"],
     ["acknowledges constraints", "uses data for decision", "accepts ownership"],
     ["opportunity cost", "reversible vs irreversible", "two-way door decision"],
     "Describe a situation where competing priorities forced you to make a difficult trade-off in your technical work."),

    ("behavioral", "mid", 5, ["mentoring", "junior developer", "teaching"],
     ["describes tailored approach", "measures growth", "shows patience"],
     ["pair programming", "code review as teaching", "growth mindset"],
     "Tell me about a time you mentored a junior developer. What approach did you take and what was the outcome?"),

    ("behavioral", "mid", 5, ["pushback", "product requirement", "technical concerns"],
     ["frames concern with data", "proposes alternative", "respects final decision"],
     ["no-estimate", "technical spike", "RFC process"],
     "Give an example of when you pushed back on a product or business requirement because of technical concerns. "
     "What happened?"),
]


# ══════════════════════════════════════════════════════════════════════════════
# Seeder
# ══════════════════════════════════════════════════════════════════════════════

def seed_knowledge_base() -> dict:
    """
    Idempotent: inserts topics and questions that don't yet exist.
    Returns {"topics_added": N, "questions_added": M}.
    """
    from database.connection import SessionLocal
    from models.db.kb_topic import KbTopic
    from models.db.kb_question import KbQuestion

    db = SessionLocal()
    try:
        topics_added = 0
        topic_map: dict[str, int] = {}

        for t in _TOPICS:
            existing = db.query(KbTopic).filter(KbTopic.name == t["name"]).first()
            if existing:
                topic_map[t["name"]] = existing.id
            else:
                row = KbTopic(name=t["name"], display_name=t["display_name"])
                db.add(row)
                db.flush()
                topic_map[t["name"]] = row.id
                topics_added += 1

        questions_added = 0
        for (topic_name, difficulty, round_hint, ctx_tags, ideal_pts, expert_t, q_text) in _QUESTIONS:
            existing = db.query(KbQuestion).filter(KbQuestion.question_text == q_text).first()
            if existing:
                continue
            row = KbQuestion(
                topic_id=topic_map[topic_name],
                question_text=q_text,
                difficulty=difficulty,
                round_hint=round_hint,
                context_tags=ctx_tags,
                ideal_points=ideal_pts,
                expert_terms=expert_t,
                is_active=True,
            )
            db.add(row)
            questions_added += 1

        db.commit()
        logger.info("[kb] Seed complete: %d topics, %d questions added.", topics_added, questions_added)
        return {"topics_added": topics_added, "questions_added": questions_added}
    except Exception as exc:
        db.rollback()
        logger.error("[kb] Seed failed: %s", exc)
        raise
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# Embedding precomputation
# ══════════════════════════════════════════════════════════════════════════════

def compute_and_store_embeddings(force_recompute: bool = False) -> int:
    """
    Precompute MiniLM embeddings for all active questions without stored embeddings.
    Pass force_recompute=True to regenerate all embeddings.
    Returns the number of embeddings written.
    """
    from database.connection import SessionLocal
    from models.db.kb_question import KbQuestion
    from models.db.kb_question_embedding import KbQuestionEmbedding

    enc = _get_encoder()
    if enc is None:
        logger.warning("[kb] Cannot compute embeddings — encoder not available.")
        return 0

    db = SessionLocal()
    try:
        q = db.query(KbQuestion).filter(KbQuestion.is_active == True)
        if not force_recompute:
            embedded_ids = {
                row.question_id
                for row in db.query(KbQuestionEmbedding.question_id).all()
            }
            questions = [r for r in q.all() if r.id not in embedded_ids]
        else:
            questions = q.all()
            db.query(KbQuestionEmbedding).delete()

        if not questions:
            logger.info("[kb] All embeddings up to date.")
            return 0

        texts = [r.question_text for r in questions]
        vecs  = _encode_texts(texts)
        if len(vecs) != len(questions):
            logger.error("[kb] Embedding count mismatch.")
            return 0

        written = 0
        for qrow, vec in zip(questions, vecs):
            existing = db.query(KbQuestionEmbedding).filter(
                KbQuestionEmbedding.question_id == qrow.id
            ).first()
            if existing:
                existing.embedding = vec
                existing.model_id = _MODEL_ID.split("/")[-1]
            else:
                db.add(KbQuestionEmbedding(
                    question_id=qrow.id,
                    model_id=_MODEL_ID.split("/")[-1],
                    embedding=vec,
                ))
            written += 1

        db.commit()
        logger.info("[kb] Stored %d embeddings.", written)
        return written
    except Exception as exc:
        db.rollback()
        logger.error("[kb] compute_and_store_embeddings failed: %s", exc)
        return 0
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# Gap-targeted retrieval
# ══════════════════════════════════════════════════════════════════════════════

def get_gap_targeted_question(
    gaps: list[str],
    jr_knowledge_gaps: list[str],
    project_tech_stacks: list[str],
    job_title: str,
    exclude_topics: Optional[list[str]] = None,
) -> str:
    """
    Return the single best gap-targeted question from the DB for Round 3.
    Falls back to a hardcoded template if the DB is empty or encoder unavailable.
    """
    from database.connection import SessionLocal
    from models.db.kb_question import KbQuestion
    from models.db.kb_question_embedding import KbQuestionEmbedding
    from models.db.kb_topic import KbTopic

    all_gaps = [g for g in (gaps + jr_knowledge_gaps) if g and g.strip()]
    if not all_gaps:
        return (
            "What was the most challenging technical problem you encountered "
            "in your recent projects and how did you resolve it?"
        )

    skip_topics = set(exclude_topics or ["architecture", "behavioral"])
    gap_query = " ".join(all_gaps[:6])
    if job_title:
        gap_query = f"{job_title} {gap_query}"

    db = SessionLocal()
    try:
        # Load all active embeddings, excluding skip topics
        rows = (
            db.query(KbQuestionEmbedding, KbQuestion, KbTopic)
            .join(KbQuestion, KbQuestionEmbedding.question_id == KbQuestion.id)
            .join(KbTopic, KbQuestion.topic_id == KbTopic.id)
            .filter(KbQuestion.is_active == True)
            .filter(~KbTopic.name.in_(skip_topics))
            .all()
        )

        if not rows:
            return _fallback_question_by_gaps(all_gaps)

        q_texts = [r[1].question_text for r in rows]
        q_vecs  = [r[0].embedding for r in rows]

        # Encode the gap query
        enc_vecs = _encode_texts([gap_query])
        if not enc_vecs:
            return _fallback_question_by_keyword(all_gaps, db)

        query_vec = enc_vecs[0]
        scored = [(
            _cosine(query_vec, q_vec), q_text
        ) for q_vec, q_text in zip(q_vecs, q_texts)]
        scored.sort(reverse=True)
        return scored[0][1]
    finally:
        db.close()


def get_all_five_questions(
    gaps: list[str],
    jr_knowledge_gaps: list[str],
    project_name: str,
    main_tech: str,
    job_title: str,
    application_id: Optional[int] = None,
    requisition_id: Optional[int] = None,
) -> list[str]:
    """
    Return 5 personalised questions for the interview rounds.

    Q1 — Project architecture warm-up (template — uses project_name / job_title)
    Q2 — Deep-dive on main project tech (template — uses main_tech)
    Q3 — Gap-targeted question (DB semantic retrieval)
    Q4 — Problem-solving scenario (DB retrieval from algorithms topic)
    Q5 — Behavioral / STAR (DB retrieval from behavioral topic)
    """
    project_label = project_name or "your most significant project"
    tech_label    = main_tech or "the technology stack you chose"

    q1 = (
        f"Walk me through the architecture of {project_label} — "
        "what were the main components, how did they interact, and what was your personal ownership?"
    )

    q2 = (
        f"Why did you choose {tech_label} for {project_label}? "
        "What alternatives did you consider and what trade-offs drove the decision?"
    )

    q3 = get_gap_targeted_question(
        gaps, jr_knowledge_gaps, [], job_title,
        exclude_topics=["architecture", "behavioral"],
    )

    q4 = _get_topic_question("algorithms", gaps + jr_knowledge_gaps, job_title)
    q5 = _get_topic_question("behavioral", gaps + jr_knowledge_gaps, job_title)

    questions = [q1, q2, q3, q4, q5]

    # Log selections to audit table if context is available
    if application_id and requisition_id:
        try:
            _log_selections_batch(application_id, requisition_id, questions, gaps)
        except Exception as exc:
            logger.warning("[kb] audit log failed: %s", exc)

    return questions


def _get_topic_question(
    topic_name: str,
    gap_terms: list[str],
    job_title: str,
) -> str:
    from database.connection import SessionLocal
    from models.db.kb_question import KbQuestion
    from models.db.kb_question_embedding import KbQuestionEmbedding
    from models.db.kb_topic import KbTopic

    db = SessionLocal()
    try:
        rows = (
            db.query(KbQuestionEmbedding, KbQuestion)
            .join(KbQuestion, KbQuestionEmbedding.question_id == KbQuestion.id)
            .join(KbTopic, KbQuestion.topic_id == KbTopic.id)
            .filter(KbQuestion.is_active == True)
            .filter(KbTopic.name == topic_name)
            .all()
        )

        if not rows:
            return _hardcoded_fallback(topic_name)

        gap_query = " ".join([job_title] + gap_terms[:4])
        enc_vecs  = _encode_texts([gap_query])
        if not enc_vecs:
            return rows[0][1].question_text  # first available

        query_vec = enc_vecs[0]
        scored = [(_cosine(query_vec, r[0].embedding), r[1].question_text) for r in rows]
        scored.sort(reverse=True)
        return scored[0][1]
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# Audit logging
# ══════════════════════════════════════════════════════════════════════════════

def log_question_selection(
    requisition_id: int,
    application_id: int,
    question_id: int,
    round_number: int,
    reason: str = "",
) -> None:
    from database.connection import SessionLocal
    from models.db.jr_question_selection import JrQuestionSelection

    db = SessionLocal()
    try:
        db.add(JrQuestionSelection(
            requisition_id=requisition_id,
            application_id=application_id,
            question_id=question_id,
            round_number=round_number,
            selection_reason=reason,
        ))
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("[kb] log_question_selection failed: %s", exc)
    finally:
        db.close()


def _log_selections_batch(
    application_id: int,
    requisition_id: int,
    questions: list[str],
    gaps: list[str],
) -> None:
    """Match question texts back to DB IDs and write audit rows."""
    from database.connection import SessionLocal
    from models.db.kb_question import KbQuestion
    from models.db.jr_question_selection import JrQuestionSelection

    db = SessionLocal()
    try:
        for rnd, q_text in enumerate(questions, start=1):
            row = db.query(KbQuestion).filter(KbQuestion.question_text == q_text).first()
            if row is None:
                continue
            db.add(JrQuestionSelection(
                requisition_id=requisition_id,
                application_id=application_id,
                question_id=row.id,
                round_number=rnd,
                selection_reason=f"gaps:{','.join(gaps[:3])}",
            ))
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("[kb] batch audit log failed: %s", exc)
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# Candidate gap loader (synchronous, for thread pool use)
# ══════════════════════════════════════════════════════════════════════════════

def load_candidate_gaps_sync(application_id: int) -> dict:
    """
    Synchronous DB loader — runs safely in asyncio.to_thread().

    Returns:
        {
          "gaps":              list[str],
          "jr_knowledge_gaps": list[str],
          "project_name":      str,
          "main_tech":         str,
          "job_title":         str,
          "requisition_id":    int | None,
        }
    """
    from database.connection import SessionLocal
    from models.db.application import Application
    from models.db.semantic_analysis_report import SemanticAnalysisReport
    from models.db.job_requisition import JobRequisition
    from models.db.cv_project import CVProject
    from models.db.jr_knowledge_gap import JrKnowledgeGap

    db = SessionLocal()
    try:
        application = (
            db.query(Application)
            .filter(Application.application_id == application_id)
            .first()
        )
        if not application:
            return _empty_gaps()

        requisition_id = (
            application.posting.requisition_id
            if application.posting else None
        )

        # Semantic analysis gaps
        gaps: list[str] = []
        sem = (
            db.query(SemanticAnalysisReport)
            .filter(SemanticAnalysisReport.application_id == application_id)
            .first()
        )
        if sem and sem.hr_explanation_json:
            try:
                hr_data = json.loads(sem.hr_explanation_json)
                gaps = hr_data.get("gaps", [])
            except (json.JSONDecodeError, TypeError):
                pass

        # JR knowledge gaps from jr_knowledge_gaps table
        jr_knowledge_gaps: list[str] = []
        if requisition_id:
            kg_rows = (
                db.query(JrKnowledgeGap)
                .filter(JrKnowledgeGap.jr_id == requisition_id)
                .all()
            )
            jr_knowledge_gaps = [
                r.tool_name for r in kg_rows if r.tool_name and r.tool_name.strip()
            ]

        # Job title
        job_title = ""
        if requisition_id:
            jr = (
                db.query(JobRequisition)
                .filter(JobRequisition.requisition_id == requisition_id)
                .first()
            )
            job_title = (jr.job_title or "") if jr else ""

        # Primary CV project
        project_name = ""
        main_tech    = ""
        cv_id = None
        for cv in (application.candidate.cvs or []):
            if cv.is_primary:
                cv_id = cv.cv_id
                break
        if cv_id:
            projects = (
                db.query(CVProject).filter(CVProject.cv_id == cv_id).all()
            )
            if projects:
                main_proj    = max(projects, key=lambda p: len(p.tech_stack or ""))
                project_name = main_proj.project_name or ""
                tech_stack   = main_proj.tech_stack or ""
                main_tech    = tech_stack.split(",")[0].strip() if tech_stack else ""

        return {
            "gaps":              gaps[:8],
            "jr_knowledge_gaps": jr_knowledge_gaps[:5],
            "project_name":      project_name,
            "main_tech":         main_tech,
            "job_title":         job_title,
            "requisition_id":    requisition_id,
        }

    except Exception as exc:
        logger.warning("[kb] load_candidate_gaps_sync failed: %s", exc)
        return _empty_gaps()
    finally:
        db.close()


def _empty_gaps() -> dict:
    return {
        "gaps": [], "jr_knowledge_gaps": [],
        "project_name": "", "main_tech": "",
        "job_title": "", "requisition_id": None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Convenience wrapper: get 5 questions directly from application_id
# ══════════════════════════════════════════════════════════════════════════════

def get_targeted_questions(application_id: int, n: int = 5) -> list[str]:
    """
    High-level entry point used by livekit_agent.py.
    Loads gap data then returns n personalised questions.
    """
    gap_data = load_candidate_gaps_sync(application_id)
    return get_all_five_questions(
        gaps=gap_data["gaps"],
        jr_knowledge_gaps=gap_data["jr_knowledge_gaps"],
        project_name=gap_data["project_name"],
        main_tech=gap_data["main_tech"],
        job_title=gap_data["job_title"],
        application_id=application_id,
        requisition_id=gap_data.get("requisition_id"),
    )[:n]


# ══════════════════════════════════════════════════════════════════════════════
# Fallback helpers (no DB / no encoder)
# ══════════════════════════════════════════════════════════════════════════════

def _fallback_question_by_gaps(gaps: list[str]) -> str:
    keyword_map = {
        "docker": "How have you used Docker and container orchestration in a production environment?",
        "kubernetes": "How have you used Docker and container orchestration in a production environment?",
        "security": "How have you implemented authentication and authorisation in a multi-service application?",
        "auth": "Explain how JWTs work and what their security limitations are. How do you mitigate token theft?",
        "sql": "Describe a situation where you diagnosed and resolved a slow database query in production.",
        "database": "How did you choose between relational and non-relational storage in a recent project?",
        "redis": "How have you implemented caching in a distributed system? How did you handle cache invalidation?",
        "scale": "Describe a performance bottleneck you identified and fixed in a production system.",
        "performance": "Describe a performance bottleneck you identified and fixed in a production system.",
        "api": "How do you design RESTful APIs that remain backward-compatible as requirements evolve?",
    }
    for gap in gaps:
        lower = gap.lower()
        for kw, q in keyword_map.items():
            if kw in lower:
                return q
    return (
        "What was the most challenging technical problem you encountered "
        "in your recent projects and how did you resolve it?"
    )


def _fallback_question_by_keyword(gaps: list[str], db) -> str:
    return _fallback_question_by_gaps(gaps)


def _hardcoded_fallback(topic_name: str) -> str:
    fallbacks = {
        "algorithms": (
            "If you had to redesign your main project to handle 100× the current load, "
            "what is the first bottleneck you would tackle and how?"
        ),
        "behavioral": (
            "Describe a time a technical decision you made was challenged by a teammate or manager. "
            "How did you resolve the disagreement?"
        ),
    }
    return fallbacks.get(topic_name, "Describe the most challenging technical problem you solved.")
