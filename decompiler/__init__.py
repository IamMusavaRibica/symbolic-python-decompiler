"""Python 3.9 bytecode decompiler.

    >>> from decompiler import decompile
    >>> print(decompile(compile('x = a if b else c', '<demo>', 'exec')))
    x = a if b else c

The decompiler must run on the Python version whose bytecode it reads
(currently 3.9 only).  See ``readme.md`` for how it works.
"""
from __future__ import annotations

import ast
import sys
import threading
from types import CodeType
from typing import List

from loguru import logger

from decompiler.emit import keep_on_one_line, peephole_skipped
from decompiler.errors import DecompileError
from decompiler.graph.dump import (CO_ASYNC_GENERATOR, CO_GENERATOR, docstring_of, dump_code)

logger.disable('decompiler')

__all__ = ['Decompiler', 'DecompileError', 'decompile', 'dump_code']

#: opcodes that only exist for names declared 'global' (in functions LOAD_GLOBAL is the normal case)
_GLOBAL_OPS = frozenset({'STORE_GLOBAL', 'DELETE_GLOBAL'})
_NONLOCAL_OPS = frozenset({'STORE_DEREF', 'DELETE_DEREF'})
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _walker_class():
    version = sys.version_info[:2]
    if version != (3, 9):
        raise RuntimeError(f'only Python 3.9 bytecode is supported, this is Python {version[0]}.{version[1]}')
    from decompiler.py39.walker import Walker
    return Walker


#: name of the call that stands in for a function or class body that could not be decompiled
FAILURE_MARKER = '__decompilation_failed__'


