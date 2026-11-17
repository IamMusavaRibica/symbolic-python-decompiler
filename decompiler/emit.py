"""Writing the final tree as source text.

``ast.unparse`` does the real work.  It writes every statement on one line,
however long, and that is more than a matter of taste: CPython 3.9 does not run
its peephole optimizer on a code object in which a single source line accounts
for 255 or more bytes of bytecode (``peephole.c``: "Don't optimize if lnotab
contains instruction pointer delta larger than +255").  A module-level table
written on one line would therefore recompile to different (unoptimized) code
than the original, whose author spread it over many lines.  So long lines are
broken after commas and binary operators inside brackets, where Python allows a
line break anywhere, and with a backslash after a binary operator elsewhere.

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
from typing import Dict, Iterable, List, Set, Tuple

#: lines longer than this are broken at the next opportunity
WIDTH = 100
_OPEN, _CLOSE = '([{', ')]}'
_BREAK_AFTER = {',', '+', '-', '*', '/', '//', '%', '**', '@', '|', '&', '^', '<<', '>>',
                '==', '!=', '<', '>', '<=', '>='}
_BREAK_AFTER_WORDS = {'and', 'or'}
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
    """Break lines longer than ``width`` after commas and operators, except the lines in ``keep``.

    Inside brackets a plain line break is used, elsewhere a backslash.
    """
    keep = set(keep)
    lines = source.split('\n')
    if all(len(line) <= width for line in lines):
        return source
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return source
    # row -> (column after which the line is broken, whether a backslash is needed)
    breaks: Dict[int, List[Tuple[int, bool]]] = {}
    depth = 0
    for tok in tokens:
        if tok.type == tokenize.OP and tok.string in _OPEN:
            depth += 1
        elif tok.type == tokenize.OP and tok.string in _CLOSE:
            depth -= 1
        elif (tok.type == tokenize.OP and tok.string in _BREAK_AFTER and (depth > 0 or tok.string != ',')) \
                or (tok.type == tokenize.NAME and tok.string in _BREAK_AFTER_WORDS):
            row, col = tok.end
            if row in keep or row != tok.start[0] or len(lines[row - 1]) <= width:
                continue
            cuts = breaks.setdefault(row, [])
            if col - (cuts[-1][0] if cuts else 0) >= width - 20:
                cuts.append((col, depth == 0))
    for row, cuts in breaks.items():
        line = lines[row - 1]
        indent = ' ' * (len(line) - len(line.lstrip(' ')) + 4)
        pieces = []
        start = 0
        for col, backslash in cuts:
            pieces.append(line[start:col].rstrip(' ') + (' \\' if backslash else ''))
            start = col
        rest = line[start:]
        if rest.strip():
            pieces.append(rest)
        else:
            pieces[-1] = pieces[-1].rstrip(' \\') + rest
        lines[row - 1] = ('\n' + indent).join(p.strip(' ') if i else p for i, p in enumerate(pieces))
    return '\n'.join(lines)
