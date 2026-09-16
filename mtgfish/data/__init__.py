"""Card acquisition and deck import.

This is the only layer that talks to the network. Everything downstream reads
from a local SQLite snapshot, so a simulation run is reproducible against a
fixed card pool: the snapshot's content hash is recorded in the run metadata,
and a replay refuses to reconstruct a game whose card pool has since changed.
"""
