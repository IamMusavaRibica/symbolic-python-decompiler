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

# %% while true without back edge: every path breaks through a finally
def f(x):
    while True:
        try:
            if x:
                if y:
                    if z:
                        break
                    break
                break
        finally:
            g()
        break
def h(r, x):
    for i in r:
        while True:
            try:
                if x:
                    break
            finally:
                g()
            break
        h(i)
def k(x):
    a()
    while True:
        try:
            with m():
                if x:
                    break
        finally:
            g()
        b()
        break
    c()
def test_jump_over_break_in_try_finally_block(output):
    output.append(1)
    while True:
        output.append(3)
        try:
            output.append(5)
            if not output:
                break
            output.append(8)
        finally:
            output.append(10)
        output.append(11)
        break
    output.append(13)

# %% unreachable code after continue, raise and return (decompiled only with --dead-code)
object = 5
def f(x):
    while True:
        try:
            continue
            def lmao(r):
                nonlocal x
                y = 1
            raise
        finally:
            g()
        break
def g(a):
    if a:
        return 1
    else:
        return 2
def h(self, key):
    try:
        self[key]
    except KeyError:
        return False
    else:
        return True

# %% dead-code shapes: a return inside try whose finally holds a with statement that continues, inside with inside for
def f(xs):
    for x in xs:
        with a:
            try:
                with b as y:
                    return y
            finally:
                with e:
                    continue

# %% dead-code shapes: a return inside try whose finally holds try/except/finally with returns in body and handler
def f(x):
    try:
        return x
    finally:
        try:
            if x:
                return 2
        except Exception:
            return 3
        finally:
            x = 0

# %% dead-code shapes: try/except/else with continue and break inside while True, then a return
def f():
    while True:
        try:
            x = g()
        except E:
            continue
        else:
            break
    return x

# %% dead-code shapes: handler that continues through a finally body with a branch
def f(data):
    for cell in data:
        try:
            a()
        except E:
            pass
            continue
        finally:
            assert data

# %% dead-code shapes: try/except/else in while True: handler continues, else breaks
def f():
    while True:
        try:
            x = get()
        except Empty:
            continue
        else:
            if x is None:
                break
            handle(x)
    return "left"

# %% dead-code shapes: a return inside try whose finally continues
def l3(c):
    for j in c:
        try:
            return j
        finally:
            continue

# %% dead-code shapes: try whose finally returns (same live code as a bare except that returns)
def gen(xs):
    try:
        yield 1
    finally:
        return

# %% stdlib dead-code shapes: if/else whose else raises (the implicit return is reached by the body alone)
def set_subdir(self, subdir):
    if subdir == 'new':
        self._subdir = subdir
    else:
        raise ValueError(subdir)

# %% stdlib dead-code shapes: raise inside a loop laid out before the loop end is no break target
def unwrap(func, memo):
    while w(func):
        func = func.w
        if func in memo or len(memo) >= 3:
            raise ValueError(func)
        memo[func] = func
    return func

# %% stdlib dead-code shapes: return False / return True in while True inside with
def f(fp1, fp2):
    with a as fp:
        while True:
            b1 = fp1.read()
            b2 = fp2.read()
            if b1 != b2:
                return False
            if not b1:
                return True

# %% stdlib dead-code shapes: two try statements in while True, handler returns (dead back jump)
def f(pop, d):
    while True:
        try:
            key = pop()
        except IndexError:
            return
        try:
            del d[key]
        except KeyError:
            pass

# %% stdlib dead-code shapes: continue after a handler that continues
def f(node):
    for name in node:
        try:
            value = g(name)
        except AttributeError:
            keywords = True
            continue
        if value is None:
            keywords = True
            continue
        h(value)

# %% stdlib dead-code shapes: break whose target is reached by a conditional jump the optimizer threaded
def f(self):
    result = ''
    while True:
        c = self.next
        if c is None:
            if not result:
                raise E
            raise F
        if c == t:
            if not result:
                raise G
            break
        result += c
    return result

# %% stdlib dead-code shapes: return True as the last statement of a try/finally body
def wait(self):
    if self._value:
        return True
    fut = self._loop.create_future()
    try:
        f(fut)
        return True
    finally:
        self._waiters.remove(fut)

# %% stdlib dead-code shapes: if then break inside try inside for (shared clean-up chain)
def f(ress):
    for res in ress:
        err = sock = None
        try:
            sock = g(res)
            if sock == 1:
                sock.connect()
            break
        except OSError as exc:
            err = exc
            if sock is not None:
                sock.close()
    if err is not None:
        raise err

# %% stdlib dead-code shapes: if/else raise followed by a return reached by the body alone
def seek(self, pos=0):
    if pos - self.pos >= 0:
        for i in range(3):
            self.read(self.bufsize)
        self.read(pos)
    else:
        raise StreamError('x')
    return self.pos

