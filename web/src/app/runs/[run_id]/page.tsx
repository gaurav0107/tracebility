import Link from "next/link";
import { notFound } from "next/navigation";
import { ReplayDiffClient } from "@/components/ReplayDiffClient";
import { Shell } from "@/components/Shell";
import { apiGet } from "@/lib/api";
import { resolveActiveProject } from "@/lib/projects";

/**
 * Run detail — three-pane debugger shell (DESIGN.md v2 mock-as-truth):
 *   span-tree (360) │ timeline canvas (1fr) │ inspector (440)
 *
 * Server-rendered: resolves active project, fetches /v1/runs/:id and
 * /v1/runs/:id/spans in parallel, builds the tree from parent_span_id
 * chains, treats orphans as roots so a missing parent never silently
 * drops data (ER-23).
 *
 * Selection is driven by ?span=<span_id> so the inspector survives
 * deep-linking and reload.
 */

type Status = "ok" | "error" | "running" | string;

interface Run {
  run_id: string;
  project_id: string;
  parent_run_id: string | null;
  name: string;
  kind: string;
  status: Status;
  start_time: string;
  end_time: string | null;
  latency_ms: number | null;
  inputs: string;
  outputs: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
  sdk: string;
  sdk_version: string;
  session_id: string | null;
  user_id: string | null;
  tags: string[];
  metadata: string;
  error_kind: string;
  error_message: string;
}

interface Span {
  span_id: string;
  parent_span_id: string | null;
  name: string;
  kind: string;
  status: Status;
  start_time: string;
  end_time: string | null;
  latency_ms: number | null;
  inputs: string;
  outputs: string;
  model: string;
  temperature: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
  error_kind: string;
  error_message: string;
  attributes: string;
}

interface SpanListResponse {
  items: Span[];
}

interface ReplayCaptureItem {
  span_id: string;
  kind: string;
  content_hash: string;
  object_ref: string;
  size_bytes: number;
  attributes: string;
  captured_at: string;
}

interface ReplayCaptureSummary {
  total: number;
  by_kind: Record<string, number>;
  bytes_total: number;
  unique_hashes: number;
}

interface ReplayCaptureList {
  summary: ReplayCaptureSummary;
  items: ReplayCaptureItem[];
}

interface SpanNode {
  span: Span;
  depth: number;
}

export default async function RunDetailPage({
  params,
  searchParams,
}: {
  params: { run_id: string };
  searchParams: { span?: string };
}) {
  const { active, all, reason } = await resolveActiveProject();
  if (!active) {
    return (
      <Shell active={null} projects={all}>
        <div style={{ padding: 24 }}>
          <p style={{ color: "var(--text-2)", fontSize: 13 }}>
            no project resolved{reason ? ` (${reason})` : ""}.{" "}
            <Link href="/">back to overview</Link>
          </p>
        </div>
      </Shell>
    );
  }

  const projectQuery = `project_id=${encodeURIComponent(active.id)}`;
  const [runRes, spansRes, capturesRes] = await Promise.all([
    apiGet<Run>(`/v1/runs/${encodeURIComponent(params.run_id)}?${projectQuery}`),
    apiGet<SpanListResponse>(
      `/v1/runs/${encodeURIComponent(params.run_id)}/spans?${projectQuery}`,
    ),
    apiGet<ReplayCaptureList>(
      `/v1/runs/${encodeURIComponent(params.run_id)}/replay-captures?${projectQuery}`,
    ),
  ]);

  if (runRes.status === 404) {
    notFound();
  }

  if (!runRes.data) {
    return (
      <Shell active={active} projects={all}>
        <div style={{ padding: 24 }}>
          <p style={{ color: "var(--danger)", fontSize: 13 }}>
            run unavailable: {runRes.error ?? "unknown error"}
          </p>
        </div>
      </Shell>
    );
  }

  const run = runRes.data;
  const spans = spansRes.data?.items ?? [];
  const flat = flatten(buildTree(spans));
  const selectedSpan =
    spans.find((s) => s.span_id === searchParams.span) ?? null;
  const captures = capturesRes.data ?? null;
  const capturesBySpanId = new Map<string, ReplayCaptureItem>();
  if (captures) {
    for (const c of captures.items) {
      capturesBySpanId.set(c.span_id, c);
    }
  }

  const crumbs = (
    <>
      <Link href="/">langprobe</Link>
      <span className="sep">/</span>
      <Link href="/runs">runs</Link>
      <span className="sep">/</span>
      <span className="last mono">{run.run_id.slice(0, 8)}</span>
    </>
  );

  return (
    <Shell active={active} projects={all} crumbs={crumbs}>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
          gap: 18,
        }}
      >
        <RunHeader run={run} spanCount={spans.length} />
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 400px",
            gap: 18,
            alignItems: "start",
            minWidth: 0,
          }}
        >
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 18,
              minWidth: 0,
            }}
          >
            <TimelinePane
              run={run}
              nodes={flat}
              runId={run.run_id}
              selectedSpanId={selectedSpan?.span_id ?? null}
              error={spansRes.error}
            />
          </div>
          <InspectorPane
            run={run}
            span={selectedSpan}
            captures={captures}
            spans={spans}
            projectId={active.id}
            capture={
              selectedSpan
                ? capturesBySpanId.get(selectedSpan.span_id) ?? null
                : null
            }
          />
        </div>
      </div>
    </Shell>
  );
}

