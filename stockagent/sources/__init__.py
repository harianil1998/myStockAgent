"""Sub-agents, one per information source."""

from .ark import ArkAgent
from .base import SourceAgent
from .congress import CongressAgent
from .discord import DiscordAgent
from .insiders import InsiderAgent
from .market import MarketAgent
from .news import NewsAgent
from .reddit import RedditAgent
from .stocktwits import StockTwitsAgent
from .superinvestors import SuperinvestorAgent
from .twitter import TwitterAgent

REGISTRY: dict[str, type[SourceAgent]] = {
    cls.name: cls
    for cls in (RedditAgent, StockTwitsAgent, TwitterAgent, DiscordAgent, NewsAgent,
                SuperinvestorAgent, ArkAgent, InsiderAgent, CongressAgent, MarketAgent)
}

__all__ = ["REGISTRY", "SourceAgent"]
