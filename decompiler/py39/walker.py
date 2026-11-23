"""The structural parser for one code object.

It walks the instruction graph from the entry while executing the value stack
symbolically (stack entries are AST expressions or markers, see ``values``).
Straight-line instructions are handled by ``op_<OPNAME>`` methods; control
instructions hand over to the parsers of the construct they start.  The
methods live in mixins, one per family of constructs:

=================  ==========================================================
``expressions``    everything that only computes values
``statements``     stores, deletes, imports, simple statements
``control``        conditions, ``if``, conditional expressions, loops
``exceptions``     ``try``, ``with``, unwinding (``break``/``continue``/``return``)
``definitions``    functions, lambdas, classes, comprehensions
=================  ==========================================================

Supporting a new opcode means adding an ``op_`` method to the fitting mixin.

Blocks
------
``parse_block`` turns the code starting at a node into a list of statements
and stops at the block's *boundary*, returning the node where it stopped.  A
block ends before a node when

* the node is one of the context's stop nodes (e.g. the head of the loop whose
  body is being parsed);
* the node is not dominated by the block's first node, i.e. control can get
  there without passing through the block (the join after an ``if``, ...);
* at statement level, the node is an instruction that belongs to the enclosing
  construct (``POP_BLOCK`` ending a ``try`` body, ...) or starts unwinding one.

Whoever asked for the block decides what the exit means: a join, the end of a
body, or a ``break``/``continue``/``return`` (see ``resolve_jump``).
"""
from __future__ import annotations

import ast

from loguru import logger
from types import CodeType
from typing import List, Optional

from decompiler.errors import DecompileError, SyntheticLoop
from decompiler.graph.analysis import GraphAnalysis
from decompiler.graph.builder import build_graph
from decompiler.graph.node import FALSE, JUMP, TRUE, InstructionNode, OP_SPIN
from decompiler.py39.context import (TERMINATED, Block, Ctx, FinallyTryBlock, LoopBlock, State)
from decompiler.py39.control import ControlMixin
from decompiler.py39.definitions import DefinitionMixin
from decompiler.py39.exceptions import ExceptionMixin
from decompiler.py39.expressions import ExpressionMixin
from decompiler.py39.statements import StatementMixin
from decompiler.py39.values import Accumulator, IterValue, is_plain

#: instructions that end a block when met at statement level
SETUP_OPS = frozenset({'SETUP_FINALLY', 'SETUP_WITH', 'SETUP_ASYNC_WITH'})  # [dead-code]
BLOCK_END_OPS = frozenset({'POP_BLOCK', 'POP_EXCEPT', 'RERAISE', 'END_ASYNC_FOR', 'WITH_EXCEPT_START', 'POP_TOP'})
#: first instruction of the clean-up code of 'return <value>' inside a frame block
RETURN_UNWIND_OPS = frozenset({'POP_BLOCK', 'ROT_TWO', 'ROT_FOUR'})
#: first instruction of a comprehension -> what it builds
ACCUMULATORS = {'BUILD_LIST': 'list', 'BUILD_SET': 'set', 'BUILD_MAP': 'dict'}


