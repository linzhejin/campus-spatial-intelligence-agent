"""Reusable, bounded preparation for routing on one in-memory graph."""

from dataclasses import dataclass
from threading import RLock
import weakref


@dataclass(frozen=True)
class ModeIndex:
    graph: object
    mode_status: str
    mode_penalty: dict
    max_len: float
    norm_lengths: dict


class RoutingIndex:
    def __init__(self, graph):
        self.graph = graph
        self._modes = {}

    def for_mode(self, mode):
        from spatial.routing import normalize_mode

        normalized = normalize_mode(mode)
        if normalized not in self._modes:
            self._modes[normalized] = build_mode_index(self.graph, normalized)
        return self._modes[normalized]


def build_mode_index(graph, mode):
    # Local imports prevent a cycle when spatial.routing asks for the index.
    from spatial.routing import _normalize_lengths, filter_graph_for_mode, normalize_mode

    normalized = normalize_mode(mode)
    mode_graph, status, penalty = filter_graph_for_mode(graph, normalized)
    max_len, norm_lengths = _normalize_lengths(mode_graph)
    return ModeIndex(
        graph=mode_graph,
        mode_status=status,
        mode_penalty=dict(penalty),
        max_len=max_len,
        norm_lengths=norm_lengths,
    )


_lock = RLock()
_graph_ref = None
_index = None


def get_routing_index(graph):
    """Return the single index associated with the current graph object."""
    global _graph_ref, _index
    with _lock:
        if _graph_ref is None or _graph_ref() is not graph:
            _graph_ref = weakref.ref(graph)
            _index = RoutingIndex(graph)
        return _index


def invalidate_routing_index():
    """Discard prepared views after a graph reload or explicit cache clear."""
    global _graph_ref, _index
    with _lock:
        _graph_ref = None
        _index = None
