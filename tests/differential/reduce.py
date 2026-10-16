"""
Shrink a failing case to a minimal one. Hypothesis' own shrinking struggles here: the
label sets amlint's prober adds change with the config, so a smaller config may lose the
label set that showed the bug. This reducer keeps the failing label set fixed and greedily
deletes whatever it can (routes, matchers, keys, list items, labels) and hoists child
routes into their parent's place, keeping each change only if the case still fails.
"""

from typing import Any, Callable, Iterator, List, Tuple

Path = Tuple[Any, ...]


def _paths(value) -> List[Tuple[Path, Any]]:
    """Every (path, value) inside `value`, breadth-first."""
    out: List[Tuple[Path, Any]] = []
    queue: List[Tuple[Path, Any]] = [((), value)]
    while queue:
        path, v = queue.pop(0)
        out.append((path, v))
        if isinstance(v, dict):
            queue += [(path + (k,), x) for k, x in v.items()]
        elif isinstance(v, list):
            queue += [(path + (i,), x) for i, x in enumerate(v)]
    return out


def _replace(value, path: Path, make: Callable[[Any, Any], Any]):
    """A copy of `value` with the container holding `path` rebuilt by make(container, key)."""
    if len(path) == 1:
        return make(value, path[0])
    head = path[0]
    child = _replace(value[head], path[1:], make)
    if isinstance(value, dict):
        return {**value, head: child}
    return value[:head] + [child] + value[head + 1:]


def _without(container, key):
    if isinstance(container, dict):
        return {k: v for k, v in container.items() if k != key}
    return container[:key] + container[key + 1:]


def _hoisted(container, key):
    """The route at container[key] replaced by its child routes."""
    return container[:key] + list(container[key]["routes"]) + container[key + 1:]


def _variants(case, path: Path, value) -> Iterator[Any]:
    yield _replace(case, path, _without)
    if isinstance(path[-1], int) and isinstance(value, dict) and value.get("routes"):
        yield _replace(case, path, _hoisted)


def reduce(case, fails: Callable[[Any], bool]):
    """The smallest variant of `case` that still `fails`, found greedily. Paths are tried
    deepest-last-first, so a change never shifts a path that is still to be tried."""
    progress = True
    while progress:
        progress = False
        for path, value in reversed(_paths(case)):
            if not path:
                continue
            for variant in _variants(case, path, value):
                if fails(variant):
                    case, progress = variant, True
                    break
    return case
