"""Instructions that only compute values."""
from __future__ import annotations

import ast
import math
from types import CodeType

from decompiler.errors import DecompileError
from decompiler.graph.dump import const_key
from decompiler.py39.values import (Accumulator, AssertionErrorValue, AwaitableValue, BuildClass, ClassValue,
                                    ClosureCell, CodeValue, ConstSet, FStringPart, FunctionValue, InplaceValue,
                                    IterValue, MethodValue, YieldFromIter, is_plain)

BINARY_OPS = {
    'ADD': ast.Add, 'SUBTRACT': ast.Sub, 'MULTIPLY': ast.Mult, 'TRUE_DIVIDE': ast.Div,
    'FLOOR_DIVIDE': ast.FloorDiv, 'MODULO': ast.Mod, 'POWER': ast.Pow, 'LSHIFT': ast.LShift,
    'RSHIFT': ast.RShift, 'AND': ast.BitAnd, 'XOR': ast.BitXor, 'OR': ast.BitOr,
    'MATRIX_MULTIPLY': ast.MatMult,
}
UNARY_OPS = {
    'UNARY_POSITIVE': ast.UAdd, 'UNARY_NEGATIVE': ast.USub, 'UNARY_INVERT': ast.Invert, 'UNARY_NOT': ast.Not,
}
COMPARE_OPS = {
    '<': ast.Lt, '<=': ast.LtE, '>': ast.Gt, '>=': ast.GtE, '==': ast.Eq, '!=': ast.NotEq,
}
#: FORMAT_VALUE conversion (low two bits) -> ast.FormattedValue.conversion
CONVERSIONS = {0: -1, 1: ord('s'), 2: ord('r'), 3: ord('a')}


