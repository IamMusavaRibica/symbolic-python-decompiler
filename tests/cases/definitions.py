# %% simple function
def f():
    pass

# %% function arguments
def f(a, b=1, *args, c, d=2, **kwargs):
    return a

# %% positional only
def f(a, b, /, c, *, d):
    return a + b + c + d

# %% annotations
def f(a: int, b: str = 'x', *c: tuple, d: float = 1.0, **e: dict) -> bool:
    return True

# %% docstring
def f():
    """doc"""
    return 1

# %% decorators
@a
@b.c(d)
def f():
    pass

# %% nested function and closure
def outer(a):
    b = 1
    def inner(c):
        return a + b + c
    return inner

# %% nonlocal and global
def outer():
    x = 1
    def inner():
        nonlocal x
        global y
        x = 2
        y = 3
        del x
    return inner

# %% generator
def gen(a):
    for x in a:
        yield x
    yield from a
    y = yield
    return y

# %% generator without reachable yield
def gen():
    return
    yield

# %% async function
async def f(a):
    await a
    x = await a.b()
    async with a as b:
        await b
    async for c in a:
        await c
    else:
        d
    return x

# %% async generator
async def f(a):
    async for x in a:
        yield x

# %% class
class A:
    pass

# %% class with bases and body
class A(B, C, metaclass=M):
    """doc"""
    x = 1
    def f(self):
        return self.x
    @staticmethod
    def g():
        pass

# %% class with super
class A(B):
    def __init__(self):
        super().__init__()
        self.__private = 1
    def __m(self):
        return __class__

# %% decorated class
@dec
class A:
    y: int = 1
    z: str

# %% module annotations
x: int = 1
y: str

# %% module docstring
"""module doc"""
x = 1

# %% comprehensions
a = [x for x in y]
b = [x for x in y if x]
c = [x + z for x in y for z in x if z if x]
d = {x for x in y}
e = {k: v for k, v in y}
g = (x for x in y)
h = [[z for z in x] for x in y]
i = sum(x for x in y if x > 1)
j = [x if x else None for x in y]
k = [x for x in y if x and x.a or x.b]

# %% comprehension with closure
def f(a, b):
    return [x + a for x in b if a]

# %% async comprehension
async def f(a):
    return [x async for x in a]

async def g(a):
    return [await x for x in a]

# %% lambda with closure
def f(a):
    return lambda b: a + b

# %% function defaults using expressions
def f(a=b + 1, *, c=d()):
    pass

# %% class in function
def f():
    class A:
        def g(self):
            return A
    return A

# %% star args in class bases
class A(*bases, **kw):
    pass

# %% future annotations
from __future__ import annotations
def f(a: int, b: 'str') -> List[int]:
    pass
x: Dict[str, int] = {}

# %% top level await [await]
async with v:
    ...
async for x in z:
    pass
