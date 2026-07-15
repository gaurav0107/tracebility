import { cookies } from "next/headers";
import { NextResponse } from "next/server";

/**
 * Proxy to FastAPI /v1/runs/{run_id}. Client surfaces that need a
 * run's boundary I/O after initial render (the annotation label card)
 * fetch through here so the browser never talks to :7081 directly.
 */

function apiBase(): string {
  return (
    process.env.API_BASE_INTERNAL ||
    process.env.NEXT_PUBLIC_API_BASE ||
    "http://localhost:7081"
  );
}

function cookieHeader(): string {
  return cookies()
    .getAll()
    .map((c) => `${c.name}=${c.value}`)
    .join("; ");
}

export async function GET(
  request: Request,
  { params }: { params: { run_id: string } },
): Promise<NextResponse> {
  const url = new URL(request.url);
  const projectId = url.searchParams.get("project_id");
  if (!projectId) {
    return NextResponse.json({ error: "project_id required" }, { status: 400 });
  }
  const res = await fetch(
    `${apiBase()}/v1/runs/${encodeURIComponent(params.run_id)}?project_id=${encodeURIComponent(projectId)}`,
    { cache: "no-store", headers: { cookie: cookieHeader() } },
  );
  const text = await res.text();
  return new NextResponse(text, {
    status: res.status,
    headers: { "content-type": "application/json" },
  });
}
