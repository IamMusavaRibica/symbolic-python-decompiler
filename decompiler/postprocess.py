"""Turn the raw statement tree produced by the walker into a tree that can be unparsed.

Two jobs:

* *finalising*: helper node classes are replaced by the real ``ast`` classes
  and anything that must not survive (pseudo expressions) is reported;
* *tidying*: rewrites that make the source look like something a person would
  write.  Every one of them must leave the compiled graph unchanged, e.g.
  ``with a:`` directly containing ``with b:`` is the same code as
  ``with a, b:``.
"""
from __future__ import annotations

import ast
from typing import List

from decompiler.errors import DecompileError
from decompiler.py39.definitions import ComprehensionElement
from decompiler.py39.statements import ChainedTarget
from decompiler.py39.values import ConstSet, FStringPart, Pseudo, const_to_ast

LOOPS = (ast.For, ast.AsyncFor, ast.While)


def finalize(tree: ast.AST) -> ast.AST:
    return _walk(tree)


def _walk(node: ast.AST) -> ast.AST:
    if isinstance(node, (Pseudo, ComprehensionElement)):
        raise DecompileError(f'internal value {type(node).__name__} left in the result')
    _expand_constant_display(node)
    for field, value in ast.iter_fields(node):
        if isinstance(value, list):
            setattr(node, field, [_walk(v) if isinstance(v, ast.AST) else v for v in value])
        elif isinstance(value, ast.AST):
            setattr(node, field, _walk(value))
    return _fix(node)


def _expand_constant_display(node: ast.AST) -> None:
    """``[1, 2, 3]`` is compiled as ``[*(1, 2, 3)]`` with a constant tuple (same for sets)."""
    if not isinstance(node, (ast.List, ast.Set)) or len(node.elts) != 1 or not isinstance(node.elts[0], ast.Starred):
        return
    inner = node.elts[0].value
    if isinstance(node, ast.List) and isinstance(inner, ast.Constant) and isinstance(inner.value, tuple) \
            and len(inner.value) >= 3:
        node.elts = [const_to_ast(v, True) for v in inner.value]
    elif isinstance(node, ast.Set) and isinstance(inner, ConstSet) and len(inner.elts) >= 3:
        node.elts = list(inner.elts)


def _fix(node: ast.AST) -> ast.AST:
    if isinstance(node, ChainedTarget):
        if not isinstance(node.target, ast.Name):
            raise DecompileError('assignment expression with a target that is not a name')
        return ast.NamedExpr(target=node.target, value=node.value)
    if isinstance(node, FStringPart):
        return ast.JoinedStr(values=node.values)
    if isinstance(node, ConstSet):
        return ast.Set(elts=node.elts)
    if isinstance(node, ast.Constant) and isinstance(node.value, tuple):
        # written as a display so that every element is spelled the way source code spells it
        return _walk(const_to_ast(node.value, True))
    if isinstance(node, (ast.With, ast.AsyncWith)):
        # with a: with b: ...  ==  with a, b: ...
        if len(node.body) == 1 and type(node.body[0]) is type(node):
            inner = node.body[0]
            node.items = node.items + inner.items
            node.body = inner.body
    elif isinstance(node, ast.Try):
        # try: (try: ... except ...) finally: ...  ==  try: ... except ... finally: ...
        if node.finalbody and not node.handlers and not node.orelse and len(node.body) == 1 \
                and isinstance(node.body[0], ast.Try) and not node.body[0].finalbody:
            inner = node.body[0]
            node.body, node.handlers, node.orelse = inner.body, inner.handlers, inner.orelse
    elif isinstance(node, LOOPS):
        node.body = _strip_continue(node.body) or [ast.Pass()]
    return node


def _strip_continue(body: List[ast.stmt]) -> List[ast.stmt]:
    """Drop a ``continue`` that is the last thing a loop body does anyway."""
    if not body:
        return body
    last = body[-1]
    if isinstance(last, ast.Continue):
        return body[:-1]
    if isinstance(last, ast.If):
        stripped = _strip_continue(last.body)
        if not stripped:
            if not last.orelse:
                # 'if test: continue' as the last statement still has to evaluate the test
                stripped = [ast.Pass()]
            else:
                return body
        last.body = stripped
        if last.orelse:
            last.orelse = _strip_continue(last.orelse)
    return body
