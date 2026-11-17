"""Build a static copy of the dashboard for GitHub Pages.

The live app is a Python process; Pages can only serve files. So this module
runs the real engine once, writes the resulting state to JSON, and publishes the
same front-end in read-only mode.

The snapshot is exactly as stale as the schedule that produced it.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .app import degradation, trend_context
from .authority import decide
from .config import Config, empty_config
from .domain import iso, utc_now
from .market import build_snapshot
from .telemetry import load_telemetry

WEB_ROOT = Path(__file__).resolve().parent / "web"
ASSETS = ("app.js", "style.css", "favicon.svg")

STATIC_BANNER = """
    <div class="static-note" id="static-note">
      <strong>Static snapshot.</strong> This page is rebuilt on a schedule and cannot refresh itself.
      Live behaviour, configuration and audit history are only in the self-hosted instance.
    </div>
"""


def build_state(config: Config, *, telemetry_path: str | Path | None = None) -> dict[str, Any]:
    market = build_snapshot(config=config)
    decision = decide(config=config, market=market)
    report = load_telemetry(telemetry_path) if telemetry_path else None
    return {
        "now": iso(utc_now()),
        "static": True,
        "generated_at": iso(datetime.now(timezone.utc)),
        "cached_market": False,
        "config": config.to_dict(),
        "config_hash": config.config_hash,
        "config_missing": list(config.missing()),
        "market": market.to_dict(),
        "decision": decision.to_dict(),
        "trend": trend_context(market),
        "telemetry": None if report is None else report.to_dict(),
        "research": None,
        "integrations": degradation(market),
        "config_history": [],
    }


def _static_index(source: str) -> str:
    html = source.replace('href="/style.css"', 'href="style.css"')
    html = html.replace('href="/favicon.svg"', 'href="favicon.svg"')
    html = html.replace(
        '<script src="/app.js"></script>',
        '<script>window.CORTEX_STATIC = true;</script>\n<script src="app.js"></script>',
    )
    html = html.replace('<main id="view" class="view">', STATIC_BANNER + '\n  <main id="view" class="view">')
    return html


def build_site(
    out_dir: str | Path,
    *,
    config_path: str | Path | None = None,
    telemetry_path: str | Path | None = None,
) -> dict[str, Any]:
    output = Path(out_dir)
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    config = empty_config()
    if config_path and Path(config_path).exists():
        config = Config.from_dict(json.loads(Path(config_path).read_text(encoding="utf-8-sig")))

    state = build_state(config, telemetry_path=telemetry_path)
    (output / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    (output / "index.html").write_text(
        _static_index((WEB_ROOT / "index.html").read_text(encoding="utf-8")), encoding="utf-8"
    )
    for asset in ASSETS:
        shutil.copy2(WEB_ROOT / asset, output / asset)
    (output / ".nojekyll").write_text("", encoding="utf-8")
    return state


def main(argv: list[str] | None = None) -> int:
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Build a static CORTEX-BTC snapshot")
    parser.add_argument("--out", default="_site")
    parser.add_argument("--config", default="config/research-seed.json")
    parser.add_argument("--telemetry", default=None)
    args = parser.parse_args(argv)

    state = build_site(args.out, config_path=args.config, telemetry_path=args.telemetry)
    print(
        json.dumps(
            {
                "out": args.out,
                "verdict": state["decision"]["verdict"],
                "price": state["market"]["price"],
                "generated_at": state["generated_at"],
                "venue": os.environ.get("CORTEX_SPOT_BASE", "https://api.binance.com"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
