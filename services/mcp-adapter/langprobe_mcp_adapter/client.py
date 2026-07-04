"""VerbHTTPClient: thin httpx translation layer over the `/v1/verbs/*`
routes (Task 8, D2-A).

One async method per agent-drivable verb. Each method:
  1. POSTs `params` as the JSON body to the matching route.
  2. Sends the caller's session cookie (`langprobe_session`) — the api
     verb routes are session-gated (Task 7), so an MCP-driven agent
     call carries a scoped session the same way a browser would.
  3. Returns the parsed JSON response on 2xx.
  4. Raises :class:`VerbCallError` (carrying status + body) on any
     non-2xx response.

Zero business logic lives here — no retries, no caching, no scope
checks (those all live in the verb service layer / router on the api
side). `promote_to_recurring` has NO method here on purpose: promoting
mutates production judge config and must stay reachable only via a
human session on the api's session-gated `/v1/verbs/promote` route,
never via this agent-facing adapter (see `tools.py`'s module
docstring for the full rationale).

No `mcp` import in this module.
"""

from __future__ import annotations

import httpx


class VerbCallError(Exception):
    """Raised when a verb HTTP call returns a non-2xx response.

    Carries the HTTP status code and raw response body so a caller
    (the MCP server layer, or a test) can decide how to translate it
    further without re-parsing an exception message.
    """

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"verb call failed: status={status_code} body={body!r}")


class VerbHTTPClient:
    """Translates agent tool calls into authenticated HTTP calls
    against the api's `/v1/verbs/*` routes.

    Constructed per-caller with the target api's `base_url` and the
    caller's `session_cookie` (a `langprobe_session` cookie value
    already scoped to a single project via the api's session/auth
    layer) — this client does no scoping itself.
    """

    def __init__(self, base_url: str, session_cookie: str):
        self.base_url = base_url.rstrip("/")
        self.session_cookie = session_cookie

    async def _post(self, path: str, params: dict) -> dict:
        url = f"{self.base_url}{path}"
        cookies = {"langprobe_session": self.session_cookie}
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=params, cookies=cookies)
        if response.status_code >= 300:
            raise VerbCallError(status_code=response.status_code, body=response.text)
        return response.json()

    async def cluster_failures(self, params: dict) -> dict:
        return await self._post("/v1/verbs/cluster-failures", params)

    async def propose_eval(self, params: dict) -> dict:
        return await self._post("/v1/verbs/propose-eval", params)

    async def run_judge_over_cohort(self, params: dict) -> dict:
        return await self._post("/v1/verbs/backtest", params)

    async def watch_judge(self, params: dict) -> dict:
        return await self._post("/v1/verbs/watch", params)
