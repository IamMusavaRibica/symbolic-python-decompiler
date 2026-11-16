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

    def __init__(self, code: CodeType, toplevel: bool = True, strict: bool = True):
        self.code = code
        self.toplevel = toplevel
        self.strict = strict
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
        return _run_deep(lambda: ast.unparse(self.tree)) + '\n'

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
            return self._function_body(code, walker, stmts)
        if kind == 'class':
            return self._class_body(walker, stmts)
        if kind == 'module':
            return self._module_body(walker, stmts)
        return stmts

    def _function_body(self, code, walker, stmts):
        if stmts and isinstance(stmts[-1], ast.Return) and stmts[-1].value is None:
            stmts = stmts[:-1]  # the implicit 'return None' at the end
        head: List[ast.stmt] = []
        doc = docstring_of(code)
        if doc is not None:
            head.append(ast.Expr(value=ast.Constant(value=doc)))
        head.extend(self._declarations(walker, 'function'))
        if code.co_flags & (CO_GENERATOR | CO_ASYNC_GENERATOR) and not _yields(stmts):
            # a generator whose 'yield' is unreachable: keep one that produces no code
            head.append(ast.If(test=ast.Constant(value=False), body=[ast.Expr(value=ast.Yield(value=None))],
                               orelse=[]))
        return head + stmts or [ast.Pass()]

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
        head = [s for s in stmts[:1] if _is_docstring(s)]
        return head + self._declarations(walker, 'module') + stmts[len(head):]

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


def decompile(code: CodeType) -> str:
    """Decompile a code object to source code."""
    return Decompiler(code).get_source_code()


# -- helpers --------------------------------------------------------------------
def _is_assign(stmt, name: str) -> bool:
    return (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == name)


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
