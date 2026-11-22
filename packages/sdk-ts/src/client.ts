import {
  ArcToolRegistry,
  ToolBridge,
  ToolWebhookResult,
  createToolBridge,
} from "./toolServer";

export type ArcRoutingMode = "bypass" | "shadow" | "active";

export interface ArcModelRolesConfig {
  source: string;
  builder?: string;
  target?: string;
}

export interface ArcClientConfig {
  gatewayUrl: string;
  project: string;
  mode?: ArcRoutingMode;
  models: ArcModelRolesConfig;
  tools?: ArcToolRegistry;
  toolDescriptions?: Record<string, string>;
  toolCallbackUrl?: string;
  toolCallbackSecret?: string;
  apiKey?: string;
  fetchImpl?: typeof fetch;
}

export interface ArcChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface ArcChatRequest {
  prompt?: string;
  messages?: ArcChatMessage[];
  mode?: ArcRoutingMode;
  params?: Record<string, unknown>;
  temperature?: number;
}

export interface ArcTelemetry {
  requestId: string;
  project: string;
  path: "arc_hit" | "arc_miss_build" | "bypass" | "shadow";
  matchedEntryKey: string | null;
  similarityScore: number;
  actualCostUsd: number;
  counterfactualCostUsd: number;
  savedUsd: number;
  latencyMs: number;
  narrationTrace: string[];
  autoBuiltEntries?: string[];
}

export interface ArcChatResponse {
  id: string;
  answer: string;
  model: string;
  telemetry: ArcTelemetry;
  usage: {
    promptTokens: number;
    completionTokens: number;
    totalTokens: number;
  };
}

export interface ArcFunctionBreakEven {
  entryKey: string;
  intentDescription: string;
  hitCount: number;
  builderCostUsd: number;
  avgSourceCostUsd: number;
  avgCacheCostUsd: number;
  breakEvenCalls: number | null;
  isProfitable: boolean;
}

export interface ArcProjectTelemetry {
  project: string;
  totalRequests: number;
  arcHits: number;
  counterfactualCostUsd: number;
  sourceCostUsd: number;
  builderCostUsd: number;
  cacheExecutionCostUsd: number;
  actualTotalCostUsd: number;
  netSavingsUsd: number;
  functions: ArcFunctionBreakEven[];
}

export interface ArcClient {
  chat(request: ArcChatRequest): Promise<ArcChatResponse>;
  syncTools(): Promise<{ project: string; registeredTools: string[] }>;
  triggerBuild(): Promise<{ processed: number; artifacts: unknown[] }>;
  getTelemetry(): Promise<ArcProjectTelemetry>;
  handleToolWebhook(
    rawBody: string | Uint8Array,
    signatureHeader?: string | null
  ): Promise<ToolWebhookResult>;
  toolBridge: ToolBridge;
}

