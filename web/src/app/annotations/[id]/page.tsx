import Link from "next/link";
import { Shell } from "@/components/Shell";
import {
  type AnnotationItemRow,
  type AnnotationQueueRow,
  AnnotationLabelForm,
  DeleteAnnotationQueueButton,
} from "@/components/AnnotationsClient";
import { apiGet } from "@/lib/api";
import { resolveActiveProject } from "@/lib/projects";

/**
 * Annotation queue detail — sticky label form for the next pending item
 * sits above the items table so reviewers can rip through a queue with
 * minimal pointer travel. Once an item is submitted, server refresh
 * advances the form to the next pending item; if all are reviewed, the
 * form disappears and the queue reads "complete".
 */

export const dynamic = "force-dynamic";

interface AnnotationItemList {
  items: AnnotationItemRow[];
}

export default async function AnnotationQueueDetailPage({
  params,
}: {
  params: { id: string };
}) {
  const queueId = decodeURIComponent(params.id);
  const { active, all, reason } = await resolveActiveProject();

  if (!active) {
    return (
      <Shell active={null} projects={all}>
        <PageInterior>
          <BreadcrumbBar />
          <PageHeader title="Annotation queue" subtitle={queueId} />
          <UnconfiguredState reason={reason} />
        </PageInterior>
      </Shell>
    );
  }

  const queueRes = await apiGet<AnnotationQueueRow>(
    `/v1/annotations/${queueId}`,
  );
  const queue = queueRes.data;

  if (!queue) {
    return (
      <Shell active={active} projects={all}>
        <PageInterior>
          <BreadcrumbBar />
          <PageHeader title="Annotation queue" subtitle={queueId} />
          <NotFoundState reason={queueRes.error} status={queueRes.status} />
        </PageInterior>
      </Shell>
    );
  }

  const itemsRes = await apiGet<AnnotationItemList>(
    `/v1/annotations/${queueId}/items?limit=500`,
  );
  const items = itemsRes.data?.items ?? [];

  const counts = items.reduce(
    (acc, it) => {
      acc[it.status]++;
      return acc;
    },
    { pending: 0, done: 0, skipped: 0 } as Record<
      AnnotationItemRow["status"],
      number
    >,
  );

  const nextPending = items.find((it) => it.status === "pending") ?? null;

  return (
    <Shell active={active} projects={all}>
      <PageInterior>
        <BreadcrumbBar />
        <PageHeader
          title={queue.name}
          subtitle={
            <span className="mono" style={{ color: "var(--text-3)" }}>
              {queue.id.slice(0, 8)}
            </span>
          }
          right={
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <QueueStatusBadge status={queue.status} />
              <DeleteAnnotationQueueButton
                queueId={queue.id}
                name={queue.name}
                redirectTo="/annotations"
              />
            </div>
          }
        />
        {queue.description ? (
          <p
            style={{
              margin: 0,
              color: "var(--text-2)",
              fontSize: 13,
              lineHeight: 1.5,
            }}
          >
            {queue.description}
          </p>
        ) : null}
        <KpiStrip
          total={queue.item_total}
          done={queue.item_done}
          pending={counts.pending}
          skipped={counts.skipped}
        />
        <SettingsCard queue={queue} />
        {nextPending ? (
          <AnnotationLabelForm
            queueId={queue.id}
            item={nextPending}
            rubric={queue.rubric}
            projectId={queue.project_id}
          />
        ) : queue.status === "complete" ? (
          <CompleteCard total={queue.item_total} done={queue.item_done} />
        ) : null}
        <ItemsCard
          items={items}
          rubric={queue.rubric}
          reason={itemsRes.error}
          status={itemsRes.status}
        />
      </PageInterior>
    </Shell>
  );
}

function PageInterior({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        padding: 24,
        display: "flex",
        flexDirection: "column",
        gap: 16,
        maxWidth: 1400,
      }}
    >
      {children}
    </div>
  );
}

