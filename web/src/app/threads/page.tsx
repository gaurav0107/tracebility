import { redirect } from "next/navigation";

/**
 * Threads merged into the unified Tracing surface (/runs?view=threads).
 * This route survives only so old links and bookmarks keep working;
 * /threads/[session_id] remains the per-session drill-down.
 */
// Force per-request rendering: a statically prerendered redirect() page
// ships a 307 with no Location header (JS-only redirect); dynamic emits
// a real Location so curl/agents and no-JS clients follow it too.
export const dynamic = "force-dynamic";

export default function ThreadsRedirect() {
  redirect("/runs?view=threads");
}
