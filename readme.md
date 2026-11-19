# Python 3.9 decompiler

A decompiler for CPython 3.9 bytecode, meant as a continuation of
[uncompyle6](https://github.com/rocky/python-uncompyle6), which stops at 3.8.

Taken inspiration from [bytecode_simplifier](https://github.com/extremecoders-re/bytecode_simplifier): the bytecode
is represented by a graph ([`networkx`](https://networkx.org/)) in which the jump opcodes are silently dropped.
Decompiled code therefore does not always compile back to *identical* bytecode, but to bytecode with the same graph.
For example these two compile to different bytecode (`a` and `b` swap places) and to the same graph:

```python
if not x:        if x:
    a                b
else:            else:
    b                a
```

You must run this on the same Python version that you're decompiling. Currently, only 3.9.

## Usage

```python
from decompiler import decompile
print(decompile(compile(source, '<source>', 'exec')))
```

```
python -m decompiler file.py            decompile (a .py is compiled first; .pyc works too)
python -m decompiler file.py --strict   stop at the first body that cannot be decompiled
python -m decompiler file.py --graph    print the instruction graph as text
python -m decompiler file.py --check    decompile, recompile, compare the graphs
python -m decompiler file.py --render   write the graphs to _graphs/*.svg (needs graphviz)
```

`Decompiler(code, strict=False)` (what the command line does unless `--strict`) keeps going when a function or
class body cannot be decompiled: the body is replaced by a call to `__decompilation_failed__('reason')` and
listed in `.failures`.

## How it works

**The graph** ([decompiler/graph](decompiler/graph)). One node per reachable instruction. `JUMP_FORWARD` /
`JUMP_ABSOLUTE` are not nodes, the edges are threaded through them. Edges are labelled: `next` (fall through),
`true` / `false` (both conditional pop-jumps are normalised to these, which is what makes the two snippets above
equal) and `jump` (the other way out of `FOR_ITER`, `SETUP_FINALLY`, `JUMP_IF_TRUE_OR_POP`, ...).
`dump_code()` prints a graph in a canonical text form (node numbers follow a fixed traversal, no offsets, no
line numbers); two code objects have isomorphic graphs exactly when their dumps are equal, so comparing
graphs is comparing text, and a failed comparison is a readable diff.

**The decompiler** ([decompiler/py39](decompiler/py39)) walks the graph from the entry and executes the value
stack symbolically: stack entries are `ast` expressions. Straight-line instructions are `op_<OPNAME>` methods
(adding an opcode means adding a method). Control instructions start a parser for the construct, which mirrors
what `compile.c` emits for it. Three ideas carry most of the weight:

* *Blocks end where the graph says so.* A block is parsed until a node that is not dominated by the block's
  first node (the join after an `if`), a node the context names (the loop head), or an instruction that belongs
  to the enclosing construct (`POP_BLOCK`, ...). Whoever asked for the block decides what its exit means.
* *The frame block stack of the compiler is mirrored.* Knowing which loops, `try`, `with` and handler bodies are
  open is what makes the clean-up code the compiler inlines for `break`, `continue` and `return` recognisable
  (`compiler_unwind_fblock`), including the `ROT_FOUR` sequences of `return value` inside `except` and `finally`.
* *Layout is only a hint.* Offsets are never needed for correctness; they break ties between spellings that
  compile to the same graph (is the code after a `while True:` loop part of the breaking branch or not).

| module | what lives there |
|---|---|
| `walker.py` | blocks, boundaries, resolving exits as `break` / `continue` / `return` |
| `expressions.py` | everything that only computes values |
| `statements.py` | stores, deletes, imports, `return`, `raise` |
| `control.py` | conditions (`and` / `or` / `not`, chained comparisons), `if`, conditional expressions, loops |
| `exceptions.py` | `try`, `with`, handlers |
| `definitions.py` | functions, lambdas, classes, comprehensions |
| `context.py`, `values.py` | frame blocks, parsing state, what can sit on the symbolic stack |

Afterwards [postprocess.py](decompiler/postprocess.py) tidies the tree (`with a: with b:` becomes `with a, b:`
and so on, only rewrites that keep the graph) and [emit.py](decompiler/emit.py) writes it with `ast.unparse`.

## Testing

A decompilation is correct when compiling the decompiled source gives the same graph as the original code
object, recursively for nested code objects ([decompiler/verify.py](decompiler/verify.py)).

```
python -m tests                      run the cases in tests/cases (-k TEXT, -v, -x, -m stock|nopeephole)
python -m unittest tests.test_roundtrip
python tools/corpus_check.py [PATH]  round-trip every code object of a source tree (default: the stdlib)
python tools/corpus_check.py --explain FILE [-k NAME]    show how the graphs of the bad ones differ
python tools/build_graph.py FILE [-k NAME]               render the instruction graphs as .svg and open them
```

Test cases are plain Python snippets separated by `# %% name` lines. Every case runs with two compilers: the
running interpreter, and (when available) a CPython build without peephole optimizer, whose bytecode is closer to
what `compile.c` literally emits. The second one is found through `DECOMPILER_NOPEEPHOLE_PYTHON`
(see [decompiler/utils/bytecode.py](decompiler/utils/bytecode.py), helper script: [tools/python_compile.py](tools/python_compile.py)).
Before two graphs are compared, both code objects go through a port of 3.9's peephole optimizer
([decompiler/graph/peephole.py](decompiler/graph/peephole.py)): the stock compiler skips the optimizer on a code object
with a source line of 255+ bytes of bytecode, so the same program compiles differently depending on its line
layout, and the comparison must not care.

State of things: the standard library of 3.9 round-trips completely (641 files, 19 873 code objects), and so do
sympy, django, sqlalchemy, twisted, botocore, pygments, astroid and jedi (108 182 code objects). The CPython test
suite round-trips completely as well (43 183 code objects).

## Unreachable code (experimental, off by default)

The graph only holds reachable instructions, so statements after an unconditional `raise`, `return`, `break`
or `continue` are normally lost, although the compiler emitted them. `--dead-code` (`python -m decompiler`,
`python -m tests -d`, `tools/corpus_check.py --dead-code`, `tools/build_graph.py --dead-code`; in code
`decompiler.options.dead_code = True` or the environment variable `DECOMPILER_DEAD_CODE=1`) follows the
instruction after every terminator as well (an edge labelled `dead`, `D->n` in the text dump, dotted in
the drawings). Unreachable code is then part of the graph and of the comparison, and a block that ended in a
jump statement continues with the dead statements that follow it. Unconditional jumps inside dead code stay
nodes (a dead `break` is nothing else), regions made of nothing but jumps are dropped (they depend on which
of two equivalent spellings was written), and spellings that only differ in unreachable code are told apart
by it (`if a: return 1 else: return 2` leaves the function's implicit `return None` behind,
`if a: return 1` + `return 2` does not).

State of things: the test cases pass 384 of 404 runs in this mode, the standard library 19 787 of 19 873
code objects (99.6%). What fails are mostly try statements whose body or handlers end in a jump statement:
their dead epilogues (`POP_BLOCK`, `POP_EXCEPT`, the jump over the handlers) are sometimes read as statements,
which either yields a different spelling (`compare: graphs differ`) or no parse (`cannot tell where control
goes from here`).
Everything that exists only for this mode is marked `[dead-code]` in the source, so it can be found, fixed
or removed as one piece; with the option off the decompiler behaves exactly as before.

## Known limitations

* Unreachable code is not decompiled unless `--dead-code` is given (see above): the graph only contains
  reachable instructions, so statements after an unconditional `raise` or `return` are lost (a module-level
  bare `raise` makes the rest of the module disappear). What unreachable code decided about scopes (locals,
  closures, generators) is restored either way.
* Annotations of local variables leave no trace in the bytecode. Those of non-simple targets (`x.y: int`)
  are restored, but not always on the statement the author annotated.
* Where several spellings compile to the same graph, the one that is written is not always the one the
  author chose (`try: return 1` + `finally:` comes back as `try: pass` + `finally:` followed by `return 1`).

If you find code that does not decompile correctly, please open an issue with the source: it is better to tackle
decompilation issues early than having to deal with them later.
