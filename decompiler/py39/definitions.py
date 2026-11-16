"""Functions, lambdas, classes and comprehensions: everything that owns a nested code object."""
from __future__ import annotations

import ast
from typing import Dict, List, Optional

from decompiler.errors import DecompileError
from decompiler.graph.dump import (CO_ASYNC_GENERATOR, CO_COROUTINE, CO_FUTURE_ANNOTATIONS, CO_VARARGS,
                                   CO_VARKEYWORDS)
from decompiler.py39.values import ClassValue, CodeValue, FunctionValue, IterValue, Pseudo

COMPREHENSIONS = {
    '<listcomp>': ast.ListComp,
    '<setcomp>': ast.SetComp,
    '<dictcomp>': ast.DictComp,
    '<genexpr>': ast.GeneratorExp,
}


class ComprehensionElement(ast.stmt):
    """Pseudo statement: the comprehension produces ``value`` (for ``key`` in a dict comprehension)."""
    _fields = ()

    def __init__(self, value: ast.expr, key: Optional[ast.expr] = None):
        super().__init__()
        self.value = value
        self.key = key


class DefinitionMixin:
    def op_MAKE_FUNCTION(self, node, st):
        qualname = self.pop_expr(st, node)
        code = self.pop_expr(st, node)
        if not isinstance(code, CodeValue) or not isinstance(qualname, ast.Constant):
            raise DecompileError('MAKE_FUNCTION without a code object and a name', node)
        closure = self.pop_expr(st, node) if node.arg & 8 else None
        annotations = self.pop_expr(st, node) if node.arg & 4 else None
        kwdefaults = self.pop_expr(st, node) if node.arg & 2 else None
        defaults = self.pop_expr(st, node) if node.arg & 1 else None
        function = FunctionValue(code.code, qualname.value, defaults, kwdefaults, annotations, closure)
        if code.code.co_name == '<lambda>':
            st.push(self.make_lambda(node, function))
        else:
            st.push(function)

    # -- signatures --------------------------------------------------------------------
    def annotation(self, value: ast.expr) -> ast.expr:
        """Under ``from __future__ import annotations`` an annotation is the string of its source."""
        if self.code.co_flags & CO_FUTURE_ANNOTATIONS and isinstance(value, ast.Constant) \
                and isinstance(value.value, str):
            try:
                return ast.parse(value.value, mode='eval').body
            except SyntaxError:
                pass
        return value

    def make_arguments(self, node, function: FunctionValue):
        """(ast.arguments, return annotation) of the function built by MAKE_FUNCTION."""
        code = function.code
        annotations: Dict[str, ast.expr] = {}
        if function.annotations is not None:
            if type(function.annotations) is not ast.Dict:
                raise DecompileError('unsupported function annotations', node)
            for key, value in zip(function.annotations.keys, function.annotations.values):
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    raise DecompileError('unsupported function annotations', node)
                annotations[key.value] = self.annotation(value)

        def arg(name):
            return ast.arg(arg=name, annotation=annotations.get(name))

        names = code.co_varnames
        positional = names[:code.co_argcount]
        index = code.co_argcount + code.co_kwonlyargcount
        kwonly = names[code.co_argcount:index]
        vararg = kwarg = None
        if code.co_flags & CO_VARARGS:
            vararg = arg(names[index])
            index += 1
        if code.co_flags & CO_VARKEYWORDS:
            kwarg = arg(names[index])

        defaults: List[ast.expr] = []
        if function.defaults is not None:
            if type(function.defaults) is ast.Tuple:
                defaults = list(function.defaults.elts)
            elif isinstance(function.defaults, ast.Constant) and isinstance(function.defaults.value, tuple):
                defaults = [self.const(v) for v in function.defaults.value]
            else:
                raise DecompileError('unsupported default arguments', node)
        kw_defaults: List[Optional[ast.expr]] = [None] * len(kwonly)
        if function.kwdefaults is not None:
            if type(function.kwdefaults) is not ast.Dict:
                raise DecompileError('unsupported keyword-only defaults', node)
            by_name = {k.value: v for k, v in zip(function.kwdefaults.keys, function.kwdefaults.values)}
            kw_defaults = [by_name.get(name) for name in kwonly]
        arguments = ast.arguments(
            posonlyargs=[arg(n) for n in positional[:code.co_posonlyargcount]],
            args=[arg(n) for n in positional[code.co_posonlyargcount:]],
            vararg=vararg, kwonlyargs=[arg(n) for n in kwonly], kw_defaults=kw_defaults, kwarg=kwarg,
            defaults=defaults)
        return arguments, annotations.get('return')

    # -- lambda, def, class ---------------------------------------------------------------
    def make_lambda(self, node, function: FunctionValue) -> ast.Lambda:
        arguments, _ = self.make_arguments(node, function)
        body = self.stmts_to_expr(self.decompiler.body_of(function.code, 'lambda'))
        if body is None:
            raise DecompileError('the body of a lambda is not an expression', node)
        return ast.Lambda(args=arguments, body=body)

    @staticmethod
    def _definition_in(value) -> bool:
        while type(value) is ast.Call and len(value.args) == 1 and not value.keywords:
            value = value.args[0]
        return isinstance(value, (FunctionValue, ClassValue))

    def as_definition(self, target, value, node) -> Optional[ast.stmt]:
        """``def`` / ``class`` statement for a stored function or class object (with its decorators)."""
        if not self._definition_in(value):
            return None
        decorators = []
        while type(value) is ast.Call:
            decorators.append(value.func)
            value = value.args[0]
        if not isinstance(target, ast.Name):
            raise DecompileError('a function or class is bound to something that is not a name', node)
        if isinstance(value, ClassValue):
            body = self.decompiler.body_of(value.function.code, 'class')
            return ast.ClassDef(name=value.name, bases=value.bases, keywords=value.keywords, body=body,
                                decorator_list=decorators)
        code = value.code
        if code.co_name in COMPREHENSIONS:
            raise DecompileError('a comprehension is used without being called', node)
        arguments, returns = self.make_arguments(node, value)
        body = self.decompiler.body_of(code, 'function')
        is_async = code.co_flags & (CO_COROUTINE | CO_ASYNC_GENERATOR)
        cls = ast.AsyncFunctionDef if is_async else ast.FunctionDef
        return cls(name=code.co_name, args=arguments, body=body, decorator_list=decorators, returns=returns)

    def make_class(self, node, args, keywords) -> ClassValue:
        if len(args) < 2 or not isinstance(args[0], FunctionValue) or not isinstance(args[1], ast.Constant):
            raise DecompileError('unsupported class definition', node)
        return ClassValue(args[0], args[1].value, args[2:], keywords)

    # -- comprehensions ----------------------------------------------------------------------
    def comprehension_element(self, node, st, value, key=None):
        st.emit(ComprehensionElement(value, key))

    def make_comprehension(self, node, function: FunctionValue, args, keywords) -> ast.expr:
        code = function.code
        cls = COMPREHENSIONS.get(code.co_name)
        if cls is None or len(args) != 1 or keywords or not isinstance(args[0], IterValue):
            # a decorator applied to a function, or something similar: an ordinary call
            return ast.Call(func=function, args=args, keywords=keywords)
        stmts = self.decompiler.body_of(code, 'comprehension')
        generators: List[ast.comprehension] = []
        element = None
        while True:
            if stmts and isinstance(stmts[-1], ast.Return) and stmts[-1].value is None:
                stmts = stmts[:-1]
            if len(stmts) > 1 and isinstance(stmts[0], ast.Assign) and len(stmts[0].targets) == 1 and generators:
                # 'for y in [expr]' is compiled as the assignment 'y = expr'
                assign = stmts[0]
                generators.append(ast.comprehension(target=assign.targets[0],
                                                    iter=ast.List(elts=[assign.value], ctx=ast.Load()),
                                                    ifs=[], is_async=0))
                stmts = stmts[1:]
                continue
            if len(stmts) > 1 and isinstance(stmts[0], ast.If) and not stmts[0].orelse and generators \
                    and len(stmts[0].body) == 1 and isinstance(stmts[0].body[0], ast.Pass):
                # a test whose outcome does not matter: 'if (test or True)'
                always = ast.BoolOp(op=ast.Or(), values=[stmts[0].test, ast.Constant(value=True)])
                generators[-1].ifs.append(always)
                stmts = stmts[1:]
                continue
            if len(stmts) != 1:
                break
            stmt = stmts[0]
            if isinstance(stmt, (ast.For, ast.AsyncFor)) and not stmt.orelse:
                generators.append(ast.comprehension(target=stmt.target, iter=stmt.iter, ifs=[],
                                                    is_async=int(isinstance(stmt, ast.AsyncFor))))
                stmts = stmt.body
            elif isinstance(stmt, ast.If) and not stmt.orelse and generators:
                generators[-1].ifs.append(stmt.test)
                stmts = stmt.body
            elif isinstance(stmt, ComprehensionElement):
                element = stmt
                break
            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Yield) and cls is ast.GeneratorExp:
                element = ComprehensionElement(stmt.value.value or ast.Constant(value=None))
                break
            else:
                break
        if element is None or not generators:
            raise DecompileError(f'unsupported {code.co_name}', node)
        first = generators[0].iter
        if not (isinstance(first, ast.Name) and first.id == '.0'):
            raise DecompileError(f'unsupported {code.co_name}', node)
        generators[0].iter = args[0].value
        if cls is ast.DictComp:
            result = ast.DictComp(key=element.key, value=element.value, generators=generators)
        else:
            result = cls(elt=element.value, generators=generators)
        if code.co_flags & CO_COROUTINE and cls is not ast.GeneratorExp:
            result.implicit_await = True
        return result
