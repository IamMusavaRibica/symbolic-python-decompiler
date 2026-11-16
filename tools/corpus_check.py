"""Measure the decompiler on a pile of real source files (by default the standard library).

    python tools/corpus_check.py [PATH ...] [-j N] [-n] [--limit N] [--show KIND] [-o report.json]

Every ``.py`` file is compiled, decompiled in tolerant mode, recompiled and
compared *per code object*, so one bad function does not hide the rest of a
module.  The summary groups the failures by reason; ``--show TEXT`` prints the
places whose reason contains TEXT.

  -j N        worker processes (default: number of CPUs)
  -n          compile with the interpreter without peephole optimizer
  --limit N   stop after N files
  --timeout S seconds allowed per file (default 120)
"""
from __future__ import annotations

import argparse
import collections
import json
import multiprocessing
import os
import re
import sys
import time
import tokenize
from types import CodeType

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

SKIP_DIRS = {'test', 'tests', 'idle_test', 'site-packages', '__pycache__', 'lib2to3'}


def find_files(paths):
    for path in paths:
        if os.path.isfile(path):
            yield path
            continue
        for root, dirs, files in os.walk(path):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(files):
                if name.endswith('.py'):
                    yield os.path.join(root, name)


def normalise(reason: str) -> str:
    """Strip what is specific to one occurrence so equal problems group together."""
    reason = re.sub(r' at node \d+ <.*', '', reason)
    reason = re.sub(r' in \S+$', '', reason)
    reason = re.sub(r'<[^<>]* object at 0x[0-9A-Fa-f]+>', '<obj>', reason)
    return reason.strip()[:160]


def children_of(code: CodeType):
    """Nested code objects in the order the canonical dump lists them."""
    from decompiler.graph.builder import build_graph
    return [node.argval for node in build_graph(code).nodes
            if node.opname == 'LOAD_CONST' and isinstance(node.argval, CodeType)]


def own_dump(code: CodeType) -> str:
    from decompiler.graph.builder import build_graph
    from decompiler.graph.dump import code_header, dump_graph
    return code_header(code) + '\n' + '\n'.join(dump_graph(build_graph(code)))


def compare_trees(original: CodeType, recompiled: CodeType, path: str, mismatches: list, counter: list):
    """Compare two code objects level by level; record the path of every code object that differs."""
    counter[0] += 1
    same = own_dump(original) == own_dump(recompiled)
    if not same:
        mismatches.append(path)
    a, b = children_of(original), children_of(recompiled)
    if same or len(a) == len(b):
        for x, y in zip(a, b):
            compare_trees(x, y, f'{path}.{x.co_name}', mismatches, counter)
    else:
        counter[0] += sum(1 for _ in walk(original)) - 1  # children cannot be paired up


def walk(code: CodeType):
    yield code
    for const in code.co_consts:
        if isinstance(const, CodeType):
            yield from walk(const)


def check_file(args):
    path, nopeephole = args
    from decompiler import Decompiler
    from decompiler.utils.bytecode import compile_flags_for, compile_source, compile_without_peephole
    result = {'path': path, 'objects': 0, 'bad': [], 'fatal': None, 'seconds': 0.0}
    started = time.time()
    try:
        try:
            with tokenize.open(path) as f:  # honours the coding declaration
                source = f.read()
        except (SyntaxError, UnicodeDecodeError):
            result['fatal'] = 'skipped: cannot be read'
            return result
        compiler = compile_without_peephole if nopeephole else (lambda src, flags=0: compile_source(src, path, flags))
        try:
            code = compiler(source)
        except (SyntaxError, ValueError, UnicodeDecodeError):
            result['fatal'] = 'skipped: does not compile'
            return result
        result['objects'] = sum(1 for _ in walk(code))
        decompiler = Decompiler(code, strict=False, peephole=not nopeephole)
        try:
            text = decompiler.get_source_code()
        except Exception as e:
            result['fatal'] = f'decompile: {normalise(str(e)) or type(e).__name__}'
            return result
        failed = {}
        for name, error in decompiler.failures:
            failed[name] = normalise(str(error))
        try:
            recompiled = compiler(text, compile_flags_for(code))
        except Exception as e:
            result['fatal'] = f'recompile: {type(e).__name__}: {normalise(str(e))}'
            result['bad'] = [[name, 'decompile: ' + reason] for name, reason in failed.items()]
            return result
        mismatches, counter = [], [0]
        compare_trees(code, recompiled, '<module>', mismatches, counter)
        for name in mismatches:
            if name in failed:
                result['bad'].append([name, 'decompile: ' + failed[name]])
            elif not any(name.startswith(f + '.') or name == f for f in failed):
                result['bad'].append([name, 'compare: graphs differ'])
    except Exception as e:  # keep the pool alive whatever happens
        result['fatal'] = f'crash: {type(e).__name__}: {normalise(str(e))}'
    finally:
        result['seconds'] = round(time.time() - started, 2)
    return result


