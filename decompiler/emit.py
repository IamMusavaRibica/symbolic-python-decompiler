"""Writing the final tree as source text.

``ast.unparse`` does the real work.  It writes every statement on one line,
however long, and that is more than a matter of taste: CPython 3.9 does not run
its peephole optimizer on a code object in which a single source line accounts
for 255 or more bytes of bytecode (``peephole.c``: "Don't optimize if lnotab
contains instruction pointer delta larger than +255").  A module-level table
written on one line would therefore recompile to different (unoptimized) code
than the original, whose author spread it over many lines.  So long lines are
broken after commas inside brackets, where Python allows a line break anywhere.
"""
from __future__ import annotations

import ast
import io
import tokenize
from typing import Dict, List

#: lines longer than this are broken at the next opportunity
WIDTH = 100
_OPEN, _CLOSE = '([{', ')]}'


def unparse(tree: ast.AST) -> str:
    return wrap_long_lines(ast.unparse(tree)) + '\n'


def wrap_long_lines(source: str, width: int = WIDTH) -> str:
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
            if row != tok.start[0] or len(lines[row - 1]) <= width:
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
