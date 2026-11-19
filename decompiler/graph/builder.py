"""Build the jump-free instruction graph of a code object.

The representation deliberately forgets how the compiler laid the basic blocks
out: ``JUMP_FORWARD`` / ``JUMP_ABSOLUTE`` (and ``EXTENDED_ARG`` / ``NOP``) are
not nodes, edges are simply threaded through them, and the two conditional
pop-jumps are normalised to ``true`` / ``false`` edges.  Two pieces of bytecode
that only differ in block order or in the sense of a conditional jump therefore
produce the same graph, e.g. ::

    if not x:        if x:
        a                b
    else:            else:
        b                a

Only instructions reachable from the entry point are part of the graph.
"""
from __future__ import annotations

import dis
from collections import deque
from types import CodeType
from typing import Dict, List, Optional, Tuple

import networkx

from decompiler import options
from decompiler.graph.node import (InstructionNode, OP_ENTRY, OP_SPIN, NEXT, TRUE, FALSE, JUMP, DEAD, EDGE_ORDER)

UNCONDITIONAL_JUMPS = frozenset({'JUMP_FORWARD', 'JUMP_ABSOLUTE'})
#: instructions after which execution never continues with the following instruction
NO_FALLTHROUGH = UNCONDITIONAL_JUMPS | {'RETURN_VALUE', 'RAISE_VARARGS', 'RERAISE'}
#: instructions that are skipped as if they were not there
TRANSPARENT = frozenset({'EXTENDED_ARG', 'NOP'})
#: [dead-code] instructions after which the physically following instruction is unreachable
TERMINATORS = frozenset({'RETURN_VALUE', 'RAISE_VARARGS', 'RERAISE'})
HAS_JUMP_TARGET = frozenset(dis.opname[op] for op in dis.hasjabs + dis.hasjrel)


