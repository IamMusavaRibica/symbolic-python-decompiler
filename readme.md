# Python 3.9 decompiler

I worked on it for a while, satisfied with the progress by far and decided to open source it.

Taken inspiration from [bytecode_simplifier](https://github.com/extremecoders-re/bytecode_simplifier): the bytecode
structure is represented by a [`networkx.DiGraph`](https://networkx.org/documentation/stable/reference/classes/digraph.html)
and the jump opcodes are silently ignored, which sometimes causes different control flow, but the code is equivalent,
see [example.py](example.py), try to decompile it and study both codes, you will notice how it's equivalent, then
compare the `dis.dis` and see it's different.

The graph structure undergoes a series of transformations, then we build the basic blocks from the beginning, taking
common patterns (which appear after simplifications) and then converting them into code segments. For the purpose of 
assisting simplification, the decompiler introduces some new opcodes, prefixed by an asterisk. Take a look at them
[here](decompiler/models.py)

Entire transformations can be nicely described visually, but I haven't done that as there is no reason to do so if
nobody is going to read it. The [transformer](decompiler/transformers/transformer_3_9.py) code is quite ugly and hard
to comprehend. A good example of a transformation is the `_binary_operation` method, it's probably simple enough to
understand without a visual representation. I agree, however, that the code can be written very well, e.g. starting
from extending `DiGraph` with custom methods that better handle opcode transformations. If you are interested in 
improving this further, please contact me and I'll be happy to explain the inner workings of the decompiler.

With that said, if you find an edge case that does not decompile correctly (or fails to decompile), but the code
contains only things that are labelled as currently supported below, please open an issue! It's better to tackle
decompilation issues early than having to deal with them later.


### Currently supported:
- [x] all sorts of literals
- [x] assignments (`a = b`, `a.b = c`, `a[b] = c`)
- [x] augmented assignments (`a += b`, `a.b += c`, `a[b] += c`)
- [x] assignment expressions (`(a := b)`)
- [x] function and method calls, with positional and keyword arguments
- [ ] `await` expressions
- [ ] `if` statements (only simple ones are implemented right now)
- [ ] chained comparisons (`a < b < c`)
- [ ] chained boolean operations (`a and b or c`)
- [x] format strings
- [x] `try` statements, including `finally` and `else`
- [x] `raise` statements
- [x] `del` statements
- [x] `return` statements
- [x] `for` loops, including `continue`, `break` and `else`
- [x] `async for` loops
- [ ] `while` loops
- [x] `with` statements
- [x] `async with` statements
- [ ] `def` functions
- [ ] `async def` functions
- [ ] `lambda` functions
- [ ] `class` statements
- [ ] generator functions
- [ ] list comprehensions
- [ ] set comprehensions
- [ ] dict comprehensions
- [ ] generator expressions  
...and anything else not written in this list


You must run this on the same Python version that you're decompiling. Currently, only 3.9.

Requires graphviz executables in PATH to render graphs, if you don't want that, replace `decompiler.utils.render_graph` 
with a no-op function