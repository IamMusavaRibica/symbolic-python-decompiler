"""A port of CPython 3.9's peephole optimizer (``Python/peephole.c``), used to compare code modulo it.

The stock compiler runs ``PyCode_Optimize`` on every code object, except on those whose
line number table has an address delta of 255 bytes (a source line that produced more
than 255 bytes of bytecode): those it leaves alone, so the same source yields different
bytecode depending on how its lines are laid out.  The comparison must not care, so both
code objects are passed through this port, which never bails out for that reason, before
their graphs are compared.  On code the optimizer already saw the pass changes nothing.

It is a literal translation, including the places where the C code gives up
(``exitUnchanged``); only the line number table fix-up is left out, since nothing that
compares graphs looks at line numbers.
"""
from __future__ import annotations

from dis import opmap
from types import CodeType
from typing import List

NOP = opmap['NOP']
EXTENDED_ARG = opmap['EXTENDED_ARG']
LOAD_CONST = opmap['LOAD_CONST']
BUILD_TUPLE = opmap['BUILD_TUPLE']
UNPACK_SEQUENCE = opmap['UNPACK_SEQUENCE']
ROT_TWO = opmap['ROT_TWO']
ROT_THREE = opmap['ROT_THREE']
RETURN_VALUE = opmap['RETURN_VALUE']
JUMP_FORWARD = opmap['JUMP_FORWARD']
JUMP_ABSOLUTE = opmap['JUMP_ABSOLUTE']
POP_JUMP_IF_FALSE = opmap['POP_JUMP_IF_FALSE']
POP_JUMP_IF_TRUE = opmap['POP_JUMP_IF_TRUE']
JUMP_IF_FALSE_OR_POP = opmap['JUMP_IF_FALSE_OR_POP']
JUMP_IF_TRUE_OR_POP = opmap['JUMP_IF_TRUE_OR_POP']
JUMP_IF_NOT_EXC_MATCH = opmap['JUMP_IF_NOT_EXC_MATCH']
FOR_ITER = opmap['FOR_ITER']
SETUP_FINALLY = opmap['SETUP_FINALLY']
SETUP_WITH = opmap['SETUP_WITH']
SETUP_ASYNC_WITH = opmap['SETUP_ASYNC_WITH']
RERAISE = opmap['RERAISE']
UNARY_NOT = opmap['UNARY_NOT']

UNCONDITIONAL = {JUMP_ABSOLUTE, JUMP_FORWARD}
CONDITIONAL = {POP_JUMP_IF_FALSE, POP_JUMP_IF_TRUE, JUMP_IF_FALSE_OR_POP, JUMP_IF_TRUE_OR_POP}
ABSOLUTE = CONDITIONAL | {JUMP_ABSOLUTE, JUMP_IF_NOT_EXC_MATCH}
JUMPS_ON_TRUE = {POP_JUMP_IF_TRUE, JUMP_IF_TRUE_OR_POP}
RELATIVE = {FOR_ITER, JUMP_FORWARD, SETUP_FINALLY, SETUP_WITH, SETUP_ASYNC_WITH}
ALL_JUMPS = ABSOLUTE | RELATIVE


def instrsize(oparg: int) -> int:
    return 1 + (oparg > 0xff) + (oparg > 0xffff) + (oparg > 0xffffff)


