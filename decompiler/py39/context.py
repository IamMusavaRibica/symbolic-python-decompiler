"""Parsing state: frame blocks, block contexts and results.

The frame blocks mirror the ``fblock`` stack of CPython's ``compile.c``: every
construct that needs clean-up code when control leaves it early (``break``,
``continue``, ``return``) pushes one while its body is compiled.  Knowing which
frame blocks are open is what lets the decompiler recognise the clean-up code
the compiler inlined (``compiler_unwind_fblock``) and therefore tell a
``return`` inside a ``with`` inside a ``for`` from ordinary statements.
"""
from __future__ import annotations

import ast
from typing import FrozenSet, List, Optional, Set, Tuple

from decompiler.graph.node import InstructionNode

#: returned by handlers/parsers when control never continues past the construct
TERMINATED = object()


def follow(node: Optional[InstructionNode], *opnames: str) -> Optional[InstructionNode]:
    """Match a straight run of instructions by name; the node after it, or None."""
    for name in opnames:
        if node is None or node.opname != name or len(node.out) != 1 or node.next is None:
            return None
        node = node.next
    return node


class FBlock:
    """Base class of frame blocks."""
    is_loop = False

    def unwind(self, walker, node: InstructionNode, preserve_tos: bool):
        """Match the code that leaves this block early, starting at ``node``.

        Returns the node after it, None when ``node`` does not start that code,
        or TERMINATED when the clean-up code itself never completes.
        """
        raise NotImplementedError


class LoopBlock(FBlock):
    """WHILE_LOOP / FOR_LOOP."""
    is_loop = True

    def __init__(self, header: InstructionNode, nodes: Set[InstructionNode], is_for: bool):
        self.header = header  # 'continue' goes here
        self.nodes = nodes  # the natural loop
        self.is_for = is_for  # the loop keeps an iterator on the stack
        self.break_target: Optional[InstructionNode] = None  # found when the first 'break' is seen
        self.has_exit = True  # the loop can end without 'break' (False for 'while True')
        #: layout hint for 'while True': the node outside the loop that was placed last in the bytecode,
        #: if it lies behind the loop.  Code after the loop is laid out after everything in the loop.
        self.layout_exit: Optional[InstructionNode] = None
        #: [dead-code] offset of the code the loop's test leaves to (a while loop): the body ends before it
        self.exit_offset: Optional[int] = None
        #: layout hint: offset of the last jump back to the header
        self.end_offset = max(header.back_jumps, default=header.offset)

    def unwind(self, walker, node, preserve_tos):
        if not self.is_for:
            return node
        if preserve_tos:
            node = follow(node, 'ROT_TWO')
        return follow(node, 'POP_TOP')


class TryExceptBlock(FBlock):
    """TRY_EXCEPT: the body of a try statement that has except clauses."""

    def unwind(self, walker, node, preserve_tos):
        return follow(node, 'POP_BLOCK')


class FinallyTryBlock(FBlock):
    """FINALLY_TRY: the body of try/finally; leaving it runs an inlined copy of the finally body."""

    def __init__(self, entry: InstructionNode, terminal: Optional[InstructionNode]):
        self.entry = entry  # first instruction of the exception-path copy
        self.terminal = terminal  # the RERAISE that ends that copy (None: the body never completes)
        self.copy_nodes = set()  # the nodes of the inlined copy that was skipped last
        #: [dead-code] the body's normal exit is unreachable (its epilogue exists as dead code): every
        #: live way out of the body is a jump statement
        self.dead_normal_exit = None  # the dead epilogue's first node when so
        #: [dead-code] that epilogue lost its POP_BLOCK (the optimizer wiped it behind a return): it starts
        #: with the copy itself
        self.dead_exit_bare = False
        #: [dead-code] the nodes where the inlined copies skipped last ended (several RETURN_VALUEs
        #: when the optimizer duplicated the return into the copy's branches)
        self.copy_ends = set()

    def unwind(self, walker, node, preserve_tos):
        node = follow(node, 'POP_BLOCK')
        if node is None:
            return None
        return walker.skip_finally_copy(self, node)


class FinallyEndBlock(FBlock):
    """FINALLY_END: the finally body on the exception path (exception triple on the stack)."""

    def unwind(self, walker, node, preserve_tos):
        if preserve_tos:
            return follow(node, 'ROT_FOUR', 'POP_TOP', 'POP_TOP', 'POP_TOP', 'ROT_FOUR', 'POP_EXCEPT')
        return follow(node, 'POP_TOP', 'POP_TOP', 'POP_TOP', 'POP_EXCEPT')


