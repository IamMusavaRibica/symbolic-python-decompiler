"""What can sit on the symbolic stack besides ordinary ``ast.expr`` nodes.

Two families:

* *markers* stand for values that never appear in source code (the iterator of
  a ``for`` loop, the exception triple inside a handler, ...).  They only ever
  get popped by the code that knows about the construct they belong to.
* *pseudo expressions* are intermediate results that become real syntax a few
  instructions later (the function object built by ``MAKE_FUNCTION`` becomes a
  ``def`` when it is stored, ``GET_ITER`` on a value becomes a ``for`` loop or
  the argument of a comprehension, ...).  They subclass ``ast.expr`` so they can
  be nested inside real expressions while those are being put together, but
  they must be gone by the time source code is produced.
"""
from __future__ import annotations

import ast
import math
from types import CodeType
from typing import List, Optional


# -- markers ----------------------------------------------------------------
class Marker:
    """A stack entry that is not an expression."""

    def __repr__(self):
        return f'<{type(self).__name__}>'


class ExcMarker(Marker):
    """One of the values pushed when an exception handler is entered."""

    def __init__(self, kind: str):
        self.kind = kind  # 'tb', 'value', 'type' (current) or 'saved'

    def __repr__(self):
        return f'<exc {self.kind}>'


class IterMarker(Marker):
    """The iterator a ``for`` loop keeps on the stack while it runs."""


class WithExitMarker(Marker):
    """The bound ``__exit__`` a ``with`` statement keeps on the stack."""


class Accumulator(Marker):
    """The list/set/dict a comprehension is filling."""

    def __init__(self, kind: str):
        self.kind = kind  # 'list', 'set', 'dict'


class NullMarker(Marker):
    """The extra slot LOAD_METHOD pushes below the method."""


class TargetMarker(Marker):
    """A value produced by the construct itself whose only use is to be stored.

    The loop variable of a ``for``, the ``as`` value of a ``with``, the caught
    exception of an ``except``: whatever target the value is stored to is
    recorded in ``target``.
    """

    def __init__(self, what: str):
        self.what = what
        self.target: Optional[ast.expr] = None

    def __repr__(self):
        return f'<target of {self.what}>'


class Unpack:
    """Bookkeeping for UNPACK_SEQUENCE / UNPACK_EX: collects one target per item."""

    def __init__(self, source, count: int, star: int = -1):
        self.source = source  # the value that was unpacked (expression or marker)
        self.targets: List[Optional[ast.expr]] = [None] * count
        self.star = star  # index of the starred target, -1 if none
        self.missing = count

    def fill(self, index: int, target: ast.expr) -> bool:
        """Record a target; True once every item has one."""
        if index == self.star:
            target = ast.Starred(value=target, ctx=ast.Store())
        self.targets[index] = target
        self.missing -= 1
        return self.missing == 0

    def as_target(self) -> ast.Tuple:
        return ast.Tuple(elts=list(self.targets), ctx=ast.Store())


class UnpackItem(Marker):
    def __init__(self, unpack: Unpack, index: int):
        self.unpack = unpack
        self.index = index

    def __repr__(self):
        return f'<unpack item {self.index}>'


# -- pseudo expressions -------------------------------------------------------
class Pseudo(ast.expr):
    """Base class of intermediate values; never valid in the final AST."""
    _fields = ()


class CodeValue(Pseudo):
    """LOAD_CONST of a code object."""

    def __init__(self, code: CodeType):
        super().__init__()
        self.code = code


class ClosureCell(Pseudo):
    """LOAD_CLOSURE."""

    def __init__(self, name: str):
        super().__init__()
        self.name = name


class IterValue(Pseudo):
    """GET_ITER / GET_AITER applied to ``value``."""

    def __init__(self, value: ast.expr, is_async: bool = False):
        super().__init__()
        self.value = value
        self.is_async = is_async


class AwaitableValue(Pseudo):
    """GET_AWAITABLE applied to ``value``."""

    def __init__(self, value: ast.expr):
        super().__init__()
        self.value = value


class YieldFromIter(Pseudo):
    """GET_YIELD_FROM_ITER applied to ``value``."""

    def __init__(self, value: ast.expr):
        super().__init__()
        self.value = value


class InplaceValue(Pseudo):
    """``left op= right`` before it has been stored back."""

    def __init__(self, left: ast.expr, op: ast.operator, right: ast.expr):
        super().__init__()
        self.left = left
        self.op = op
        self.right = right


class MethodValue(Pseudo):
    """LOAD_METHOD: ``value.attr`` about to be called."""

    def __init__(self, value: ast.expr, attr: str):
        super().__init__()
        self.value = value
        self.attr = attr


