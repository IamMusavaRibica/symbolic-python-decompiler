# Deliberately bizarre nesting of loops, try / except / finally, with, break, continue and return.

# %% return from every layer of try inside except inside finally inside loops
def f(xs):
    for x in xs:
        while x:
            try:
                try:
                    return x
                except KeyError:
                    try:
                        return x.k
                    finally:
                        x -= 1
                finally:
                    try:
                        if x:
                            return 'inner'
                    except Exception:
                        return 'handler'
                    finally:
                        x = 0
            except Exception as e:
                try:
                    raise
                finally:
                    del e
            finally:
                x = None
    return 'end'

# %% break and continue crossing several finally blocks
def f(xs, ys):
    for x in xs:
        try:
            for y in ys:
                try:
                    if y:
                        break
                    if x:
                        continue
                    try:
                        return y
                    finally:
                        log(1)
                finally:
                    log(2)
            else:
                continue
            break
        finally:
            log(3)
    else:
        return 'no break'
    return 'break'

# %% while loops with else clauses and breaks inside handlers inside with
def f(a, b):
    while a:
        with b as c:
            try:
                while c:
                    try:
                        c = c.next()
                    except StopIteration:
                        break
                    except ValueError:
                        continue
                    else:
                        if c is None:
                            return
                else:
                    a = None
                    continue
                break
            except Exception:
                a -= 1
            else:
                break
    else:
        return 'exhausted'

# %% nested with statements with returns, raises and break inside loops
def f(items):
    for item in items:
        with open(item) as fh, lock:
            with fh.sub() as s:
                if s.bad:
                    raise ValueError(s)
                if s.done:
                    return s
                if s.skip:
                    continue
                if s.stop:
                    break
            with other():
                pass
    return None

# %% try in else of for in handler of try in while true
def f():
    while True:
        try:
            x = get()
        except Empty:
            for y in pending():
                if y:
                    break
            else:
                try:
                    return finish()
                except Done:
                    break
                finally:
                    reset()
            continue
        else:
            if x is None:
                break
            handle(x)
    return 'left'

# %% a generator that returns from within everything
def gen(xs):
    for x in xs:
        try:
            with ctx(x) as c:
                try:
                    while c:
                        yield c
                        c = c.next()
                        if not c:
                            return
                except Stop:
                    yield 'stopped'
                    return
                finally:
                    yield 'finally'
        except Exception as e:
            yield e
            continue
        else:
            yield 'else'
        finally:
            yield 'outer finally'
        yield 'after'

# %% conditions with and / or / not spread over nested loops and tries
def f(a, b, c, d):
    while a and (b or not c) and not (d and a):
        try:
            if a and b or c and not d:
                if not (a or b) and (c or d):
                    break
                elif a is not None and b in c or d:
                    continue
                else:
                    a = a and b or c
            while not a or not b:
                if a or b or c or d:
                    break
                a, b = b, a
            else:
                continue
        except (A, B) as e:
            if e and a or not b:
                raise
        finally:
            d = not d
    return a if a and b else b if b or c else c if not d else d

# %% finally bodies that themselves contain loops with breaks and returns
def f(xs):
    try:
        return first(xs)
    finally:
        for x in xs:
            if x:
                break
            try:
                while x:
                    x -= 1
                    if x == 3:
                        return x
            finally:
                try:
                    pass
                finally:
                    x = None
        else:
            return 'no break'

# %% handlers with nested try that re-raise, and bare except after named ones
def f():
    try:
        try:
            risky()
        except A as a:
            try:
                recover(a)
            except B:
                raise
            except:
                raise C from a
            else:
                return a
        except (D, E):
            pass
        except:
            raise
        finally:
            cleanup()
    except Exception as e:
        try:
            return e
        finally:
            del e
    finally:
        done()

# %% deeply nested functions each with its own loop, try and return
def l1(a):
    for i in a:
        def l2(b):
            while b:
                try:
                    def l3(c):
                        for j in c:
                            try:
                                def l4(d):
                                    try:
                                        return i, j, d
                                    except Exception:
                                        return None
                                    finally:
                                        nonlocal b
                                        b = None
                                return l4
                            finally:
                                continue
                    return l3
                except Exception:
                    break
                finally:
                    b = b and b[1:]
            return None
        yield l2

# %% class body with loops, tries and nested class in handler
class C:
    for i in range(3):
        try:
            x = compute(i)
        except Fail:
            class Fallback:
                value = i
            continue
        else:
            if x:
                break
        finally:
            i = None
    else:
        while True:
            try:
                break
            finally:
                pass
    del i

# %% chains of elif with returns, raises, breaks, continues inside while inside for
def f(xs):
    for x in xs:
        while x:
            if x == 1:
                return 1
            elif x == 2:
                raise E
            elif x == 3:
                break
            elif x == 4:
                continue
            elif x == 5:
                x = 0
            elif x == 6:
                try:
                    return 6
                except Exception:
                    break
            else:
                x -= 1
        else:
            continue
        break
    else:
        return 0
    return -1

# %% with inside try inside with inside except inside for with returns everywhere
def f(xs):
    for x in xs:
        try:
            with a:
                try:
                    with b as y:
                        return y
                except Exception:
                    with c:
                        try:
                            return x
                        except Exception:
                            return None
                        finally:
                            with d:
                                pass
                finally:
                    with e:
                        continue
        except Exception:
            with f:
                return 'handled'

# %% assert and del inside nested loops with try
def f(data):
    for row in data:
        assert row, 'empty row'
        for cell in row:
            try:
                assert cell is not None
                del row[0]
            except AssertionError:
                assert len(row) < 3, f'too long {row!r}'
                del cell
                continue
            finally:
                assert data
        else:
            assert not row
            del row

# %% ternaries and comprehensions nested inside conditions of loops with try
def f(xs, ys):
    while [x for x in xs if x] if xs else any(y for y in ys):
        try:
            z = (xs if xs else ys)[0] if xs or ys else None
            if [a for a in z if a and [b for b in a if b]]:
                break
        except IndexError:
            xs = ys = None
        finally:
            ys = [y for y in ys if y] if ys else ys
    return {k: v for k, v in zip(xs, ys)} if xs and ys else {}

# %% lambda and nested function in conditions and default arguments of nested loops
def f(xs):
    for g in (lambda x: x, lambda x: not x):
        def h(y, k=lambda: g(y)):
            while k():
                try:
                    return y
                finally:
                    y = None
            return g
        if h(1)():
            continue
        if (lambda: g)()(h):
            break
    return g

# %% while true with every exit kind and a finally that breaks
def f(q):
    while True:
        try:
            item = q.get()
            if item is None:
                return
            if item == 'skip':
                continue
            if item == 'stop':
                break
            process(item)
        except Empty:
            try:
                q.refill()
            except Exhausted:
                break
            continue
        except Fatal as e:
            try:
                raise RuntimeError(e)
            finally:
                q.close()
        finally:
            q.task_done()
    q.join()

# %% try / except / else / finally all containing loops with their own tries
def f(xs):
    try:
        for x in xs:
            try:
                x()
            except A:
                continue
    except B:
        while xs:
            try:
                xs.pop()()
            except C:
                break
    else:
        for x in xs:
            try:
                if x:
                    return x
            finally:
                x = None
    finally:
        for x in xs:
            try:
                x.close()
            except D:
                pass
            else:
                break
