#!/usr/bin/env python3
"""doctor extension: MCP servers — which catalog servers are registered with the host,
which are available to install, and which are blocked (missing runner / node / app).

probe() never raises and never writes: it only reads data/mcp_servers.json, the host config
(<HOME>/.claude.json) and <user_home>/installed_mcp.json, and looks up tools on PATH.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_INSTALLER = Path(__file__).resolve().parents[1] / "mcp" / "install_mcp.py"


def _load_installer():
    # Loaded by file path, never `import mcp`, so the MCP SDK distribution is never shadowed.
    spec = importlib.util.spec_from_file_location("coordn8_install_mcp", _INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def probe(data_path=None) -> dict:
    try:
        m = _load_installer()
        data = m.load_catalog(data_path)
        st = m.status(data)
        rec = m.load_record()
        registered, available, blocked = [], [], []
        lines = []
        for s, row in zip(data["servers"], st["servers"]):
            if row["registered"]:
                registered.append(s["id"])
                lines.append(f"{s['id']}: {row['state']}")
                continue
            plan = m.plan_one(s, {}, rec, {s["id"]})  # read-only decision, nothing is run
            if plan["action"] in ("install", "update"):
                available.append(s["id"])
                lines.append(f"{s['id']}: available — `coordn8 mcp install --only {s['id']}`")
            else:
                blocked.append({"id": s["id"], "reason": plan["reason"]})
                lines.append(f"{s['id']}: not installable here — {plan['reason']}")
        offers = [o["id"] for o in data.get("signin_offers", [])]
        if offers:
            lines.append(f"sign-in offers (never auto-installed): {', '.join(offers)} — `coordn8 mcp offer`")
        if st.get("host_error"):
            lines.append(f"host config problem: {st['host_error']}")
        summary = (f"{len(registered)} registered, {len(available)} available, {len(blocked)} blocked "
                   f"of {len(data['servers'])} open-source MCP servers; host config {st['host_config']}")
        return {"summary": summary, "lines": lines, "registered": registered, "available": available,
                "blocked": blocked, "signin_offers": offers, "claude_cli": st.get("claude_cli"),
                "host_config": st["host_config"]}
    except Exception as e:  # doctor must never break because of an extension
        return {"summary": f"MCP check could not run: {type(e).__name__}: {e}", "lines": [],
                "registered": [], "available": [], "blocked": [], "signin_offers": []}


if __name__ == "__main__":
    import json
    print(json.dumps(probe(), indent=1))
