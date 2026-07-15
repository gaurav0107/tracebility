import Link from "next/link";

/**
 * App-wide not-found surface. Replaces Next's bare default (no chrome,
 * no way back) for bad run ids, deleted branches, and stale deep
 * links. Kept chrome-free on purpose — the Shell needs a resolved
 * project — but styled and with recovery paths.
 */
export default function NotFound() {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--bg)",
      }}
    >
      <div style={{ textAlign: "center", maxWidth: 420, padding: 24 }}>
        <p
          className="mono"
          style={{ fontSize: 13, color: "var(--text-3)", margin: 0 }}
        >
          404
        </p>
        <h1 style={{ fontSize: 20, margin: "8px 0 6px" }}>
          Nothing at this address
        </h1>
        <p
          style={{
            fontSize: 13,
            color: "var(--text-2)",
            lineHeight: 1.55,
            margin: "0 0 20px",
          }}
        >
          The run, branch, or page you&apos;re after doesn&apos;t exist in
          this workspace — it may have been deleted, or the id in the URL
          is from another project.
        </p>
        <div
          style={{ display: "flex", gap: 8, justifyContent: "center" }}
        >
          <Link href="/runs" className="btn btn-primary">
            go to tracing
          </Link>
          <Link href="/" className="btn btn-ghost">
            overview
          </Link>
        </div>
      </div>
    </div>
  );
}
