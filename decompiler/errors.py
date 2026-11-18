"""Exceptions raised by the decompiler."""
from __future__ import annotations


class DecompileError(Exception):
    """The bytecode has a shape the decompiler does not understand (yet).

    ``node`` is the instruction at which the problem was noticed; its index
    refers to the text dump of the graph (``python -m decompiler --graph``).
    """

    def __init__(self, message: str, node=None):
        self.message = message
        self.node = node
        self.code_name = None
        super().__init__(message)

    def __str__(self):
        where = ''
        if self.node is not None:
            where = f' at node {self.node.index} {self.node!r}'
        if self.code_name:
            where += f' in {self.code_name}'
        return self.message + where


class SyntheticLoop(DecompileError):
    """Control leaves through inlined finally bodies to ``exit`` without a loop in the graph.

    Only ``break`` and ``continue`` are compiled that way (``return`` ends in RETURN_VALUE),
    so the code is inside a ``while True:`` loop that no path goes around; the graph has no
    cycle for it.  The loop is synthesized around the statement at frame block depth
    ``depth`` by the block that parses it.
    """

    def __init__(self, exit, depth: int):
        super().__init__('a loop without back edge is left here', exit)
        self.exit = exit
        self.depth = depth


class StackError(DecompileError):
    """The symbolic stack does not hold what an instruction needs."""