function RunHeader({ run, spanCount }: { run: Run; spanCount: number }) {
  return (
    <div
      className="card"
      style={{
        padding: "20px 24px",
        display: "flex",
        flexDirection: "column",
        gap: 16,
        flexShrink: 0,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          flexWrap: "wrap",
        }}
      >
        <h1
          style={{
            fontSize: 20,
            fontWeight: 800,
            letterSpacing: "-0.02em",
            margin: 0,
          }}
        >
          {run.name || "(unnamed run)"}
        </h1>
        <KindBadge kind={run.kind} />
        <StatusPill status={run.status} />
        <span style={{ flex: 1 }} />
        <span
          className="mono"
          style={{ fontSize: 11.5, color: "var(--text-4)" }}
        >
          {run.run_id}
        </span>
        <button
          type="button"
          className="btn btn-primary btn-sm"
          style={{ borderRadius: "var(--r-pill)" }}
        >
          replay
        </button>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(6, minmax(0, 1fr))",
          gap: 16,
        }}
      >
        <Stat label="started" value={fmtIso(run.start_time)} />
        <Stat label="latency" value={fmtLatency(run.latency_ms)} />
        <Stat
          label="tokens"
          value={
            run.total_tokens
              ? run.total_tokens.toLocaleString("en-US")
              : "—"
          }
        />
        <Stat label="cost" value={fmtCost(run.cost_usd)} />
        <Stat
          label="sdk"
          value={
            run.sdk
              ? `${run.sdk}${run.sdk_version ? ` ${run.sdk_version}` : ""}`
              : "—"
          }
        />
        <Stat label="spans" value={spanCount.toLocaleString("en-US")} />
      </div>
      {run.error_kind || run.error_message ? (
        <div
          style={{
            border: "1px solid #F4DBD6",
            background: "#FDF1EF",
            borderRadius: 12,
            padding: "10px 16px",
            display: "flex",
            alignItems: "center",
            gap: 12,
          }}
        >
          <span
            style={{
              fontSize: 11,
              fontWeight: 700,
              color: "var(--danger)",
              background: "var(--surface)",
              borderRadius: "var(--r-pill)",
              padding: "3px 10px",
              flexShrink: 0,
            }}
          >
            {run.error_kind || "error"}
          </span>
          {run.error_message ? (
            <span
              className="mono"
              style={{ fontSize: 12, color: "var(--danger)" }}
            >
              {run.error_message}
            </span>
          ) : null}
          <span style={{ flex: 1 }} />
        </div>
      ) : null}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span
        style={{
          fontSize: 10.5,
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          color: "var(--text-4)",
        }}
      >
        {label}
      </span>
      <span
        className="mono num"
        style={{ fontSize: 13.5, fontWeight: 600, color: "var(--text)" }}
      >
        {value}
      </span>
    </div>
  );
}

