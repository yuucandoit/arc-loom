# ARC-Loom: Multi-Language CPU-ARC Orchestrator & Cost Telemetry Gateway

> *Reason once with frontier models, weave into deterministic code, execute anywhere on CPU.*

ARC-Loom implements Agentic Reasoning Caching (CPU-ARC) across Python (`arc-loom`), TypeScript/Bun (`@arc-loom/sdk`), an OpenAI-compatible HTTP Gateway (`arc-loom-server`), and an Industrial Ledger FinOps Console (`@arc-loom/dashboard`).

## Monorepo Layout

```text
arc-loom/
├── DESIGN.md                    # Visual direction, Three Dials, WCAG AA contrast & R-31 ledger
├── docker-compose.yml           # Runs arc-loom-server (port 8080) + PostgreSQL 16
├── packages/
│   ├── core-py/                 # PyPI package: arc-loom
│   │   ├── arc_loom/
│   │   │   ├── runtime/         # context.py, primitives.py, registry.py, compiler.py
│   │   │   ├── builder/         # worker.py (5-phase construction + AST audit + 1B validation loop)
│   │   │   ├── telemetry/       # pricing.py, ledger.py, schema.sql
│   │   │   ├── server/          # app.py (OpenAI-compatible proxy + FinOps & compiler endpoints)
│   │   │   └── client.py        # Embedded & Gateway Python SDK (ArcLoom, @tool)
│   │   ├── tests/               # Sprint 1, 2, and 4 unit test suites
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   ├── sdk-ts/                  # NPM/Bun package: @arc-loom/sdk
│   │   ├── src/
│   │   │   ├── client.ts        # createArcClient (chat, syncTools, triggerBuild, getTelemetry)
│   │   │   ├── toolServer.ts    # HMAC-SHA256 webhook verifier & local tool dispatcher
│   │   │   └── index.ts
│   │   ├── tests/sdk.test.ts
│   │   └── package.json
│   └── dashboard/               # FinOps Ledger & Routing Visualizer Web UI
│       ├── index.html
│       ├── src/
│       │   ├── styles.css
│       │   ├── finopsEngine.ts
│       │   └── app.ts
│       ├── dist/app.js
│       ├── tests/dashboard.test.ts
│       └── package.json
└── arsitektur_blueprint_arc_loom_cpu_arc_orchestrator.md
```

## Running Tests & Builds

- **Python Core (`packages/core-py`)**:
  ```bash
  PYTHONPATH=packages/core-py python3 -m unittest discover -s packages/core-py/tests -v
  ```
- **TypeScript SDK (`packages/sdk-ts`)**:
  ```bash
  cd packages/sdk-ts && bun test
  ```
- **FinOps Dashboard (`packages/dashboard`)**:
  ```bash
  cd packages/dashboard && bun run build && bun test
  ```
