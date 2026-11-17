# Shapes that first went wrong on real standard library code.

# %% negative constants need parentheses
x = (-1) ** a
y = (-1).real
z = -1.5 ** b
w = (-2j) ** c

# %% is / in tests cannot be negated
def f(a, b, xs):
    for x in xs:
        if x in a:
            continue
        b(x)
    if a is not None or b:
        return 1
    return (f for f in xs if not f.cancelled() and f.exception() is not None)

# %% negated is / in at the end of a loop body
def f(xs, seen):
    for x in xs:
        if x not in seen:
            g(x)
    while xs:
        if xs.pop() is not None:
            pass
        else:
            h()

# %% try else at the end of a function
def interleave(inter, f, seq):
    seq = iter(seq)
    try:
        f(next(seq))
    except StopIteration:
        pass
    else:
        for x in seq:
            inter()
            f(x)

# %% nonlocal that is never used
def _cell_factory():
    a = 1
    def f():
        nonlocal a
    return f.__closure__[0]

# %% break in a nested if inside try inside while
def _readlink_deep(path, seen):
    while normcase(path) not in seen:
        seen.add(normcase(path))
        try:
            old_path = path
            path = _nt_readlink(path)
            if not isabs(path):
                if not islink(old_path):
                    path = old_path
                    break
                path = normpath(join(dirname(old_path), path))
        except OSError as ex:
            if ex.winerror in allowed_winerror:
                break
            raise
        except ValueError:
            break
    return path

# %% tuple constant with ellipsis
f(type, (...,), obj=obj)
x = (None, ..., True, -1, (2, 'a'))

# %% star arguments that look like plain ones
f(self, 'text', policy=policy, **{'charset': str(c)})
f(*(a, b))
f(*(1, 2), **{'k': v})
f(**{'not-a-name': 1})
f(a, **{'k': v})
f(*a, k=v)
f(k=v, **b)
f(**a, **b)
f(*a, *b)

# %% assert not
def f(x):
    assert not x
    assert x is not None, 'message'

# %% assert as the last statement of a while true loop
def _exitfunc(cls, pending):
    while True:
        if not pending:
            break
        f = pending.pop()
        assert f not in cls._registry

# %% return through a finally body that branches
def wait(self, timeout, waiter):
    gotit = False
    try:
        if timeout is None:
            waiter.acquire()
            gotit = True
        else:
            gotit = waiter.acquire(False)
        return gotit
    finally:
        self._acquire_restore()
        if not gotit:
            try:
                self._waiters.remove(waiter)
            except ValueError:
                pass

# %% return constant through a finally body that branches
def _loop_reading(self, fut=None):
    data = None
    try:
        if self._closing:
            data = None
            return
        if data == b'':
            return
        if not self._paused:
            self._read_fut = self._loop.recv()
    except ConnectionAbortedError as exc:
        if not self._closing:
            self._fatal_error(exc)
        elif self._loop.get_debug():
            logger.debug('x')
    except ConnectionResetError as exc:
        self._force_close(exc)
    except CancelledError:
        if not self._closing:
            raise
    else:
        if not self._paused:
            self._read_fut.add_done_callback(self._loop_reading)
    finally:
        if data is not None:
            self._data_received(data)

# %% nested while true loops whose breaks continue the outer loop
def _generate_toc(self):
    starts, stops = [], []
    next_pos = 0
    while True:
        line_pos = next_pos
        line = self._file.readline()
        next_pos = self._file.tell()
        if line.startswith(b'x'):
            starts.append(next_pos)
            while True:
                line_pos = next_pos
                line = self._file.readline()
                next_pos = self._file.tell()
                if line == b'x':
                    stops.append(line_pos - 1)
                    break
                elif not line:
                    stops.append(line_pos)
                    break
        elif not line:
            break
    self._toc = dict(enumerate(zip(starts, stops)))

# %% while true whose body ends in raise
def _parse_flags(source, state, char):
    add_flags = 0
    if char != '-':
        while True:
            flag = FLAGS[char]
            add_flags |= flag
            char = source.get()
            if char is None:
                raise source.error('missing')
            if char in ')-:':
                break
            if char not in FLAGS:
                msg = 'unknown flag' if char.isalpha() else 'missing'
                raise source.error(msg, len(char))
    if char == ')':
        state.flags |= add_flags
        return None
    return add_flags

