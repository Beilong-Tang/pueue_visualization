"""Command line entry point: `python3 -m pueue_viz [--check] ...`."""

import argparse
import sys
import time
from collections import Counter

from .pueue import PueueClient, PueueError


def check(client: PueueClient) -> int:
    """Run the pueue command the app uses and report whether the data looks right."""
    print(f"Checking pueue access: {' '.join(client.status_args())}\n")
    t0 = time.perf_counter()
    try:
        snap = client.snapshot().value
    except PueueError as exc:
        print(f"  FAIL  {exc}")
        print("\n  Is the daemon running? Start it with `pueued -d`.")
        return 1
    print(f"  ok    pueue status --json ({(time.perf_counter() - t0) * 1000:.0f} ms)")
    tasks = snap.tasks
    print(f"\n  tasks: {len(tasks)}")
    print("  by category:", dict(Counter(t.category for t in tasks)))
    print("  by state:   ", dict(Counter(t.state_label for t in tasks).most_common()))
    print("  groups:     ", {name: f"{g['status']}, {g['parallel'] or 'unlimited'} parallel"
                             for name, g in snap.groups.items()})
    if tasks:
        print("\n  newest 5 tasks:")
        for t in tasks[:5]:
            print(f"    {t.task_id:>5}  {t.state_label:<18} {t.group:<10} {t.command[:50]}")
    print("\nAll checks passed.")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pueue_viz", description="Web dashboard for your pueue tasks")
    p.add_argument("--check", action="store_true", help="test pueue access and exit")
    p.add_argument("--config", default="", help="pueue config file (passed to `pueue -c`)")
    p.add_argument("--pueue", default="pueue", help="pueue executable (default: pueue on PATH)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--refresh", type=int, default=10, help="auto-refresh seconds (0 = off)")
    p.add_argument("--page-size", type=int, default=20)
    p.add_argument("--ttl", type=float, default=5.0, help="seconds to cache pueue output")
    p.add_argument("--ssh", default="", help="user@host to show in the ssh tunnel hint "
                                             "(default: you@<this machine's FQDN>)")
    args = p.parse_args(argv)

    client = PueueClient(config=args.config, ttl=args.ttl, pueue=args.pueue)
    if args.check:
        return check(client)
    from .server import serve
    return serve(client, args)


if __name__ == "__main__":
    sys.exit(main())
