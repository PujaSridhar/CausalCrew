"""Entry point packaged into each Rote Play's resources/.

    python3 crew.py health  --orders <file|demo|demo-broken> [--days N] [--workspaces DIR]
    python3 crew.py replay  --recipe <file> --orders <file|demo|demo-broken> ...

orders=demo generates the seeded demo data (637k orders, a planted West shipping-fee
drop); demo-broken is the same data with one day loaded twice. Generated files go
under --workspaces and are reused on later steps. See causal_crew/plays.py.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DEMO = {"demo": "orders.parquet", "demo-broken": "orders_broken.parquet"}


def resolve_demo(argv):
    """Swap --orders demo|demo-broken for a generated file, generating it once."""
    if "--orders" not in argv:
        return argv
    i = argv.index("--orders") + 1
    if i >= len(argv) or argv[i] not in DEMO:
        return argv
    ws = argv[argv.index("--workspaces") + 1] if "--workspaces" in argv else "workspaces"
    out = os.path.join(ws, "demo-data")
    target = os.path.join(out, DEMO[argv[i]])
    if not os.path.exists(target):
        import generate_orders
        sys.stdout = sys.stderr  # keep stdout for the one JSON document
        generate_orders.main(out)
        sys.stdout = sys.__stdout__
    return argv[:i] + [target] + argv[i + 1:]


if __name__ == "__main__":
    from causal_crew import plays
    sys.exit(plays.main(resolve_demo(sys.argv[1:])))
