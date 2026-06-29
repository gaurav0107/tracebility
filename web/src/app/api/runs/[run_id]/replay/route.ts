import { cookies } from "next/headers";
import { NextResponse } from "next/server";

/**
 * Proxy to FastAPI POST /v1/runs/{run_id}/replay.
 *
 * Phase 0 span-level replay: re-dispatch the edited span(s) live, hold the rest
 * at captured values, diff. Body ({ project_id, edits }) is forwarded as-is;
 * the FastAPI side enforces RBAC against the project's workspace.
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

export async function POST(
  request: Request,
  { params }: { params: { run_id: string } },
): Promise<NextResponse> {
  const body = await request.text();
  const res = await fetch(
    `${apiBase()}/v1/runs/${encodeURIComponent(params.run_id)}/replay`,
    {
      method: "POST",
      headers: { cookie: cookieHeader(), "content-type": "application/json" },
      body,
      cache: "no-store",
    },
  );
  const text = await res.text();
  return new NextResponse(text, {
    status: res.status,
    headers: { "content-type": "application/json" },
  });
}
