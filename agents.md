# Working notes for agents and maintainers

Everything non-obvious about this code base, written for whoever (human or agent) picks it up next.
The readme is the user-facing overview; this file is the engineering knowledge.

## Ground rules

- Python 3.9 only. Run everything with the project venv: `./.venv/Scripts/python.exe` (Windows). The
  decompiler must run on the same interpreter version as the bytecode it reads.
- Correctness criterion: **graph equality** of the original code object and the recompiled decompilation,
  recursively over nested code objects (`decompiler/graph/compare.py`). Never byte equality. Line numbers,
  offsets and jump opcodes are layout hints only; nothing that decides equality may look at them.
- Before comparing, both code objects go through `decompiler/graph/peephole.py`, a literal port of CPython
  3.9's `Python/peephole.c` (`PyCode_Optimize`) minus its bail-out on a 255-byte line-table delta. Reason:
  the stock compiler skips the optimizer on any code object with a single source line of 255+ bytes of
  bytecode, so the same program compiles differently depending on its line layout, and the check must not
  care. The port was validated by compiling the whole stdlib with the peephole-less interpreter, running the
  port, and comparing with the stock compiler byte for byte: 701 files, 0 mismatches.
- Two compilers are used in tests: the running interpreter (stock) and a CPython 3.9.22 build with the
  peephole optimizer disabled (`DECOMPILER_NOPEEPHOLE_PYTHON`, default path in `decompiler/utils/bytecode.py`,
  helper `tools/python_compile.py` runs as a long-lived compile server). The no-peephole bytecode is closer to
  what `compile.c` literally emits: unthreaded jumps, unfolded constant tuples, dead code kept.
- Commands: `python -m tests` (`-k TEXT`, `-m stock|nopeephole|both`, `-v`, `-x`, `-d` for dead-code mode),
  `python -m decompiler FILE --check [-n] [--dead-code] [--strict] [--graph [-o]]`,
  `python tools/corpus_check.py [-n] [--dead-code] -j 8` (stdlib by default; pass paths for other corpora),
  `python tools/build_graph.py FILE [-k NAME] [-c] [--dead-code]` (renders one .svg per code object).
- State at the time of writing: default mode round-trips 100% of the stdlib (19 873 code objects) with both
  compilers, the CPython test suite (43 183) and eight large packages (108 182); the test suite is 466/466
  with both compilers in both modes; dead-code mode round-trips the whole stdlib and the eight packages (82 543 objects) as well
  (with the peephole-less compiler, stdlib: 2 failures and 6 objects whose unreachable code differs).
- Git: commit with `git -c commit.gpgsign=false commit ...` (signing is configured and would prompt for a
  passphrase). No `Co-Authored-By` or other attribution trailers in commit messages.

## Architecture