class CodeGraph:
    """The instruction graph of one code object.

    ``nx`` is a :class:`networkx.MultiDiGraph` (edge key == edge label) kept for
    graph algorithms and rendering; the nodes additionally carry their
    successors/predecessors directly (``node.out`` / ``node.preds``).
    """

    def __init__(self, code: CodeType, dead_code: Optional[bool] = None):
        self.code: CodeType = code
        #: [dead-code] whether unreachable instructions are part of the graph (see decompiler.options)
        self.dead_code: bool = options.dead_code if dead_code is None else dead_code
        #: [dead-code] (anchor, entry): the live instruction after which the dead region at entry follows
        self.dead_entries: List[Tuple[InstructionNode, InstructionNode]] = []
        self._dead_phase = False
        self.nx = networkx.MultiDiGraph()
        self.entry = InstructionNode(OP_ENTRY, None, None, -1)
        self.nx.add_node(self.entry)
        self._by_offset: Dict[int, InstructionNode] = {}
        self._spins: Dict[int, InstructionNode] = {}
        self._build()
        #: nodes in canonical order: DFS pre-order from the entry following EDGE_ORDER
        self.nodes: List[InstructionNode] = self._number()

    # -- construction -------------------------------------------------------
    def _add_edge(self, src: InstructionNode, dst: InstructionNode, label: str):
        if label == DEAD:
            src.dead_next = dst  # [dead-code]
        else:
            src.out[label] = dst
        if src.dead and not dst.dead:
            # [dead-code] dead code flowing into live code: followed, but no predecessor of the live
            # node and no part of the dominator tree, so that the live structure is as without it
            return
        dst.preds.append((src, label))
        self.nx.add_edge(src, dst, key=label, label=label)

    def _build(self):
        instructions = list(dis.get_instructions(self.code))
        if not instructions:
            return
        by_offset = {i.offset: i for i in instructions}
        offsets = [i.offset for i in instructions]
        following = dict(zip(offsets, offsets[1:]))
        linenos: Dict[int, Optional[int]] = {}
        current = None
        for i in instructions:
            if i.starts_line is not None:
                current = i.starts_line
            linenos[i.offset] = current

        def resolve(offset: Optional[int], thread_jumps: bool = True) -> Tuple[Optional[object], List[int]]:
            """Follow transparent instructions and unconditional jumps starting at ``offset``.

            Returns ``(target, jumps)``: target is the offset of the first real
            instruction, ``('spin', key)`` for a cycle consisting only of jumps or
            None when execution would run off the end; jumps are the offsets of
            the unconditional jumps that were threaded through.
            """
            path: List[int] = []
            while offset is not None:
                instr = by_offset.get(offset)
                if instr is None:
                    return None, path
                if instr.opname in TRANSPARENT:
                    offset = following.get(offset)
                elif instr.opname in UNCONDITIONAL_JUMPS and (thread_jumps or offset in live_jumps):
                    # ([dead-code] a jump that live code goes through is never a node)
                    if offset in path:
                        return ('spin', min(path[path.index(offset):])), path
                    path.append(offset)
                    offset = instr.argval
                else:
                    return offset, path
            return None, path

        queue = deque()

        def node_for(target) -> Optional[InstructionNode]:
            if target is None:
                return None
            if isinstance(target, tuple):
                key = target[1]
                node = self._spins.get(key)
                if node is None:
                    node = self._spins[key] = InstructionNode(OP_SPIN, None, None, key, linenos.get(key))
                    self.nx.add_node(node)
                    self._add_edge(node, node, NEXT)
                return node
            node = self._by_offset.get(target)
            if node is None:
                node = self._by_offset[target] = InstructionNode.from_instruction(by_offset[target], linenos[target])
                node.dead = self._dead_phase  # [dead-code]
                self.nx.add_node(node)
                queue.append(node)
            return node

        threaded: Dict[int, int] = {}  # [dead-code] node offset -> first jump its fall-through went through
        live_jumps = set()  # [dead-code] offsets of the jumps live code goes through

        def connect(src: InstructionNode, raw_offset: Optional[int], label: str, origin: int):
            # [dead-code] in dead regions jumps are kept as nodes: a dead 'break' / 'continue' is one
            target, jumps = resolve(raw_offset, not self._dead_phase)
            if jumps and origin >= 0 and raw_offset == following.get(origin):
                threaded[origin] = jumps[0]  # [dead-code]
                src.threaded = True
                src.threaded_via = jumps[0]  # [dead-code]
            if not self._dead_phase:
                live_jumps.update(jumps)  # [dead-code]
            dst = node_for(target)
            if dst is None:
                return
            self._add_edge(src, dst, label)
            # layout hints: which jump instructions go backwards to dst
            if not dst.is_pseudo:
                for j in jumps:
                    if dst.offset <= j:
                        dst.back_jumps.append(j)
                if not jumps and label != NEXT and origin >= 0 and dst.offset <= origin:
                    dst.back_jumps.append(origin)

        def drain():
            while queue:
                node = queue.popleft()
                name = node.opname
                fall = following.get(node.offset)
                if name in UNCONDITIONAL_JUMPS:
                    connect(node, node.argval, JUMP, node.offset)  # [dead-code] (only dead ones are nodes)
                elif name in HAS_JUMP_TARGET:
                    target = node.argval
                    if name == 'POP_JUMP_IF_FALSE':
                        connect(node, fall, TRUE, node.offset)
                        connect(node, target, FALSE, node.offset)
                    elif name == 'POP_JUMP_IF_TRUE':
                        connect(node, target, TRUE, node.offset)
                        connect(node, fall, FALSE, node.offset)
                    else:
                        connect(node, fall, NEXT, node.offset)
                        connect(node, target, JUMP, node.offset)
                elif name not in NO_FALLTHROUGH:
                    connect(node, fall, NEXT, node.offset)

        connect(self.entry, offsets[0], NEXT, -1)
        drain()
        if self.dead_code:
            # [dead-code] Unreachable instructions: whatever physically follows a terminator or an
            # unconditional jump and is not reached otherwise.  Dead regions are built like live
            # code (their own terminators get continuations too), in order of discovery.
            self._dead_phase = True
            examined = set()
            while True:
                anchors = [n for n in self._by_offset.values() if n not in examined]
                if not anchors:
                    break
                for anchor in sorted(anchors, key=lambda n: n.offset):
                    examined.add(anchor)
                    if anchor.opname in TERMINATORS or anchor.opname in UNCONDITIONAL_JUMPS:
                        after = following.get(anchor.offset)
                        while after in live_jumps:
                            # a live jump that a branch was threaded through: what follows it is
                            # the terminator's unreachable successor just the same
                            after = following.get(after)
                    elif anchor.offset in threaded:
                        after = following.get(threaded[anchor.offset])
                    else:
                        continue
                    target, _ = resolve(after, False)
                    if target is None or isinstance(target, tuple):
                        continue
                    existing = self._by_offset.get(target)
                    if existing is not None:
                        if existing.dead and not anchor.dead and existing.opname not in UNCONDITIONAL_JUMPS:
                            # a dead region reached from a second (live) terminator as well; a dead
                            # anchor's physical successor inside a region is the region's own business
                            # (and a jump never starts statements: see the probe below)
                            self._add_edge(anchor, existing, DEAD)
                            self.dead_entries.append((anchor, existing))
                        continue
                    # A region of nothing but jumps (a loop's back jump after a 'break', the jump
                    # over an exception handler after a 'return') is left out: which of two
                    # spellings with the same graph was written decides whether it exists.
                    probe = target
                    while probe is not None and by_offset[probe].opname in UNCONDITIONAL_JUMPS | TRANSPARENT:
                        probe = following.get(probe)
                    if probe is None or probe in self._by_offset:
                        continue
                    entry = node_for(target)
                    self._add_edge(anchor, entry, DEAD)
                    self.dead_entries.append((anchor, entry))
                    drain()
        self._undo_jump_simplification()

    def _remove_edges(self, node: InstructionNode):
        for label, dst in list(node.out.items()):
            dst.preds.remove((node, label))
            self.nx.remove_edge(node, dst, key=label)
        node.out.clear()

    def _undo_jump_simplification(self):
        """Put back the conditional jumps the peephole optimizer threaded through each other.

        ``(a or b) and c`` as a value compiles to ``a: JUMP_IF_TRUE_OR_POP y`` with
        ``y: JUMP_IF_FALSE_OR_POP z``; the optimizer turns the first one into
        ``POP_JUMP_IF_TRUE y+1`` (and the mirror image for ``(a and b) or c``),
        which jumps into the middle of a boolean operation.  Both spellings
        behave the same, and the one that goes through ``y`` is the one the
        structure of the expression can be read from.
        """
        rewrites = {'POP_JUMP_IF_TRUE': (TRUE, FALSE, 'JUMP_IF_FALSE_OR_POP', 'JUMP_IF_TRUE_OR_POP'),
                    'POP_JUMP_IF_FALSE': (FALSE, TRUE, 'JUMP_IF_TRUE_OR_POP', 'JUMP_IF_FALSE_OR_POP')}
        changed = True
        while changed:
            changed = False
            for node in list(self._by_offset.values()):
                spec = rewrites.get(node.opname)
                if spec is None:
                    continue
                taken, other_label, through, new_name = spec
                target, other = node.out.get(taken), node.out.get(other_label)
                if target is None or other is None or target.is_pseudo:
                    continue
                via = [p for p, label in target.preds if label == NEXT and p.opname == through]
                if not via:
                    continue
                self._remove_edges(node)
                node.opcode, node.opname = dis.opmap[new_name], new_name
                node.arg = node.argval = via[0].offset
                self._add_edge(node, other, NEXT)
                self._add_edge(node, via[0], JUMP)
                changed = True

    def _number(self) -> List[InstructionNode]:
        order: List[InstructionNode] = []
        seen = set()
        stack = [self.entry]
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            node.index = len(order)
            order.append(node)
            if node.dead_next is not None and node.dead_next not in seen:  # [dead-code] numbered last
                stack.append(node.dead_next)
            for label in reversed(EDGE_ORDER):
                succ = node.out.get(label)
                if succ is not None and succ not in seen:
                    stack.append(succ)
        return order

    # -- queries ------------------------------------------------------------
    @property
    def first(self) -> Optional[InstructionNode]:
        """The first real instruction."""
        return self.entry.next

    def node_at(self, offset: int) -> Optional[InstructionNode]:
        return self._by_offset.get(offset)

    def __len__(self):
        return len(self.nodes)

    def __iter__(self):
        return iter(self.nodes)


def build_graph(code: CodeType, dead_code: Optional[bool] = None) -> CodeGraph:
    return CodeGraph(code, dead_code)