export function createArcClient(config: ArcClientConfig): ArcClient {
  const baseUrl = config.gatewayUrl.replace(/\/+$/, "");
  const defaultMode: ArcRoutingMode = config.mode ?? "active";
  const tools = config.tools ?? {};
  const fetchFn = config.fetchImpl ?? globalThis.fetch;

  const toolBridge = createToolBridge({
    tools,
    secret: config.toolCallbackSecret,
    project: config.project,
  });

  function buildHeaders(modeOverride?: ArcRoutingMode): Record<string, string> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      "X-ARC-Project": config.project,
      "X-ARC-Mode": modeOverride ?? defaultMode,
      "X-ARC-Source-Model": config.models.source,
      "X-ARC-Builder-Model": config.models.builder ?? config.models.source,
      "X-ARC-Target-Model": config.models.target ?? "local/llama-3.2-1b",
    };
    if (config.toolCallbackUrl) {
      headers["X-ARC-Tool-Callback"] = config.toolCallbackUrl;
    }
    if (config.toolCallbackSecret) {
      headers["X-ARC-Tool-Secret"] = config.toolCallbackSecret;
    }
    if (config.apiKey) {
      headers["Authorization"] = `Bearer ${config.apiKey}`;
    }
    return headers;
  }

  async function chat(request: ArcChatRequest): Promise<ArcChatResponse> {
    const messages: ArcChatMessage[] =
      request.messages && request.messages.length > 0
        ? request.messages
        : [{ role: "user", content: request.prompt ?? "" }];

    const payload: Record<string, unknown> = {
      model: config.models.source,
      messages,
      temperature: request.temperature ?? 0.0,
    };
    if (request.params) {
      payload.arc_params = request.params;
    }

    const response = await fetchFn(`${baseUrl}/chat/completions`, {
      method: "POST",
      headers: buildHeaders(request.mode),
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const errText = await response.text();
      throw new Error(
        `ARC-Loom gateway request failed (${response.status}): ${errText}`
      );
    }

    const data = (await response.json()) as Record<string, any>;
    const choiceContent =
      data.choices?.[0]?.message?.content ?? data.output ?? "";
    const rawTelem = data.arc_telemetry ?? {};
    const rawUsage = data.usage ?? {};

    return {
      id: String(data.id ?? ""),
      answer: String(choiceContent),
      model: String(data.model ?? config.models.source),
      telemetry: {
        requestId: String(rawTelem.requestId ?? ""),
        project: String(rawTelem.project ?? config.project),
        path: rawTelem.path ?? "bypass",
        matchedEntryKey: rawTelem.matchedEntryKey ?? null,
        similarityScore: Number(rawTelem.similarityScore ?? 0),
        actualCostUsd: Number(rawTelem.actualCostUsd ?? 0),
        counterfactualCostUsd: Number(rawTelem.counterfactualCostUsd ?? 0),
        savedUsd: Number(rawTelem.savedUsd ?? 0),
        latencyMs: Number(rawTelem.latencyMs ?? 0),
        narrationTrace: Array.isArray(rawTelem.narrationTrace)
          ? rawTelem.narrationTrace.map(String)
          : [],
        autoBuiltEntries: Array.isArray(rawTelem.autoBuiltEntries)
          ? rawTelem.autoBuiltEntries.map(String)
          : undefined,
      },
      usage: {
        promptTokens: Number(rawUsage.prompt_tokens ?? 0),
        completionTokens: Number(rawUsage.completion_tokens ?? 0),
        totalTokens: Number(rawUsage.total_tokens ?? 0),
      },
    };
  }

  async function syncTools(): Promise<{
    project: string;
    registeredTools: string[];
  }> {
    const descriptions: Record<string, string> = {};
    for (const toolName of Object.keys(tools)) {
      descriptions[toolName] =
        config.toolDescriptions?.[toolName] ?? `Local client tool: ${toolName}`;
    }

    const response = await fetchFn(`${baseUrl}/arc/tools/register`, {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify({
        project: config.project,
        toolCallbackUrl: config.toolCallbackUrl,
        toolCallbackSecret: config.toolCallbackSecret,
        tools: descriptions,
      }),
    });

    if (!response.ok) {
      throw new Error(`Failed to register tools (${response.status})`);
    }
    return (await response.json()) as {
      project: string;
      registeredTools: string[];
    };
  }

  async function triggerBuild(): Promise<{
    processed: number;
    artifacts: unknown[];
  }> {
    const response = await fetchFn(`${baseUrl}/arc/build`, {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify({ project: config.project }),
    });
    if (!response.ok) {
      throw new Error(`Failed to trigger build queue (${response.status})`);
    }
    return (await response.json()) as {
      processed: number;
      artifacts: unknown[];
    };
  }

  async function getTelemetry(): Promise<ArcProjectTelemetry> {
    const response = await fetchFn(
      `${baseUrl}/arc/telemetry/${encodeURIComponent(config.project)}`,
      {
        method: "GET",
        headers: buildHeaders(),
      }
    );
    if (!response.ok) {
      throw new Error(`Failed to fetch project telemetry (${response.status})`);
    }
    return (await response.json()) as ArcProjectTelemetry;
  }

  return {
    chat,
    syncTools,
    triggerBuild,
    getTelemetry,
    handleToolWebhook: toolBridge.dispatch,
    toolBridge,
  };
}