class Walker(ExpressionMixin, StatementMixin, ControlMixin, ExceptionMixin, DefinitionMixin):
    def __init__(self, decompiler, code: CodeType, kind: str):
        self.decompiler = decompiler
        self.code = code
        #: 'module', 'function', 'lambda', 'class' or 'comprehension'
        self.kind = kind
        self.graph = build_graph(code, decompiler.dead_code)
        self.analysis = GraphAnalysis(self.graph)
        # [dead-code] dead regions already attached to a block; the RERAISE that ends a chain of
        # handlers is never a statement (dead when a bare 'except:' precedes it)
        self._dead_claimed = {entry for _, entry in self.graph.dead_entries if entry.opname == 'RERAISE'}
        self._dead_no_claim = False  # [dead-code] the block ended at the loop's own back jump
        # [dead-code] the function's implicit 'return None' when it is unreachable: it belongs to the
        # function body, not to whatever nested block ends just before it
        self.implicit_end = None
        #: [dead-code] the final 'LOAD_CONST None' of the code object (the implicit return), live or not
        self.function_end = None
        size = len(code.co_code)
        last, before = self.graph.node_at(size - 2), self.graph.node_at(size - 4)
        if last is not None and before is not None and last.opname == 'RETURN_VALUE' \
                and before.opname == 'LOAD_CONST' and before.argval is None and before.next is last                 and len(last.preds) == 1:  # (not the 'None' of a 'return x or None')
            self.function_end = before
            if before.dead:
                self.implicit_end = before
        #: the code has variable annotations ('x: int' at module or class level)
        self.has_annotations = any(n.opname == 'SETUP_ANNOTATIONS' for n in self.graph.nodes)

    # -- entry point ---------------------------------------------------------
    def run(self) -> List[ast.stmt]:
        first = self.graph.first
        if first is None:
            return []
        stack = []
        if self.kind == 'comprehension' and first.opname in ACCUMULATORS and first.arg == 0:
            # the list/set/dict being filled stays on the stack for the whole comprehension
            stack.append(Accumulator(ACCUMULATORS[first.opname]))
            first = first.next
        ctx = Ctx(self.graph.entry, frozenset(), len(stack), ())
        try:
            try:
                block = self.parse_block(first, stack, ctx)
            except SyntheticLoop as missing:
                # not for the walker of an enclosing scope to act on
                raise DecompileError('a loop without back edge could not be placed', missing.node) from missing
            if not block.terminated:
                raise DecompileError('statements end without a return', block.exit)
        except DecompileError as e:
            if e.code_name is None:
                e.code_name = self.code.co_name
            raise
        return block.stmts

    # -- blocks --------------------------------------------------------------
    def parse_block(self, start: InstructionNode, stack: list, ctx: Ctx, entering: bool = False) -> Block:
        """Parse statements from ``start`` until the block's boundary.

        ``entering`` says that ``start`` is the head of the loop this block is
        the body of, so reaching it the first time is neither a stop nor a new
        loop.
        """
        st = State(list(stack), ctx)
        node = start
        first = entering
        statement = (node, list(st.stack), 0)  # where the current statement started
        last = None
        while True:
            if node is None:
                if self.graph.dead_code and last is not None and last.dead:
                    # [dead-code] unreachable code at the very end of the code object: the compiler
                    # adds no implicit return behind a block that already returned
                    return Block(st.stmts, None, st.stack, True)
                raise DecompileError('control flow runs off the end of the code')
            last = node
            # [dead-code] a POP_BLOCK followed (after the clean-up code) by unreachable code is not the
            # normal end of a try body but a jump statement: the normal end is never followed by one
            jump_copy = self.graph.dead_code and not first and st.depth == 0 \
                and node.opname in ('POP_BLOCK', 'POP_EXCEPT') and self.copy_has_dead_continuation(node, ctx)
            if not jump_copy and self.at_boundary(node, st, first):
                return Block(st.stmts, node, st.stack)
            if st.depth == 0:
                statement = (node, list(st.stack), len(st.stmts))
            if node.dead:
                self._dead_claimed.add(node)  # [dead-code] reached by flow: not a continuation any more
            try:
                if jump_copy:
                    jump = self.resolve_jump(node, ctx)
                    if jump is None:
                        raise DecompileError('cannot tell where control goes from here', node)
                    st.stmts.extend(jump)
                    result = TERMINATED
                else:
                    result = self.dispatch(node, st, first)
            except SyntheticLoop as missing:
                if missing.depth != len(ctx.fblocks):
                    raise
                head, saved_stack, count = statement
                st.stack[:] = saved_stack
                del st.stmts[count:]
                try:
                    result = self.parse_synthetic_loop(head, st, missing.exit)
                except DecompileError:
                    raise missing  # an enclosing statement may be the one to wrap
            first = False
            if result is TERMINATED:
                if self.graph.dead_code and not self._dead_no_claim:  # [dead-code]
                    continuation = self.dead_continuation(statement[0], ctx)
                    while continuation is not None and continuation.dead and continuation.jump is statement[0]                             and continuation.opname in ('JUMP_ABSOLUTE', 'JUMP_FORWARD'):
                        # a loop's own back jump, dead because the body's last jump was threaded
                        # past it: what follows it is the code after the loop
                        continuation = continuation.dead_next
                        if continuation is not None:
                            self._dead_claimed.add(continuation)
                    fragment = self.return_fragment(continuation)
                    while fragment is not None:
                        # the rest of a return statement whose finally copy jumped (the value's
                        # clean-up and the RETURN_VALUE): what follows it is the continuation
                        continuation = fragment.dead_next
                        if continuation is None or continuation in self._dead_claimed:
                            continuation = None
                            break
                        self._dead_claimed.add(continuation)
                        fragment = self.return_fragment(continuation)
                        if continuation.opname in ('WITH_EXCEPT_START', 'DUP_TOP'):
                            continuation = None  # an exception handler of the copy, not statements
                    if continuation is not None and continuation.opname in BLOCK_END_OPS:
                        # the epilogue of the construct the jump statement left: the block stops
                        # there, and the construct reads where the epilogue goes (at the top level
                        # there is no such construct: the epilogue was nobody's continuation)
                        if ctx.fblocks:
                            return Block(st.stmts, continuation, st.stack, st.implicit_end)
                        continuation = None
                    if continuation is not None:
                        # parsed tentatively: a fragment of unreachable clean-up code that is not a
                        # statement continuation is dropped rather than failing the whole function
                        try:
                            tail = self.parse_block(continuation, statement[1], ctx)
                        except DecompileError as e:
                            logger.debug('dead continuation at {} dropped: {}', continuation, e)
                            tail = None
                        if tail is not None and tail.exit is self.implicit_end and ctx.start is not self.graph.entry:
                            tail = None
                        if tail is not None:
                            st.stmts.extend(tail.stmts)
                            return Block(st.stmts, tail.exit, tail.stack, tail.implicit)
                self._dead_no_claim = False
                return Block(st.stmts, None, st.stack, st.implicit_end)
            node = node.next if result is None else result

    def copy_has_dead_continuation(self, node: InstructionNode, ctx: Ctx) -> bool:
        """[dead-code] The clean-up code starting at ``node`` is followed by unreachable code."""
        anchors = {anchor for anchor, entry in self.graph.dead_entries if entry.opname != 'RERAISE'}
        for fb in reversed(ctx.fblocks):
            after = fb.unwind(self, node, False)  # (a loop's: the POP_TOP of a 'break' out of a for loop)
            if after is None or after is TERMINATED:
                return False
            consumed = set(fb.copy_nodes) if isinstance(fb, FinallyTryBlock) else set()
            current = node
            for _ in range(200):
                if current is None or current is after:
                    break
                consumed.add(current)
                current = current.next
            # The dead code follows the jump at the end of the clean-up code: the anchor is the
            # node whose fall-through was threaded through that jump.  Clean-up code contains no
            # other jump: one before the end means this is the normal end of a construct (a with
            # statement's exit call jumping over its handler) and not part of a jump statement.
            if any(n.next is after for n in consumed & anchors):
                return True
            # (a jump inside a finally body's copy, a break in a loop of its own, is not that)
            if any(n.threaded and n.next is after for n in consumed) or fb.is_loop:
                return False
            node = after
        return False

    def leaves_by_return(self, node: InstructionNode, ctx: Ctx) -> bool:
        """[dead-code] The clean-up code at ``node`` is that of a jump statement: some enclosing try
        statement's normal exit is dead, and the chain runs through the frame blocks without a jump
        in between (a body that falls through jumps over its exception handler)."""
        if not any(isinstance(fb, FinallyTryBlock) and fb.dead_normal_exit is not None for fb in ctx.fblocks):
            return False
        for fb in reversed(ctx.fblocks):
            if fb.is_loop:
                break
            after = fb.unwind(self, node, False)
            if after is None or after is TERMINATED:
                return False
            consumed = set(fb.copy_nodes) if isinstance(fb, FinallyTryBlock) else set()
            current = node
            for _ in range(200):
                if current is None or current is after:
                    break
                consumed.add(current)
                current = current.next
            if any(n.threaded and n.next is after for n in consumed):
                return False
            node = after
        return True

    def handler_epilogue(self, start: InstructionNode, end: Optional[InstructionNode]):
        """[dead-code] Where the dead epilogue (POP_EXCEPT and a jump) of an except clause that ended
        in a jump statement leads, when that is live code (the clause then has no continuation);
        None otherwise.  The clause's code starts at ``start`` and ends before ``end``."""
        for anchor, entry in self.graph.dead_entries:
            if entry.opname == 'POP_EXCEPT' and anchor.offset >= start.offset                     and (end is None or anchor.offset < end.offset) and self.analysis.dominates(start, anchor):
                after = self.follow_dead_jumps(entry.next)
                if after is not None and after is not TERMINATED and not after.dead:
                    return after
        return None

    def handler_jumped(self, start: InstructionNode, ctx: Ctx) -> bool:
        """[dead-code] The clean-up chain at ``start`` (an except clause's continuation that resolves
        to a jump statement, just unwound) is followed by the dead epilogue of a handler that jumped:
        a POP_EXCEPT leading to live code.  Told apart from a handler that fell through into the
        loop's own clean-up, which has no such epilogue."""
        chain = set()
        node = start
        for _ in range(200):
            if node is None or node.dead or node in chain:
                break
            chain.add(node)
            if node.threaded or any(fb.is_loop and node is fb.header for fb in ctx.fblocks):
                break
            node = node.next
        for fb in ctx.fblocks:
            if isinstance(fb, FinallyTryBlock):
                chain |= fb.copy_nodes | fb.copy_ends
        anchored = {}
        for anchor, entry in self.graph.dead_entries:
            anchored.setdefault(anchor, []).append(entry)
        work = [anchor for anchor in anchored if anchor in chain]
        seen = set()
        while work:
            for entry in anchored[work.pop()]:
                if entry in seen:
                    continue
                seen.add(entry)
                if entry.opname == 'POP_EXCEPT':
                    after = self.follow_dead_jumps(entry.next)
                    if after is not None and after is not TERMINATED and not after.dead:
                        return True
                if entry in anchored:
                    work.append(entry)  # a dead jump anchors the region behind it
        return False

    @staticmethod
    def follow_dead_jumps(node):
        """[dead-code] A dead unconditional jump stands for where it goes."""
        while node is not None and node is not TERMINATED and node.dead and node.opname in ('JUMP_ABSOLUTE', 'JUMP_FORWARD'):
            node = node.jump
        return node

    def _dead_jump(self, node, st: State):
        """[dead-code] An unconditional jump in unreachable code: 'break', 'continue', the loop's own
        back jump, or a jump the compiler put there for structure (over an exception handler)."""
        if st.depth != 0:
            raise DecompileError('a jump in the middle of an expression', node)
        target = node.jump
        loop = st.ctx.loop
        if loop is not None:
            if target is loop.header:
                if node.offset >= max(loop.header.back_jumps, default=-1):
                    self._dead_no_claim = True  # the loop's own back jump: the body ends here
                    return TERMINATED
                st.emit(ast.Continue())
                return TERMINATED
            # a jump out of the loop is a 'break'; one to dead code that the loop region contains
            # (over an exception handler, say) is just structure
            if target is loop.break_target or not self.analysis.dominates(loop.header, target) \
                    or (not target.dead and target not in loop.nodes) \
                    or (target.dead and (target is self.implicit_end or target.offset > loop.end_offset)):
                st.emit(ast.Break())
                return TERMINATED
        return target

    op_JUMP_ABSOLUTE = op_JUMP_FORWARD = _dead_jump

    def ends_function(self, first: InstructionNode) -> bool:
        """[dead-code] The statement starting at ``first`` is followed by the function's unreachable
        implicit 'return None': it is the last statement and every path through it ends in a jump."""
        return self.graph.dead_code and self.implicit_end is not None and any(
            entry is self.implicit_end and self.analysis.dominates(first, anchor)
            for anchor, entry in self.graph.dead_entries)

    def body_jumps_past(self, first: InstructionNode, second: InstructionNode) -> bool:
        """[dead-code] A jump statement inside the code at ``first`` (an if body) left a dead epilogue
        that leads to live code other than the code at ``second`` (what follows the if body when the
        statement is read without an else clause): the epilogue jumps past ``second``, so ``second``
        is an else clause."""
        if not self.graph.dead_code:
            return False
        dominates = self.analysis.dominates
        skip = BLOCK_END_OPS | {'LOAD_CONST', 'STORE_FAST', 'DELETE_FAST', 'STORE_NAME', 'DELETE_NAME'}
        for anchor, entry in self.graph.dead_entries:
            if not dominates(first, anchor) or dominates(second, anchor):
                continue
            node = entry
            for _ in range(10):
                node = self.follow_dead_jumps(node)
                if node is None or node is TERMINATED or not node.dead or node.opname not in skip:
                    break
                node = node.next
            if node is not None and node is not TERMINATED and not node.dead and node is not second                     and not dominates(second, node) and not dominates(first, node):
                return True
        return False

    def closes_with_dead(self, first: InstructionNode, own: bool = False):
        """[dead-code] The unreachable region that follows the code starting at ``first`` directly:
        the one hanging off its last (in layout) live instruction, or None.

        With ``own``, the region counts as evidence that the code at ``first`` is a clause of its
        own (an else clause, whose end is a jump target): only what the optimizer would have wiped
        otherwise qualifies, which is a region right behind a RETURN_VALUE with no jump target in
        between.  Anything else (behind a raise, behind the RERAISE of an enclosing handler, behind
        a live jump) exists in either spelling and tells nothing.
        """
        if not self.graph.dead_code:
            return None
        dominates = self.analysis.dominates
        live = [n for n in self.graph.nodes if not n.dead and not n.is_pseudo and dominates(first, n)]
        if not live:
            return None
        last = max(live, key=lambda n: n.offset)
        if last.threaded:
            return None  # the code after it is where its jump went, not what follows it in layout
        if own and last.opname != 'RETURN_VALUE':
            return None
        for anchor, entry in self.graph.dead_entries:
            if anchor is last:
                if own and entry in self.graph.dead_gapped:
                    return None
                return entry
        return None

    def drop_foreign_epilogue(self, block: Block, expected: str) -> None:
        """[dead-code] A block that stopped at a dead region which is not its construct's epilogue
        (``expected``) but the entry of an exception handler (one of a dead copy of a with statement,
        say) has simply ended; the region is nobody's continuation."""
        if self.graph.dead_code and block.exit is not None and block.exit.dead and block.exit.opname != expected                 and block.exit.opname in ('WITH_EXCEPT_START', 'DUP_TOP', 'POP_TOP'):
            self._dead_claimed.add(block.exit)
            block.exit = None

    @staticmethod
    def return_fragment(node: Optional[InstructionNode]) -> Optional[InstructionNode]:
        """[dead-code] The RETURN_VALUE ending the fragment ``(ROT_TWO | POP_TOP)* RETURN_VALUE`` that
        starts at ``node``: the remainder of a return statement whose inlined finally body jumped
        away with the value still on the stack.  None when ``node`` starts no such fragment."""
        for _ in range(6):
            if node is None or not node.dead:
                return None
            if node.opname == 'RETURN_VALUE':
                return node
            if node.opname not in ('ROT_TWO', 'ROT_THREE', 'ROT_FOUR', 'POP_TOP'):
                return None
            node = node.next
        return None

    def dead_continuation(self, head: InstructionNode, ctx: Ctx, claim: bool = True) -> Optional[InstructionNode]:
        """[dead-code] The unreachable code that follows the statement starting at ``head``, if any.

        The statement that ended the block consists of nodes ``head`` dominates; the
        dead region hanging off the first such node (in layout order) is the code
        the author wrote after the jump statement.
        """
        dominates = self.analysis.dominates
        toplevel = ctx.start is self.graph.entry
        candidates = [entry for anchor, entry in self.graph.dead_entries
                      if entry not in self._dead_claimed and anchor.offset >= head.offset and dominates(head, anchor)
                      and (toplevel or entry is not self.implicit_end)]
        if not candidates:
            return None
        entry = min(candidates, key=lambda n: n.offset)
        logger.debug('dead region at {} continues the statement at {} (claimed: {})', entry, head, claim)
        if claim:
            self._dead_claimed.add(entry)
        return entry

    def parse_branch(self, start: InstructionNode, owners, stack: list, ctx: Ctx) -> Block:
        """Parse the block entered through the edges coming from ``owners`` only.

        When ``start`` can also be reached from elsewhere it is not part of
        the branch: the branch is empty and ``start`` is its exit.
        """
        if start in ctx.stops or not self.owned(start, owners) or self.behind_loop(start, ctx):
            return Block([], start, list(stack))
        return self.parse_block(start, stack, ctx.evolve(start=start))

    def behind_loop(self, node: InstructionNode, ctx: Ctx) -> bool:
        """[dead-code] ``node`` is live code laid out behind the innermost loop that only jumps lead
        to: the code after the loop, where 'break' goes, even when one path alone reaches it
        (parsed inline it would be a 'return' inside the loop, with a dead implicit return behind)."""
        loop = ctx.loop
        if not self.graph.dead_code or loop is None or node.dead or node in loop.nodes or node.is_pseudo:
            return False
        # (reached by jumps only, never by falling through: a threaded fall-through, or a branch's
        # taken edge, which the optimizer may have threaded through the 'break' jump itself)
        # (a for loop's body ends where FOR_ITER leaves to; a while loop's where its last back jump
        # is, which the optimizer wipes behind a return, making the last statement look outside)
        end = loop.header.jump.offset if loop.is_for and loop.header.jump is not None else loop.end_offset
        return node.offset >= end and node.offset > loop.end_offset and bool(node.preds)             and all(p.threaded or self.is_taken_edge(p, label) for p, label in node.preds)

    def owned(self, node: InstructionNode, owners) -> bool:
        return all(p in owners for p in self.analysis.forward_preds(node))

    def at_boundary(self, node: InstructionNode, st: State, entering: bool = False) -> bool:
        ctx = st.ctx
        if not entering and node in ctx.stops:
            return True
        if node is self.implicit_end and ctx.start is not self.graph.entry:
            return True  # [dead-code] the function's own end: not part of a nested block
        if self.graph.dead_code and node is self.function_end and ctx.start is not self.graph.entry                 and len(st.stack) == ctx.base and self.kind not in ('lambda', 'comprehension'):
            # [dead-code] the live implicit return is never a statement of a nested block either: a
            # block that alone reaches it ('if a: x else: raise') falls out to the function's end
            # (written 'return', an explicit one would leave the implicit return dead)
            return True
        if not self.analysis.dominates(ctx.start, node):
            return True
        if not entering and len(st.stack) == ctx.base and self.behind_loop(node, ctx):
            return True  # [dead-code] the code after the loop, reached by 'break'
        if len(st.stack) == ctx.base:
            if node.opname in BLOCK_END_OPS:
                return True
            # Layout hint: code placed after a 'while' loop that is not part of it is where the
            # loop's 'break's go, even when (graph-wise) it could be nested in the breaking branch.
            if not entering and ctx.fblocks:
                loop = ctx.fblocks[-1]
                if loop.is_loop and not loop.is_for:
                    if node is loop.break_target:
                        return True  # the code after the loop
                    if not loop.has_exit and node is loop.layout_exit and loop.break_target is None:
                        return True
        return False

    def dispatch(self, node: InstructionNode, st: State, entering: bool = False):
        """Execute one instruction (or the whole construct it starts).

        Returns None to continue with ``node.next``, a node to continue there,
        or TERMINATED.
        """
        if node.opcode == OP_SPIN:
            return self.parse_spin(node, st)
        name = node.opname
        if not entering and node in self.analysis.loops and name != 'FOR_ITER' and not self.is_async_for(node):
            if st.depth != 0:
                raise DecompileError('a loop starts in the middle of an expression', node)
            return self.parse_while(node, st)
        if name in RETURN_UNWIND_OPS and st.depth == 1 and st.ctx.fblocks and is_plain(st.stack[-1]):
            if self.try_return(node, st):
                return TERMINATED
        handler = getattr(self, 'op_' + name, None)
        if handler is None:
            raise DecompileError(f'unsupported opcode {name}', node)
        return handler(node, st)

    def run_until(self, node: InstructionNode, st: State, done) -> InstructionNode:
        """Execute straight-line code from ``node`` until ``done()`` is true; the next node."""
        while not done():
            if node is None:
                raise DecompileError('control flow runs off the end of the code')
            result = self.dispatch(node, st)
            if result is TERMINATED:
                raise DecompileError('unexpected end of control flow', node)
            node = node.next if result is None else result
        return node

    # -- leaving blocks early --------------------------------------------------
    def resolve_jump(self, node: InstructionNode, ctx: Ctx, owned: bool = True) -> Optional[List[ast.stmt]]:
        """Interpret a block exit as ``break``, ``continue`` or ``return <constant>``.

        ``node`` is where a block stopped.  The clean-up code of the enclosing
        frame blocks is matched from the innermost one outwards; what is found
        behind it decides the statement.  None when it is none of the three.

        The clean-up code of a jump statement belongs to that statement alone.
        ``owned`` tells whether ``node`` can only be reached from the construct
        that asks; if not, ``node`` is shared with other code and can only be
        the *target* of a jump that needs no clean-up.
        """
        if not owned:
            fb = ctx.fblocks[-1] if ctx.fblocks else None
            if fb is None or not fb.is_loop:
                return None
            if node is fb.header:
                return [ast.Continue()]
            if fb.is_for:
                return None
            if fb.break_target is None and node not in fb.nodes:
                fb.break_target = node
            return [ast.Break()] if node is fb.break_target else None
        return self._resolve(node, len(ctx.fblocks) - 1, ctx, False, entry=True)

    def _resolve(self, node, index: int, ctx: Ctx, past_loop: bool, after_copy=None, entry: bool = False):
        """``after_copy``: ``node`` follows an inlined finally body, whose nodes these are
        (several paths of that body may end at ``node``)."""
        if node is None or node is TERMINATED:
            return None
        # clean-up code that other code runs as well is not the clean-up code of a jump statement
        if after_copy is not None:
            unshared = all(p in after_copy for p, _ in node.preds)
        else:
            unshared = len(node.preds) == 1
        exclusive = entry or unshared
        if index < 0:
            node = self.follow_dead_jumps(node)  # [dead-code]
            if self.graph.dead_code and node is self.function_end:
                # [dead-code] falling through to the end of the function is not a 'return' statement:
                # written as one, the implicit return would become unreachable
                return None
            # every frame block is closed: 'return <constant>'
            # (the compiler emits these two instructions for one 'return' only: nothing else leads to them)
            if node.opname == 'LOAD_CONST' and node.next is not None and node.next.opname == 'RETURN_VALUE' \
                    and unshared and len(node.next.preds) == 1 \
                    and self.kind in ('function', 'lambda', 'comprehension'):
                return [self.make_return(self.const(node.argval))]
            return None
        fb = ctx.fblocks[index]
        if fb.is_loop and not past_loop:
            node = self.follow_dead_jumps(node)  # [dead-code]
            if node is fb.header:
                return [ast.Continue()]
            after = self.follow_dead_jumps(fb.unwind(self, node, False))  # [dead-code] (the follow)
            if after is None or (after is not node and not exclusive):
                return None
            if fb.break_target is not None:
                if after is fb.break_target:
                    return [ast.Break()]
                return self._resolve(after, index - 1, ctx, True)
            # No 'break' seen yet.  Code that only this path runs and that returns is a
            # 'return'; anything else outside the loop is where 'break' goes.
            if self.behind_loop(after, ctx):
                fb.break_target = after  # [dead-code]
                return [ast.Break()]
            if len(after.preds) == 1:
                as_return = self._resolve(after, index - 1, ctx, True)
                if as_return is not None:
                    return as_return
            if self.graph.dead_code and fb.header.offset < after.offset < fb.end_offset:
                # [dead-code] laid out inside the loop: a statement of the body that never comes
                # back (a raise), not where 'break' goes (written 'break' + the statement after the
                # loop, the loop's end would leave a dead implicit return behind)
                return None
            if after not in fb.nodes:
                fb.break_target = after
                return [ast.Break()]
            return None
        after = fb.unwind(self, node, False)
        if after is None or after is TERMINATED or (after is not node and not exclusive):
            return None
        after = self.follow_dead_jumps(after)  # [dead-code]
        return self._resolve(after, index - 1, ctx, past_loop,
                             fb.copy_nodes if isinstance(fb, FinallyTryBlock) else None)

    @staticmethod
    def is_taken_edge(node: InstructionNode, label: str) -> bool:
        """[dead-code] The edge ``label`` out of ``node`` is the jump, not the fall-through."""
        if label == JUMP:
            return True
        return (node.opname == 'POP_JUMP_IF_FALSE' and label == FALSE)             or (node.opname == 'POP_JUMP_IF_TRUE' and label == TRUE)

    def close_block(self, block: Block, ctx: Ctx, accept=()) -> None:
        """Make sure ``block`` ends in a way its construct expects.

        An exit that is not one of ``accept`` must be a jump statement, which
        is appended; the block is then terminated.
        """
        if block.exit is None or block.exit in accept:
            return
        jump = self.resolve_jump(block.exit, ctx)
        if jump is None:
            self.check_missing_loop(block.exit, ctx)
            raise DecompileError('cannot tell where control goes from here', block.exit)
        self.append_jump(block, jump, ctx)

    def append_jump(self, block: Block, jump: List[ast.stmt], ctx: Ctx) -> None:
        """End ``block`` with the jump statement found at its exit."""
        exit = block.exit
        block.stmts.extend(jump)
        block.exit = None
        if self.graph.dead_code:
            # [dead-code] the unreachable statements written after the jump statement
            continuation = self.dead_continuation(exit, ctx)
            if continuation is not None and continuation.opname not in BLOCK_END_OPS:
                # (a region that starts with clean-up code is the epilogue of the construct the
                # jump left, not statements; see handler_epilogue)
                tail = self.parse_block(continuation, block.stack, ctx.evolve(start=continuation))
                block.stmts.extend(tail.stmts)
                block.exit = tail.exit
                block.implicit = tail.implicit

    def check_missing_loop(self, node: InstructionNode, ctx: Ctx) -> None:
        """Raise SyntheticLoop when ``node`` runs inlined finally bodies and then jumps somewhere
        that is neither a return nor explained by a loop of the graph: see SyntheticLoop."""
        copies = 0
        index = len(ctx.fblocks) - 1
        while index >= 0:
            fb = ctx.fblocks[index]
            if fb.is_loop:
                break
            after = fb.unwind(self, node, False)
            if after is None or after is TERMINATED:
                return
            if isinstance(fb, FinallyTryBlock):
                copies += 1
            node = after
            index -= 1
        if copies == 0 or node.opname == 'RETURN_VALUE':
            return  # ('return <constant>' through the copies was ruled out by the caller already)
        raise SyntheticLoop(node, index + 1)

    def parse_synthetic_loop(self, header: InstructionNode, st: State, exit: InstructionNode):
        """``while True:`` around the statement at ``header``, which every path leaves with ``break`` to ``exit``."""
        nodes = {header}
        work = [header]
        while work:
            for _, succ in work.pop().successors():
                if succ is not exit and succ not in nodes:
                    nodes.add(succ)
                    work.append(succ)
        fb = LoopBlock(header, nodes, False)
        fb.has_exit = False
        fb.break_target = exit
        body_ctx = st.ctx.evolve(start=header, stops=st.ctx.stops | {header, exit}, push=fb)
        body = self.parse_block(header, st.stack, body_ctx, entering=True)
        self.close_block(body, body_ctx)
        if not any(isinstance(s, ast.Break) for s in ast.walk(ast.Module(body=body.stmts, type_ignores=[]))):
            raise DecompileError('no break in the synthesized loop', header)
        st.emit(ast.While(test=ast.Constant(value=True), body=body.stmts, orelse=[]))
        return exit

    def try_return(self, node: InstructionNode, st: State) -> bool:
        """``return <value>``: the value is on the stack and the clean-up code of every frame block follows."""
        current = node
        for fb in reversed(st.ctx.fblocks):
            current = fb.unwind(self, current, True)
            if current is None:
                return False
            if current is TERMINATED:
                break
        else:
            if current.opname != 'RETURN_VALUE':
                return False
        st.emit(self.make_return(st.pop()))
        return True

    def skip_finally_copy(self, fb: FinallyTryBlock, start: InstructionNode):
        """Find the end of an inlined copy of a finally body that starts at ``start``.

        The copy is walked in lock-step with the copy on the exception path
        (which has been parsed); the node that corresponds to the RERAISE
        ending that one is where control continues.  Paths on which the two
        copies differ leave the finally body for good (their clean-up code
        differs) and are not followed.  TERMINATED when the body never completes.
        """
        fb.copy_nodes = set()
        fb.copy_ends = set()
        if fb.terminal is None:
            return TERMINATED
        seen = {}
        work = [(fb.entry, start)]
        found = None
        while work:
            canon, copy = work.pop()
            while copy is not None and copy is not TERMINATED and copy.dead                     and copy.opname in ('JUMP_ABSOLUTE', 'JUMP_FORWARD'):
                # [dead-code] a dead copy keeps its jumps as nodes: they are part of the copy (what
                # hangs off them, such as the dead exit of a with statement whose body jumped, is
                # the copy's own business), and stand for where they go
                fb.copy_nodes.add(copy)
                copy = copy.jump
            if canon is fb.terminal:
                fb.copy_ends.add(copy)
                if found is None:
                    found = copy
                elif found is not copy and not (found.opname == copy.opname == 'RETURN_VALUE'):
                    # (the peephole optimizer turns jumps to a RETURN_VALUE into copies of it)
                    raise DecompileError('inconsistent copies of a finally body', copy)
                continue
            if canon in seen:
                continue
            seen[canon] = copy
            fb.copy_nodes.add(copy)
            if not self._same_instruction(canon, copy):
                if self.graph.dead_code:
                    # [dead-code] the copies part here: a jump statement leaving the finally body
                    # unwinds different blocks in each.  The copy's clean-up chain, up to where
                    # the jump goes, is still the copy's code (what hangs off it, such as the dead
                    # exit of a with statement the jump left, is the copy's business)
                    chain = copy
                    while chain is not None and chain is not TERMINATED and not chain.is_pseudo                             and chain not in fb.copy_nodes | {copy} and self.analysis.dominates(start, chain):
                        fb.copy_nodes.add(chain)
                        chain = chain.next
                    chain = copy.next
                    while chain is not None and chain is not TERMINATED and not chain.is_pseudo                             and chain not in fb.copy_nodes and self.analysis.dominates(start, chain):
                        fb.copy_nodes.add(chain)
                        chain = chain.next
                continue
            for label, succ in canon.successors():
                other = copy.out.get(label)
                if other is not None:
                    work.append((succ, other))
        if self.graph.dead_code:
            # [dead-code] unreachable code inside the copy (the epilogue of a try statement left with
            # 'break') belongs to the copy, which is never read as statements
            # (not the one hanging off the copy's last node: that follows the jump statement itself).
            # What hangs off a RETURN_VALUE that ends the copy is a fragment of the epilogue that
            # the optimizer left standing at a jump target -- except for the return statement's
            # own RETURN_VALUE, the one laid out last: what hangs off that one follows the statement.
            returns = [n for n in fb.copy_ends if n.opname == 'RETURN_VALUE']
            own = max(returns, key=lambda n: n.offset) if returns else None
            self._dead_claimed.update(entry for anchor, entry in self.graph.dead_entries
                                      if (anchor in fb.copy_nodes and anchor.next is not found)
                                      or (anchor in returns and anchor is not own))
        return found if found is not None else TERMINATED

    @staticmethod
    def _same_instruction(a: InstructionNode, b: InstructionNode) -> bool:
        if a.opcode != b.opcode or a.is_branch != b.is_branch:
            return False
        if a.out.keys() != b.out.keys():
            return False
        if a.opname in ('POP_JUMP_IF_TRUE', 'POP_JUMP_IF_FALSE', 'JUMP_IF_TRUE_OR_POP', 'JUMP_IF_FALSE_OR_POP',
                        'FOR_ITER', 'SETUP_FINALLY', 'SETUP_WITH', 'SETUP_ASYNC_WITH', 'JUMP_IF_NOT_EXC_MATCH'):
            return True
        if isinstance(a.argval, CodeType) and isinstance(b.argval, CodeType):
            return a.argval.co_code == b.argval.co_code and a.argval.co_name == b.argval.co_name
        return a.argval == b.argval and type(a.argval) is type(b.argval)

    # -- small helpers ---------------------------------------------------------
    @staticmethod
    def make_return(value: ast.expr) -> ast.Return:
        if isinstance(value, ast.Constant) and value.value is None:
            return ast.Return(value=None)
        return ast.Return(value=value)

    def pop_expr(self, st: State, node: InstructionNode = None) -> ast.expr:
        """Pop a value that must be an expression."""
        if not st.stack:
            raise DecompileError('stack underflow', node)
        value = st.stack.pop()
        if not isinstance(value, ast.expr):
            raise DecompileError(f'expected an expression on the stack, found {value!r}', node)
        return value

    def pop_many(self, st: State, count: int, node: InstructionNode = None) -> list:
        if count == 0:
            return []
        if len(st.stack) < count:
            raise DecompileError('stack underflow', node)
        values = st.stack[-count:]
        del st.stack[-count:]
        for v in values:
            if not isinstance(v, ast.expr):
                raise DecompileError(f'expected an expression on the stack, found {v!r}', node)
        return values
