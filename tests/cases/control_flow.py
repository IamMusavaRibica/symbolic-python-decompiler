# %% if
if a:
    b

# %% if else
if a:
    b
else:
    c

# %% if not
if not a:
    b
else:
    c

# %% elif chain
if a:
    b
elif c:
    d
elif e:
    f
else:
    g

# %% nested if
if a:
    if b:
        c
    else:
        d
    e
else:
    f

# %% if pass
if a:
    pass
x

# %% if pass else
if a:
    pass
else:
    b

# %% compound conditions
if a and b:
    x
if a or b:
    x
if not (a and b):
    x
if not (a or b):
    x
if a and not b:
    x
if (a or b) and (c or d):
    x
else:
    y
if a and b or c and d:
    x
if a and (b or c) and d:
    x

# %% chained comparison in a test
if a < b < c:
    x
if a < b < c < d:
    x
else:
    y
if not a < b < c:
    x
if a < b < c and d:
    x

# %% conditional expression as test
if (a if b else c):
    x
else:
    y

# %% while
while a:
    b

# %% while else
while a:
    b
else:
    c
d

# %% while true
while True:
    a

# %% while true pass
while True:
    pass

# %% while with break and continue
while a:
    if b:
        break
    if c:
        continue
    d
e

# %% while else with break
while a:
    if b:
        break
    c
else:
    d
e

# %% while true with break
while True:
    if a:
        break
    b
c

# %% while compound condition
while a and b or c:
    d

# %% while not
while not a:
    b

# %% nested while
while a:
    while b:
        if c:
            break
        d
    e

# %% for
for a in b:
    c

# %% for pass
for a in b:
    pass

# %% for else
for a in b:
    c
else:
    d
e

# %% for with break and continue
for a in b:
    if c:
        continue
    if d:
        break
    e
f

# %% for else with break
for a in b:
    if c:
        break
else:
    d
e

# %% for unpacking targets
for a.b, c[d], e[e][f], *g, h in ggrr()()():
    pass

# %% nested for
for a in b:
    for c in d:
        if e:
            break
        f
    else:
        continue
    g

# %% for break in both branches
for a in b:
    if c:
        d
        break
    else:
        e
        break
f

# %% example from the readme
for a in b:
    if test1:
        pass
    else:
        break
    if test2:
        break
else:
    for_else

# %% if in loop tail
for a in b:
    if c:
        d
    else:
        e

# %% while with if else continue
while a:
    if b:
        c
        continue
    else:
        d
    e

# %% loops in functions with returns
def f(a):
    for x in a:
        if x:
            return x
    return None

def g(a):
    while a:
        if a.b:
            return 1
        a = a.next
    return 2

def h(a):
    while True:
        x = a()
        if not x:
            return x
        use(x)

def k(a):
    for x in a:
        for y in x:
            if y:
                return x, y
            if y is None:
                break
        else:
            continue
        z(x)

# %% early returns
def f(a):
    if a:
        return 1
    if not a.b:
        return 2
    elif a.c:
        return 3
    return 4

def g(a):
    if a:
        return 1
    else:
        return 2

def h(a):
    return a if a.b else a.c

def k(a, b, c):
    return a < b < c

def m(a, b, c):
    return a and (b if c else a)
