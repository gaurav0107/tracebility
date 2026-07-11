import { redirect } from "next/navigation";

/**
 * Alerts merged into /monitoring?tab=alerts (rules + incident history
 * live beside the dashboards they threshold). This route survives only
 * so old links and bookmarks keep working.
 */
// Force per-request rendering: a statically prerendered redirect() page
// ships a 307 with no Location header (JS-only redirect); dynamic emits
// a real Location so curl/agents and no-JS clients follow it too.
export const dynamic = "force-dynamic";

export default function AlertsRedirect() {
  redirect("/monitoring?tab=alerts");
}
