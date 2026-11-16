"""Canonical text form of instruction graphs.

``dump_code`` renders a code object (and, recursively, every code object it
loads) as text.  The text is *canonical*: nodes are numbered in DFS pre-order
from the entry following the edge labels in a fixed order, and nothing that
depends on layout (offsets, line numbers, constant/name table order) is
printed.  Two code objects therefore have the same dump exactly when their
graphs are isomorphic, which is what :mod:`decompiler.graph.compare` uses.

The same text is the easiest way to *look* at a graph without rendering it::

      0 *ENTRY
      1 LOAD_NAME x
      2 POP_JUMP_IF            T->3 F->6
      3 LOAD_NAME a
      4 POP_TOP
      5 LOAD_CONST None        ->8
      6 LOAD_NAME b
      7 POP_TOP
      8 RETURN_VALUE

A trailing ``->N`` is a fall-through to a node that is not printed on the next
line; ``T->``/``F->`` are the two sides of a conditional pop-jump and ``J->``
is the jump side of every other instruction that has one.
"""
from __future__ import annotations

import dis
from types import CodeType
from typing import Dict, List, Optional

from decompiler.graph.builder import CodeGraph, build_graph
from decompiler.graph.node import InstructionNode, NEXT, TRUE, FALSE, JUMP

_HASCONST = frozenset(dis.hasconst)
_BY_NAME = frozenset(dis.hasname) | frozenset(dis.haslocal) | frozenset(dis.hasfree)
_HASCOMPARE = frozenset(dis.hascompare)
_HASJUMP = frozenset(dis.hasjabs) | frozenset(dis.hasjrel)

# co_flags bits that are part of a code object's identity
CO_VARARGS = 0x04
CO_VARKEYWORDS = 0x08
CO_GENERATOR = 0x20
CO_COROUTINE = 0x80
CO_ITERABLE_COROUTINE = 0x100
CO_ASYNC_GENERATOR = 0x200
CO_OPTIMIZED = 0x01
CO_NEWLOCALS = 0x02
CO_FUTURE_ANNOTATIONS = 0x1000000
_FLAG_NAMES = (
    (CO_OPTIMIZED, 'OPTIMIZED'), (CO_NEWLOCALS, 'NEWLOCALS'), (CO_VARARGS, 'VARARGS'),
    (CO_VARKEYWORDS, 'VARKEYWORDS'), (CO_GENERATOR, 'GENERATOR'), (CO_COROUTINE, 'COROUTINE'),
    (CO_ITERABLE_COROUTINE, 'ITERABLE_COROUTINE'), (CO_ASYNC_GENERATOR, 'ASYNC_GENERATOR'),
    (CO_FUTURE_ANNOTATIONS, 'FUTURE_ANNOTATIONS'),
)


def const_key(value) -> str:
    """A text form of a constant in which different constants never look the same.

    ``repr`` alone is not enough: ``1 == 1.0 == True`` and ``0.0 == -0.0`` must
    stay distinguishable, and the iteration order of a frozenset is arbitrary.
    """
    if value is None or value is Ellipsis or isinstance(value, bool):
        return repr(value)
    if isinstance(value, (int, float, str, bytes)):
        return f'{type(value).__name__}:{value!r}'
    if isinstance(value, complex):
        return f'complex:({value.real!r},{value.imag!r})'
    if isinstance(value, tuple):
        return '(' + ', '.join(const_key(v) for v in value) + ')'
    if isinstance(value, frozenset):
        return 'frozenset{' + ', '.join(sorted(const_key(v) for v in value)) + '}'
    if isinstance(value, CodeType):
        return f'<code {value.co_name}>'
    return f'{type(value).__name__}:{value!r}'


def docstring_of(code: CodeType) -> Optional[str]:
    """The docstring of a function-like code object (it lives in co_consts[0])."""
    if not code.co_flags & CO_OPTIMIZED or code.co_name in ('<lambda>', '<listcomp>', '<setcomp>',
                                                            '<dictcomp>', '<genexpr>'):
        return None
    if code.co_consts and isinstance(code.co_consts[0], str):
        return code.co_consts[0]
    return None


