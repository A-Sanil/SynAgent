"""Top-level ORD tool — delegates to agents.ordtool (real implementation).

Kept for backwards compatibility with any code importing from synagent.ordtool.
"""
from .agents.ordtool import agent, query_ord_reaction, get_database_stats

__all__ = ["agent", "query_ord_reaction", "get_database_stats"]
