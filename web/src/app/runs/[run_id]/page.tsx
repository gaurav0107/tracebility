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
          height: "100%",
          minHeight: 0,
        }}
      >
        <RunHeader run={run} spanCount={spans.length} />
        <div
          style={{
            display: "grid",
            gridTemplateColumns:
              "var(--tracepane-l) 1fr var(--tracepane-r)",
            flex: 1,
            minHeight: 0,
            overflow: "hidden",
          }}
        >
          <SpanTreePane
            runId={run.run_id}
            nodes={flat}
            selectedSpanId={selectedSpan?.span_id ?? null}
            error={spansRes.error}
          />
          <TimelinePane
            run={run}
            nodes={flat}
            runId={run.run_id}
            selectedSpanId={selectedSpan?.span_id ?? null}
          />
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
    <header
      style={{
        padding: "16px 20px",
        borderBottom: "1px solid var(--border)",
        background: "var(--surface)",
        display: "flex",
        flexDirection: "column",
        gap: 12,
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
        <h1 style={{ fontSize: 16 }}>{run.name || "(unnamed run)"}</h1>
        <StatusPill status={run.status} />
        <KindBadge kind={run.kind} />
        <span
          className="mono"
          style={{ fontSize: 11, color: "var(--text-3)", marginLeft: "auto" }}
        >
          {run.run_id}
        </span>
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
            padding: "8px 12px",
            background: "var(--danger-soft)",
            color: "var(--danger)",
            fontSize: 12,
            borderRadius: "var(--r-2)",
            display: "flex",
            gap: 10,
            alignItems: "baseline",
          }}
        >
          <span
            className="mono"
            style={{
              textTransform: "uppercase",
              letterSpacing: "0.04em",
              fontSize: 11,
            }}
          >
            {run.error_kind || "error"}
          </span>
          <span>{run.error_message}</span>
        </div>
      ) : null}
    </header>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span
        style={{
          fontSize: 11,
          fontWeight: 500,
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          color: "var(--text-3)",
        }}
      >
        {label}
      </span>
      <span
        className="mono num"
        style={{ fontSize: 14, color: "var(--text)" }}
      >
        {value}
      </span>
    </div>
  );
}

function SpanTreePane({
  runId,
  nodes,
  selectedSpanId,
  error,
}: {
  runId: string;
  nodes: SpanNode[];
  selectedSpanId: string | null;
  error: string | null;
}) {
  return (
    <aside
      style={{
        borderRight: "1px solid var(--border)",
        background: "var(--surface)",
        overflow: "auto",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
        }}
      >
        <h2>Spans</h2>
        <span
          className="mono"
          style={{ fontSize: 11, color: "var(--text-3)" }}
        >
          {nodes.length}
        </span>
      </div>
      {nodes.length === 0 ? (
        <div
          style={{
            padding: 24,
            color: "var(--text-3)",
            fontSize: 13,
          }}
        >
          No spans recorded for this run.
        </div>
      ) : (
        <div>
          {nodes.map(({ span, depth }) => {
            const selected = span.span_id === selectedSpanId;
            return (
              <Link
                key={span.span_id}
                href={`/runs/${runId}?span=${span.span_id}`}
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr auto",
                  alignItems: "center",
                  gap: 8,
                  padding: "8px 16px",
                  paddingLeft: 16 + depth * 14,
                  borderBottom: "1px solid var(--border)",
                  background: selected ? "var(--surface-3)" : "transparent",
                  color: "var(--text)",
                  textDecoration: "none",
                  fontSize: 13,
                }}
              >
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 8,
                    overflow: "hidden",
                  }}
                >
                  <StatusDot status={span.status} />
                  <span
                    style={{
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {span.name || "(unnamed)"}
                  </span>
                </span>
                <span
                  className="mono num"
                  style={{ fontSize: 11, color: "var(--text-3)" }}
                >
                  {fmtLatency(span.latency_ms)}
                </span>
              </Link>
            );
          })}
        </div>
      )}
      {error ? (
        <div
          style={{
            margin: "12px 16px",
            padding: "8px 12px",
            background: "var(--danger-soft)",
            color: "var(--danger)",
            fontSize: 11,
            borderRadius: "var(--r-2)",
          }}
        >
          spans unavailable: {error}
        </div>
      ) : null}
    </aside>
  );
}

function TimelinePane({
  run,
  nodes,
  runId,
  selectedSpanId,
}: {
  run: Run;
  nodes: SpanNode[];
  runId: string;
  selectedSpanId: string | null;
}) {
  const window = computeWindow(run, nodes);
  return (
    <section
      style={{
        background: "var(--bg)",
        overflow: "auto",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid var(--border)",
          background: "var(--surface)",
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
        }}
      >
        <h2>Timeline</h2>
        <span
          className="mono"
          style={{ fontSize: 11, color: "var(--text-3)" }}
        >
          {window.totalMs > 0 ? `${fmtLatency(window.totalMs)} window` : "—"}
        </span>
      </div>
      {nodes.length === 0 || window.totalMs <= 0 ? (
        <div
          style={{
            padding: 24,
            color: "var(--text-3)",
            fontSize: 13,
          }}
        >
          No timeline data.
        </div>
      ) : (
        <div style={{ padding: "12px 16px" }}>
          <TimeAxis totalMs={window.totalMs} />
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
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
          </div>
        </div>
      )}
    </section>
  );
}