function BreadcrumbBar() {
  return (
    <div style={{ fontSize: 12, color: "var(--text-3)" }}>
      <Link href="/annotations" style={{ color: "var(--text-3)" }}>
        ← all queues
      </Link>
    </div>
  );
}

function PageHeader({
  title,
  subtitle,
  right,
}: {
  title: string;
  subtitle: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <header
      style={{
        display: "flex",
        alignItems: "baseline",
        justifyContent: "space-between",
        gap: 16,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
        <h1>{title}</h1>
        {subtitle}
      </div>
      {right}
    </header>
  );
}

function QueueStatusBadge({
  status,
}: {
  status: AnnotationQueueRow["status"];
}) {
  if (status === "complete") {
    return <span className="badge badge-success">complete</span>;
  }
  if (status === "archived") {
    return <span className="badge badge-neutral">archived</span>;
  }
  return <span className="badge badge-warn">open</span>;
}

function KpiStrip({
  total,
  done,
  pending,
  skipped,
}: {
  total: number;
  done: number;
  pending: number;
  skipped: number;
}) {
  const pct = total === 0 ? 0 : Math.round((done / total) * 100);
  return (
    <section
      className="card"
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(5, minmax(0, 1fr))",
        gap: 0,
        overflow: "hidden",
      }}
    >
      <KpiCell label="Total" value={String(total)} />
      <KpiCell
        label="Done"
        value={String(done)}
        valueColor={done > 0 ? "var(--success, #1f7a3a)" : undefined}
      />
      <KpiCell
        label="Pending"
        value={String(pending)}
        valueColor={pending > 0 ? "var(--warn, #a36a14)" : undefined}
      />
      <KpiCell label="Skipped" value={String(skipped)} />
      <KpiCell label="Progress" value={`${pct}%`} mono />
    </section>
  );
}

function KpiCell({
  label,
  value,
  mono,
  valueColor,
}: {
  label: string;
  value: string;
  mono?: boolean;
  valueColor?: string;
}) {
  return (
    <div
      style={{
        padding: "16px 20px",
        borderRight: "1px solid var(--border)",
        display: "flex",
        flexDirection: "column",
        gap: 4,
      }}
    >
      <span
        style={{
          fontSize: 11,
          color: "var(--text-3)",
          textTransform: "uppercase",
          letterSpacing: 0.4,
        }}
      >
        {label}
      </span>
      <span
        className={mono ? "mono" : "num"}
        style={{
          fontSize: 22,
          fontWeight: 500,
          color: valueColor ?? "var(--text-1)",
        }}
      >
        {value}
      </span>
    </div>
  );
}

function SettingsCard({ queue }: { queue: AnnotationQueueRow }) {
  return (
    <section className="card card-pad-lg">
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
          gap: 16,
        }}
      >
        <SettingBlock label="Rubric">
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            <span
              className="mono"
              style={{ fontSize: 12, color: "var(--text-3)" }}
            >
              {queue.rubric.score}
            </span>
            {queue.rubric.labels.map((l) => (
              <span
                key={l}
                className="badge badge-neutral"
                style={{ fontSize: 11 }}
              >
                {l}
              </span>
            ))}
          </div>
        </SettingBlock>
        <SettingBlock label="Sampling">
          <span className="mono" style={{ fontSize: 12 }}>
            n={queue.sampling.sample_size} ·{" "}
            {fmtDuration(queue.sampling.window_seconds)}
            {queue.sampling.status !== "any" ? (
              <span style={{ color: "var(--text-3)" }}>
                {" · "}
                {queue.sampling.status}
              </span>
            ) : null}
          </span>
        </SettingBlock>
        <SettingBlock label="Created">
          <span
            className="mono"
            style={{ fontSize: 12, color: "var(--text-3)" }}
          >
            {fmtDateTime(queue.created_at)}
          </span>
        </SettingBlock>
      </div>
    </section>
  );
}

