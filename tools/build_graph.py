"""Render the instruction graph of a file without decompiling anything.

    python tools/build_graph.py FILE [-k NAME] [-d DIR] [--no-open]

FILE is a .py (compiled first) or a .pyc.  One .svg per code object is written to
DIR (default: _graphs), named after the code object's qualified path; -k keeps only
the code objects whose name contains NAME.  The files are opened afterwards unless
--no-open is given.  Needs pydotplus and the graphviz executables (``dot``).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from decompiler.__main__ import load  # noqa: E402
from decompiler.graph.builder import build_graph  # noqa: E402
from decompiler.graph.render import render_graph  # noqa: E402


def code_objects(code, path=''):
    name = path or code.co_name
    yield name, code
    for const in code.co_consts:
        if hasattr(const, 'co_code'):
            yield from code_objects(const, f'{name}.{const.co_name}')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[1])
    parser.add_argument('file')
    parser.add_argument('-k', '--keep', default=None, help='only code objects whose name contains this')
    parser.add_argument('-d', '--directory', default='_graphs')
    parser.add_argument('-n', '--nopeephole', action='store_true', help='compile without the peephole optimizer')
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args(argv)

    code = load(args.file, args.nopeephole)
    written = []
    names = []
    for name, obj in code_objects(code):
        names.append(name)
        if args.keep is not None and args.keep not in name:
            continue
        safe = ''.join(c if c.isalnum() or c in '._-' else '_' for c in name)
        filename = f'{safe}.svg'
        render_graph(build_graph(obj), filename, args.directory)
        written.append(os.path.join(args.directory, filename))
        print(written[-1])
    if not written:
        print(f'no code object name contains {args.keep!r}; the names are:', file=sys.stderr)
        for name in names:
            print('   ', name, file=sys.stderr)
        return 1
    if not args.no_open:
        for path in written:
            if sys.platform == 'win32':
                os.startfile(os.path.abspath(path))
            else:
                import webbrowser
                webbrowser.open('file://' + os.path.abspath(path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