```
decompiler/
  __init__.py      Decompiler class: per-code-object bodies, scope facts, annotations, failures
  __main__.py      CLI (load .py/.pyc, --check, --graph, --dead-code, --strict, -n)
  options.py       global switch: dead_code (env DECOMPILER_DEAD_CODE=1)
  verify.py        round trip: decompile, recompile with the same compiler, compare graphs
  emit.py          ast.unparse + wrapping of lines over 100 columns (cosmetic only)
  postprocess.py   final AST clean-ups
  graph/
    node.py        InstructionNode, edge labels NEXT/TRUE/FALSE/JUMP (+ DEAD for dead-code mode)
    builder.py     CodeGraph: bytecode -> graph; jumps threaded away; dead phase
    analysis.py    dominators, forward_preds, natural loops
    compare.py     dump both (after the peephole port) and diff
    dump.py        canonical text dump (DFS numbering); the comparison key
    peephole.py    port of peephole.c
    render.py      graphviz rendering (dotted dead edges, -c collapses straight-line runs)
  py39/
    walker.py      the structured walker: parse_block, boundaries, jump resolution, dead-code claims
    context.py     Ctx (frame-block stack), State (stack machine), Block, frame blocks and their unwind()
    control.py     if / while / for / loops, break targets, conditions (Cond)
    exceptions.py  try/except, try/finally, with, async variants, handlers
    expressions.py expression ops, calls, comprehensions
    statements.py  stores, deletes, assignments, imports, annotations
    definitions.py functions, classes, lambdas, nested code objects
    values.py      stack markers (WithExitMarker, TargetMarker, exception triples)
  utils/bytecode.py  compile_source, compile_without_peephole (compile server), flags
tools/            corpus_check.py, build_graph.py, python_compile.py
tests/            __main__.py runner; cases/*.py are plain snippets split by `# %% name` lines
```

### The graph (`graph/builder.py`)

- One node per *reachable* instruction. Unconditional jumps are never nodes: `resolve()` threads through
  them, so control flow is only edges: `next` (fall-through), `true`/`false` (POP_JUMP_IF_*, normalised so
  the label is the truth value, not the opcode), `jump` (the other way out of JUMP_IF_*_OR_POP, FOR_ITER,
  SETUP_*, JUMP_IF_NOT_EXC_MATCH).
- Pseudo nodes: `*ENTRY` (root) and `*SPIN` (a cycle of only jumps, `while True: pass`).
- `_undo_jump_simplification` puts back the JUMP_IF_x_OR_POP chains that the peephole optimizer threaded
  through each other (`(a or b) and c`), so boolean expressions keep their structure.
- Layout hints kept on nodes: `offset`, `lineno`, `back_jumps` (offsets of jumps that go backwards to the
  node), `threaded` / `threaded_via` (the fall-through went through an unconditional jump at that offset).
- `CodeGraph.dead_entries`, `node.dead`, `node.dead_next`: dead-code mode only (see below).

### The walker (`py39/walker.py`)

- `parse_block(start, stack, ctx)` runs the stack machine statement by statement: `dispatch` calls
  `op_<OPNAME>` handlers which push expressions on `State.stack` or emit statements. A handler returns
  `None` (fall to `.next`), a node (continue there), or `TERMINATED`.
- `at_boundary` decides where a block ends: a node in `ctx.stops`, a node not dominated by `ctx.start`,
  a BLOCK_END op (POP_BLOCK, POP_EXCEPT, RERAISE, END_ASYNC_FOR, WITH_EXCEPT_START, POP_TOP at base depth)
  at statement level, the function's implicit end when inside a nested block.
- `Ctx.fblocks` mirrors the compiler's frame-block stack (`LoopBlock`, `TryExceptBlock`, `FinallyTryBlock`,
  `FinallyEndBlock`, `WithBlock`, `HandlerCleanupBlock`, ...). Each has `unwind(walker, node, preserve_tos)`
  that matches the clean-up code the compiler emits when a jump statement leaves that block.
- `resolve_jump` / `_resolve` interpret a block exit as `break`, `continue` or `return <constant>` by
  unwinding the frame blocks innermost-out and looking at what is behind the clean-up. `owned` matters:
  clean-up code shared with other paths is never the clean-up of *this* jump.
- `skip_finally_copy` walks an inlined copy of a finally body in lockstep with the exception-path copy
  (which has been parsed): matching instructions, branches followed on both sides; paths on which the
  copies differ leave the finally body for good (different clean-up) and are not followed. `copy_nodes`
  and `copy_ends` are recorded for later rules.
- `find_break_target` (control.py) guesses where `break` goes before the body is parsed; a wrong guess
  shows as a body that makes no sense, and `parse_while` retries with less guessing.
- `SyntheticLoop` / `check_missing_loop`: a `while True` whose every path breaks has no back edge at all;
  the walker infers the loop when a `break`-shaped exit is found with no loop in context.
- Conditions (`Cond`) are built from chains of branches; `parse_if` picks the body as the fall-through
  side of the last jump, and keeps `if/else` with returns in both branches only when something follows.
- The `peephole` flag was removed: the comparison is peephole-agnostic now, so `f(1, 2, **k)` is always
  spelled with plain arguments (an empty constant tuple is still `*()`).

### Scoping and silent facts (`__init__.py`)

- Scope is decided from the code objects, not from a symtable: cell/free variables, `nonlocal`/`global`
  declarations, and comprehension stores are reconstructed (`_declarations`, `_silent_scope_facts`,
  `_comprehension_stores`). Facts that produce no bytecode (a cell bound only in dead `if 0:` code) are
  restored with `if False:` blocks and `name: object` annotations so the recompile has the same cells.
- Annotations: non-simple targets (`(x): ann = v`) and the `__annotations__` handling are restored in
  `_restore_annotations`. Constant tests are written `if 1 or 1:` because the compiler drops `if 1:`.
- A body that cannot be decompiled is replaced by `__decompilation_failed__('reason')` unless `--strict`;
  `Decompiler.failures` lists them.

## Dead-code mode (`--dead-code`, off by default)

Everything that exists only for this mode carries `[dead-code]` in a comment or docstring, so it can be
found, fixed or removed as one piece; with the switch off the decompiler behaves exactly as before.

### Builder

- After the live graph is built, a dead phase runs: whatever physically follows a terminator
  (RETURN_VALUE, RAISE_VARARGS, RERAISE) or an unconditional jump, and is not reached otherwise, becomes a
  dead region entry. Entry anchors are recorded in `graph.dead_entries` as `(anchor, entry)` pairs and the
  anchor gets `dead_next` (kept out of `out`, so `successors()` never yields it).
- Dead regions are built like live code, in order of discovery, except that their own unconditional jumps
  stay nodes (`JUMP`), while live jumps that live code goes through (`live_jumps`) are still threaded.
  A live jump sitting right after a terminator (a branch was threaded through it) is skipped so the region
  behind it is still seeded.
- Dropped on purpose: regions of nothing but jumps (a loop's back jump after `break`, the jump over a
  handler after `return`): which of two spellings with the same live graph was written decides whether
  they exist, and the walker handles that by spelling. A second (live) terminator into an existing dead
  node gets an extra entry, unless that node is a jump.
- Dead-to-live edges are not recorded in `preds` or in the networkx graph; `_number` visits `dead_next`
  last; the dump prints `D->n`.

### Walker: claiming regions

- `dead_continuation(head, ctx)` claims the lowest-offset unclaimed region anchored at a node dominated by
  the statement head: that is the code the author wrote after the jump statement. The implicit end of the
  function is only claimed at the top level. Nodes reached by flow are marked claimed too.
- A claimed continuation is parsed *tentatively* (`parse_block` in a try); a fragment that does not parse,
  or runs to the implicit end inside a nested block, is dropped (logged with `-v`). Before parsing, a
  `return_fragment` (`(ROT_TWO|POP_TOP)* RETURN_VALUE`, the rest of a return whose inlined finally jumped
  away) is skipped, and a region starting at an exception handler entry is dropped.
- A continuation that starts with a BLOCK_END op is the epilogue of the construct the jump left: the block
  stops there and returns it as its exit, and the construct reads where the epilogue goes (handlers:
  `dead_after`; try bodies: `after_body`; with bodies via `unwind` + `follow_dead_jumps`). Constructs that
  consume such an exit claim it. At the top level (no frame blocks) such a region is nobody's.
- `drop_foreign_epilogue`: a block exit that is a dead handler entry (WITH_EXCEPT_START, DUP_TOP, POP_TOP)
  rather than the construct's own epilogue means the block simply ended.
- `skip_finally_copy` claims regions anchored inside the copy (`copy_nodes`, including dead jump nodes it
  stepped over and the divergent clean-up chain of a jump statement leaving the finally body, as far as
  nodes dominated by the copy's start), except the one hanging off the copy's last node (that follows the
  jump statement itself). Regions hanging off copy-ending RETURN_VALUEs are claimed (duplicated returns
  the optimizer left standing), except the statement's own return, the one laid out last.
- `jump_copy`: a POP_BLOCK/POP_EXCEPT at statement level that starts a jump statement's clean-up chain
  ending at a dead-region anchor (`copy_has_dead_continuation`) is resolved as that jump.

### Walker: spelling decisions that only dead code tells apart

- `parse_if`: `if a: return 1 else: return 2` keeps the else form when unreachable code follows
  (`closes_with_dead(second, own=True)`): the end of an else clause is a jump target, which stops the
  optimizer from wiping the code after the return. `own` ignores a closing RERAISE that belongs to an
  enclosing statement's handler. `dead_else` likewise.
- `parse_if`, both branches jump: the side that reaches the loop header through the loop's own end jump
  (`through_loop_end`: the branch targets `loop.end_offset`, or the header directly, since the stock
  optimizer threads and erases that jump; or a predecessor with `threaded_via == end_offset`) is the end of
  the loop body, not a `continue`.
- `behind_loop` / `_resolve`: live code laid out behind the innermost loop and reached only through
  unconditional jumps (predecessors' threaded fall-throughs) is the code after the loop, where `break`
  goes, even when a single path reaches it. Parsed inline it would be a `return` inside the loop with a
  dead implicit return behind. Applied in `parse_branch` and the no-break-yet case of `_resolve`; applying
  it as a general `at_boundary` rule was too aggressive.
- `find_break_target`: when the loop's normal exit is the function end, `break` goes there.
- try/except: `dead_try_join` decides whether the code after the body is an else clause from where the
  handlers' dead epilogues go (`Handler.dead_after`, found through `handler_epilogue` even when a nested
  construct closed the handler's jump) or from unreachable code behind the clause (own only).
  `fell_through`: a handler whose continuation resolves to the loop's own jump fell through, unless
  `handler_jumped` finds its dead POP_EXCEPT epilogue (anchored inside the finally copy its chain ran
  through, or after a dead jump).
- try/except vs try/finally with a POP_TOP handler: a real try/except body always jumps over its handler
  (`body.exit.threaded`); a body that falls into the code behind it is a try/finally whose copy starts
  with a jump statement or return (`finally: return` and `except: return` have identical live code).
- try/finally: `FinallyTryBlock.dead_normal_exit` (the body's normal exit exists only as dead code: every
  live way out is a jump); `leaves_by_return` then appends the `return <constant>` that the clean-up chain
  spells; the dead normal exit is the statement's continuation.
- Code running off the end of the code object (no implicit return after a block that returned) ends like
  a module body.

### The dead-code verdict

`compare_code` compares code object by code object (`compare_objects`): 'equal', 'live' (the live
graphs match, only the unreachable code differs) or 'differ'. A round trip with no 'differ' passes;
'live' objects are reported (`RoundTrip.stage == 'dead-code'`, the test runner's NOTE lines, the
corpus tool's separate count) and `verify.annotate` puts a comment above each such definition in the
decompiled source. So a wrong guess about unreachable code never fails a decompilation whose live
code is right.

### Remaining dead-mode failures

None in the stdlib with the stock compiler. With the peephole-less compiler: 2 failures (idlelib
`outwin.file_line_helper`, `rpc.localcall`) and 6 objects with differing unreachable code, plus three
reduced shapes in the suite that show as NOTE: `if / elif / else` whose first and last branches
`continue`; `while True` with `if c: return` followed by try/except/else ending in `continue`; a
`return` inside an `if` inside a nested loop whose else branch ends in try/finally. All are about which
jump to the loop header is an explicit `continue` when no jump was threaded (`explicit_continue`,
`through_loop_end`), which the stock rules decide from the erased or dead end jump; on unoptimized
code the opcode (JUMP_ABSOLUTE vs JUMP_FORWARD) would be the exact signal, and an unthreaded
jump-to-jump chain (`chains` in the builder) proves a code object is unoptimized.

Approach that worked for every case: dump with `--graph -o` (offsets), map each dead region to the
construct and copy that emitted it (the compiler emits a finally body up to three times: return path,
normal path, exception path; `continue` / `break` inside copies unwind different block sets; the stock
optimizer wipes code after a RETURN_VALUE up to the next jump target, SETUP_FINALLY or RERAISE,
threads jumps to jumps and turns a threaded JUMP_FORWARD into JUMP_ABSOLUTE), then find which claim or
spelling rule misreads it. Reduce to a minimal shape first; add it to `tests/cases/torture.py`.

### A direction worth taking: post-dominators

Several dead-mode rules reason about layout (`end_offset`, `behind_loop`, `bound`, "the last jump
before the dead end jump") to answer one question: *does this node belong to the construct being
parsed, or to what follows it?*  `analysis.py` has dominators; it does not have post-dominators
(reverse dominators).  With them, "the code after the loop" is the immediate post-dominator of the
loop's exits, "the join of an if" is the immediate post-dominator of its branches, and "a jump
statement" is an edge that leaves a construct without passing through that post-dominator.  Most
of the layout rules are approximations of those three facts, and layout breaks exactly where the
stock optimizer erased a jump.  Rebuilding the claiming layer on post-dominators (computed on the
live graph, dead regions attached afterwards) is the cleanest way to retire the heuristics.

## Debugging tips

- `python -m decompiler FILE -v` enables loguru debug output: dead regions claimed, dropped continuations,
  `if at ...` / `try at ...` / `try/finally at ...` decision lines.
- `--graph -o` prints the dump with `@offset Lline`; `D->` marks dead edges, `->n` non-sequential live ones.
- `tools/build_graph.py FILE -k NAME -c` renders the graph; with no match it lists the code object names.
- The test runner prints source, decompiled text and the unified diff of the dumps with `-v`.
- Minimal reproductions used during the dead-code work live in `tests/cases/torture.py` under
  `dead-code shapes: ...`.
