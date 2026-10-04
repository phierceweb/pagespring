"""Reading order for a Sphinx crawl: each staged page nests under its toctree parent,
and siblings follow the toctrees that list them."""

from __future__ import annotations

from heapq import heapify, heappop, heappush

# One toctree sibling list: (the parent's URL, or None when unknown; member URLs).
Siblings = tuple[str | None, tuple[str, ...]]


def _merge(nodes: list[str], pairs: set[tuple[str, str]], rank: dict[str, int]) -> list[str]:
    """``nodes`` honouring each (before, after) pair, lowest rank first among the
    unconstrained; a cycle breaks at its lowest-ranked node."""
    succ: dict[str, list[str]] = {n: [] for n in nodes}
    blocked = dict.fromkeys(nodes, 0)
    for a, b in pairs:
        succ[a].append(b)
        blocked[b] += 1
    free = [(rank[n], n) for n in nodes if not blocked[n]]
    heapify(free)
    out: dict[str, None] = {}
    while len(out) < len(nodes):
        if not free:
            n = min((x for x in nodes if x not in out), key=rank.__getitem__)
            free.append((rank[n], n))
        _r, n = heappop(free)
        if n in out:
            continue
        out[n] = None
        for m in succ[n]:
            blocked[m] -= 1
            if not blocked[m]:
                heappush(free, (rank[m], m))
    return list(out)


def reading_order(
    ranked: list[str],
    alias: dict[str, str],
    found_on: dict[str, str],
    tocs: dict[Siblings, str | None],
) -> list[str]:
    """Staged pages in toctree order: each nests under its toctree parent, else the nearest staged
    page linking it; siblings follow the toctrees, then crawl order (``ranked``)."""
    rank = {u: i for i, u in enumerate(ranked)}
    parent: dict[str, str] = {}  # first claim wins: toctree, then sidebar top level, then link

    def claim(kids: list[str], par: str | None) -> None:
        for kid in kids:
            if par is not None and kid != par and rank[kid]:
                parent.setdefault(kid, par)

    sibling_lists: list[list[str]] = []
    unrooted: list[tuple[str | None, list[str]]] = []
    for (hint, members), page in tocs.items():
        kids = list(dict.fromkeys(alias[m] for m in members if m in alias))
        sibling_lists.append(kids)
        if hint is None:
            unrooted.append((alias.get(page) if page else None, kids))
        else:
            claim(kids, alias.get(hint))
    for page_url, kids in unrooted:
        claim(kids, next((parent[k] for k in kids if k in parent), page_url))
    for url, finder in found_on.items():
        while finder not in alias and finder in found_on:
            finder = found_on[finder]
        if url in alias:
            claim([alias[url]], alias.get(finder))

    children: dict[str, list[str]] = {}
    for kid in ranked:
        if kid in parent:
            children.setdefault(parent[kid], []).append(kid)
    pairs: dict[str, set[tuple[str, str]]] = {}
    for kids in sibling_lists:
        prev: dict[str, str] = {}
        for kid in (k for k in kids if k in parent):
            if parent[kid] in prev:
                pairs.setdefault(parent[kid], set()).add((prev[parent[kid]], kid))
            prev[parent[kid]] = kid

    order: dict[str, None] = {}
    for start in ranked:
        stack = [start]
        while stack:
            node = stack.pop()
            if node not in order:
                order[node] = None
                kids = _merge(children.get(node, []), pairs.get(node, set()), rank)
                stack.extend(reversed(kids))
    return list(order)
