import { describe, expect, test } from "bun:test";
import {
  computeHmacSignature,
  createArcClient,
  createToolBridge,
} from "../src/index";

describe("@arc-loom/sdk client and HMAC tool bridge", () => {
  test("createArcClient sends ARC headers and parses chat completion + telemetry", async () => {
    let capturedUrl = "";
    let capturedHeaders: Record<string, string> = {};
    let capturedBody: Record<string, any> = {};

    const mockFetch = async (
      input: string | URL | Request,
      init?: RequestInit
    ): Promise<Response> => {
      capturedUrl = String(input);
      capturedHeaders = (init?.headers as Record<string, string>) ?? {};
      capturedBody = JSON.parse(String(init?.body ?? "{}"));

      return new Response(
        JSON.stringify({
          id: "chatcmpl-arc-001",
          object: "chat.completion",
          created: 1763827200,
          model: "anthropic/claude-opus-4.5",
          choices: [
            {
              index: 0,
              message: {
                role: "assistant",
                content: "Total pengeluaran makan bulan November 2026: Rp 2.450.000",
              },
              finish_reason: "stop",
            },
          ],
          usage: {
            prompt_tokens: 45,
            completion_tokens: 12,
            total_tokens: 57,
          },
          arc_telemetry: {
            requestId: "req-abc123",
            project: "finansia",
            path: "arc_hit",
            matchedEntryKey: "get_monthly_food_expense",
            similarityScore: 0.9412,
            actualCostUsd: 0.00004,
            counterfactualCostUsd: 0.04124,
            savedUsd: 0.0412,
            latencyMs: 145,
            narrationTrace: [
              "Mengekstrak bulan dan kategori menggunakan llama-3.2-1b",
              "Memanggil tool getMonthlyExpense",
            ],
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      );
    };

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
      toolCallbackSecret: "top-secret-key",
      fetchImpl: mockFetch as typeof fetch,
      tools: {
        getMonthlyExpense: async ({
          month,
          category,
        }: {
          month: string;
          category?: string;
        }) => {
          return { month, category: category ?? "all", totalIdr: 2450000 };
        },
      },
    });

    const res = await arc.chat({
      prompt: "Berapa total pengeluaran kategori makan bulan November 2026?",
    });

    expect(capturedUrl).toBe("http://localhost:8080/v1/chat/completions");
    expect(capturedHeaders["X-ARC-Project"]).toBe("finansia");
    expect(capturedHeaders["X-ARC-Mode"]).toBe("active");
    expect(capturedHeaders["X-ARC-Tool-Callback"]).toBe(
      "http://localhost:3000/internal/arc-tools"
    );
    expect(capturedBody.messages[0].content).toBe(
      "Berapa total pengeluaran kategori makan bulan November 2026?"
    );

    expect(res.answer).toBe(
      "Total pengeluaran makan bulan November 2026: Rp 2.450.000"
    );
    expect(res.telemetry.path).toBe("arc_hit");
    expect(res.telemetry.actualCostUsd).toBe(0.00004);
    expect(res.telemetry.savedUsd).toBe(0.0412);
    expect(res.telemetry.latencyMs).toBe(145);
    expect(res.telemetry.narrationTrace.length).toBe(2);
  });

  test("HMAC-SHA256 tool bridge verifies signature and executes local TypeScript tools", async () => {
    const secret = "arc-shared-hmac-secret";
    const bridge = createToolBridge({
      project: "finansia",
      secret,
      tools: {
        getMonthlyExpense: async ({
          month,
          category,
        }: {
          month: string;
          category?: string;
        }) => {
          return {
            month,
            category: category ?? "umum",
            totalIdr: month === "2026-11" ? 2450000 : 1800000,
          };
        },
      },
    });

    const rawBody = JSON.stringify({
      args: { category: "makan", month: "2026-11" },
      project: "finansia",
      request_id: "req-webhook-99",
      tool: "getMonthlyExpense",
    });

    const validSignature = computeHmacSignature(rawBody, secret);
    const okOutcome = await bridge.dispatch(rawBody, validSignature);
    expect(okOutcome.status).toBe(200);
    expect(okOutcome.body.result).toEqual({
      month: "2026-11",
      category: "makan",
      totalIdr: 2450000,
    });

    const badOutcome = await bridge.dispatch(rawBody, "sha256=deadbeef");
    expect(badOutcome.status).toBe(401);

    const request = new Request("http://localhost:3000/internal/arc-tools", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-ARC-Signature": validSignature,
      },
      body: rawBody,
    });
    const httpResp = await bridge.fetchHandler(request);
    expect(httpResp.status).toBe(200);
    const jsonResp = (await httpResp.json()) as { result: unknown };
    expect(jsonResp.result).toEqual({
      month: "2026-11",
      category: "makan",
      totalIdr: 2450000,
    });
  });

  test("syncTools registers declarative tool schemas with the ARC-Loom gateway", async () => {
    let postedBody: Record<string, any> = {};
    const mockFetch = async (
      input: string | URL | Request,
      init?: RequestInit
    ): Promise<Response> => {
      postedBody = JSON.parse(String(init?.body ?? "{}"));
      return new Response(
        JSON.stringify({
          project: "finansia",
          registeredTools: ["getMonthlyExpense"],
        }),
        { status: 200 }
      );
    };

    const arc = createArcClient({
      gatewayUrl: "http://localhost:8080/v1",
      project: "finansia",
      models: { source: "fable-5.1" },
      toolCallbackUrl: "http://localhost:3000/arc-webhook",
      fetchImpl: mockFetch as typeof fetch,
      tools: {
        getMonthlyExpense: async () => 2450000,
      },
      toolDescriptions: {
        getMonthlyExpense: "Query monthly expense by category",
      },
    });

    const reg = await arc.syncTools();
    expect(reg.registeredTools).toEqual(["getMonthlyExpense"]);
    expect(postedBody.tools.getMonthlyExpense).toBe(
      "Query monthly expense by category"
    );
  });
});
