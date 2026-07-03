"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";

/**
 * Unified sign-in / sign-up card (design 4A — centered card over the
 * trace-graph backdrop).
 *
 * The card leads with "welcome back" (login). OAuth buttons + email/password
 * are shared across modes; the bottom "create an account" link toggles to
 * signup, which only changes (a) the heading, (b) the submit copy, and (c) the
 * OAuth `intent=` sent to the api so first-time vs returning is audited right.
 *
 * Email+password posts to /api/auth/login for both modes (v1 has no
 * self-service password signup — operators bootstrap via /v1/setup; everyone
 * else uses OAuth). The `LAST USED` badge records the last OAuth provider in
 * localStorage. A live ticker cycles real product signals under the heading.
 */

export interface OAuthProviders {
  google: boolean;
  github: boolean;
}

type Tab = "login" | "signup";
const LAST_USED_KEY = "langprobe:auth:last-provider";

const TICKER_LINES = [
  "3,214 runs traced in the last minute",
  "replay rep_c11d finished · 9/10 deterministic",
  "eval suite rag-v3 passing · 94.2%",
  "alert cleared · error rate back under 2%",
];

export function AuthClient({
  initialTab,
  providers,
  returnTo,
}: {
  initialTab: Tab;
  providers: OAuthProviders;
  returnTo: string | null;
}) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const anyOAuth = providers.google || providers.github;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20, width: "100%" }}>
      <CardHeader tab={tab} />

      {anyOAuth ? (
        <OAuthButtons tab={tab} providers={providers} returnTo={returnTo} />
      ) : null}

      {anyOAuth ? <Divider label="or continue with email" /> : null}

      <PasswordForm tab={tab} />

      <SsoRow />

      <p style={{ textAlign: "center", fontSize: 12.5, color: "var(--text-3)", margin: 0 }}>
        {tab === "login" ? "new here? " : "already have an account? "}
        <button
          type="button"
          onClick={() => setTab(tab === "login" ? "signup" : "login")}
          style={{
            background: "none",
            border: 0,
            padding: 0,
            cursor: "pointer",
            font: "inherit",
            color: "var(--link)",
            fontWeight: 700,
          }}
        >
          {tab === "login" ? "create an account" : "sign in"}
        </button>
      </p>

      {!anyOAuth ? (
        <p
          className="mono"
          style={{ margin: 0, fontSize: 11, color: "var(--text-3)", lineHeight: 1.55 }}
        >
          OAuth providers are not configured for this deployment. The operator
          can enable them by setting <code>OAUTH_GOOGLE_CLIENT_ID</code> /{" "}
          <code>OAUTH_GITHUB_CLIENT_ID</code> on the api service.
        </p>
      ) : null}

      <p
        style={{
          textAlign: "center",
          fontSize: 11,
          color: "var(--text-4)",
          margin: 0,
          lineHeight: 1.55,
        }}
      >
        By continuing, you agree to the{" "}
        <a href="/terms" style={{ color: "var(--link)" }}>
          terms
        </a>{" "}
        and{" "}
        <a href="/privacy" style={{ color: "var(--link)" }}>
          privacy policy
        </a>
        .
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Header — app mark, heading, live ticker
// ---------------------------------------------------------------------------

function CardHeader({ tab }: { tab: Tab }) {
  const [idx, setIdx] = useState(0);
  useEffect(() => {
    if (
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    ) {
      return; // respect reduced motion — leave a static line
    }
    const t = setInterval(() => setIdx((i) => (i + 1) % TICKER_LINES.length), 3200);
    return () => clearInterval(t);
  }, []);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 10,
        marginBottom: 2,
      }}
    >
      <span
        aria-hidden
        style={{
          width: 44,
          height: 44,
          borderRadius: 14,
          background: "var(--accent)",
          display: "grid",
          placeItems: "center",
          boxShadow: "0 8px 20px rgba(4,133,247,0.4)",
        }}
      >
        <span style={{ width: 16, height: 16, borderRadius: 5, background: "#FFFFFF" }} />
      </span>
      <span style={{ fontSize: 19, fontWeight: 800, letterSpacing: "-0.02em" }}>
        {tab === "login" ? "welcome back" : "create your account"}
      </span>
      <span
        style={{
          fontFamily: "var(--f-mono)",
          fontSize: 11.5,
          color: "var(--text-4)",
          textAlign: "center",
        }}
      >
        {TICKER_LINES[idx]}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// OAuth buttons
// ---------------------------------------------------------------------------

