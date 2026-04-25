"""
Full knowledge-db seed — 12 categories, 100+ tools, 900+ concepts.
Run from src/:  python seed.py
"""
import httpx

BASE = "http://localhost:8000/api/v1/knowledge"

def post(client, path, payload):
    r = client.post(f"{BASE}{path}/", json=payload, follow_redirects=True)
    if r.status_code == 201:
        return r.json()
    if r.status_code in (400, 409, 422):
        # duplicate or validation error — safe to skip
        return None
    print(f"\n  ERROR {r.status_code} on {path}: {r.text[:500]}")
    r.raise_for_status()

def add_concepts(client, tool_id, triples):
    # triples: list of (level, name, notes)
    for level, name, notes in triples:
        r = post(client, "/concepts", {"tool_id": tool_id, "level": level, "name": name, "notes": notes})
        if r:
            print(f"      [{r['level']}] {r['name']}")

def run():
    with httpx.Client(timeout=120) as c:

        # ── CATEGORIES ────────────────────────────────────────────────────────
        print("=== CATEGORIES ===")
        CAT = {}
        for row in [
            ("backend",      "Backend Technologies",           "Server-side frameworks, APIs, databases, and messaging"),
            ("frontend",     "Frontend Technologies",          "UI frameworks, build tools, and browser technologies"),
            ("ai",           "AI / ML",                        "Machine learning, LLMs, MLOps, and AI tooling"),
            ("devops",       "DevOps & Infrastructure",        "CI/CD, containers, cloud, and infrastructure-as-code"),
            ("data",         "Data Engineering & Analytics",   "Pipelines, warehousing, streaming, and analytics"),
            ("mobile",       "Mobile Development",             "iOS, Android, and cross-platform mobile frameworks"),
            ("security",     "Cybersecurity",                  "AppSec, cloud security, cryptography, and pen-testing"),
            ("cloud",        "Cloud Platforms",                "GCP, Azure, Cloudflare, and serverless architecture"),
            ("databases",    "Databases",                      "Relational, NoSQL, graph, time-series, and NewSQL"),
            ("testing",      "Testing & QA",                   "Unit, integration, E2E, load testing, and API testing"),
            ("languages",    "Programming Languages",          "Core language knowledge across the stack"),
            ("architecture", "Software Architecture",          "System design, patterns, and distributed systems"),
        ]:
            r = post(c, "/categories", {"name": row[0], "label": row[1], "description": row[2]})
            if r:
                CAT[row[0]] = r["id"]
                print(f"  {row[0]} id={r['id']}")

        # ── TOOLS ─────────────────────────────────────────────────────────────
        print("\n=== TOOLS ===")
        TOOL = {}
        tool_list = [
            # backend
            ("FastAPI",              "fastapi",          "backend",      "High-performance async Python API framework"),
            ("Django",               "django",           "backend",      "Batteries-included Python web framework (MVT)"),
            ("Flask",                "flask",            "backend",      "Lightweight Python WSGI micro-framework"),
            ("Express.js",           "expressjs",        "backend",      "Minimal Node.js web application framework"),
            ("NestJS",               "nestjs",           "backend",      "TypeScript Node.js framework with DI and decorators"),
            ("Spring Boot",          "spring-boot",      "backend",      "Production-ready Java/Kotlin framework"),
            ("ASP.NET Core",         "aspnet-core",      "backend",      "Cross-platform .NET framework for web APIs"),
            ("Laravel",              "laravel",          "backend",      "Elegant PHP framework with expressive syntax"),
            ("Ruby on Rails",        "rails",            "backend",      "Convention-over-configuration Ruby web framework"),
            ("Gin",                  "gin",              "backend",      "High-performance HTTP framework for Go"),
            ("SQLAlchemy",           "sqlalchemy",       "backend",      "Python SQL toolkit and full-featured ORM"),
            ("Celery",               "celery",           "backend",      "Distributed task queue for Python"),
            ("RabbitMQ",             "rabbitmq",         "backend",      "AMQP message broker for reliable messaging"),
            ("Redis",                "redis",            "backend",      "In-memory store — cache, broker, and DB"),
            ("GraphQL",              "graphql",          "backend",      "Typed query language and runtime for APIs"),
            ("gRPC",                 "grpc",             "backend",      "High-performance RPC with Protocol Buffers"),
            ("Prisma",               "prisma",           "backend",      "Next-gen Node.js/TypeScript ORM with auto-migration"),
            ("TypeORM",              "typeorm",          "backend",      "ORM for TypeScript supporting multiple databases"),
            # frontend
            ("React",                "react",            "frontend",     "Component-based JavaScript UI library"),
            ("Vue.js",               "vuejs",            "frontend",     "Progressive JavaScript framework for UIs"),
            ("Angular",              "angular",          "frontend",     "Full-featured TypeScript SPA framework"),
            ("Next.js",              "nextjs",           "frontend",     "React framework with SSR, SSG, and App Router"),
            ("Svelte",               "svelte",           "frontend",     "Compile-time reactive UI framework with no virtual DOM"),
            ("TypeScript",           "typescript",       "frontend",     "Strongly-typed superset of JavaScript"),
            ("Tailwind CSS",         "tailwind",         "frontend",     "Utility-first CSS framework"),
            ("Vite",                 "vite",             "frontend",     "Lightning-fast frontend build tool using native ESM"),
            ("Webpack",              "webpack",          "frontend",     "Powerful and configurable JavaScript module bundler"),
            ("Storybook",            "storybook",        "frontend",     "UI component workshop for isolated development"),
            # ai
            ("LangChain",            "langchain",        "ai",           "Framework for building LLM-powered apps and agents"),
            ("LlamaIndex",           "llamaindex",       "ai",           "Data framework for LLM applications and RAG"),
            ("CrewAI",               "crewai",           "ai",           "Multi-agent orchestration framework built on LangChain"),
            ("OpenAI API",           "openai-api",       "ai",           "API access to GPT-4o, embeddings, Whisper, and DALL-E"),
            ("PyTorch",              "pytorch",          "ai",           "Dynamic deep learning framework from Meta"),
            ("TensorFlow",           "tensorflow",       "ai",           "End-to-end ML platform from Google"),
            ("Scikit-learn",         "sklearn",          "ai",           "Classical ML algorithms and pipelines in Python"),
            ("Hugging Face",         "hf-transformers",  "ai",           "Pre-trained transformers for NLP, vision, and audio"),
            ("Vector Databases",     "vector-db",        "ai",           "Pinecone, ChromaDB, Weaviate — embedding storage"),
            ("MLflow",               "mlflow",           "ai",           "MLOps platform for experiment tracking and model registry"),
            ("Weights & Biases",     "wandb",            "ai",           "Experiment tracking, model registry, and dataset versioning"),
            # devops
            ("Docker",               "docker",           "devops",       "Container platform for build, ship, run"),
            ("Kubernetes",           "kubernetes",       "devops",       "Container orchestration and auto-scaling"),
            ("Helm",                 "helm",             "devops",       "Kubernetes package manager using templated charts"),
            ("ArgoCD",               "argocd",           "devops",       "GitOps continuous delivery tool for Kubernetes"),
            ("GitHub Actions",       "gh-actions",       "devops",       "Native GitHub CI/CD pipeline platform"),
            ("GitLab CI",            "gitlab-ci",        "devops",       "Integrated CI/CD in GitLab with .gitlab-ci.yml"),
            ("Jenkins",              "jenkins",          "devops",       "Extensible open-source automation server"),
            ("Terraform",            "terraform",        "devops",       "Infrastructure-as-Code with HCL"),
            ("Ansible",              "ansible",          "devops",       "Agentless configuration management and automation"),
            ("Linux & Bash",         "linux-bash",       "devops",       "Linux administration and Bash scripting"),
            ("Nginx",                "nginx",            "devops",       "High-performance reverse proxy and web server"),
            ("Prometheus & Grafana", "prom-grafana",     "devops",       "Metrics collection and visualisation stack"),
            # data
            ("Apache Kafka",         "kafka",            "data",         "Distributed event streaming platform"),
            ("Apache Airflow",       "airflow",          "data",         "Workflow orchestration for data pipelines"),
            ("dbt",                  "dbt",              "data",         "SQL-based data transformation in the warehouse"),
            ("Apache Spark",         "spark",            "data",         "Unified engine for large-scale data processing"),
            ("Elasticsearch",        "elasticsearch",    "data",         "Distributed search and analytics engine"),
            ("Snowflake",            "snowflake",        "data",         "Cloud data warehouse with elastic compute"),
            ("Pandas",               "pandas",           "data",         "Python DataFrame library for data manipulation"),
            ("ClickHouse",           "clickhouse",       "data",         "Column-oriented OLAP database for analytics"),
            ("Apache Flink",         "flink",            "data",         "Stateful stream and batch processing engine"),
            ("BigQuery",             "bigquery",         "data",         "Serverless data warehouse on Google Cloud"),
            ("Databricks",           "databricks",       "data",         "Unified analytics platform on Apache Spark + Delta Lake"),
            # mobile
            ("React Native",         "react-native",     "mobile",       "Cross-platform mobile apps with React"),
            ("Flutter",              "flutter",          "mobile",       "Google's UI toolkit for multi-platform apps from one Dart codebase"),
            ("SwiftUI",              "swiftui",          "mobile",       "Declarative UI framework for Apple platforms"),
            ("Android / Kotlin",     "android-kotlin",   "mobile",       "Native Android development with Kotlin and Jetpack"),
            ("Expo",                 "expo",             "mobile",       "Managed workflow and SDK on top of React Native"),
            # security
            ("OWASP Top 10",         "owasp",            "security",     "Top 10 critical web application security risks"),
            ("OAuth2 & JWT",         "oauth-jwt",        "security",     "Delegated auth protocol and stateless token standard"),
            ("Web App Security",     "web-security",     "security",     "XSS, CSRF, SQLi, SSRF — attack and defence patterns"),
            ("Container Security",   "container-sec",    "security",     "Securing Docker, Kubernetes, and CI/CD pipelines"),
            ("Penetration Testing",  "pentest",          "security",     "Methodology and tooling for authorised security assessment"),
            ("Cryptography",         "cryptography",     "security",     "Hashing, encryption, PKI, and TLS fundamentals"),
            # cloud
            ("Google Cloud (GCP)",   "gcp",              "cloud",        "Google Cloud services — GKE, BigQuery, Pub/Sub, Cloud Run"),
            ("Microsoft Azure",      "azure",            "cloud",        "Azure services — AKS, Functions, Cosmos DB, DevOps"),
            ("AWS",                  "aws",              "cloud",        "Amazon Web Services — EC2, S3, Lambda, RDS, ECS"),
            ("Cloudflare",           "cloudflare",       "cloud",        "CDN, edge computing, DNS, and security platform"),
            ("Serverless Architecture","serverless",     "cloud",        "Lambda, Cloud Functions, Vercel edge — FaaS patterns"),
            # databases
            ("PostgreSQL",           "postgresql",       "databases",    "Advanced open-source relational database"),
            ("MySQL",                "mysql",            "databases",    "World's most popular open-source relational database"),
            ("MongoDB",              "mongodb",          "databases",    "Document-oriented NoSQL database"),
            ("Cassandra",            "cassandra",        "databases",    "Wide-column NoSQL for high-throughput workloads"),
            ("DynamoDB",             "dynamodb",         "databases",    "AWS managed key-value and document NoSQL store"),
            ("Neo4j",                "neo4j",            "databases",    "Native graph database with Cypher query language"),
            ("InfluxDB",             "influxdb",         "databases",    "Purpose-built time-series database"),
            ("Firebase / Firestore", "firebase",         "databases",    "Google's BaaS with real-time NoSQL database"),
            ("CockroachDB",          "cockroachdb",      "databases",    "Distributed SQL database with Postgres compatibility"),
            ("SQLite",               "sqlite",           "databases",    "Embedded, serverless, zero-configuration SQL engine"),
            # testing
            ("Pytest",               "pytest",           "testing",      "Powerful Python testing framework with fixtures"),
            ("Jest",                 "jest",             "testing",      "JavaScript testing framework with snapshot support"),
            ("Cypress",              "cypress",          "testing",      "Fast, reliable E2E testing for web apps"),
            ("Playwright",           "playwright",       "testing",      "Cross-browser E2E testing from Microsoft"),
            ("k6",                   "k6",               "testing",      "Modern load and performance testing tool"),
            ("Postman / Newman",     "postman",          "testing",      "API development, testing, and automation platform"),
            # languages
            ("Python",               "python",           "languages",    "General-purpose, high-level interpreted language"),
            ("JavaScript / ES6+",    "javascript",       "languages",    "The language of the web — modern ES features"),
            ("Go",                   "golang",           "languages",    "Statically typed, compiled language by Google"),
            ("Java",                 "java",             "languages",    "Object-oriented, platform-independent JVM language"),
            ("Rust",                 "rust",             "languages",    "Systems language focused on safety and performance"),
            ("Kotlin",               "kotlin",           "languages",    "Modern JVM language — primary Android language"),
            ("Swift",                "swift",            "languages",    "Apple's fast and safe language for iOS/macOS"),
            # architecture
            ("System Design",        "system-design",    "architecture", "Designing scalable, reliable distributed systems"),
            ("Microservices",        "microservices",    "architecture", "Decomposing applications into independently deployable services"),
            ("REST API Design",      "rest-api-design",  "architecture", "Principles and best practices for RESTful APIs"),
            ("Event-Driven Architecture","event-driven", "architecture", "Async communication via events and message brokers"),
            ("Domain-Driven Design", "ddd",              "architecture", "Modelling software around the business domain"),
            ("CQRS & Event Sourcing","cqrs",             "architecture", "Command/query separation and event-log as source of truth"),
        ]
        for name, slug, cat, desc in tool_list:
            cat_id = CAT.get(cat)
            if not cat_id:
                print(f"  skip tool {name} — no category")
                continue
            r = post(c, "/tools", {"name": name, "slug": slug, "description": desc, "category_id": cat_id})
            if r:
                TOOL[slug] = r["id"]
                print(f"  {name} id={r['id']}")

        # ── CONCEPTS ──────────────────────────────────────────────────────────
        print("\n=== CONCEPTS ===")

        # helper: (level, name, notes)
        B, M, H = "beginner", "mid", "high"

        # ── BACKEND ───────────────────────────────────────────────────────────
        if t := TOOL.get("fastapi"):
            print(f"\n  FastAPI")
            add_concepts(c, t, [
                (B,"Basic Routes","@app.get/post/put/delete, HTTP methods, path decorator syntax."),
                (B,"Path & Query Parameters","Path params /items/{id}, query params, Optional with defaults."),
                (B,"Request Body & Pydantic","Pydantic models as bodies, field validation, nested models."),
                (B,"Response Models","response_model param, filtering output fields, status codes."),
                (M,"Async / Await","async def vs def, blocking I/O in event loop, asyncio basics."),
                (M,"Dependency Injection","Depends(), chaining deps, session injection, shared services."),
                (M,"Middleware","Custom middleware, logging, auth checks, execution order."),
                (M,"HTTPException & Error Handlers","Custom exception handlers, 422 shaping, error detail."),
                (M,"Background Tasks","BackgroundTasks, fire-and-forget, diff vs Celery."),
                (H,"OAuth2 + JWT","OAuth2PasswordBearer, token creation/validation, refresh."),
                (H,"WebSockets","WS endpoint, connection lifecycle, broadcasting."),
                (H,"Custom APIRouter","Modular routers, prefixes, tags, router-level deps."),
                (H,"Testing with TestClient","TestClient/AsyncClient, dependency overrides."),
                (H,"Lifespan Events","lifespan context manager, startup/shutdown, async init."),
            ])

        if t := TOOL.get("django"):
            print(f"\n  Django")
            add_concepts(c, t, [
                (B,"MVT Architecture","Model-View-Template pattern, request lifecycle."),
                (B,"Models & ORM","Field types, migrations, basic QuerySet operations."),
                (B,"URL Routing","path(), include(), namespaces, URL resolution."),
                (B,"Django Admin","Registering models, list_display, search_fields, actions."),
                (M,"Class-Based Views","ListView/DetailView/CreateView, mixins, method_decorator."),
                (M,"Django REST Framework","Serializers, ViewSets, Routers, permissions, pagination."),
                (M,"Auth & Sessions","Custom User model, @login_required, permissions."),
                (M,"Forms & Validation","ModelForm, validators, CSRF, file uploads."),
                (M,"Middleware","Custom middleware, execution order, process_request."),
                (H,"Celery Integration","Async tasks, beat scheduling, result backends."),
                (H,"Caching","Per-view/site/low-level APIs, Redis backend, invalidation."),
                (H,"Signals","pre_save/post_save receivers, avoiding pitfalls."),
                (H,"Management Commands","BaseCommand, add_arguments, maintenance scripts."),
                (H,"Multi-tenancy","Row-level vs schema-level isolation, DB routing."),
            ])

        if t := TOOL.get("flask"):
            print(f"\n  Flask")
            add_concepts(c, t, [
                (B,"App Factory & Routes","@app.route, HTTP methods, create_app() pattern."),
                (B,"Request & Response","request.args/json/form, jsonify, status codes."),
                (B,"Jinja2 Templates","render_template, variables, control flow, inheritance."),
                (B,"Configuration","app.config, config classes, environment configs."),
                (M,"Blueprints","Modular app design, url_prefix, blueprint templates."),
                (M,"Flask-SQLAlchemy","db.Model, relationships, db.session, query patterns."),
                (M,"Error Handling","@errorhandler, abort(), custom JSON error responses."),
                (M,"Flask-Login","UserMixin, @login_required, current_user, login flows."),
                (H,"App Context & g","Request vs app context, per-request state, teardown."),
                (H,"Extensions Architecture","init_app() pattern, lazy initialisation."),
                (H,"Testing","FlaskClient, overriding config, mocking externals."),
            ])

        if t := TOOL.get("expressjs"):
            print(f"\n  Express.js")
            add_concepts(c, t, [
                (B,"Routing Basics","Router, HTTP methods, route params, query strings."),
                (B,"Middleware Chain","app.use(), execution order, next(), error middleware."),
                (B,"Request & Response","req.body/params/query, res.json/status/send."),
                (M,"MVC Structure","Modular routing, controller/service separation."),
                (M,"JWT Authentication","jsonwebtoken, auth middleware, protecting routes."),
                (M,"Error Handling","Global error handler, async errors, custom classes."),
                (M,"Database Integration","Mongoose/Sequelize, connection pooling, models."),
                (H,"Clustering & Performance","cluster module, PM2, caching, connection pooling."),
                (H,"Socket.io","Rooms, namespaces, events, Redis adapter for scale."),
                (H,"Security Hardening","helmet.js, rate-limit, CORS, input sanitisation."),
            ])

        if t := TOOL.get("nestjs"):
            print(f"\n  NestJS")
            add_concepts(c, t, [
                (B,"Modules, Controllers, Providers","Core NestJS building blocks, @Module/@Controller/@Injectable."),
                (B,"Decorators & DI","Constructor injection, @Inject, provider scope (singleton/request/transient)."),
                (B,"Routing & DTOs","@Get/@Post params, class-validator DTOs, ValidationPipe."),
                (M,"Interceptors & Guards","@UseGuards for auth, @UseInterceptors for logging/transform."),
                (M,"Pipes & Exception Filters","Built-in pipes, custom validation, HttpExceptionFilter."),
                (M,"TypeORM / Prisma Integration","Repository pattern, entity definitions, transactions."),
                (M,"Configuration & Env","ConfigModule, @nestjs/config, env schema validation."),
                (H,"Microservices Transport","TCP/Redis/RabbitMQ transports, @MessagePattern, hybrid apps."),
                (H,"WebSockets Gateway","@WebSocketGateway, @SubscribeMessage, rooms."),
                (H,"Testing","unit with Jest, e2e with supertest, overriding providers."),
            ])

        if t := TOOL.get("spring-boot"):
            print(f"\n  Spring Boot")
            add_concepts(c, t, [
                (B,"Auto-Configuration","@SpringBootApplication, starters, auto-config conditions."),
                (B,"REST Controllers","@RestController, @GetMapping, @RequestBody, @PathVariable."),
                (B,"Spring Data JPA","@Entity, JpaRepository, derived query methods, @Query."),
                (M,"Dependency Injection","@Autowired, constructor injection, @Component/@Service/@Repository."),
                (M,"Spring Security","Authentication, authorization, JWT filters, password encoding."),
                (M,"Exception Handling","@ControllerAdvice, @ExceptionHandler, ProblemDetail."),
                (M,"Profiles & Configuration","@Profile, application.yml, @ConfigurationProperties."),
                (H,"Reactive with WebFlux","Mono/Flux, reactive repositories, backpressure."),
                (H,"Spring Batch","Jobs, Steps, ItemReader/Processor/Writer, skip/retry."),
                (H,"Testing","@SpringBootTest, MockMvc, @DataJpaTest, Testcontainers."),
            ])

        if t := TOOL.get("aspnet-core"):
            print(f"\n  ASP.NET Core")
            add_concepts(c, t, [
                (B,"Controllers & Routing","[ApiController], [HttpGet], attribute routing, model binding."),
                (B,"Minimal APIs","app.MapGet/MapPost, route groups, filters, OpenAPI."),
                (B,"Entity Framework Core","DbContext, migrations, LINQ queries, relationships."),
                (M,"Middleware Pipeline","Use/Run/Map, order matters, custom middleware."),
                (M,"Authentication & Authorization","JWT bearer, policies, claims, [Authorize]."),
                (M,"Dependency Injection","Built-in DI, lifetime (Singleton/Scoped/Transient), IOptions."),
                (M,"Configuration","appsettings.json, environment overrides, secrets."),
                (H,"SignalR","Real-time hubs, groups, clients, scaling with Redis backplane."),
                (H,"Background Services","IHostedService, BackgroundService, Quartz.NET."),
                (H,"Testing","WebApplicationFactory, HttpClient, mocking services."),
            ])

        if t := TOOL.get("laravel"):
            print(f"\n  Laravel")
            add_concepts(c, t, [
                (B,"Routing & Controllers","Route::get/post, route params, resource controllers."),
                (B,"Eloquent ORM","Model definition, relationships (hasMany/belongsTo), query builder."),
                (B,"Blade Templates","Directives (@if/@foreach), layouts, components."),
                (M,"Middleware","Creating middleware, global vs route middleware, terminate()."),
                (M,"Authentication (Breeze/Sanctum)","API tokens, SPA auth, session-based auth."),
                (M,"Queues & Jobs","Job classes, queue workers, failed jobs, retries."),
                (M,"Artisan CLI","Custom commands, make:command, task scheduling."),
                (H,"Service Container & Providers","Binding, resolving, service providers, facades."),
                (H,"Testing","PHPUnit, RefreshDatabase, Http fake, mocking."),
                (H,"Laravel Octane","Swoole/RoadRunner, performance, long-running workers."),
            ])

        if t := TOOL.get("rails"):
            print(f"\n  Ruby on Rails")
            add_concepts(c, t, [
                (B,"MVC & Convention","CoC/DRY, generators, routing resources, naming conventions."),
                (B,"ActiveRecord","Model CRUD, validations, callbacks, associations."),
                (B,"Views & ERB","ERB templates, layouts, partials, helpers."),
                (M,"RESTful API Mode","API-only app, Jbuilder/Active Model Serializers, versioning."),
                (M,"Authentication","Devise gem, JWT with knock/jwt, session management."),
                (M,"Background Jobs","Sidekiq, ActiveJob, queue adapters, retries."),
                (M,"Testing with RSpec","describe/it/expect, FactoryBot, VCR cassettes."),
                (H,"ActiveRecord Advanced","Scopes, STI, polymorphic, complex queries, N+1 avoidance."),
                (H,"Hotwire & Turbo","Turbo Streams/Frames, Stimulus controllers, SPA-like UX."),
                (H,"Performance Tuning","Bullet gem, DB indexes, caching (fragment/action/page)."),
            ])

        if t := TOOL.get("gin"):
            print(f"\n  Gin (Go)")
            add_concepts(c, t, [
                (B,"Router & Handlers","gin.Default(), GET/POST/group, c.JSON, c.Param, c.Query."),
                (B,"Binding & Validation","ShouldBindJSON, struct tags, validator constraints."),
                (B,"Middleware","Use(), Logger, Recovery, custom middleware, c.Next/Abort."),
                (M,"Route Groups & Auth","Group(), JWT middleware, Bearer token validation."),
                (M,"Error Handling","c.Error, central error middleware, problem JSON."),
                (M,"Database with GORM","Model definition, CRUD, associations, migrations."),
                (M,"Configuration","Viper or env-based config, environment profiles."),
                (H,"Testing","httptest.NewRecorder, TestMain, table-driven tests."),
                (H,"Graceful Shutdown","http.Server.Shutdown, signal handling, draining."),
                (H,"Structured Logging & Tracing","Zerolog/Zap, OpenTelemetry, trace propagation."),
            ])

        if t := TOOL.get("sqlalchemy"):
            print(f"\n  SQLAlchemy")
            add_concepts(c, t, [
                (B,"Engine & Connection","create_engine, connection strings, echo, text() queries."),
                (B,"Declarative Models","DeclarativeBase, Column types, pk, nullable, default."),
                (B,"Session Basics","sessionmaker, add/commit/rollback/refresh."),
                (M,"Relationships","one-to-many, many-to-many, back_populates, lazy loading."),
                (M,"Query Filters","select(), where(), order_by(), joinedload, selectinload."),
                (M,"Alembic Migrations","autogenerate, upgrade/downgrade, data migrations."),
                (H,"2.0 Style","Mapped[], mapped_column(), scalars(), execute()."),
                (H,"Events","event.listens_for, before_insert, after_update."),
                (H,"Connection Pooling","QueuePool, pool_size, max_overflow, pool_pre_ping."),
            ])

        if t := TOOL.get("celery"):
            print(f"\n  Celery")
            add_concepts(c, t, [
                (B,"Task Definition","@app.task, .delay(), .apply_async(), arguments."),
                (B,"Worker & Broker","Worker startup, Redis/RabbitMQ broker URL, result backend."),
                (B,"Task States","PENDING/SUCCESS/FAILURE, AsyncResult, result retrieval."),
                (M,"Routing & Queues","Named queues, routing_key, worker concurrency."),
                (M,"Retry Strategies","autoretry_for, max_retries, exponential backoff."),
                (M,"Celery Beat","Periodic tasks, crontab, database-backed schedules."),
                (H,"Canvas Primitives","chain, group, chord — composing workflows."),
                (H,"Monitoring","Flower dashboard, events, failure alerting."),
                (H,"Scalability","Horizontal scaling, rate limits, autoscaling."),
            ])

        if t := TOOL.get("rabbitmq"):
            print(f"\n  RabbitMQ")
            add_concepts(c, t, [
                (B,"AMQP Concepts","Producers, consumers, queues, exchanges, bindings."),
                (B,"Exchange Types","Direct, fanout, topic, headers — routing patterns."),
                (B,"Publish & Consume","Declare queues, basic_publish, basic_consume, ack/nack."),
                (M,"Message Durability","Durable queues, persistent messages, delivery guarantees."),
                (M,"Dead Letter Exchanges","DLX, TTL, retry queue pattern."),
                (M,"Prefetch & QoS","basic_qos, prefetch_count, fair dispatch."),
                (H,"Clustering","Quorum queues, network partitions, HA setup."),
                (H,"Shovel & Federation","Cross-cluster routing, multi-site federation."),
                (H,"Performance Tuning","Lazy queues, flow control, management API."),
            ])

        if t := TOOL.get("redis"):
            print(f"\n  Redis")
            add_concepts(c, t, [
                (B,"Data Types","Strings, lists, hashes, sets, sorted sets."),
                (B,"Core Commands","GET/SET/DEL/EXISTS/EXPIRE/TTL, atomic ops."),
                (B,"Key Naming","Namespace patterns, collision avoidance, TTL strategy."),
                (M,"Pub/Sub","PUBLISH/SUBSCRIBE, channels, at-most-once delivery."),
                (M,"Caching Patterns","Cache-aside, write-through, stampede prevention."),
                (M,"Transactions","MULTI/EXEC, WATCH, pipeline batching."),
                (H,"Streams","XADD/XREAD, consumer groups, vs Kafka."),
                (H,"Lua Scripting","Atomic EVALSHA, rate-limiting, compare-and-swap."),
                (H,"Cluster & HA","Hash slots, Sentinel, failover, resharding."),
            ])

        if t := TOOL.get("graphql"):
            print(f"\n  GraphQL")
            add_concepts(c, t, [
                (B,"Schema & Types","SDL, scalars/objects/enums, non-null, schema-first."),
                (B,"Queries & Mutations","Variables, aliases, field selection, mutations."),
                (B,"Resolvers","parent/args/context/info, resolver chain."),
                (M,"DataLoader & N+1","Batching, per-request caching, N+1 elimination."),
                (M,"Auth & Context","Token in context, directive-based permissions."),
                (M,"Subscriptions","WebSocket, pub/sub integration, filter by subscriber."),
                (H,"Federation","Apollo subgraphs, gateway, cross-service type extensions."),
                (H,"Persisted Queries","APQ, query complexity limits, depth limits."),
                (H,"Code-First (Strawberry)","Python code-first schema, Pydantic integration."),
            ])

        if t := TOOL.get("grpc"):
            print(f"\n  gRPC")
            add_concepts(c, t, [
                (B,"Protocol Buffers","message/service definitions, proto3 syntax, protoc."),
                (B,"Unary RPC","Stub generation, channel setup, deadlines."),
                (B,"gRPC vs REST","When to choose gRPC, binary vs JSON, HTTP/2."),
                (M,"Streaming RPC","Server/client/bidirectional streaming use cases."),
                (M,"Interceptors","Logging, auth, retry — client and server side."),
                (M,"Error Handling","Status codes, error details, metadata."),
                (H,"Auth & TLS","mTLS, token metadata, channel credentials."),
                (H,"gRPC-Web & Transcoding","Browser support, grpc-gateway for REST compat."),
                (H,"Performance","Connection pooling, keepalive, compression, flow control."),
            ])

        if t := TOOL.get("prisma"):
            print(f"\n  Prisma")
            add_concepts(c, t, [
                (B,"Schema Definition","prisma.schema, model blocks, field types, @id/@unique."),
                (B,"Migrations","prisma migrate dev/deploy, migration history, shadow DB."),
                (B,"CRUD with Prisma Client","findMany, create, update, delete, upsert."),
                (M,"Relations","one-to-one/many, explicit many-to-many, @relation."),
                (M,"Filtering & Sorting","where, orderBy, select, include, cursor pagination."),
                (M,"Transactions","$transaction, nested writes, interactive transactions."),
                (H,"Raw Queries","$queryRaw, $executeRaw, parameterised queries."),
                (H,"Prisma Middleware","Soft delete, audit logging, data transformation."),
                (H,"Multi-schema & Multi-DB","multiple schemas in one Prisma project, db adapters."),
            ])

        if t := TOOL.get("typeorm"):
            print(f"\n  TypeORM")
            add_concepts(c, t, [
                (B,"Entity & Decorators","@Entity, @Column, @PrimaryGeneratedColumn."),
                (B,"Repository Pattern","getRepository(), find/save/remove/update."),
                (B,"Migrations","migration:generate/run/revert, TypeORM CLI."),
                (M,"Relations","@OneToMany, @ManyToOne, @JoinTable, eager vs lazy."),
                (M,"QueryBuilder","createQueryBuilder, where, join, groupBy."),
                (M,"Transactions","DataSource.transaction, QueryRunner."),
                (H,"Subscribers & Listeners","@BeforeInsert, entity lifecycle events."),
                (H,"Multiple Database Support","DataSource config, MySQL/Postgres/SQLite/MongoDB."),
                (H,"Testing","In-memory SQLite setup, test DataSource, seed helpers."),
            ])

        # ── FRONTEND ──────────────────────────────────────────────────────────
        if t := TOOL.get("react"):
            print(f"\n  React")
            add_concepts(c, t, [
                (B,"JSX Syntax","JSX vs HTML, expressions, conditional rendering, lists."),
                (B,"Components & Props","Functional components, prop passing, children."),
                (B,"useState","Immutable updates, multiple slices, re-render triggers."),
                (M,"useEffect","Dependency array, cleanup, data fetching on mount."),
                (M,"Context API","createContext/useContext, when NOT to use context."),
                (M,"React Router","useNavigate, useParams, protected routes, nested."),
                (H,"Performance","useMemo, useCallback, React.memo, virtualisation."),
                (H,"Custom Hooks","Extracting logic, hook composition, testing."),
                (H,"State Management","Redux Toolkit vs Zustand vs Jotai trade-offs."),
            ])

        if t := TOOL.get("vuejs"):
            print(f"\n  Vue.js")
            add_concepts(c, t, [
                (B,"Template Syntax","v-bind, v-model, v-if, v-for, v-on."),
                (B,"Options API","data(), methods, computed, watch, lifecycle hooks."),
                (B,"Components","SFC, props, emits, slots, registration."),
                (M,"Composition API","setup(), ref(), reactive(), composables."),
                (M,"Vue Router","Guards, useRouter/useRoute, lazy loading."),
                (M,"Pinia","defineStore, state/getters/actions, devtools."),
                (H,"Custom Directives","Directive hooks, plugin install, provide/inject."),
                (H,"Performance","Async components, v-memo, keep-alive."),
                (H,"Nuxt.js / SSR","Nuxt 3, useFetch, SSR/SSG/SPA modes, hydration."),
            ])

        if t := TOOL.get("angular"):
            print(f"\n  Angular")
            add_concepts(c, t, [
                (B,"Components","@Component, template syntax, property/event binding."),
                (B,"Directives","NgIf, NgFor, NgClass/NgStyle, custom directives."),
                (B,"Services & DI","@Injectable, providedIn, constructor injection."),
                (M,"RxJS","Observable, pipe, map/filter/switchMap/takeUntil."),
                (M,"Router","Guards, lazy loading, resolvers, paramMap."),
                (M,"Reactive Forms","FormBuilder, Validators, async validators."),
                (H,"Change Detection","Default vs OnPush, markForCheck, NgZone."),
                (H,"Standalone Components","bootstrapApplication, import patterns."),
                (H,"NgRx","Store, Actions, Reducers, Effects, Selectors."),
            ])

        if t := TOOL.get("nextjs"):
            print(f"\n  Next.js")
            add_concepts(c, t, [
                (B,"File-Based Routing","pages/ vs app/, dynamic routes, Link."),
                (B,"Data Fetching","getStaticProps, getServerSideProps, revalidate."),
                (B,"Image & Font Optimisation","next/image, WebP, next/font."),
                (M,"App Router & RSC","Server Components, 'use client', layouts, streaming."),
                (M,"Route Handlers","app/api/route.ts, middleware, edge runtime."),
                (M,"NextAuth.js","Providers, JWT vs DB session, protected pages."),
                (H,"Core Web Vitals","LCP/CLS/FID, bundle analysis, ISR."),
                (H,"Middleware","next/middleware, matcher, request rewriting."),
                (H,"Deployment & Caching","Vercel CDN, cache-control, on-demand revalidation."),
            ])

        if t := TOOL.get("svelte"):
            print(f"\n  Svelte")
            add_concepts(c, t, [
                (B,"Reactivity","$: reactive declarations, reactive statements, stores."),
                (B,"Components & Props","export let, component events, slots."),
                (B,"Template Syntax","{#if}, {#each}, {#await}, event directives."),
                (M,"Stores","writable/readable/derived, auto-subscription with $."),
                (M,"Lifecycle","onMount, onDestroy, beforeUpdate, afterUpdate."),
                (M,"SvelteKit Routing","File-based routing, load functions, form actions."),
                (H,"Animations","built-in transitions, tweened/spring stores."),
                (H,"SvelteKit SSR & SSG","server-side load, adapter selection, prerendering."),
                (H,"Performance","Compile-time reactivity, no virtual DOM, bundle size."),
            ])

        if t := TOOL.get("typescript"):
            print(f"\n  TypeScript")
            add_concepts(c, t, [
                (B,"Type Annotations","Primitives, arrays, tuples, inference, no-any."),
                (B,"Interfaces & Types","interface vs type, optional, readonly, intersection."),
                (B,"Functions & Generics Basics","Param/return types, optional params, basic <T>."),
                (M,"Advanced Types","Union, discriminated union, type guards, never."),
                (M,"Generics & Constraints","extends, keyof, conditional types."),
                (M,"Utility Types","Partial, Pick, Omit, Record, ReturnType."),
                (H,"Declaration Files","Writing .d.ts, module augmentation, DefinitelyTyped."),
                (H,"Decorators","Class/method decorators, reflect-metadata."),
                (H,"tsconfig Deep Dive","strict flags, paths, project references, perf."),
            ])

        if t := TOOL.get("tailwind"):
            print(f"\n  Tailwind CSS")
            add_concepts(c, t, [
                (B,"Utility-First Basics","Spacing, typography, colours, flex, grid."),
                (B,"Responsive Design","sm/md/lg breakpoints, mobile-first."),
                (B,"State Variants","hover:/focus:/disabled:/dark: variants."),
                (M,"@apply & Component Extraction","When to extract vs keep utilities."),
                (M,"Theming","tailwind.config.js, extend, arbitrary values []."),
                (M,"Dark Mode","class vs media strategy, designing for both."),
                (H,"Custom Plugins","addUtilities, addComponents, matchUtilities."),
                (H,"Production Build","content array, safelisting, JIT mechanics."),
                (H,"Design System","Tokens, CSS variables, component library scaling."),
            ])

        if t := TOOL.get("vite"):
            print(f"\n  Vite")
            add_concepts(c, t, [
                (B,"Dev Server","Native ESM, instant HMR, no bundling in dev."),
                (B,"Project Setup","vite.config.ts, plugins, framework templates."),
                (B,"Static Assets","Asset handling, public dir, import.meta.url."),
                (M,"Plugins","Plugin API, Rollup plugin compat, env variables."),
                (M,"Build Optimisation","Code splitting, tree shaking, chunk strategy."),
                (M,"Library Mode","Building reusable libraries, entry points, externals."),
                (H,"SSR","Vite SSR API, framework integration, streaming."),
                (H,"Environment Variables","import.meta.env, .env files, mode handling."),
                (H,"Performance Analysis","rollup-plugin-visualizer, build profiling."),
            ])

        if t := TOOL.get("webpack"):
            print(f"\n  Webpack")
            add_concepts(c, t, [
                (B,"Core Concepts","Entry, output, loaders, plugins, mode."),
                (B,"Loaders","babel-loader, css-loader, file-loader, ts-loader."),
                (B,"Plugins","HtmlWebpackPlugin, MiniCssExtractPlugin, DefinePlugin."),
                (M,"Code Splitting","Dynamic import(), SplitChunksPlugin, lazy loading."),
                (M,"Source Maps","devtool options, production vs dev maps."),
                (M,"Dev Server","webpack-dev-server, HMR, proxy config."),
                (H,"Tree Shaking","sideEffects, ES modules, dead code elimination."),
                (H,"Module Federation","Micro-frontend sharing, remote/host config."),
                (H,"Performance Tuning","DLL plugin, persistent cache, parallel builds."),
            ])

        if t := TOOL.get("storybook"):
            print(f"\n  Storybook")
            add_concepts(c, t, [
                (B,"Stories","Component Story Format (CSF), args, argTypes."),
                (B,"Controls & Actions","Args-based controls, action logging."),
                (B,"Addons","Essential addons: docs, controls, viewport, a11y."),
                (M,"Decorators","Global and story-level decorators, providers."),
                (M,"Documentation","MDX docs, autodocs, JSDoc integration."),
                (M,"Testing Integration","Interaction testing with play(), Storyshots."),
                (H,"Design Tokens","Token integration, Figma plugin, design-dev sync."),
                (H,"Chromatic & Visual Testing","Snapshot diffing, CI integration, review flow."),
                (H,"Custom Addons","Panel addons, decorator addons, preset system."),
            ])

        # ── AI ────────────────────────────────────────────────────────────────
        if t := TOOL.get("langchain"):
            print(f"\n  LangChain")
            add_concepts(c, t, [
                (B,"LLM & ChatModel Basics","ChatOpenAI, invoke, temperature, streaming."),
                (B,"Prompt Templates","PromptTemplate, ChatPromptTemplate, variable interpolation."),
                (B,"Output Parsers","StrOutputParser, JSON, Pydantic, parse errors."),
                (M,"Chains & LCEL","Pipe operator, RunnableSequence/Parallel, batching."),
                (M,"RAG","Loaders, splitters, embeddings, vector stores, RetrievalQA."),
                (M,"Memory","ConversationBufferMemory, history persistence, trimming."),
                (H,"Agents & Tools","AgentExecutor, @tool, ReAct loop, tool errors."),
                (H,"LangGraph","StateGraph, conditional routing, human-in-the-loop."),
                (H,"Custom Runnables","Runnable interface, callbacks, LangSmith eval."),
            ])

        if t := TOOL.get("llamaindex"):
            print(f"\n  LlamaIndex")
            add_concepts(c, t, [
                (B,"Document Loading","SimpleDirectoryReader, loaders, node parsing."),
                (B,"Index Types","VectorStoreIndex, SummaryIndex, KeywordTable."),
                (B,"Basic Query","as_query_engine(), query(), response synthesis."),
                (M,"Retrieval Modes","Top-k, MMR, hybrid retrieval, node postprocessors."),
                (M,"Chat Engine","CondensePlusContextChatEngine, memory, streaming."),
                (M,"Embeddings","OpenAIEmbedding, HuggingFaceEmbedding, custom."),
                (H,"Agentic RAG","ReActAgent, FunctionCallingAgent, tool selection."),
                (H,"Workflows","Event-driven workflows, step decorators, branching."),
                (H,"Observability","LlamaTrace, callbacks, token counting, cost tracking."),
            ])

        if t := TOOL.get("crewai"):
            print(f"\n  CrewAI")
            add_concepts(c, t, [
                (B,"Agents & Roles","Agent, role/goal/backstory, LLM assignment."),
                (B,"Tasks","Task, description, expected_output, agent assignment."),
                (B,"Crew Execution","Crew, sequential vs hierarchical process, kickoff."),
                (M,"Tools","BaseTool, @tool decorator, built-in search/file tools."),
                (M,"Memory","Short-term, long-term, entity memory, shared context."),
                (M,"Delegation","allow_delegation, agent-to-agent task passing."),
                (H,"Custom LLMs","Ollama, Azure, Anthropic integration with CrewAI."),
                (H,"Flows","Event-driven Flow API, @start/@listen/@router."),
                (H,"Testing & Evaluation","pytest integration, crew output validation."),
            ])

        if t := TOOL.get("openai-api"):
            print(f"\n  OpenAI API")
            add_concepts(c, t, [
                (B,"Chat Completions","messages array, temperature, max_tokens, roles."),
                (B,"Prompt Engineering","Few-shot, chain-of-thought, system instructions."),
                (B,"Models & Pricing","GPT-4o vs GPT-4 vs GPT-3.5, context, tiktoken."),
                (M,"Function / Tool Calling","tools param, tool_choice, parsing tool_calls."),
                (M,"Embeddings API","text-embedding-3, cosine similarity, RAG layer."),
                (M,"Streaming","stream=True, SSE chunks, streaming UI patterns."),
                (H,"Assistants API","Thread/Run/Message, file search, code interpreter."),
                (H,"Batch API","JSONL format, async processing, cost optimisation."),
                (H,"Fine-Tuning","Training format, upload/monitor jobs, evaluation."),
            ])

        if t := TOOL.get("pytorch"):
            print(f"\n  PyTorch")
            add_concepts(c, t, [
                (B,"Tensors","Creation, shapes, device, math ops, NumPy bridge."),
                (B,"Autograd","requires_grad, backward(), grad, no_grad."),
                (B,"nn.Module","Linear/ReLU, forward(), parameter iteration."),
                (M,"Training Loop","DataLoader/Dataset, zero_grad/backward/step."),
                (M,"CNNs & RNNs","Conv2d, MaxPool, LSTM, input/output shapes."),
                (M,"Transfer Learning","torchvision models, freezing, fine-tuning."),
                (H,"Custom Datasets","__len__/__getitem__, transforms, augmentation."),
                (H,"Distributed Training","DDP, torch.distributed, gradient sync."),
                (H,"Deployment","TorchScript, ONNX export, TorchServe, quantisation."),
            ])

        if t := TOOL.get("tensorflow"):
            print(f"\n  TensorFlow")
            add_concepts(c, t, [
                (B,"Keras Sequential","Dense/Conv2D/LSTM, compile, fit, predict."),
                (B,"tf.data","Dataset pipeline, batch, shuffle, prefetch."),
                (B,"Loss & Optimizers","CrossEntropy, MSE, Adam, SGD, metrics."),
                (M,"Functional API","Multi-input/output, shared layers, branching."),
                (M,"Custom Layers","Subclass Layer/Model, call(), build()."),
                (M,"Callbacks","ModelCheckpoint, EarlyStopping, TensorBoard."),
                (H,"TF Serving","SavedModel, serving signatures, REST vs gRPC."),
                (H,"@tf.function","AutoGraph, tracing, concrete functions."),
                (H,"Distributed Strategy","MirroredStrategy, MultiWorker, TPU."),
            ])

        if t := TOOL.get("sklearn"):
            print(f"\n  Scikit-learn")
            add_concepts(c, t, [
                (B,"Estimator API","fit/predict/transform, train_test_split."),
                (B,"Core Algorithms","LinearRegression, LogisticRegression, DecisionTree, KNN."),
                (B,"Preprocessing","StandardScaler, OneHotEncoder, SimpleImputer."),
                (M,"Pipelines","Pipeline, ColumnTransformer, avoiding leakage."),
                (M,"Evaluation","cross_val_score, confusion matrix, F1, ROC-AUC."),
                (M,"Hyperparameter Tuning","GridSearchCV, RandomizedSearchCV."),
                (H,"Ensembles","RandomForest, GradientBoosting, stacking."),
                (H,"Custom Estimators","BaseEstimator, TransformerMixin."),
                (H,"Feature Selection","SelectKBest, RFE, PCA, SHAP values."),
            ])

        if t := TOOL.get("hf-transformers"):
            print(f"\n  Hugging Face")
            add_concepts(c, t, [
                (B,"Pipeline API","Zero-code NLP/vision inference."),
                (B,"Tokenizers","AutoTokenizer, padding, truncation, attention masks."),
                (B,"Model Loading","AutoModel, from_pretrained(), task variants."),
                (M,"Trainer API","TrainingArguments, compute_metrics, Hub push."),
                (M,"Datasets Library","load_dataset, map, batched, Arrow format."),
                (M,"Evaluation","BLEU/ROUGE/accuracy, benchmark datasets."),
                (H,"Custom Training Loops","Gradient accumulation, mixed precision."),
                (H,"PEFT / LoRA","QLoRA, adapter layers, reducing trainable params."),
                (H,"Deployment","ONNX, BetterTransformer, Inference Endpoints, TGI."),
            ])

        if t := TOOL.get("vector-db"):
            print(f"\n  Vector Databases")
            add_concepts(c, t, [
                (B,"Embedding Concepts","Semantic similarity, cosine vs dot vs euclidean."),
                (B,"Basic CRUD","Upsert, top-k query, delete, metadata."),
                (B,"Metadata Filtering","Combining semantic search with structured filters."),
                (M,"Chunking Strategies","Fixed, sentence, recursive, semantic chunking."),
                (M,"RAG Pipeline","Indexing, query, hybrid search, reranking."),
                (M,"Multi-tenancy","Namespace/collection per tenant, isolation."),
                (H,"Hybrid Search","BM25+dense, RRF, sparse-dense indexes."),
                (H,"Production","Shard/replica count, ANN vs exact, latency-recall."),
                (H,"Evaluation","RAGAS, precision/recall@k, embedding drift."),
            ])

        if t := TOOL.get("mlflow"):
            print(f"\n  MLflow")
            add_concepts(c, t, [
                (B,"Tracking","mlflow.log_param/metric/artifact, runs, experiments."),
                (B,"Projects","MLproject file, conda/docker env, mlflow run."),
                (B,"Models","mlflow.sklearn.log_model, flavors, model signature."),
                (M,"Model Registry","Staging/Production/Archived, version management."),
                (M,"Autologging","mlflow.autolog(), framework-specific logging."),
                (M,"Model Serving","mlflow models serve, REST API, CLI prediction."),
                (H,"Custom Plugins","Custom tracking stores, artifact repos."),
                (H,"Recipes","MLflow Recipes for regression/classification pipelines."),
                (H,"Integration","Spark, Databricks, SageMaker deployment."),
            ])

        if t := TOOL.get("wandb"):
            print(f"\n  Weights & Biases")
            add_concepts(c, t, [
                (B,"Experiment Tracking","wandb.init, log(), config, run naming."),
                (B,"Visualisation","Loss curves, confusion matrix, custom charts."),
                (B,"Artifacts","Dataset/model versioning, lineage tracking."),
                (M,"Sweeps","Hyperparameter search, bayes/grid/random strategies."),
                (M,"Tables & Media","Log images, audio, text, and comparison tables."),
                (M,"Integration","PyTorch, HuggingFace, Keras autolog."),
                (H,"Reports","Collaborative analysis reports, panel layouts."),
                (H,"Launch","Remote job execution, queue management."),
                (H,"Model Registry","Production model management, aliasing."),
            ])

        # ── DEVOPS ────────────────────────────────────────────────────────────
        if t := TOOL.get("docker"):
            print(f"\n  Docker")
            add_concepts(c, t, [
                (B,"Images & Containers","build/run/stop/rm, image vs container, port mapping."),
                (B,"Dockerfile","FROM/RUN/COPY/EXPOSE/CMD vs ENTRYPOINT, layer caching."),
                (B,"Registries","pull/push, tagging, Docker Hub, private registries."),
                (M,"Docker Compose","Multi-container, depends_on+healthcheck, volumes."),
                (M,"Volumes & Networking","Named volumes, bind mounts, bridge/overlay networks."),
                (M,"Environment Variables","env_file, --env-flag, secrets management."),
                (H,"Multi-Stage Builds","Builder pattern, image size, distroless."),
                (H,"Security","Non-root user, read-only FS, image scanning."),
                (H,"Docker Swarm","Service replication, rolling updates, stack deploy."),
            ])

        if t := TOOL.get("kubernetes"):
            print(f"\n  Kubernetes")
            add_concepts(c, t, [
                (B,"Core Objects","Pod, Deployment, Service, Namespace, kubectl basics."),
                (B,"Services","ClusterIP/NodePort/LoadBalancer, cluster DNS."),
                (B,"kubectl","apply/get/describe/logs/exec, port-forward."),
                (M,"ConfigMaps & Secrets","Env var vs volume mount, secret rotation."),
                (M,"Health Probes","liveness/readiness/startup, HTTP/TCP/command."),
                (M,"Resource Limits","requests vs limits, QoS classes, OOMKilled."),
                (H,"StatefulSets","PVC, StorageClass, headless services, ordering."),
                (H,"RBAC","Roles, ClusterRoles, RoleBindings, ServiceAccounts."),
                (H,"Network Policies","Ingress/Egress rules, namespace isolation."),
            ])

        if t := TOOL.get("helm"):
            print(f"\n  Helm")
            add_concepts(c, t, [
                (B,"Chart Structure","Chart.yaml, values.yaml, templates/, helpers.tpl."),
                (B,"Install & Upgrade","helm install/upgrade/rollback/uninstall."),
                (B,"Template Basics","{{ .Values }}, if/range/with, include."),
                (M,"values.yaml Overrides","--set, -f, environment-specific values files."),
                (M,"Dependencies","Chart.yaml dependencies, helm dep update."),
                (M,"Hooks","pre-install, post-upgrade, job hooks, hook weights."),
                (H,"Library Charts","Reusable partial templates across charts."),
                (H,"OCI Registries","Helm charts in OCI, push/pull, authentication."),
                (H,"Testing","helm test, test pods, golden file testing."),
            ])

        if t := TOOL.get("argocd"):
            print(f"\n  ArgoCD")
            add_concepts(c, t, [
                (B,"GitOps Principles","Git as source of truth, desired vs live state."),
                (B,"Application CRD","App definition, source, destination, sync policy."),
                (B,"Sync & Diff","Manual vs auto sync, diff view, self-heal."),
                (M,"App of Apps","Managing multiple apps, ApplicationSet."),
                (M,"Sync Waves & Hooks","Resource ordering, PreSync/PostSync hooks."),
                (M,"RBAC & Projects","AppProject, allowed repos/clusters, permissions."),
                (H,"ApplicationSet","Matrix/cluster/git generators, templating."),
                (H,"Image Updater","Automated image tag updates, write-back."),
                (H,"Multi-cluster","Hub-spoke topology, cluster secrets, federation."),
            ])

        if t := TOOL.get("gh-actions"):
            print(f"\n  GitHub Actions")
            add_concepts(c, t, [
                (B,"Workflow Basics","on: triggers, jobs, steps, runs-on."),
                (B,"Actions","uses:, with: inputs, checkout, setup-python."),
                (B,"Secrets & Vars","secrets.NAME, env:, vars context, GITHUB_TOKEN."),
                (M,"Matrix Strategy","matrix, fail-fast, include/exclude."),
                (M,"Caching","actions/cache, cache-dependency-path, hit/miss."),
                (M,"Environments","Protection rules, required reviewers, deploy status."),
                (H,"Reusable Workflows","workflow_call, inputs/outputs, composite."),
                (H,"Custom Actions","action.yml, Docker vs JS actions."),
                (H,"Self-Hosted & OIDC","Ephemeral runners, OIDC cloud auth, fork safety."),
            ])

        if t := TOOL.get("gitlab-ci"):
            print(f"\n  GitLab CI")
            add_concepts(c, t, [
                (B,".gitlab-ci.yml","stages, jobs, script, artifacts, cache."),
                (B,"Runners","Shared vs specific runners, tags, executor types."),
                (B,"Variables","Predefined vars, custom CI/CD vars, secrets masking."),
                (M,"Includes & Extends","!reference, extends, include: remote/template."),
                (M,"Environments & Deployments","environment:, review apps, stop action."),
                (M,"Rules & Workflow","rules: if/changes/exists, workflow: rules."),
                (H,"DAG Pipelines","needs:, parallel/sequential, cross-project deps."),
                (H,"Auto DevOps","Built-in pipeline templates, customisation."),
                (H,"Security Scanning","SAST, DAST, dependency scan, secret detection."),
            ])

        if t := TOOL.get("jenkins"):
            print(f"\n  Jenkins")
            add_concepts(c, t, [
                (B,"Freestyle Jobs","Build triggers, build steps, post-build actions."),
                (B,"Declarative Pipeline","pipeline{}, stages{}, steps{}, agent."),
                (B,"Plugins","Blue Ocean, Git, Docker, Credentials, Slack plugins."),
                (M,"Scripted Pipeline","Groovy DSL, node{}, stage{}, try/catch."),
                (M,"Shared Libraries","@Library, vars/, src/, resource files."),
                (M,"Credentials","Credential binding, withCredentials, secret text."),
                (H,"Distributed Builds","Master/agents, cloud plugins, dynamic agents."),
                (H,"Pipeline Best Practices","Restart from stage, parallel stages, throttle."),
                (H,"Security","Matrix auth, role strategy, script approval."),
            ])

        if t := TOOL.get("terraform"):
            print(f"\n  Terraform")
            add_concepts(c, t, [
                (B,"HCL & Providers","provider/resource/data, init/plan/apply/destroy."),
                (B,"Variables & Outputs","variable, tfvars, output, sensitive."),
                (B,"Resource Lifecycle","depends_on, create_before_destroy, ignore_changes."),
                (M,"Modules","Structure, calling, versioning, registry."),
                (M,"State Management","Remote state S3+DynamoDB, locking, import."),
                (M,"Workspaces","Per-env vars, limitations vs separate backends."),
                (H,"Terragrunt","DRY patterns, dependency blocks."),
                (H,"Terratest","Go-based infra tests, apply-validate-destroy."),
                (H,"Policy & Drift","Sentinel/OPA, plan in CI, Infracost."),
            ])

        if t := TOOL.get("ansible"):
            print(f"\n  Ansible")
            add_concepts(c, t, [
                (B,"Inventory & Playbooks","hosts, groups, plays, tasks, YAML structure."),
                (B,"Core Modules","copy, template, apt/yum, service, file, command."),
                (B,"Variables & Facts","vars, group_vars, host_vars, gather_facts."),
                (M,"Roles","Role structure, defaults, tasks, handlers, meta."),
                (M,"Templates & Jinja2","Jinja2 in templates, filters, conditionals."),
                (M,"Handlers","notify, listen, handler order, flush_handlers."),
                (H,"Vault","ansible-vault encrypt/decrypt, inline secrets."),
                (H,"Dynamic Inventory","Cloud plugins (AWS/GCP), custom scripts."),
                (H,"Molecule Testing","Role testing, Docker driver, idempotency tests."),
            ])

        if t := TOOL.get("linux-bash"):
            print(f"\n  Linux & Bash")
            add_concepts(c, t, [
                (B,"File System","ls/cd/pwd/mkdir/rm/cp/mv/find, permissions."),
                (B,"Text Processing","grep/sed/awk/cut/sort/uniq, pipes, redirection."),
                (B,"Package Management","apt/yum/dnf, snap, pip, brew."),
                (M,"Shell Scripting","Variables, if/for/while, functions, exit codes."),
                (M,"Process Management","ps/top/kill/pkill, signals, nohup, jobs."),
                (M,"Networking","curl/wget/ss/netstat, SSH, iptables basics."),
                (H,"Systemd","Unit files, systemctl, journald, timers."),
                (H,"Performance Analysis","vmstat/iostat/strace/perf, bottleneck diagnosis."),
                (H,"Advanced Scripting","Arrays, traps, heredocs, strict mode."),
            ])

        if t := TOOL.get("nginx"):
            print(f"\n  Nginx")
            add_concepts(c, t, [
                (B,"Server Blocks","listen, server_name, root, index, location."),
                (B,"Static Serving","try_files, MIME types, access/error logs."),
                (B,"Reverse Proxy","proxy_pass, upstream, proxy_set_header."),
                (M,"SSL/TLS","Certbot, ssl_certificate, HSTS, HTTP→HTTPS."),
                (M,"Load Balancing","Round-robin, least_conn, ip_hash, health checks."),
                (M,"Caching & Gzip","proxy_cache, gzip, cache bypass rules."),
                (H,"Rate Limiting","limit_req_zone, burst, connection limiting."),
                (H,"Security Headers","CSP, X-Frame-Options, HSTS, hide server version."),
                (H,"Performance Tuning","worker_processes, sendfile, tcp_nopush, buffers."),
            ])

        if t := TOOL.get("prom-grafana"):
            print(f"\n  Prometheus & Grafana")
            add_concepts(c, t, [
                (B,"Metric Types","Counter, Gauge, Histogram, Summary."),
                (B,"PromQL Basics","rate(), sum() by, avg(), range vectors."),
                (B,"Grafana Dashboards","Data sources, panels, time ranges."),
                (M,"Instrumentation","prometheus_client, custom metrics, /metrics."),
                (M,"Alertmanager","Alert rules, routing, receivers, inhibition."),
                (M,"Service Discovery","Kubernetes SD, relabelling, scrape_configs."),
                (H,"Recording Rules","Pre-computed queries, cardinality management."),
                (H,"HA with Thanos/Mimir","Remote write, long-term storage, federation."),
                (H,"Loki","LogQL, Promtail, log-metric correlation."),
            ])

        # ── DATA ──────────────────────────────────────────────────────────────
        if t := TOOL.get("kafka"):
            print(f"\n  Apache Kafka")
            add_concepts(c, t, [
                (B,"Core Concepts","Topics, partitions, offsets, producers, consumers."),
                (B,"Produce & Consume","KafkaProducer/Consumer API, serialisation, poll."),
                (B,"Consumer Groups","Partition assignment, offset commit, rebalancing."),
                (M,"Topic Config","Retention, compaction, replication, ISR."),
                (M,"Kafka Streams","KStream/KTable, windowing, state stores."),
                (M,"Schema Registry","Avro, evolution, compatibility modes."),
                (H,"Exactly-Once","Idempotent producers, transactions, EOS."),
                (H,"Kafka Connect","Source/sink connectors, SMTs, distributed mode."),
                (H,"Performance Tuning","linger.ms, batch.size, fetch tuning."),
            ])

        if t := TOOL.get("airflow"):
            print(f"\n  Apache Airflow")
            add_concepts(c, t, [
                (B,"DAG Basics","schedule_interval, default_args, catchup."),
                (B,"Operators","PythonOperator, BashOperator, task dependencies."),
                (B,"Airflow UI","DAG view, trigger, logs, task states."),
                (M,"XComs","xcom_push/pull, size limits, anti-patterns."),
                (M,"Connections & Hooks","BaseHook, Variables, Jinja templating."),
                (M,"TaskFlow API","@task, @dag, TaskGroup, auto XCom."),
                (H,"Dynamic Mapping",".expand(), dag factories."),
                (H,"Custom Operators","BaseOperator, execute(), BaseHook."),
                (H,"Deployment","CeleryExecutor vs KubernetesExecutor, HA."),
            ])

        if t := TOOL.get("dbt"):
            print(f"\n  dbt")
            add_concepts(c, t, [
                (B,"Project Structure","models/seeds/tests/macros, dbt_project.yml."),
                (B,"Writing Models","ref()/source(), materialisation types."),
                (B,"dbt run & test","dbt run/test/docs, schema tests, freshness."),
                (M,"Incremental Models","is_incremental(), unique_key, strategies."),
                (M,"Testing & Docs","Custom tests, YAML descriptions."),
                (M,"Macros & Jinja","Macros, run_query(), dbt_utils."),
                (H,"Snapshots","SCD Type 2, timestamp/check strategy."),
                (H,"dbt Mesh","Cross-project ref, groups, contracts."),
                (H,"CI/CD","state:modified+, deferred execution, slim CI."),
            ])

        if t := TOOL.get("spark"):
            print(f"\n  Apache Spark")
            add_concepts(c, t, [
                (B,"RDD Basics","parallelize, map/filter/reduce, actions vs transforms."),
                (B,"DataFrames","SparkSession, read/write, select/filter, SQL."),
                (B,"Architecture","Driver, executors, DAG, stages, shuffle."),
                (M,"Transformations","groupBy, join, window, UDFs, broadcast."),
                (M,"Structured Streaming","readStream, watermarks, output modes."),
                (M,"MLlib","Pipeline API, VectorAssembler, cross-validation."),
                (H,"Performance Tuning","AQE, skew, Spark UI, partitioning."),
                (H,"Delta Lake","ACID, time travel, MERGE, Z-ordering."),
                (H,"Cluster Config","Dynamic allocation, Kubernetes, YARN tuning."),
            ])

        if t := TOOL.get("elasticsearch"):
            print(f"\n  Elasticsearch")
            add_concepts(c, t, [
                (B,"Core Concepts","Index, document, shards, replicas, mapping."),
                (B,"CRUD & Search","Index/Get/Search API, match/term/range queries."),
                (B,"Query DSL","bool (must/should/filter), relevance scoring."),
                (M,"Mappings & Analyzers","text vs keyword, custom analyzers."),
                (M,"Aggregations","Terms, date_histogram, bucket vs metric."),
                (M,"Index Management","ILM, rollover, aliases, reindex."),
                (H,"Performance","Shard sizing, bulk indexing, circuit breakers."),
                (H,"Cluster Architecture","Node roles, cross-cluster, split brain."),
                (H,"ELK Stack","Logstash, Filebeat, Kibana, TLS+auth."),
            ])

        if t := TOOL.get("snowflake"):
            print(f"\n  Snowflake")
            add_concepts(c, t, [
                (B,"Architecture","Warehouses, cloud services layer, storage separation."),
                (B,"DDL & DML","CREATE TABLE, clone, time travel, MERGE."),
                (B,"Data Loading","COPY INTO, stages, file formats, Snowpipe."),
                (M,"Warehouse Sizing","T-shirt sizes, auto-suspend/resume, multi-cluster."),
                (M,"Semi-Structured Data","VARIANT, PARSE_JSON, flatten, Parquet."),
                (M,"Streams & Tasks","CDC streams, task CRON scheduling."),
                (H,"Snowpark","Python/Java DataFrame API, UDFs, stored procs."),
                (H,"Performance","Clustering keys, pruning, result cache, query profile."),
                (H,"Data Sharing","Secure sharing, marketplace, reader accounts."),
            ])

        if t := TOOL.get("pandas"):
            print(f"\n  Pandas")
            add_concepts(c, t, [
                (B,"DataFrame Basics","read_csv, loc/iloc, shape, dtypes, head/info."),
                (B,"Filtering & Sorting","Boolean indexing, query(), sort_values()."),
                (B,"Missing Data","isna, dropna, fillna, interpolate."),
                (M,"GroupBy","groupby, agg, transform, apply, pivot_table."),
                (M,"Merge & Join","pd.merge, pd.concat, join types."),
                (M,"Time Series","DatetimeIndex, resample, rolling, dt accessor."),
                (H,"Performance","Vectorisation, category dtype, chunking, eval."),
                (H,"Custom Apply","vectorized vs apply, np.vectorize."),
                (H,"Pandas 2.0","Copy-on-Write, ArrowDtype, nullable dtypes."),
            ])

        if t := TOOL.get("clickhouse"):
            print(f"\n  ClickHouse")
            add_concepts(c, t, [
                (B,"Columnar Storage","Column-oriented storage, compression, OLAP use cases."),
                (B,"Table Engines","MergeTree family, ReplacingMergeTree, AggregatingMergeTree."),
                (B,"Basic Queries","SELECT, WHERE, GROUP BY, ORDER BY, LIMIT."),
                (M,"MergeTree Keys","ORDER BY (sorting key), PARTITION BY, primary key."),
                (M,"Materialized Views","Auto-updating aggregations, chained views."),
                (M,"Data Ingestion","INSERT INTO, file table functions, Kafka engine."),
                (H,"Distributed Tables","Sharding, replication, distributed engine."),
                (H,"Query Optimisation","Projections, query profiling, skipping indexes."),
                (H,"ClickHouse Cloud","Cloud managed, serverless, tiered storage."),
            ])

        if t := TOOL.get("flink"):
            print(f"\n  Apache Flink")
            add_concepts(c, t, [
                (B,"Stream vs Batch","Unified runtime, DataStream vs Table API."),
                (B,"DataStream API","map/filter/keyBy/reduce, source/sink connectors."),
                (B,"Event Time & Watermarks","Processing vs event time, out-of-order handling."),
                (M,"Stateful Processing","Keyed state, operator state, state backends."),
                (M,"Windows","Tumbling, sliding, session windows, triggers."),
                (M,"Table API & SQL","Unified SQL, streaming tables, temporal joins."),
                (H,"Checkpointing","Exactly-once, savepoints, incremental checkpoints."),
                (H,"Flink on Kubernetes","Kubernetes operator, session vs application mode."),
                (H,"Connectors","Kafka, JDBC, Iceberg, Hudi — source/sink config."),
            ])

        if t := TOOL.get("bigquery"):
            print(f"\n  BigQuery")
            add_concepts(c, t, [
                (B,"Datasets & Tables","Project/dataset/table hierarchy, schema definition."),
                (B,"Standard SQL","SELECT, JOINS, subqueries, DML statements."),
                (B,"Data Loading","bq load, GCS import, streaming inserts, Transfer Service."),
                (M,"Partitioning & Clustering","Date/integer partitioning, clustering columns."),
                (M,"Views & Materialised Views","Logical vs materialised views, cache."),
                (M,"Cost Control","On-demand vs capacity pricing, dry run, slot reservations."),
                (H,"BigQuery ML","CREATE MODEL, ML.PREDICT, built-in algorithms."),
                (H,"Analytics Hub","Data sharing, listings, subscriber access."),
                (H,"Authorised Views & Row Security","Column/row-level security, IAM policies."),
            ])

        if t := TOOL.get("databricks"):
            print(f"\n  Databricks")
            add_concepts(c, t, [
                (B,"Workspace & Clusters","Notebooks, cluster config, autoscaling, runtime."),
                (B,"Delta Lake Basics","ACID transactions, DeltaTable CRUD, time travel."),
                (B,"Notebooks & Magic Commands","%python/%sql/%scala, dbutils, display()."),
                (M,"Jobs & Workflows","Multi-task jobs, task dependencies, triggers."),
                (M,"Databricks SQL","SQL warehouses, dashboards, alerts."),
                (M,"Unity Catalog","3-level namespace, data lineage, access control."),
                (H,"MLflow Integration","Tracking, model registry, deployment."),
                (H,"Structured Streaming","Auto Loader, Delta as sink, exactly-once."),
                (H,"Performance Tuning","Photon engine, Z-ordering, liquid clustering."),
            ])

        # ── MOBILE ────────────────────────────────────────────────────────────
        if t := TOOL.get("react-native"):
            print(f"\n  React Native")
            add_concepts(c, t, [
                (B,"Core Components","View, Text, Image, ScrollView, FlatList."),
                (B,"Styling","StyleSheet.create(), flexbox, platform-specific styles."),
                (B,"Navigation","React Navigation, Stack/Tab/Drawer navigators."),
                (M,"State Management","useState, Context, Zustand/Redux in RN."),
                (M,"Native Modules","Linking, permissions, platform APIs."),
                (M,"Networking","fetch/axios, loading states, error handling."),
                (H,"Performance","FlatList optimisation, Hermes engine, memo."),
                (H,"Animations","Animated API, Reanimated 2, Gesture Handler."),
                (H,"Publishing","EAS Build, App Store/Play Store submission."),
            ])

        if t := TOOL.get("flutter"):
            print(f"\n  Flutter")
            add_concepts(c, t, [
                (B,"Widgets","StatelessWidget, StatefulWidget, widget tree."),
                (B,"Layouts","Column/Row/Stack, Expanded, Padding, Container."),
                (B,"Dart Basics","null safety, async/await, Future/Stream."),
                (M,"State Management","setState, Provider, Riverpod, BLoC."),
                (M,"Navigation","Navigator 2.0, GoRouter, named routes."),
                (M,"HTTP & APIs","http/dio packages, JSON serialisation, error handling."),
                (H,"Custom Painting","CustomPainter, Canvas API, animations."),
                (H,"Platform Channels","MethodChannel for native iOS/Android code."),
                (H,"Flutter Web & Desktop","Multi-platform targets, responsive layouts."),
            ])

        if t := TOOL.get("swiftui"):
            print(f"\n  SwiftUI")
            add_concepts(c, t, [
                (B,"Views & Modifiers","View protocol, modifier chaining, ViewBuilder."),
                (B,"State & Binding","@State, @Binding, @ObservedObject, @StateObject."),
                (B,"Layout","VStack/HStack/ZStack, Spacer, Padding, GeometryReader."),
                (M,"Navigation","NavigationStack, NavigationLink, .navigationTitle."),
                (M,"Data Flow","@EnvironmentObject, ObservableObject, Combine."),
                (M,"Lists & Data","List, ForEach, Identifiable, onDelete."),
                (H,"Custom Animations","withAnimation, matchedGeometryEffect, transitions."),
                (H,"Combine Framework","Publisher/Subscriber, operators, sink."),
                (H,"App Architecture","MVVM, Clean Architecture in Swift, async/await."),
            ])

        if t := TOOL.get("android-kotlin"):
            print(f"\n  Android / Kotlin")
            add_concepts(c, t, [
                (B,"Activity & Lifecycle","onCreate/onResume/onDestroy, configuration changes."),
                (B,"Jetpack Compose","Composable functions, state, recomposition."),
                (B,"Kotlin Basics","Data classes, extension functions, coroutines basics."),
                (M,"ViewModel & LiveData","MVVM, ViewModel scope, StateFlow."),
                (M,"Room Database","Entity, DAO, Database class, migrations."),
                (M,"Navigation Component","NavGraph, NavController, safe args."),
                (H,"Coroutines & Flow","structured concurrency, Flow operators, channel."),
                (H,"Hilt DI","@HiltAndroidApp, @Inject, modules, testing."),
                (H,"Publishing","App signing, Play Console, staged rollout."),
            ])

        if t := TOOL.get("expo"):
            print(f"\n  Expo")
            add_concepts(c, t, [
                (B,"Managed Workflow","expo-cli, Expo Go, Metro bundler, app.json."),
                (B,"Expo SDK","expo-camera, expo-location, expo-notifications APIs."),
                (B,"File Structure","app/, assets/, app.json, babel.config."),
                (M,"EAS Build","eas.json, build profiles, cloud builds, credentials."),
                (M,"OTA Updates","expo-updates, update channels, rollback."),
                (M,"Bare Workflow","Ejecting, native code access, RN compatibility."),
                (H,"Custom Dev Client","Custom native modules with Expo."),
                (H,"EAS Submit","Automated App Store / Play Store submission."),
                (H,"Performance","Tree shaking, selective imports, bundle size."),
            ])

        # ── SECURITY ──────────────────────────────────────────────────────────
        if t := TOOL.get("owasp"):
            print(f"\n  OWASP Top 10")
            add_concepts(c, t, [
                (B,"Injection Attacks","SQL, command, LDAP injection — parameterised queries."),
                (B,"Broken Authentication","Weak passwords, missing MFA, session fixation."),
                (B,"Sensitive Data Exposure","Encryption at rest/transit, TLS, data masking."),
                (M,"Broken Access Control","IDOR, privilege escalation, missing auth checks."),
                (M,"Security Misconfiguration","Default creds, open S3 buckets, verbose errors."),
                (M,"XSS","Reflected, stored, DOM-based XSS — output encoding, CSP."),
                (H,"Insecure Deserialisation","Object injection, gadget chains, safe alternatives."),
                (H,"Using Vulnerable Components","SCA tools, CVE monitoring, dependency updates."),
                (H,"Logging & Monitoring Failures","SIEM integration, alerting on anomalies."),
            ])

        if t := TOOL.get("oauth-jwt"):
            print(f"\n  OAuth2 & JWT")
            add_concepts(c, t, [
                (B,"OAuth2 Flows","Authorization Code, Client Credentials, Implicit (deprecated)."),
                (B,"JWT Structure","Header.Payload.Signature, claims (sub/iss/exp/iat)."),
                (B,"Token Validation","Signature verification, expiry check, issuer validation."),
                (M,"PKCE","Proof Key for Code Exchange — mobile/SPA security."),
                (M,"Refresh Tokens","Rotation, family detection, revocation strategies."),
                (M,"Scopes & Claims","Defining scopes, custom claims, token introspection."),
                (H,"OIDC","ID token, UserInfo endpoint, discovery document."),
                (H,"Token Storage","Memory vs localStorage vs httpOnly cookie trade-offs."),
                (H,"Mutual TLS","mTLS client auth, certificate binding, security."),
            ])

        if t := TOOL.get("web-security"):
            print(f"\n  Web App Security")
            add_concepts(c, t, [
                (B,"XSS Prevention","Output encoding, DOMPurify, Content-Security-Policy."),
                (B,"CSRF Protection","SameSite cookies, CSRF tokens, double-submit."),
                (B,"SQL Injection","Parameterised queries, ORMs, input sanitisation."),
                (M,"SSRF","URL validation, allowlist, metadata endpoint risk."),
                (M,"Security Headers","HSTS, X-Frame-Options, X-Content-Type-Options."),
                (M,"Rate Limiting & Brute Force","Account lockout, CAPTCHA, token bucket."),
                (H,"Business Logic Flaws","Privilege escalation, IDOR, workflow bypass."),
                (H,"API Security","BOLA, mass assignment, excessive data exposure."),
                (H,"Threat Modelling","STRIDE, attack surface, data flow diagrams."),
            ])

        if t := TOOL.get("container-sec"):
            print(f"\n  Container Security")
            add_concepts(c, t, [
                (B,"Image Hardening","Minimal base images, non-root user, no secrets in layers."),
                (B,"Image Scanning","Trivy, Snyk, Grype — CVE detection in CI."),
                (B,"Secrets Management","Vault, Kubernetes Secrets, sealed-secrets, SOPS."),
                (M,"Runtime Security","Falco, seccomp profiles, AppArmor, read-only FS."),
                (M,"K8s Security","PodSecurityAdmission, network policies, RBAC."),
                (M,"Registry Security","Private registry auth, content trust, image signing."),
                (H,"Supply Chain Security","SBOM, Sigstore/Cosign, SLSA provenance."),
                (H,"Zero-Trust Networking","mTLS service mesh, Istio, SPIFFE/SPIRE."),
                (H,"Compliance","CIS Benchmarks, NIST containers, audit logging."),
            ])

        if t := TOOL.get("pentest"):
            print(f"\n  Penetration Testing")
            add_concepts(c, t, [
                (B,"Methodology","PTES/OWASP, reconnaissance, scoping, rules of engagement."),
                (B,"Recon Tools","nmap, Shodan, WHOIS, theHarvester, passive recon."),
                (B,"Vulnerability Scanning","Nessus, OpenVAS, Nikto — scan vs manual testing."),
                (M,"Web App Testing","Burp Suite proxy, intruder, repeater, OWASP WSTG."),
                (M,"Exploitation","Metasploit basics, payload types, post-exploitation."),
                (M,"Privilege Escalation","Linux/Windows PrivEsc, sudo misconfig, SUID."),
                (H,"Active Directory","Kerberoasting, Pass-the-Hash, BloodHound, mimikatz."),
                (H,"Report Writing","CVSS scoring, PoC evidence, remediation guidance."),
                (H,"Red Team Ops","C2 frameworks, lateral movement, persistence, OPSEC."),
            ])

        if t := TOOL.get("cryptography"):
            print(f"\n  Cryptography")
            add_concepts(c, t, [
                (B,"Hashing","SHA-256/512, MD5 (broken), password hashing (bcrypt/argon2)."),
                (B,"Symmetric Encryption","AES-GCM, AES-CBC, key sizes, IV/nonce usage."),
                (B,"Asymmetric Encryption","RSA, EC keypairs, public/private key concepts."),
                (M,"TLS / HTTPS","Handshake, certificates, cipher suites, TLS 1.3."),
                (M,"Digital Signatures","ECDSA, RSA-PSS, non-repudiation, PKI."),
                (M,"Key Derivation","PBKDF2, bcrypt, scrypt, Argon2 parameters."),
                (H,"PKI & Certificates","CA hierarchy, X.509, CSR, certificate pinning."),
                (H,"Secrets Management","Vault, KMS, HSM, envelope encryption."),
                (H,"Post-Quantum Cryptography","Lattice-based, NIST PQC standards, migration."),
            ])

        # ── CLOUD ─────────────────────────────────────────────────────────────
        if t := TOOL.get("gcp"):
            print(f"\n  Google Cloud (GCP)")
            add_concepts(c, t, [
                (B,"Core Services","GCE, GCS, Cloud SQL, IAM, VPC basics."),
                (B,"IAM","Roles (basic/predefined/custom), service accounts, bindings."),
                (B,"Cloud Storage","Buckets, storage classes, lifecycle policies, ACLs."),
                (M,"GKE","Autopilot vs Standard, node pools, Workload Identity."),
                (M,"Cloud Run","Serverless containers, concurrency, scaling to zero."),
                (M,"Pub/Sub","Topics, subscriptions, push vs pull, dead-letter."),
                (H,"BigQuery Advanced","Slots, commitments, BQML, DTS."),
                (H,"Cloud CDN & Load Balancing","GLB, backend services, NEGs, Cloud Armor."),
                (H,"Networking","Shared VPC, VPC peering, Private Google Access, Cloud NAT."),
            ])

        if t := TOOL.get("azure"):
            print(f"\n  Microsoft Azure")
            add_concepts(c, t, [
                (B,"Core Services","VMs, Blob Storage, SQL DB, Azure AD, VNet."),
                (B,"Azure AD & RBAC","Tenants, service principals, managed identities, RBAC."),
                (B,"Blob Storage","Containers, tiers (hot/cool/archive), SAS tokens."),
                (M,"AKS","Managed Kubernetes, VMSS node pools, AAD integration."),
                (M,"Azure Functions","Triggers/bindings, Durable Functions, cold start."),
                (M,"Cosmos DB","Multi-model API, partition key, consistency levels."),
                (H,"Azure DevOps","Pipelines YAML, repos, boards, artifacts."),
                (H,"Service Bus","Queues, topics/subscriptions, dead-letter."),
                (H,"Networking","Hub-spoke, Private Endpoints, Application Gateway."),
            ])

        if t := TOOL.get("aws"):
            print(f"\n  AWS")
            add_concepts(c, t, [
                (B,"Core Services","EC2, S3, RDS, IAM, VPC, ELB."),
                (B,"IAM","Users, roles, policies, least privilege, STS."),
                (B,"S3","Buckets, storage classes, pre-signed URLs, versioning."),
                (M,"VPC","Subnets, route tables, IGW, NAT, security groups."),
                (M,"ECS & Fargate","Task definitions, services, ALB, service discovery."),
                (M,"RDS","Multi-AZ, read replicas, parameter groups, backups."),
                (H,"Lambda","Cold starts, layers, concurrency, VPC, event sources."),
                (H,"CDK","L1/L2/L3 constructs, stack, synth/deploy."),
                (H,"Cost Optimisation","Savings Plans, Spot, right-sizing, Cost Explorer."),
            ])

        if t := TOOL.get("cloudflare"):
            print(f"\n  Cloudflare")
            add_concepts(c, t, [
                (B,"DNS","A/CNAME/MX records, proxied vs DNS-only, TTL."),
                (B,"CDN & Caching","Edge caching, cache rules, purge, cache-control."),
                (B,"SSL/TLS","Universal SSL, custom certs, TLS mode (Full/Strict)."),
                (M,"Workers","Edge functions, fetch handler, KV, Durable Objects."),
                (M,"WAF & Security","Managed rules, custom rules, rate limiting, bot mgmt."),
                (M,"Pages","Static site hosting, build integration, preview deployments."),
                (H,"R2 Object Storage","S3-compatible, zero egress, Workers binding."),
                (H,"Zero Trust (Access)","Tunnels, access policies, WARP, identity providers."),
                (H,"Analytics & Logs","Log drain, GraphQL analytics API, real-time logs."),
            ])

        if t := TOOL.get("serverless"):
            print(f"\n  Serverless Architecture")
            add_concepts(c, t, [
                (B,"FaaS Concepts","Stateless functions, event-driven invocation, billing per invocation."),
                (B,"Cold Starts","Causes, mitigation (provisioned concurrency, warm-up)."),
                (B,"Triggers & Events","HTTP, queue, storage, schedule, pub/sub triggers."),
                (M,"Stateless Design","External state (DB/cache), idempotency, retry handling."),
                (M,"Serverless Framework","serverless.yml, plugins, deploy, offline testing."),
                (M,"SAM & CDK for Lambda","Infrastructure-as-code for Lambda functions."),
                (H,"Event-Driven Patterns","Saga, choreography vs orchestration in serverless."),
                (H,"Observability","Distributed tracing, X-Ray, Powertools for Lambda."),
                (H,"Cost Optimisation","Right-sizing memory, concurrency limits, async patterns."),
            ])

        # ── DATABASES ─────────────────────────────────────────────────────────
        if t := TOOL.get("postgresql"):
            print(f"\n  PostgreSQL")
            add_concepts(c, t, [
                (B,"DDL Basics","CREATE TABLE, types, PK, FK, UNIQUE, NOT NULL."),
                (B,"CRUD Queries","SELECT/INSERT/UPDATE/DELETE, WHERE, ORDER BY."),
                (B,"Joins","INNER/LEFT/RIGHT/FULL JOIN, ON, aliases."),
                (M,"Indexes","B-tree, GIN, GiST, EXPLAIN ANALYZE, partial indexes."),
                (M,"Transactions","BEGIN/COMMIT/ROLLBACK, isolation levels, deadlocks."),
                (M,"Window Functions","ROW_NUMBER, RANK, LEAD/LAG, PARTITION BY."),
                (H,"JSONB & Full-Text Search","@>, GIN indexing, to_tsvector."),
                (H,"Partitioning","Range/list/hash, pruning, pg_partman."),
                (H,"Replication","Streaming replication, WAL, PgBouncer, HA."),
            ])

        if t := TOOL.get("mysql"):
            print(f"\n  MySQL")
            add_concepts(c, t, [
                (B,"DDL & DML","CREATE/ALTER/DROP TABLE, INSERT/UPDATE/DELETE/SELECT."),
                (B,"Data Types","INT, VARCHAR, TEXT, DATETIME, DECIMAL, ENUM, JSON."),
                (B,"Joins & Subqueries","INNER/LEFT JOIN, correlated subqueries."),
                (M,"InnoDB & Indexes","Clustered index, secondary indexes, EXPLAIN."),
                (M,"Transactions","ACID, autocommit, isolation levels, deadlock detection."),
                (M,"Stored Procedures","CREATE PROCEDURE, IN/OUT params, cursors."),
                (H,"Replication","Source-replica, GTID-based, semi-sync replication."),
                (H,"MySQL 8 Features","Window functions, CTEs, invisible indexes, histograms."),
                (H,"Performance Schema","Query profiling, wait analysis, slow query log."),
            ])

        if t := TOOL.get("mongodb"):
            print(f"\n  MongoDB")
            add_concepts(c, t, [
                (B,"Documents & Collections","BSON model, _id, CRUD ops."),
                (B,"Query Operators","$eq/$gt/$in/$and/$or, projection, sort."),
                (B,"Schema Design","Embed vs reference, denormalisation trade-offs."),
                (M,"Aggregation Pipeline","$match/$group/$lookup/$project."),
                (M,"Indexes","Single, compound, text, TTL, explain()."),
                (M,"Mongoose ODM","Schema, virtuals, hooks, populate."),
                (H,"Transactions","Multi-document ACID, session management."),
                (H,"Replica Sets & Sharding","RS roles, shard key, mongos."),
                (H,"Change Streams","CDC, resume tokens, filtering events."),
            ])

        if t := TOOL.get("cassandra"):
            print(f"\n  Cassandra")
            add_concepts(c, t, [
                (B,"Data Model","Keyspaces, tables, partition key, clustering columns."),
                (B,"CQL Basics","CREATE TABLE, INSERT, SELECT with partition key."),
                (B,"CAP Trade-offs","AP system, eventual consistency, tunable consistency."),
                (M,"Consistency Levels","ONE/QUORUM/ALL, read/write consistency config."),
                (M,"Query-Driven Design","Denormalisation, query-first schema design."),
                (M,"Compaction Strategies","STCS, LCS, TWCS — when to use each."),
                (H,"Replication","NetworkTopologyStrategy, RF, data centre awareness."),
                (H,"Lightweight Transactions","IF NOT EXISTS, compare-and-set, trade-offs."),
                (H,"Spark + Cassandra","spark-cassandra-connector, pushdown predicates."),
            ])

        if t := TOOL.get("dynamodb"):
            print(f"\n  DynamoDB")
            add_concepts(c, t, [
                (B,"Tables & Keys","Partition key, sort key, item structure, attributes."),
                (B,"CRUD Operations","PutItem, GetItem, UpdateItem, DeleteItem, Query."),
                (B,"Billing Modes","On-demand vs provisioned, RCU/WCU."),
                (M,"Indexes","GSI, LSI — when to use, trade-offs."),
                (M,"DynamoDB Streams","CDC events, Lambda triggers, event types."),
                (M,"Access Patterns","Single-table design, adjacency list, overloading keys."),
                (H,"Transactions","TransactWrite/Get, atomicity, 25-item limit."),
                (H,"TTL & Archiving","TTL attribute, expired item deletion, archiving to S3."),
                (H,"Performance","DAX caching, hot partitions, adaptive capacity."),
            ])

        if t := TOOL.get("neo4j"):
            print(f"\n  Neo4j")
            add_concepts(c, t, [
                (B,"Graph Model","Nodes, relationships, properties, labels, directions."),
                (B,"Cypher Basics","MATCH, WHERE, RETURN, CREATE, MERGE, DELETE."),
                (B,"Use Cases","Social networks, recommendation, fraud detection, knowledge graphs."),
                (M,"Indexes & Constraints","Node/relationship indexes, uniqueness constraints."),
                (M,"Path Finding","SHORTEST PATH, ALL PATHS, APOC procedures."),
                (M,"Graph Algorithms","PageRank, community detection, centrality via GDS."),
                (H,"Neo4j GDS","Graph Data Science library, projections, algorithms."),
                (H,"Clustering","Causal cluster, leader election, read replicas."),
                (H,"GraphQL Integration","neo4j/graphql library, auto-generated CRUD."),
            ])

        if t := TOOL.get("influxdb"):
            print(f"\n  InfluxDB")
            add_concepts(c, t, [
                (B,"Time-Series Model","Measurement, tags, fields, timestamps, retention."),
                (B,"Flux Basics","from(), range(), filter(), aggregate window."),
                (B,"Write & Query","Line Protocol, InfluxDB UI, CLI, REST API."),
                (M,"Retention Policies","RP configuration, downsampling, continuous queries."),
                (M,"Tasks & Alerts","Flux tasks, check/threshold alerts, notification rules."),
                (M,"Telegraf Agent","Plugins (input/output/processor), config."),
                (H,"Clustering (InfluxDB Enterprise)","Data nodes, meta nodes, anti-entropy."),
                (H,"InfluxDB 3.0","Arrow/Parquet storage, IOx engine, SQL support."),
                (H,"Grafana Integration","InfluxDB data source, Flux queries in Grafana."),
            ])

        if t := TOOL.get("firebase"):
            print(f"\n  Firebase / Firestore")
            add_concepts(c, t, [
                (B,"Firestore Model","Collections, documents, sub-collections, auto-IDs."),
                (B,"CRUD","addDoc, setDoc, getDoc, updateDoc, deleteDoc."),
                (B,"Real-Time Listeners","onSnapshot, live updates, offline persistence."),
                (M,"Queries","where, orderBy, limit, compound queries, indexes."),
                (M,"Security Rules","match paths, request.auth, allow read/write."),
                (M,"Firebase Auth","Email/password, Google/GitHub OAuth, custom tokens."),
                (H,"Cloud Functions","Firestore triggers, HTTPS functions, scheduled."),
                (H,"Transactions & Batches","Atomic writes, runTransaction, writeBatch."),
                (H,"Firebase Extensions & Emulator","Local dev, emulator suite, extensions."),
            ])

        if t := TOOL.get("cockroachdb"):
            print(f"\n  CockroachDB")
            add_concepts(c, t, [
                (B,"PostgreSQL Compatibility","Wire protocol, SQL compatibility, migration path."),
                (B,"Distributed Architecture","Ranges, replication, leaseholder, multi-region."),
                (B,"CRUD & DDL","Standard SQL, auto-generated cluster IDs."),
                (M,"Transactions","Serialisable isolation, contention, retry logic."),
                (M,"Multi-Region","Survive zone/region, regional/global tables."),
                (M,"Indexes","Primary, secondary, partial, hash-sharded indexes."),
                (H,"Change Data Capture","changefeeds, Kafka/webhook sinks."),
                (H,"Performance Tuning","Explain (distsql), hot spots, key design."),
                (H,"Serverless vs Dedicated","CockroachDB Cloud tiers, pricing model."),
            ])

        if t := TOOL.get("sqlite"):
            print(f"\n  SQLite")
            add_concepts(c, t, [
                (B,"Embedded Engine","File-based, serverless, zero-config, when to use."),
                (B,"SQL Basics","DDL/DML/DQL, data types, type affinity."),
                (B,"Python Integration","sqlite3 module, connection, cursor, execute."),
                (M,"WAL Mode","Write-Ahead Log, concurrent reads, checkpoint."),
                (M,"Full-Text Search","FTS5, MATCH, BM25 ranking."),
                (M,"Indexes & Query Plan","EXPLAIN QUERY PLAN, index usage."),
                (H,"Litestream","Streaming replication to S3, disaster recovery."),
                (H,"SQLite Extensions","JSON, spatialite, custom functions."),
                (H,"Production Patterns","Edge/embedded use cases, Turso libSQL."),
            ])

        # ── TESTING ───────────────────────────────────────────────────────────
        if t := TOOL.get("pytest"):
            print(f"\n  Pytest")
            add_concepts(c, t, [
                (B,"Test Functions","def test_, assert, running with pytest."),
                (B,"Fixtures","@pytest.fixture, scope (function/class/module/session)."),
                (B,"Parametrize","@pytest.mark.parametrize, multiple inputs."),
                (M,"Mocking","unittest.mock, mocker fixture, patch, MagicMock."),
                (M,"Markers & Filtering","-m flag, custom markers, skip, xfail."),
                (M,"Coverage","pytest-cov, branch coverage, .coveragerc."),
                (H,"Async Tests","pytest-asyncio, async fixtures, event_loop scope."),
                (H,"Plugins","pytest-django, pytest-fastapi, conftest structure."),
                (H,"Test Architecture","Factory pattern, test isolation, DB strategies."),
            ])

        if t := TOOL.get("jest"):
            print(f"\n  Jest")
            add_concepts(c, t, [
                (B,"Test Basics","describe/it/test, expect matchers, toBe/toEqual."),
                (B,"Setup & Teardown","beforeEach/afterEach, beforeAll/afterAll."),
                (B,"Mocking","jest.fn(), jest.mock(), mockReturnValue."),
                (M,"Async Testing","async/await, resolves/rejects matchers."),
                (M,"Snapshot Testing","toMatchSnapshot, updating snapshots."),
                (M,"Code Coverage","--coverage, lcov, coverage thresholds."),
                (H,"React Testing Library","render, screen, userEvent, queries."),
                (H,"Module Mocking","Manual mocks, __mocks__ dir, factory pattern."),
                (H,"Custom Matchers","expect.extend, matcher factories."),
            ])

        if t := TOOL.get("cypress"):
            print(f"\n  Cypress")
            add_concepts(c, t, [
                (B,"Test Structure","cy.visit, cy.get, cy.click, cy.type basics."),
                (B,"Selectors","data-testid, cy.contains, within."),
                (B,"Assertions","should, and, then, expect."),
                (M,"Fixtures & Intercepts","cy.fixture, cy.intercept, route stubbing."),
                (M,"Commands","Custom commands, Cypress.Commands.add."),
                (M,"CI Integration","Headless mode, screenshots, videos."),
                (H,"Component Testing","Mount component, stub props, events."),
                (H,"Network & Auth","Cookie handling, token storage, login helpers."),
                (H,"Performance","Parallelisation, spec splitting, Cypress Cloud."),
            ])

        if t := TOOL.get("playwright"):
            print(f"\n  Playwright")
            add_concepts(c, t, [
                (B,"Page & Actions","page.goto, click, fill, press, type."),
                (B,"Locators","getByRole, getByText, getByTestId, locator."),
                (B,"Assertions","expect(locator).toBeVisible/toHaveText."),
                (M,"Fixtures","test.extend, reusable page objects, test isolation."),
                (M,"Network Interception","page.route, mock responses, waitForResponse."),
                (M,"Multi-Browser","chromium/firefox/webkit, mobile viewports."),
                (H,"API Testing","APIRequestContext, JSON validation."),
                (H,"Tracing & Debugging","trace viewer, screenshots, video recording."),
                (H,"CI & Sharding","--shard, GitHub Actions integration, test reports."),
            ])

        if t := TOOL.get("k6"):
            print(f"\n  k6")
            add_concepts(c, t, [
                (B,"Script Basics","default export, http.get/post, options config."),
                (B,"Virtual Users & Duration","vus, duration, ramp-up stages."),
                (B,"Checks & Thresholds","check(), threshold assertions, pass/fail."),
                (M,"Scenarios","Executor types (ramping-vus, constant-arrival-rate)."),
                (M,"Custom Metrics","Counter, Rate, Gauge, Trend."),
                (M,"Data & Environment","SharedArray, ENV vars, test data patterns."),
                (H,"Extensions","xk6 extensions, browser module, gRPC."),
                (H,"Distributed Execution","k6 Cloud, k6 operator for Kubernetes."),
                (H,"Results Analysis","InfluxDB/Grafana output, percentile interpretation."),
            ])

        if t := TOOL.get("postman"):
            print(f"\n  Postman / Newman")
            add_concepts(c, t, [
                (B,"Collections & Requests","Organising requests, HTTP methods, auth tabs."),
                (B,"Environments & Variables","Env, global, collection variables, {{syntax}}."),
                (B,"Pre-request & Tests","pm.test, pm.expect, status code assertions."),
                (M,"Newman CLI","newman run, reporters, CI integration."),
                (M,"Mock Servers","Simulating API responses, error scenarios."),
                (M,"Monitors","Scheduled collection runs, uptime alerting."),
                (H,"Contract Testing","Schema validation, OpenAPI import/sync."),
                (H,"Workflows","Postman Flows, visual chaining of requests."),
                (H,"API Documentation","Published docs, versioning, changelog."),
            ])

        # ── LANGUAGES ─────────────────────────────────────────────────────────
        if t := TOOL.get("python"):
            print(f"\n  Python")
            add_concepts(c, t, [
                (B,"Data Structures","list, dict, set, tuple — operations, comprehensions."),
                (B,"Functions & Scope","def, *args/**kwargs, closures, LEGB scope."),
                (B,"OOP Basics","class, __init__, inheritance, dunder methods."),
                (M,"Iterators & Generators","__iter__/__next__, yield, generator expressions."),
                (M,"Decorators","functools.wraps, parameterised decorators, class decorators."),
                (M,"Context Managers","__enter__/__exit__, contextlib.contextmanager."),
                (H,"Async Python","asyncio, async/await, event loop, aiohttp."),
                (H,"Metaclasses","type, __new__, metaclass uses in frameworks."),
                (H,"Performance","cProfile, dis, memoryview, cython basics."),
            ])

        if t := TOOL.get("javascript"):
            print(f"\n  JavaScript / ES6+")
            add_concepts(c, t, [
                (B,"ES6 Syntax","const/let, arrow functions, template literals, destructuring."),
                (B,"Array & Object Methods","map/filter/reduce, spread, Object.entries/keys."),
                (B,"Promises","new Promise, .then/.catch/.finally, async/await."),
                (M,"Closures & Scope","Lexical scope, closure use cases, IIFE."),
                (M,"Event Loop","Call stack, task queue, microtasks, setTimeout(0)."),
                (M,"Modules","ES modules (import/export), CommonJS, dynamic import."),
                (H,"Prototypes & Classes","Prototype chain, class sugar, mixins."),
                (H,"Proxy & Reflect","Meta-programming, reactive patterns."),
                (H,"Performance","V8 optimisations, memory leaks, Web Workers."),
            ])

        if t := TOOL.get("golang"):
            print(f"\n  Go")
            add_concepts(c, t, [
                (B,"Types & Syntax","Structs, interfaces, slices, maps, zero values."),
                (B,"Functions","Multiple return values, named returns, variadic."),
                (B,"Error Handling","error type, errors.Is/As, wrapping with %w."),
                (M,"Goroutines & Channels","go keyword, buffered/unbuffered channels, select."),
                (M,"Concurrency Patterns","WaitGroup, Mutex, sync package, fan-out/in."),
                (M,"Interfaces & Composition","Implicit satisfaction, embedding, io.Reader."),
                (H,"Context Package","ctx cancellation, timeout, value propagation."),
                (H,"Generics","Type parameters, constraints, comparable."),
                (H,"Testing & Benchmarks","testing.T, table-driven tests, Benchmark, pprof."),
            ])

        if t := TOOL.get("java"):
            print(f"\n  Java")
            add_concepts(c, t, [
                (B,"OOP Principles","Classes, inheritance, polymorphism, encapsulation."),
                (B,"Collections","List, Map, Set, ArrayList vs LinkedList, HashMap."),
                (B,"Exception Handling","try/catch/finally, checked vs unchecked exceptions."),
                (M,"Generics","Type parameters, wildcards (? extends / ? super), erasure."),
                (M,"Streams & Lambda","Stream API, filter/map/collect, method references."),
                (M,"Concurrency","Thread, Runnable, ExecutorService, Future."),
                (H,"JVM Internals","GC algorithms, heap/metaspace, JIT, class loading."),
                (H,"Java 17–21 Features","Records, sealed classes, pattern matching, virtual threads."),
                (H,"Reactive (Project Reactor)","Mono/Flux, backpressure, schedulers."),
            ])

        if t := TOOL.get("rust"):
            print(f"\n  Rust")
            add_concepts(c, t, [
                (B,"Ownership & Borrowing","Ownership rules, borrow checker, lifetimes basics."),
                (B,"Types & Pattern Matching","Structs, enums, Option/Result, match."),
                (B,"Error Handling","Result<T,E>, ?, thiserror, anyhow."),
                (M,"Traits","Defining/implementing traits, generics + trait bounds."),
                (M,"Lifetimes","Lifetime annotations, 'static, elision rules."),
                (M,"Async Rust","async/await, tokio, futures, Pin."),
                (H,"Unsafe Rust","raw pointers, unsafe blocks, FFI."),
                (H,"Macros","macro_rules!, procedural macros, derive."),
                (H,"Performance","zero-cost abstractions, profiling, SIMD."),
            ])

        if t := TOOL.get("kotlin"):
            print(f"\n  Kotlin")
            add_concepts(c, t, [
                (B,"Null Safety","?, !!, safe call, Elvis operator, nullable types."),
                (B,"Data Classes & Sealed","data class, copy, destructuring, sealed class."),
                (B,"Extension Functions","Adding methods to existing classes, scope."),
                (M,"Coroutines Basics","launch, async, await, CoroutineScope."),
                (M,"Flows","cold vs hot flows, operators, StateFlow, SharedFlow."),
                (M,"Collections","map/filter/fold, sequences, groupBy."),
                (H,"DSL Building","lambda with receiver, apply/run/let/also/with."),
                (H,"Multiplatform (KMP)","shared code, expect/actual, targets."),
                (H,"Coroutines Advanced","structured concurrency, SupervisorJob, channels."),
            ])

        if t := TOOL.get("swift"):
            print(f"\n  Swift")
            add_concepts(c, t, [
                (B,"Optionals","Optional<T>, if let, guard let, forced unwrap pitfalls."),
                (B,"Structs vs Classes","Value vs reference, mutability, when to use each."),
                (B,"Closures","Capture lists, @escaping, trailing closure syntax."),
                (M,"Protocols","Protocol-oriented programming, associated types."),
                (M,"Generics","Type parameters, where clause, protocol constraints."),
                (M,"Error Handling","throws, try/catch/try?, Result type."),
                (H,"Concurrency","async/await, actors, Task, structured concurrency."),
                (H,"Memory Management","ARC, strong/weak/unowned, retain cycles."),
                (H,"Swift Macros","Attached macros, freestanding macros, macro expansion."),
            ])

        # ── ARCHITECTURE ──────────────────────────────────────────────────────
        if t := TOOL.get("system-design"):
            print(f"\n  System Design")
            add_concepts(c, t, [
                (B,"Scalability Basics","Horizontal vs vertical scaling, stateless services."),
                (B,"Load Balancing","Round-robin, consistent hashing, L4 vs L7."),
                (B,"Caching Basics","CDN, in-memory cache, cache-aside, write-through."),
                (M,"Database Scaling","Read replicas, sharding, connection pooling."),
                (M,"Async & Queues","Message queues, task queues, decoupling services."),
                (M,"CAP Theorem","Consistency, availability, partition tolerance trade-offs."),
                (H,"Rate Limiting","Token bucket, leaky bucket, sliding window."),
                (H,"Distributed Transactions","2PC, saga pattern, compensating transactions."),
                (H,"Observability","Metrics, logs, traces — the three pillars."),
            ])

        if t := TOOL.get("microservices"):
            print(f"\n  Microservices")
            add_concepts(c, t, [
                (B,"Service Decomposition","Single responsibility, domain boundaries, cohesion."),
                (B,"Sync vs Async","REST/gRPC vs messaging, when to use each."),
                (B,"API Gateway","Routing, auth, rate limiting, aggregation."),
                (M,"Service Discovery","Client-side vs server-side, Consul, K8s DNS."),
                (M,"Resilience Patterns","Circuit breaker, retry, bulkhead, timeout."),
                (M,"Data Isolation","Database per service, shared database anti-pattern."),
                (H,"Distributed Tracing","OpenTelemetry, Jaeger, trace context propagation."),
                (H,"Service Mesh","Istio, Envoy, mTLS, traffic management."),
                (H,"Testing Strategies","Consumer-driven contracts, Pact, test pyramid."),
            ])

        if t := TOOL.get("rest-api-design"):
            print(f"\n  REST API Design")
            add_concepts(c, t, [
                (B,"HTTP Methods & Status Codes","GET/POST/PUT/PATCH/DELETE semantics, 2xx/4xx/5xx."),
                (B,"Resource Naming","Nouns not verbs, plural resources, nested resources."),
                (B,"Request & Response","JSON conventions, envelopes, pagination patterns."),
                (M,"Versioning","URL vs header vs query param versioning, deprecation."),
                (M,"Filtering, Sorting, Pagination","Query params, cursor vs offset pagination."),
                (M,"Authentication","Bearer token, API keys, OAuth2 in REST context."),
                (H,"HATEOAS","Hypermedia links, self-describing APIs, level 3 REST."),
                (H,"OpenAPI / Swagger","Spec-first design, code generation, contract testing."),
                (H,"Idempotency","Idempotency keys, safe methods, retry patterns."),
            ])

        if t := TOOL.get("event-driven"):
            print(f"\n  Event-Driven Architecture")
            add_concepts(c, t, [
                (B,"Events vs Commands vs Queries","Semantic differences, naming conventions."),
                (B,"Pub/Sub Pattern","Topics, subscribers, fan-out, at-least-once delivery."),
                (B,"Message Brokers","Kafka vs RabbitMQ vs SNS/SQS — choosing one."),
                (M,"Event Schema Design","Event versioning, schema registry, backward compat."),
                (M,"Idempotent Consumers","Deduplication, idempotency keys, at-least-once."),
                (M,"Choreography vs Orchestration","Trade-offs, saga pattern."),
                (H,"Event Sourcing","Events as source of truth, rebuilding state, projections."),
                (H,"Outbox Pattern","Transactional outbox, CDC, reliably publishing events."),
                (H,"Stream Processing","Stateful consumers, windowing, exactly-once."),
            ])

        if t := TOOL.get("ddd"):
            print(f"\n  Domain-Driven Design")
            add_concepts(c, t, [
                (B,"Ubiquitous Language","Shared vocabulary between devs and domain experts."),
                (B,"Entities & Value Objects","Identity vs equality, immutability, equality."),
                (B,"Aggregates","Aggregate root, consistency boundary, invariants."),
                (M,"Repositories","Abstraction over persistence, collection-like interface."),
                (M,"Domain Services","Stateless operations that don't fit an entity."),
                (M,"Bounded Contexts","Context map, context isolation, shared kernel."),
                (H,"Domain Events","Publishing events from aggregates, event handlers."),
                (H,"CQRS + DDD","Separate read/write models, denormalised read side."),
                (H,"Anti-Corruption Layer","Translating between bounded contexts, adapters."),
            ])

        if t := TOOL.get("cqrs"):
            print(f"\n  CQRS & Event Sourcing")
            add_concepts(c, t, [
                (B,"Command vs Query","Separating state-changing from read operations."),
                (B,"Read & Write Models","Separate models, eventual consistency implications."),
                (B,"Event Log","Append-only store, event as first-class citizen."),
                (M,"Projections","Building read models from events, rehydration."),
                (M,"Eventual Consistency","Lag, stale reads, compensating patterns."),
                (M,"Snapshotting","Periodic state snapshots to speed up rehydration."),
                (H,"Event Store","EventStoreDB, custom implementation, optimistic locking."),
                (H,"Temporal Queries","Querying state at a point in time, audit log."),
                (H,"Testing Event-Sourced Systems","Given-when-then, aggregate test patterns."),
            ])

        print("\n\n=== SEED COMPLETE ===")


if __name__ == "__main__":
    run()
