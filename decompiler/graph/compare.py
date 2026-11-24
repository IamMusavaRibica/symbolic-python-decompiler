"""Decide whether two code objects are equivalent *as graphs*.

This is the correctness criterion of the whole project: a decompilation is
accepted when the code object obtained by compiling the decompiled source has
the same instruction graph as the original code object, recursively for every
nested code object.  Byte-for-byte equality is not required (and not expected,
see :mod:`decompiler.graph.builder`).

When the graphs differ as they are, both code objects go through
:mod:`decompiler.graph.peephole` and are compared again, so that the comparison does
not depend on whether the compiler's peephole optimizer ran on them (it skips code
objects with a source line of more than 255 bytes of bytecode).  Not the other way
round: the optimizer is a single pass, and a second pass over already optimized code
can wipe more (an implicit return whose only jump the first pass removed), which
would make equal code look different.
"""
from __future__ import annotations

import difflib
from types import CodeType
from typing import List, NamedTuple, Tuple

from decompiler.graph.dump import dump_code
from decompiler.graph.peephole import optimize_code


class GraphComparison(NamedTuple):
    equal: bool
    #: unified diff of the two canonical dumps ('' when equal)
    diff: str
    #: [dead-code] qualified paths of the code objects whose live graphs are equal while their
    #: unreachable code differs; the comparison counts as equal then (the live code is right)
    dead_only: Tuple[str, ...] = ()

    def __bool__(self):
        return self.equal


def compare_code(original: CodeType, recompiled: CodeType, context: int = 4) -> GraphComparison:
    if dump_code(original) == dump_code(recompiled):
        return GraphComparison(True, '')
    a = dump_code(optimize_code(original))
    b = dump_code(optimize_code(recompiled))
    if a == b:
        return GraphComparison(True, '')
    from decompiler import options
    if options.dead_code:
        # [dead-code] object by object: unreachable code that differs while the live code is equal
        # is reported, not failed
        statuses = compare_objects(original, recompiled)
        if all(status != 'differ' for _, status in statuses):
            return GraphComparison(True, '', tuple(path for path, status in statuses if status == 'live'))
    diff = '\n'.join(difflib.unified_diff(a.splitlines(), b.splitlines(), 'original', 'recompiled',
                                          n=context, lineterm=''))
    return GraphComparison(False, diff)


def compare_objects(original: CodeType, recompiled: CodeType) -> List[Tuple[str, str]]:
    """[dead-code] (qualified path, status) per code object, status one of 'equal' (graphs equal, dead
    code included), 'live' (only the unreachable code differs) and 'differ'."""
    from decompiler.graph.builder import build_graph
    from decompiler.graph.dump import dump_graph

    def own(code: CodeType, dead_code: bool) -> str:
        return '\n'.join(dump_graph(build_graph(code, dead_code=dead_code)))

    def children(code: CodeType) -> List[CodeType]:
        return [n.argval for n in build_graph(code, dead_code=False).nodes
                if n.opname == 'LOAD_CONST' and isinstance(n.argval, CodeType)]

    out: List[Tuple[str, str]] = []

    def walk(a: CodeType, b: CodeType, path: str) -> None:
        if own(a, True) == own(b, True) or own(optimize_code(a), True) == own(optimize_code(b), True):
            status = 'equal'
        elif own(a, False) == own(b, False) or own(optimize_code(a), False) == own(optimize_code(b), False):
            status = 'live'
        else:
            status = 'differ'
        out.append((path, status))
        ca, cb = children(a), children(b)
        if len(ca) != len(cb):
            if status != 'differ':
                out[-1] = (path, 'differ')
            return
        for x, y in zip(ca, cb):
            walk(x, y, f'{path}.{x.co_name}')

    walk(original, recompiled, '<module>')
    return out


def graphs_equal(original: CodeType, recompiled: CodeType) -> bool:
    return dump_code(original) == dump_code(recompiled)         or dump_code(optimize_code(original)) == dump_code(optimize_code(recompiled))
