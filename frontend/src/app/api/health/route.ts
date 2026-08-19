import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET() {
  const backend = process.env.BACKEND_INTERNAL_URL;
  if (!backend) return NextResponse.json({ status: "not_ready" }, { status: 503 });
  try {
    const response = await fetch(`${backend}/api/v1/health/live`, { cache: "no-store", signal: AbortSignal.timeout(2000) });
    if (!response.ok) throw new Error("backend unavailable");
    return NextResponse.json({ status: "ok", backend: "ok" });
  } catch {
    return NextResponse.json({ status: "not_ready", backend: "unavailable" }, { status: 503 });
  }
}
