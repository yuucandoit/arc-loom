export type RoutingMode = "active" | "shadow" | "bypass";
export type ExecutionPath = "arc_hit" | "arc_miss_build" | "bypass" | "shadow";
export type TelemetryRole = "source" | "builder" | "target" | "extractor" | "converter";

export interface SemanticFunctionItem {
  entryKey: string;
  projectSlug: string;
  intentDescription: string;
  paramSchema: Record<string, string>;
  isCompiled: boolean;
  hitCount: number;
  avgSourceCostUsd: number;
  builderCostUsd: number;
  avgCacheCostUsd: number;
  dynamicCode: string;
  compiledCode: string;
}

export interface TelemetryLedgerRow {
  id: number;
  requestId: string;
  projectSlug: string;
  entryKey: string | null;
  executionPath: ExecutionPath;
  role: TelemetryRole;
  modelName: string;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
  counterfactualCostUsd: number;
  latencyMs: number;
  timestamp: string;
}

export interface FinOpsTotals {
  totalRequests: number;
  arcHits: number;
  hitRatePercent: number;
  counterfactualCostUsd: number;
  sourceCostUsd: number;
  builderCostUsd: number;
  cacheExecutionCostUsd: number;
  actualTotalCostUsd: number;
  netSavingsUsd: number;
}

export interface BreakEvenEvaluation {
  breakEvenCalls: number | null;
  remainingCalls: number | null;
  isProfitable: boolean;
  marginPerCallUsd: number;
}

export function formatUsd8(amount: number): string {
  const sign = amount < 0 ? "-" : "";
  return `${sign}$${Math.abs(amount).toFixed(8)}`;
}

export function formatUsd4(amount: number): string {
  const sign = amount < 0 ? "-" : "";
  return `${sign}$${Math.abs(amount).toFixed(4)}`;
}

export function evaluateBreakEven(fn: Pick<
  SemanticFunctionItem,
  "builderCostUsd" | "avgSourceCostUsd" | "avgCacheCostUsd" | "hitCount"
>): BreakEvenEvaluation {
  const margin = fn.avgSourceCostUsd - fn.avgCacheCostUsd;
  if (margin <= 0) {
    return {
      breakEvenCalls: null,
      remainingCalls: null,
      isProfitable: false,
      marginPerCallUsd: margin,
    };
  }
  const breakEvenCalls = Math.ceil(fn.builderCostUsd / margin);
  const remainingCalls = Math.max(0, breakEvenCalls - fn.hitCount);
  return {
    breakEvenCalls,
    remainingCalls,
    isProfitable: fn.hitCount >= breakEvenCalls,
    marginPerCallUsd: margin,
  };
}

export function computeFinOpsTotals(
  rows: TelemetryLedgerRow[],
  projectFilter: string = "all"
): FinOpsTotals {
  const filtered =
    projectFilter === "all"
      ? rows
      : rows.filter((r) => r.projectSlug === projectFilter);

  const reqIds = new Set<string>();
  const hitReqIds = new Set<string>();
  let counterfactualCostUsd = 0;
  let sourceCostUsd = 0;
  let builderCostUsd = 0;
  let cacheExecutionCostUsd = 0;

  for (const row of filtered) {
    reqIds.add(row.requestId);
    if (row.executionPath === "arc_hit") {
      hitReqIds.add(row.requestId);
    }
    counterfactualCostUsd += row.counterfactualCostUsd;
    if (row.role === "source") {
      sourceCostUsd += row.costUsd;
    } else if (row.role === "builder") {
      builderCostUsd += row.costUsd;
    } else {
      cacheExecutionCostUsd += row.costUsd;
    }
  }

  const actualTotalCostUsd =
    sourceCostUsd + builderCostUsd + cacheExecutionCostUsd;
  const netSavingsUsd = counterfactualCostUsd - actualTotalCostUsd;
  const totalRequests = reqIds.size;
  const arcHits = hitReqIds.size;
  const hitRatePercent =
    totalRequests > 0 ? Math.round((arcHits / totalRequests) * 1000) / 10 : 0;

  return {
    totalRequests,
    arcHits,
    hitRatePercent,
    counterfactualCostUsd: Number(counterfactualCostUsd.toFixed(8)),
    sourceCostUsd: Number(sourceCostUsd.toFixed(8)),
    builderCostUsd: Number(builderCostUsd.toFixed(8)),
    cacheExecutionCostUsd: Number(cacheExecutionCostUsd.toFixed(8)),
    actualTotalCostUsd: Number(actualTotalCostUsd.toFixed(8)),
    netSavingsUsd: Number(netSavingsUsd.toFixed(8)),
  };
}