class ExpressionMixin:
    # -- constants and names ---------------------------------------------------
    def const(self, value) -> ast.expr:
        """The expression that loads the constant ``value``."""
        if isinstance(value, CodeType):
            return CodeValue(value)
        if isinstance(value, frozenset):
            elts = [self.const(v) for v in sorted(value, key=const_key)]
            return ConstSet(elts=elts)
        if type(value) in (int, float) and math.copysign(1, value) < 0:
            # written as '-<literal>' (the compiler folds it back), so that it gets parentheses
            # where it needs them: (-1) ** x, (-1).real
            return ast.UnaryOp(op=ast.USub(), operand=ast.Constant(value=-value))
        if type(value) is complex and value.real == 0 and math.copysign(1, value.real) < 0:
            return ast.UnaryOp(op=ast.USub(), operand=ast.Constant(value=-value))
        return ast.Constant(value=value)

    def op_LOAD_CONST(self, node, st):
        st.push(self.const(node.argval))

    def _load_name(self, node, st):
        st.push(ast.Name(id=node.argval, ctx=ast.Load()))

    op_LOAD_NAME = op_LOAD_GLOBAL = op_LOAD_FAST = op_LOAD_DEREF = op_LOAD_CLASSDEREF = _load_name

    def op_LOAD_CLOSURE(self, node, st):
        st.push(ClosureCell(node.argval))

    def op_LOAD_ATTR(self, node, st):
        value = self.pop_expr(st, node)
        st.push(ast.Attribute(value=value, attr=node.argval, ctx=ast.Load()))

    def op_LOAD_METHOD(self, node, st):
        st.push(MethodValue(self.pop_expr(st, node), node.argval))

    def op_LOAD_BUILD_CLASS(self, node, st):
        st.push(BuildClass())

    def op_LOAD_ASSERTION_ERROR(self, node, st):
        st.push(AssertionErrorValue())

    # -- operators ---------------------------------------------------------------
    def _binary(self, node, st):
        right = self.pop_expr(st, node)
        left = self.pop_expr(st, node)
        st.push(ast.BinOp(left=left, op=BINARY_OPS[node.opname[len('BINARY_'):]](), right=right))

    def _inplace(self, node, st):
        right = self.pop_expr(st, node)
        left = self.pop_expr(st, node)
        st.push(InplaceValue(left, BINARY_OPS[node.opname[len('INPLACE_'):]](), right))

    def _unary(self, node, st):
        st.push(ast.UnaryOp(op=UNARY_OPS[node.opname](), operand=self.pop_expr(st, node)))

    for _name in BINARY_OPS:
        locals()['op_BINARY_' + _name] = _binary
        locals()['op_INPLACE_' + _name] = _inplace
    for _name in UNARY_OPS:
        locals()['op_' + _name] = _unary
    del _name

    def op_BINARY_SUBSCR(self, node, st):
        index = self.pop_expr(st, node)
        value = self.pop_expr(st, node)
        st.push(ast.Subscript(value=value, slice=index, ctx=ast.Load()))

    def _compare(self, node, st, op):
        right = self.pop_expr(st, node)
        left = self.pop_expr(st, node)
        st.push(ast.Compare(left=left, ops=[op], comparators=[right]))

    def op_COMPARE_OP(self, node, st):
        self._compare(node, st, COMPARE_OPS[node.argval]())

    def op_IS_OP(self, node, st):
        self._compare(node, st, ast.IsNot() if node.arg else ast.Is())

    def op_CONTAINS_OP(self, node, st):
        self._compare(node, st, ast.NotIn() if node.arg else ast.In())

    # -- stack shuffling -----------------------------------------------------------
    def op_DUP_TOP(self, node, st):
        if not st.stack:
            raise DecompileError('stack underflow', node)
        st.push(st.stack[-1])

    def op_DUP_TOP_TWO(self, node, st):
        if len(st.stack) < 2:
            raise DecompileError('stack underflow', node)
        st.stack.extend(st.stack[-2:])

    def _rotate(self, node, st, count):
        if len(st.stack) < count:
            raise DecompileError('stack underflow', node)
        st.stack.insert(len(st.stack) - count, st.stack.pop())

    def op_ROT_TWO(self, node, st):
        self._rotate(node, st, 2)

    def op_ROT_THREE(self, node, st):
        self._rotate(node, st, 3)

    def op_ROT_FOUR(self, node, st):
        self._rotate(node, st, 4)

    # -- displays --------------------------------------------------------------------
    def op_BUILD_TUPLE(self, node, st):
        st.push(ast.Tuple(elts=self.pop_many(st, node.arg, node), ctx=ast.Load()))

    def op_BUILD_LIST(self, node, st):
        st.push(ast.List(elts=self.pop_many(st, node.arg, node), ctx=ast.Load()))

    def op_BUILD_SET(self, node, st):
        st.push(ast.Set(elts=self.pop_many(st, node.arg, node)))

    def op_BUILD_MAP(self, node, st):
        items = self.pop_many(st, 2 * node.arg, node)
        st.push(ast.Dict(keys=items[0::2], values=items[1::2]))

    def op_BUILD_CONST_KEY_MAP(self, node, st):
        keys = self.pop_expr(st, node)
        values = self.pop_many(st, node.arg, node)
        if not isinstance(keys, ast.Constant) or not isinstance(keys.value, tuple) or len(keys.value) != node.arg:
            raise DecompileError('BUILD_CONST_KEY_MAP without a constant tuple of keys', node)
        st.push(ast.Dict(keys=[self.const(k) for k in keys.value], values=values))

    def _container(self, node, st, kind):
        """The container an *_APPEND/*_ADD/*_EXTEND/*_UPDATE instruction works on (after its operands were popped)."""
        index = len(st.stack) - node.arg
        if index < 0:
            raise DecompileError('stack underflow', node)
        target = st.stack[index]
        if not isinstance(target, kind):
            raise DecompileError(f'{node.opname} on {target!r}', node)
        return index, target

    def op_LIST_APPEND(self, node, st):
        value = self.pop_expr(st, node)
        index, target = self._container(node, st, (ast.List, Accumulator))
        if isinstance(target, Accumulator):
            return self.comprehension_element(node, st, value)
        st.stack[index] = ast.List(elts=target.elts + [value], ctx=ast.Load())

    def op_SET_ADD(self, node, st):
        value = self.pop_expr(st, node)
        index, target = self._container(node, st, (ast.Set, Accumulator))
        if isinstance(target, Accumulator):
            return self.comprehension_element(node, st, value)
        st.stack[index] = ast.Set(elts=target.elts + [value])

    def op_MAP_ADD(self, node, st):
        value = self.pop_expr(st, node)
        key = self.pop_expr(st, node)
        index, target = self._container(node, st, (ast.Dict, Accumulator))
        if isinstance(target, Accumulator):
            return self.comprehension_element(node, st, value, key)
        st.stack[index] = ast.Dict(keys=target.keys + [key], values=target.values + [value])

    def op_LIST_EXTEND(self, node, st):
        value = self.pop_expr(st, node)
        index, target = self._container(node, st, ast.List)
        st.stack[index] = ast.List(elts=target.elts + [ast.Starred(value=value, ctx=ast.Load())], ctx=ast.Load())

    def op_SET_UPDATE(self, node, st):
        value = self.pop_expr(st, node)
        index, target = self._container(node, st, ast.Set)
        st.stack[index] = ast.Set(elts=target.elts + [ast.Starred(value=value, ctx=ast.Load())])

    def op_LIST_TO_TUPLE(self, node, st):
        value = self.pop_expr(st, node)
        if not isinstance(value, ast.List):
            raise DecompileError('LIST_TO_TUPLE on something that is not a list display', node)
        st.push(ast.Tuple(elts=value.elts, ctx=ast.Load()))

    def _dict_update(self, node, st):
        value = self.pop_expr(st, node)
        index, target = self._container(node, st, ast.Dict)
        if type(value) is ast.Dict and value.keys and None not in value.keys:
            # '{**a, k: v}': the pairs after an unpacking are built as their own dict and merged in
            st.stack[index] = ast.Dict(keys=target.keys + value.keys, values=target.values + value.values)
        else:
            st.stack[index] = ast.Dict(keys=target.keys + [None], values=target.values + [value])

    op_DICT_UPDATE = op_DICT_MERGE = _dict_update

    def op_BUILD_SLICE(self, node, st):
        parts = self.pop_many(st, node.arg, node)
        parts = [None if isinstance(p, ast.Constant) and p.value is None else p for p in parts]
        if len(parts) == 2:
            parts.append(None)
        st.push(ast.Slice(lower=parts[0], upper=parts[1], step=parts[2]))

    # -- f-strings ---------------------------------------------------------------------
    def op_FORMAT_VALUE(self, node, st):
        spec = None
        if node.arg & 4:
            spec = self.pop_expr(st, node)
            if not isinstance(spec, ast.JoinedStr):
                spec = ast.JoinedStr(values=[spec])
            elif isinstance(spec, FStringPart):
                spec = ast.JoinedStr(values=list(spec.values))
        value = self.pop_expr(st, node)
        field = ast.FormattedValue(value=value, conversion=CONVERSIONS[node.arg & 3], format_spec=spec)
        st.push(FStringPart(values=[field]))

    def op_BUILD_STRING(self, node, st):
        values = []
        for part in self.pop_many(st, node.arg, node):
            if isinstance(part, FStringPart):
                values.extend(part.values)
            elif isinstance(part, ast.Constant) and isinstance(part.value, str):
                values.append(part)
            else:
                raise DecompileError('BUILD_STRING of something that is not a string piece', node)
        st.push(ast.JoinedStr(values=values))

    # -- calls -----------------------------------------------------------------------------
    def make_call(self, node, func, args, keywords) -> ast.expr:
        if isinstance(func, BuildClass):
            return self.make_class(node, args, keywords)
        if isinstance(func, FunctionValue):
            return self.make_comprehension(node, func, args, keywords)
        return ast.Call(func=func, args=args, keywords=keywords)

    def op_CALL_FUNCTION(self, node, st):
        args = self.pop_many(st, node.arg, node)
        func = self.pop_expr(st, node)
        st.push(self.make_call(node, func, args, []))

    def op_CALL_FUNCTION_KW(self, node, st):
        names = self.pop_expr(st, node)
        if not isinstance(names, ast.Constant) or not isinstance(names.value, tuple):
            raise DecompileError('CALL_FUNCTION_KW without a tuple of keyword names', node)
        values = self.pop_many(st, node.arg, node)
        func = self.pop_expr(st, node)
        count = len(names.value)
        positional = values[:len(values) - count]
        keywords = [ast.keyword(arg=name, value=value)
                    for name, value in zip(names.value, values[len(values) - count:])]
        st.push(self.make_call(node, func, positional, keywords))

    def op_CALL_METHOD(self, node, st):
        args = self.pop_many(st, node.arg, node)
        method = st.pop() if st.stack else None
        if not isinstance(method, MethodValue):
            raise DecompileError('CALL_METHOD without LOAD_METHOD', node)
        func = ast.Attribute(value=method.value, attr=method.attr, ctx=ast.Load())
        st.push(ast.Call(func=func, args=args, keywords=[]))

    def op_CALL_FUNCTION_EX(self, node, st):
        keywords = []
        if node.arg & 1:
            mapping = self.pop_expr(st, node)
            if type(mapping) is ast.Dict:
                for key, value in zip(mapping.keys, mapping.values):
                    if key is None:
                        keywords.append(ast.keyword(arg=None, value=value))
                    elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                        keywords.append(ast.keyword(arg=key.value, value=value))
                    else:
                        raise DecompileError('keyword argument with a key that is not a name', node)
            else:
                keywords.append(ast.keyword(arg=None, value=mapping))
        sequence = self.pop_expr(st, node)
        if type(sequence) is ast.Tuple:
            args = list(sequence.elts)
        elif isinstance(sequence, ast.Constant) and isinstance(sequence.value, tuple):
            args = [self.const(v) for v in sequence.value]
        else:
            args = [ast.Starred(value=sequence, ctx=ast.Load())]
        func = self.pop_expr(st, node)
        st.push(self.make_call(node, func, args, keywords))

    # -- iteration, generators, coroutines ----------------------------------------------------
    def op_GET_ITER(self, node, st):
        st.push(IterValue(self.pop_expr(st, node)))

    def op_GET_AITER(self, node, st):
        st.push(IterValue(self.pop_expr(st, node), is_async=True))

    def op_GET_YIELD_FROM_ITER(self, node, st):
        st.push(YieldFromIter(self.pop_expr(st, node)))

    def op_GET_AWAITABLE(self, node, st):
        st.push(AwaitableValue(self.pop_expr(st, node)))

    def op_YIELD_VALUE(self, node, st):
        value = self.pop_expr(st, node)
        if isinstance(value, ast.Constant) and value.value is None:
            value = None
        st.push(ast.Yield(value=value))

    def op_YIELD_FROM(self, node, st):
        sent = self.pop_expr(st, node)
        if not (isinstance(sent, ast.Constant) and sent.value is None):
            raise DecompileError('YIELD_FROM without None on top of the stack', node)
        value = self.pop_expr(st, node)
        if isinstance(value, AwaitableValue):
            inner = value.value
            if getattr(inner, 'implicit_await', False):
                # an asynchronous comprehension: awaiting it is part of the comprehension syntax
                inner.implicit_await = False
                st.push(inner)
            else:
                st.push(ast.Await(value=inner))
        elif isinstance(value, YieldFromIter):
            st.push(ast.YieldFrom(value=value.value))
        else:
            raise DecompileError('YIELD_FROM on a value that is neither awaited nor iterated', node)
