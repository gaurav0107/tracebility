import Link from "next/link";
import { AuthClient, type OAuthProviders } from "@/components/AuthClient";
import { LoginBackdrop } from "@/components/LoginBackdrop";
import { apiGet } from "@/lib/api";

/**
 * Unified sign-in / sign-up page (design 4A — "sign-in refined").
 *
 * A single full-bleed light scene: an animated trace-graph backdrop (source
 * signals converging left → outcomes exiting right) behind a centered auth
 * card, framed by the brand top-left, a docs link top-right, and a social-proof
 * strip along the bottom. The card content (OAuth, email/password, SSO,
 * create-account toggle, live ticker) lives in <AuthClient/>.
 *
 * `?tab=signup` defaults to the create-account mode; `?return_to=/path` is
 * preserved through the OAuth round-trip. Workspace SSO (per-workspace OIDC)
 * lives at /workspace/sso.
 */

export const dynamic = "force-dynamic";

export default async function LoginPage({
  searchParams,
}: {
  searchParams?: { tab?: string; return_to?: string };
}) {
  const providersRes = await apiGet<OAuthProviders>("/v1/auth/oauth/providers");
  const providers: OAuthProviders = providersRes.data ?? {
    google: false,
    github: false,
  };
  const initialTab = searchParams?.tab === "signup" ? "signup" : "login";
  const returnTo = sanitizeReturnTo(searchParams?.return_to ?? null);

  return (
    <main
      style={{
        position: "relative",
        minHeight: "100vh",
        background: "var(--bg)",
        overflow: "hidden",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "96px 24px",
      }}
    >
      <style>{`
        .lg-proof { display: none; }
        @media (min-width: 720px) { .lg-proof { display: flex; } }
      `}</style>

      <LoginBackdrop />

      {/* top-left brand */}
      <Link
        href="/"
        aria-label="langprobe home"
        style={{
          position: "absolute",
          left: 36,
          top: 32,
          zIndex: 3,
          display: "flex",
          alignItems: "center",
          gap: 9,
          textDecoration: "none",
          color: "var(--text)",
        }}
      >
        <span
          aria-hidden
          style={{
            width: 24,
            height: 24,
            borderRadius: 8,
            background: "var(--accent)",
            display: "grid",
            placeItems: "center",
            boxShadow: "var(--shadow-logo)",
          }}
        >
          <span style={{ width: 9, height: 9, borderRadius: 3, background: "#FFFFFF" }} />
        </span>
        <span style={{ fontSize: 15, fontWeight: 800, letterSpacing: "-0.01em" }}>
          langprobe
        </span>
      </Link>

      {/* top-right docs link */}
      <a
        href="https://github.com/langprobe/langprobe"
        target="_blank"
        rel="noopener noreferrer"
        style={{
          position: "absolute",
          right: 36,
          top: 30,
          zIndex: 3,
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: 12.5,
          fontWeight: 600,
          color: "var(--text-2)",
          border: "1px solid var(--border)",
          background: "var(--surface)",
          borderRadius: 999,
          padding: "8px 18px",
          textDecoration: "none",
        }}
      >
        docs <span aria-hidden style={{ fontSize: 11, color: "var(--text-4)" }}>↗</span>
      </a>

      {/* centered auth card */}
      <div
        className="stage-item"
        style={{
          position: "relative",
          zIndex: 2,
          width: "100%",
          maxWidth: 424,
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: 26,
          boxShadow: "0 32px 80px -20px rgba(10,30,60,0.22)",
          padding: 36,
          animationDelay: "120ms",
        }}
      >
        <AuthClient initialTab={initialTab} providers={providers} returnTo={returnTo} />
      </div>

      {/* bottom social-proof strip */}
      <div
        className="lg-proof"
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: 64,
          zIndex: 1,
          justifyContent: "center",
          gap: 28,
          fontFamily: "var(--f-mono)",
          fontSize: 11,
          color: "var(--text-4)",
          flexWrap: "wrap",
          padding: "0 24px",
        }}
      >
        <span>
          <span style={{ color: "var(--text-2)", fontWeight: 600 }}>12.4k</span> github stars
        </span>
        <span style={{ color: "var(--border-frame)" }}>|</span>
        <span>
          <span style={{ color: "var(--text-2)", fontWeight: 600 }}>2,140</span> self-hosted
          deployments
        </span>
        <span style={{ color: "var(--border-frame)" }}>|</span>
        <span>
          <span style={{ color: "var(--text-2)", fontWeight: 600 }}>98M</span> spans traced this
          week
        </span>
      </div>
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: 26,
          zIndex: 1,
          textAlign: "center",
          fontFamily: "var(--f-mono)",
          fontSize: 10.5,
          letterSpacing: "0.12em",
          textTransform: "uppercase",
          color: "var(--text-4)",
        }}
      >
        open source · apache-2.0 · your data, your call
      </div>
    </main>
  );
}

function sanitizeReturnTo(v: string | null): string | null {
  if (!v) return null;
  if (!v.startsWith("/") || v.startsWith("//")) return null;
  return v;
}