export function computeRoleBreakdown(
  rows: TelemetryLedgerRow[],
  projectFilter: string = "all"
): Record<
  TelemetryRole,
  { calls: number; inputTokens: number; outputTokens: number; costUsd: number }
> {
  const summary: Record<
    TelemetryRole,
    { calls: number; inputTokens: number; outputTokens: number; costUsd: number }
  > = {
    source: { calls: 0, inputTokens: 0, outputTokens: 0, costUsd: 0 },
    builder: { calls: 0, inputTokens: 0, outputTokens: 0, costUsd: 0 },
    target: { calls: 0, inputTokens: 0, outputTokens: 0, costUsd: 0 },
    extractor: { calls: 0, inputTokens: 0, outputTokens: 0, costUsd: 0 },
    converter: { calls: 0, inputTokens: 0, outputTokens: 0, costUsd: 0 },
  };

  const filtered =
    projectFilter === "all"
      ? rows
      : rows.filter((r) => r.projectSlug === projectFilter);

  for (const row of filtered) {
    const slot = summary[row.role];
    slot.calls += 1;
    slot.inputTokens += row.inputTokens;
    slot.outputTokens += row.outputTokens;
    slot.costUsd = Number((slot.costUsd + row.costUsd).toFixed(8));
  }

  return summary;
}

export const INITIAL_FUNCTIONS: SemanticFunctionItem[] = [
  {
    entryKey: "query_monthly_category_expense",
    projectSlug: "finansia",
    intentDescription:
      "Berapa total pengeluaran kategori makan bulan November 2026?",
    paramSchema: { month: "str", category: "str" },
    isCompiled: true,
    hitCount: 5,
    avgSourceCostUsd: 0.04124,
    builderCostUsd: 0.0985,
    avgCacheCostUsd: 0.00000216,
    dynamicCode: `def run(ctx):
    raw_q = load(ctx, "raw user prompt query text", str)
    narrate(ctx, "Ekstraksi bulan & kategori via Llama 3.2 1B")
    month = extract(ctx, raw_q, "Extract month in YYYY-MM", str)
    category = extract(ctx, raw_q, "Extract expense category", str)
    total = invoke_tool(ctx, "getMonthlyExpense", {"month": month, "category": category})
    return f"Total pengeluaran kategori {category} ({month}): Rp {total}"`,
    compiledCode: `# ARC-Loom Static O(1) Compiled Module (zero runtime vector search)
def run(ctx):
    raw_q = smart_convert(ctx, ctx.get_exact('query'), str)
    narrate(ctx, 'Ekstraksi bulan & kategori via Llama 3.2 1B')
    month = extract(ctx, raw_q, 'Extract month in YYYY-MM', str)
    category = extract(ctx, raw_q, 'Extract expense category', str)
    total = invoke_tool(ctx, 'getMonthlyExpense', {'month': month, 'category': category})
    return f'Total pengeluaran kategori {category} ({month}): Rp {total}'`,
  },
  {
    entryKey: "summarize_bi_rate_news",
    projectSlug: "ima-isa",
    intentDescription:
      "Ringkas sentimen berita tentang suku bunga BI 7 hari terakhir",
    paramSchema: { topic: "str", days: "int" },
    isCompiled: false,
    hitCount: 2,
    avgSourceCostUsd: 0.0414,
    builderCostUsd: 0.114,
    avgCacheCostUsd: 0.0000031,
    dynamicCode: `def run(ctx):
    raw_q = load(ctx, "raw user prompt query text", str)
    narrate(ctx, "Mengekstrak rentang hari dari prompt pengguna")
    days = extract(ctx, raw_q, "Berapa rentang hari yang diminta?", int)
    articles = invoke_tool(ctx, "fetch_articles", {"topic": "suku bunga BI", "days": days})
    sentiment = do(ctx, "klasifikasi sentimen kumpulan artikel berita", {"daftar_artikel": articles})
    return f"Ringkasan {len(articles)} artikel ({days} hari): {sentiment}"`,
    compiledCode: `# ARC-Loom Static O(1) Compiled Module (zero runtime vector search)
def _compiled_classify_article_batch(ctx):
    items = smart_convert(ctx, ctx.get_exact('daftar_artikel'), list)
    return lm(ctx, f'Klasifikasikan sentimen: {items}')

def run(ctx):
    raw_q = smart_convert(ctx, ctx.get_exact('query'), str)
    narrate(ctx, 'Mengekstrak rentang hari dari prompt pengguna')
    days = extract(ctx, raw_q, 'Berapa rentang hari yang diminta?', int)
    articles = invoke_tool(ctx, 'fetch_articles', {'topic': 'suku bunga BI', 'days': days})
    sentiment = _invoke_static(_compiled_classify_article_batch, ctx, {'daftar_artikel': articles})
    return f'Ringkasan {len(articles)} artikel ({days} hari): {sentiment}'`,
  },
  {
    entryKey: "verify_invoice_tax_ppn",
    projectSlug: "finansia-laravel",
    intentDescription:
      "Hitung PPN 11 persen dan validasi nomor faktur pajak klien",
    paramSchema: { invoice_amount: "float", tax_rate: "float" },
    isCompiled: true,
    hitCount: 4,
    avgSourceCostUsd: 0.0365,
    builderCostUsd: 0.082,
    avgCacheCostUsd: 0.0000018,
    dynamicCode: `def run(ctx):
    raw_q = load(ctx, "raw user prompt query text", str)
    nominal = extract(ctx, raw_q, "Ekstrak nilai nominal tagihan", float)
    ppn = round(nominal * 0.11, 2)
    narrate(ctx, f"PPN 11% dihitung deterministik di CPU: Rp {ppn}")
    return f"Tagihan Rp {nominal:,.0f} | PPN 11%: Rp {ppn:,.0f}"`,
    compiledCode: `# ARC-Loom Static O(1) Compiled Module (zero runtime vector search)
def run(ctx):
    raw_q = smart_convert(ctx, ctx.get_exact('query'), str)
    nominal = extract(ctx, raw_q, 'Ekstrak nilai nominal tagihan', float)
    ppn = round(nominal * 0.11, 2)
    narrate(ctx, f'PPN 11% dihitung deterministik di CPU: Rp {ppn}')
    return f'Tagihan Rp {nominal:,.0f} | PPN 11%: Rp {ppn:,.0f}'`,
  },
];

