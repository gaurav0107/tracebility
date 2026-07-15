import { cookies } from "next/headers";
import { NextResponse } from "next/server";

/**
 * Command-palette search. Fans out server-side to the FastAPI list
 * endpoints (runs / prompts / datasets / eval runs) with the caller's
 * session cookie and returns one combined payload, so the palette
 * makes a single round trip and the browser never talks to :7081
 * directly.
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

async function getJson<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${apiBase()}${path}`, {
      cache: "no-store",
      headers: { cookie: cookieHeader() },
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

interface RunItem {
  run_id: string;
  name: string;
  kind: string;
  status: string;
  start_time: string;
}
interface PromptItem {
  id: string;
  slug: string;
  name: string;
}
interface DatasetItem {
  id: string;
  slug: string;
  name: string;
  item_count?: number;
}
interface EvalItem {
  id: string;
  name: string | null;
  status: string;
  dataset_slug?: string | null;
}

export async function GET(request: Request): Promise<NextResponse> {
  const url = new URL(request.url);
  const projectId = url.searchParams.get("project_id");
  const q = (url.searchParams.get("q") ?? "").trim();
  if (!projectId) {
    return NextResponse.json({ error: "project_id required" }, { status: 400 });
  }

  const enc = encodeURIComponent;
  const [runs, prompts, datasets, evals] = await Promise.all([
    q
      ? getJson<{ items: RunItem[] }>(
          `/v1/runs?project_id=${enc(projectId)}&search=${enc(q)}&limit=8`,
        )
      : Promise.resolve({ items: [] as RunItem[] }),
    getJson<PromptItem[]>(`/v1/prompts?project_id=${enc(projectId)}`),
    getJson<DatasetItem[]>(`/v1/datasets?project_id=${enc(projectId)}`),
    getJson<EvalItem[]>(`/v1/eval-runs?project_id=${enc(projectId)}`),
  ]);

  const needle = q.toLowerCase();
  const match = (...fields: (string | null | undefined)[]) =>
    !needle ||
    fields.some((f) => (f ?? "").toLowerCase().includes(needle));

  return NextResponse.json({
    runs: runs?.items ?? [],
    prompts: (prompts ?? []).filter((p) => match(p.slug, p.name)).slice(0, 6),
    datasets: (datasets ?? [])
      .filter((d) => match(d.slug, d.name))
      .slice(0, 6),
    evals: (evals ?? []).filter((e) => match(e.name, e.id)).slice(0, 6),
  });
}
