/**
 * Sign-in backdrop (design 4A — "trace-graph backdrop").
 *
 * Pure-CSS decorative scene behind the centered auth card: a faint grid, a
 * soft blue glow at top-center, and a converging trace graph — three source
 * nodes on the left (sdk / otel / shim) whose signals travel inward and three
 * outcome nodes on the right (evals / replays / alerts) exiting. Every dot is
 * a CSS animation; nothing here is interactive, so it stays a server component.
 *
 * The graph (`.lg-graph`) is hidden under 1100px so it never crowds the card
 * on narrow viewports — the grid + glow carry the atmosphere there. The global
 * prefers-reduced-motion rule in globals.css zeroes every animation.
 */

import type { CSSProperties } from "react";

type Node = {
  side: "left" | "right";
  top: string;
  label: string;
  meta: string;
  fg: string;
  bg: string;
  border: string;
  dot: string;
  glow: string;
  rot: number; // path tilt in degrees
  dur: string;
  delay: string;
};

const NODES: Node[] = [
  {
    side: "left", top: "22%", label: "sdk · python", meta: "3.2k runs/min",
    fg: "#0A66C2", bg: "rgba(4,133,247,0.06)", border: "rgba(4,133,247,0.25)",
    dot: "#0485F7", glow: "rgba(4,133,247,0.8)", rot: 14, dur: "4.4s", delay: "0s",
  },
  {
    side: "left", top: "50%", label: "otel · collector", meta: "1.8k spans/s",
    fg: "#0A66C2", bg: "rgba(4,133,247,0.06)", border: "rgba(4,133,247,0.25)",
    dot: "#0485F7", glow: "rgba(4,133,247,0.8)", rot: 0, dur: "5.2s", delay: "1.3s",
  },
  {
    side: "left", top: "78%", label: "shim · langsmith", meta: "640 runs/min",
    fg: "#0A66C2", bg: "rgba(4,133,247,0.06)", border: "rgba(4,133,247,0.25)",
    dot: "#0485F7", glow: "rgba(4,133,247,0.8)", rot: -14, dur: "4.8s", delay: "2.6s",
  },
  {
    side: "right", top: "22%", label: "evals · passing", meta: "94.2%",
    fg: "#157A45", bg: "#E7F4ED", border: "rgba(21,122,69,0.28)",
    dot: "#157A45", glow: "rgba(21,122,69,0.7)", rot: -14, dur: "4.6s", delay: "0.8s",
  },
  {
    side: "right", top: "50%", label: "replays · exact", meta: "12/min",
    fg: "#0A66C2", bg: "rgba(4,133,247,0.06)", border: "rgba(4,133,247,0.25)",
    dot: "#0485F7", glow: "rgba(4,133,247,0.8)", rot: 0, dur: "5.6s", delay: "2s",
  },
  {
    side: "right", top: "78%", label: "alerts · 2 firing", meta: "err > 2%",
    fg: "#C0382B", bg: "#FBEAE7", border: "rgba(192,56,43,0.25)",
    dot: "#C0382B", glow: "rgba(192,56,43,0.6)", rot: 14, dur: "5s", delay: "3.4s",
  },
];

const PATH_W = 300;

export function LoginBackdrop() {
  return (
    <div aria-hidden style={{ position: "absolute", inset: 0, overflow: "hidden", zIndex: 0 }}>
      <style>{`
        @keyframes lg-tx { 0% { transform: translateX(0); opacity: 0; } 10% { opacity: 1; } 88% { opacity: 1; } 100% { transform: translateX(var(--d, 200px)); opacity: 0; } }
        @keyframes lg-pulse { 0%, 100% { opacity: 0.45; } 50% { opacity: 1; } }
        .lg-graph { display: none; }
        @media (min-width: 1100px) { .lg-graph { display: block; } }
      `}</style>

      {/* faint grid */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          backgroundImage:
            "linear-gradient(rgba(10,10,10,0.028) 1px, transparent 1px), linear-gradient(90deg, rgba(10,10,10,0.028) 1px, transparent 1px)",
          backgroundSize: "72px 72px",
        }}
      />
      {/* soft blue glow, top-center */}
      <div
        style={{
          position: "absolute",
          top: "-360px",
          left: "50%",
          width: "1300px",
          maxWidth: "120vw",
          height: "760px",
          transform: "translateX(-50%)",
          background:
            "radial-gradient(ellipse at center, rgba(4,133,247,0.13) 0%, transparent 65%)",
        }}
      />

      <div className="lg-graph">
        {NODES.map((n, i) => {
          const edge = n.side === "left" ? { left: "3.5%" } : { right: "3.5%" };
          const pathEdge =
            n.side === "left" ? { left: "calc(3.5% + 8px)" } : { right: "calc(3.5% + 8px)" };
          const gradient =
            n.side === "left"
              ? `linear-gradient(90deg, transparent, ${n.dot === "#0485F7" ? "rgba(4,133,247,0.22)" : n.border})`
              : `linear-gradient(90deg, ${n.dot === "#0485F7" ? "rgba(4,133,247,0.22)" : n.border}, transparent)`;
          const travel = n.side === "left" ? PATH_W - 28 : -(PATH_W - 28);
          return (
            <div key={i}>
              {/* path */}
              <div
                style={{
                  position: "absolute",
                  ...pathEdge,
                  top: n.top,
                  width: `${PATH_W}px`,
                  height: "2px",
                  background: gradient,
                  transform: `rotate(${n.side === "left" ? n.rot : -n.rot}deg)`,
                  transformOrigin: n.side === "left" ? "left center" : "right center",
                }}
              >
                <div
                  style={
                    {
                      position: "absolute",
                      left: n.side === "left" ? 0 : "auto",
                      right: n.side === "right" ? 0 : "auto",
                      top: "-2px",
                      width: 6,
                      height: 6,
                      borderRadius: 999,
                      background: n.dot,
                      boxShadow: `0 0 10px ${n.glow}`,
                      animation: `lg-tx ${n.dur} linear infinite ${n.delay}`,
                      "--d": `${travel}px`,
                    } as CSSProperties
                  }
                />
              </div>
              {/* node + label */}
              <div
                style={{
                  position: "absolute",
                  ...edge,
                  top: n.top,
                  transform: `translate(${n.side === "left" ? "-50%" : "50%"}, -50%)`,
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <span
                  style={{
                    width: 10,
                    height: 10,
                    borderRadius: 999,
                    background: n.dot,
                    boxShadow: `0 0 12px ${n.glow}`,
                    animation: `lg-pulse ${n.dur} ease-in-out infinite ${n.delay}`,
                  }}
                />
                <span
                  style={{
                    fontFamily: "var(--f-mono)",
                    fontSize: 11,
                    fontWeight: 600,
                    letterSpacing: "0.08em",
                    color: n.fg,
                    background: n.bg,
                    border: `1px solid ${n.border}`,
                    borderRadius: 999,
                    padding: "5px 14px",
                    whiteSpace: "nowrap",
                  }}
                >
                  {n.label}
                </span>
                <span
                  style={{
                    fontFamily: "var(--f-mono)",
                    fontSize: 10,
                    color: "var(--text-4)",
                  }}
                >
                  {n.meta}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