class _Code:
    """The code units as two parallel lists, ``op[i]`` and ``arg[i]``."""

    def __init__(self, raw: bytes):
        self.op: List[int] = list(raw[0::2])
        self.arg: List[int] = list(raw[1::2])
        self.len = len(self.op)

    def find_op(self, i: int) -> int:
        while i < self.len and self.op[i] == EXTENDED_ARG:
            i += 1
        return i

    def get_arg(self, i: int) -> int:
        """The full oparg of the effective opcode at ``i`` (EXTENDED_ARG prefixes included)."""
        oparg = self.arg[i]
        if i >= 1 and self.op[i - 1] == EXTENDED_ARG:
            oparg |= self.arg[i - 1] << 8
            if i >= 2 and self.op[i - 2] == EXTENDED_ARG:
                oparg |= self.arg[i - 2] << 16
                if i >= 3 and self.op[i - 3] == EXTENDED_ARG:
                    oparg |= self.arg[i - 3] << 24
        return oparg

    def jump_target(self, i: int) -> int:
        return self.get_arg(i) // 2 + (0 if self.op[i] in ABSOLUTE else i + 1)

    def fill_nops(self, start: int, end: int) -> None:
        for i in range(start, end):
            self.op[i], self.arg[i] = NOP, 0

    def write_op_arg(self, pos: int, opcode: int, oparg: int, ilen: int) -> None:
        for shift in range(ilen - 1, -1, -1):
            self.op[pos], self.arg[pos] = (EXTENDED_ARG if shift else opcode), (oparg >> (8 * shift)) & 0xff
            pos += 1

    def set_arg(self, i: int, oparg: int) -> int:
        """Replace the oparg at ``i`` in place; -1 when it does not fit, else the new op index."""
        curarg = self.get_arg(i)
        if curarg == oparg:
            return i
        curilen, newilen = instrsize(curarg), instrsize(oparg)
        if curilen < newilen:
            return -1
        self.write_op_arg(i + 1 - curilen, self.op[i], oparg, newilen)
        self.fill_nops(i + 1 - curilen + newilen, i + 1)
        return i - curilen + newilen

    def copy_op_arg(self, i: int, opcode: int, oparg: int, maxi: int) -> int:
        """Write op/arg at the end of ``[i, maxi)``, NOPs before it; -1 when it does not fit."""
        ilen = instrsize(oparg)
        if i + ilen > maxi:
            return -1
        self.write_op_arg(maxi - ilen, opcode, oparg, ilen)
        self.fill_nops(i, maxi - ilen)
        return maxi - 1

    def lastn_const_start(self, i: int, n: int) -> int:
        """Index of the n-th last LOAD_CONST before ``i`` (its EXTENDED_ARG prefix included)."""
        while True:
            i -= 1
            if self.op[i] == LOAD_CONST:
                n -= 1
                if not n:
                    while i > 0 and self.op[i - 1] == EXTENDED_ARG:
                        i -= 1
                    return i

    def markblocks(self) -> List[int]:
        blocks = [0] * self.len
        for i in range(self.len):
            if self.op[i] in ALL_JUMPS:
                blocks[self.jump_target(i)] = 1
        count = 0
        for i in range(self.len):
            count += blocks[i]
            blocks[i] = count
        return blocks