function OAuthButtons({
  tab,
  providers,
  returnTo,
}: {
  tab: Tab;
  providers: OAuthProviders;
  returnTo: string | null;
}) {
  const [lastUsed, setLastUsed] = useState<string | null>(null);
  useEffect(() => {
    try {
      setLastUsed(window.localStorage.getItem(LAST_USED_KEY));
    } catch {
      // localStorage unavailable (SSR / private mode); harmless
    }
  }, []);

  const params = new URLSearchParams({ intent: tab });
  if (returnTo) params.set("return_to", returnTo);
  const startUrl = (provider: string): string =>
    `/api/auth/oauth/${provider}/start?${params.toString()}`;

  function recordUsed(provider: string) {
    try {
      window.localStorage.setItem(LAST_USED_KEY, provider);
    } catch {
      // ignore
    }
  }

  const buttons: Array<{ key: string; label: string; icon: React.ReactNode }> = [];
  if (providers.google) {
    buttons.push({ key: "google", label: "google", icon: <GoogleMark /> });
  }
  if (providers.github) {
    buttons.push({ key: "github", label: "github", icon: <GithubMark /> });
  }

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${buttons.length}, minmax(0, 1fr))`,
        gap: 10,
      }}
    >
      {buttons.map((b) => (
        <a
          key={b.key}
          href={startUrl(b.key)}
          onClick={() => recordUsed(b.key)}
          style={{
            position: "relative",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            fontSize: 13,
            fontWeight: 700,
            color: "var(--text)",
            border: "1px solid var(--border)",
            background: "var(--surface)",
            borderRadius: 999,
            padding: "11px 0",
            textDecoration: "none",
            transition: "background 120ms",
          }}
        >
          {b.icon}
          {b.label}
          {lastUsed === b.key ? <LastUsedBadge /> : null}
        </a>
      ))}
    </div>
  );
}

function LastUsedBadge() {
  return (
    <span
      style={{
        position: "absolute",
        top: -9,
        right: 14,
        background: "var(--accent)",
        color: "var(--accent-fg)",
        fontSize: 8.5,
        fontWeight: 700,
        letterSpacing: "0.06em",
        textTransform: "uppercase",
        padding: "2px 8px",
        borderRadius: 999,
        lineHeight: 1.2,
        pointerEvents: "none",
      }}
    >
      last used
    </span>
  );
}

// ---------------------------------------------------------------------------
// Password form
// ---------------------------------------------------------------------------

function PasswordForm({ tab }: { tab: Tab }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!email || !password) {
      setError("email and password are required");
      return;
    }
    startTransition(async () => {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        let detail = `request failed (${res.status})`;
        try {
          const body = (await res.json()) as { detail?: string };
          if (body.detail) detail = body.detail;
        } catch {
          // ignore
        }
        if (tab === "signup" && res.status === 401) {
          detail =
            "No account found. Use Google or GitHub to sign up. Password signup is operator-only in v1.";
        }
        setError(detail);
        return;
      }
      router.push("/");
      router.refresh();
    });
  }

  const invalid = error !== null;

  return (
    <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <label className="field">
        <span className="field-label">Email</span>
        <span className="input-shell">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            placeholder="you@example.com"
            aria-invalid={invalid}
            required
          />
        </span>
      </label>

      <label className="field">
        <span className="field-label" style={{ display: "flex", alignItems: "baseline" }}>
          <span style={{ flex: 1 }}>Password</span>
          {tab === "login" ? (
            <a
              href="/forgot"
              style={{
                fontSize: 11.5,
                fontWeight: 700,
                color: "var(--link)",
                textDecoration: "none",
                textTransform: "none",
                letterSpacing: "normal",
              }}
              onClick={(e) => {
                e.preventDefault();
                /* TODO: password-reset flow on roadmap */
              }}
            >
              forgot?
            </a>
          ) : null}
        </span>
        <span className="input-shell">
          <input
            type={showPw ? "text" : "password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={tab === "signup" ? "new-password" : "current-password"}
            placeholder="••••••••"
            aria-invalid={invalid}
            required
          />
          <button
            type="button"
            className="ie-sfx-btn"
            onClick={() => setShowPw((s) => !s)}
            aria-label={showPw ? "Hide password" : "Show password"}
          >
            {showPw ? "hide" : "show"}
          </button>
        </span>
      </label>

      {error ? (
        <p className="field-error" role="alert" style={{ margin: 0 }}>
          {error}
        </p>
      ) : null}

      <button
        type="submit"
        className="btn btn-primary btn-lg"
        disabled={pending}
        style={{ width: "100%" }}
      >
        {pending
          ? tab === "signup"
            ? "creating account…"
            : "signing in…"
          : tab === "signup"
            ? "create account"
            : "sign in"}
      </button>
    </form>
  );
}

function SsoRow() {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
        flexWrap: "wrap",
      }}
    >
      <span
        aria-hidden
        style={{ width: 6, height: 6, borderRadius: 999, background: "var(--success)" }}
      />
      <span style={{ fontFamily: "var(--f-mono)", fontSize: 11, color: "var(--text-3)" }}>
        sso · saml &amp; oidc for self-hosted
      </span>
      <Link
        href="/workspace/sso"
        style={{ fontSize: 11.5, fontWeight: 700, color: "var(--link)", textDecoration: "none" }}
      >
        use sso →
      </Link>
    </div>
  );
}

function Divider({ label }: { label: string }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        color: "var(--text-4)",
        fontSize: 12,
        fontWeight: 500,
      }}
    >
      <span style={{ flex: 1, height: 1, background: "var(--border)" }} />
      <span>{label}</span>
      <span style={{ flex: 1, height: 1, background: "var(--border)" }} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Provider marks
// ---------------------------------------------------------------------------

function GoogleMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 48 48" aria-hidden>
      <path
        fill="#FFC107"
        d="M43.6 20.5H42V20H24v8h11.3c-1.6 4.5-5.9 7.5-11.3 7.5-6.6 0-12-5.4-12-12s5.4-12 12-12c3 0 5.7 1.1 7.8 3l5.7-5.7C34.2 5.5 29.4 3.5 24 3.5 12.7 3.5 3.5 12.7 3.5 24S12.7 44.5 24 44.5 44.5 35.3 44.5 24c0-1.2-.1-2.3-.3-3.5z"
      />
      <path
        fill="#FF3D00"
        d="M6.3 14.7l6.6 4.8C14.7 15 19 12 24 12c3 0 5.7 1.1 7.8 3l5.7-5.7C34.2 5.5 29.4 3.5 24 3.5 16.3 3.5 9.7 7.7 6.3 14.7z"
      />
      <path
        fill="#4CAF50"
        d="M24 44.5c5.3 0 10.1-2 13.8-5.4l-6.4-5.2c-2 1.4-4.6 2.2-7.4 2.2-5.4 0-9.6-3-11.3-7.5l-6.6 5.1C9.7 40.3 16.4 44.5 24 44.5z"
      />
      <path
        fill="#1976D2"
        d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.3 4.1-4.3 5.4l6.4 5.2c4.5-4.1 7.1-10.1 7.1-17.1 0-1.2-.1-2.3-.3-3.5z"
      />
    </svg>
  );
}

function GithubMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden>
      <path
        fill="currentColor"
        d="M8 0C3.6 0 0 3.6 0 8c0 3.5 2.3 6.5 5.5 7.6.4.1.5-.2.5-.4v-1.4c-2.2.5-2.7-1-2.7-1-.4-.9-.9-1.2-.9-1.2-.7-.5.1-.5.1-.5.8.1 1.2.8 1.2.8.7 1.2 1.9.9 2.4.7.1-.5.3-.9.5-1.1-1.8-.2-3.6-.9-3.6-4 0-.9.3-1.6.8-2.1-.1-.2-.4-1 .1-2.1 0 0 .7-.2 2.2.8.6-.2 1.3-.3 2-.3s1.4.1 2 .3c1.5-1 2.2-.8 2.2-.8.4 1.1.2 1.9.1 2.1.5.6.8 1.3.8 2.1 0 3.1-1.9 3.7-3.6 3.9.3.3.5.8.5 1.5v2.2c0 .2.1.5.5.4C13.7 14.5 16 11.5 16 8c0-4.4-3.6-8-8-8z"
      />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Logout (used by the app shell)
// ---------------------------------------------------------------------------

export function LogoutLink() {
  const [pending, startTransition] = useTransition();
  return (
    <button
      type="button"
      className="btn btn-ghost"
      style={{ fontSize: 12 }}
      disabled={pending}
      onClick={() => {
        startTransition(async () => {
          try {
            await fetch("/api/auth/logout", {
              method: "POST",
              credentials: "same-origin",
              cache: "no-store",
            });
          } catch {
            // even if the request fails, fall through to a hard reload — the
            // worst case is the user lands on /login with a stale cookie, the
            // middleware accepts them, and they're booted on the next 401.
          }
          window.location.href = "/login";
        });
      }}
    >
      {pending ? "signing out…" : "sign out"}
    </button>
  );
}
