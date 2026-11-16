"""Conditions, ``if``, conditional expressions, boolean operators and loops."""
from __future__ import annotations

import ast
from typing import List, Optional, Set

from decompiler.errors import DecompileError
from decompiler.graph.node import InstructionNode
from decompiler.py39.context import TERMINATED, Block, Ctx, LoopBlock, State, follow
from decompiler.py39.values import AssertionErrorValue, IterMarker, IterValue, TargetMarker, is_plain

CONDITIONAL_OPS = frozenset({'POP_JUMP_IF_TRUE', 'POP_JUMP_IF_FALSE', 'JUMP_IF_TRUE_OR_POP', 'JUMP_IF_FALSE_OR_POP'})
#: instructions a condition can never contain; meeting one ends the attempt to read a condition
NOT_IN_CONDITION = frozenset({'SETUP_FINALLY', 'SETUP_WITH', 'SETUP_ASYNC_WITH', 'FOR_ITER', 'RETURN_VALUE',
                              'RAISE_VARARGS', 'RERAISE', 'POP_BLOCK', 'POP_EXCEPT'})


def negate(test: ast.expr) -> ast.expr:
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return test.operand
    return ast.UnaryOp(op=ast.Not(), operand=test)


def negatable(test: ast.expr) -> bool:
    """False when ``not test`` would not compile to the same test with the jump reversed.

    The compiler rewrites ``not (a is b)`` to ``a is not b`` (same for ``in``),
    which changes the instruction; such a test can only be used the way it is.
    """
    return not (isinstance(test, ast.Compare) and len(test.ops) == 1
                and isinstance(test.ops[0], (ast.Is, ast.IsNot, ast.In, ast.NotIn)))


def bool_op(op_type, left: ast.expr, right: ast.expr) -> ast.BoolOp:
    """``left op right``, written as one flat operation when an operand already is one.

    ``a and (b and c)`` and ``a and b and c`` compile to the same code, so
    nothing is lost.
    """
    values: List[ast.expr] = []
    for operand in (left, right):
        if type(operand) is ast.BoolOp and isinstance(operand.op, op_type):
            values.extend(operand.values)
        else:
            values.append(operand)
    return ast.BoolOp(op=op_type(), values=values)


def chain_compare(left: ast.expr, right: ast.expr, shared) -> Optional[ast.Compare]:
    """``a < b`` and ``b < c`` with the very same ``b`` are the two halves of ``a < b < c``."""
    if isinstance(left, ast.Compare) and isinstance(right, ast.Compare) and right.left is shared \
            and left.comparators[-1] is shared:
        return ast.Compare(left=left.left, ops=left.ops + right.ops, comparators=left.comparators + right.comparators)
    return None


class Cond:
    """A (possibly compound) condition: where control goes when it is true and when it is false.

    ``keep_true`` / ``keep_false`` say that the value of the condition is left
    on the stack along that edge (``JUMP_IF_TRUE_OR_POP`` and friends).
    """
    __slots__ = ('test', 'true', 'false', 'keep_true', 'keep_false', 'nodes', 'first', 'last', 'chain')

    def __init__(self, test, true, false, keep_true, keep_false, node, chain):
        self.test: ast.expr = test
        self.true: InstructionNode = true
        self.false: InstructionNode = false
        self.keep_true: bool = keep_true
        self.keep_false: bool = keep_false
        self.nodes: Set[InstructionNode] = {node}  # the conditional jumps that make up the condition
        self.first: InstructionNode = node  # the first jump: it dominates everything the condition guards
        self.last: InstructionNode = node  # the jump whose fall-through leads to the 'body'
        #: the duplicated middle operand of a chained comparison that is still on the stack
        self.chain = chain