function TimelinePane({
  run,
  nodes,
  runId,
  selectedSpanId,
  error,
}: {
  run: Run;
  nodes: SpanNode[];
  runId: string;
  selectedSpanId: string | null;
  error: string | null;
}) {
  const window = computeWindow(run, nodes);
  const empty = nodes.length === 0 || window.totalMs <= 0;
  return (
    <div className="card" style={{ overflow: "hidden" }}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 10,
          padding: "18px 24px 12px",
        }}
      >
        <span className="card-title">spans</span>
        <span className="card-sub">
          {nodes.length}
          {window.totalMs > 0 ? ` · ${fmtLatency(window.totalMs)} window` : ""}
        </span>
      </div>
      {empty ? (
        <div
          style={{
            padding: "0 24px 20px",
            color: "var(--text-3)",
            fontSize: 13,
          }}
        >
          No spans recorded for this run.
        </div>
      ) : (
        <>
          <TimeAxis totalMs={window.totalMs} />
          {nodes.map(({ span, depth }) => (
            <TimelineRow
              key={span.span_id}
              runId={runId}
              span={span}
              depth={depth}
              window={window}
              selected={span.span_id === selectedSpanId}
            />
          ))}
        </>
      )}
      {error ? (
        <div
          style={{
            margin: "12px 24px 20px",
            padding: "8px 12px",
            background: "var(--danger-soft)",
            color: "var(--danger)",
            fontSize: 11,
            borderRadius: "var(--r-3)",
          }}
        >
          spans unavailable: {error}
        </div>
      ) : null}
    </div>
  );
}

function TimeAxis({ totalMs }: { totalMs: number }) {
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "250px 1fr 64px",
        gap: 16,
        padding: "0 24px 8px",
        borderBottom: "1px solid var(--divider)",
      }}
    >
      <span />
      <span
        className="mono"
        style={{
          display: "flex",
          justifyContent: "space-between",
          fontSize: 10,
          color: "var(--text-4)",
        }}
      >
        {ticks.map((t) => (
          <span key={t}>{fmtLatency(totalMs * t)}</span>
        ))}
      </span>
      <span />
    </div>
  );
}

function TimelineRow({
  runId,
  span,
  depth,
  window,
  selected,
}: {
  runId: string;
  span: Span;
  depth: number;
  window: TimelineWindow;
  selected: boolean;
}) {
  const startMs = isoToMs(span.start_time);
  const endMs = span.end_time
    ? isoToMs(span.end_time)
    : startMs + (span.latency_ms ?? 0);
  const left =
    window.totalMs > 0
      ? Math.max(0, ((startMs - window.startMs) / window.totalMs) * 100)
      : 0;
  const width =
    window.totalMs > 0
      ? Math.max(0.5, ((endMs - startMs) / window.totalMs) * 100)
      : 0;
  const bar = waterfallBarColor(span);
  return (
    <Link
      href={`/runs/${runId}?span=${span.span_id}`}
      style={{
        display: "grid",
        gridTemplateColumns: "250px 1fr 64px",
        alignItems: "center",
        gap: 16,
        height: 46,
        padding: "0 24px",
        borderBottom: "1px solid var(--divider-row)",
        background: selected ? "rgba(4,133,247,0.06)" : "transparent",
        color: "var(--text)",
        textDecoration: "none",
      }}
    >
      <span
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          minWidth: 0,
          paddingLeft: depth * 18,
        }}
      >
        <KindBadge kind={span.kind} />
        <span
          className="mono"
          style={{
            fontSize: 12,
            fontWeight: 600,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {span.name || "(unnamed)"}
        </span>
      </span>
      <span
        style={{
          position: "relative",
          height: 16,
          background: "var(--surface-2)",
          borderRadius: "var(--r-pill)",
          display: "block",
        }}
      >
        <span
          style={{
            position: "absolute",
            top: 0,
            bottom: 0,
            left: `${left}%`,
            width: `${width}%`,
            minWidth: 2,
            borderRadius: "var(--r-pill)",
            background: bar,
          }}
        />
      </span>
      <span
        className="mono num"
        style={{
          fontSize: 11.5,
          color: "var(--text-3)",
          textAlign: "right",
        }}
      >
        {fmtLatency(span.latency_ms)}
      </span>
    </Link>
  );
}

function InspectorPane({
  run,
  span,
  captures,
  spans,
  projectId,
  capture,
}: {
  run: Run;
  span: Span | null;
  captures: ReplayCaptureList | null;
  spans: Span[];
  projectId: string;
  capture: ReplayCaptureItem | null;
}) {
  return (
    <div
      className="card"
      style={{
        padding: "20px 24px",
        display: "flex",
        flexDirection: "column",
        gap: 16,
      }}
    >
      {span ? (
        <SpanInspector span={span} capture={capture} />
      ) : (
        <RunInspector
          run={run}
          captures={captures}
          spans={spans}
          projectId={projectId}
        />
      )}
    </div>
  );
}

