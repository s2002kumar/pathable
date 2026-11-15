"""Process entry point: ``python -m pathable_api``.

This module exists because ``uvicorn.run()`` cannot be made to work with psycopg
on Windows.

Since uvicorn 0.36, the event loop is created from an explicit *loop factory*
rather than from the asyncio policy, and on Windows that factory returns
``asyncio.ProactorEventLoop`` — which psycopg 3 refuses to run on. Setting the
event-loop policy has no effect, because uvicorn never consults it.

The fix is to construct the server ourselves and hand ``asyncio.run`` an explicit
selector loop factory. Running the API through this module is therefore the
supported way to start it. A bare ``uvicorn pathable_api.main:app`` still works on
Linux and macOS, which is what the container image uses.
"""

from __future__ import annotations

import asyncio

import uvicorn

from pathable_api.core.config import get_settings
from pathable_api.core.event_loop import configure_event_loop_policy, selector_loop_factory

#: Seconds an idle keep-alive connection stays open. Longer than Caddy keeps an
#: idle upstream connection for reuse (its reverse_proxy default, two minutes),
#: so Caddy is always the side that closes one. With uvicorn's own default of
#: 5 s the API could close a connection at the moment Caddy reused it: Caddy read
#: a reset and answered a route comparison with 502, and a POST is not retried
#: (pilot, 2026-11-15, under load). The margin is generous because a saturated
#: event loop starts this timer before a large answer has left the socket, so
#: Caddy's own idle clock can start seconds later.
KEEP_ALIVE_SECONDS = 180


def build_server() -> uvicorn.Server:
    """Construct the uvicorn server from validated application settings."""
    settings = get_settings()

    config = uvicorn.Config(
        "pathable_api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
        access_log=True,
        timeout_keep_alive=KEEP_ALIVE_SECONDS,
    )
    return uvicorn.Server(config)


def main() -> None:
    """Start the API server on an event loop psycopg can actually use."""
    # Helps anything in-process that does consult the policy; harmless elsewhere.
    configure_event_loop_policy()

    server = build_server()
    loop_factory = selector_loop_factory()

    if loop_factory is None:
        # Off Windows, let uvicorn choose (it will prefer uvloop when installed).
        server.run()
        return

    asyncio.run(server.serve(), loop_factory=loop_factory)


if __name__ == "__main__":
    main()
