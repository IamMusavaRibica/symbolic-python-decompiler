import ast
import dis
from typing import Optional, Literal

from loguru import logger
from networkx import DiGraph

_opname2 = {

    -3: '*BASIC_BLOCK',
    -4: '*LOAD_AST_EXPR',
    -5: '*STORE_VARIABLE',
    -6: '*FOR_LOOP_START',

    -9: '*PSEUDOTRY',  # https://github.com/python/cpython/blob/3.9/Python/compile.c#L3121

    -11: '*IF_STMT',
    -12: '*SETUP_FINALLY',
    -13: '*EXC_MATCH',
    -14: '*FORMATTED_VALUE',
}

class InstructionNode:
    def __init__(self, graph: DiGraph, opcode, arg, argval, offset):
        self.graph = graph
        self.opcode: int = opcode
        self.arg = arg  # unnecessary, most likely never used after building the graph
        self.argval = argval
        self.offset = offset  # offset becomes irrelevant after the graph is modified

    @classmethod
    def from_instruction(cls, graph: DiGraph, instr: dis.Instruction):
        assert isinstance(graph, DiGraph) and isinstance(instr, dis.Instruction)
        return cls(graph, instr.opcode, instr.arg, instr.argval, instr.offset)

    @property
    def opname(self):
        if 0 <= self.opcode < len(dis.opname):
            return dis.opname[self.opcode]
        return _opname2.get(self.opcode, f'UNKNOWN OPCODE {self.opcode}')

    def _get_successor(self, edge_type: str):
        successors = list(self.graph.successors(self))
        if edge_type == 'implicit' and len([s for s in successors if self.graph.get_edge_data(self, s)['edge_type'] == 'implicit']) > 1:
            raise RuntimeError(f'Node {self} has more than one implicit successor!')
        for i in self.graph.successors(self):
            if self.graph.get_edge_data(self, i)['edge_type'] == edge_type:
                return i

    @property
    def next(self):
        # if self.opname == 'RERAISE':
        #     logger.warning(f'Accessing .next of a RERAISE!')
        return self._get_successor('implicit')

    @property
    def target(self):
        return self._get_successor('explicit')

    def nth_implicit_prev(self, n: int):
        for i in self.graph.predecessors(self):
            if self.graph.get_edge_data(i, self)['edge_type'] == 'implicit':
                return i if n == 1 else i.nth_implicit_prev(n - 1)

    @property
    def single_prev(self):
        preds = list(self.graph.predecessors(self))
        if len(preds) == 1:
            return preds[0]

    def __repr__(self):
        return f'InstructionNode(offset={self.offset}, opname={self.opname}, argval={self.argval})'


class ForLoopContext:
    def __init__(self, node, assignment, iterable, is_async: bool = False):
        assert node.opcode == -6
        self.start_node: InstructionNode = node
        self.assignment: ast.expr = assignment
        self.iterable: ast.expr = iterable
        self.is_async: bool = is_async
        self.for_bb: Optional[InstructionNode] = None
        self.else_bb: Optional[InstructionNode] = None
        self.after_for: Optional[InstructionNode] = None

    def is_for_body(self, ins):
        return ins == self.for_bb

    def is_else_body(self, ins):
        return ins == self.else_bb

    @property
    def ast_for(self) -> Literal[ast.For, ast.AsyncFor]:
        return ast.For if not self.is_async else ast.AsyncFor

    def __str__(self):
        return 'Async'*self.is_async + f'ForLoopContext(offset={self.start_node.offset})'

    __repr__ = __str__

class TryContext:
    def __init__(self, node):
        assert node.opname == 'SETUP_FINALLY'
        self.start_node: InstructionNode = node
        self.try_bb: Optional[InstructionNode] = None
        self.except_bb: Optional[InstructionNode] = None
        self.finally_bb: Optional[InstructionNode] = None
        self.handlers = []

    def __str__(self):
        return f'TryContext(offset={self.start_node.offset})'

    __repr__ = __str__