"""OSINT Map for Hermes Agent.

A live globe of public situational-awareness feeds for Hermes Desktop, plus agent tools over the
same data. Layers live in osintmap/layers.py (parsers in parsers.py, GDELT in gdelt.py), panels in
panels.py, recon lookups in recon.py, shared HTTP/caching/SSRF guard in http.py, and the tool
schemas and handlers in tools.py. The Desktop page reads the routes in dashboard/plugin_api.py.
"""

from __future__ import annotations

from typing import Any

__version__ = "0.1.0"


def register(ctx: Any) -> None:
    # Imported here, not at module top, so loading the package (pytest does, from the repo root)
    # never needs the plugin's relative imports to resolve.
    from .osintmap.tools import EMOJI, HANDLERS, SCHEMAS, TOOLSET

    for name, handler in HANDLERS.items():
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema=SCHEMAS[name],
            handler=handler,
            emoji=EMOJI.get(name, "🌍"),
        )
