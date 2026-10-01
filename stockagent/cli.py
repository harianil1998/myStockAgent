"""Command line: `stockagent run | once | digest | sources | score`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .config import load_settings
from .orchestrator import Orchestrator


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="stockagent", description="Multi-source stock research agent")
    p.add_argument("-c", "--config", help="Path to config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="Realtime daemon: sub-agents poll continuously, alerts + daily digest")
    sub.add_parser("once", help="One sweep of every source, then a full report (good for cron)")
    sub.add_parser("digest", help="Report from already-collected signals, no new collection")
    sub.add_parser("score", help="Print the raw signal leaderboard (no LLM)")
    sub.add_parser("sources", help="Run each sub-agent once and show its status")
    args = p.parse_args(argv)

    # Windows consoles default to a legacy code page that can't print the report's emoji.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    orch = Orchestrator(load_settings(args.config))
    try:
        asyncio.run(_dispatch(orch, args.cmd))
    except KeyboardInterrupt:
        pass


async def _dispatch(orch: Orchestrator, cmd: str) -> None:
    if cmd == "run":
        await orch.run_forever()
    elif cmd == "once":
        await orch.once()
    elif cmd == "digest":
        await orch.setup()
        await orch.analyze("daily")
    elif cmd == "score":
        await orch.setup()
        print(f"{'TICKER':8} {'SCORE':>7} {'SENT':>6} {'MENT':>5} {'VEL':>6} {'SMART$':>7}  SOURCES")
        for c in orch.score()[:40]:
            print(f"{c.ticker:8} {c.score:+7.1f} {c.sentiment:+6.2f} {c.mentions:5d} {c.velocity:5.1f}x "
                  f"{c.smart_money:+7.1f}  {', '.join(sorted(c.sources))}")
    elif cmd == "sources":
        await orch.setup()
        await orch.sweep()
        for name, status in sorted(orch.status.items()):
            print(f"{name:15} {status}")


if __name__ == "__main__":
    main()
