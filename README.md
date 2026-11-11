# Börsen-Morgenblick

Jeden Werktag morgens berechnet GitHub Actions die Top-10-Gewinner und -Verlierer für
**DAX 40**, **S&P 500** und **CSI 300** über drei Zeiträume (Vortag, 1 Woche, 1 Monat)
und veröffentlicht das Ergebnis als Dashboard auf GitHub Pages.

- Kurse: Yahoo Finance (via `yfinance`), Schlusskurse bereinigt
- Indexzusammensetzung: Wikipedia, mit Zwischenspeicher in `data/constituents.json`
- Manuell starten: Actions → „Börsen-Dashboard aktualisieren“ → *Run workflow*
- Lokal: `pip install -r requirements.txt && python scripts/build.py` → `site/index.html`

Keine Anlageberatung.
