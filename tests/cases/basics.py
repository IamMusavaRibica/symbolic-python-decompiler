# %% literals and names
a = 1
b = 'text'
c = b'bytes'
d = None
e = ...
f = 1.5
g = 2j
h = (1, 2, 3)
i = -1

# %% expression statement
f(x)
a.b.c
x[1]

# %% binary and unary operators
x = a + b - c * d / e // f % g ** h
y = a << b >> c & d | e ^ f
z = -a + +b + ~c
w = not a
v = a @ b

# %% comparisons
a = b < c
a = b <= c
a = b > c
a = b >= c
a = b == c
a = b != c
a = b is c
a = b is not c
a = b in c
a = b not in c

# %% attribute and subscript stores
a.b = c
a[b] = c
a.b.c[d].e = f
a[1:2] = b
a[::2] = b
a[1:2, 3] = b

# %% slices
x = a[1:2]
x = a[1:]
x = a[:2]
x = a[:]
x = a[1:2:3]
x = a[::3]
x = a[b:c, d]

# %% augmented assignment
a += 1
a.b -= c
a[b] *= c
a[b:c] //= d
x @= y

# %% chained assignment
a = b = c
a = b.c = d[e] = f

# %% unpacking
a, b = c
a, *b = c
*a, b = c
a, (b, c), d = e
a.x, b[y] = c
a, b = b, a
a, b, c = c, b, a
a, b = c = d

# %% walrus
f((a := b))
(c := d)
x = [y := g(), y ** 2]

# %% calls
f()
f(a, b)
f(a, b=c)
f(*a)
f(**a)
f(a, *b, c, **d)
f(a, *b, *c, d=e, **f)
a.b(c)
a.b(c, d=e)
a.b(*c)
f(a)(b)(c)

# %% displays
a = []
a = [b]
a = [b, c]
a = [1, 2, 3]
a = [1, 2, 3, b]
a = [*b]
a = [*b, c, *d]
a = ()
a = (b,)
a = (b, *c)
a = {b}
a = {1, 2, 3}
a = {*b, c}
a = {}
a = {b: c}
a = {'x': 1, 'y': b}
a = {**b}
a = {'x': 1, **b, 'y': 2}

# %% delete
del a
del a.b
del a[b]
del a, b

# %% imports
import a
import a.b.c
import a as b
import a.b.c as d
from a import b
from a import b as c, d
from a.b import *
from . import x
from .. import y as z
from ...pkg.mod import name

# %% f-strings
a = f'{b}'
a = f'x{b}y'
a = f'{b!r} {c!s} {d!a}'
a = f'{b:>10} {c:{d}} {e!r:{f}.{g}}'
a = f'nice {formatted} {string:.{{{x}}}f}'.a

# %% raise
raise
# %% raise exc
raise a
# %% raise from
raise a from b

# %% assert
assert a
assert a, b
assert a and b, 'msg'
assert a or b

# %% conditional expression
x = a if b else c
x = a if b else c if d else e
f(a if b else c, d)
x = (a if b else c) + 1

# %% boolean operators as values
x = a and b
x = a or b
x = a and b and c
x = a or b or c
x = (a and b) or c
x = a and (b or c)
x = (a or b) and (c or d)
x = not (a and b)
x = a and not b
f(a or b, c and d)

# %% chained comparison as value
x = a < b < c
x = a < b <= c < d
x = (a < b) < c
f(a < b < c)
x = a < b < c and d

# %% lambda
f = lambda: 1
f = lambda a, b=1, *c, d, e=2, **g: a + b
f = lambda x: x if x else None
f = lambda: (yield)

# %% global statement at module level
global g
g = 1
