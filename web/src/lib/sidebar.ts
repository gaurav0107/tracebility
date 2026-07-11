/**
 * Sidebar collapse cookie contract, shared by the server read
 * (Shell.tsx via next/headers cookies()) and the client write
 * (SidebarShell.tsx via document.cookie).
 *
 * MUST stay a plain module — no "use client". A server component that
 * imports runtime values from a client module receives client-reference
 * proxies instead of the actual strings (Next 14 flight loader), which
 * silently breaks the cookie lookup.
 */
export const SIDEBAR_COOKIE = "lp_sidebar";
export const SIDEBAR_COLLAPSED_VALUE = "rail";
export const SIDEBAR_EXPANDED_VALUE = "open";

export interface MeResponse {
  user_id: string;
  email: string;
  is_root: boolean;
}
