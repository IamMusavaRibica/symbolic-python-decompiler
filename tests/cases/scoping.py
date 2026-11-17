# Hand-crafted scoping and class cases: nested functions, global / nonlocal, generators, closures.

# %% three levels of nesting with global and nonlocal everywhere
counter = 0
def outer(a):
    global counter
    b = a + 1
    def middle(c):
        nonlocal b
        global counter
        b += c
        counter += 1
        def inner(d):
            nonlocal b, c
            global counter
            c = c * 2
            b = b - d
            counter -= d
            return a, b, c, d, counter
        return inner
    return middle

# %% generator in the middle of a closure chain
def outer(items):
    total = 0
    def gen():
        nonlocal total
        for item in items:
            total += item
            yield total
            if total > 100:
                return
    def report():
        return total, list(gen())
    return report

# %% generator with send and yield from
def chain(*its):
    for it in its:
        yield from it
def echo():
    received = yield 'ready'
    while received is not None:
        received = yield received * 2
    return 'done'
def nested_from():
    result = yield from echo()
    print(result)

# %% async generator and coroutine closures
async def producer(xs):
    for x in xs:
        yield await transform(x)
async def consumer(xs):
    seen = []
    async def collect():
        nonlocal seen
        async for x in producer(xs):
            seen.append(x)
        return seen
    async with lock:
        return await collect()

# %% class with nested class, methods and closures over the class scope
def factory(prefix):
    suffix = '!'
    class Base:
        count = 0
        def __init__(self, name):
            self.name = prefix + name + suffix
            Base.count += 1
        class Inner:
            def describe(self):
                return prefix, suffix
        @staticmethod
        def make(name):
            return Base(name)
        @classmethod
        def named(cls, name):
            return cls(name)
        @property
        def upper(self):
            return self.name.upper()
    return Base

# %% class body with global, nonlocal and comprehensions
G = 1
def wrapper():
    local = 2
    class C:
        global G
        nonlocal local
        G = 10
        local = 20
        xs = [i * local for i in range(3)]
        ys = {i: G for i in xs}
        def method(self):
            return local, G
    return C

# %% class attribute that shadows a global and a method that reads the global
value = 'global'
class K:
    value = 'class'
    def read(self):
        return value
    def read_class(self):
        return self.value, K.value
    def write(self):
        global value
        value = 'changed'

# %% __class__ cell and super
class A:
    def f(self):
        return super().f()
    def g(self):
        return __class__
    def h(self):
        return lambda: super().h()

# %% lambda closures and default arguments
def make_adders(n):
    plain = [lambda x: x + i for i in range(n)]
    bound = [lambda x, i=i: x + i for i in range(n)]
    nested = lambda a: lambda b: lambda c: a + b + c + n
    return plain, bound, nested

# %% closure over loop variables and deleted names
def f(xs):
    for x in xs:
        def g():
            return x
    del x
    def h():
        return xs
    del xs
    return g, h

# %% nonlocal rebinding inside nested loops and try
def f():
    state = 0
    def step():
        nonlocal state
        for i in range(3):
            while state < i:
                try:
                    state += 1
                except Exception:
                    nonlocal_helper = state
                    state = -1
                    break
                finally:
                    state += 0
            else:
                continue
            break
        return state
    return step

# %% generator expression and comprehensions nested in a class in a function
def f(data):
    class C:
        rows = [[c for c in row] for row in data]
        total = sum(sum(row) for row in rows)
        flat = {c for row in rows for c in row}
        def method(self):
            return [r for r in self.rows if r]
    return C

# %% decorators with closures and free variables
def deco(tag):
    def wrap(fn):
        def inner(*args, **kwargs):
            print(tag, fn.__name__)
            return fn(*args, **kwargs)
        return inner
    return wrap
def f():
    log = []
    @deco('a')
    @deco('b')
    def g(x):
        log.append(x)
        return x
    return g, log

# %% recursive nested function with nonlocal memo
def fib(n):
    memo = {}
    def go(k):
        nonlocal memo
        if k < 2:
            return k
        if k not in memo:
            memo[k] = go(k - 1) + go(k - 2)
        return memo[k]
    return go(n)

# %% function with every argument kind and nested defaults
def f(a, b=1, /, c=2, *args, d, e=3, **kwargs):
    def g(x=a, y=b, *, z=d):
        return x, y, z, c, args, e, kwargs
    return g

# %% generator in a class body and generator methods
class Source:
    def __iter__(self):
        yield from self.items
    def pairs(self):
        it = iter(self.items)
        for a in it:
            try:
                b = next(it)
            except StopIteration:
                yield (a, None)
                return
            yield (a, b)
    def __aiter__(self):
        return self
    async def __anext__(self):
        try:
            return await self.queue.get()
        except Empty:
            raise StopAsyncIteration

# %% global declared after use in a nested function
def f():
    def g():
        return x
    global x
    x = g
    return g

# %% conditional function definitions and class bodies
def f(flag):
    if flag:
        def g():
            return 1
    else:
        def g():
            return 2
    class C:
        if flag:
            a = 1
        else:
            a = 2
        for i in range(3):
            pass
        while a:
            a -= 1
    return g, C

# %% class with metaclass, keywords, bases from closures and __slots__
def make(meta, *bases, **kw):
    class C(*bases, metaclass=meta, **kw):
        __slots__ = ('a', 'b')
        def __init_subclass__(cls, flag=False, **kwargs):
            super().__init_subclass__(**kwargs)
            cls.flag = flag
    class D(C, flag=True):
        pass
    return D

# %% walrus in nested scopes, comprehensions and conditions
def f(data):
    total = 0
    def g():
        nonlocal total
        if (n := len(data)) > 2:
            total += n
        return [y for x in data if (y := x * 2) > total]
    while (chunk := data[:2]):
        data = data[2:]
        total += sum(chunk)
    return g, total

# %% yield inside try / except / finally with returns
def gen(xs):
    for x in xs:
        try:
            yield x
        except GeneratorExit:
            return
        except ValueError as e:
            yield e
            continue
        finally:
            yield 'cleanup'
    try:
        yield 'end'
    finally:
        return

# %% del of nonlocal and global names, del in nested loops
G = 1
def f():
    local = 1
    def g():
        nonlocal local
        global G
        del local
        del G
        for i, (a, b) in enumerate(pairs):
            del a
            del pairs[i]
        return b
    return g

# %% generator expression over a nonlocal that is rebound inside it
def f(xs):
    n = 0
    def bump():
        nonlocal n
        n += 1
        return n
    return list(bump() + x for x in xs if bump())

# %% unpacking assignments with stars across scopes
def f(seq):
    first, *rest = seq
    def g():
        nonlocal rest
        (a, b), *rest = rest
        *init, last = rest
        return a, b, init, last
    [x, [y, *z]] = seq
    return g, x, y, z

# %% try / except in a class body with a method defined in the handler
class C:
    try:
        import missing_module as mm
    except ImportError:
        mm = None
        def fallback(self):
            return self
    else:
        def fallback(self):
            return mm
    finally:
        done = True

# %% conditional expression and boolean operators across nested calls
def f(a, b, c):
    def g(x):
        return (x if x else a) or (b and c) or not a
    return g(a and b or c) if a is not None and (b or c) else g(None)
