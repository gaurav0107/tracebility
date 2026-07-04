import { NextResponse, type NextRequest } from "next/server";

/**
 * Auth gate middleware.
 *
 * If the session cookie is absent, redirect the request to /login and
 * preserve the original path as ?return_to=... so the AuthClient can
 * send the user back after sign-in.
 *
 * The matcher excludes /login, /signup, /api, _next assets, public
 * marketing routes, and the OAuth callback path. Everything else is
 * gated behind a valid session cookie.
 */

const SESSION_COOKIE = "langprobe_session";

const PUBLIC_PATHS = new Set<string>([
  "/login",
  "/signup",
  "/privacy",
  "/terms",
]);

export function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;

  if (PUBLIC_PATHS.has(pathname)) return NextResponse.next();

  const hasSession = req.cookies.has(SESSION_COOKIE);
  if (hasSession) return NextResponse.next();

  const url = req.nextUrl.clone();
  url.pathname = "/login";
  url.search = "";
  const returnTo = pathname + (search || "");
  if (returnTo && returnTo !== "/") {
    url.searchParams.set("return_to", returnTo);
  }
  return NextResponse.redirect(url);
}

export const config = {
  // Match every path except: api routes, next assets, static files,
  // and the OAuth callback (which lives under /api/auth/...).
  matcher: ["/((?!api|_next/static|_next/image|favicon\\.ico|.*\\.(?:png|jpg|jpeg|gif|svg|webp|ico|woff2?|ttf|otf|map)$).*)"],
};
