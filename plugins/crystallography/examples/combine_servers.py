"""Chain two MCP servers without an LLM in the loop: crystallography → phase-field.

    python examples/combine_servers.py BaTiO3
    python examples/combine_servers.py mp-5229        # needs MP_API_KEY

Swap the second StdioServerParameters for your collaborator's server (or a URL string for a
remote Streamable HTTP server, e.g. "https://their-host.example/mcp").
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

HERE = Path(__file__).resolve().parent

CRYSTAL = StdioServerParameters(command=sys.executable, args=["-m", "consilith_crystallography.server"])
PHASEFIELD = StdioServerParameters(
    command=sys.executable, args=[str(HERE / "mock_phasefield_server.py")]
)


async def main(structure: str) -> None:
    async with Client(CRYSTAL) as crystal, Client(PHASEFIELD) as phasefield:
        r = await crystal.call_tool("get_material_descriptor", {"structure": structure})
        if r.is_error:
            raise SystemExit(r.content[0].text)
        descriptor = r.structured_content
        print(
            "descriptor:",
            json.dumps({k: descriptor[k] for k in ("schema", "formula", "molar_volume")}),
        )
        r = await phasefield.call_tool("run_phase_field", {"descriptor": descriptor})
        if r.is_error:
            raise SystemExit(r.content[0].text)
        print("phase-field result:", r.content[0].text)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "BaTiO3"))
