"""Nodes of the instruction graph.

One node is one *reachable* bytecode instruction.  Unconditional jumps never
become nodes (see :mod:`decompiler.graph.builder`), so the only way control
flow is expressed is through labelled edges:

=========  ===============================================================
label      meaning
=========  ===============================================================
``next``   plain fall-through to the following instruction
``true``   ``POP_JUMP_IF_TRUE`` / ``POP_JUMP_IF_FALSE``: the popped value
           was truthy (regardless of which of the two opcodes was used)
``false``  same two opcodes: the popped value was falsy
``jump``   the "other" way out of every remaining instruction that owns a
           jump target: ``JUMP_IF_TRUE_OR_POP`` / ``JUMP_IF_FALSE_OR_POP``
           (value kept), ``FOR_ITER`` (iterator exhausted),
           ``SETUP_FINALLY`` / ``SETUP_WITH`` / ``SETUP_ASYNC_WITH``
           (exception handler entry), ``JUMP_IF_NOT_EXC_MATCH`` (no match)
=========  ===============================================================
"""
from __future__ import annotations

import dis
from typing import Dict, Iterator, List, Optional, Tuple

# Edge labels, in the order in which they are followed by every traversal
# that has to be deterministic (text dump, comparison).
NEXT = 'next'
TRUE = 'true'
FALSE = 'false'
JUMP = 'jump'
#: [dead-code] from a terminator (or the instruction before a threaded jump) to the unreachable
#: instruction that physically follows; not in EDGE_ORDER, so successors() never yields it
DEAD = 'dead'
EDGE_ORDER = (NEXT, TRUE, FALSE, JUMP)

# Pseudo instructions (negative opcodes never collide with real ones)
OP_ENTRY = -1  # unique root of every graph, has a single ``next`` edge
OP_SPIN = -2  # a cycle made only of unconditional jumps (``while True: pass``)

PSEUDO_OPNAMES = {
    OP_ENTRY: '*ENTRY',
    OP_SPIN: '*SPIN',
}

BRANCH_OPS = frozenset({'POP_JUMP_IF_TRUE', 'POP_JUMP_IF_FALSE'})


class InstructionNode:
    """A single instruction together with its labelled successors.

    ``offset`` and ``lineno`` describe where the instruction was found in the
    original bytecode.  They are *hints* only: nothing that decides whether
    two graphs are equal may look at them.
    """

    __slots__ = ('opcode', 'opname', 'arg', 'argval', 'offset', 'lineno',
                 'out', 'preds', 'back_jumps', 'index', 'dead', 'dead_next', 'threaded', 'threaded_via', 'threaded_op', '__weakref__')

    def __init__(self, opcode: int, arg, argval, offset: int, lineno: Optional[int] = None):
        self.opcode: int = opcode
        self.opname: str = PSEUDO_OPNAMES[opcode] if opcode < 0 else dis.opname[opcode]
        self.arg = arg
        self.argval = argval
        self.offset: int = offset
        self.lineno: Optional[int] = lineno
        #: label -> successor
        self.out: Dict[str, 'InstructionNode'] = {}
        #: (predecessor, label) pairs
        self.preds: List[Tuple['InstructionNode', str]] = []
        #: offsets of the jump instructions that jump *backwards* to this node (layout hint)
        self.back_jumps: List[int] = []
        #: position in the canonical (DFS pre-order) numbering, filled in by CodeGraph
        self.index: int = -1
        #: [dead-code] not reachable from the entry
        self.dead: bool = False
        #: [dead-code] the unreachable instruction that physically follows a terminator (kept out of
        #: ``out`` so that nothing that looks at successors sees it)
        self.dead_next: Optional['InstructionNode'] = None
        #: [dead-code] the fall-through of this instruction went through an unconditional jump
        self.threaded: bool = False
        #: [dead-code] offset of the unconditional jump that fall-through went through
        self.threaded_via: Optional[int] = None
        #: [dead-code] opname of that jump (JUMP_ABSOLUTE: a 'continue' or a loop's end jump; JUMP_FORWARD: the compiler's own jump over an else clause or a handler)
        self.threaded_op: Optional[str] = None

    @classmethod
    def from_instruction(cls, instr: dis.Instruction, lineno: Optional[int] = None) -> 'InstructionNode':
        return cls(instr.opcode, instr.arg, instr.argval, instr.offset, lineno)

    # -- successors ---------------------------------------------------------
    @property
    def next(self) -> Optional['InstructionNode']:
        return self.out.get(NEXT)

    @property
    def jump(self) -> Optional['InstructionNode']:
        return self.out.get(JUMP)

    @property
    def true(self) -> Optional['InstructionNode']:
        return self.out.get(TRUE)

    @property
    def false(self) -> Optional['InstructionNode']:
        return self.out.get(FALSE)

    def successors(self) -> Iterator[Tuple[str, 'InstructionNode']]:
        """(label, node) pairs in canonical label order."""
        for label in EDGE_ORDER:
            succ = self.out.get(label)
            if succ is not None:
                yield label, succ

    def pred_nodes(self) -> List['InstructionNode']:
        return [p for p, _ in self.preds]

    # -- classification -----------------------------------------------------
    @property
    def is_branch(self) -> bool:
        """POP_JUMP_IF_TRUE / POP_JUMP_IF_FALSE (normalised to true/false edges)."""
        return self.opname in BRANCH_OPS

    @property
    def is_pseudo(self) -> bool:
        return self.opcode < 0

    def __repr__(self):
        arg = '' if self.opcode < dis.HAVE_ARGUMENT else f' {self.argval!r}'
        return f'<{self.offset} {self.opname}{arg}>'
