"""Smoke-test every service Causal Crew depends on.

Run after filling in .env:

    .venv/bin/python scripts/check_env.py

Each check prints PASS, FAIL, or SKIP (key not set yet). Never prints secrets.
Exits non-zero if anything FAILs.
"""

import asyncio
import os
import shutil
import sys

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

results = []


def report(name, status, detail):
    results.append(status)
    print(f"  {status:4s}  {name:10s} {detail}")


def missing(*names):
    return [n for n in names if not os.environ.get(n)]


async def check_rocketride():
    gap = missing("ROCKETRIDE_URI", "ROCKETRIDE_APIKEY")
    if gap:
        return report("RocketRide", "SKIP", f"set {', '.join(gap)} in .env")
    from rocketride import RocketRideClient
    try:
        async with RocketRideClient() as client:
            if client.is_authenticated():
                report("RocketRide", "PASS", f"authenticated at {os.environ['ROCKETRIDE_URI']}")
            else:
                report("RocketRide", "FAIL", "connected but not authenticated — check ROCKETRIDE_APIKEY")
    except Exception as e:
        report("RocketRide", "FAIL", f"{type(e).__name__}: {e}")


def check_hotdata():
    gap = missing("HOTDATA_API_KEY", "HOTDATA_WORKSPACE")
    if gap:
        return report("Hotdata", "SKIP", f"set {', '.join(gap)} in .env")
    import hotdata
    try:
        config = hotdata.Configuration(api_key=os.environ["HOTDATA_API_KEY"],
                                       workspace_id=os.environ["HOTDATA_WORKSPACE"])
        with hotdata.ApiClient(config) as api:
            resp = hotdata.DatabasesApi(api).list_databases(limit=5)
        body = resp.to_dict() if hasattr(resp, "to_dict") else {}
        listed = next((len(v) for v in body.values() if isinstance(v, list)), "?")
        report("Hotdata", "PASS", f"API reachable, {listed} database(s) listed")
    except Exception as e:
        report("Hotdata", "FAIL", f"{type(e).__name__}: {str(e).splitlines()[0][:160]}")


def check_cognee():
    gap = missing("LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY", "EMBEDDING_PROVIDER")
    if gap:
        return report("Cognee", "SKIP", f"set {', '.join(gap)} in .env")
    if os.environ["EMBEDDING_PROVIDER"] != "fastembed":
        return report("Cognee", "FAIL", "EMBEDDING_PROVIDER should be fastembed (else it defaults to OpenAI)")
    try:
        import cognee  # noqa: F401
        import fastembed  # noqa: F401
        report("Cognee", "PASS", f"configured: {os.environ['LLM_PROVIDER']} LLM + fastembed "
                                  "(config only; no LLM call made)")
    except Exception as e:
        report("Cognee", "FAIL", f"{type(e).__name__}: {e}")


def check_cli(name, binary, hint):
    path = shutil.which(binary)
    report(name, "PASS" if path else "FAIL", path or hint)


def main():
    print("Causal Crew environment check\n")
    asyncio.run(check_rocketride())
    check_hotdata()
    check_cognee()
    check_cli("hotdata CLI", "hotdata", "brew install hotdata-dev/tap/cli")
    check_cli("snyk CLI", "snyk", "brew install snyk-cli")
    print()
    if "FAIL" in results:
        print("Some checks FAILED.")
        sys.exit(1)
    skipped = results.count("SKIP")
    print(f"No failures. {skipped} check(s) skipped until keys are in .env." if skipped
          else "All services ready.")


if __name__ == "__main__":
    main()
