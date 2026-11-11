"""Baut das Börsen-Dashboard: Top-10-Gewinner und -Verlierer für DAX 40, S&P 500 und CSI 300
über Vortag, 1 Woche und 1 Monat. Ausgabe: site/index.html und site/data.json."""
import datetime as dt
import io
import json
import pathlib
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
CACHE = ROOT / "data" / "constituents.json"
UA = {"User-Agent": "boersen-dashboard/1.0 (GitHub Actions; private Morgenübersicht)"}

MARKETS = {
    "de": {"label": "Deutschland", "index": "DAX 40", "currency": "EUR",
           "tz": "Europe/Berlin", "close": (17, 45)},
    "us": {"label": "USA", "index": "S&P 500", "currency": "USD",
           "tz": "America/New_York", "close": (16, 15)},
    "cn": {"label": "China", "index": "CSI 300", "currency": "CNY",
           "tz": "Asia/Shanghai", "close": (15, 15)},
}
PERIODS = [("d1", "Vortag"), ("w1", "1 Woche"), ("m1", "1 Monat")]
TOP_N = 10


# ---------- Indexzusammensetzung (Wikipedia, mit Cache als Rückfall) ----------

def wiki_tables(url):
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    tables = pd.read_html(io.StringIO(r.text))
    for t in tables:
        t.columns = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in t.columns]
    return tables


def col(df, *keys):
    for c in df.columns:
        if any(k in c.lower() for k in keys):
            return c
    return None


def get_dax():
    for t in wiki_tables("https://en.wikipedia.org/wiki/DAX"):
        tc, nc = col(t, "ticker", "symbol"), col(t, "company", "name")
        if tc and nc and 35 <= len(t) <= 45:
            out = []
            for s, n in zip(t[tc], t[nc]):
                s = str(s).strip().upper().split(".")[0]  # z. B. "AIR.PA" -> Xetra "AIR.DE"
                s = f"{s}.DE"
                out.append({"symbol": s, "name": str(n).strip()})
            return out
    raise RuntimeError("DAX-Tabelle nicht gefunden")


def get_sp500():
    for t in wiki_tables("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"):
        tc, nc = col(t, "symbol"), col(t, "security")
        if tc and nc and len(t) > 450:
            return [{"symbol": str(s).strip().replace(".", "-"), "name": str(n).strip()}
                    for s, n in zip(t[tc], t[nc])]
    raise RuntimeError("S&P-500-Tabelle nicht gefunden")


def get_csi300():
    code_re = re.compile(r"(\d{6})")
    for t in wiki_tables("https://en.wikipedia.org/wiki/CSI_300_Index"):
        if len(t) < 250:
            continue
        code_col = None
        for c in t.columns:
            vals = t[c].astype(str)
            if vals.str.contains(r"\b\d{6}\b").mean() > 0.8:
                code_col = c
                break
        if code_col is None:
            continue
        name_col = col(t, "company", "name", "constituent") or next(
            c for c in t.columns if c != code_col)
        exch_col = col(t, "exchange")
        out = []
        for _, row in t.iterrows():
            m = code_re.search(str(row[code_col]))
            if not m:
                continue
            code = m.group(1)
            exch = f"{row[exch_col]} {row[code_col]}" if exch_col else str(row[code_col])
            if re.search(r"shanghai|sse(?!z)|\.ss|sh", exch, re.I) and not re.search(r"shenzhen|szse", exch, re.I):
                suffix = ".SS"
            elif re.search(r"shenzhen|szse|\.sz", exch, re.I):
                suffix = ".SZ"
            else:
                suffix = ".SS" if code[0] in "69" else ".SZ"
            out.append({"symbol": code + suffix, "name": str(row[name_col]).strip()})
        if len(out) >= 250:
            return out
    raise RuntimeError("CSI-300-Tabelle nicht gefunden")


def load_constituents():
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    fetchers = {"de": get_dax, "us": get_sp500, "cn": get_csi300}
    result, notes = {}, {}
    for key, fn in fetchers.items():
        try:
            result[key] = fn()
        except Exception as e:  # Wikipedia nicht erreichbar oder Layout geändert
            print(f"[{key}] Zusammensetzung: {e} – nutze Cache", file=sys.stderr)
            result[key] = cache.get(key, [])
            notes[key] = "Indexliste aus Zwischenspeicher"
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    return result, notes


# ---------- Kurse und Performance ----------

def download_closes(symbols):
    closes = {}
    for i in range(0, len(symbols), 150):
        chunk = symbols[i:i + 150]
        df = yf.download(chunk, period="3mo", interval="1d", auto_adjust=True,
                         progress=False, threads=True)
        if df.empty:
            continue
        close = df["Close"]
        if isinstance(close, pd.Series):
            close = close.to_frame(chunk[0])
        for s in close.columns:
            ser = close[s].dropna()
            if len(ser):
                idx = ser.index
                if getattr(idx, "tz", None) is not None:
                    idx = idx.tz_localize(None)
                ser.index = idx.normalize()
                closes[s] = ser[~ser.index.duplicated(keep="last")]
    return closes


