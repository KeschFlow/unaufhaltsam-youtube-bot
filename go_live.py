"""Explicit, gated live switch. Running without flags is read-only."""

from __future__ import annotations

import argparse
import json

from unaufhaltsam_youtube.preflight import run_preflight
from unaufhaltsam_youtube.runtime import read_runtime, set_publish_mode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--enable-public", action="store_true")
    group.add_argument("--disable-public", action="store_true")
    args = parser.parse_args(argv)

    if args.disable_public:
        set_publish_mode("dry_run")
        print(json.dumps({"PUBLISH_MODE": "dry_run", "ROLLBACK": "complete"}, indent=2))
        return 0

    report = run_preflight()
    if args.enable_public:
        if not report["READY_FOR_PUBLIC"]:
            print(json.dumps(report, indent=2))
            print("PUBLIC_SWITCH_BLOCKED=true")
            return 2
        set_publish_mode("public")
        print(json.dumps({"PUBLISH_MODE": "public", "READY_FOR_PUBLIC": True}, indent=2))
        return 0

    print(json.dumps(report, indent=2))
    print(f"READY_FOR_PUBLIC={'true' if report['READY_FOR_PUBLIC'] else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
