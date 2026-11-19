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

# %% a line over 255 bytes of bytecode makes the peephole optimizer skip the module: its unfolded defaults tuple must not be taken for a peephole-less compiler
names = [alpha_0, alpha_1, alpha_2, alpha_3, alpha_4, alpha_5, alpha_6, alpha_7, alpha_8, alpha_9, alpha_10, alpha_11, alpha_12, alpha_13, alpha_14, alpha_15, alpha_16, alpha_17, alpha_18, alpha_19, alpha_20, alpha_21, alpha_22, alpha_23, alpha_24, alpha_25, alpha_26, alpha_27, alpha_28, alpha_29, alpha_30, alpha_31, alpha_32, alpha_33, alpha_34, alpha_35, alpha_36, alpha_37, alpha_38, alpha_39, alpha_40, alpha_41, alpha_42, alpha_43, alpha_44, alpha_45, alpha_46, alpha_47, alpha_48, alpha_49, alpha_50, alpha_51, alpha_52, alpha_53, alpha_54, alpha_55, alpha_56, alpha_57, alpha_58, alpha_59, alpha_60, alpha_61, alpha_62, alpha_63, alpha_64, alpha_65, alpha_66, alpha_67, alpha_68, alpha_69, alpha_70, alpha_71, alpha_72, alpha_73, alpha_74, alpha_75, alpha_76, alpha_77, alpha_78, alpha_79, alpha_80, alpha_81, alpha_82, alpha_83, alpha_84, alpha_85, alpha_86, alpha_87, alpha_88, alpha_89, alpha_90, alpha_91, alpha_92, alpha_93, alpha_94, alpha_95, alpha_96, alpha_97, alpha_98, alpha_99, alpha_100, alpha_101, alpha_102, alpha_103, alpha_104, alpha_105, alpha_106, alpha_107, alpha_108, alpha_109, alpha_110, alpha_111, alpha_112, alpha_113, alpha_114, alpha_115, alpha_116, alpha_117, alpha_118, alpha_119, alpha_120, alpha_121, alpha_122, alpha_123, alpha_124, alpha_125, alpha_126, alpha_127, alpha_128, alpha_129, alpha_130, alpha_131, alpha_132, alpha_133, alpha_134, alpha_135, alpha_136, alpha_137, alpha_138, alpha_139]
def later(a=(1, 2), b=None):
    return x if a else y