function SettingBlock({
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
          color: "var(--text-3)",
          textTransform: "uppercase",
          letterSpacing: 0.4,
        }}
      >
        {label}
      </span>
      {children}
    </div>
  );
}

function CompleteCard({ total, done }: { total: number; done: number }) {
  if (total === 0) {
    return (
      <section
        className="card card-pad-lg"
        style={{ borderColor: "var(--warn, #9C5400)" }}
      >
        <h2 style={{ marginBottom: 6, color: "var(--warn, #9C5400)" }}>
          No items sampled
        </h2>
        <p
          style={{
            color: "var(--text-2)",
            margin: 0,
            fontSize: 13,
            lineHeight: 1.55,
          }}
        >
          The sampler found zero runs matching this queue&apos;s window and
          status filter. Widen the window or relax the status filter when
          you create the next queue — sampling happens once, at creation.
        </p>
      </section>
    );
  }
  return (
    <section
      className="card card-pad-lg"
      style={{ borderColor: "var(--success, #1f7a3a)" }}
    >
      <h2 style={{ marginBottom: 6, color: "var(--success, #1f7a3a)" }}>
        Queue complete
      </h2>
      <p
        style={{
          color: "var(--text-2)",
          margin: 0,
          fontSize: 13,
          lineHeight: 1.55,
        }}
      >
        {done} of {total} items reviewed. Each submission landed in the
        same <code>eval_score</code> store as LLM-judge scores and
        end-user feedback, tagged{" "}
        <code className="mono">judge_name=&apos;human&apos;</code>.
      </p>
    </section>
  );
}

