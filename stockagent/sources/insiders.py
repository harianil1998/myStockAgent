"""Insider sub-agent: SEC Form 4 filings, near-realtime from EDGAR's current feed.

Open-market purchases (code "P") by officers/directors are one of the best
documented bullish signals; clusters of them even more so.
"""

from __future__ import annotations

import asyncio
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import feedparser

from ..models import Signal, SignalKind
from .base import SourceAgent

FEED = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&company=&dateb="
        "&owner=include&start=0&count={n}&output=atom")


def _txt(el, path: str) -> str:
    found = el.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def parse_form4(xml: str) -> list[dict]:
    """Extract non-derivative transactions from a Form 4 ownershipDocument."""
    root = ET.fromstring(xml)
    ticker = _txt(root, "issuer/issuerTradingSymbol").upper()
    owner = _txt(root, "reportingOwner/reportingOwnerId/rptOwnerName")
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    role = []
    if rel is not None:
        if _txt(rel, "isDirector") in ("1", "true"):
            role.append("Director")
        if _txt(rel, "isOfficer") in ("1", "true"):
            role.append(_txt(rel, "officerTitle") or "Officer")
        if _txt(rel, "isTenPercentOwner") in ("1", "true"):
            role.append("10% owner")
    plan = _txt(root, "aff10b5One") in ("1", "true")
    txs = []
    for t in root.iter("nonDerivativeTransaction"):
        code = _txt(t, "transactionCoding/transactionCode")
        shares = float(_txt(t, "transactionAmounts/transactionShares/value") or 0)
        price = float(_txt(t, "transactionAmounts/transactionPricePerShare/value") or 0)
        txs.append({
            "ticker": ticker, "owner": owner, "role": ", ".join(role) or "Insider", "code": code,
            "shares": shares, "price": price, "value": shares * price, "plan_10b5_1": plan,
            "date": _txt(t, "transactionDate/value"),
        })
    return txs


class InsiderAgent(SourceAgent):
    name = "insiders"

    async def fetch(self) -> list[Signal]:
        n = int(self.options.get("max_filings", 40))
        async with self.client() as c:
            r = await c.get(FEED.format(n=n))
            r.raise_for_status()
            seen = set(self.store.get_state("insiders:seen", []))
            links = []
            for e in feedparser.parse(r.text).entries:
                link = e.get("link", "")
                acc = link.rsplit("/", 1)[-1].replace("-index.htm", "")
                if link.endswith("-index.htm") and acc not in seen:
                    seen.add(acc)
                    links.append(link)
            out: list[Signal] = []
            for link in links:
                try:
                    r = await c.get(link.replace("-index.htm", ".txt"))
                    r.raise_for_status()
                except Exception:
                    continue
                m = re.search(r"<ownershipDocument>.*?</ownershipDocument>", r.text, re.S)
                if m:
                    out.extend(self.to_signals(parse_form4(m.group(0)), link))
                await asyncio.sleep(0.15)  # SEC fair-access
            self.store.set_state("insiders:seen", list(seen)[-2000:])
        return out

    def to_signals(self, txs: list[dict], url: str) -> list[Signal]:
        min_value = float(self.options.get("min_value_usd", 100_000))
        out = []
        for tx in txs:
            if not tx["ticker"] or tx["value"] < min_value:
                continue
            if tx["code"] == "P":
                sentiment = 0.9
            elif tx["code"] == "S" and not tx["plan_10b5_1"]:
                sentiment = -0.4  # sales are often diversification; weaker signal
            else:
                continue
            ts = datetime.now(timezone.utc)
            if tx["date"]:
                ts = datetime.fromisoformat(tx["date"][:10]).replace(tzinfo=timezone.utc)
            verb = "BOUGHT" if tx["code"] == "P" else "SOLD"
            out.append(Signal(
                source="sec/form4", ticker=tx["ticker"], kind=SignalKind.INSIDER, sentiment=sentiment,
                text=f"{tx['owner']} ({tx['role']}) {verb} {tx['shares']:,.0f} sh @ ${tx['price']:.2f} = ${tx['value']:,.0f}",
                url=url, author=tx["owner"], timestamp=ts,
                weight=1.0 + math.log10(max(tx["value"], 1)) - 4,  # $100k -> 2, $10M -> 4
                extra=tx,
            ))
        return out