class Decompiler:
    """Decompiles one code object (normally that of a module).

    With ``strict`` (the default) the first problem raises DecompileError.
    Without it, a function or class body that cannot be decompiled is replaced
    by a call to ``__decompilation_failed__('reason')`` so that the rest of the
    module is still produced; ``failures`` lists what was skipped.
    """

    def __init__(self, code: CodeType, toplevel: bool = True, strict: bool = True, peephole: bool = None):
        self.code = code
        self.toplevel = toplevel
        self.strict = strict
        #: whether the code went through the peephole optimizer (guessed from the code when not given);
        #: only matters for a few spellings that compile differently without it
        self.peephole = _looks_optimized(code) if peephole is None else peephole
        self.tree: ast.Module = None
        #: (qualified name of the code object, error) for every body that was skipped
        self.failures: List[tuple] = []
        self._walker_class = _walker_class()
        self._path: List[str] = []

    # -- public ----------------------------------------------------------------
    def decompile(self) -> ast.Module:
        """Decompile the code object into an ``ast.Module``."""
        from decompiler.postprocess import finalize
        kind = 'module' if self.code.co_name == '<module>' else 'function'
        body = _run_deep(lambda: self.body_of(self.code, kind))
        self.tree = _run_deep(lambda: ast.fix_missing_locations(finalize(ast.Module(body=body, type_ignores=[]))))
        return self.tree

    def get_source_code(self) -> str:
        if self.tree is None:
            self.decompile()
        from decompiler.emit import unparse
        return _run_deep(lambda: unparse(self.tree))

    # -- used by the walkers -----------------------------------------------------
    def body_of(self, code: CodeType, kind: str) -> List[ast.stmt]:
        """The statements of a code object; ``kind`` says what the code object is the body of."""
        self._path.append(code.co_name)
        try:
            return self._body_of(code, kind)
        except Exception as e:
            if self.strict or kind not in ('function', 'class') or len(self._path) == 1:
                raise
            if not isinstance(e, (DecompileError, RecursionError)):
                e = DecompileError(f'internal error: {type(e).__name__}: {e}')
            self.failures.append(('.'.join(self._path), e))
            logger.warning('skipped {}: {}', '.'.join(self._path), e)
            reason = ast.Constant(value=str(e))
            return [ast.Expr(value=ast.Call(func=ast.Name(id=FAILURE_MARKER, ctx=ast.Load()), args=[reason],
                                            keywords=[]))]
        finally:
            self._path.pop()

    def _body_of(self, code: CodeType, kind: str) -> List[ast.stmt]:
        walker = self._walker_class(self, code, kind)
        logger.debug('decompiling {} {}', kind, code.co_name)
        stmts = walker.run()
        if kind == 'function':
            stmts = self._function_body(code, walker, stmts)
        elif kind == 'class':
            stmts = self._class_body(walker, stmts)
        elif kind == 'module':
            stmts = self._module_body(walker, stmts)
        stmts = self._restore_annotations(walker, stmts, kind)
        if self.peephole and peephole_skipped(code):
            # compiled from a line so long that the optimizer gave up: it has to stay that long
            keep_on_one_line(stmts)
        return stmts

    def _function_body(self, code, walker, stmts):
        if stmts and isinstance(stmts[-1], ast.Return) and stmts[-1].value is None:
            stmts = stmts[:-1]  # the implicit 'return None' at the end
        head: List[ast.stmt] = []
        doc = docstring_of(code)
        if doc is not None:
            head.append(ast.Expr(value=ast.Constant(value=doc)))
        head.extend(self._declarations(walker, 'function'))
        head.extend(self._silent_scope_facts(code, walker, stmts))
        return head + stmts or [ast.Pass()]

    @staticmethod
    def _silent_scope_facts(code, walker, stmts) -> List[ast.stmt]:
        """Statements that compile to no instructions but restore what unreachable code decided.

        Code the compiler dropped (after a ``return``, under ``if 0:``) still
        took part in deciding which names are local, which are shared with
        nested functions and whether the function is a generator.
        """
        result: List[ast.stmt] = []
        nodes = walker.graph.nodes
        nargs = code.co_argcount + code.co_kwonlyargcount + bool(code.co_flags & 0x04) + bool(code.co_flags & 0x08)
        bound = {n.argval for n in nodes if n.opname in ('STORE_FAST', 'DELETE_FAST', 'STORE_DEREF', 'DELETE_DEREF')}
        bound.update(code.co_varnames[:nargs])
        for name in list(code.co_varnames[nargs:]) + list(code.co_cellvars):
            if name not in bound and name.isidentifier():
                # a local variable that is never assigned: an annotation makes it local without code
                bound.add(name)
                result.append(ast.AnnAssign(target=ast.Name(id=name, ctx=ast.Store()),
                                            annotation=ast.Name(id='object', ctx=ast.Load()), value=None, simple=1))
        captured = {n.argval for n in nodes if n.opname == 'LOAD_CLOSURE'}
        hidden = [name for name in code.co_cellvars if name not in captured and name.isidentifier()]
        dead: List[ast.stmt] = []
        if hidden:
            # variables shared with a nested function that is never created
            names = ast.Tuple(elts=[ast.Name(id=name, ctx=ast.Load()) for name in hidden], ctx=ast.Load())
            no_args = ast.arguments(posonlyargs=[], args=[], vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None,
                                    defaults=[])
            dead.append(ast.Expr(value=ast.Lambda(args=no_args, body=names)))
        if code.co_flags & (CO_GENERATOR | CO_ASYNC_GENERATOR) and not _yields(stmts):
            # a generator whose 'yield' is unreachable
            dead.append(ast.Expr(value=ast.Yield(value=None)))
        if dead:
            result.append(ast.If(test=ast.Constant(value=False), body=dead, orelse=[]))
        return result

    def _class_body(self, walker, stmts):
        # every class body starts with '__module__ = __name__' and '__qualname__ = ...'
        if stmts and _is_assign(stmts[0], '__module__') and isinstance(stmts[0].value, ast.Name) \
                and stmts[0].value.id == '__name__':
            stmts = stmts[1:]
        if stmts and _is_assign(stmts[0], '__qualname__') and isinstance(stmts[0].value, ast.Constant):
            stmts = stmts[1:]
        stmts = _docstring_first(stmts)
        head = [s for s in stmts[:1] if _is_docstring(s)]
        return head + self._declarations(walker, 'class') + stmts[len(head):] or [ast.Pass()]

    def _module_body(self, walker, stmts):
        stmts = _docstring_first(stmts)
        # declarations go after the docstring and the 'from __future__' imports, which must come first
        count = 1 if stmts[:1] and _is_docstring(stmts[0]) else 0
        while count < len(stmts) and isinstance(stmts[count], ast.ImportFrom) and stmts[count].module == '__future__':
            count += 1
        return stmts[:count] + self._declarations(walker, 'module') + stmts[count:]

    @staticmethod
    def _restore_annotations(walker, stmts, kind) -> List[ast.stmt]:
        """Annotations of non-simple targets (``x.y: int``, ``(z): int``) leave almost no trace.

        The target's sub-expressions and, in a module or class body, the annotation (even under
        ``from __future__ import annotations``) are evaluated and discarded, and the body gets a
        ``SETUP_ANNOTATIONS``.  Both are
        restored, onto whatever statement can carry them: a discarded constant can only
        come from an annotation (a constant statement produces no code), and a body with
        ``SETUP_ANNOTATIONS`` but no simple annotated name had an annotated non-simple target.
        The result is correct, but the annotation does not land where the author put it.
        """
        def annotated(body, index, stmt):
            if kind == 'function':
                # the attribute base is evaluated and discarded; the annotation is not evaluated
                return ast.AnnAssign(target=ast.Attribute(value=stmt.value, attr='_', ctx=ast.Store()),
                                     annotation=ast.Constant(value=0), value=None, simple=0)
            previous = body[index - 1] if index else None
            if isinstance(previous, ast.Assign) and len(previous.targets) == 1 \
                    and isinstance(previous.targets[0], (ast.Attribute, ast.Subscript)):
                # 'x.y: ann = v' is 'x.y = v' followed by 'ann'
                body[index - 1] = None
                return ast.AnnAssign(target=previous.targets[0], annotation=stmt.value, value=previous.value,
                                     simple=0)
            return ast.AnnAssign(target=ast.Name(id='_', ctx=ast.Store()), annotation=stmt.value, value=None,
                                 simple=0)

        bodies = list(_bodies(stmts))
        for body in bodies:
            for i, stmt in enumerate(body):
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) \
                        and not (i == 0 and _is_docstring(stmt)):
                    body[i] = annotated(body, i, stmt)
        if kind != 'function' and walker.has_annotations \
                and not any(isinstance(s, ast.AnnAssign) for body in bodies for s in body):
            candidates = [(body, i, s) for body in bodies for i, s in enumerate(body)
                          if isinstance(s, ast.Expr) and not (i == 0 and _is_docstring(s))]
            pairs = [c for c in candidates if isinstance(c[0][c[1] - 1] if c[1] else None, ast.Assign)]
            for body, i, stmt in (pairs or candidates)[:1]:
                body[i] = annotated(body, i, stmt)
        for body in bodies:
            body[:] = [s for s in body if s is not None]
        return stmts

    @staticmethod
    def _declarations(walker, kind) -> List[ast.stmt]:
        """``global`` / ``nonlocal`` statements the code needs to compile to the same instructions."""
        global_ops = _GLOBAL_OPS if kind == 'function' else _GLOBAL_OPS | {'LOAD_GLOBAL'}
        free = set(walker.code.co_freevars)
        global_names, nonlocal_names = [], []
        for node in walker.graph.nodes:
            if node.opname in global_ops and node.argval not in global_names:
                global_names.append(node.argval)
            elif node.opname in _NONLOCAL_OPS and node.argval in free and node.argval not in nonlocal_names:
                nonlocal_names.append(node.argval)
        if kind == 'function':
            # An assignment expression inside a comprehension binds its target in the function.
            # The store instruction is in the comprehension's code, the declaration belongs here.
            for op, name in _comprehension_stores(walker.code):
                if op == 'STORE_GLOBAL' and name not in global_names:
                    global_names.append(name)
                elif op == 'STORE_DEREF' and name in free and name not in nonlocal_names:
                    nonlocal_names.append(name)
            # a free variable no instruction uses can only come from a 'nonlocal' statement
            used = {node.argval for node in walker.graph.nodes
                    if node.opname in ('LOAD_DEREF', 'STORE_DEREF', 'DELETE_DEREF', 'LOAD_CLOSURE', 'LOAD_CLASSDEREF')}
            for name in walker.code.co_freevars:
                if name not in used and name != '__class__' and name not in nonlocal_names:
                    nonlocal_names.append(name)
        result: List[ast.stmt] = []
        if global_names:
            result.append(ast.Global(names=global_names))
        if nonlocal_names:
            result.append(ast.Nonlocal(names=nonlocal_names))
        return result


