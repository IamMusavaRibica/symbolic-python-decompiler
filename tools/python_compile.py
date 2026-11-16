"""Compile source code with whatever interpreter runs this script and hand the code object back.

It is meant to be run by an interpreter *other* than the one running the
decompiler, in practice a CPython 3.9 build whose peephole optimizer is
disabled (see ``decompiler.utils.bytecode.compile_without_peephole``).  It
must therefore stay free of third party imports.

Protocol: every line on stdin is ``<base64 of the utf-8 source>`` optionally
followed by a space and the integer ``compile()`` flags.  For every line one
line is written to stdout: the base64 of the marshalled code object, or
``!`` followed by the base64 of the error message when compilation failed.
Feeding a single line and closing stdin gives the original one-shot behaviour.
"""
import base64
import marshal
import sys


def main():
    stdout = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        source, _, flags = line.partition(' ')
        try:
            code = compile(base64.b64decode(source).decode('utf-8'), '<source>', 'exec',
                           flags=int(flags or 0), dont_inherit=True)
            stdout.write(base64.b64encode(marshal.dumps(code)).decode('ascii') + '\n')
        except Exception as e:  # SyntaxError, ValueError, ...
            message = '%s: %s' % (type(e).__name__, e)
            stdout.write('!' + base64.b64encode(message.encode('utf-8')).decode('ascii') + '\n')
        stdout.flush()


if __name__ == '__main__':
    main()
