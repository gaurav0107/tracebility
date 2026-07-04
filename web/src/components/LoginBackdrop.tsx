/**
 * Sign-in backdrop (design 4A — "trace-graph backdrop").
 *
 * A faint grid, a soft blue glow at top-center, and a converging trace graph:
 * three source nodes on the left (sdk / otel / shim) whose signals flow inward
 * toward the card, and three outcome nodes on the right (evals / replays /
 * alerts) whose signals exit outward. The whole thing reads left→right: data
 * enters from the left, passes through langprobe (the card), and exits right.
 *
 * Every traveling dot is a CSS animation. Path length + travel are expressed in
 * viewport units (`50vw - 300px`) so the lines reach toward the centered card
 * at any resolution instead of floating as short stubs on wide displays. The
 * graph hides under 1100px so it never crowds the card; the global
 * prefers-reduced-motion rule in globals.css zeroes every animation.
 */

import type { CSSProperties } from "react";

type Node = {
  side: "left" | "right";
  nodeTop: string; // node-dot vertical anchor
  pathTop: string; // path vertical anchor (~16px below the dot, per design)
  rot: number; // path tilt, exact design value
  dur: string;
  delay: string;
  label: string;
  meta: string;
  dot: string; // node pulse-dot colour
  fg: string;
  bg: string;
  border: string;
  glow: string;
};

// Path length + dot travel scale with the viewport so the fan reaches the card
// at any width (floored so it never collapses on the narrowest supported size).
const PATH_W = "max(200px, calc(50vw - 300px))";
const TRAVEL = "max(180px, calc(50vw - 322px))";
const BLUE_LINE = "rgba(4,133,247,0.22)";

const NODES: Node[] = [
  {
    side: "left", nodeTop: "20%", pathTop: "calc(20% + 16px)", rot: 14, dur: "4.4s", delay: "0s",
    label: "sdk · python", meta: "3.2k runs/min",
    dot: "#0485F7", fg: "#0A66C2", bg: "rgba(4,133,247,0.06)", border: "rgba(4,133,247,0.25)", glow: "rgba(4,133,247,0.8)",
  },
  {
    side: "left", nodeTop: "47%", pathTop: "calc(47% + 16px)", rot: 0, dur: "5.2s", delay: "1.3s",
    label: "otel · collector", meta: "1.8k spans/s",
    dot: "#0485F7", fg: "#0A66C2", bg: "rgba(4,133,247,0.06)", border: "rgba(4,133,247,0.25)", glow: "rgba(4,133,247,0.8)",
  },
  {
    side: "left", nodeTop: "72%", pathTop: "calc(72% + 16px)", rot: -14, dur: "4.8s", delay: "2.6s",
    label: "shim · langsmith", meta: "640 runs/min",
    dot: "#0485F7", fg: "#0A66C2", bg: "rgba(4,133,247,0.06)", border: "rgba(4,133,247,0.25)", glow: "rgba(4,133,247,0.8)",
  },
  {
    side: "right", nodeTop: "20%", pathTop: "calc(20% + 16px)", rot: -14, dur: "4.6s", delay: "0.8s",
    label: "evals · passing", meta: "94.2%",
    dot: "#157A45", fg: "#157A45", bg: "#E7F4ED", border: "rgba(21,122,69,0.28)", glow: "rgba(21,122,69,0.7)",
  },
  {
    side: "right", nodeTop: "47%", pathTop: "calc(47% + 16px)", rot: 0, dur: "5.6s", delay: "2s",
    label: "replays · exact", meta: "12/min",
    dot: "#0485F7", fg: "#0A66C2", bg: "rgba(4,133,247,0.06)", border: "rgba(4,133,247,0.25)", glow: "rgba(4,133,247,0.8)",
  },
  {
    side: "right", nodeTop: "72%", pathTop: "calc(72% + 16px)", rot: 14, dur: "5s", delay: "3.4s",
    label: "alerts · 2 firing", meta: "err > 2%",
    dot: "#C0382B", fg: "#C0382B", bg: "#FBEAE7", border: "rgba(192,56,43,0.25)", glow: "rgba(192,56,43,0.6)",
  },
];

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
          top: "-300px",
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
          const anchor = n.side === "left" ? "left" : "right";
          return (
            <div key={i}>
              {/* path — anchored at the node edge, extending inward toward the card */}
              <div
                style={{
                  position: "absolute",
                  [anchor]: "calc(2.6vw + 2px)",
                  top: n.pathTop,
                  width: PATH_W,
                  height: "2px",
                  background:
                    n.side === "left"
                      ? `linear-gradient(90deg, transparent, ${BLUE_LINE})`
                      : `linear-gradient(90deg, ${BLUE_LINE}, transparent)`,
                  transform: `rotate(${n.rot}deg)`,
                  transformOrigin: n.side === "left" ? "left center" : "right center",
                }}
              >
                {/* traveling dot — always starts at left:0 and moves right; on the
                    left that is inward (converging), on the right it is outward
                    (exiting, since the right path is anchored to the right edge). */}
                <div
                  style={
                    {
                      position: "absolute",
                      left: 0,
                      top: "-2px",
                      width: 6,
                      height: 6,
                      borderRadius: 999,
                      background: "#0485F7",
                      boxShadow: "0 0 10px rgba(4,133,247,0.8)",
                      animation: `lg-tx ${n.dur} linear infinite ${n.delay}`,
                      "--d": TRAVEL,
                    } as CSSProperties
                  }
                />
              </div>

              {/* node + label */}
              <div
                style={{
                  position: "absolute",
                  [anchor]: "2.6vw",
                  top: n.nodeTop,
                  transform: `translateX(${n.side === "left" ? "-50%" : "50%"})`,
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
                <span style={{ fontFamily: "var(--f-mono)", fontSize: 10, color: "var(--text-4)" }}>
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
