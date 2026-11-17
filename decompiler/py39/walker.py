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
from types import CodeType
from typing import List, Optional

from decompiler.errors import DecompileError
from decompiler.graph.analysis import GraphAnalysis
from decompiler.graph.builder import build_graph
from decompiler.graph.node import InstructionNode, OP_SPIN
from decompiler.py39.context import (TERMINATED, Block, Ctx, FinallyTryBlock, LoopBlock, State)
from decompiler.py39.control import ControlMixin
from decompiler.py39.definitions import DefinitionMixin
from decompiler.py39.exceptions import ExceptionMixin
from decompiler.py39.expressions import ExpressionMixin
from decompiler.py39.statements import StatementMixin
from decompiler.py39.values import Accumulator, IterValue, is_plain

#: instructions that end a block when met at statement level
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
        self.graph = build_graph(code)
        self.analysis = GraphAnalysis(self.graph)
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
            block = self.parse_block(first, stack, ctx)
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
        while True:
            if node is None:
                raise DecompileError('control flow runs off the end of the code')
            if self.at_boundary(node, st, first):
                return Block(st.stmts, node, st.stack)
            result = self.dispatch(node, st, first)
            first = False
            if result is TERMINATED:
                return Block(st.stmts, None, st.stack, st.implicit_end)
            node = node.next if result is None else result

    def parse_branch(self, start: InstructionNode, owners, stack: list, ctx: Ctx) -> Block:
        """Parse the block entered through the edges coming from ``owners`` only.

        When ``start`` can also be reached from elsewhere it is not part of
        the branch: the branch is empty and ``start`` is its exit.
        """
        if start in ctx.stops or not self.owned(start, owners):
            return Block([], start, list(stack))
        return self.parse_block(start, stack, ctx.evolve(start=start))

    def owned(self, node: InstructionNode, owners) -> bool:
        return all(p in owners for p in self.analysis.forward_preds(node))

    def at_boundary(self, node: InstructionNode, st: State, entering: bool = False) -> bool:
        ctx = st.ctx
        if not entering and node in ctx.stops:
            return True
        if not self.analysis.dominates(ctx.start, node):
            return True
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
            # every frame block is closed: 'return <constant>'
            # (the compiler emits these two instructions for one 'return' only: nothing else leads to them)
            if node.opname == 'LOAD_CONST' and node.next is not None and node.next.opname == 'RETURN_VALUE' \
                    and unshared and len(node.next.preds) == 1 \
                    and self.kind in ('function', 'lambda', 'comprehension'):
                return [self.make_return(self.const(node.argval))]
            return None
        fb = ctx.fblocks[index]
        if fb.is_loop and not past_loop:
            if node is fb.header:
                return [ast.Continue()]
            after = fb.unwind(self, node, False)
            if after is None or (after is not node and not exclusive):
                return None
            if fb.break_target is not None:
                if after is fb.break_target:
                    return [ast.Break()]
                return self._resolve(after, index - 1, ctx, True)
            # No 'break' seen yet.  Code that only this path runs and that returns is a
            # 'return'; anything else outside the loop is where 'break' goes.
            if len(after.preds) == 1:
                as_return = self._resolve(after, index - 1, ctx, True)
                if as_return is not None:
                    return as_return
            if after not in fb.nodes:
                fb.break_target = after
                return [ast.Break()]
            return None
        after = fb.unwind(self, node, False)
        if after is None or after is TERMINATED or (after is not node and not exclusive):
            return None
        return self._resolve(after, index - 1, ctx, past_loop,
                             fb.copy_nodes if isinstance(fb, FinallyTryBlock) else None)

    def close_block(self, block: Block, ctx: Ctx, accept=()) -> None:
        """Make sure ``block`` ends in a way its construct expects.

        An exit that is not one of ``accept`` must be a jump statement, which
        is appended; the block is then terminated.
        """
        if block.exit is None or block.exit in accept:
            return
        jump = self.resolve_jump(block.exit, ctx)
        if jump is None:
            raise DecompileError('cannot tell where control goes from here', block.exit)
        block.stmts.extend(jump)
        block.exit = None

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
        if fb.terminal is None:
            return TERMINATED
        seen = {}
        work = [(fb.entry, start)]
        found = None
        while work:
            canon, copy = work.pop()
            if canon is fb.terminal:
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
                continue
            for label, succ in canon.successors():
                other = copy.out.get(label)
                if other is not None:
                    work.append((succ, other))
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