function TimeAxis({ totalMs }: { totalMs: number }) {
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  return (
    <div
      style={{
        position: "relative",
        height: 20,
        marginBottom: 8,
        borderBottom: "1px solid var(--border)",
      }}
    >
      {ticks.map((t) => (
        <span
          key={t}
          className="mono"
          style={{
            position: "absolute",
            left: `${t * 100}%`,
            transform: t === 1 ? "translateX(-100%)" : "translateX(0)",
            fontSize: 10,
            color: "var(--text-3)",
          }}
        >
          {fmtLatency(totalMs * t)}
        </span>
      ))}
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
  const barColor = barColorForKind(span.kind);
  return (
    <Link
      href={`/runs/${runId}?span=${span.span_id}`}
      style={{
        display: "grid",
        gridTemplateColumns: "200px 1fr 70px",
        alignItems: "center",
        gap: 12,
        padding: "4px 8px",
        borderRadius: "var(--r-2)",
        background: selected ? "var(--surface-3)" : "transparent",
        color: "var(--text)",
        textDecoration: "none",
        fontSize: 12,
        minHeight: 28,
      }}
    >
      <span
        style={{
          paddingLeft: depth * 12,
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        <StatusDot status={span.status} />
        <span
          style={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {span.name || "(unnamed)"}
        </span>
      </span>
      <div
        style={{
          position: "relative",
          height: 14,
          background: "var(--surface-2)",
          borderRadius: "var(--r-1)",
        }}
      >
        <div
          style={{
            position: "absolute",
            left: `${left}%`,
            width: `${width}%`,
            top: 0,
            bottom: 0,
            background: barColor.fill,
            borderRadius: "var(--r-1)",
            border: `1px solid ${barColor.stroke}`,
            minWidth: 2,
          }}
        />
      </div>
      <span
        className="mono num"
        style={{
          fontSize: 11,
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
    <aside
      style={{
        borderLeft: "1px solid var(--border)",
        background: "var(--surface)",
        overflow: "auto",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <h2>{span ? "Span" : "Run"}</h2>
        <div
          className="mono"
          style={{
            fontSize: 11,
            color: "var(--text-3)",
            marginTop: 2,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {span ? span.span_id : run.run_id}
        </div>
      </div>
      <div
        style={{
          padding: 16,
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
    </aside>
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
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <h3>{span.name || "(unnamed)"}</h3>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <KindBadge kind={span.kind} />
          <StatusPill status={span.status} />
          {capture ? (
            <span
              className="badge badge-neutral"
              title={`replay-capture · ${capture.kind} · ${capture.size_bytes} bytes`}
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
          style={{
            padding: "8px 12px",
            background: "var(--danger-soft)",
            color: "var(--danger)",
            fontSize: 12,
            borderRadius: "var(--r-2)",
          }}
        >
          <strong>{span.error_kind || "error"}:</strong> {span.error_message}
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
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <h3>{run.name || "(unnamed)"}</h3>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
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
              border: "1px solid var(--border)",
              borderRadius: "var(--r-2)",
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
                  borderBottom: "1px solid var(--border)",
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
        display: "flex",
        flexDirection: "column",
        gap: 0,
        border: "1px solid var(--border)",
        borderRadius: "var(--r-2)",
        background: "var(--surface)",
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
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span
        style={{
          fontSize: 11,
          fontWeight: 500,
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          color: "var(--text-3)",
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
        gridTemplateColumns: "minmax(0, 90px) 1fr",
        gap: 12,
        padding: "8px 12px",
        borderBottom: "1px solid var(--border)",
        fontSize: 12,
      }}
    >
      <span
        style={{
          fontSize: 11,
          fontWeight: 500,
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          color: "var(--text-3)",
        }}
      >
        {k}
      </span>
      <span
        className="mono"
        style={{
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
        fontSize: 11,
        lineHeight: 1.5,
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
        : k === "retriever" || k === "retr"
          ? "kind-retr"
          : "kind-chain";
  return <span className={`kind-badge ${cls}`}>{kind || "chain"}</span>;
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

function StatusDot({ status }: { status: Status }) {
  const cls =
    status === "ok"
      ? "dot dot-success"
      : status === "error"
        ? "dot dot-danger"
        : "dot dot-warn";
  return <span className={cls} aria-hidden />;
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

function barColorForKind(kind: string): { fill: string; stroke: string } {
  const k = (kind || "").toLowerCase();
  if (k === "llm")
    return { fill: "var(--kind-llm-bg)", stroke: "var(--kind-llm)" };
  if (k === "tool")
    return { fill: "var(--kind-tool-bg)", stroke: "var(--kind-tool)" };
  if (k === "retriever" || k === "retr")
    return { fill: "var(--kind-retr-bg)", stroke: "var(--kind-retr)" };
  return { fill: "var(--kind-chain-bg)", stroke: "var(--kind-chain)" };
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
