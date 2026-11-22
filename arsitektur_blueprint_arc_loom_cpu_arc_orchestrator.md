# ARC-Loom: Multi-Language CPU-ARC Orchestrator & Cost Telemetry Gateway

> **Tagline:** *Reason once with frontier models, weave into deterministic code, execute anywhere on CPU.*

**ARC-Loom** adalah implementasi arsitektur *Agentic Reasoning Caching* (CPU-ARC) yang dirancang agar mudah diintegrasikan ke berbagai proyek dan bahasa pemrograman—baik sebagai **SDK native (`pip` & `npm`)** maupun sebagai **Centralized OpenAI-Compatible Gateway** untuk aplikasi seperti PHP/Laravel, Go, atau bot operasional.

---

## 1. Mengapa Multi-Platform (`pip` + `npm` + Gateway)?

Agar konsep CPU-ARC bisa digunakan secara menyeluruh di seluruh ekosistem proyek, arsitektur dibagi menjadi **Core Engine** dan **Thin Client SDKs**:

| Paket / Layanan | Target Ekosistem | Cara Kerja |
| :--- | :--- | :--- |
| **`arc-loom` (PyPI / `pip`)** | Python (FastAPI, AI Workers, Data Pipelines) | Dapat berjalan secara *embedded* (langsung di proses Python lokal) atau terhubung ke ARC-Loom Server. |
| **`@arc-loom/sdk` (`npm` / Bun)** | TypeScript / JavaScript (ElysiaJS, Next.js, Node) | *Thin client* ringan yang membungkus OpenAI SDK / fetch, mendaftarkan *local tools* secara otomatis, dan mengirim telemetri ke server. |
| **`arc-loom-server` (Docker / CLI)** | Semua bahasa (PHP/Laravel, Go, Rust, Telegram Bot) | Berfungsi sebagai *drop-in reverse proxy* (`/v1/chat/completions`), pusat indeks vektor FAISS, *background builder*, dan *dashboard* FinOps. |

---

## 2. Arsitektur Tingkat Tinggi

```text
+-----------------------------------------------------------------------------+
|  CLIENT APPLICATIONS                                                        |
|  +-----------------------+  +-----------------------+  +-----------------+  |
|  | Python App            |  | Bun / Node App        |  | Laravel / PHP   |  |
|  | pip install arc-loom  |  | npm i @arc-loom/sdk   |  | HTTP OpenAI SDK |  |
|  +-----------+-----------+  +-----------+-----------+  +--------+--------+  |
+--------------|--------------------------|-----------------------|-----------+
               |                          |                       |
               +--------------------------+-----------------------+
                                          | (Header: X-ARC-Project, X-ARC-Mode)
                                          v
+-----------------------------------------------------------------------------+
|  ARC-LOOM SERVER (Central Orchestrator & Telemetry Engine)                  |
|                                                                             |
|  1. Entrypoint Router (CPU)                                                 |
|     +-- Cek Routing Mode: [ bypass | shadow | active ]                      |
|     +-- Embedding Query -> Cari kecocokan di FAISS Registry                 |
|                                                                             |
|  2. Jalur Eksekusi                                                          |
|     +-- [CACHE HIT]  --> Jalankan Semantic Function (.py) di CPU            |
|     |                    +-- Panggil Local SLM 1B (extract / lm / convert)  |
|     |                    +-- Panggil Tool Callback ke Client App (jika ada) |
|     |                                                                       |
|     +-- [CACHE MISS] --> Teruskan ke Source Model (S) [Opus / Fable 5.1]    |
|                          +-- Kirim jawaban ke user (zero-delay)             |
|                          +-- Masukkan ke Async Builder Queue (Background)   |
|                                                                             |
|  3. Async Builder & Validator (Background Worker)                           |
|     +-- Source (S) & Builder (B) memecah logika & menulis kode Python       |
|     +-- Uji fungsional & uji ekstraksi dengan Local SLM 1B (T)              |
|     +-- Lolos? Simpan ke Registry + opsi Compile ke O(1) Python             |
+--------------------------------------+--------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
|  STORAGE & FINOPS LEDGER                                                    |
|  - PostgreSQL : projects, cache_entries, telemetry_ledger, tool_schemas     |
|  - Disk/S3    : /caches/{project}/*.py (dynamic & compiled) + *.faiss       |
|  - Web UI     : Real-time Routing Visualizer, Token per Role, ROI Counter   |
+-----------------------------------------------------------------------------+
```

---

## 3. Cara Penggunaan di Kode (Developer Experience)

### A. Penggunaan via `npm` (Bun / ElysiaJS / TypeScript)

Developer cukup membungkus *client* AI mereka dan mendaftarkan *tool* lokal secara deklaratif:

