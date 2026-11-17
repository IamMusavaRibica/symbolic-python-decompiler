import ast

from decompiler import Decompiler
from decompiler.graph.compare import compare_code

code = '''
import abcd.efgh as xyz
from ..a.b.c import d, e as f

# a bare `raise` at module level would make everything after it unreachable,
# and unreachable code is not in the graph, so the raises live in functions
def r1(): raise
def r2(): raise abc
def r3(): raise abc from xyz

a = f'nice {formatted} {string:.{{{x}}}f} {b!r} {c!s} {d!a}'.a
a = b.c(g, k, l)

for a.b, c[d], e[e][f], *g, h in ggrr()()():
    pass  # notice how these turn into a continue

async for x in z:
    pass

# chained with statement will decompile into nested with statements with single target
with a as b, c as d:
    pass

async with v:
    ...

# try-finally statement decompiles into different source code, but equal bytecode
try:
    1 / 0
except NamedException as E1:
    named_handler
except NamedException2 as E2:
    named_handler2
except:
    default_generic_handler
else:
    else_block
finally:
    what_if_we_had_finally_too

________

# a for loop which decompiles into different bytecode, but functionally equivalent
for a in b:
    if test1:
        pass
    else:
        break
    if test2:
        break
else:
    for_else

# `return value` inside except / finally bodies compiles to ROT_FOUR sequences
def g(a):
    for x in a:
        try:
            return x.y
        except KeyError:
            return x.z
        finally:
            cleanup(x)
'''

original = compile(code, '<test>', 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
source = Decompiler(original).get_source_code()
print(source)

recompiled = compile(source, '<test>', 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
result = compare_code(original, recompiled)
print('# graphs are equal' if result.equal else result.diff)