def decompile(code: CodeType, peephole: bool = None) -> str:
    """Decompile a code object to source code."""
    return Decompiler(code, peephole=peephole).get_source_code()


def _looks_optimized(code: CodeType) -> bool:
    """False when the bytecode has shapes the peephole optimizer never leaves behind."""
    import dis
    jumps = ('JUMP_FORWARD', 'JUMP_ABSOLUTE')
    instructions = list(dis.get_instructions(code))
    by_offset = {i.offset: i for i in instructions}
    for index, instr in enumerate(instructions):
        if instr.opname in jumps:
            target = by_offset.get(instr.argval)
            if target is not None and target.opname in jumps:
                return False  # a jump to a jump would have been threaded
            if index and instructions[index - 1].opname == 'RETURN_VALUE' and not instr.is_jump_target:
                return False  # dead code after a return would have been removed
        elif instr.opname == 'BUILD_TUPLE' and 0 < instr.arg <= index and all(
                i.opname == 'LOAD_CONST' and not i.is_jump_target for i in instructions[index - instr.arg:index]):
            return False  # a tuple of constants would have been folded
    return all(_looks_optimized(c) for c in code.co_consts if isinstance(c, CodeType))


# -- helpers --------------------------------------------------------------------
def _comprehension_stores(code: CodeType):
    """(opname, name) of the stores to outer variables in the comprehensions of ``code``."""
    import dis
    for const in code.co_consts:
        if isinstance(const, CodeType) and const.co_name in ('<listcomp>', '<setcomp>', '<dictcomp>', '<genexpr>'):
            for instr in dis.get_instructions(const):
                if instr.opname in ('STORE_GLOBAL', 'STORE_DEREF'):
                    yield instr.opname, instr.argval
            yield from _comprehension_stores(const)


