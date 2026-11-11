import json, datetime as dt, yfinance as yf
from zoneinfo import ZoneInfo
out = {}
for base in ["SAP", "SIE", "ALV", "IFX"]:
    for suf in [".DE", ".F", ".SG", ".MU", ".DU", ".BE", ".HM", ".TG"]:
        s = base + suf
        try:
            t = yf.Ticker(s); h = t.history(period="10d"); md = t.history_metadata or {}
            ts = md.get("regularMarketTime")
            out[s] = {"last_bar": str(h.index[-1].date()) if len(h) else None,
                      "rmt": dt.datetime.fromtimestamp(ts, ZoneInfo("Europe/Berlin")).isoformat() if ts else None,
                      "price": md.get("regularMarketPrice"), "vol_last": int(h["Volume"].iloc[-1]) if len(h) else None}
        except Exception as e:
            out[s] = {"err": str(e)[:120]}
open("data/probe.json", "w").write(json.dumps(out, indent=1))
