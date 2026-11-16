"""The round trip that decides whether a decompilation is correct.

    source --compile--> code --decompile--> source' --compile--> code'

``code`` and ``code'`` must have the same instruction graph (recursively for
nested code objects).  The same compiler is used for both compilations, so the
check works for the stock interpreter as well as for the peephole-less one.
"""
from __future__ import annotations

import traceback
from types import CodeType
from typing import Callable, NamedTuple, Optional

from decompiler.graph.compare import compare_code
from decompiler.utils.bytecode import compile_flags_for, compile_source, compile_without_peephole

STOCK = 'stock'
NOPEEPHOLE = 'nopeephole'

COMPILERS = {
    STOCK: lambda source, flags=0: compile_source(source, '<source>', flags),
    NOPEEPHOLE: lambda source, flags=0: compile_without_peephole(source, flags),
}


class RoundTrip(NamedTuple):
    ok: bool
    #: 'ok', or the stage that failed: 'compile', 'decompile', 'recompile', 'compare'
    stage: str
    #: human readable explanation of the failure ('' when ok)
    detail: str
    #: the decompiled source, when the decompiler produced one
    decompiled: Optional[str]

    def __bool__(self):
        return self.ok


def verify_code(code: CodeType, compiler: Callable[..., CodeType] = COMPILERS[STOCK],
                peephole: Optional[bool] = None) -> RoundTrip:
    """Decompile ``code``, recompile the result with ``compiler`` and compare the graphs.

    ``peephole`` says whether ``compiler`` runs the peephole optimizer (guessed when None).
    """
    from decompiler import decompile
    try:
        decompiled = decompile(code, peephole)
    except RecursionError:
        return RoundTrip(False, 'decompile', 'RecursionError', None)
    except Exception:
        return RoundTrip(False, 'decompile', traceback.format_exc(), None)
    try:
        recompiled = compiler(decompiled, compile_flags_for(code))
    except Exception as e:
        return RoundTrip(False, 'recompile', f'{type(e).__name__}: {e}', decompiled)
    comparison = compare_code(code, recompiled)
    if not comparison:
        return RoundTrip(False, 'compare', comparison.diff, decompiled)
    return RoundTrip(True, 'ok', '', decompiled)


def verify_source(source: str, mode: str = STOCK, flags: int = 0) -> RoundTrip:
    """Compile ``source`` with the compiler named ``mode`` and run the round trip on the result."""
    compiler = COMPILERS[mode]
    try:
        code = compiler(source, flags)
    except Exception as e:
        return RoundTrip(False, 'compile', f'{type(e).__name__}: {e}', None)
    return verify_code(code, compiler, mode == STOCK)
