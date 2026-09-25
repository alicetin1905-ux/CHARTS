#!/usr/bin/env python3
"""Fetch US spot Bitcoin ETF flows from SoSoValue and print them as compact JSON.

The output shape is what liquidations.html reads (the `etf/btc` document on the hosted page):
  {asOf, updatedAt, tot: {a, f, c, v, h}, history: [{d, f, c, a, v}], funds: [{t, i, f, a, c, p}]}
"""
import json, sys, time, urllib.request

API = "https://api.sosovalue.xyz/openapi/v2/etf/"


def post(path):
    req = urllib.request.Request(API + path, data=json.dumps({"type": "us-btc-spot"}).encode(),
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


def main():
    hist = post("historicalInflowChart")
    cur = post("currentEtfDataMetrics")
    history = sorted(({"d": r["date"], "f": num(r.get("totalNetInflow")), "c": num(r.get("cumNetInflow")),
                       "a": num(r.get("totalNetAssets")), "v": num(r.get("totalValueTraded"))} for r in hist),
                     key=lambda r: r["d"])
    funds = [{"t": e["ticker"], "i": (e.get("institute") or "").strip(), "f": num(e.get("dailyNetInflow")),
              "a": num(e.get("netAssets")), "c": num(e.get("cumNetInflow")),
              "p": num(e.get("discountPremiumRate"))} for e in cur.get("list", [])]
    out = {
        "asOf": cur["dailyNetInflow"]["lastUpdateDate"],
        "updatedAt": int(time.time() * 1000),
        "tot": {"a": num(cur["totalNetAssets"]), "f": num(cur["dailyNetInflow"]), "c": num(cur["cumNetInflow"]),
                "v": num(cur["dailyTotalValueTraded"]), "h": num(cur["totalTokenHoldings"])},
        "history": history,
        "funds": funds,
    }
    json.dump(out, sys.stdout, separators=(",", ":"))


if __name__ == "__main__":
    main()
