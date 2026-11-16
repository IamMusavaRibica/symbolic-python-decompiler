"""Structural facts about an instruction graph: dominators and natural loops."""
from __future__ import annotations

from typing import Dict, List, Optional, Set

import networkx

from decompiler.graph.builder import CodeGraph
from decompiler.graph.node import InstructionNode


class Loop:
    """A natural loop: ``header`` dominates every node of ``nodes``."""

    def __init__(self, header: InstructionNode):
        self.header = header
        self.nodes: Set[InstructionNode] = {header}
        #: sources of the back edges
        self.latches: List[InstructionNode] = []

    def __contains__(self, node: InstructionNode) -> bool:
        return node in self.nodes

    def __repr__(self):
        return f'Loop({self.header!r}, {len(self.nodes)} nodes)'


class GraphAnalysis:
    def __init__(self, graph: CodeGraph):
        self.graph = graph
        self.idom: Dict[InstructionNode, InstructionNode] = networkx.immediate_dominators(graph.nx, graph.entry)
        # Euler tour of the dominator tree gives O(1) dominance queries
        children: Dict[InstructionNode, List[InstructionNode]] = {n: [] for n in self.idom}
        for node, parent in self.idom.items():
            if node is not parent:
                children[parent].append(node)
        self._tin: Dict[InstructionNode, int] = {}
        self._tout: Dict[InstructionNode, int] = {}
        clock = 0
        stack = [(graph.entry, iter(children[graph.entry]))]
        self._tin[graph.entry] = clock
        while stack:
            node, it = stack[-1]
            child = next(it, None)
            if child is None:
                clock += 1
                self._tout[node] = clock
                stack.pop()
            else:
                clock += 1
                self._tin[child] = clock
                stack.append((child, iter(children[child])))
        self.loops: Dict[InstructionNode, Loop] = self._find_loops()

    def dominates(self, a: InstructionNode, b: InstructionNode) -> bool:
        """True when every path from the entry to ``b`` goes through ``a`` (a node dominates itself)."""
        return self._tin[a] <= self._tin[b] and self._tout[b] <= self._tout[a]

    def _find_loops(self) -> Dict[InstructionNode, Loop]:
        loops: Dict[InstructionNode, Loop] = {}
        for node in self.graph.nodes:
            for _, succ in node.successors():
                if self.dominates(succ, node):  # back edge node -> succ
                    loop = loops.get(succ)
                    if loop is None:
                        loop = loops[succ] = Loop(succ)
                    loop.latches.append(node)
                    work = [node]
                    while work:
                        n = work.pop()
                        if n in loop.nodes:
                            continue
                        loop.nodes.add(n)
                        work.extend(p for p, _ in n.preds)
        return loops

    def loop_of(self, header: InstructionNode) -> Optional[Loop]:
        return self.loops.get(header)

    def is_loop_header(self, node: InstructionNode) -> bool:
        return node in self.loops

    def forward_preds(self, node: InstructionNode) -> List[InstructionNode]:
        """Predecessors of ``node`` that are not sources of back edges into it."""
        return [p for p, _ in node.preds if not self.dominates(node, p)]