# %% stdlib dead-code shapes: while 1 with an unreachable else clause
def PipeClient(address):
    t = _init_timeout()
    while 1:
        try:
            h = f(address)
        except OSError as e:
            if e.winerror not in (1, 2) or _check_timeout(t):
                raise
        else:
            break
    else:
        raise
    return PipeConnection(h)

# %% stdlib dead-code shapes: raise followed by if/else returns (no elif)
def median(data):
    n = len(data)
    if n == 0:
        raise E
    if n % 2 == 1:
        return data[1]
    else:
        i = n // 2
        return data[i]


# %% stdlib dead-code shapes: handler that falls through next to a handler that returns, with an else clause
def f(self, sock):
    for _ in range(3):
        try:
            conn = sock.accept()
        except BlockingIOError:
            return None
        except OSError as exc:
            if exc.errno in (1, 2):
                self.call_later(exc)
            else:
                raise
        else:
            extra = conn
            self.create_task(extra)

# %% stdlib dead-code shapes: handler with an if then continue, else break
def f(loop):
    while True:
        try:
            loop.run_forever()
        except KeyboardInterrupt:
            if g():
                h()
            continue
        else:
            break

# %% stdlib dead-code shapes: return through a finally that holds a try/finally
def run(main, loop):
    try:
        set_loop(loop)
        return loop.run(main)
    finally:
        try:
            cancel(loop)
        finally:
            loop.close()

# %% stdlib dead-code shapes: module level while True whose else breaks to the end of the module
import sys
if __name__ == '__main__':
    loop = new_loop()
    while True:
        try:
            loop.run_forever()
        except KeyboardInterrupt:
            if repl_future and not repl_future.done():
                repl_future.cancel()
            continue
        else:
            break

# %% stdlib dead-code shapes: if then break, else try then break with a handler that continues, in a for with else
def _setup(sys, details):
    for builtin_os, seps in details:
        path_sep = seps[0]
        if builtin_os in sys.modules:
            os_module = sys.modules[builtin_os]
            break
        else:
            try:
                os_module = name(builtin_os)
                break
            except ImportError:
                continue
    else:
        raise ImportError('x')
    setattr(sys, '_os', os_module)

# %% stdlib dead-code shapes: three handlers in a while True whose body breaks (tarfile.next)
def next(self):
    tarinfo = None
    while True:
        try:
            tarinfo = self.tarinfo.fromtarfile(self)
        except EOFHeaderError as e:
            if self.ignore_zeros:
                self._dbg(2, "0x%X: %s" % (self.offset, e))
                self.offset += BLOCKSIZE
                continue
        except InvalidHeaderError as e:
            if self.ignore_zeros:
                self._dbg(2, "0x%X: %s" % (self.offset, e))
                self.offset += BLOCKSIZE
                continue
            elif self.offset == 0:
                raise ReadError(str(e))
        except EmptyHeaderError:
            if self.offset == 0:
                raise ReadError("empty file")
        break
    if tarinfo is not None:
        self.members.append(tarinfo)
    else:
        self._loaded = True
    return tarinfo

# %% stdlib dead-code shapes: bare except that returns inside an else branch (fetch_completions)
def fetch_completions(self, what, mode):
    """Return a pair of lists of completions for something. The first list
    is a sublist of the second. Both are sorted.

    If there is a Python subprocess, get the comp. list there.  Otherwise,
    either fetch_completions() is running in the subprocess itself or it
    was called in an IDLE EditorWindow before any script had been run.

    The subprocess environment is that of the most recently run script.  If
    two unrelated modules are being edited some calltips in the current
    module may be inoperative if the module was not the last to run.
    """
    try:
        rpcclt = self.editwin.flist.pyshell.interp.rpcclt
    except:
        rpcclt = None
    if rpcclt:
        return rpcclt.remotecall("exec", "get_the_completion_list",
                                 (what, mode), {})
    else:
        if mode == ATTRS:
            if what == "":  # Main module names.
                namespace = {**__main__.__builtins__.__dict__,
                             **__main__.__dict__}
                bigl = eval("dir()", namespace)
                kwds = (s for s in keyword.kwlist
                        if s not in {'True', 'False', 'None'})
                bigl.extend(kwds)
                bigl.sort()
                if "__all__" in bigl:
                    smalll = sorted(eval("__all__", namespace))
                else:
                    smalll = [s for s in bigl if s[:1] != '_']
            else:
                try:
                    entity = self.get_entity(what)
                    bigl = dir(entity)
                    bigl.sort()
                    if "__all__" in bigl:
                        smalll = sorted(entity.__all__)
                    else:
                        smalll = [s for s in bigl if s[:1] != '_']
                except:
                    return [], []

        elif mode == FILES:
            if what == "":
                what = "."
            try:
                expandedpath = os.path.expanduser(what)
                bigl = os.listdir(expandedpath)
                bigl.sort()
                smalll = [s for s in bigl if s[:1] != '.']
            except OSError:
                return [], []

        if not smalll:
            smalll = bigl
        return smalll, bigl