class ControlMixin:
    # -- conditions ----------------------------------------------------------------
    def make_cond(self, node: InstructionNode, test: ast.expr, stack: list) -> Cond:
        chain = None
        if isinstance(test, ast.Compare) and stack and stack[-1] is test.comparators[-1]:
            chain = stack[-1]
        name = node.opname
        if name in ('POP_JUMP_IF_FALSE', 'POP_JUMP_IF_TRUE'):
            return Cond(test, node.true, node.false, False, False, node, chain)
        if name == 'JUMP_IF_TRUE_OR_POP':
            return Cond(test, node.jump, node.next, True, False, node, chain)
        return Cond(test, node.next, node.jump, False, True, node, chain)

    def extend_cond(self, cond: Cond, stack: list, ctx: Ctx, extend: bool = True) -> list:
        """Absorb the conditions ``cond`` is combined with by ``and`` / ``or`` / ``if-else``.

        ``stack`` is the stack after the test was popped; the stack to continue
        with is returned (a chained comparison consumes one more entry).
        """
        if cond.chain is not None and not cond.keep_false and not cond.keep_true:
            # a < b < c in a test: on failure the duplicated b is popped by a cleanup POP_TOP
            cleanup = cond.false
            target = follow(cleanup, 'POP_TOP')
            sub = self.try_condition(cond.true, stack, len(stack), ctx, extend=False) if target is not None else None
            merged = chain_compare(cond.test, sub.test, cond.chain) if sub is not None else None
            if merged is None or sub.keep_true or sub.keep_false or sub.false is not target:
                raise DecompileError('unsupported chained comparison', cond.last)
            cond.test = merged
            cond.true, cond.false = sub.true, target
            cond.nodes |= sub.nodes
            cond.nodes.add(cleanup)
            cond.last = sub.last
            cond.chain = None
            stack = stack[:-1]
        if cond.chain is not None or not extend:
            return stack  # (a chain that is left is in value context: handled by parse_boolop)
        depth = len(stack) + 1
        changed = True
        while changed:
            changed = False
            if cond.true is cond.false:
                break
            # cond and sub
            if not cond.keep_true and self.owned(cond.true, cond.nodes):
                sub = self.try_condition(cond.true, stack, depth, ctx)
                if sub is not None:
                    if sub.false is cond.false and sub.keep_false == cond.keep_false:
                        cond.test = bool_op(ast.And, cond.test, sub.test)
                        cond.true, cond.keep_true = sub.true, sub.keep_true
                        changed = True
                    elif sub.true is cond.false and not (sub.keep_true or sub.keep_false or cond.keep_false):
                        # 'cond and not sub' or, seen from the other side, 'not cond or sub': take the
                        # one that reads naturally (its true side is where the last jump falls through to)
                        forms = ['and-not', 'not-or'] if sub.last.opname == 'POP_JUMP_IF_TRUE' else ['not-or', 'and-not']
                        for form in forms:
                            if form == 'and-not' and negatable(sub.test):
                                cond.test = bool_op(ast.And, cond.test, negate(sub.test))
                                cond.true = sub.false
                                changed = True
                                break
                            if form == 'not-or' and negatable(cond.test):
                                cond.test = bool_op(ast.Or, negate(cond.test), sub.test)
                                cond.true, cond.false = cond.false, sub.false
                                changed = True
                                break
                    if changed:
                        cond.nodes |= sub.nodes
                        cond.last = sub.last
                        continue
            # cond or sub
            if not cond.keep_false and self.owned(cond.false, cond.nodes):
                sub = self.try_condition(cond.false, stack, depth, ctx)
                if sub is not None:
                    if sub.true is cond.true and sub.keep_true == cond.keep_true:
                        cond.test = bool_op(ast.Or, cond.test, sub.test)
                        cond.false, cond.keep_false = sub.false, sub.keep_false
                        changed = True
                    elif sub.false is cond.true and not (sub.keep_true or sub.keep_false or cond.keep_true):
                        # 'cond or not sub' or 'not cond and sub'
                        forms = ['or-not', 'not-and'] if sub.last.opname == 'POP_JUMP_IF_TRUE' else ['not-and', 'or-not']
                        for form in forms:
                            if form == 'or-not' and negatable(sub.test):
                                cond.test = bool_op(ast.Or, cond.test, negate(sub.test))
                                cond.false = sub.true
                                changed = True
                                break
                            if form == 'not-and' and negatable(cond.test):
                                cond.test = bool_op(ast.And, negate(cond.test), sub.test)
                                cond.true, cond.false = sub.true, cond.true
                                changed = True
                                break
                    if changed:
                        cond.nodes |= sub.nodes
                        cond.last = sub.last
                        continue
            # (a if cond else b) used as a test: both sides are tests with the same two targets
            if not cond.keep_true and not cond.keep_false and self.owned(cond.true, cond.nodes) \
                    and self.owned(cond.false, cond.nodes):
                yes = self.try_condition(cond.true, stack, depth, ctx)
                no = self.try_condition(cond.false, stack, depth, ctx) if yes is not None else None
                if yes is not None and no is not None and not (yes.keep_true or yes.keep_false
                                                               or no.keep_true or no.keep_false):
                    if yes.true is no.true and yes.false is no.false:
                        cond.test = ast.IfExp(test=cond.test, body=yes.test, orelse=no.test)
                    elif yes.true is no.false and yes.false is no.true and negatable(no.test):
                        cond.test = ast.IfExp(test=cond.test, body=yes.test, orelse=negate(no.test))
                    else:
                        continue
                    cond.true, cond.false = yes.true, yes.false
                    cond.nodes |= yes.nodes | no.nodes
                    cond.last = yes.last
                    changed = True
        return stack

    def try_condition(self, start: InstructionNode, stack: list, depth: int, ctx: Ctx,
                      entering: bool = False, extend: bool = True) -> Optional[Cond]:
        """Read a condition that starts at ``start``, or None when there is none.

        The code from ``start`` must compute a single value (leaving the stack
        ``depth`` deep) and test it, without doing anything else.  With
        ``extend`` false the condition is not combined with the ``and``/``or``
        operands that follow it.
        """
        st = State(list(stack), ctx.evolve(start=start))
        node = start
        first = entering
        try:
            while True:
                if node is None:
                    return None
                if node.opname in CONDITIONAL_OPS and (len(st.stack) == depth or (
                        # the middle of a chained comparison: its duplicated operand is still below the test
                        len(st.stack) == depth + 1 and isinstance(st.stack[-1], ast.Compare)
                        and st.stack[-2] is st.stack[-1].comparators[-1])):
                    test = st.pop()
                    if not is_plain(test):
                        return None
                    cond = self.make_cond(node, test, st.stack)
                    self.extend_cond(cond, st.stack, ctx, extend)
                    return cond
                if node.opname in NOT_IN_CONDITION or node.is_pseudo:
                    return None
                if not first and node in self.analysis.loops:
                    return None
                if self.at_boundary(node, st, first):
                    return None
                result = self.dispatch(node, st, first)
                first = False
                if result is TERMINATED or st.stmts or st.pending:
                    return None
                node = node.next if result is None else result
        except DecompileError:
            return None

    def _conditional(self, node, st):
        test = self.pop_expr(st, node)
        cond = self.make_cond(node, test, st.stack)
        st.stack = self.extend_cond(cond, st.stack, st.ctx)
        if cond.keep_true or cond.keep_false:
            return self.parse_boolop(cond, st)
        return self.parse_if(cond, st)

    op_POP_JUMP_IF_TRUE = op_POP_JUMP_IF_FALSE = _conditional
    op_JUMP_IF_TRUE_OR_POP = op_JUMP_IF_FALSE_OR_POP = _conditional

    # -- a and b, a or b, a < b < c as values -------------------------------------------
    def parse_boolop(self, cond: Cond, st: State):
        if cond.keep_true and cond.keep_false:
            raise DecompileError('unsupported boolean operation', cond.last)
        ctx, stack = st.ctx, st.stack
        if cond.keep_false:
            op, join, start = ast.And, cond.false, cond.true
        else:
            op, join, start = ast.Or, cond.true, cond.false
        below = stack
        if cond.chain is not None:
            # the failing side of a chained comparison drops the duplicated operand: ROT_TWO, POP_TOP
            join = follow(join, 'ROT_TWO', 'POP_TOP')
            if join is None or op is not ast.And:
                raise DecompileError('unsupported chained comparison', cond.last)
            below = stack[:-1]
        if not self.owned(start, cond.nodes):
            raise DecompileError('unsupported boolean operation', cond.last)
        rhs = self.parse_block(start, stack, ctx.evolve(start=start, stops=ctx.stops | {join}))

        def combine(right):
            if cond.chain is not None:
                merged = chain_compare(cond.test, right, cond.chain)
                if merged is None:
                    raise DecompileError('unsupported chained comparison', cond.last)
                return merged
            return bool_op(op, cond.test, right)

        if rhs.exit is join and not rhs.stmts and len(rhs.stack) == len(below) + 1 and is_plain(rhs.stack[-1]):
            st.stack = below + [combine(rhs.stack[-1])]
            return join
        if join.opname == 'RETURN_VALUE' and len(below) == ctx.base:
            # 'return a and (b if c else d)': the peephole optimizer gave each way out its own RETURN_VALUE
            stmts = list(rhs.stmts)
            if rhs.exit is join and len(rhs.stack) == len(below) + 1:
                stmts.append(ast.Return(value=rhs.stack[-1]))
            if rhs.exit is None or rhs.exit is join:
                value = self.stmts_to_expr(stmts)
                if value is not None:
                    st.stack = below
                    st.emit(self.make_return(combine(value)))
                    return TERMINATED
        raise DecompileError('unsupported boolean operation', cond.last)

    def stmts_to_expr(self, stmts: List[ast.stmt]) -> Optional[ast.expr]:
        """Turn ``if t: return a`` / ``return b`` back into the expression ``a if t else b``."""
        if not stmts:
            return None
        first = stmts[0]
        if isinstance(first, ast.Return) and len(stmts) == 1:
            return first.value if first.value is not None else ast.Constant(value=None)
        if isinstance(first, ast.If):
            body = self.stmts_to_expr(first.body)
            orelse = self.stmts_to_expr(first.orelse + stmts[1:])
            if body is not None and orelse is not None:
                return ast.IfExp(test=first.test, body=body, orelse=orelse)
        return None

    # -- if / conditional expression / assert ---------------------------------------------
    def parse_if(self, cond: Cond, st: State):
        ctx, stack = st.ctx, st.stack
        depth = len(stack)
        statement = st.depth == 0
        if cond.true is cond.false:
            if not statement:
                raise DecompileError('unsupported conditional', cond.last)
            st.emit(ast.If(test=cond.test, body=[ast.Pass()], orelse=[]))
            return cond.true
        if statement:
            # assert test: the failing side raises AssertionError ('assert not x' fails on the true side)
            if cond.false.opname == 'LOAD_ASSERTION_ERROR' and self.owned(cond.false, cond.nodes):
                return self.parse_assert(cond.test, cond.false, cond.true, st)
            if cond.true.opname == 'LOAD_ASSERTION_ERROR' and self.owned(cond.true, cond.nodes):
                return self.parse_assert(negate(cond.test), cond.true, cond.false, st)

        # the side reached by falling through the last jump is what the source calls the body
        if cond.last.opname == 'POP_JUMP_IF_TRUE' and negatable(cond.test):
            test, first, second = negate(cond.test), cond.false, cond.true
        else:
            test, first, second = cond.test, cond.true, cond.false
        body = self.parse_branch(first, cond.nodes, stack, ctx)
        if statement and body.terminated:
            # 'if test: ...; return' followed by the rest: no need to nest the rest in an else
            st.emit(ast.If(test=test, body=body.stmts or [ast.Pass()], orelse=[]))
            return second
        orelse = self.parse_branch(second, cond.nodes, stack, ctx)

        if (body.exit is not None and body.exit is orelse.exit and not body.stmts and not orelse.stmts
                and len(body.stack) == depth + 1 == len(orelse.stack)
                and is_plain(body.stack[-1]) and is_plain(orelse.stack[-1])):
            st.stack = stack + [ast.IfExp(test=test, body=body.stack[-1], orelse=orelse.stack[-1])]
            return body.exit
        if not statement:
            raise DecompileError('unsupported conditional inside an expression', cond.last)
        for block in (body, orelse):
            if not block.terminated and len(block.stack) != depth:
                raise DecompileError('a branch leaves a value on the stack', block.exit)

        if body.exit is not None and orelse.exit is not None and body.exit is not orelse.exit:
            # the branches go to different places: at least one of them jumps
            # (an exit that code outside this statement also leads to is not a jump of ours)
            def ours(exit_node):
                return all(self.analysis.dominates(cond.first, p) for p in self.analysis.forward_preds(exit_node))

            body_jump = self.resolve_jump(body.exit, ctx, ours(body.exit))
            orelse_jump = self.resolve_jump(orelse.exit, ctx, ours(orelse.exit))
            if body_jump is None and orelse_jump is None:
                raise DecompileError('the branches of an if never meet', cond.last)
            if body_jump is not None and orelse_jump is not None:
                # Both could be jumps.  The one placed last in the bytecode may just as well be the
                # way the enclosing statement ends; whoever owns that statement decides.
                if body.exit.offset > orelse.exit.offset:
                    body_jump = None
                else:
                    orelse_jump = None
            if body_jump is not None:
                body.stmts.extend(body_jump)
                body.exit = None
            if orelse_jump is not None:
                orelse.stmts.extend(orelse_jump)
                orelse.exit = None

        after = body.exit if body.exit is not None else orelse.exit
        if body.terminated and orelse.stmts:
            st.emit(ast.If(test=test, body=body.stmts, orelse=[]))
            st.stmts.extend(orelse.stmts)
        elif not body.stmts and orelse.stmts:
            loop = ctx.loop
            if negatable(test):
                st.emit(ast.If(test=negate(test), body=orelse.stmts, orelse=[]))
            elif loop is not None and after is loop.header:
                st.emit(ast.If(test=test, body=[ast.Continue()], orelse=[]))
                st.stmts.extend(orelse.stmts)
            else:
                st.emit(ast.If(test=test, body=[ast.Pass()], orelse=orelse.stmts))
        else:
            st.emit(ast.If(test=test, body=body.stmts or [ast.Pass()], orelse=orelse.stmts))
        return after if after is not None else TERMINATED

    def parse_assert(self, test: ast.expr, failing: InstructionNode, passing: InstructionNode, st: State):
        failed = self.parse_block(failing, st.stack, st.ctx.evolve(start=failing))
        if failed.terminated and len(failed.stmts) == 1 and isinstance(failed.stmts[0], ast.Raise):
            exc = failed.stmts[0].exc
            if isinstance(exc, AssertionErrorValue):
                st.emit(ast.Assert(test=test, msg=None))
                return passing
            if isinstance(exc, ast.Call) and isinstance(exc.func, AssertionErrorValue) and len(exc.args) == 1                     and not exc.keywords:
                st.emit(ast.Assert(test=test, msg=exc.args[0]))
                return passing
        raise DecompileError('unsupported assert', failing)

    # -- loops ------------------------------------------------------------------------------
    def parse_spin(self, node, st: State):
        """A jump to itself: ``while True: pass``."""
        st.emit(ast.While(test=ast.Constant(value=True), body=[ast.Pass()], orelse=[]))
        return TERMINATED

    def parse_while(self, header: InstructionNode, st: State):
        # Where 'break' goes is partly guessed before the body is parsed (find_break_target).
        # A wrong guess shows as a body that makes no sense; then the loop is parsed again
        # with less guessing.
        try:
            return self._parse_while(header, st, True, True)
        except DecompileError:
            pass
        try:
            return self._parse_while(header, st, False, True)
        except DecompileError:
            return self._parse_while(header, st, False, False)

    def _parse_while(self, header: InstructionNode, st: State, guess_target: bool, use_layout: bool):
        ctx, stack = st.ctx, st.stack
        loop = self.analysis.loops[header]
        fb = LoopBlock(header, loop.nodes, False)
        body_ctx = ctx.evolve(stops=ctx.stops | {header}, push=fb)

        test: ast.expr = ast.Constant(value=True)
        normal_exit = None
        owners = None
        cond = self.try_condition(header, stack, len(stack) + 1, ctx, entering=True)
        if cond is not None and not cond.keep_true and not cond.keep_false:
            inside_true, inside_false = cond.true in loop.nodes, cond.false in loop.nodes
            # ('assert x' at the start of the body leaves the loop too, but it is no loop test)
            if inside_true and not inside_false and cond.false.opname != 'LOAD_ASSERTION_ERROR':
                test, start, normal_exit = cond.test, cond.true, cond.false
            elif inside_false and not inside_true and negatable(cond.test) \
                    and cond.true.opname != 'LOAD_ASSERTION_ERROR':
                test, start, normal_exit = negate(cond.test), cond.false, cond.true
        self.find_break_target(fb, normal_exit, cond.nodes if normal_exit is not None else ())
        if not guess_target:
            fb.break_target = None
        if not use_layout:
            fb.layout_exit = None
        if normal_exit is not None:
            owners = cond.nodes
            if start is header or not self.owned(start, owners):
                body = Block([], start, list(stack))
            else:
                body = self.parse_block(start, stack, body_ctx.evolve(start=start))
        else:
            fb.has_exit = False
            body = self.parse_block(header, stack, body_ctx.evolve(start=header), entering=True)
        self.close_block(body, body_ctx, accept=(header,))
        return self.finish_loop(st, fb, normal_exit, owners,
                                lambda orelse: ast.While(test=test, body=body.stmts or [ast.Pass()], orelse=orelse))

    def find_break_target(self, fb: LoopBlock, normal_exit, cond_nodes) -> None:
        """Work out where 'break' goes in a while loop, as far as the graph alone tells.

        Code that leaves the loop either ends in 'return'/'raise' or arrives
        at the code after the loop.  Everything such a path runs before that
        point belongs to it alone, so when two of these paths first meet other
        code at the same node, that node is the code after the loop.  With
        fewer than two 'break's the target is found while the body is parsed.
        """
        exits = []
        for node in fb.nodes:
            for _, succ in node.successors():
                if succ not in fb.nodes and succ not in exits:
                    exits.append(succ)
        behind = [e for e in exits if not e.is_pseudo and e.offset > fb.end_offset
                  and e.opname != 'LOAD_ASSERTION_ERROR']
        if behind:
            fb.layout_exit = max(behind, key=lambda e: e.offset)
        if normal_exit is not None and any(p in fb.nodes and p not in cond_nodes for p, _ in normal_exit.preds):
            # the loop is left to where it ends anyway from somewhere else than its test:
            # 'break' in a loop without else clause
            fb.break_target = normal_exit
            return
        dominates = self.analysis.dominates
        votes = {}
        for start in exits:
            if start is normal_exit:
                continue
            if len(self.analysis.forward_preds(start)) >= 2:
                # entered directly from several places: a join itself (or the body of 'if a or b:',
                # in which case the walk below finds the real one behind it)
                votes[start] = votes.get(start, 0) + 2
            seen = {start}
            work = [start]
            while work:
                for _, succ in work.pop().successors():
                    if succ in seen:
                        continue
                    seen.add(succ)
                    if dominates(start, succ):
                        work.append(succ)
                    else:
                        votes[succ] = votes.get(succ, 0) + 1
        if normal_exit is not None and votes.get(normal_exit):
            # a path that arrives where the loop ends anyway: 'break' in a loop without else clause
            fb.break_target = normal_exit
            return
        joins = [node for node, count in votes.items() if count >= 2]
        if len(joins) > 1:
            # Several joins: the body of 'if a or b:' is entered from two tests as well.  Every
            # path that leaves the loop without ending comes by the code after the loop, so that
            # code is the join all the other joins lead to.
            joins = [j for j in joins if all(k is j or self._reaches(k, j) for k in joins)]
        if len(joins) == 1:
            fb.break_target = joins[0]

    @staticmethod
    def _reaches(start: InstructionNode, goal: InstructionNode) -> bool:
        seen = {start}
        work = [start]
        while work:
            for _, succ in work.pop().successors():
                if succ is goal:
                    return True
                if succ not in seen:
                    seen.add(succ)
                    work.append(succ)
        return False

    def finish_loop(self, st: State, fb: LoopBlock, normal_exit, owners, make):
        """Emit a loop statement once its body is known; work out ``else`` and where control continues."""
        ctx = st.ctx
        target = fb.break_target
        if normal_exit is None:
            # no way out except 'break'
            st.emit(make([]))
            return target if target is not None else TERMINATED
        if target is None or target is normal_exit:
            st.emit(make([]))
            return normal_exit
        # the code between the normal exit and where 'break' goes is the else clause
        orelse_ctx = ctx.evolve(stops=ctx.stops | {target})
        orelse = self.parse_branch(normal_exit, owners, st.stack, orelse_ctx)
        self.close_block(orelse, orelse_ctx, accept=(target,))
        st.emit(make(orelse.stmts or [ast.Pass()]))
        return target

    def loop_target(self, node: InstructionNode, st: State, inner: list, what: str):
        """Run the stores that receive the loop variable; (target, node after them)."""
        marker = TargetMarker(what)
        target_state = State(inner + [marker], st.ctx.evolve(base=len(inner)))
        after = self.run_until(node, target_state, lambda: marker.target is not None)
        if target_state.stmts or len(target_state.stack) != len(inner):
            raise DecompileError('unsupported loop target', node)
        return marker.target, after

    def parse_loop_body(self, st: State, fb: LoopBlock, start: InstructionNode, inner: list):
        ctx = st.ctx
        header = fb.header
        body_ctx = ctx.evolve(start=start, stops=ctx.stops | {header}, base=len(inner), push=fb)
        if start is header:
            body = Block([], header, list(inner))
        else:
            body = self.parse_block(start, inner, body_ctx)
        self.close_block(body, body_ctx, accept=(header,))
        return body

    def pop_iterator(self, node, st: State, is_async: bool) -> IterValue:
        """The iterator a loop runs over: GET_ITER/GET_AITER of a value, or the argument of a comprehension."""
        value = st.pop() if st.stack else None
        if isinstance(value, ast.Name) and value.id == '.0' and self.kind == 'comprehension':
            return IterValue(value, is_async)
        if not isinstance(value, IterValue) or value.is_async != is_async:
            raise DecompileError('a loop without an iterator', node)
        return value

    def op_FOR_ITER(self, node, st: State):
        iterable = self.pop_iterator(node, st, False)
        loop = self.analysis.loops.get(node)
        fb = LoopBlock(node, loop.nodes if loop else {node}, True)
        inner = st.stack + [IterMarker()]
        target, start = self.loop_target(node.next, st, inner, 'for')
        body = self.parse_loop_body(st, fb, start, inner)
        return self.finish_loop(st, fb, node.jump, {node}, lambda orelse: ast.For(
            target=target, iter=iterable.value, body=body.stmts or [ast.Pass()], orelse=orelse))

    def parse_async_for(self, node, st: State):
        """GET_AITER; SETUP_FINALLY: GET_ANEXT, LOAD_CONST None, YIELD_FROM, POP_BLOCK, <store>, body."""
        iterable = self.pop_iterator(node, st, True)
        end = node.jump
        start = follow(node.next, 'GET_ANEXT', 'LOAD_CONST', 'YIELD_FROM', 'POP_BLOCK')
        if start is None or end is None or end.opname != 'END_ASYNC_FOR':
            raise DecompileError('unsupported async for', node)
        loop = self.analysis.loops.get(node)
        fb = LoopBlock(node, loop.nodes if loop else {node}, True)
        inner = st.stack + [IterMarker()]
        target, start = self.loop_target(start, st, inner, 'async for')
        body = self.parse_loop_body(st, fb, start, inner)
        return self.finish_loop(st, fb, end.next, {end}, lambda orelse: ast.AsyncFor(
            target=target, iter=iterable.value, body=body.stmts or [ast.Pass()], orelse=orelse))