function SpanInspector({
  span,
  capture,
}: {
  span: Span;
  capture: ReplayCaptureItem | null;
}) {
  return (
    <>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <span
          className="mono"
          style={{
            fontSize: 11,
            color: "var(--text-4)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {span.span_id}
        </span>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            flexWrap: "wrap",
          }}
        >
          <span
            className="mono"
            style={{
              fontSize: 17,
              fontWeight: 800,
              letterSpacing: "-0.01em",
            }}
          >
            {span.name || "(unnamed)"}
          </span>
          <KindBadge kind={span.kind} />
          <StatusPill status={span.status} />
          {capture ? (
            <span
              title={`replay-capture · ${capture.kind} · ${capture.size_bytes} bytes`}
              style={{
                fontSize: 10.5,
                fontWeight: 700,
                color: "var(--text-2)",
                background: "var(--surface-3)",
                borderRadius: "var(--r-pill)",
                padding: "3px 10px",
              }}
            >
              replay-ready
            </span>
          ) : null}
        </div>
      </div>
      <KvList>
        {span.model ? <Kv k="model" v={span.model} /> : null}
        {span.temperature !== null ? (
          <Kv k="temperature" v={String(span.temperature)} />
        ) : null}
        <Kv k="latency" v={fmtLatency(span.latency_ms)} />
        <Kv
          k="tokens"
          v={
            span.total_tokens
              ? `${span.prompt_tokens} + ${span.completion_tokens} = ${span.total_tokens}`
              : "—"
          }
        />
        <Kv k="cost" v={fmtCost(span.cost_usd)} />
        <Kv k="started" v={fmtIso(span.start_time)} />
      </KvList>
      {span.error_message ? (
        <div
          className="mono"
          style={{
            border: "1px solid #F4DBD6",
            background: "#FDF1EF",
            borderRadius: 12,
            padding: "10px 14px",
            fontSize: 11.5,
            lineHeight: 1.6,
            color: "var(--danger)",
          }}
        >
          <b style={{ fontWeight: 600 }}>{span.error_kind || "error"}:</b>{" "}
          {span.error_message}
        </div>
      ) : null}
      <Section label="inputs">
        <Pre value={span.inputs} />
      </Section>
      <Section label="outputs">
        <Pre value={span.outputs} />
      </Section>
      {span.attributes ? (
        <Section label="attributes">
          <Pre value={span.attributes} />
        </Section>
      ) : null}
      {capture ? <CaptureBlock capture={capture} /> : null}
      <div style={{ display: "flex", gap: 10, marginTop: 2 }}>
        <button
          type="button"
          className="btn btn-primary btn-sm"
          style={{ borderRadius: "var(--r-pill)" }}
        >
          replay with edits
        </button>
        <button
          type="button"
          className="btn btn-sm"
          style={{ borderRadius: "var(--r-pill)" }}
        >
          add to dataset
        </button>
      </div>
    </>
  );
}

function CaptureBlock({ capture }: { capture: ReplayCaptureItem }) {
  return (
    <Section label="replay capture">
      <KvList>
        <Kv k="kind" v={capture.kind} />
        <Kv k="hash" v={capture.content_hash.slice(0, 16) + "…"} />
        <Kv k="ref" v={capture.object_ref} />
        <Kv k="bytes" v={fmtBytes(capture.size_bytes)} />
        <Kv k="captured" v={fmtIso(capture.captured_at)} />
      </KvList>
    </Section>
  );
}