# %% while true: the code after the loop among several joins
def consume_optional(action, explicit_arg, arg_count, chars):
    action_tuples = []
    while True:
        if action is None:
            return 1
        if explicit_arg is not None:
            if arg_count == 0 and explicit_arg not in chars:
                action_tuples.append(action)
                action = explicit_arg
            elif arg_count == 1:
                stop = 1
                action_tuples.append(action)
                break
            else:
                raise ArgumentError(action)
        else:
            stop = 2
            action_tuples.append(action)
            break
    assert action_tuples
    for action in action_tuples:
        take_action(action)
    return stop

# %% while true: breaks after statements in compound-condition bodies
def fetch(self, pointer, nhist, prefix):
    while 1:
        pointer += 1
        if pointer < 0 or pointer >= nhist:
            self.bell()
            if not self.cyclic and pointer < 0:
                return
            else:
                pointer = prefix = None
            break
        item = self.history[pointer]
        if item[:2] == prefix and len(item) > 2:
            self.insert(item)
            break
    self.see('insert')
    self.pointer = pointer

# %% continue inside with inside try inside for
def read_registry(self, keys):
    for name in keys:
        try:
            with open_key(name) as subkey:
                if not name.startswith('.'):
                    continue
                mimetype, datatype = query(subkey)
                if datatype != 1:
                    continue
                self.add_type(mimetype, name)
        except OSError:
            continue

# %% handlers that fall through to a break
def next_member(self):
    while True:
        try:
            tarinfo = self.fromtarfile()
        except EOFHeaderError as e:
            if self.ignore_zeros:
                self.offset += 1
                continue
        except InvalidHeaderError as e:
            if self.ignore_zeros:
                self.offset += 1
                continue
            elif self.offset == 0:
                raise ReadError(str(e))
        except EmptyHeaderError:
            if self.offset == 0:
                raise ReadError('empty file')
        break
    if tarinfo is not None:
        self.members.append(tarinfo)
    return tarinfo

# %% return in a try body whose handler falls through, with finally
def _is_ipv6_enabled():
    if socket.has_ipv6:
        sock = None
        try:
            sock = socket.socket(1, 2)
            return True
        except OSError:
            pass
        finally:
            if sock:
                sock.close()
    return False

# %% assert at the start of a while true loop
def all_sorts(G, D, count):
    while True:
        assert all(count[v] == 0 for v in D)
        if len(D) == len(G):
            yield list(D)
        for j in G:
            count[j] += 1
            assert count[j] >= 0
        if not D:
            break

# %% complex constants with signed zeros
a = 1j
b = -1j
c = 0 - 1j
d = -(1 + 0j)
e = 1 + 2j
f = 1 - 2j
g = -(0 - 2j)

# %% f-string without fields as a statement
def f():
    f'Not a docstring'
def g():
    """A docstring"""
    f'x'

# %% comprehension that binds a name to a single value
x = [y * 2 for z in data for y in [g(z)] if y]
t = {k: v for item in data for (k, v) in [item]}
u = ', '.join(read(p) for p in paths if (check(p) or True) and isfile(p))

# %% assignment expression in a comprehension with declared targets
def f(data):
    global G
    return [(G := x) for x in data]
def outer():
    n = None
    def g(data):
        nonlocal n
        return [(n := x) for x in data]
    return g
h = [(M := x) for x in data]

# %% scope facts decided by code that produces no bytecode
def cells(self):
    if 0:
        def inner():
            return self
    return self
def local_without_binding():
    return x
    x = 1
def cell_bound_later(a):
    if 0:
        b = 1
        def inner():
            return a, b
    return a

# %% set of equal constants
s = {1, 1.0, True}
t = {1, 2, 3}
u = {'a', 'a', 'a', 'b'}

# %% equal lambdas on different lines
table = {
    'a': lambda x: x,
    'b': lambda x: x,
    'c': lambda x: x + 1,
}

