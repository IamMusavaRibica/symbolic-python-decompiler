"""``try`` statements and ``with`` statements.

Shapes (compile.c: compiler_try_finally, compiler_try_except, compiler_with,
compiler_async_with)::

    try/finally                         try/except
        SETUP_FINALLY  F                    SETUP_FINALLY  H
        <body>                              <body>
        POP_BLOCK                           POP_BLOCK
        <finally body>                      -> else / end
        -> end                          H:  DUP_TOP, <type>, JUMP_IF_NOT_EXC_MATCH next
    F:  <finally body>                      POP_TOP, STORE name | POP_TOP, POP_TOP
        RERAISE                             [SETUP_FINALLY C]          (only with a name)
                                            <handler body>
                                            [POP_BLOCK] POP_EXCEPT [name = None; del name]
                                            -> end
                                        next: ... RERAISE

The finally body exists once per way of leaving the try body; the copy on the
exception path is the one that gets parsed, the others are only skipped over
(``Walker.skip_finally_copy``).
"""
from __future__ import annotations

import ast
from typing import List, Optional

from decompiler.errors import DecompileError
from decompiler.py39.context import (TERMINATED, Block, FinallyEndBlock, FinallyTryBlock, HandlerCleanupBlock,
                                     State, TryExceptBlock, WithBlock, follow)
from decompiler.py39.values import ExcMarker, IterValue, TargetMarker, WithExitMarker, is_plain


def exception_entry(stack: list) -> list:
    """The stack at the start of an exception handler: saved exception state, then the exception."""
    return stack + [ExcMarker('saved'), ExcMarker('saved'), ExcMarker('saved'),
                    ExcMarker('tb'), ExcMarker('value'), ExcMarker('type')]


class Handler:
    __slots__ = ('type', 'name', 'block', 'after')

    def __init__(self, type_, name, block: Block, after):
        self.type = type_
        self.name = name
        self.block = block
        self.after = after  # where control continues after the handler, None if it never does


