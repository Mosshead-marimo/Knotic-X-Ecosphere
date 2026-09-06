import type { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const SAFE_REQUEST_HEADERS = new Set([
  "accept",
  "content-type",
  "cookie",
  "idempotency-key",
  "if-match",
  "last-event-id",
  "x-correlation-id",
  "x-csrf-token",
  "x-request-id",
]);

const SAFE_RESPONSE_HEADERS = new Set([
  "cache-control",
  "content-disposition",
  "content-type",
  "etag",
  "location",
  "ratelimit-limit",
  "ratelimit-remaining",
  "ratelimit-reset",
  "retry-after",
  "set-cookie",
  "x-correlation-id",
  "x-request-id",
]);

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const baseUrl = path.join("/") === "console/stream"
    ? process.env.EVENTS_INTERNAL_URL ?? process.env.BACKEND_INTERNAL_URL
    : process.env.BACKEND_INTERNAL_URL;
  if (!baseUrl) {
    return Response.json(
      { error: { code: "BACKEND_NOT_CONFIGURED", message: "The application backend is unavailable." } },
      { status: 503 },
    );
  }
  const target = new URL(`/api/v1/${path.map(encodeURIComponent).join("/")}`, baseUrl);
  target.search = request.nextUrl.search;
  const headers = new Headers();
  for (const [name, value] of request.headers) {
    if (SAFE_REQUEST_HEADERS.has(name.toLowerCase())) headers.set(name, value);
  }
  headers.set("Origin", request.nextUrl.origin);
  headers.set("X-Forwarded-Host", request.nextUrl.host);
  headers.set("X-Forwarded-Proto", request.nextUrl.protocol.replace(":", ""));

  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
      redirect: "manual",
      cache: "no-store",
      // Required by Node's fetch implementation for streamed request bodies.
      duplex: "half",
    } as RequestInit & { duplex: "half" });
    const responseHeaders = new Headers();
    for (const [name, value] of upstream.headers) {
      if (SAFE_RESPONSE_HEADERS.has(name.toLowerCase())) responseHeaders.append(name, value);
    }
    if (upstream.headers.get("content-type")?.includes("text/event-stream")) {
      responseHeaders.set("Cache-Control", "no-cache, no-transform");
      responseHeaders.set("X-Accel-Buffering", "no");
    }
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    return Response.json(
      { error: { code: "BACKEND_UNAVAILABLE", message: "The application backend is unavailable." } },
      { status: 503 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
