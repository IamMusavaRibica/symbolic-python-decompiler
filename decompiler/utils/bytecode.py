"""Turning source code into code objects.

Two compilers are supported:

* the interpreter running the decompiler (``compile_source``), which is what
  real ``.pyc`` files look like, and
* a CPython build whose peephole optimizer is disabled
  (``compile_without_peephole``): no jump threading, dead code is kept.  Its
  bytecode is closer to what ``compile.c`` literally emits, which makes it a
  good second opinion for the decompiler.

The second interpreter is located through the ``DECOMPILER_NOPEEPHOLE_PYTHON``
environment variable and falls back to ``DEFAULT_NOPEEPHOLE_PYTHON``.
"""
from __future__ import annotations

import ast
import atexit
import base64
import marshal
import os
import subprocess
from types import CodeType
from typing import Optional

DEFAULT_NOPEEPHOLE_PYTHON = r'C:\Users\IamMusavaRibica\Documents\Python-3.9.22\PCbuild\amd64\python.exe'
_HELPER = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', 'tools', 'python_compile.py'))

CO_COROUTINE = 0x80


def compile_source(source: str, filename: str = '<source>', flags: int = 0) -> CodeType:
    """Compile with the running interpreter (peephole optimizer enabled)."""
    return compile(source, filename, 'exec', flags=flags, dont_inherit=True)


def nopeephole_python() -> Optional[str]:
    """Path of the interpreter without peephole optimizer, None when it is not available."""
    path = os.environ.get('DECOMPILER_NOPEEPHOLE_PYTHON', DEFAULT_NOPEEPHOLE_PYTHON)
    return path if path and os.path.isfile(path) and os.path.isfile(_HELPER) else None


class _CompileServer:
    """A long-lived helper process, so compiling many snippets does not start many interpreters."""

    def __init__(self, python: str):
        self.process = subprocess.Popen([python, '-I', _HELPER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, encoding='ascii')

    def compile(self, source: str, flags: int) -> CodeType:
        line = base64.b64encode(source.encode('utf-8')).decode('ascii')
        self.process.stdin.write(f'{line} {flags}\n')
        self.process.stdin.flush()
        reply = self.process.stdout.readline().strip()
        if not reply:
            raise RuntimeError('the compile helper process died')
        if reply.startswith('!'):
            raise SyntaxError(base64.b64decode(reply[1:]).decode('utf-8'))
        return marshal.loads(base64.b64decode(reply))

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=5)
        except Exception:
            self.process.kill()


_server: Optional[_CompileServer] = None


def compile_without_peephole(source: str, flags: int = 0) -> CodeType:
    """Compile with the peephole-less interpreter (raises RuntimeError when there is none)."""
    global _server
    if _server is None or _server.process.poll() is not None:
        python = nopeephole_python()
        if python is None:
            raise RuntimeError('no interpreter without peephole optimizer available '
                               '(set DECOMPILER_NOPEEPHOLE_PYTHON)')
        _server = _CompileServer(python)
        atexit.register(_server.close)
    return _server.compile(source, flags)


# the name this function had before the helper script was lost
compile_without_peephole__ = compile_without_peephole


def compile_flags_for(code: CodeType) -> int:
    """compile() flags needed to compile source decompiled from ``code`` again."""
    flags = 0
    if code.co_flags & CO_COROUTINE and code.co_name == '<module>':
        flags |= ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
    return flags