# %% a statement too long for one line
names = [alpha_0, alpha_1, alpha_2, alpha_3, alpha_4, alpha_5, alpha_6, alpha_7, alpha_8, alpha_9, alpha_10, alpha_11, alpha_12, alpha_13, alpha_14, alpha_15, alpha_16, alpha_17, alpha_18, alpha_19, alpha_20, alpha_21, alpha_22, alpha_23, alpha_24, alpha_25, alpha_26, alpha_27, alpha_28, alpha_29, alpha_30, alpha_31, alpha_32, alpha_33, alpha_34, alpha_35, alpha_36, alpha_37, alpha_38, alpha_39, alpha_40, alpha_41, alpha_42, alpha_43, alpha_44, alpha_45, alpha_46, alpha_47, alpha_48, alpha_49, alpha_50, alpha_51, alpha_52, alpha_53, alpha_54, alpha_55, alpha_56, alpha_57, alpha_58, alpha_59, alpha_60, alpha_61, alpha_62, alpha_63, alpha_64, alpha_65, alpha_66, alpha_67, alpha_68, alpha_69, alpha_70, alpha_71, alpha_72, alpha_73, alpha_74, alpha_75, alpha_76, alpha_77, alpha_78, alpha_79, alpha_80, alpha_81, alpha_82, alpha_83, alpha_84, alpha_85, alpha_86, alpha_87, alpha_88, alpha_89, alpha_90, alpha_91, alpha_92, alpha_93, alpha_94, alpha_95, alpha_96, alpha_97, alpha_98, alpha_99, alpha_100, alpha_101, alpha_102, alpha_103, alpha_104, alpha_105, alpha_106, alpha_107, alpha_108, alpha_109, alpha_110, alpha_111, alpha_112, alpha_113, alpha_114, alpha_115, alpha_116, alpha_117, alpha_118, alpha_119, alpha_120, alpha_121, alpha_122, alpha_123, alpha_124, alpha_125, alpha_126, alpha_127, alpha_128, alpha_129, alpha_130, alpha_131, alpha_132, alpha_133, alpha_134, alpha_135, alpha_136, alpha_137, alpha_138, alpha_139]
def later(a=(1, 2), b=None):
    return x if a else y

# %% global statement in a module with future imports
"""docstring"""
from __future__ import annotations
import os
def f():
    return cache
global cache
cache = {}

# %% assert False under a condition
def f(a, b):
    if a != b:
        assert False, 'msg %d' % a
def g(x):
    for p in x:
        if p and x:
            assert False
        p.q()
def h(x):
    if not x or False:
        return 1
    if not x:
        if False:
            return 2

# %% chained comparison inside or inside and, as a value
def h(A, B, x, y):
    return (A or 1 <= x <= 2) and (B or 1 <= y <= 2)
def k(A, B, x, y):
    z = (A and 1 <= x <= 2) or (B and 1 <= y <= 2)
    w = ((A or x) and B) or y
    v = (A or B) and (x or y) and not (A and B)
    return z, w, v

# %% module level if / else whose branch ends the module
import sys
if sys.version_info[:2] == (3, 9):
    names = PY
else:
    raise AssertionError('msg')

# %% class body if / else whose branch ends the class
class C:
    if flag:
        a = 1
    else:
        raise RuntimeError('no')

# %% nested and / or values whose jumps the optimizer threads through each other
def f(a, b, c, d, e):
    x = (a and b or c and d) and e
    y = (a or b) and (c or d) or e
    z = ((a and b) or (c and d)) and ((a or b) and (c or d))
    if x and (a and b or c and d) and e:
        return (a and b or c and d) or e
    return x, y, z

# %% assert with a conditional expression whose else is a constant
def g(a):
    assert not a.empty if a.c else True
    assert a.x if a.c else a.y
    assert (a.x if a.c else a.y is not None), 'm'