class FunctionValue(Pseudo):
    """The result of MAKE_FUNCTION."""

    def __init__(self, code: CodeType, qualname: str, defaults=None, kwdefaults=None, annotations=None,
                 closure=None):
        super().__init__()
        self.code = code
        self.qualname = qualname
        self.defaults = defaults
        self.kwdefaults = kwdefaults
        self.annotations = annotations
        self.closure = closure


class BuildClass(Pseudo):
    """LOAD_BUILD_CLASS."""


class ClassValue(Pseudo):
    """``__build_class__(body, name, *bases, **keywords)``."""

    def __init__(self, function: FunctionValue, name: str, bases: List[ast.expr], keywords: List[ast.keyword]):
        super().__init__()
        self.function = function
        self.name = name
        self.bases = bases
        self.keywords = keywords


class ImportValue(Pseudo):
    """IMPORT_NAME: the module object of an import statement in progress."""

    def __init__(self, name: str, level: int, fromlist):
        super().__init__()
        self.name = name
        self.level = level
        self.fromlist = fromlist  # None or a tuple of names
        self.names: List[ast.alias] = []  # ``from`` import: aliases collected so far
        self.done = False  # the statement has been emitted


class ImportFromValue(Pseudo):
    """IMPORT_FROM: an attribute of the module (or of a submodule for ``import a.b as c``)."""

    def __init__(self, parent, attr: str):
        super().__init__()
        self.parent = parent  # ImportValue or ImportFromValue
        self.attr = attr

    @property
    def root(self) -> ImportValue:
        value = self
        while isinstance(value, ImportFromValue):
            value = value.parent
        return value

    def path(self) -> List[str]:
        parts = []
        value = self
        while isinstance(value, ImportFromValue):
            parts.append(value.attr)
            value = value.parent
        return parts[::-1]


class AssertionErrorValue(Pseudo):
    """LOAD_ASSERTION_ERROR."""


class FStringPart(ast.JoinedStr):
    """The result of a single FORMAT_VALUE: an f-string with exactly one replacement field.

    It is a real JoinedStr (so it is valid wherever it ends up); BUILD_STRING
    recognises the subclass and splices the field into the string it builds.
    """


class ConstSet(ast.Set):
    """A frozenset constant written as a set display (only correct where the compiler folds it back)."""


def is_plain(value) -> bool:
    """True for a finished expression that could be written in source code as is."""
    return isinstance(value, ast.expr) and not isinstance(value, Pseudo)


def const_to_ast(value, expand_tuples: bool = False) -> ast.expr:
    """The expression that loads the constant ``value``.

    Tuples normally stay a single ``ast.Constant`` (the stack machine looks at
    their value); ``expand_tuples`` turns them into tuple displays, which is
    what gets written to source code (the compiler folds them back).
    """
    from decompiler.graph.dump import const_key
    if isinstance(value, CodeType):
        return CodeValue(value)
    if isinstance(value, frozenset):
        return ConstSet(elts=[const_to_ast(v, expand_tuples) for v in sorted(value, key=const_key)])
    if isinstance(value, tuple) and expand_tuples:
        return ast.Tuple(elts=[const_to_ast(v, True) for v in value], ctx=ast.Load())
    if type(value) in (int, float) and math.copysign(1, value) < 0:
        # written as '-<literal>' (the compiler folds it back), so that it gets parentheses
        # where it needs them: (-1) ** x, (-1).real
        return ast.UnaryOp(op=ast.USub(), operand=ast.Constant(value=-value))
    if type(value) is complex:
        return _complex_to_ast(value)
    return ast.Constant(value=value)


def _complex_to_ast(value: complex) -> ast.expr:
    """An expression of literals that the compiler folds to exactly ``value``, signed zeros included."""
    negative = lambda x: math.copysign(1, x) < 0
    real, imag = value.real, value.imag
    if real == 0 and not negative(real):
        if not negative(imag):
            return ast.Constant(value=complex(0.0, imag))  # 2j
        # 0.0 - 2j
        return ast.BinOp(left=ast.Constant(value=0.0), op=ast.Sub(), right=ast.Constant(value=complex(0.0, -imag)))
    if real == 0:
        if negative(imag):
            return ast.UnaryOp(op=ast.USub(), operand=ast.Constant(value=complex(0.0, -imag)))  # -2j
        # -(0.0 - 2j)
        return ast.UnaryOp(op=ast.USub(), operand=_complex_to_ast(complex(0.0, -imag)))
    if imag == 0 and negative(imag):
        # -(-1.0 + 0j)
        return ast.UnaryOp(op=ast.USub(), operand=_complex_to_ast(complex(-real, 0.0)))
    op = ast.Sub() if negative(imag) else ast.Add()
    return ast.BinOp(left=const_to_ast(real), op=op, right=ast.Constant(value=complex(0.0, abs(imag))))