class WithBlock(FBlock):
    """WITH / ASYNC_WITH."""

    def __init__(self, is_async: bool):
        self.is_async = is_async

    def unwind(self, walker, node, preserve_tos):
        node = follow(node, 'POP_BLOCK')
        if preserve_tos:
            node = follow(node, 'ROT_TWO')
        return self.call_exit(node)

    def call_exit(self, node):
        """``__exit__(None, None, None)`` and discarding its result."""
        if node is None or node.opname != 'LOAD_CONST' or node.argval is not None:
            return None
        node = follow(node, 'LOAD_CONST', 'DUP_TOP', 'DUP_TOP')
        if node is None or node.opname != 'CALL_FUNCTION' or node.arg != 3:
            return None
        node = node.next
        if self.is_async:
            node = follow(node, 'GET_AWAITABLE', 'LOAD_CONST', 'YIELD_FROM')
        return follow(node, 'POP_TOP')


class HandlerCleanupBlock(FBlock):
    """HANDLER_CLEANUP: the body of an except clause."""

    def __init__(self, name: Optional[str]):
        self.name = name  # 'except E as name'

    def unwind(self, walker, node, preserve_tos):
        if self.name:
            node = follow(node, 'POP_BLOCK')
        if preserve_tos:
            node = follow(node, 'ROT_FOUR')
        node = follow(node, 'POP_EXCEPT')
        if self.name:
            node = self.clear_name(node)
        return node

    def clear_name(self, node):
        """``name = None; del name``"""
        if node is None or node.opname != 'LOAD_CONST' or node.argval is not None:
            return None
        node = node.next
        if node is None or not node.opname.startswith('STORE_') or node.argval != self.name:
            return None
        node = node.next
        if node is None or not node.opname.startswith('DELETE_') or node.argval != self.name:
            return None
        return node.next


class Ctx:
    """Where a block lives: its dominance region, where it must stop and what encloses it."""
    __slots__ = ('start', 'stops', 'base', 'fblocks', 'bound', 'inside')

    def __init__(self, start: InstructionNode, stops: FrozenSet[InstructionNode], base: int,
                 fblocks: Tuple[FBlock, ...]):
        self.start = start  # the block may only contain nodes dominated by this one
        self.stops = stops  # nodes that end the block when reached
        self.base = base  # stack depth at statement boundaries
        self.fblocks = fblocks  # enclosing frame blocks, outermost first
        #: [dead-code] offset from which on code belongs to a later construct (an if body's else clause)
        self.bound: Optional[int] = None
        #: [dead-code] (loop depth, offset): the code of the innermost enclosing try body, handler or with
        #: body nested in the loop at that depth ends before that offset, so a node before it is inside the loop
        self.inside = None

    def evolve(self, start=None, stops=None, base=None, push=None, bound=None, inside=None) -> 'Ctx':
        ctx = Ctx(self.start if start is None else start,
                  self.stops if stops is None else stops,
                  self.base if base is None else base,
                  self.fblocks if push is None else self.fblocks + (push,))
        ctx.bound = self.bound if bound is None else bound  # [dead-code]
        ctx.inside = self.inside if inside is None else (ctx.loop_depth, inside)  # [dead-code]
        return ctx

    @property
    def loop_depth(self) -> int:
        return sum(1 for fb in self.fblocks if fb.is_loop)

    @property
    def loop(self) -> Optional[LoopBlock]:
        """The innermost enclosing loop."""
        for fb in reversed(self.fblocks):
            if fb.is_loop:
                return fb
        return None


class State:
    """The symbolic machine state while a block is being parsed."""
    __slots__ = ('stack', 'stmts', 'ctx', 'pending', 'implicit_end', 'extent')

    def __init__(self, stack: list, ctx: Ctx):
        self.stack = stack
        self.stmts: List[ast.stmt] = []
        self.ctx = ctx
        #: the block reached the implicit 'return None' that ends a module or class body
        self.implicit_end = False
        #: [dead-code] offset where the statement that just terminated ends in layout, when its
        #: construct knows (a try statement's closing RERAISE): its dead continuation hangs off its own code
        self.extent = None
        #: (target, value) pairs of a tuple assignment compiled without building the tuple
        self.pending: List[Tuple[ast.expr, ast.expr]] = []

    @property
    def depth(self) -> int:
        """Stack depth relative to the statement level of the block."""
        return len(self.stack) - self.ctx.base

    def push(self, value):
        self.stack.append(value)

    def pop(self):
        return self.stack.pop()

    def emit(self, stmt: ast.stmt):
        self.stmts.append(stmt)


class Block:
    """The result of parsing a block."""
    __slots__ = ('stmts', 'exit', 'stack', 'implicit')

    def __init__(self, stmts: List[ast.stmt], exit: Optional[InstructionNode], stack: list, implicit: bool = False):
        self.stmts = stmts
        #: the node at which the block stopped; None when control never falls out of it
        self.exit = exit
        self.stack = stack
        #: terminated by reaching the implicit end of a module or class body, not by a statement
        self.implicit = implicit

    @property
    def terminated(self) -> bool:
        return self.exit is None
