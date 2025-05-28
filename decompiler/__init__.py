import ast
import dis
import importlib
import platform
from queue import Queue
from types import CodeType

from loguru import logger
from networkx import DiGraph

from decompiler.models import InstructionNode
from decompiler.utils.bytecode import resolve_binop, resolve_unaryop, resolve_const
from decompiler.utils.graph import find_linear_path, merge_nodes, render_graph

logger.info(f'Python version: {platform.python_implementation()} {platform.python_version()}')



class Decompiler:
    def __init__(self, code: CodeType, toplevel=True):
        self.code = code
        self.entrypoint = None
        self.graph = self.build_graph()
        self.toplevel = toplevel
        M, m, pl = platform.python_version_tuple()
        self.transformer = importlib.import_module(f'decompiler.transformers.transformer_{M}_{m}').GraphTransformer(self, self.graph)

    def build_graph(self):
        graph = DiGraph()
        bytecodes = [InstructionNode.from_instruction(graph, i) for i in dis.Bytecode(self.code)]
        entrypoint = InstructionNode(graph, -3, l := [], l, -2)
        entrypoint.contexts = []
        bytecodes.insert(0, entrypoint)
        self.entrypoint = entrypoint
        offset_table = {i.offset: i for i in bytecodes}
        jumps = dis.hasjabs + dis.hasjrel

        bfs = Queue()
        bfs.put(entrypoint)
        visited = {None}
        while not bfs.empty():
            visited.add(curr := bfs.get())

            if curr.opname not in {'RETURN_VALUE', 'JUMP_ABSOLUTE', 'JUMP_FORWARD'}:  # TODO: should RERAISE be here?
                if _next := offset_table.get(curr.offset + 2):
                    if _next.opname == 'EXTENDED_ARG':
                        _next = offset_table.get(curr.offset + 4)
                    graph.add_edge(curr, _next, edge_type='implicit')
                    _next in visited or bfs.put(_next)

            if curr.opcode in jumps:
                if _target := offset_table.get(curr.argval):
                    if _target.opname == 'EXTENDED_ARG':
                        _target = offset_table.get(curr.argval + 2)
                    graph.add_edge(curr, _target, edge_type='explicit')
                    _target in visited or bfs.put(_target)

        return graph

    def decompile(self):
        has_changes = True
        i = 0
        while has_changes:
            i += 1
            logger.info(f'iteration {i}')
            has_changes = False

            for d in range(4):
                has_changes |= self.transformer.transform(d)
                # render_graph(self.graph, f'graph_{i}_{d}.svg')
                if has_changes:
                    break

            if has_changes:
                continue

            render_graph(self.graph, f'graph_{i}.svg')
            has_changes = self.transformer.construct_basic_blocks()
            if has_changes:
                continue

        if len(self.graph.nodes) != 1:
            logger.error('Decompilation failed')

    def get_source_code(self):
        if not self.toplevel:
            logger.warning('get_source_code called on a non-toplevel decompiler instance')

        return ast.unparse(self.entrypoint.argval)
                