```typescript
import { createArcClient } from "@arc-loom/sdk";

const arc = createArcClient({
  gatewayUrl: "http://localhost:8080/v1",
  project: "finansia",
  mode: "active", // "bypass" | "shadow" | "active"
  models: {
    source: "anthropic/claude-opus-4.5", // atau "fable-5.1"
    builder: "anthropic/claude-opus-4.5",
    target: "local/llama-3.2-1b",
  },
  // Daftarkan fungsi lokal yang boleh dipanggil oleh kode hasil cache
  tools: {
    getMonthlyExpense: async ({ month, category }: { month: string; category?: string }) => {
      return await db.queryExpense(month, category);
    },
  },
});

// Pemakaian standar layaknya chat completion biasa
const res = await arc.chat({
  prompt: "Berapa total pengeluaran kategori makan bulan November 2026?",
});

console.log(res.answer);
console.log(res.telemetry);
// Output telemetry:
// { path: "arc_hit", actualCostUsd: 0.00004, savedUsd: 0.0412, latencyMs: 145 }
```

### B. Penggunaan via `pip` (Python)

Bisa dijalankan secara mandiri (*standalone mode*) tanpa server eksternal, maupun terhubung ke *central gateway*:

```python
from arc_loom import ArcLoom, tool

arc = ArcLoom(
    project="ima-isa",
    mode="active",
    source_model="fable-5.1",
    builder_model="fable-5.1",
    target_endpoint="http://localhost:11434/v1",  # llama.cpp / Ollama
    target_model="llama-3.2-1b",
)

@arc.tool("Ambil daftar artikel berdasarkan topik dan rentang hari")
def fetch_articles(topic: str, days: int = 7) -> list[dict]:
    return search_database(topic, days)

response = arc.run("Ringkas sentimen berita tentang suku bunga BI 7 hari terakhir")
print(response.output)
print(response.narration_trace)  # Jejak audit langkah demi langkah
```

### C. Penggunaan via HTTP Proxy (Laravel / PHP / Bahasa Lain)

Tanpa perlu menginstal SDK khusus, cukup arahkan konfigurasi OpenAI client ke `http://localhost:8080/v1` dengan menyertakan *header*:
* `X-ARC-Project: finansia-laravel`
* `X-ARC-Mode: shadow` (untuk observasi aman tanpa mengubah respons produksi) atau `active`
* `X-ARC-Tool-Callback: http://host.docker.internal:8000/api/internal/arc-tools`

---

## 4. Runtime Primitives (Inti Eksekusi CPU-ARC)

Setiap skrip Python yang dihasilkan oleh *Builder Model* ($\mathcal{B}$) wajib menggunakan primitif standar berikut agar tetap modular, aman tipe datanya (*type-safe*), dan dapat diaudit:

1. **`Context` (Layered Memory):** Tumpukan *dictionary* berlapis dengan pencarian kemiripan semantik lokal. Setiap pemanggilan sub-fungsi mendorong (*push*) *layer* baru dan menghapusnya (*pop*) saat selesai agar variabel tidak saling bertabrakan.
2. **`load(ctx, query, d_type)`:** Mengambil parameter dari `Context` berdasarkan deskripsi bahasa alami (bukan nama variabel kaku) dan melakukan *type coercion*.
3. **`extract(ctx, text, prompt, d_type)`:** Memanggil model lokal 1B ($\mathcal{T}$) dengan `temperature=0.0` untuk mengekstrak nilai spesifik (angka, tanggal, entitas) dari teks mentah.
4. **`lm(ctx, prompt)`:** Memanggil model lokal 1B ($\mathcal{T}$) untuk klasifikasi singkat atau konversi format.
5. **`smart_convert(ctx, text, target_type)`:** Konversi tipe data bertingkat; jika *casting* Python biasa gagal, meminta model 1B menulis ulang menjadi *Python literal* lalu memvalidasinya lewat `ast.literal_eval()`.
6. **`do(ctx, intent_description, payloads)`:** Menemukan dan menjalankan sub-fungsi semantik lain berdasarkan deskripsi niatnya (*intent*).
7. **`invoke_tool(ctx, tool_name, args)`:** Mengeksekusi *tool* lokal (di mode *embedded*) atau mengirim *signed webhook* ke aplikasi klien (di mode *gateway*).
8. **`narrate(ctx, message)`:** Mencatat setiap langkah penalaran dalam bahasa manusia untuk keperluan *debugging* dan audit kepatuhan.

---

## 5. Skema Telemetri Token & FinOps Ledger (PostgreSQL)

Untuk melacak pergerakan token secara presisi di tiap peran—**Source ($\mathcal{S}$)**, **Builder ($\mathcal{B}$)**, **Target ($\mathcal{T}$)**, **Extractor**, dan **Converter**—serta membandingkan biaya aktual terhadap skenario tanpa CPU-ARC:

