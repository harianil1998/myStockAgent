"""Discord sub-agent: reads messages from trading-server channels via a bot.

Setup: create a bot at discord.com/developers, enable the MESSAGE CONTENT
intent, invite it to the servers you're a member of, and list the channel IDs
(alerts / trade-ideas channels work best) in config. Token: DISCORD_BOT_TOKEN.
Only add bots to servers whose rules allow it.
"""

from __future__ import annotations

import os
from datetime import datetime

from ..models import Signal, SignalKind
from ..sentiment import score_text
from .base import SourceAgent

API = "https://discord.com/api/v10"


class DiscordAgent(SourceAgent):
    name = "discord"
    requires_env = ("DISCORD_BOT_TOKEN",)

    async def fetch(self) -> list[Signal]:
        headers = {"Authorization": f"Bot {os.environ['DISCORD_BOT_TOKEN']}"}
        out: list[Signal] = []
        async with self.client(headers=headers) as c:
            for ch in self.options.get("channel_ids", []):
                ch = str(ch)
                key = f"discord:after:{ch}"
                params = {"limit": 100}
                if after := self.store.get_state(key):
                    params["after"] = after
                msgs = await self.get_json(c, f"{API}/channels/{ch}/messages", params=params)
                if msgs:
                    self.store.set_state(key, max(msgs, key=lambda m: int(m["id"]))["id"])
                out.extend(self.parse(msgs, ch))
        return out

    def parse(self, msgs: list[dict], channel_id: str) -> list[Signal]:
        out = []
        for m in msgs:
            parts = [m.get("content", "")]
            for e in m.get("embeds", []):  # alert bots usually post embeds
                parts += [e.get("title", ""), e.get("description", "")]
                parts += [f"{f.get('name', '')} {f.get('value', '')}" for f in e.get("fields", [])]
            text = "\n".join(p for p in parts if p)
            tickers = self.universe.extract(text)
            if not tickers or len(tickers) > 5:
                continue
            ts = datetime.fromisoformat(m.get("timestamp", "1970-01-01T00:00:00+00:00"))
            for t in tickers:
                out.append(Signal(
                    source=f"discord/{channel_id}", ticker=t, kind=SignalKind.SOCIAL,
                    sentiment=score_text(text), text=text[:600],
                    url=f"https://discord.com/channels/{m.get('guild_id', '@me')}/{channel_id}/{m.get('id')}",
                    author=(m.get("author") or {}).get("username", ""), timestamp=ts,
                    weight=1.5 / len(tickers),  # curated channels: a bit above random chatter
                ))
        return out
