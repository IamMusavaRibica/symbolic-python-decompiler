"""Loading and running the round-trip test cases.

A test case is a piece of Python source.  It passes when compiling it,
decompiling the code object and compiling the decompiled source again gives
the same instruction graph (see ``decompiler.verify``).

Cases live in ``tests/cases/*.py``.  One file holds many cases, separated by
cell markers::

    # %% name of the case
    x = a if b else c

    # %% another one [await]
    async with a: pass

Options go in square brackets after the name:

``await``     compile with ``ast.PyCF_ALLOW_TOP_LEVEL_AWAIT``
``xfail``     known not to work yet; reported separately, not a failure
``stock`` / ``nopeephole``   only run with that compiler
"""
from __future__ import annotations

import ast
import glob
import os
import re
from typing import Iterator, List, NamedTuple, Sequence

from decompiler.utils.bytecode import nopeephole_python
from decompiler.verify import NOPEEPHOLE, STOCK, RoundTrip, verify_source

CASES_DIR = os.path.join(os.path.dirname(__file__), 'cases')
_MARKER = re.compile(r'^# %%\s*(.*?)\s*(?:\[([^\]]*)\])?\s*$')


class Case(NamedTuple):
    file: str  # file name without extension
    name: str
    source: str
    options: frozenset
    line: int

    @property
    def id(self) -> str:
        return f'{self.file}::{self.name}'

    @property
    def flags(self) -> int:
        return ast.PyCF_ALLOW_TOP_LEVEL_AWAIT if 'await' in self.options else 0

    def modes(self) -> List[str]:
        if STOCK in self.options:
            return [STOCK]
        if NOPEEPHOLE in self.options:
            return [NOPEEPHOLE]
        return [STOCK, NOPEEPHOLE]


def parse_cases(path: str) -> List[Case]:
    file = os.path.splitext(os.path.basename(path))[0]
    cases: List[Case] = []
    name, options, lines, start = None, frozenset(), [], 0
    with open(path, encoding='utf-8') as f:
        content = f.read().splitlines()

    def flush():
        if name is not None:
            cases.append(Case(file, name, '\n'.join(lines).strip('\n') + '\n', options, start))

    for number, line in enumerate(content, 1):
        m = _MARKER.match(line)
        if m:
            flush()
            name = m.group(1) or f'line {number}'
            options = frozenset((m.group(2) or '').split())
            lines, start = [], number
        elif name is not None:
            lines.append(line)
    flush()
    return cases


def load_cases(directory: str = CASES_DIR) -> List[Case]:
    cases: List[Case] = []
    for path in sorted(glob.glob(os.path.join(directory, '*.py'))):
        cases.extend(parse_cases(path))
    return cases


def available_modes() -> List[str]:
    return [STOCK, NOPEEPHOLE] if nopeephole_python() else [STOCK]


class Outcome(NamedTuple):
    case: Case
    mode: str
    result: RoundTrip

    @property
    def xfail(self) -> bool:
        return 'xfail' in self.case.options


def run_cases(cases: Sequence[Case], modes: Sequence[str]) -> Iterator[Outcome]:
    for case in cases:
        for mode in case.modes():
            if mode in modes:
                yield Outcome(case, mode, verify_source(case.source, mode, case.flags))
