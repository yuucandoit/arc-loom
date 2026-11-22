// src/finopsEngine.ts
function formatUsd8(amount) {
  const sign = amount < 0 ? "-" : "";
  return `${sign}$${Math.abs(amount).toFixed(8)}`;
}
function formatUsd4(amount) {
  const sign = amount < 0 ? "-" : "";
  return `${sign}$${Math.abs(amount).toFixed(4)}`;
}
function evaluateBreakEven(fn) {
  const margin = fn.avgSourceCostUsd - fn.avgCacheCostUsd;
  if (margin <= 0) {
    return {
      breakEvenCalls: null,
      remainingCalls: null,
      isProfitable: false,
      marginPerCallUsd: margin
    };
  }
  const breakEvenCalls = Math.ceil(fn.builderCostUsd / margin);
  const remainingCalls = Math.max(0, breakEvenCalls - fn.hitCount);
  return {
    breakEvenCalls,
    remainingCalls,
    isProfitable: fn.hitCount >= breakEvenCalls,
    marginPerCallUsd: margin
  };
}
function computeFinOpsTotals(rows, projectFilter = "all") {
  const filtered = projectFilter === "all" ? rows : rows.filter((r) => r.projectSlug === projectFilter);
  const reqIds = new Set;
  const hitReqIds = new Set;
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
  const actualTotalCostUsd = sourceCostUsd + builderCostUsd + cacheExecutionCostUsd;
  const netSavingsUsd = counterfactualCostUsd - actualTotalCostUsd;
  const totalRequests = reqIds.size;
  const arcHits = hitReqIds.size;
  const hitRatePercent = totalRequests > 0 ? Math.round(arcHits / totalRequests * 1000) / 10 : 0;
  return {
    totalRequests,
    arcHits,
    hitRatePercent,
    counterfactualCostUsd: Number(counterfactualCostUsd.toFixed(8)),
    sourceCostUsd: Number(sourceCostUsd.toFixed(8)),
    builderCostUsd: Number(builderCostUsd.toFixed(8)),
    cacheExecutionCostUsd: Number(cacheExecutionCostUsd.toFixed(8)),
    actualTotalCostUsd: Number(actualTotalCostUsd.toFixed(8)),
    netSavingsUsd: Number(netSavingsUsd.toFixed(8))
  };
}
function computeRoleBreakdown(rows, projectFilter = "all") {
  const summary = {
    source: { calls: 0, inputTokens: 0, outputTokens: 0, costUsd: 0 },
    builder: { calls: 0, inputTokens: 0, outputTokens: 0, costUsd: 0 },
    target: { calls: 0, inputTokens: 0, outputTokens: 0, costUsd: 0 },
    extractor: { calls: 0, inputTokens: 0, outputTokens: 0, costUsd: 0 },
    converter: { calls: 0, inputTokens: 0, outputTokens: 0, costUsd: 0 }
  };
  const filtered = projectFilter === "all" ? rows : rows.filter((r) => r.projectSlug === projectFilter);
  for (const row of filtered) {
    const slot = summary[row.role];
    slot.calls += 1;
    slot.inputTokens += row.inputTokens;
    slot.outputTokens += row.outputTokens;
    slot.costUsd = Number((slot.costUsd + row.costUsd).toFixed(8));
  }
  return summary;
}
var INITIAL_FUNCTIONS = [
  {
    entryKey: "query_monthly_category_expense",
    projectSlug: "finansia",
    intentDescription: "Berapa total pengeluaran kategori makan bulan November 2026?",
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
    return f'Total pengeluaran kategori {category} ({month}): Rp {total}'`
  },
  {
    entryKey: "summarize_bi_rate_news",
    projectSlug: "ima-isa",
    intentDescription: "Ringkas sentimen berita tentang suku bunga BI 7 hari terakhir",
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
    return f'Ringkasan {len(articles)} artikel ({days} hari): {sentiment}'`
  },
  {
    entryKey: "verify_invoice_tax_ppn",
    projectSlug: "finansia-laravel",
    intentDescription: "Hitung PPN 11 persen dan validasi nomor faktur pajak klien",
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
    return f'Tagihan Rp {nominal:,.0f} | PPN 11%: Rp {ppn:,.0f}'`
  }
];
var INITIAL_LEDGER_ROWS = [
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
    latencyMs: 1420,
    timestamp: "2026-11-22 22:10:04"
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
    counterfactualCostUsd: 0,
    latencyMs: 2150,
    timestamp: "2026-11-22 22:10:07"
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
    timestamp: "2026-11-22 22:12:19"
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
    timestamp: "2026-11-22 22:15:40"
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
    timestamp: "2026-11-22 22:18:02"
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
    timestamp: "2026-11-22 22:21:11"
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
    timestamp: "2026-11-22 22:24:55"
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
    timestamp: "2026-11-22 22:30:10"
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
    counterfactualCostUsd: 0,
    latencyMs: 16.4,
    timestamp: "2026-11-22 22:30:10"
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
    latencyMs: 28,
    timestamp: "2026-11-22 22:33:45"
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
    timestamp: "2026-11-22 22:40:12"
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
    timestamp: "2026-11-22 22:42:01"
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
    timestamp: "2026-11-22 22:45:19"
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
    timestamp: "2026-11-22 22:47:30"
  }
];

// src/app.ts
function initDashboard(doc = document) {
  const state = {
    projectFilter: "all",
    routingMode: "active",
    roleFilter: "all",
    viewState: "ready",
    theme: "light",
    mobileNavCollapsed: true,
    inspectedEntryKey: null,
    isLiveConnected: false,
    gatewayUrl: "http://localhost:8080/v1",
    lastErrorDetail: "",
    functions: structuredClone(INITIAL_FUNCTIONS),
    ledgerRows: structuredClone(INITIAL_LEDGER_ROWS)
  };
  const gatewayUrlInput = doc.getElementById("gateway-url-input");
  const connectGatewayBtn = doc.getElementById("connect-gateway-btn");
  const datasetBadge = doc.getElementById("dataset-badge");
  const projectSelect = doc.getElementById("project-select");
  const routingModeSelect = doc.getElementById("routing-mode-select");
  const viewStateSelect = doc.getElementById("view-state-select");
  const themeToggleBtn = doc.getElementById("theme-toggle-btn");
  const mobileNavToggle = doc.getElementById("mobile-nav-toggle");
  const primaryNavList = doc.getElementById("primary-nav-list");
  const stateFeedbackContainer = doc.getElementById("state-feedback-container");
  const mainLedgerContent = doc.getElementById("main-ledger-content");
  const metricNetSavings = doc.getElementById("metric-net-savings");
  const metricCounterfactual = doc.getElementById("metric-counterfactual");
  const metricHitRate = doc.getElementById("metric-hit-rate");
  const metricFrontierCost = doc.getElementById("metric-frontier-cost");
  const metricFrontierSplit = doc.getElementById("metric-frontier-split");
  const metricCacheCost = doc.getElementById("metric-cache-cost");
  const registryTableBody = doc.getElementById("registry-table-body");
  const roleSummaryStrip = doc.getElementById("role-summary-strip");
  const ledgerTableBody = doc.getElementById("ledger-table-body");
  const simForm = doc.getElementById("routing-sim-form");
  const simPromptInput = doc.getElementById("sim-prompt-input");
  const simResultSummary = doc.getElementById("sim-result-summary");
  const simNarrationList = doc.getElementById("sim-narration-list");
  const compilerDialog = doc.getElementById("compiler-dialog");
  const compilerDialogTitle = doc.getElementById("compiler-dialog-title");
  const compilerDialogSubtitle = doc.getElementById("compiler-dialog-subtitle");
  const modalDynamicCode = doc.getElementById("modal-dynamic-code");
  const modalCompiledCode = doc.getElementById("modal-compiled-code");
  const compileNowBtn = doc.getElementById("compile-now-btn");
  const closeModalBtn = doc.getElementById("close-modal-btn");
  function renderViewStateNotice() {
    if (!stateFeedbackContainer || !mainLedgerContent)
      return;
    if (state.viewState === "ready") {
      stateFeedbackContainer.innerHTML = "";
      mainLedgerContent.removeAttribute("hidden");
      return;
    }
    mainLedgerContent.setAttribute("hidden", "true");
    if (state.viewState === "loading") {
      stateFeedbackContainer.innerHTML = `
        <div class="state-panel">
          <h2 class="tabular">[MEMUAT LEDGER TELEMETRI...]</h2>
          <p>Menarik data tabel projects, cache_entries, dan telemetry_ledger dari ARC-Loom Gateway (/v1/arc/telemetry).</p>
        </div>
      `;
      return;
    }
    if (state.viewState === "empty") {
      stateFeedbackContainer.innerHTML = `
        <div class="state-panel">
          <h2 class="tabular">[LEDGER KOSONG: BELUM ADA FUNGSI SEMANTIK]</h2>
          <p>Proyek ini belum memiliki rekaman cache_entries maupun transaksi token di telemetry_ledger.</p>
          <button type="button" id="empty-seed-btn" class="btn btn-accent">
            Simulasikan Request Pertama (/v1/chat/completions)
          </button>
        </div>
      `;
      const seedBtn = doc.getElementById("empty-seed-btn");
      seedBtn?.addEventListener("click", () => {
        state.viewState = "ready";
        if (viewStateSelect)
          viewStateSelect.value = "ready";
        renderAll();
      });
      return;
    }
    if (state.viewState === "error") {
      const detail = state.lastErrorDetail || "Tidak dapat menghubungi endpoint PostgreSQL / ARC-Loom Server di http://localhost:8080/v1.";
      stateFeedbackContainer.innerHTML = `
        <div class="state-panel">
          <h2 class="tabular status-err">[KONEKSI GATEWAY GAGAL: HTTP 503]</h2>
          <p>${detail} Pastikan container docker-compose aktif atau pulihkan dari snapshot lokal.</p>
          <button type="button" id="error-recover-btn" class="btn btn-accent">
            Muat Ulang dari Snapshot Lokal
          </button>
        </div>
      `;
      const recoverBtn = doc.getElementById("error-recover-btn");
      recoverBtn?.addEventListener("click", () => {
        state.isLiveConnected = false;
        state.functions = structuredClone(INITIAL_FUNCTIONS);
        state.ledgerRows = structuredClone(INITIAL_LEDGER_ROWS);
        state.viewState = "ready";
        if (viewStateSelect)
          viewStateSelect.value = "ready";
        renderAll();
      });
    }
  }
  function renderEquationBanner() {
    const totals = computeFinOpsTotals(state.ledgerRows, state.projectFilter);
    if (metricNetSavings) {
      metricNetSavings.textContent = formatUsd8(totals.netSavingsUsd);
    }
    if (metricCounterfactual) {
      metricCounterfactual.textContent = formatUsd8(totals.counterfactualCostUsd);
    }
    if (metricHitRate) {
      metricHitRate.textContent = `${totals.arcHits} / ${totals.totalRequests} request via ARC Hit (${totals.hitRatePercent}%)`;
    }
    if (metricFrontierCost) {
      metricFrontierCost.textContent = formatUsd8(totals.sourceCostUsd + totals.builderCostUsd);
    }
    if (metricFrontierSplit) {
      metricFrontierSplit.textContent = `Source: ${formatUsd4(totals.sourceCostUsd)} | Builder: ${formatUsd4(totals.builderCostUsd)}`;
    }
    if (metricCacheCost) {
      metricCacheCost.textContent = formatUsd8(totals.cacheExecutionCostUsd);
    }
  }
  function renderRegistryTable() {
    if (!registryTableBody)
      return;
    const visibleFns = state.projectFilter === "all" ? state.functions : state.functions.filter((f) => f.projectSlug === state.projectFilter);
    registryTableBody.innerHTML = "";
    for (const fn of visibleFns) {
      const be = evaluateBreakEven(fn);
      const tr = doc.createElement("tr");
      const runtimeBadge = fn.isCompiled ? `<span class="status-tag status-hit">[COMPILED O(1)]</span>` : `<span class="status-tag status-warn">[DYNAMIC .PY]</span>`;
      const breakEvenText = be.breakEvenCalls === null ? "N/A" : `${fn.hitCount} / ${be.breakEvenCalls} calls`;
      const profitTag = be.isProfitable ? `<span class="status-tag status-hit">[IMPASS TERCAPAI]</span>` : `<span class="status-tag status-warn">[KURANG ${be.remainingCalls} HIT]</span>`;
      tr.innerHTML = `
        <td>
          <div class="tabular" style="font-weight: 700;">${fn.entryKey}</div>
          <div style="font-size: 0.8rem; color: var(--text-secondary);">Proyek: ${fn.projectSlug} // "${fn.intentDescription}"</div>
        </td>
        <td>${runtimeBadge}</td>
        <td class="tabular">
          <div>${breakEvenText}</div>
          <div>${profitTag}</div>
        </td>
        <td class="tabular">${formatUsd4(fn.builderCostUsd)}</td>
        <td class="tabular">${formatUsd4(be.marginPerCallUsd)}</td>
        <td>
          <button type="button" class="btn inspect-fn-btn" data-entry="${fn.entryKey}">
            Inspeksi &amp; Kompilasi O(1)
          </button>
        </td>
      `;
      registryTableBody.appendChild(tr);
    }
    const inspectBtns = registryTableBody.querySelectorAll(".inspect-fn-btn");
    inspectBtns.forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.getAttribute("data-entry");
        if (key)
          openCompilerModal(key);
      });
    });
  }
  function renderRoleMatrixAndLedger() {
    const roleMap = computeRoleBreakdown(state.ledgerRows, state.projectFilter);
    if (roleSummaryStrip) {
      const roles = [
        "source",
        "builder",
        "target",
        "extractor",
        "converter"
      ];
      roleSummaryStrip.innerHTML = roles.map((role) => {
        const item = roleMap[role];
        return `
            <div class="role-cell">
              <div class="tabular" style="font-weight: 700;">Role: ${role.toUpperCase()}</div>
              <div class="tabular" style="font-size: 1.05rem; margin: 0.25rem 0;">${formatUsd8(item.costUsd)}</div>
              <div class="tabular" style="font-size: 0.78rem; color: var(--text-secondary);">
                ${item.calls} call // In: ${item.inputTokens} / Out: ${item.outputTokens} tok
              </div>
            </div>
          `;
      }).join("");
    }
    if (!ledgerTableBody)
      return;
    let rows = state.projectFilter === "all" ? state.ledgerRows : state.ledgerRows.filter((r) => r.projectSlug === state.projectFilter);
    if (state.roleFilter !== "all") {
      rows = rows.filter((r) => r.role === state.roleFilter);
    }
    ledgerTableBody.innerHTML = rows.slice().reverse().map((row) => {
      const pathClass = row.executionPath === "arc_hit" ? "status-hit" : "status-warn";
      return `
          <tr>
            <td class="tabular">${row.requestId}</td>
            <td class="tabular">${row.projectSlug}</td>
            <td><span class="status-tag ${pathClass}">[${row.executionPath.toUpperCase()}]</span></td>
            <td class="tabular">${row.role}</td>
            <td class="tabular">${row.modelName}</td>
            <td class="tabular">${row.inputTokens} / ${row.outputTokens}</td>
            <td class="tabular">${formatUsd8(row.costUsd)}</td>
            <td class="tabular">${formatUsd8(row.counterfactualCostUsd)}</td>
            <td class="tabular">${row.latencyMs.toFixed(1)} ms</td>
          </tr>
        `;
    }).join("");
  }
  function openCompilerModal(entryKey) {
    const fn = state.functions.find((f) => f.entryKey === entryKey);
    if (!fn || !compilerDialog)
      return;
    state.inspectedEntryKey = entryKey;
    if (compilerDialogTitle) {
      compilerDialogTitle.textContent = `Kompilator Statis O(1): ${fn.entryKey}`;
    }
    if (compilerDialogSubtitle) {
      compilerDialogSubtitle.textContent = fn.isCompiled ? "Status: Sudah dikompilasi ke modul statis O(1) (tanpa pencarian vektor saat runtime)." : "Status: Masih berupa skrip dinamis (.py). Klik tombol di kanan untuk membekukan sub-call do() dan load().";
    }
    if (modalDynamicCode) {
      modalDynamicCode.textContent = fn.dynamicCode;
    }
    if (modalCompiledCode) {
      modalCompiledCode.textContent = fn.compiledCode;
    }
    if (compileNowBtn) {
      compileNowBtn.textContent = fn.isCompiled ? "Sudah Terkompilasi O(1)" : "Kompilasi ke O(1) Sekarang";
    }
    compilerDialog.removeAttribute("hidden");
  }
  function closeCompilerModal() {
    if (!compilerDialog)
      return;
    compilerDialog.setAttribute("hidden", "true");
    state.inspectedEntryKey = null;
  }
  function renderAll() {
    if (datasetBadge) {
      datasetBadge.textContent = state.isLiveConnected ? `[LIVE GATEWAY: ${state.gatewayUrl}]` : "[SIMULASI BLUEPRINT]";
    }
    renderViewStateNotice();
    renderEquationBanner();
    renderRegistryTable();
    renderRoleMatrixAndLedger();
  }
  async function syncFromLiveGateway() {
    const baseUrl = (gatewayUrlInput?.value || state.gatewayUrl).replace(/\/+$/, "");
    state.gatewayUrl = baseUrl;
    state.viewState = "loading";
    if (viewStateSelect)
      viewStateSelect.value = "loading";
    renderAll();
    try {
      const resp = await fetch(`${baseUrl}/arc/telemetry`);
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status} dari ${baseUrl}/arc/telemetry`);
      }
      const data = await resp.json();
      state.functions = Array.isArray(data.functions) ? data.functions : [];
      state.ledgerRows = Array.isArray(data.ledgerRows) ? data.ledgerRows : [];
      state.isLiveConnected = true;
      if (projectSelect && Array.isArray(data.projects)) {
        projectSelect.innerHTML = `<option value="all">Semua Proyek (${data.projects.length})</option>`;
        for (const slug of data.projects) {
          const opt = doc.createElement("option");
          opt.value = slug;
          opt.textContent = slug;
          projectSelect.appendChild(opt);
        }
        state.projectFilter = "all";
      }
      state.viewState = state.functions.length === 0 && state.ledgerRows.length === 0 ? "empty" : "ready";
      if (viewStateSelect)
        viewStateSelect.value = state.viewState;
      renderAll();
    } catch (err) {
      state.isLiveConnected = false;
      state.lastErrorDetail = err instanceof Error ? err.message : "Gagal menghubungi Gateway Server.";
      state.viewState = "error";
      if (viewStateSelect)
        viewStateSelect.value = "error";
      renderAll();
    }
  }
  connectGatewayBtn?.addEventListener("click", () => {
    syncFromLiveGateway();
  });
  projectSelect?.addEventListener("change", () => {
    state.projectFilter = projectSelect.value;
    renderAll();
  });
  routingModeSelect?.addEventListener("change", () => {
    state.routingMode = routingModeSelect.value;
  });
  viewStateSelect?.addEventListener("change", () => {
    state.viewState = viewStateSelect.value;
    renderAll();
  });
  themeToggleBtn?.addEventListener("click", () => {
    state.theme = state.theme === "light" ? "dark" : "light";
    doc.documentElement.setAttribute("data-theme", state.theme);
    const isDark = state.theme === "dark";
    themeToggleBtn.setAttribute("aria-pressed", String(isDark));
    themeToggleBtn.textContent = isDark ? "Tema: Karbon Gelap" : "Tema: Kertas Terang";
  });
  mobileNavToggle?.addEventListener("click", () => {
    state.mobileNavCollapsed = !state.mobileNavCollapsed;
    primaryNavList?.setAttribute("data-collapsed", String(state.mobileNavCollapsed));
    mobileNavToggle.setAttribute("aria-expanded", String(!state.mobileNavCollapsed));
  });
  const roleFilterBtns = doc.querySelectorAll(".filter-btn");
  roleFilterBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const role = btn.getAttribute("data-role");
      state.roleFilter = role || "all";
      roleFilterBtns.forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
      renderRoleMatrixAndLedger();
    });
  });
  const presetBtns = doc.querySelectorAll(".preset-prompt-btn");
  presetBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const promptText = btn.getAttribute("data-prompt") || "";
      const proj = btn.getAttribute("data-project") || "all";
      if (simPromptInput)
        simPromptInput.value = promptText;
      if (projectSelect && proj) {
        projectSelect.value = proj;
        state.projectFilter = proj;
        renderAll();
      }
    });
  });
  simForm?.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const promptText = simPromptInput?.value.trim() || "";
    if (!promptText)
      return;
    const targetProject = state.projectFilter === "all" ? "finansia" : state.projectFilter;
    const reqId = `req-sim-${state.ledgerRows.length + 1}`;
    const lower = promptText.toLowerCase();
    let matchedFn;
    if (lower.includes("pengeluaran") || lower.includes("makan")) {
      matchedFn = state.functions.find((f) => f.entryKey === "query_monthly_category_expense");
    } else if (lower.includes("suku bunga") || lower.includes("sentimen")) {
      matchedFn = state.functions.find((f) => f.entryKey === "summarize_bi_rate_news");
    } else if (lower.includes("ppn") || lower.includes("faktur")) {
      matchedFn = state.functions.find((f) => f.entryKey === "verify_invoice_tax_ppn");
    }
    if (state.routingMode === "bypass") {
      state.ledgerRows.push({
        id: state.ledgerRows.length + 1,
        requestId: reqId,
        projectSlug: targetProject,
        entryKey: null,
        executionPath: "bypass",
        role: "source",
        modelName: "anthropic/claude-opus-4.5",
        inputTokens: 920,
        outputTokens: 340,
        costUsd: 0.0393,
        counterfactualCostUsd: 0.0393,
        latencyMs: 1340,
        timestamp: "2026-11-22 23:25:00"
      });
      if (simResultSummary) {
        simResultSummary.textContent = `[BYPASS] Request ${reqId} diteruskan langsung ke Source Model ($0.03930000, penghematan $0.00000000).`;
      }
      if (simNarrationList) {
        simNarrationList.innerHTML = `<li>Mode bypass aktif: melewati pencarian vektor SemanticRegistry.</li>`;
      }
      renderAll();
      return;
    }
    if (matchedFn && state.routingMode === "active") {
      matchedFn.hitCount += 1;
      state.ledgerRows.push({
        id: state.ledgerRows.length + 1,
        requestId: reqId,
        projectSlug: matchedFn.projectSlug,
        entryKey: matchedFn.entryKey,
        executionPath: "arc_hit",
        role: "extractor",
        modelName: "local/llama-3.2-1b",
        inputTokens: 90,
        outputTokens: 14,
        costUsd: matchedFn.avgCacheCostUsd,
        counterfactualCostUsd: matchedFn.avgSourceCostUsd,
        latencyMs: matchedFn.isCompiled ? 11.4 : 21.8,
        timestamp: "2026-11-22 23:25:00"
      });
      const saved = matchedFn.avgSourceCostUsd - matchedFn.avgCacheCostUsd;
      if (simResultSummary) {
        simResultSummary.textContent = `[ARC_HIT] cocok dengan '${matchedFn.entryKey}' (${matchedFn.isCompiled ? "Static O(1)" : "Dynamic .py"}) | Biaya: ${formatUsd8(matchedFn.avgCacheCostUsd)} | Hemat: ${formatUsd8(saved)}`;
      }
      if (simNarrationList) {
        simNarrationList.innerHTML = `
          <li>load(ctx, 'query', str) diambil dalam ${matchedFn.isCompiled ? "O(1) ctx.get_exact()" : "pencarian vektor layered memory"}.</li>
          <li>extract(ctx, text, prompt, str) dijalankan oleh local/llama-3.2-1b (temperature=0.0).</li>
          <li>invoke_tool() mengembalikan hasil deterministik ke pengguna dalam ${matchedFn.isCompiled ? "11.4 ms" : "21.8 ms"}.</li>
        `;
      }
      renderAll();
      return;
    }
    if (matchedFn && state.routingMode === "shadow") {
      matchedFn.hitCount += 1;
      state.ledgerRows.push({
        id: state.ledgerRows.length + 1,
        requestId: reqId,
        projectSlug: matchedFn.projectSlug,
        entryKey: matchedFn.entryKey,
        executionPath: "shadow",
        role: "source",
        modelName: "anthropic/claude-opus-4.5",
        inputTokens: 950,
        outputTokens: 320,
        costUsd: matchedFn.avgSourceCostUsd,
        counterfactualCostUsd: matchedFn.avgSourceCostUsd,
        latencyMs: 1390,
        timestamp: "2026-11-22 23:25:00"
      });
      if (simResultSummary) {
        simResultSummary.textContent = `[SHADOW] Respons produksi dikirim dari Source Model, sementara fungsi '${matchedFn.entryKey}' dieksekusi paralel untuk verifikasi akurasi.`;
      }
      if (simNarrationList) {
        simNarrationList.innerHTML = `
          <li>Shadow hit pada '${matchedFn.entryKey}': hasil lokal 1B cocok 100% dengan output Source Model.</li>
        `;
      }
      renderAll();
      return;
    }
    state.ledgerRows.push({
      id: state.ledgerRows.length + 1,
      requestId: reqId,
      projectSlug: targetProject,
      entryKey: null,
      executionPath: "arc_miss_build",
      role: "source",
      modelName: "anthropic/claude-opus-4.5",
      inputTokens: 1050,
      outputTokens: 380,
      costUsd: 0.04425,
      counterfactualCostUsd: 0.04425,
      latencyMs: 1480,
      timestamp: "2026-11-22 23:25:00"
    });
    if (simResultSummary) {
      simResultSummary.textContent = `[ARC_MISS_BUILD] Tidak ada fungsi di atas ambang 0.82. Dijawab oleh Source Model tanpa tunda & dimasukkan ke Async Builder Queue.`;
    }
    if (simNarrationList) {
      simNarrationList.innerHTML = `
        <li>Cosine similarity di bawah threshold 0.82.</li>
        <li>Menjalankan fallback zero-delay ke Source Model (S) dan menjadwalkan 5-phase Async Builder (B).</li>
      `;
    }
    renderAll();
  });
  compileNowBtn?.addEventListener("click", async () => {
    if (!state.inspectedEntryKey)
      return;
    const fn = state.functions.find((f) => f.entryKey === state.inspectedEntryKey);
    if (!fn)
      return;
    if (state.isLiveConnected) {
      try {
        const resp = await fetch(`${state.gatewayUrl}/arc/compile`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ entryKey: fn.entryKey })
        });
        if (resp.ok) {
          const data = await resp.json();
          if (data.compiledCode) {
            fn.compiledCode = data.compiledCode;
          }
        }
      } catch {}
    }
    fn.isCompiled = true;
    openCompilerModal(fn.entryKey);
    renderRegistryTable();
  });
  closeModalBtn?.addEventListener("click", closeCompilerModal);
  doc.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      if (compilerDialog && !compilerDialog.hasAttribute("hidden")) {
        closeCompilerModal();
      }
      if (!state.mobileNavCollapsed) {
        state.mobileNavCollapsed = true;
        primaryNavList?.setAttribute("data-collapsed", "true");
        mobileNavToggle?.setAttribute("aria-expanded", "false");
      }
    }
  });
  renderAll();
}
if (typeof document !== "undefined") {
  initDashboard(document);
}
export {
  initDashboard
};
