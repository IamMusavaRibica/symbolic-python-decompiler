"""Run the round-trip cases:  python -m tests [-k TEXT] [-m stock|nopeephole|both] [-v] [-x]

  -k TEXT   only cases whose id (file::name) contains TEXT (may be repeated)
  -m MODE   which compiler(s) to use (default: every available one)
  -v        show the decompiled source and the graph diff of failures
  -x        stop at the first failure
  -l        only list the failing case ids
"""
from __future__ import annotations

import argparse
import sys
import time

from tests.harness import available_modes, load_cases, run_cases


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='python -m tests', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-k', action='append', default=[], metavar='TEXT')
    parser.add_argument('-m', choices=['stock', 'nopeephole', 'both'], default=None)
    parser.add_argument('-v', action='store_true')
    parser.add_argument('-x', action='store_true')
    parser.add_argument('-l', action='store_true')
    parser.add_argument('-d', '--dead-code', action='store_true', help='run with decompiler.options.dead_code')
    args = parser.parse_args(argv)
    if args.dead_code:
        from decompiler import options
        options.dead_code = True

    modes = available_modes()
    if args.m and args.m != 'both':
        if args.m not in modes:
            print(f'compiler {args.m!r} is not available', file=sys.stderr)
            return 2
        modes = [args.m]
    cases = [c for c in load_cases() if all(k in c.id for k in args.k)]

    started = time.time()
    passed = failed = xfailed = xpassed = 0
    failures = []
    for outcome in run_cases(cases, modes):
        if outcome.result.ok:
            if outcome.xfail:
                xpassed += 1
                print(f'XPASS {outcome.case.id} [{outcome.mode}]  (remove the xfail marker)')
            else:
                passed += 1
            continue
        if outcome.xfail:
            xfailed += 1
            continue
        failed += 1
        failures.append(outcome)
        if args.l:
            print(f'{outcome.case.id} [{outcome.mode}] {outcome.result.stage}')
        else:
            print(f'FAIL  {outcome.case.id} [{outcome.mode}]  stage: {outcome.result.stage}')
            if args.v:
                print('--- source ' + '-' * 60)
                print(outcome.case.source.rstrip())
                if outcome.result.decompiled is not None:
                    print('--- decompiled ' + '-' * 56)
                    print(outcome.result.decompiled.rstrip())
                print('--- detail ' + '-' * 60)
                print(outcome.result.detail.rstrip())
                print('=' * 71)
            else:
                detail = outcome.result.detail.strip().splitlines()
                if detail:
                    print('      ' + detail[-1][:200])
        if args.x:
            break
    summary = f'{passed} passed, {failed} failed'
    if xfailed:
        summary += f', {xfailed} expected failures'
    if xpassed:
        summary += f', {xpassed} unexpectedly passing'
    print(f'{summary}  ({len(cases)} cases, compilers: {", ".join(modes)}, {time.time() - started:.1f}s)')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
