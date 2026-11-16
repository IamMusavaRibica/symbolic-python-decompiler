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


class StackError(DecompileError):
    """The symbolic stack does not hold what an instruction needs."""
