"""Launch Amap MCP Server with stdout-safe stdio transport."""

import builtins
from importlib import import_module
import sys


def _stderr_print(*_args, **_kwargs):
    """Suppress upstream payloads without polluting JSON-RPC stdout."""
    builtins.print(
        "amap-mcp-server stdout debug output suppressed",
        file=sys.stderr,
        flush=True,
    )


def main() -> None:
    """Patch the upstream module, then start its MCP server."""
    package = import_module("amap_mcp_server")
    server = import_module("amap_mcp_server.server")

    server.print = _stderr_print
    package.main()


if __name__ == "__main__":
    main()
