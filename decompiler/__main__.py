"""Command line front end:  python -m decompiler [options] FILE

FILE is a ``.py`` file (compiled first), a ``.pyc`` file, or ``-`` for source
code on standard input.

  -g, --graph       print the instruction graph (text form) instead of decompiling
  -o, --offsets     with --graph: also show bytecode offsets and line numbers
  -c, --check       decompile, recompile and compare the graphs; print the verdict
  -n, --nopeephole  compile source with the interpreter that has no peephole optimizer
  -r, --render      write the graph of every code object to _graphs/*.svg (needs graphviz)
  -v, --verbose     log what the decompiler is doing
"""
from __future__ import annotations

import argparse
import marshal
import sys

from decompiler import Decompiler
from decompiler.graph.dump import dump_code
from decompiler.utils.bytecode import compile_source, compile_without_peephole


def load(path: str, nopeephole: bool):
    if path.endswith('.pyc'):
        with open(path, 'rb') as f:
            f.read(16)  # magic, flags, mtime/hash, size
            return marshal.load(f)
    if path == '-':
        source = sys.stdin.read()
    else:
        with open(path, encoding='utf-8') as f:
            source = f.read()
    return compile_without_peephole(source) if nopeephole else compile_source(source, path)


def render(code, prefix='module'):
    from types import CodeType
    from decompiler.graph.builder import build_graph
    from decompiler.graph.render import render_graph
    print(render_graph(build_graph(code), prefix + '.svg'))
    for index, const in enumerate(c for c in code.co_consts if isinstance(c, CodeType)):
        render(const, f'{prefix}.{index}_{const.co_name.strip("<>")}')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='python -m decompiler', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('file')
    parser.add_argument('-g', '--graph', action='store_true')
    parser.add_argument('-o', '--offsets', action='store_true')
    parser.add_argument('-c', '--check', action='store_true')
    parser.add_argument('-n', '--nopeephole', action='store_true')
    parser.add_argument('-r', '--render', action='store_true')
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('-s', '--strict', action='store_true',
                        help='stop at the first body that cannot be decompiled instead of marking it')
    args = parser.parse_args(argv)
    if args.verbose:
        from loguru import logger
        logger.enable('decompiler')

    code = load(args.file, args.nopeephole)
    if args.render:
        render(code)
    if args.graph:
        print(dump_code(code, offsets=args.offsets))
        return 0
    if args.check:
        from decompiler.verify import COMPILERS, NOPEEPHOLE, STOCK, verify_code
        result = verify_code(code, COMPILERS[NOPEEPHOLE if args.nopeephole else STOCK],
                             False if args.nopeephole else None)
        if result.decompiled is not None:
            print(result.decompiled)
        if result.ok:
            print('# graphs are equal', file=sys.stderr)
            return 0
        print(f'# FAILED at stage {result.stage!r}', file=sys.stderr)
        print(result.detail, file=sys.stderr)
        return 1
    decompiler = Decompiler(code, strict=args.strict, peephole=False if args.nopeephole else None)
    sys.stdout.write(decompiler.get_source_code())
    for name, error in decompiler.failures:
        print(f'# {name or "<module>"}: {error}', file=sys.stderr)
    return 1 if decompiler.failures else 0


if __name__ == '__main__':
    sys.exit(main())
