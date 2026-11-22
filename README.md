# ARC-Loom: Multi-Language CPU-ARC Orchestrator & Cost Telemetry Gateway

> *Reason once with frontier models, weave into deterministic code, execute anywhere on CPU.*

**ARC-Loom** is a multi-language implementation of **Agentic Reasoning Caching (CPU-ARC)**, providing a Python runtime and compiler (`arc-loom`), a TypeScript/Bun SDK (`@arc-loom/sdk`), an OpenAI-compatible HTTP gateway (`arc-loom-server`) backed by persistent SQLite (WAL mode) or optional PostgreSQL, and an interactive FinOps ledger dashboard (`@arc-loom/dashboard`).

## Research Foundation

ARC-Loom is built on the architecture and runtime primitives introduced in:

- **Paper:** *CPU-ARC: Agentic Reasoning Caching for on-CPU Reasoning Reuse* (Peter Belcak, Greg Heinrich, Saurav Muralidharan, Shizhe Diao, Pavlo Molchanov)
- **PDF:** [https://arxiv.org/pdf/2611.09891](https://arxiv.org/pdf/2611.09891)
- **Abstract:** [https://arxiv.org/abs/2611.09891](https://arxiv.org/abs/2611.09891)

Core concepts implemented from the paper:
- **Role Separation (S / B / T):** Frontier **Source (S)** handles cache misses, **Builder (B)** synthesizes deterministic Python semantic functions, and a local 1B **Target (T)** model (`llama-3.2-1b`) handles lightweight runtime extraction, classification, and type conversion.
- **8 Runtime Primitives:** `Context` (layered semantic memory), `load`, `extract`, `lm`, `smart_convert` (with `ast.literal_eval()` fallback), `do`, `invoke_tool`, and `narrate`.
- **5-Phase Builder & Perturbation Loop:** Decomposes reasoning traces, synthesizes `def run(ctx):`, audits AST safety, validates against the 1B Target model across reference and perturbed queries (Phase 4b), and exports validated traces to JSONL (`export_sft_jsonl`) for continual 1B fine-tuning.
- **Static O(1) Compiler:** Compiles `load()` and `do()` calls into direct `ctx.get_exact()` lookups and static Python function calls without runtime vector search.

## Monorepo Layout

```text
arc-loom/
├── DESIGN.md                    # Visual direction, Three Dials, WCAG AA contrast & R-31 ledger
├── docker-compose.yml           # Runs arc-loom-server (port 8080) with persistent SQLite volume
├── requirements.txt             # Root pip requirements (-r packages/core-py/requirements.txt)
├── .gitignore
├── packages/
│   ├── core-py/                 # PyPI package: arc-loom
│   │   ├── arc_loom/
│   │   │   ├── runtime/         # context.py, primitives.py, registry.py, compiler.py
│   │   │   ├── builder/         # worker.py (5-phase builder, perturbation check, SFT export)
│   │   │   ├── telemetry/       # pricing.py, ledger.py (SQLite WAL + optional Postgres), schema.sql
│   │   │   ├── server/          # app.py (OpenAI-compatible proxy + FinOps & compiler endpoints)
│   │   │   └── client.py        # Embedded & Gateway Python SDK (ArcLoom, @tool)
│   │   ├── tests/               # Unit tests (test_sprint1.py .. test_sprint5_parity.py)
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── requirements.txt
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

## Quickstart

### 1. Python (Embedded / Standalone)

```bash
pip install -r requirements.txt
```

```python
from arc_loom import ArcLoom

arc = ArcLoom(
    project="my-python-app",
    mode="active",  # "bypass" | "shadow" | "active"
    source_model="openai/gpt-4o",
    builder_model="openai/gpt-4o",
    source_endpoint="https://api.openai.com/v1",
    source_api_key="sk-...",
    target_endpoint="http://localhost:11434/v1",
    target_model="llama-3.2-1b",
    sqlite_path="./data/arc_loom.db",
    cache_dir="./data/caches",
)

@arc.tool("Query monthly expense by category")
def get_expense(month: str, category: str = "food") -> int:
    return 2450000

res = arc.run("What is my food expense for November 2026?")
print(res.output, res.execution_path, res.saved_usd)
```

### 2. Gateway Server (OpenAI-Compatible Proxy)

Start the server locally (uses persistent SQLite at `./data/arc_loom.db` with WAL mode by default):

```bash
uvicorn arc_loom.server.app:create_fastapi_app --factory --host 0.0.0.0 --port 8080
```

Or via Docker Compose:

```bash
docker compose up -d
```

Send requests to `http://localhost:8080/v1/chat/completions` from any language (PHP/Laravel, Go, Rust) with headers:
- `X-ARC-Project: <project-slug>`
- `X-ARC-Mode: active` (`bypass`, `shadow`, or `active`)
- `X-ARC-Tool-Callback: http://localhost:8000/api/internal/arc-tools`
- `X-ARC-Tool-Secret: <hmac-secret>`

### 3. TypeScript / Bun (`@arc-loom/sdk`)

```bash
bun add ./packages/sdk-ts
```

```typescript
import { createArcClient } from "@arc-loom/sdk";

const arc = createArcClient({
  gatewayUrl: "http://localhost:8080/v1",
  project: "finansia",
  mode: "active",
  models: {
    source: "anthropic/claude-opus-4.5",
    builder: "anthropic/claude-opus-4.5",
    target: "local/llama-3.2-1b",
  },
  toolCallbackUrl: "http://localhost:3000/internal/arc-tools",
  toolCallbackSecret: "hmac-secret",
  tools: {
    getMonthlyExpense: async ({ month, category }: { month: string; category?: string }) => {
      return { month, category, totalIdr: 2450000 };
    },
  },
});

await arc.syncTools();
const res = await arc.chat({ prompt: "Berapa pengeluaran makan bulan November 2026?" });
console.log(res.answer, res.telemetry);
```

### 4. FinOps Dashboard (`packages/dashboard`)

```bash
python3 -m http.server 3001 -d packages/dashboard
```

Open `http://localhost:3001`, set the Gateway URL to `http://localhost:8080/v1`, and click **Sinkronkan Live** to inspect Net Savings, Break-Even Calls, Role Token Breakdown, and O(1) Static Compilation.

## Running Tests

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