```sql
CREATE TABLE projects (
    id SERIAL PRIMARY KEY,
    slug VARCHAR(64) UNIQUE NOT NULL,
    routing_mode VARCHAR(16) DEFAULT 'active',   -- 'bypass' | 'shadow' | 'active'
    similarity_threshold REAL DEFAULT 0.82,
    source_model VARCHAR(128) NOT NULL,
    builder_model VARCHAR(128) NOT NULL,
    target_model VARCHAR(128) NOT NULL,
    tool_callback_url TEXT,
    tool_callback_secret VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE cache_entries (
    id SERIAL PRIMARY KEY,
    project_slug VARCHAR(64) REFERENCES projects(slug),
    entry_key VARCHAR(128) UNIQUE NOT NULL,
    intent_description TEXT NOT NULL,
    param_schema JSONB NOT NULL,
    source_path TEXT NOT NULL,
    is_compiled BOOLEAN DEFAULT FALSE,
    hit_count INTEGER DEFAULT 0,
    avg_source_cost_usd NUMERIC(12, 8) DEFAULT 0,
    builder_cost_usd NUMERIC(12, 8) DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE telemetry_ledger (
    id BIGSERIAL PRIMARY KEY,
    request_id VARCHAR(64) NOT NULL,
    project_slug VARCHAR(64) NOT NULL,
    cache_entry_id INTEGER REFERENCES cache_entries(id) ON DELETE SET NULL,
    execution_path VARCHAR(32) NOT NULL,       -- 'arc_hit' | 'arc_miss_build' | 'bypass' | 'shadow'
    role VARCHAR(24) NOT NULL,                 -- 'source' | 'builder' | 'target' | 'extractor' | 'converter'
    model_name VARCHAR(128) NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    cost_usd NUMERIC(12, 8) DEFAULT 0,
    counterfactual_cost_usd NUMERIC(12, 8) DEFAULT 0, -- Estimasi biaya jika tanpa CPU-ARC
    latency_ms REAL DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### Rumus Metrik Utama di Dashboard
* **Net Savings (Penghematan Bersih):**
  $$\text{Net Savings} = \sum \text{Counterfactual Cost} - \sum (\text{Source Cost} + \text{Builder Cost} + \text{Cache Execution Cost})$$
* **Break-Even Point per Fungsi (Titik Impas):**
  $$\text{Break-Even Calls} = \left\lceil \frac{\text{Builder Cost}}{\text{Avg Source Cost} - \text{Avg Cache Cost}} \right\rceil$$

---

## 6. Struktur Monorepo `arc-loom`

```text
arc-loom/
├── packages/
│   ├── core-py/                 # Paket PyPI: `arc-loom` (Runtime, Compiler, Builder, FastAPI Server)
│   │   ├── arc_loom/
│   │   │   ├── runtime/         # context.py, primitives.py, registry.py, compiler.py
│   │   │   ├── builder/         # worker.py (5-phase construction + 1B validation loop)
│   │   │   ├── telemetry/       # ledger.py, pricing.py
│   │   │   └── server/          # app.py (OpenAI-compatible proxy + webhook dispatcher)
│   │   └── pyproject.toml
│   ├── sdk-ts/                  # Paket NPM: `@arc-loom/sdk` (Thin client + tool bridge server)
│   │   ├── src/
│   │   │   ├── client.ts
│   │   │   └── toolServer.ts
│   │   └── package.json
│   └── dashboard/               # Web UI FinOps & Routing Visualizer (React / Vite)
├── docker-compose.yml           # Menjalankan arc-loom-server + PostgreSQL
└── README.md
```

---

## 7. Roadmap Implementasi

1. **Fase 1 — Core Runtime & Telemetry Ledger (`packages/core-py`):**
   Implementasi primitif (`load`, `extract`, `lm`, `smart_convert`, `do`, `narrate`), *wrapper* pencatat token per peran (`source`, `builder`, `target`, `extractor`, `converter`), dan mode `bypass` vs `active`.
2. **Fase 2 — Async Builder & 1B Validation Loop:**
   Implementasi *worker* 5 fase yang menyuruh model besar ($\mathcal{S}$ & $\mathcal{B}$) menulis kode Python saat *cache miss*, lalu memvalidasinya terhadap *output* asli dan menguji ketahanan ekstraksinya menggunakan Llama 3.2 1B lokal.
3. **Fase 3 — Multi-Language SDK (`@arc-loom/sdk`) & Tool Bridge:**
   Rilis paket NPM untuk ekosistem Node/Bun beserta protokol *HMAC-signed webhook* agar fungsi Python hasil *cache* bisa memanggil fungsi lokal TypeScript atau endpoint Laravel.
4. **Fase 4 — Static Compiler & FinOps Dashboard:**
   Tambahkan kompilator yang mengubah jaringan *semantic function* menjadi modul Python statis $O(1)$ (tanpa *vector search* saat *runtime*) serta antarmuka visual untuk memantau grafik penghematan biaya lintas proyek.