def explain(path: str, nopeephole: bool, only: str = '', context: int = 3) -> None:
    """Print, for every code object of ``path`` that does not round-trip, how the graphs differ."""
    import difflib
    from decompiler import Decompiler
    from decompiler.utils.bytecode import compile_flags_for, compile_source, compile_without_peephole
    with open(path, encoding='utf-8') as f:
        source = f.read()
    compiler = compile_without_peephole if nopeephole else (lambda src, flags=0: compile_source(src, path, flags))
    code = compiler(source)
    decompiler = Decompiler(code, strict=False, peephole=not nopeephole)
    text = decompiler.get_source_code()
    for name, error in decompiler.failures:
        if only in name:
            print(f'### {name}: decompile: {error}')
    try:
        recompiled = compiler(text, compile_flags_for(code))
    except SyntaxError as e:
        print(f'### recompile: {e}')
        lines = text.splitlines()
        for number in range(max((e.lineno or 1) - 4, 0), min((e.lineno or 1) + 2, len(lines))):
            print(f'{number + 1:5} {lines[number]}')
        return

    def visit(a, b, name):
        da, db = own_dump(a), own_dump(b)
        if da != db and only in name and not any(name == f or name.startswith(f + '.')
                                                 for f, _ in decompiler.failures):
            print(f'### {name}: graphs differ (line {a.co_firstlineno})')
            for line in difflib.unified_diff(da.splitlines(), db.splitlines(), 'original', 'recompiled',
                                             n=context, lineterm=''):
                print(line)
        ca, cb = children_of(a), children_of(b)
        if da == db or len(ca) == len(cb):
            for x, y in zip(ca, cb):
                visit(x, y, f'{name}.{x.co_name}')

    visit(code, recompiled, '<module>')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('paths', nargs='*')
    parser.add_argument('-j', type=int, default=os.cpu_count() or 2)
    parser.add_argument('-n', action='store_true')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--show', default=None)
    parser.add_argument('-o', default=None)
    parser.add_argument('--explain', action='store_true',
                        help='single file: print the graph differences of every bad code object')
    parser.add_argument('-k', default='', help='with --explain: only code objects whose name contains this')
    args = parser.parse_args(argv)
    if args.explain:
        for path in args.paths:
            explain(path, args.n, args.k)
        return 0

    paths = args.paths or [os.path.dirname(os.__file__)]
    files = list(find_files(paths))
    if args.limit:
        files = files[:args.limit]
    started = time.time()
    results = []
    with multiprocessing.Pool(args.j, maxtasksperchild=20) as pool:
        pending = [(f, pool.apply_async(check_file, ((f, args.n),))) for f in files]
        for path, job in pending:
            try:
                results.append(job.get(timeout=args.timeout))
            except multiprocessing.TimeoutError:
                results.append({'path': path, 'objects': 0, 'bad': [], 'fatal': 'timeout', 'seconds': args.timeout})
        pool.terminate()

    objects = sum(r['objects'] for r in results)
    reasons = collections.Counter()
    examples = collections.defaultdict(list)
    bad_objects = 0
    clean_files = 0
    for r in results:
        if r['fatal']:
            reasons['FILE ' + r['fatal']] += 1
            examples['FILE ' + r['fatal']].append(r['path'])
            if r['fatal'].startswith('skipped'):
                continue
            bad_objects += r['objects']
        else:
            bad_objects += len(r['bad'])
            if not r['bad']:
                clean_files += 1
        for name, reason in r['bad']:
            reasons[reason] += 1
            examples[reason].append(f"{r['path']}::{name}")
    usable = [r for r in results if not (r['fatal'] or '').startswith('skipped')]
    print(f'{len(usable)} files, {clean_files} fully correct '
          f'({100.0 * clean_files / max(len(usable), 1):.1f}%)')
    print(f'{objects} code objects, {objects - bad_objects} correct '
          f'({100.0 * (objects - bad_objects) / max(objects, 1):.1f}%)   [{time.time() - started:.0f}s]')
    print()
    for reason, count in reasons.most_common():
        print(f'{count:6}  {reason}')
        if args.show is not None and args.show in reason:
            for example in examples[reason][:40]:
                print(f'          {example}')
        else:
            print(f'          e.g. {examples[reason][0]}')
    slow = sorted(results, key=lambda r: -r['seconds'])[:5]
    print('\nslowest: ' + ', '.join(f"{os.path.basename(r['path'])} {r['seconds']}s" for r in slow))
    if args.o:
        with open(args.o, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
