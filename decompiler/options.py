"""Switches that change what the decompiler does.  Set them before building graphs.

``dead_code`` -- also decompile unreachable code.  [dead-code]
    The graph normally holds reachable instructions only.  With this on, the
    instruction after a ``return``, ``raise``, ``break`` or ``continue`` is
    followed as well (an edge labelled ``dead``), unreachable code becomes part
    of the graph (and of the comparison), and the walker continues a block that
    ended in a jump statement with the dead statements that follow it.
    Everything that exists only for this is marked ``[dead-code]`` in the source.
    The environment variable ``DECOMPILER_DEAD_CODE=1`` turns it on as well
    (that is how the corpus runner's worker processes get it).
"""
import os

dead_code: bool = os.environ.get('DECOMPILER_DEAD_CODE', '') not in ('', '0')