def latest_quote(sym):
    """Letzter regulärer Schlusskurs samt Datum aus den Yahoo-Metadaten."""
    try:
        t = yf.Ticker(sym)
        t.history(period="5d", interval="1d")
        md = t.history_metadata or {}
        price, ts, tz = md.get("regularMarketPrice"), md.get("regularMarketTime"), md.get("exchangeTimezoneName")
        if price and ts:
            when = (pd.Timestamp(ts, unit="s", tz="UTC") if isinstance(ts, (int, float))
                    else pd.Timestamp(ts))
            when = (when.tz_localize("UTC") if when.tzinfo is None else when).tz_convert(tz or "UTC")
            return sym, float(price), when
    except Exception as e:
        print(f"[{sym}] Nachtrag fehlgeschlagen: {e}", file=sys.stderr)
    return sym, None, None


def fill_missing_session(closes, d0, today, session_open):
    """Ergänzt den letzten Handelstag, wenn Yahoo ihn noch nicht in der Tageshistorie führt."""
    probe = latest_quote(next(iter(closes)))
    if not probe[2] or pd.Timestamp(probe[2].date()) <= d0:
        return 0
    with ThreadPoolExecutor(8) as ex:
        quotes = list(ex.map(latest_quote, list(closes)))
    added = 0
    for sym, price, when in quotes:
        if not price:
            continue
        day = pd.Timestamp(when.date())
        if day <= d0 or (day == today and session_open):
            continue
        closes[sym] = pd.concat([closes[sym][closes[sym].index < day], pd.Series([price], index=[day])])
        added += 1
    return added


def compute_market(key, members):
    cfg = MARKETS[key]
    names = {m["symbol"]: m["name"] for m in members}
    closes = download_closes(list(names))
    if not closes:
        return {"error": "Keine Kursdaten erhalten"}

    # Laufende Sitzung ausschließen: heutiger Balken zählt erst nach Börsenschluss
    now = dt.datetime.now(ZoneInfo(cfg["tz"]))
    today = pd.Timestamp(now.date())
    session_open = (now.hour, now.minute) < cfg["close"]

    counts = pd.Series(pd.concat([s.index.to_series() for s in closes.values()])).value_counts()
    days = counts[counts >= 0.5 * len(closes)].index.sort_values()
    if session_open:
        days = days[days < today]
    if len(days) < 2:
        return {"error": "Zu wenige Handelstage"}
    filled = fill_missing_session(closes, days[-1], today, session_open)
    if filled >= 0.5 * len(closes):
        counts = pd.Series(pd.concat([s.index.to_series() for s in closes.values()])).value_counts()
        days = counts[counts >= 0.5 * len(closes)].index.sort_values()
        if session_open:
            days = days[days < today]
    d0 = days[-1]
    diag = {"now_local": now.isoformat(timespec="minutes"), "session_open": session_open, "filled": filled,
            "last_days": {d.date().isoformat(): int(counts[d]) for d in counts.sort_index().index[-5:]}}
    refs = {
        "d1": days[-2],
        "w1": days[days <= d0 - pd.Timedelta(days=7)].max(),
        "m1": days[days <= d0 - pd.DateOffset(months=1)].max(),
    }

    periods = {}
    for pkey, plabel in PERIODS:
        ref = refs[pkey]
        rows = []
        for sym, ser in closes.items():
            if d0 not in ser.index or pd.isna(ref):
                continue
            base = ser[ser.index <= ref]
            if base.empty:
                continue
            p0, p1 = float(ser[d0]), float(base.iloc[-1])
            if p1 <= 0:
                continue
            pct = (p0 / p1 - 1) * 100
            if abs(pct) > 500:  # offensichtliche Datenfehler
                continue
            rows.append({"symbol": sym, "name": names.get(sym, sym),
                         "pct": round(pct, 2), "price": round(p0, 2)})
        rows.sort(key=lambda r: r["pct"], reverse=True)
        periods[pkey] = {
            "label": plabel,
            "from": ref.date().isoformat() if not pd.isna(ref) else None,
            "to": d0.date().isoformat(),
            "gainers": rows[:TOP_N],
            "losers": sorted(rows, key=lambda r: r["pct"])[:TOP_N],
            "count": len(rows),
        }
    return {"asof": d0.date().isoformat(), "members": len(members),
            "priced": len(closes), "periods": periods, "diag": diag}


def main():
    constituents, notes = load_constituents()
    data = {"generated": dt.datetime.now(ZoneInfo("Europe/Berlin")).isoformat(timespec="minutes"),
            "markets": {}}
    for key, cfg in MARKETS.items():
        print(f"[{key}] {len(constituents[key])} Werte …", file=sys.stderr)
        res = compute_market(key, constituents[key]) if constituents[key] else {"error": "Indexliste fehlt"}
        res.update({k: cfg[k] for k in ("label", "index", "currency")})
        if key in notes:
            res["note"] = notes[key]
        data["markets"][key] = res

    SITE.mkdir(exist_ok=True)
    (SITE / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    (ROOT / "data" / "latest.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    template = (ROOT / "scripts" / "template.html").read_text()
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    (SITE / "index.html").write_text(template.replace("/*__DATA__*/null", payload))
    print("fertig:", SITE / "index.html", file=sys.stderr)


if __name__ == "__main__":
    main()
