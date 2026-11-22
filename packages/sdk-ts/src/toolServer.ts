import { createHmac, timingSafeEqual } from "node:crypto";

export type ArcToolHandler<TArgs = Record<string, unknown>, TResult = unknown> = (
  args: TArgs
) => TResult | Promise<TResult>;

export type ArcToolRegistry = Record<string, ArcToolHandler<any, any>>;

export interface ToolWebhookPayload {
  project: string;
  request_id: string;
  tool: string;
  args: Record<string, unknown>;
}

export interface ToolWebhookResult {
  status: number;
  body: {
    result?: unknown;
    error?: string;
  };
}

export interface ToolBridgeOptions {
  tools: ArcToolRegistry;
  secret?: string;
  project?: string;
}

export interface ToolBridge {
  dispatch(
    rawBody: string | Uint8Array,
    signatureHeader?: string | null
  ): Promise<ToolWebhookResult>;
  fetchHandler(request: Request): Promise<Response>;
}

export function computeHmacSignature(
  rawBody: string | Uint8Array,
  secret: string
): string {
  const hmac = createHmac("sha256", secret);
  hmac.update(rawBody);
  return `sha256=${hmac.digest("hex")}`;
}

export function verifyHmacSignature(
  rawBody: string | Uint8Array,
  signatureHeader: string | null | undefined,
  secret: string
): boolean {
  if (!signatureHeader || !signatureHeader.startsWith("sha256=")) {
    return false;
  }
  const expected = computeHmacSignature(rawBody, secret);
  const actualBuf = Buffer.from(signatureHeader, "utf-8");
  const expectedBuf = Buffer.from(expected, "utf-8");
  if (actualBuf.length !== expectedBuf.length) {
    return false;
  }
  return timingSafeEqual(actualBuf, expectedBuf);
}

export function createToolBridge(options: ToolBridgeOptions): ToolBridge {
  const { tools, secret, project } = options;

  async function dispatch(
    rawBody: string | Uint8Array,
    signatureHeader?: string | null
  ): Promise<ToolWebhookResult> {
    if (secret && !verifyHmacSignature(rawBody, signatureHeader, secret)) {
      return {
        status: 401,
        body: { error: "Invalid or missing X-ARC-Signature header." },
      };
    }

    const bodyText =
      typeof rawBody === "string"
        ? rawBody
        : new TextDecoder("utf-8").decode(rawBody);

    let payload: ToolWebhookPayload;
    try {
      payload = JSON.parse(bodyText) as ToolWebhookPayload;
    } catch {
      return {
        status: 400,
        body: { error: "Malformed JSON tool webhook payload." },
      };
    }

    if (project && payload.project && payload.project !== project) {
      return {
        status: 403,
        body: {
          error: `Tool webhook project '${payload.project}' does not match '${project}'.`,
        },
      };
    }

    const handler = tools[payload.tool];
    if (!handler) {
      return {
        status: 404,
        body: { error: `Tool '${payload.tool}' is not registered on this client.` },
      };
    }

    try {
      const output = await handler(payload.args ?? {});
      return {
        status: 200,
        body: { result: output },
      };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      return {
        status: 500,
        body: { error: `Tool execution failed: ${message}` },
      };
    }
  }

  async function fetchHandler(request: Request): Promise<Response> {
    if (request.method !== "POST") {
      return new Response(JSON.stringify({ error: "Method not allowed." }), {
        status: 405,
        headers: { "Content-Type": "application/json" },
      });
    }
    const rawBytes = new Uint8Array(await request.arrayBuffer());
    const sig = request.headers.get("x-arc-signature");
    const outcome = await dispatch(rawBytes, sig);
    return new Response(JSON.stringify(outcome.body), {
      status: outcome.status,
      headers: { "Content-Type": "application/json" },
    });
  }

  return {
    dispatch,
    fetchHandler,
  };
}