function RunInspector({
  run,
  captures,
  spans,
  projectId,
}: {
  run: Run;
  captures: ReplayCaptureList | null;
  spans: Span[];
  projectId: string;
}) {
  const llmSpans = spans
    .filter((s) => (s.kind || "").toLowerCase() === "llm")
    .map((s) => ({
      span_id: s.span_id,
      name: s.name,
      model: s.model,
      temperature: s.temperature,
    }));
  return (
    <>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <span
          className="mono"
          style={{
            fontSize: 11,
            color: "var(--text-4)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {run.run_id}
        </span>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            flexWrap: "wrap",
          }}
        >
          <span
            style={{
              fontSize: 17,
              fontWeight: 800,
              letterSpacing: "-0.01em",
            }}
          >
            {run.name || "(unnamed)"}
          </span>
          <KindBadge kind={run.kind} />
          <StatusPill status={run.status} />
        </div>
      </div>
      <KvList>
        <Kv
          k="tokens"
          v={
            run.total_tokens
              ? `${run.prompt_tokens} + ${run.completion_tokens} = ${run.total_tokens}`
              : "—"
          }
        />
        <Kv k="cost" v={fmtCost(run.cost_usd)} />
        <Kv k="latency" v={fmtLatency(run.latency_ms)} />
        {run.session_id ? <Kv k="session" v={run.session_id} /> : null}
        {run.user_id ? <Kv k="user" v={run.user_id} /> : null}
        {run.tags.length > 0 ? (
          <Kv k="tags" v={run.tags.join(", ")} />
        ) : null}
      </KvList>
      <Section label="inputs">
        <Pre value={run.inputs} />
      </Section>
      <Section label="outputs">
        <Pre value={run.outputs} />
      </Section>
      {run.metadata ? (
        <Section label="metadata">
          <Pre value={run.metadata} />
        </Section>
      ) : null}
      {captures ? <ReplayPanel captures={captures} /> : null}
      <Section label="replay & diff">
        <ReplayDiffClient
          runId={run.run_id}
          projectId={projectId}
          spans={llmSpans}
        />
      </Section>
    </>
  );
}

function ReplayPanel({ captures }: { captures: ReplayCaptureList }) {
  const { summary, items } = captures;
  const kinds = Object.entries(summary.by_kind).sort(
    ([, a], [, b]) => b - a,
  );
  return (
    <Section label="replay">
      {summary.total === 0 ? (
        <span style={{ color: "var(--text-3)", fontSize: 12 }}>
          no replay-relevant spans (llm / tool / retriever) captured for this run
        </span>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <KvList>
            <Kv k="captures" v={String(summary.total)} />
            <Kv k="unique" v={String(summary.unique_hashes)} />
            <Kv k="bytes" v={fmtBytes(summary.bytes_total)} />
            {kinds.length > 0 ? (
              <Kv
                k="by kind"
                v={kinds.map(([k, n]) => `${k}=${n}`).join(", ")}
              />
            ) : null}
          </KvList>
          <div
            style={{
              border: "1px solid var(--border-soft)",
              borderRadius: "var(--r-4)",
              overflow: "hidden",
              background: "var(--surface)",
            }}
          >
            {items.slice(0, 50).map((c) => (
              <div
                key={c.span_id}
                style={{
                  display: "grid",
                  gridTemplateColumns: "auto 1fr auto",
                  gap: 8,
                  alignItems: "center",
                  padding: "6px 10px",
                  borderBottom: "1px solid var(--divider-row)",
                  fontSize: 11,
                }}
              >
                <span
                  className="badge badge-neutral"
                  style={{ fontSize: 10 }}
                >
                  {c.kind}
                </span>
                <span
                  className="mono"
                  style={{
                    color: "var(--text-3)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                  title={c.content_hash}
                >
                  {c.content_hash.slice(0, 16)}…
                </span>
                <span
                  className="mono num"
                  style={{ color: "var(--text-3)" }}
                >
                  {fmtBytes(c.size_bytes)}
                </span>
              </div>
            ))}
            {items.length > 50 ? (
              <div
                style={{
                  padding: "6px 10px",
                  fontSize: 11,
                  color: "var(--text-3)",
                  textAlign: "center",
                }}
              >
                + {items.length - 50} more
              </div>
            ) : null}
          </div>
        </div>
      )}
    </Section>
  );
}

function KvList({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        border: "1px solid var(--border-soft)",
        borderRadius: "var(--r-4)",
        overflow: "hidden",
      }}
    >
      {children}
    </div>
  );
}

function Section({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
      <span
        style={{
          fontSize: 10.5,
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          color: "var(--text-4)",
        }}
      >
        {label}
      </span>
      {children}
    </div>
  );
}

function Kv({ k, v }: { k: string; v: string }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "110px 1fr",
        gap: 12,
        alignItems: "center",
        padding: "9px 14px",
        borderBottom: "1px solid var(--divider-row)",
      }}
    >
      <span
        style={{
          fontSize: 10.5,
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: "0.07em",
          color: "var(--text-4)",
        }}
      >
        {k}
      </span>
      <span
        className="mono"
        style={{
          fontSize: 12,
          fontWeight: 500,
          color: "var(--text)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
        title={v}
      >
        {v}
      </span>
    </div>
  );
}

