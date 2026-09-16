"""Decision-making.

Bots never decide legality for themselves - they ask the rules engine what is
legal and choose from that list. A second opinion about the rules would be a
second set of rules, and the two would drift apart the moment either changed.
"""

from .simple import SimpleAgent

__all__ = ["SimpleAgent"]
