ISSUE_TAGS = (
    "anti-cheat",
    "server-stability",
    "optimization",
    "game-balance",
    "new-content",
    "matchmaking",
    "bugs",
    "monetization",
    "general",
)

DEFAULT_ISSUE_TAG = "general"

ISSUE_TAG_DESCRIPTIONS = {
    "anti-cheat": "hackers, aimbots, wallhacks, cheater reports, anti-cheat complaints",
    "server-stability": "lag, high ping, disconnects, server crashes, desync, region issues",
    "optimization": "FPS drops, stuttering, crashes, hardware performance, loading times",
    "game-balance": "weapon balance, vehicle balance, circle, zone, loot distribution",
    "new-content": "maps, modes, skins, seasons, patches, events",
    "matchmaking": "queue times, skill-based matchmaking, bots, ranking",
    "bugs": "glitches, broken mechanics, visual bugs, audio bugs, exploits",
    "monetization": "pricing, battle pass value, crates, skins, paid content",
    "general": "overall opinion, nostalgia, playtime, recommendation without a specific topic",
}

LEGACY_TAG_ALIASES = {
    "ban": "anti-cheat",
    "cheat": "anti-cheat",
    "cheater": "anti-cheat",
    "hacker": "anti-cheat",
    "server": "server-stability",
    "servers": "server-stability",
    "performance": "optimization",
    "perf": "optimization",
    "balance": "game-balance",
    "content": "new-content",
    "update": "new-content",
    "patch": "new-content",
    "bug": "bugs",
    "bugfix": "bugs",
    "cash": "monetization",
    "monetisation": "monetization",
    "praise": "general",
}

ALERT_STATUSES = ("open", "acknowledged", "resolved", "dismissed")
ACTIVE_ALERT_STATUSES = ("open", "acknowledged")
DRAFT_STATUSES = ("pending", "approved", "rejected", "edited")
