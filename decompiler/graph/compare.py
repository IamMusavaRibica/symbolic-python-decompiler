"""Decide whether two code objects are equivalent *as graphs*.

This is the correctness criterion of the whole project: a decompilation is
accepted when the code object obtained by compiling the decompiled source has
the same instruction graph as the original code object, recursively for every
nested code object.  Byte-for-byte equality is not required (and not expected,
see :mod:`decompiler.graph.builder`).
"""
from __future__ import annotations

import difflib
from types import CodeType
from typing import NamedTuple

from decompiler.graph.dump import dump_code


class GraphComparison(NamedTuple):
    equal: bool
    #: unified diff of the two canonical dumps ('' when equal)
    diff: str

    def __bool__(self):
        return self.equal


def compare_code(original: CodeType, recompiled: CodeType, context: int = 4) -> GraphComparison:
    a = dump_code(original)
    b = dump_code(recompiled)
    if a == b:
        return GraphComparison(True, '')
    diff = '\n'.join(difflib.unified_diff(a.splitlines(), b.splitlines(), 'original', 'recompiled',
                                          n=context, lineterm=''))
    return GraphComparison(False, diff)


def graphs_equal(original: CodeType, recompiled: CodeType) -> bool:
    return dump_code(original) == dump_code(recompiled)
