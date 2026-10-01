"""Superinvestor sub-agent: diffs the two latest 13F-HR filings of famous funds.

13Fs are quarterly and lag up to 45 days, so these are slow but high-quality
"best portfolio" signals: new positions and big adds by top managers.
"""

from __future__ import annotations

import asyncio
import math
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from ..models import Signal, SignalKind
from .base import SourceAgent

# CIKs from SEC EDGAR. Override/extend with `funds:` in config.yaml.
DEFAULT_FUNDS = {
    "Berkshire Hathaway (Buffett)": "0001067983",
    "Pershing Square (Ackman)": "0001336528",
    "Scion Asset Mgmt (Burry)": "0001649339",
    "Appaloosa (Tepper)": "0001656456",
    "Duquesne Family Office (Druckenmiller)": "0001536411",
    "Baupost (Klarman)": "0001061768",
    "Third Point (Loeb)": "0001040273",
    "Greenlight (Einhorn)": "0001079114",
    "Himalaya Capital (Li Lu)": "0001709323",
    "Tiger Global": "0001167483",
}


def parse_info_table(xml: str) -> dict[str, dict]:
    """Return {cusip: {name, shares, value}} with puts/calls excluded."""
    root = ET.fromstring(xml)
    holdings: dict[str, dict] = {}
    for el in root.iter():
        if not el.tag.endswith("infoTable"):
            continue
        f = {c.tag.split("}")[-1]: c for c in el.iter()}
        if f.get("putCall") is not None and (f["putCall"].text or "").strip():
            continue
        cusip = (f["cusip"].text or "").strip()
        h = holdings.setdefault(cusip, {"name": (f["nameOfIssuer"].text or "").strip(), "shares": 0.0, "value": 0.0})
        h["shares"] += float(f["sshPrnamt"].text or 0)
        h["value"] += float(f["value"].text or 0)
    return holdings


def diff_holdings(prev: dict[str, dict], cur: dict[str, dict], min_change: float = 0.2) -> list[dict]:
    total = sum(h["value"] for h in cur.values()) or 1.0
    changes = []
    for cusip, h in cur.items():
        p = prev.get(cusip)
        pct_port = h["value"] / total
        if p is None:
            changes.append({"cusip": cusip, "name": h["name"], "action": "NEW", "change": 1.0, "pct_port": pct_port})
        elif p["shares"] and (h["shares"] - p["shares"]) / p["shares"] >= min_change:
            changes.append({"cusip": cusip, "name": h["name"], "action": "ADD",
                            "change": (h["shares"] - p["shares"]) / p["shares"], "pct_port": pct_port})
        elif p["shares"] and (h["shares"] - p["shares"]) / p["shares"] <= -min_change:
            changes.append({"cusip": cusip, "name": h["name"], "action": "TRIM",
                            "change": (h["shares"] - p["shares"]) / p["shares"], "pct_port": pct_port})
    for cusip, p in prev.items():
        if cusip not in cur:
            changes.append({"cusip": cusip, "name": p["name"], "action": "EXIT", "change": -1.0, "pct_port": 0.0})
    return changes


class SuperinvestorAgent(SourceAgent):
    name = "superinvestors"

    async def fetch(self) -> list[Signal]:
        funds = self.options.get("funds") or DEFAULT_FUNDS
        out: list[Signal] = []
        async with self.client() as c:
            for fund, cik in funds.items():
                out.extend(await self._fund(c, fund, str(cik).zfill(10)))
                await asyncio.sleep(0.2)  # SEC fair-access: <=10 req/s
        return out

    async def _fund(self, c, fund: str, cik: str) -> list[Signal]:
        subs = await self.get_json(c, f"https://data.sec.gov/submissions/CIK{cik}.json")
        recent = subs["filings"]["recent"]
        idx = [i for i, f in enumerate(recent["form"]) if f == "13F-HR"][:2]
        if len(idx) < 2:
            return []
        latest_acc = recent["accessionNumber"][idx[0]]
        state_key = f"13f:{cik}"
        if self.store.get_state(state_key) == latest_acc:
            return []  # already processed this quarter's filing
        tables = [await self._info_table(c, cik, recent["accessionNumber"][i]) for i in idx]
        filed = datetime.fromisoformat(recent["filingDate"][idx[0]]).replace(tzinfo=timezone.utc)
        signals = self.to_signals(fund, diff_holdings(tables[1], tables[0]), filed,
                                  f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=13F-HR")
        self.store.set_state(state_key, latest_acc)
        return signals

    async def _info_table(self, c, cik: str, acc: str) -> dict[str, dict]:
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}"
        index = await self.get_json(c, f"{base}/index.json")
        xmls = [i["name"] for i in index["directory"]["item"]
                if i["name"].lower().endswith(".xml") and "primary_doc" not in i["name"].lower()]
        if not xmls:
            return {}
        r = await c.get(f"{base}/{xmls[0]}")
        r.raise_for_status()
        return parse_info_table(r.text)

    def to_signals(self, fund: str, changes: list[dict], filed: datetime, url: str) -> list[Signal]:
        sentiment_for = {"NEW": 0.9, "ADD": 0.6, "TRIM": -0.4, "EXIT": -0.7}
        out = []
        for ch in changes:
            ticker = self.universe.lookup_issuer(ch["name"])
            if not ticker:
                continue
            # Bigger conviction = bigger slice of the portfolio.
            weight = 3.0 + 20 * ch["pct_port"] + math.log1p(abs(ch["change"]))
            out.append(Signal(
                source="13f", ticker=ticker, kind=SignalKind.SMART_MONEY, sentiment=sentiment_for[ch["action"]],
                text=f"{fund}: {ch['action']} {ch['name']} ({ch['change']:+.0%} shares, {ch['pct_port']:.1%} of portfolio)",
                url=url, author=fund, timestamp=filed, weight=weight, extra=ch,
            ))
        return out