function ItemsCard({
  items,
  rubric,
  reason,
  status,
}: {
  items: AnnotationItemRow[];
  rubric: AnnotationQueueRow["rubric"];
  reason: string | null;
  status: number;
}) {
  return (
    <section className="card" style={{ overflow: "hidden" }}>
      <div className="card-head">
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <h2>Items</h2>
          <span className="card-sub">
            one row per sampled run; click run_id to inspect the trace
          </span>
        </div>
      </div>
      {reason && items.length === 0 ? (
        <ItemsErrorState reason={reason} status={status} />
      ) : items.length === 0 ? (
        <EmptyItemsState />
      ) : (
        <div style={{ overflow: "auto" }}>
          <table className="table">
            <thead>
              <tr>
                <th>Status</th>
                <th>Run</th>
                <th>Label</th>
                {rubric.score === "scalar" ? (
                  <th style={{ textAlign: "right" }}>Score</th>
                ) : null}
                <th>Rationale</th>
                <th style={{ textAlign: "right" }}>Reviewed</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.id}>
                  <td>
                    <ItemStatusBadge status={it.status} />
                  </td>
                  <td>
                    <Link
                      href={`/runs/${it.run_id}`}
                      className="mono"
                      style={{
                        fontSize: 12,
                        color: "var(--link)",
                        textDecoration: "none",
                      }}
                    >
                      {it.run_id.slice(0, 8)}
                    </Link>
                  </td>
                  <td>
                    <LabelBadge label={it.label} rubric={rubric} />
                  </td>
                  {rubric.score === "scalar" ? (
                    <td className="num" style={{ textAlign: "right" }}>
                      <ScoreCell score={it.score} />
                    </td>
                  ) : null}
                  <td
                    style={{
                      maxWidth: 420,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      color: "var(--text-2)",
                      fontSize: 13,
                    }}
                    title={it.rationale ?? "—"}
                  >
                    {it.rationale ?? (
                      <span style={{ color: "var(--text-3)" }}>—</span>
                    )}
                  </td>
                  <td
                    className="num"
                    style={{ textAlign: "right", color: "var(--text-3)" }}
                  >
                    {it.reviewed_at ? fmtDateTime(it.reviewed_at) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ItemStatusBadge({ status }: { status: AnnotationItemRow["status"] }) {
  if (status === "done") {
    return <span className="badge badge-success">done</span>;
  }
  if (status === "skipped") {
    return <span className="badge badge-neutral">skipped</span>;
  }
  return <span className="badge badge-warn">pending</span>;
}

function LabelBadge({
  label,
  rubric,
}: {
  label: string | null;
  rubric: AnnotationQueueRow["rubric"];
}) {
  if (!label) return <span style={{ color: "var(--text-3)" }}>—</span>;
  const isPositive =
    rubric.score === "binary" && label === rubric.labels[0];
  const cls = isPositive ? "badge-success" : "badge-neutral";
  return <span className={`badge ${cls}`}>{label}</span>;
}

function ScoreCell({ score }: { score: number | null }) {
  if (score === null) {
    return <span style={{ color: "var(--text-3)" }}>—</span>;
  }
  const color =
    score >= 0.9
      ? "var(--success, #1f7a3a)"
      : score >= 0.5
        ? "var(--warn, #a36a14)"
        : "var(--danger)";
  return (
    <span className="mono" style={{ color, fontWeight: 500 }}>
      {(score * 100).toFixed(1)}%
    </span>
  );
}

function EmptyItemsState() {
  return (
    <div style={{ padding: 32 }}>
      <h3 style={{ marginBottom: 6 }}>No items in this queue.</h3>
      <p
        style={{
          color: "var(--text-2)",
          margin: 0,
          lineHeight: 1.55,
          maxWidth: 640,
        }}
      >
        The sampler returned zero runs from the configured window. Widen
        the window or relax the status filter when you create the next
        queue.
      </p>
    </div>
  );
}

function ItemsErrorState({
  reason,
  status,
}: {
  reason: string | null;
  status: number;
}) {
  return (
    <div style={{ padding: 32 }}>
      <h3 style={{ marginBottom: 6 }}>Unable to load items</h3>
      <p
        style={{
          color: "var(--text-2)",
          margin: 0,
          lineHeight: 1.55,
          maxWidth: 640,
        }}
      >
        {status === 503
          ? "The data plane (Postgres) is unreachable."
          : "The control plane returned an error."}
      </p>
      {reason ? (
        <p
          className="mono"
          style={{ marginTop: 12, fontSize: 11, color: "var(--text-3)" }}
        >
          ({reason})
        </p>
      ) : null}
    </div>
  );
}

function NotFoundState({
  reason,
  status,
}: {
  reason: string | null;
  status: number;
}) {
  return (
    <div className="card card-pad-lg">
      <h2 style={{ marginBottom: 8 }}>
        {status === 404 ? "Queue not found" : "Unable to load queue"}
      </h2>
      <p style={{ color: "var(--text-2)", lineHeight: 1.55, margin: 0 }}>
        {status === 404
          ? "No annotation queue matches this id in the active project."
          : "The control plane returned an error."}
      </p>
      {reason ? (
        <p
          className="mono"
          style={{ marginTop: 12, fontSize: 11, color: "var(--text-3)" }}
        >
          ({reason})
        </p>
      ) : null}
    </div>
  );
}

function UnconfiguredState({ reason }: { reason: string | null }) {
  return (
    <div className="card card-pad-lg">
      <h2 style={{ marginBottom: 8 }}>No project resolved</h2>
      <p style={{ color: "var(--text-2)", lineHeight: 1.55, margin: 0 }}>
        Run the setup wizard or create a project before viewing
        annotation queues.
      </p>
      {reason ? (
        <p
          className="mono"
          style={{ marginTop: 12, fontSize: 11, color: "var(--text-3)" }}
        >
          ({reason})
        </p>
      ) : null}
    </div>
  );
}

function fmtDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}

function fmtDateTime(iso: string): string {
  try {
    return new Date(iso).toISOString().replace("T", " ").slice(0, 16);
  } catch {
    return iso;
  }
}
