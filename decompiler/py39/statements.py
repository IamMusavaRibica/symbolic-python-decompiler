"""Instructions that finish statements: stores, deletes, imports, return, raise."""
from __future__ import annotations

import ast

from decompiler.errors import DecompileError
from decompiler.py39.context import TERMINATED
from decompiler.py39.values import (Accumulator, ClassValue, FunctionValue, ImportFromValue, ImportValue,
                                    InplaceValue, Marker, TargetMarker, Unpack, UnpackItem)


class ChainedTarget(ast.NamedExpr):
    """``value`` was stored to ``target`` and is still on the stack.

    This is what both ``a = b = value`` and ``(a := value)`` look like; which
    of the two it is shows when the remaining copy is used.
    """


class StatementMixin:
    # -- stores ------------------------------------------------------------------
    def _store_name(self, node, st):
        if not st.stack:
            raise DecompileError('stack underflow', node)
        self.store(st, ast.Name(id=node.argval, ctx=ast.Store()), st.pop(), node)

    op_STORE_NAME = op_STORE_GLOBAL = op_STORE_FAST = op_STORE_DEREF = _store_name

    def op_STORE_ATTR(self, node, st):
        obj = self.pop_expr(st, node)
        if not st.stack:
            raise DecompileError('stack underflow', node)
        self.store(st, ast.Attribute(value=obj, attr=node.argval, ctx=ast.Store()), st.pop(), node)

    def op_STORE_SUBSCR(self, node, st):
        index = self.pop_expr(st, node)
        obj = self.pop_expr(st, node)
        if not st.stack:
            raise DecompileError('stack underflow', node)
        value = st.pop()
        if self.is_annotation_store(obj, index, value):
            return self.annotate(st, index.value, value)
        self.store(st, ast.Subscript(value=obj, slice=index, ctx=ast.Store()), value, node)

    def store(self, st, target: ast.expr, value, node):
        """``target`` receives ``value`` (which has been popped)."""
        if isinstance(value, UnpackItem):
            unpack = value.unpack
            if unpack.fill(value.index, target):
                self.store(st, unpack.as_target(), unpack.source, node)
            return
        if isinstance(value, TargetMarker):
            value.target = target
            return
        if isinstance(value, Marker) or not isinstance(value, ast.expr):
            raise DecompileError(f'cannot store {value!r}', node)
        if isinstance(value, (ImportValue, ImportFromValue)):
            return self.store_import(st, target, value, node)
        if isinstance(value, InplaceValue):
            st.emit(ast.AugAssign(target=target, op=value.op, value=value.right))
            return
        if st.stack and st.stack[-1] is value:
            # DUP_TOP + store: chained assignment or assignment expression
            st.stack[-1] = ChainedTarget(target=target, value=value)
            return
        definition = self.as_definition(target, value, node)
        if definition is not None:
            st.emit(definition)
            return
        targets = [target]
        while isinstance(value, ChainedTarget):
            targets.insert(0, value.target)
            value = value.value
        if st.depth > 0:
            # 'a, b = x, y' is compiled without building a tuple: the values are
            # rotated and stored one after the other
            if len(targets) != 1:
                raise DecompileError('unsupported assignment', node)
            st.pending.append((target, value))
            return
        if st.pending:
            st.pending.append((target, value))
            pairs, st.pending = st.pending, []
            st.emit(ast.Assign(targets=[ast.Tuple(elts=[t for t, _ in pairs], ctx=ast.Store())],
                               value=ast.Tuple(elts=[v for _, v in pairs], ctx=ast.Load())))
            return
        st.emit(ast.Assign(targets=targets, value=value))

    def _unpack(self, node, st, count, star):
        if not st.stack:
            raise DecompileError('stack underflow', node)
        source = st.pop()
        unpack = Unpack(source, count, star)
        if count == 0:
            return self.store(st, unpack.as_target(), source, node)
        for index in reversed(range(count)):
            st.push(UnpackItem(unpack, index))

    def op_UNPACK_SEQUENCE(self, node, st):
        self._unpack(node, st, node.arg, -1)

    def op_UNPACK_EX(self, node, st):
        before, after = node.arg & 0xFF, node.arg >> 8
        self._unpack(node, st, before + 1 + after, before)

    # -- variable annotations --------------------------------------------------------
    def op_SETUP_ANNOTATIONS(self, node, st):
        pass

    def is_annotation_store(self, obj, index, value) -> bool:
        """``__annotations__['name'] = annotation`` generated for ``name: annotation``."""
        return (self.kind in ('module', 'class') and self.has_annotations
                and isinstance(obj, ast.Name) and obj.id == '__annotations__'
                and isinstance(index, ast.Constant) and isinstance(index.value, str) and index.value.isidentifier()
                and isinstance(value, ast.expr) and not isinstance(value, ChainedTarget))

    def annotate(self, st, name: str, annotation: ast.expr):
        annotation = self.annotation(annotation)
        value = None
        if st.stmts:
            previous = st.stmts[-1]
            if (isinstance(previous, ast.Assign) and len(previous.targets) == 1
                    and isinstance(previous.targets[0], ast.Name) and previous.targets[0].id == name):
                value = previous.value
                st.stmts.pop()
        st.emit(ast.AnnAssign(target=ast.Name(id=name, ctx=ast.Store()), annotation=annotation, value=value,
                              simple=1))

    # -- deletes ---------------------------------------------------------------------
    def _delete_name(self, node, st):
        st.emit(ast.Delete(targets=[ast.Name(id=node.argval, ctx=ast.Del())]))

    op_DELETE_NAME = op_DELETE_GLOBAL = op_DELETE_FAST = op_DELETE_DEREF = _delete_name

    def op_DELETE_ATTR(self, node, st):
        obj = self.pop_expr(st, node)
        st.emit(ast.Delete(targets=[ast.Attribute(value=obj, attr=node.argval, ctx=ast.Del())]))

    def op_DELETE_SUBSCR(self, node, st):
        index = self.pop_expr(st, node)
        obj = self.pop_expr(st, node)
        st.emit(ast.Delete(targets=[ast.Subscript(value=obj, slice=index, ctx=ast.Del())]))

    # -- expression statements ---------------------------------------------------------
    def op_POP_TOP(self, node, st):
        value = st.pop()
        if isinstance(value, ImportValue):
            if value.fromlist is not None and not value.done:
                value.done = True
                st.emit(ast.ImportFrom(module=value.name or None, names=value.names, level=value.level))
            return
        if isinstance(value, ImportFromValue):
            return
        if not isinstance(value, ast.expr):
            raise DecompileError(f'POP_TOP discards {value!r}', node)
        if st.depth != 0:
            raise DecompileError('value discarded in the middle of an expression', node)
        st.emit(ast.Expr(value=value))

    def op_PRINT_EXPR(self, node, st):
        st.emit(ast.Expr(value=self.pop_expr(st, node)))

    # -- imports -------------------------------------------------------------------------
    def op_IMPORT_NAME(self, node, st):
        fromlist = self.pop_expr(st, node)
        level = self.pop_expr(st, node)
        if not isinstance(fromlist, ast.Constant) or not isinstance(level, ast.Constant):
            raise DecompileError('IMPORT_NAME with computed arguments', node)
        st.push(ImportValue(node.argval, level.value, fromlist.value))

    def op_IMPORT_FROM(self, node, st):
        parent = st.stack[-1] if st.stack else None
        if not isinstance(parent, (ImportValue, ImportFromValue)):
            raise DecompileError('IMPORT_FROM without a module on the stack', node)
        st.push(ImportFromValue(parent, node.argval))

    def op_IMPORT_STAR(self, node, st):
        module = st.pop() if st.stack else None
        if not isinstance(module, ImportValue):
            raise DecompileError('IMPORT_STAR without a module on the stack', node)
        module.done = True
        st.emit(ast.ImportFrom(module=module.name or None, names=[ast.alias(name='*', asname=None)],
                               level=module.level))

    def store_import(self, st, target, value, node):
        if not isinstance(target, ast.Name):
            raise DecompileError('import bound to something that is not a name', node)
        root = value if isinstance(value, ImportValue) else value.root
        if root.fromlist is None:
            # import a.b.c [as d]
            if isinstance(value, ImportValue):
                top = root.name.partition('.')[0]
                asname = None if target.id == top else target.id
            else:
                asname = target.id
            root.done = True
            st.emit(ast.Import(names=[ast.alias(name=root.name, asname=asname)]))
        else:
            # from module import name [as target]
            if not isinstance(value, ImportFromValue) or value.parent is not root:
                raise DecompileError('unsupported import', node)
            root.names.append(ast.alias(name=value.attr, asname=None if target.id == value.attr else target.id))

    # -- return, raise ---------------------------------------------------------------------
    def op_RETURN_VALUE(self, node, st):
        if not st.stack:
            raise DecompileError('stack underflow', node)
        value = st.pop()
        if isinstance(value, Accumulator):
            return TERMINATED  # the end of a comprehension
        if not isinstance(value, ast.expr):
            raise DecompileError(f'cannot return {value!r}', node)
        if self.kind in ('module', 'class'):
            return TERMINATED  # the implicit end of the body
        st.emit(self.make_return(value))
        return TERMINATED

    def op_RAISE_VARARGS(self, node, st):
        cause = self.pop_expr(st, node) if node.arg == 2 else None
        exc = self.pop_expr(st, node) if node.arg >= 1 else None
        st.emit(ast.Raise(exc=exc, cause=cause))
        return TERMINATED