export const INITIAL_LEDGER_ROWS: TelemetryLedgerRow[] = [
  {
    id: 1,
    requestId: "req-fin-001",
    projectSlug: "finansia",
    entryKey: null,
    executionPath: "arc_miss_build",
    role: "source",
    modelName: "anthropic/claude-opus-4.5",
    inputTokens: 980,
    outputTokens: 354,
    costUsd: 0.04124,
    counterfactualCostUsd: 0.04124,
    latencyMs: 1420.0,
    timestamp: "2026-11-22 22:10:04",
  },
  {
    id: 2,
    requestId: "req-fin-001",
    projectSlug: "finansia",
    entryKey: "query_monthly_category_expense",
    executionPath: "arc_miss_build",
    role: "builder",
    modelName: "anthropic/claude-opus-4.5",
    inputTokens: 1600,
    outputTokens: 993,
    costUsd: 0.0985,
    counterfactualCostUsd: 0.0,
    latencyMs: 2150.0,
    timestamp: "2026-11-22 22:10:07",
  },
  {
    id: 3,
    requestId: "req-fin-002",
    projectSlug: "finansia",
    entryKey: "query_monthly_category_expense",
    executionPath: "arc_hit",
    role: "extractor",
    modelName: "local/llama-3.2-1b",
    inputTokens: 96,
    outputTokens: 12,
    costUsd: 0.00000216,
    counterfactualCostUsd: 0.04124,
    latencyMs: 24.5,
    timestamp: "2026-11-22 22:12:19",
  },
  {
    id: 4,
    requestId: "req-fin-003",
    projectSlug: "finansia",
    entryKey: "query_monthly_category_expense",
    executionPath: "arc_hit",
    role: "extractor",
    modelName: "local/llama-3.2-1b",
    inputTokens: 94,
    outputTokens: 14,
    costUsd: 0.00000216,
    counterfactualCostUsd: 0.04124,
    latencyMs: 22.1,
    timestamp: "2026-11-22 22:15:40",
  },
  {
    id: 5,
    requestId: "req-fin-004",
    projectSlug: "finansia",
    entryKey: "query_monthly_category_expense",
    executionPath: "arc_hit",
    role: "extractor",
    modelName: "local/llama-3.2-1b",
    inputTokens: 98,
    outputTokens: 10,
    costUsd: 0.00000216,
    counterfactualCostUsd: 0.04124,
    latencyMs: 21.8,
    timestamp: "2026-11-22 22:18:02",
  },
  {
    id: 6,
    requestId: "req-fin-005",
    projectSlug: "finansia",
    entryKey: "query_monthly_category_expense",
    executionPath: "arc_hit",
    role: "extractor",
    modelName: "local/llama-3.2-1b",
    inputTokens: 92,
    outputTokens: 16,
    costUsd: 0.00000216,
    counterfactualCostUsd: 0.04124,
    latencyMs: 20.4,
    timestamp: "2026-11-22 22:21:11",
  },
  {
    id: 7,
    requestId: "req-fin-006",
    projectSlug: "finansia",
    entryKey: "query_monthly_category_expense",
    executionPath: "arc_hit",
    role: "extractor",
    modelName: "local/llama-3.2-1b",
    inputTokens: 95,
    outputTokens: 13,
    costUsd: 0.00000216,
    counterfactualCostUsd: 0.04124,
    latencyMs: 19.8,
    timestamp: "2026-11-22 22:24:55",
  },
  {
    id: 8,
    requestId: "req-ima-001",
    projectSlug: "ima-isa",
    entryKey: "summarize_bi_rate_news",
    executionPath: "arc_hit",
    role: "extractor",
    modelName: "local/llama-3.2-1b",
    inputTokens: 82,
    outputTokens: 8,
    costUsd: 0.0000018,
    counterfactualCostUsd: 0.0414,
    latencyMs: 18.2,
    timestamp: "2026-11-22 22:30:10",
  },
  {
    id: 9,
    requestId: "req-ima-001",
    projectSlug: "ima-isa",
    entryKey: "summarize_bi_rate_news",
    executionPath: "arc_hit",
    role: "target",
    modelName: "local/llama-3.2-1b",
    inputTokens: 55,
    outputTokens: 10,
    costUsd: 0.0000013,
    counterfactualCostUsd: 0.0,
    latencyMs: 16.4,
    timestamp: "2026-11-22 22:30:10",
  },
  {
    id: 10,
    requestId: "req-ima-002",
    projectSlug: "ima-isa",
    entryKey: "summarize_bi_rate_news",
    executionPath: "arc_hit",
    role: "converter",
    modelName: "local/llama-3.2-1b",
    inputTokens: 110,
    outputTokens: 45,
    costUsd: 0.0000031,
    counterfactualCostUsd: 0.0414,
    latencyMs: 28.0,
    timestamp: "2026-11-22 22:33:45",
  },
  {
    id: 11,
    requestId: "req-lar-001",
    projectSlug: "finansia-laravel",
    entryKey: "verify_invoice_tax_ppn",
    executionPath: "arc_hit",
    role: "extractor",
    modelName: "local/llama-3.2-1b",
    inputTokens: 78,
    outputTokens: 12,
    costUsd: 0.0000018,
    counterfactualCostUsd: 0.0365,
    latencyMs: 17.5,
    timestamp: "2026-11-22 22:40:12",
  },
  {
    id: 12,
    requestId: "req-lar-002",
    projectSlug: "finansia-laravel",
    entryKey: "verify_invoice_tax_ppn",
    executionPath: "arc_hit",
    role: "extractor",
    modelName: "local/llama-3.2-1b",
    inputTokens: 78,
    outputTokens: 12,
    costUsd: 0.0000018,
    counterfactualCostUsd: 0.0365,
    latencyMs: 16.9,
    timestamp: "2026-11-22 22:42:01",
  },
  {
    id: 13,
    requestId: "req-lar-003",
    projectSlug: "finansia-laravel",
    entryKey: "verify_invoice_tax_ppn",
    executionPath: "arc_hit",
    role: "extractor",
    modelName: "local/llama-3.2-1b",
    inputTokens: 78,
    outputTokens: 12,
    costUsd: 0.0000018,
    counterfactualCostUsd: 0.0365,
    latencyMs: 16.1,
    timestamp: "2026-11-22 22:45:19",
  },
  {
    id: 14,
    requestId: "req-lar-004",
    projectSlug: "finansia-laravel",
    entryKey: "verify_invoice_tax_ppn",
    executionPath: "arc_hit",
    role: "extractor",
    modelName: "local/llama-3.2-1b",
    inputTokens: 78,
    outputTokens: 12,
    costUsd: 0.0000018,
    counterfactualCostUsd: 0.0365,
    latencyMs: 15.8,
    timestamp: "2026-11-22 22:47:30",
  },
];
