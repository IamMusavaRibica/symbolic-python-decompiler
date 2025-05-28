import ast

from decompiler import Decompiler
from decompiler.utils.bytecode import compile_without_peephole__

code = '''
import abcd.efgh as xyz
from ..a.b.c import d, e as f
raise
raise abc
raise abc from xyz

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

'''

cc = compile(code, '<test>', 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)

d = Decompiler(cc)
d.decompile()
print(d.get_source_code())