def optimize_bytecode(raw: bytes, consts: list):
    """``PyCode_Optimize`` without the line table bypass; ``consts`` grows in place.

    Returns the new bytecode, or None where the C code returns the input unchanged.
    """
    c = _Code(raw)
    codelen = c.len
    blocks = c.markblocks()

    def isbasicblock(start: int, end: int) -> bool:
        return blocks[start] == blocks[end]

    cumlc = lastlc = 0
    i = c.find_op(0)
    while i < codelen:
        opcode = c.op[i]
        op_start = i
        while op_start >= 1 and c.op[op_start - 1] == EXTENDED_ARG:
            op_start -= 1
        nexti = i + 1
        while nexti < codelen and c.op[nexti] == EXTENDED_ARG:
            nexti += 1
        nextop = c.op[nexti] if nexti < codelen else 0
        lastlc = cumlc
        cumlc = 0

        if opcode == LOAD_CONST:
            # skip over LOAD_CONST trueconst POP_JUMP_IF_FALSE xx ('while 1')
            cumlc = lastlc + 1
            if nextop == POP_JUMP_IF_FALSE and isbasicblock(op_start, i + 1):
                if bool(consts[c.get_arg(i)]):
                    c.fill_nops(op_start, nexti + 1)
                    cumlc = 0

        elif opcode == BUILD_TUPLE:
            j = c.get_arg(i)
            if j > 0 and lastlc >= j:
                h = c.lastn_const_start(op_start, j)
                if isbasicblock(h, op_start):
                    _fold_tuple_on_constants(c, h, i + 1, consts, j)
                    i = nexti
                    continue
            if nextop == UNPACK_SEQUENCE and isbasicblock(op_start, i + 1) and j == c.get_arg(nexti):
                if j < 2:
                    c.fill_nops(op_start, nexti + 1)
                elif j == 2:
                    c.op[op_start], c.arg[op_start] = ROT_TWO, 0
                    c.fill_nops(op_start + 1, nexti + 1)
                elif j == 3:
                    c.op[op_start], c.arg[op_start] = ROT_THREE, 0
                    c.op[op_start + 1], c.arg[op_start + 1] = ROT_TWO, 0
                    c.fill_nops(op_start + 2, nexti + 1)

        elif opcode in (JUMP_IF_FALSE_OR_POP, JUMP_IF_TRUE_OR_POP, POP_JUMP_IF_FALSE, POP_JUMP_IF_TRUE,
                        JUMP_FORWARD, JUMP_ABSOLUTE):
            done = False
            if opcode in (JUMP_IF_FALSE_OR_POP, JUMP_IF_TRUE_OR_POP):
                # conditional jump to a conditional jump whose outcome the first one decides
                tgt = c.find_op(c.get_arg(i) // 2)
                j = c.op[tgt]
                if j in CONDITIONAL:
                    if (j in JUMPS_ON_TRUE) == (opcode in JUMPS_ON_TRUE):
                        h = c.set_arg(i, c.get_arg(tgt))  # the second jump is taken iff the first is
                    else:
                        h = c.set_arg(i, (tgt + 1) * 2)  # jump past the second one, popping
                        j = POP_JUMP_IF_TRUE if opcode == JUMP_IF_TRUE_OR_POP else POP_JUMP_IF_FALSE
                    if h >= 0:
                        nexti = h
                        c.op[nexti] = j
                        done = True
            if not done:
                # replace jumps to unconditional jumps, and jumps to a return by the return
                tgt = c.find_op(c.jump_target(i))
                if opcode in UNCONDITIONAL and c.op[tgt] == RETURN_VALUE:
                    c.op[op_start], c.arg[op_start] = RETURN_VALUE, 0
                    c.fill_nops(op_start + 1, i + 1)
                elif c.op[tgt] in UNCONDITIONAL:
                    arg = c.jump_target(tgt)
                    ok = True
                    if opcode == JUMP_FORWARD:
                        opcode = JUMP_ABSOLUTE  # JMP_ABS can go backwards
                    elif opcode not in ABSOLUTE:
                        if arg < i + 1:
                            ok = False  # no backward relative jumps
                        else:
                            arg -= i + 1
                    if ok:
                        c.copy_op_arg(op_start, opcode, arg * 2, i + 1)

        elif opcode == RETURN_VALUE:
            # remove unreachable ops after RETURN
            h = i + 1
            while h < codelen and isbasicblock(i, h):
                if c.op[h] in (SETUP_FINALLY, RERAISE):
                    while h > i + 1 and c.op[h - 1] == EXTENDED_ARG:
                        h -= 1
                    break
                h += 1
            if h > i + 1:
                c.fill_nops(i + 1, h)
                nexti = c.find_op(h)

        i = nexti

    # remove the NOPs and fix up the jump targets
    nops = 0
    for i in range(codelen):
        blocks[i] = i - nops  # original code offset => new code offset
        if c.op[i] == NOP:
            nops += 1
    out = bytearray()
    i = 0
    while i < codelen:
        op_start = i
        j = c.arg[i]
        while c.op[i] == EXTENDED_ARG:
            i += 1
            j = j << 8 | c.arg[i]
        opcode = c.op[i]
        if opcode == NOP:
            i += 1
            continue
        if opcode in ABSOLUTE:
            j = blocks[j // 2] * 2
        elif opcode in RELATIVE:
            j = (blocks[j // 2 + i + 1] - blocks[i] - 1) * 2
        ilen = i - op_start + 1
        if instrsize(j) > ilen or ilen > 4:
            return None
        for shift in range(ilen - 1, -1, -1):  # an EXTENDED_ARG 0 where the arg shrank
            out += bytes(((EXTENDED_ARG if shift else opcode), (j >> (8 * shift)) & 0xff))
        i += 1
    return bytes(out)


def _fold_tuple_on_constants(c: _Code, c_start: int, opcode_end: int, consts: list, n: int) -> None:
    """LOAD_CONST c1 ... LOAD_CONST cn, BUILD_TUPLE n  -->  LOAD_CONST (c1, ..., cn)."""
    items = []
    pos = c_start
    for _ in range(n):
        pos = c.find_op(pos)
        items.append(consts[c.get_arg(pos)])
        pos += 1
    consts.append(tuple(items))
    c.copy_op_arg(c_start, LOAD_CONST, len(consts) - 1, opcode_end)


def optimize_code(code: CodeType) -> CodeType:
    """``code`` as the peephole optimizer would have left it, nested code objects included."""
    consts = [optimize_code(const) if isinstance(const, CodeType) else const for const in code.co_consts]
    raw = optimize_bytecode(code.co_code, consts)
    if raw is None:
        return code.replace(co_consts=tuple(consts))
    return code.replace(co_code=raw, co_consts=tuple(consts))
