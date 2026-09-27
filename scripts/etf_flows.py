#!/usr/bin/env python3
"""Fetch US spot crypto ETF flows from SoSoValue and print them as compact JSON.

Usage: etf_flows.py [TYPE]   TYPE defaults to us-btc-spot; also us-eth-spot, us-sol-spot, ...

The output shape is what liquidations.html and etfs.html read (the `etf/<coin>` documents on the hosted pages):
  {asOf, updatedAt, tot: {a, f, c, v, h, p}, history: [{d, f, c, a, v}], funds: [{t, i, f, a, c, p, fee, v}]}
Prints nothing and exits 1 when the source has no data for TYPE.
"""
import json, sys, time, urllib.request

TYPE = sys.argv[1] if len(sys.argv) > 1 else "us-btc-spot"

API = "https://api.sosovalue.xyz/openapi/v2/etf/"


def post(path):
    req = urllib.request.Request(API + path, data=json.dumps({"type": TYPE}).encode(),
                                 headers={"content-type": "application/json", "user-agent": "etf-flows/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = json.load(r)
    if body.get("code") != 0:
        raise SystemExit(f"SoSoValue error on {path}: {body.get('msg')}")
    return body["data"]


def num(x):
    v = x.get("value") if isinstance(x, dict) else x
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def rate(x):
    """A fraction such as a fee or premium, unrounded (num's 2 decimals would zero it out)."""
    v = x.get("value") if isinstance(x, dict) else x
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    hist = post("historicalInflowChart")
    cur = post("currentEtfDataMetrics")
    if not hist or not cur.get("dailyNetInflow", {}).get("lastUpdateDate"):
        raise SystemExit(f"No ETF data for {TYPE}")
    history = sorted(({"d": r["date"], "f": num(r.get("totalNetInflow")), "c": num(r.get("cumNetInflow")),
                       "a": num(r.get("totalNetAssets")), "v": num(r.get("totalValueTraded"))} for r in hist),
                     key=lambda r: r["d"])
    funds = [{"t": e["ticker"], "i": (e.get("institute") or "").strip(), "f": num(e.get("dailyNetInflow")),
              "a": num(e.get("netAssets")), "c": num(e.get("cumNetInflow")),
              "p": rate(e.get("discountPremiumRate")),
              "fee": rate(e.get("fee")), "v": num(e.get("dailyValueTraded"))} for e in cur.get("list", [])]
    out = {
        "asOf": cur["dailyNetInflow"]["lastUpdateDate"],
        "updatedAt": int(time.time() * 1000),
        "tot": {"a": num(cur["totalNetAssets"]), "f": num(cur["dailyNetInflow"]), "c": num(cur["cumNetInflow"]),
                "v": num(cur["dailyTotalValueTraded"]), "h": num(cur["totalTokenHoldings"]),
                "p": rate(cur.get("totalNetAssetsPercentage"))},
        "history": history,
        "funds": funds,
    }
    json.dump(out, sys.stdout, separators=(",", ":"))


if __name__ == "__main__":
    main()
