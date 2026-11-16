"""Writing the final tree as source text.

``ast.unparse`` does the real work.  It writes every statement on one line,
however long, and that is more than a matter of taste: CPython 3.9 does not run
its peephole optimizer on a code object in which a single source line accounts
for 255 or more bytes of bytecode (``peephole.c``: "Don't optimize if lnotab
contains instruction pointer delta larger than +255").  A module-level table
written on one line would therefore recompile to different (unoptimized) code
than the original, whose author spread it over many lines.  So long lines are
broken after commas inside brackets, where Python allows a line break anywhere.

The reverse happens too: a code object that *was* compiled from such a long
line never went through the optimizer, and its statements must stay on one
line for the same to happen again.  The decompiler marks them
(``keep_on_one_line``).
"""
from __future__ import annotations

import ast
import io
import tokenize
from types import CodeType
from typing import Dict, Iterable, List, Set

#: lines longer than this are broken at the next opportunity
WIDTH = 100
_OPEN, _CLOSE = '([{', ')]}'
_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def peephole_skipped(code: CodeType) -> bool:
    """True when the peephole optimizer left ``code`` alone because of a very long source line."""
    table = code.co_lnotab
    return any(table[i] == 255 for i in range(0, len(table), 2))


def keep_on_one_line(stmts: Iterable[ast.stmt]) -> None:
    """Mark the statements of one code object (not those of nested functions) as not to be wrapped."""
    for stmt in stmts:
        stmt.keep_on_one_line = True
        if isinstance(stmt, _NESTED_SCOPES):
            continue
        for field in ('body', 'orelse', 'finalbody'):
            keep_on_one_line(getattr(stmt, field, ()))
        for handler in getattr(stmt, 'handlers', ()):
            keep_on_one_line(handler.body)


class _Unparser(ast._Unparser):
    """ast.unparse that remembers on which output lines the marked statements start."""

    def __init__(self, **kwargs):  # (the base class creates helper instances of its own type)
        super().__init__(**kwargs)
        self.line = 1
        self.kept: Set[int] = set()

    def write(self, text):
        self.line += text.count('\n')
        super().write(text)

    def traverse(self, node):
        if isinstance(node, ast.stmt) and getattr(node, 'keep_on_one_line', False):
            # the statement starts on the next line (the first one on the first line);
            # a definition has its decorators first
            first = self.line + 1 if self._source else 1
            self.kept.update(range(first, first + len(getattr(node, 'decorator_list', ())) + 1))
        return super().traverse(node)


def unparse(tree: ast.AST) -> str:
    try:
        unparser = _Unparser()
        source = unparser.visit(tree)
        kept = unparser.kept
    except AttributeError:  # the private base class changed
        source, kept = ast.unparse(tree), set()
    return wrap_long_lines(source, kept) + '\n'


def wrap_long_lines(source: str, keep: Iterable[int] = (), width: int = WIDTH) -> str:
    """Break lines longer than ``width`` after commas inside brackets, except the lines in ``keep``."""
    keep = set(keep)
    lines = source.split('\n')
    if all(len(line) <= width for line in lines):
        return source
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return source
    # row -> columns after which the line is broken
    breaks: Dict[int, List[int]] = {}
    depth = 0
    for tok in tokens:
        if tok.type != tokenize.OP:
            continue
        if tok.string in _OPEN:
            depth += 1
        elif tok.string in _CLOSE:
            depth -= 1
        elif tok.string == ',' and depth > 0:
            row, col = tok.end
            if row in keep or row != tok.start[0] or len(lines[row - 1]) <= width:
                continue
            cuts = breaks.setdefault(row, [])
            if col - (cuts[-1] if cuts else 0) >= width - 20:
                cuts.append(col)
    for row, cuts in breaks.items():
        line = lines[row - 1]
        indent = ' ' * (len(line) - len(line.lstrip(' ')) + 4)
        pieces = []
        start = 0
        for cut in cuts:
            pieces.append(line[start:cut])
            start = cut
        rest = line[start:]
        if rest.strip():
            pieces.append(rest)
        else:
            pieces[-1] += rest
        lines[row - 1] = ('\n' + indent).join(p.strip(' ') if i else p.rstrip(' ') for i, p in enumerate(pieces))
    return '\n'.join(lines)