def code_header(code: CodeType) -> str:
    """Everything about a code object, apart from its instructions, that must survive a round trip."""
    nargs = code.co_argcount + code.co_kwonlyargcount
    nargs += bool(code.co_flags & CO_VARARGS) + bool(code.co_flags & CO_VARKEYWORDS)
    flags = '|'.join(name for bit, name in _FLAG_NAMES if code.co_flags & bit) or '0'
    parts = [
        f'name={code.co_name!r}',
        f'args={list(code.co_varnames[:nargs])!r}',
        f'argcount={code.co_argcount}',
        f'posonly={code.co_posonlyargcount}',
        f'kwonly={code.co_kwonlyargcount}',
        f'flags={flags}',
        f'free={sorted(code.co_freevars)!r}',
        f'cell={sorted(code.co_cellvars)!r}',
    ]
    doc = docstring_of(code)
    if doc is not None:
        parts.append(f'doc={doc!r}')
    return ' '.join(parts)


def _arg_text(node: InstructionNode, children: Dict[InstructionNode, str]) -> str:
    op = node.opcode
    if op < dis.HAVE_ARGUMENT or op in _HASJUMP:
        return ''
    if op in _HASCONST:
        if isinstance(node.argval, CodeType):
            return children[node]
        return const_key(node.argval)
    if op in _BY_NAME or op in _HASCOMPARE:
        return str(node.argval)
    return str(node.arg)


def _opname(node: InstructionNode) -> str:
    return 'POP_JUMP_IF' if node.is_branch else node.opname


def dump_graph(graph: CodeGraph, path: str = '', offsets: bool = False,
               children: Optional[Dict[InstructionNode, str]] = None) -> List[str]:
    """Lines describing the instructions of a single graph (no header, no nested code)."""
    if children is None:
        children = _children(graph, path)
    width = len(str(max(len(graph.nodes) - 1, 0)))
    lines = []
    for node in graph.nodes:
        text = f'{node.index:>{width}} {_opname(node)}'
        arg = _arg_text(node, children)
        if arg:
            text += ' ' + arg
        edges = []
        out = node.out
        if NEXT in out and out[NEXT].index != node.index + 1:
            edges.append(f'->{out[NEXT].index}')
        if TRUE in out:
            edges.append(f'T->{out[TRUE].index}')
        if FALSE in out:
            edges.append(f'F->{out[FALSE].index}')
        if JUMP in out:
            edges.append(f'J->{out[JUMP].index}')
        if edges:
            text = f'{text:<34} ' + ' '.join(edges)
        if offsets and not node.is_pseudo:
            text = f'{text:<52} @{node.offset}' + (f' L{node.lineno}' if node.lineno else '')
        lines.append(text)
    return lines


def _children(graph: CodeGraph, path: str) -> Dict[InstructionNode, str]:
    """Label for every instruction that loads a code object, in canonical order.

    Each load gets its own label, also when two instructions load the very
    same object: whether the compiler shares one constant between two equal
    lambdas depends on whether they are on the same source line, which is
    layout and must not make graphs differ.
    """
    children: Dict[InstructionNode, str] = {}
    for node in graph.nodes:
        if node.opcode in _HASCONST and isinstance(node.argval, CodeType):
            children[node] = f'<code {path}#{len(children)} {node.argval.co_name}>'
    return children


def dump_code(code: CodeType, offsets: bool = False, _path: str = '', _label: Optional[str] = None) -> str:
    """The canonical dump of ``code`` and of every code object reachable from it."""
    graph = build_graph(code)
    children = _children(graph, _path)
    out = [f'=== {_label or "<code " + code.co_name + ">"} {code_header(code)}']
    out.extend('  ' + line for line in dump_graph(graph, _path, offsets, children))
    for n, (node, label) in enumerate(children.items()):
        out.append(dump_code(node.argval, offsets, f'{_path}{n}.', label))
    return '\n'.join(out)
