# %% try except
try:
    a
except:
    b

# %% try except typed
try:
    a
except E:
    b
c

# %% try except as
try:
    a
except E as e:
    b
c

# %% try multiple handlers else
try:
    a
except E1 as e1:
    b
except (E2, E3):
    c
except:
    d
else:
    e
f

# %% try finally
try:
    a
finally:
    b
c

# %% try except finally
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
after

# %% nested try
try:
    try:
        a
    except E:
        b
    c
except F:
    d
finally:
    try:
        e
    finally:
        f

# %% raise in handler
try:
    a
except E as e:
    raise F from e
except G:
    raise

# %% with
with a:
    b
c

# %% with as
with a as b:
    c

# %% with multiple
with a as b, c as d:
    pass

# %% with unpacking target
with a as (b, c), d as e.f:
    g

# %% try in loop
for x in y:
    try:
        a
    except E:
        continue
    except F:
        break
    else:
        b
    finally:
        c
    d
e

# %% while with try
while a:
    try:
        b
    except E:
        break
    c

# %% with in loop
for x in y:
    with a:
        if b:
            continue
        if c:
            break
        d
e

# %% finally with control flow
try:
    a
finally:
    if b:
        c
    for d in e:
        f

# %% return in try
def f(a):
    try:
        return a()
    except E:
        return 1
    finally:
        cleanup()

# %% return const in try finally
def f(a):
    try:
        if a:
            return 1
        b()
    finally:
        c()
    return 2

# %% return in with
def f(a):
    with a as b:
        return b.c
def g(a):
    with a:
        return 1
def h(a):
    for x in a:
        with x as y:
            if y:
                return y
            if x:
                return 5
    return None

# %% rot_four: return value in except handler
def f():
    try:
        g()
    except E:
        return h()

# %% rot_four: return value in named except handler
def f():
    try:
        g()
    except E as e:
        return h(e)

# %% rot_four: return value in finally body
def f():
    try:
        g()
    finally:
        return k()

# %% rot_four: handler and finally together
def f(a):
    try:
        g()
    except E as e:
        return h(e)
    finally:
        return k()

# %% rot_four: finally in loop
def g2(a):
    for x in a:
        try:
            pass
        finally:
            return x + 1

# %% rot_four: nested handlers
def f(a):
    try:
        g()
    except E:
        try:
            h()
        except F as e:
            return a + e
        return a * 2

# %% rot_four: handler inside with inside loop
def f(a):
    for x in a:
        with x:
            try:
                g()
            except E:
                return x.y

# %% return const from handlers
def f():
    try:
        g()
    except E:
        return 1
    except F as e:
        return None
    return 2

# %% break and continue in finally and handlers
def f(a):
    for x in a:
        try:
            g()
        except E:
            if x:
                continue
            break
        finally:
            h()
    return x

# %% try else return
def f():
    try:
        a()
    except E:
        b()
    else:
        return 1
    return 2

# %% try in while true
def f():
    while True:
        try:
            x = g()
        except E:
            continue
        else:
            break
    return x
