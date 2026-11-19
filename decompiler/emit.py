"""Writing the final tree as source text.

``ast.unparse`` does the real work.  It writes every statement on one line,
however long; long lines are broken after commas and binary operators inside
brackets, where Python allows a line break anywhere, and with a backslash after
a binary operator elsewhere.

(The layout also decides whether CPython 3.9 runs its peephole optimizer on a
code object: it skips one in which a single source line accounts for 255 or more
bytes of bytecode.  The round trip's comparison does not depend on that, see
:mod:`decompiler.graph.peephole`.)
"""
from __future__ import annotations

import ast
import io
import tokenize
from typing import Dict, Iterable, List, Tuple

#: lines longer than this are broken at the next opportunity
WIDTH = 100
_OPEN, _CLOSE = '([{', ')]}'
_BREAK_AFTER = {',', '+', '-', '*', '/', '//', '%', '**', '@', '|', '&', '^', '<<', '>>',
                '==', '!=', '<', '>', '<=', '>='}
_BREAK_AFTER_WORDS = {'and', 'or'}
_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def unparse(tree: ast.AST) -> str:
    return wrap_long_lines(ast.unparse(tree)) + '\n'


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
