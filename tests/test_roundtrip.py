"""The round-trip cases as unittest tests:  python -m unittest tests.test_roundtrip

``python -m tests`` runs the same cases with nicer output for failures.
"""
from __future__ import annotations

import unittest

from tests.harness import available_modes, load_cases
from decompiler.verify import verify_source


class RoundTripTest(unittest.TestCase):
    def __init__(self, case, mode):
        super().__init__('run_case')
        self.case = case
        self.mode = mode

    def id(self):
        return f'{self.case.id} [{self.mode}]'

    __str__ = shortDescription = id

    def run_case(self):
        result = verify_source(self.case.source, self.mode, self.case.flags)
        if 'xfail' in self.case.options:
            if result.ok:
                self.fail('unexpectedly passes: remove the xfail option')
            self.skipTest('known failure')
        if not result.ok:
            decompiled = result.decompiled or ''
            self.fail(f'{result.stage} failed\n--- decompiled\n{decompiled}\n--- detail\n{result.detail}')


def load_tests(loader, standard_tests, pattern):
    suite = unittest.TestSuite()
    modes = available_modes()
    for case in load_cases():
        for mode in case.modes():
            if mode in modes:
                suite.addTest(RoundTripTest(case, mode))
    return suite
