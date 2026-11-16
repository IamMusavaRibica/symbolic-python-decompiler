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
