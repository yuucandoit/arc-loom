import {
  INITIAL_FUNCTIONS,
  INITIAL_LEDGER_ROWS,
  RoutingMode,
  SemanticFunctionItem,
  TelemetryLedgerRow,
  TelemetryRole,
  computeFinOpsTotals,
  computeRoleBreakdown,
  evaluateBreakEven,
  formatUsd4,
  formatUsd8,
} from "./finopsEngine";

type ViewState = "ready" | "empty" | "loading" | "error";

interface DashboardState {
  projectFilter: string;
  routingMode: RoutingMode;
  roleFilter: "all" | TelemetryRole;
  viewState: ViewState;
  theme: "light" | "dark";
  mobileNavCollapsed: boolean;
  inspectedEntryKey: string | null;
  isLiveConnected: boolean;
  gatewayUrl: string;
  lastErrorDetail: string;
  functions: SemanticFunctionItem[];
  ledgerRows: TelemetryLedgerRow[];
}

export function initDashboard(doc: Document = document): void {
  const state: DashboardState = {
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
    ledgerRows: structuredClone(INITIAL_LEDGER_ROWS),
  };

  const gatewayUrlInput = doc.getElementById("gateway-url-input") as HTMLInputElement | null;
  const connectGatewayBtn = doc.getElementById("connect-gateway-btn") as HTMLButtonElement | null;
  const datasetBadge = doc.getElementById("dataset-badge");
  const projectSelect = doc.getElementById("project-select") as HTMLSelectElement | null;
  const routingModeSelect = doc.getElementById("routing-mode-select") as HTMLSelectElement | null;
  const viewStateSelect = doc.getElementById("view-state-select") as HTMLSelectElement | null;
  const themeToggleBtn = doc.getElementById("theme-toggle-btn") as HTMLButtonElement | null;
  const mobileNavToggle = doc.getElementById("mobile-nav-toggle") as HTMLButtonElement | null;
  const primaryNavList = doc.getElementById("primary-nav-list") as HTMLUListElement | null;

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

  const simForm = doc.getElementById("routing-sim-form") as HTMLFormElement | null;
  const simPromptInput = doc.getElementById("sim-prompt-input") as HTMLInputElement | null;
  const simResultSummary = doc.getElementById("sim-result-summary");
  const simNarrationList = doc.getElementById("sim-narration-list");

  const compilerDialog = doc.getElementById("compiler-dialog");
  const compilerDialogTitle = doc.getElementById("compiler-dialog-title");
  const compilerDialogSubtitle = doc.getElementById("compiler-dialog-subtitle");
  const modalDynamicCode = doc.getElementById("modal-dynamic-code");
  const modalCompiledCode = doc.getElementById("modal-compiled-code");
  const compileNowBtn = doc.getElementById("compile-now-btn") as HTMLButtonElement | null;
  const closeModalBtn = doc.getElementById("close-modal-btn") as HTMLButtonElement | null;

  function renderViewStateNotice(): void {
    if (!stateFeedbackContainer || !mainLedgerContent) return;

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
        if (viewStateSelect) viewStateSelect.value = "ready";
        renderAll();
      });
      return;
    }

    if (state.viewState === "error") {
      const detail =
        state.lastErrorDetail ||
        "Tidak dapat menghubungi endpoint PostgreSQL / ARC-Loom Server di http://localhost:8080/v1.";
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
        if (viewStateSelect) viewStateSelect.value = "ready";
        renderAll();
      });
    }
  }

  function renderEquationBanner(): void {
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
      metricFrontierCost.textContent = formatUsd8(
        totals.sourceCostUsd + totals.builderCostUsd
      );
    }
    if (metricFrontierSplit) {
      metricFrontierSplit.textContent = `Source: ${formatUsd4(totals.sourceCostUsd)} | Builder: ${formatUsd4(totals.builderCostUsd)}`;
    }
    if (metricCacheCost) {
      metricCacheCost.textContent = formatUsd8(totals.cacheExecutionCostUsd);
    }
  }

  function renderRegistryTable(): void {
    if (!registryTableBody) return;
    const visibleFns =
      state.projectFilter === "all"
        ? state.functions
        : state.functions.filter((f) => f.projectSlug === state.projectFilter);

    registryTableBody.innerHTML = "";
    for (const fn of visibleFns) {
      const be = evaluateBreakEven(fn);
      const tr = doc.createElement("tr");

      const runtimeBadge = fn.isCompiled
        ? `<span class="status-tag status-hit">[COMPILED O(1)]</span>`
        : `<span class="status-tag status-warn">[DYNAMIC .PY]</span>`;

      const breakEvenText =
        be.breakEvenCalls === null
          ? "N/A"
          : `${fn.hitCount} / ${be.breakEvenCalls} calls`;
      const profitTag = be.isProfitable
        ? `<span class="status-tag status-hit">[IMPASS TERCAPAI]</span>`
        : `<span class="status-tag status-warn">[KURANG ${be.remainingCalls} HIT]</span>`;

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
        const key = (btn as HTMLElement).getAttribute("data-entry");
        if (key) openCompilerModal(key);
      });
    });
  }

  function renderRoleMatrixAndLedger(): void {
    const roleMap = computeRoleBreakdown(state.ledgerRows, state.projectFilter);
    if (roleSummaryStrip) {
      const roles: TelemetryRole[] = [
        "source",
        "builder",
        "target",
        "extractor",
        "converter",
      ];
      roleSummaryStrip.innerHTML = roles
        .map((role) => {
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
        })
        .join("");
    }

    if (!ledgerTableBody) return;
    let rows =
      state.projectFilter === "all"
        ? state.ledgerRows
        : state.ledgerRows.filter((r) => r.projectSlug === state.projectFilter);
    if (state.roleFilter !== "all") {
      rows = rows.filter((r) => r.role === state.roleFilter);
    }

    ledgerTableBody.innerHTML = rows
      .slice()
      .reverse()
      .map((row) => {
        const pathClass =
          row.executionPath === "arc_hit" ? "status-hit" : "status-warn";
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
      })
      .join("");
  }

  function openCompilerModal(entryKey: string): void {
    const fn = state.functions.find((f) => f.entryKey === entryKey);
    if (!fn || !compilerDialog) return;
    state.inspectedEntryKey = entryKey;

    if (compilerDialogTitle) {
      compilerDialogTitle.textContent = `Kompilator Statis O(1): ${fn.entryKey}`;
    }
    if (compilerDialogSubtitle) {
      compilerDialogSubtitle.textContent = fn.isCompiled
        ? "Status: Sudah dikompilasi ke modul statis O(1) (tanpa pencarian vektor saat runtime)."
        : "Status: Masih berupa skrip dinamis (.py). Klik tombol di kanan untuk membekukan sub-call do() dan load().";
    }
    if (modalDynamicCode) {
      modalDynamicCode.textContent = fn.dynamicCode;
    }
    if (modalCompiledCode) {
      modalCompiledCode.textContent = fn.compiledCode;
    }
    if (compileNowBtn) {
      compileNowBtn.textContent = fn.isCompiled
        ? "Sudah Terkompilasi O(1)"
        : "Kompilasi ke O(1) Sekarang";
    }
    compilerDialog.removeAttribute("hidden");
  }

  function closeCompilerModal(): void {
    if (!compilerDialog) return;
    compilerDialog.setAttribute("hidden", "true");
    state.inspectedEntryKey = null;
  }

  function renderAll(): void {
    if (datasetBadge) {
      datasetBadge.textContent = state.isLiveConnected
        ? `[LIVE GATEWAY: ${state.gatewayUrl}]`
        : "[SIMULASI BLUEPRINT]";
    }
    renderViewStateNotice();
    renderEquationBanner();
    renderRegistryTable();
    renderRoleMatrixAndLedger();
  }

  async function syncFromLiveGateway(): Promise<void> {
    const baseUrl = (gatewayUrlInput?.value || state.gatewayUrl).replace(/\/+$/, "");
    state.gatewayUrl = baseUrl;
    state.viewState = "loading";
    if (viewStateSelect) viewStateSelect.value = "loading";
    renderAll();

    try {
      const resp = await fetch(`${baseUrl}/arc/telemetry`);
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status} dari ${baseUrl}/arc/telemetry`);
      }
      const data = (await resp.json()) as {
        projects?: string[];
        functions?: SemanticFunctionItem[];
        ledgerRows?: TelemetryLedgerRow[];
      };
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

      state.viewState =
        state.functions.length === 0 && state.ledgerRows.length === 0
          ? "empty"
          : "ready";
      if (viewStateSelect) viewStateSelect.value = state.viewState;
      renderAll();
    } catch (err) {
      state.isLiveConnected = false;
      state.lastErrorDetail =
        err instanceof Error ? err.message : "Gagal menghubungi Gateway Server.";
      state.viewState = "error";
      if (viewStateSelect) viewStateSelect.value = "error";
      renderAll();
    }
  }

  connectGatewayBtn?.addEventListener("click", () => {
    void syncFromLiveGateway();
  });

  projectSelect?.addEventListener("change", () => {
    state.projectFilter = projectSelect.value;
    renderAll();
  });

  routingModeSelect?.addEventListener("change", () => {
    state.routingMode = routingModeSelect.value as RoutingMode;
  });

  viewStateSelect?.addEventListener("change", () => {
    state.viewState = viewStateSelect.value as ViewState;
    renderAll();
  });

  themeToggleBtn?.addEventListener("click", () => {
    state.theme = state.theme === "light" ? "dark" : "light";
    doc.documentElement.setAttribute("data-theme", state.theme);
    const isDark = state.theme === "dark";
    themeToggleBtn.setAttribute("aria-pressed", String(isDark));
    themeToggleBtn.textContent = isDark
      ? "Tema: Karbon Gelap"
      : "Tema: Kertas Terang";
  });

  mobileNavToggle?.addEventListener("click", () => {
    state.mobileNavCollapsed = !state.mobileNavCollapsed;
    primaryNavList?.setAttribute(
      "data-collapsed",
      String(state.mobileNavCollapsed)
    );
    mobileNavToggle.setAttribute(
      "aria-expanded",
      String(!state.mobileNavCollapsed)
    );
  });

  const roleFilterBtns = doc.querySelectorAll(".filter-btn");
  roleFilterBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const role = (btn as HTMLElement).getAttribute("data-role") as
        | "all"
        | TelemetryRole;
      state.roleFilter = role || "all";
      roleFilterBtns.forEach((b) =>
        b.setAttribute("aria-pressed", String(b === btn))
      );
      renderRoleMatrixAndLedger();
    });
  });

  const presetBtns = doc.querySelectorAll(".preset-prompt-btn");
  presetBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const promptText = (btn as HTMLElement).getAttribute("data-prompt") || "";
      const proj = (btn as HTMLElement).getAttribute("data-project") || "all";
      if (simPromptInput) simPromptInput.value = promptText;
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
    if (!promptText) return;

    const targetProject =
      state.projectFilter === "all" ? "finansia" : state.projectFilter;
    const reqId = `req-sim-${state.ledgerRows.length + 1}`;
    const lower = promptText.toLowerCase();

    let matchedFn: SemanticFunctionItem | undefined;
    if (lower.includes("pengeluaran") || lower.includes("makan")) {
      matchedFn = state.functions.find(
        (f) => f.entryKey === "query_monthly_category_expense"
      );
    } else if (lower.includes("suku bunga") || lower.includes("sentimen")) {
      matchedFn = state.functions.find(
        (f) => f.entryKey === "summarize_bi_rate_news"
      );
    } else if (lower.includes("ppn") || lower.includes("faktur")) {
      matchedFn = state.functions.find(
        (f) => f.entryKey === "verify_invoice_tax_ppn"
      );
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
        latencyMs: 1340.0,
        timestamp: "2026-11-22 23:25:00",
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
        timestamp: "2026-11-22 23:25:00",
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
        latencyMs: 1390.0,
        timestamp: "2026-11-22 23:25:00",
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
      latencyMs: 1480.0,
      timestamp: "2026-11-22 23:25:00",
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
    if (!state.inspectedEntryKey) return;
    const fn = state.functions.find(
      (f) => f.entryKey === state.inspectedEntryKey
    );
    if (!fn) return;
    if (state.isLiveConnected) {
      try {
        const resp = await fetch(`${state.gatewayUrl}/arc/compile`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ entryKey: fn.entryKey }),
        });
        if (resp.ok) {
          const data = (await resp.json()) as { compiledCode?: string };
          if (data.compiledCode) {
            fn.compiledCode = data.compiledCode;
          }
        }
      } catch {
        // Keep local optimistic compile state if live endpoint is unreachable.
      }
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