# %% a very long arithmetic statement, with and without brackets
def f(A, B, a, b, c, d):
    zz = (c - d) * (A ** 3 * a ** 9 + 3 * A ** 2 * B * a ** 6 * b ** 3 - 3 * A ** 2 * a ** 6 * c ** 3 + 9 * A ** 2 * a ** 6 * c ** 2 * d - 9 * A ** 2 * a ** 6 * c * d ** 2 + 3 * A ** 2 * a ** 6 * d ** 3 + 3 * A * B ** 2 * a ** 3 * b ** 6 + 21 * A * B * a ** 3 * b ** 3 * c ** 3 - 63 * A * B * a ** 3 * b ** 3 * c ** 2 * d + 63 * A * B * a ** 3 * b ** 3 * c * d ** 2 - 21 * A * B * a ** 3 * b ** 3 * d ** 3 + 3 * A * a ** 3 * c ** 6 - 18 * A * a ** 3 * c ** 5 * d + 45 * A * a ** 3 * c ** 4 * d ** 2 - 60 * A * a ** 3 * c ** 3 * d ** 3 + 45 * A * a ** 3 * c ** 2 * d ** 4 - 18 * A * a ** 3 * c * d ** 5 + 3 * A * a ** 3 * d ** 6 + B ** 3 * b ** 9 + 3 * B ** 2 * b ** 6 * c ** 3 - 9 * B ** 2 * b ** 6 * c ** 2 * d + 9 * B ** 2 * b ** 6 * c * d ** 2 - 3 * B ** 2 * b ** 6 * d ** 3 + 3 * B * b ** 3 * c ** 6 - 18 * B * b ** 3 * c ** 5 * d + 45 * B * b ** 3 * c ** 4 * d ** 2 - 60 * B * b ** 3 * c ** 3 * d ** 3 + 45 * B * b ** 3 * c ** 2 * d ** 4 - 18 * B * b ** 3 * c * d ** 5 + 3 * B * b ** 3 * d ** 6 + c ** 9 - 9 * c ** 8 * d + 36 * c ** 7 * d ** 2 - 84 * c ** 6 * d ** 3 + 126 * c ** 5 * d ** 4 - 126 * c ** 4 * d ** 5 + 84 * c ** 3 * d ** 6 - 36 * c ** 2 * d ** 7 + 9 * c * d ** 8 - d ** 9)
    yy = A ** 3 * a ** 9 + 3 * A ** 2 * B * a ** 6 * b ** 3 - 3 * A ** 2 * a ** 6 * c ** 3 + 9 * A ** 2 * a ** 6 * c ** 2 * d - 9 * A ** 2 * a ** 6 * c * d ** 2 + 3 * A ** 2 * a ** 6 * d ** 3 + 3 * A * B ** 2 * a ** 3 * b ** 6 + 21 * A * B * a ** 3 * b ** 3 * c ** 3 - 63 * A * B * a ** 3 * b ** 3 * c ** 2 * d + 63 * A * B * a ** 3 * b ** 3 * c * d ** 2 - 21 * A * B * a ** 3 * b ** 3 * d ** 3 + 3 * A * a ** 3 * c ** 6 - 18 * A * a ** 3 * c ** 5 * d + 45 * A * a ** 3 * c ** 4 * d ** 2 - 60 * A * a ** 3 * c ** 3 * d ** 3 + 45 * A * a ** 3 * c ** 2 * d ** 4 - 18 * A * a ** 3 * c * d ** 5 + 3 * A * a ** 3 * d ** 6 + B ** 3 * b ** 9 + 3 * B ** 2 * b ** 6 * c ** 3 - 9 * B ** 2 * b ** 6 * c ** 2 * d + 9 * B ** 2 * b ** 6 * c * d ** 2 - 3 * B ** 2 * b ** 6 * d ** 3 + 3 * B * b ** 3 * c ** 6 - 18 * B * b ** 3 * c ** 5 * d + 45 * B * b ** 3 * c ** 4 * d ** 2 - 60 * B * b ** 3 * c ** 3 * d ** 3 + 45 * B * b ** 3 * c ** 2 * d ** 4 - 18 * B * b ** 3 * c * d ** 5 + 3 * B * b ** 3 * d ** 6 + c ** 9 - 9 * c ** 8 * d + 36 * c ** 7 * d ** 2 - 84 * c ** 6 * d ** 3 + 126 * c ** 5 * d ** 4 - 126 * c ** 4 * d ** 5 + 84 * c ** 3 * d ** 6 - 36 * c ** 2 * d ** 7 + 9 * c * d ** 8 - d ** 9
    x, y = y, x
    return zz and yy or a and b and c and d and A and B and a + b + c + d + A + B + a * b * c * d * A * B + a ** b ** c ** d ** A ** B

# %% annotations of non-simple targets
class C:
    x.y: int = 0
class D:
    (z): int
    a[0]: str
w.v: print(234) = 5
def f():
    x[0]: int
    (y): int
    z.w: int
    return 1
def g(self):
    var2: [int, str]
    my_lst = [42]
    int.new_attr: int
    [list][0]: type
    my_lst[one() - 1]: int = 5

# %% annotations of non-simple targets under future annotations
from __future__ import annotations
class C:
    x.y: int = 0
class D:
    (z): int
def f():
    x[0]: int

# %% empty star arguments next to double star arguments
f1(*())
f1(*(), **{})
f1(**{})
f1(*(), **k)

# %% constant tests the compiler does not fold
if not 1: pass
if 1 and 1: pass
if 1 or 1: pass
if not not not 1: pass
if not 1 and 1 and 1: pass
if 1 and 1 or 1 and 1 and 1 or not 1 and 1: pass
if None or 0: pass
while 1 and 2:
    break

# %% non-simple annotation of a plain name
(a): print(1) = 3
b = 2
class C:
    (x): f() = 1