def _is_assign(stmt, name: str) -> bool:
    return (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == name)


def _bodies(stmts):
    """The statement lists of ``stmts`` and of its compound statements, not those of nested scopes."""
    yield stmts
    for stmt in stmts:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for field in ('body', 'orelse', 'finalbody'):
            inner = getattr(stmt, field, None)
            if isinstance(inner, list):
                yield from _bodies(inner)
        for handler in getattr(stmt, 'handlers', ()):
            yield from _bodies(handler.body)


def _is_docstring(stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def _docstring_first(stmts):
    """A leading ``__doc__ = '...'`` is how module and class docstrings are compiled."""
    if stmts and _is_assign(stmts[0], '__doc__') and isinstance(stmts[0].value, ast.Constant) \
            and isinstance(stmts[0].value.value, str):
        return [ast.Expr(value=stmts[0].value)] + stmts[1:]
    return stmts


def _yields(nodes) -> bool:
    """True when a yield belongs to the function these statements are the body of."""
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Yield, ast.YieldFrom)):
            return True
        if isinstance(node, _SCOPES):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return False


def _run_deep(function):
    """Run ``function`` with room for deep recursion (nesting depth of the code is the recursion depth)."""
    if threading.current_thread().name == 'decompiler-deep':
        return function()
    result = []
    error = []

    def target():
        try:
            result.append(function())
        except BaseException as e:  # re-raised in the caller's thread
            error.append(e)

    old_limit = sys.getrecursionlimit()
    old_size = threading.stack_size()
    sys.setrecursionlimit(max(old_limit, 30000))
    for megabytes in (240, 120, 60, 30):
        try:
            threading.stack_size(megabytes * 1024 * 1024)
            break
        except ValueError:  # not every platform accepts every size
            continue
    try:
        thread = threading.Thread(target=target, name='decompiler-deep')
        thread.start()
        thread.join()
    finally:
        threading.stack_size(old_size)
        sys.setrecursionlimit(old_limit)
    if error:
        raise error[0]
    return result[0]