function Pre({ value }: { value: string }) {
  if (!value) {
    return (
      <span style={{ color: "var(--text-3)", fontSize: 12 }}>—</span>
    );
  }
  let pretty = value;
  try {
    pretty = JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    // not JSON, render raw
  }
  return (
    <pre
      className="code"
      style={{
        fontSize: 11.5,
        lineHeight: 1.7,
        margin: 0,
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
        maxHeight: 320,
      }}
    >
      {pretty}
    </pre>
  );
}

function KindBadge({ kind }: { kind: string }) {
  const k = (kind || "").toLowerCase();
  const cls =
    k === "llm"
      ? "kind-llm"
      : k === "tool"
        ? "kind-tool"
        : k === "retriever" || k === "retr" || k === "reranker"
          ? "kind-retr"
          : k === "agent"
            ? "kind-agent"
            : "kind-chain";
  const label =
    k === "retriever" || k === "retr" || k === "reranker"
      ? "retr"
      : kind || "chain";
  return <span className={`kind-badge ${cls}`}>{label}</span>;
}

function StatusPill({ status }: { status: Status }) {
  const cls =
    status === "ok"
      ? "badge badge-success"
      : status === "error"
        ? "badge badge-danger"
        : "badge badge-warn";
  const dot =
    status === "ok"
      ? "dot dot-success"
      : status === "error"
        ? "dot dot-danger"
        : "dot dot-warn";
  return (
    <span className={cls}>
      <span className={dot} aria-hidden />
      {status}
    </span>
  );
}

interface TimelineWindow {
  startMs: number;
  endMs: number;
  totalMs: number;
}

function computeWindow(run: Run, nodes: SpanNode[]): TimelineWindow {
  const runStart = isoToMs(run.start_time);
  const runEnd = run.end_time
    ? isoToMs(run.end_time)
    : runStart + (run.latency_ms ?? 0);
  let startMs = runStart;
  let endMs = runEnd;
  for (const { span } of nodes) {
    const s = isoToMs(span.start_time);
    const e = span.end_time
      ? isoToMs(span.end_time)
      : s + (span.latency_ms ?? 0);
    if (s < startMs) startMs = s;
    if (e > endMs) endMs = e;
  }
  const totalMs = Math.max(0, endMs - startMs);
  return { startMs, endMs, totalMs };
}

function waterfallBarColor(span: Span): string {
  if (span.status === "error") return "#E05545";
  const k = (span.kind || "").toLowerCase();
  if (k === "llm") return "#D89B3C";
  if (k === "tool") return "#3FA3D6";
  if (k === "retriever" || k === "retr" || k === "reranker") return "#4CB584";
  return "#9B79E4";
}

function buildTree(spans: Span[]): SpanNode[] {
  const byId = new Map<string, Span>();
  for (const s of spans) byId.set(s.span_id, s);
  const childrenOf = new Map<string | null, Span[]>();
  for (const s of spans) {
    const key =
      s.parent_span_id && byId.has(s.parent_span_id)
        ? s.parent_span_id
        : null;
    const arr = childrenOf.get(key) ?? [];
    arr.push(s);
    childrenOf.set(key, arr);
  }
  for (const arr of childrenOf.values()) {
    arr.sort((a, b) => {
      if (a.start_time === b.start_time) {
        return a.span_id < b.span_id ? -1 : 1;
      }
      return a.start_time < b.start_time ? -1 : 1;
    });
  }
  const roots = childrenOf.get(null) ?? [];
  return walk(roots, 0, childrenOf);
}

function walk(
  spans: Span[],
  depth: number,
  childrenOf: Map<string | null, Span[]>,
): SpanNode[] {
  const out: SpanNode[] = [];
  for (const s of spans) {
    out.push({ span: s, depth });
    const kids = childrenOf.get(s.span_id);
    if (kids) out.push(...walk(kids, depth + 1, childrenOf));
  }
  return out;
}

function flatten(nodes: SpanNode[]): SpanNode[] {
  return nodes;
}

function isoToMs(iso: string): number {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : 0;
}

function fmtLatency(ms: number | null): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1) return `${ms.toFixed(2)} ms`;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function fmtCost(usd: number): string {
  if (!usd) return "—";
  return `$${usd.toFixed(4)}`;
}

function fmtBytes(bytes: number): string {
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function fmtIso(iso: string): string {
  try {
    return new Date(iso).toISOString().replace("T", " ").slice(0, 19);
  } catch {
    return iso;
  }
}