class ExceptionMixin:
    @staticmethod
    def is_async_for(node) -> bool:
        """SETUP_FINALLY that guards the GET_ANEXT of an 'async for'."""
        return node.opname == 'SETUP_FINALLY' and node.jump is not None and node.jump.opname == 'END_ASYNC_FOR'

    def op_SETUP_FINALLY(self, node, st: State):
        if self.is_async_for(node):
            return self.parse_async_for(node, st)
        if st.depth != 0:
            raise DecompileError('a try statement starts in the middle of an expression', node)
        if node.jump.opname in ('DUP_TOP', 'POP_TOP'):
            return self.parse_try_except(node, st)
        return self.parse_try_finally(node, st)

    # -- try / finally -----------------------------------------------------------------
    def parse_try_finally(self, node, st: State):
        ctx, stack = st.ctx, st.stack
        entry = node.jump
        # the exception path first: it is the reference copy of the finally body
        exc_stack = exception_entry(stack)
        end_ctx = ctx.evolve(start=entry, base=len(exc_stack), push=FinallyEndBlock())
        final = self.parse_block(entry, exc_stack, end_ctx)
        if final.exit is not None and final.exit.opname != 'RERAISE':
            self.close_block(final, end_ctx)
        fb = FinallyTryBlock(entry, final.exit)

        body_ctx = ctx.evolve(start=node.next, push=fb)
        body = self.parse_block(node.next, stack, body_ctx)
        after = TERMINATED
        if body.exit is not None:
            if body.exit.opname != 'POP_BLOCK':
                raise DecompileError('unsupported end of a try body', body.exit)
            after = self.skip_finally_copy(fb, body.exit.next)
        st.emit(ast.Try(body=body.stmts or [ast.Pass()], handlers=[], orelse=[],
                        finalbody=final.stmts or [ast.Pass()]))
        return after

    # -- try / except --------------------------------------------------------------------
    def parse_try_except(self, node, st: State):
        ctx, stack = st.ctx, st.stack
        body_ctx = ctx.evolve(start=node.next, push=TryExceptBlock())
        body = self.parse_block(node.next, stack, body_ctx)
        after_body = None
        if body.exit is not None:
            if body.exit.opname != 'POP_BLOCK':
                raise DecompileError('unsupported end of a try body', body.exit)
            after_body = body.exit.next

        handlers = self.parse_handlers(node.jump, st)
        continuations: List = []
        for handler in handlers:
            if handler.after is not None and handler.after not in continuations:
                continuations.append(handler.after)

        orelse: List[ast.stmt] = []
        if after_body is not None and after_body in continuations:
            join = after_body
        else:
            # Handlers that go somewhere else than the others jump (continue/break/return);
            # the one place that is not a jump is where the statement ends.
            unresolved = [c for c in continuations if self.resolve_jump(c, ctx) is None]
            if len(unresolved) > 1:
                raise DecompileError('the handlers of a try statement never meet', node)
            if unresolved:
                join = unresolved[0]
                if after_body is not None and after_body is not join:
                    orelse_ctx = ctx.evolve(stops=ctx.stops | {join})
                    block = self.parse_branch(after_body, {body.exit}, stack, orelse_ctx)
                    self.close_block(block, orelse_ctx, accept=(join,))
                    orelse = block.stmts
            else:
                join = after_body
        for handler in handlers:
            if handler.after is not None and handler.after is not join:
                jump = self.resolve_jump(handler.after, ctx)
                if jump is None:
                    raise DecompileError('cannot tell where an except clause goes', handler.after)
                handler.block.stmts.extend(jump)

        st.emit(ast.Try(
            body=body.stmts or [ast.Pass()],
            handlers=[ast.ExceptHandler(type=h.type, name=h.name, body=h.block.stmts or [ast.Pass()])
                      for h in handlers],
            orelse=orelse, finalbody=[]))
        return join if join is not None else TERMINATED

    def parse_handlers(self, entry, st: State) -> List[Handler]:
        ctx, stack = st.ctx, st.stack
        exc_stack = exception_entry(stack)
        saved = exc_stack[:-3]
        handlers: List[Handler] = []
        current = entry
        while current.opname != 'RERAISE':
            exc_type = None
            following = None
            node = current
            if node.opname == 'DUP_TOP':
                # DUP_TOP, <type expression>, JUMP_IF_NOT_EXC_MATCH
                type_state = State(exc_stack + [exc_stack[-1]], ctx.evolve(start=node, base=len(exc_stack) + 1))
                node = node.next
                while node is not None and node.opname != 'JUMP_IF_NOT_EXC_MATCH':
                    result = self.dispatch(node, type_state)
                    if result is TERMINATED or type_state.stmts:
                        raise DecompileError('unsupported except clause', current)
                    node = node.next if result is None else result
                if node is None or len(type_state.stack) != len(exc_stack) + 2 or not is_plain(type_state.stack[-1]):
                    raise DecompileError('unsupported except clause', current)
                exc_type = type_state.stack[-1]
                following = node.jump
                node = node.next
            node = follow(node, 'POP_TOP')
            name = None
            if node is not None and node.opname.startswith('STORE_') and node.opname != 'STORE_ATTR' \
                    and node.opname != 'STORE_SUBSCR':
                name = node.argval
                node = node.next
            else:
                node = follow(node, 'POP_TOP')
            node = follow(node, 'POP_TOP')
            if node is None:
                raise DecompileError('unsupported except clause', current)
            cleanup = HandlerCleanupBlock(name)
            if name is not None:
                # the body is wrapped in a try/finally that unbinds the name
                end = cleanup.clear_name(node.jump) if node.opname == 'SETUP_FINALLY' else None
                if end is None or end.opname != 'RERAISE':
                    raise DecompileError('unsupported except clause', node)
                node = node.next
            handler_ctx = ctx.evolve(start=node, base=len(saved), push=cleanup)
            block = self.parse_block(node, saved, handler_ctx)
            after = None
            if block.exit is not None:
                after = cleanup.unwind(self, block.exit, False)
                if after is None:
                    raise DecompileError('unsupported end of an except clause', block.exit)
            handlers.append(Handler(exc_type, name, block, after))
            if following is None:
                break  # a bare 'except:' is always the last one
            current = following
        return handlers

    # -- with ------------------------------------------------------------------------------
    def op_SETUP_WITH(self, node, st: State):
        return self.parse_with(node, st, False)

    def op_BEFORE_ASYNC_WITH(self, node, st: State):
        setup = follow(node, 'BEFORE_ASYNC_WITH', 'GET_AWAITABLE', 'LOAD_CONST', 'YIELD_FROM')
        if setup is None or setup.opname != 'SETUP_ASYNC_WITH':
            raise DecompileError('unsupported async with', node)
        return self.parse_with(setup, st, True)

    def parse_with(self, node, st: State, is_async: bool):
        ctx = st.ctx
        context_expr = self.pop_expr(st, node)
        if st.depth != 0:
            raise DecompileError('a with statement starts in the middle of an expression', node)
        stack = st.stack
        inner = stack + [WithExitMarker()]
        # the result of __enter__ is stored or dropped
        start = node.next
        target = None
        if start.opname == 'POP_TOP':
            start = start.next
        else:
            marker = TargetMarker('with')
            target_state = State(inner + [marker], ctx.evolve(base=len(inner)))
            start = self.run_until(start, target_state, lambda: marker.target is not None)
            if target_state.stmts or len(target_state.stack) != len(inner):
                raise DecompileError('unsupported with target', node)
            target = marker.target

        fb = WithBlock(is_async)
        body_ctx = ctx.evolve(start=start, base=len(inner), push=fb)
        body = self.parse_block(start, inner, body_ctx)

        # exception path: WITH_EXCEPT_START, test; true -> swallow the exception and go on
        test = follow(node.jump, 'WITH_EXCEPT_START')
        if is_async:
            test = follow(test, 'GET_AWAITABLE', 'LOAD_CONST', 'YIELD_FROM')
        if test is None or not test.is_branch or test.false is None or test.false.opname != 'RERAISE':
            raise DecompileError('unsupported with statement', node)
        join = follow(test.true, 'POP_TOP', 'POP_TOP', 'POP_TOP', 'POP_EXCEPT', 'POP_TOP')
        if join is None:
            raise DecompileError('unsupported with statement', node)
        if body.exit is not None and fb.unwind(self, body.exit, False) is not join:
            self.close_block(body, body_ctx)

        item = ast.withitem(context_expr=context_expr, optional_vars=target)
        cls = ast.AsyncWith if is_async else ast.With
        st.emit(cls(items=[item], body=body.stmts or [ast.Pass()]))
        return join
