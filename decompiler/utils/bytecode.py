import ast
import base64
import dis
import marshal
import subprocess
from typing import TypeVar, Type, Any

from decompiler import InstructionNode

T = TypeVar('T')


def is_instr(x, opname=None) -> bool:
    if isinstance(opname, str):
        opname = {opname}
    return isinstance(x, dis.Instruction) and (opname is None or x.opname in opname)


def resolve_op(i: dis.Instruction):
    if i.opname == 'COMPARE_OP':
        op = i.argval
    elif i.opname == 'IS_OP':
        op = 'is not' if i.argval else 'is'
    elif i.opname == 'CONTAINS_OP':
        op = 'not in' if i.argval else 'in'
    else:
        raise ValueError(f'not op: {i}')
    return {
        '<': ast.Lt,
        '<=': ast.LtE,
        '>': ast.Gt,
        '>=': ast.GtE,
        '==': ast.Eq,
        '!=': ast.NotEq,
        'is': ast.Is,
        'is not': ast.IsNot,
        'in': ast.In,
        'not in': ast.NotIn
    }[op]()


def resolve_binop(i: dis.Instruction):
    return {
        'ADD': ast.Add,
        'SUBTRACT': ast.Sub,
        'MULTIPLY': ast.Mult,
        'TRUE_DIVIDE': ast.Div,
        'FLOOR_DIVIDE': ast.FloorDiv,
        'MODULO': ast.Mod,
        'POWER': ast.Pow,
        'LSHIFT': ast.LShift,
        'RSHIFT': ast.RShift,
        'AND': ast.BitAnd,
        'XOR': ast.BitXor,
        'OR': ast.BitOr,
        'MATRIX_MULTIPLY': ast.MatMult
    }[i.opname.removeprefix('BINARY_').removeprefix('INPLACE_')]()

def resolve_unaryop(i: dis.Instruction):
    return {
        'UNARY_POSITIVE': ast.UAdd,
        'UNARY_NEGATIVE': ast.USub,
        'UNARY_INVERT': ast.Invert,
        'UNARY_NOT': ast.Not
    }[i.opname]()

def compile_without_peephole__(code: str):
    process = subprocess.Popen(
        [r"C:\Users\IamMusavaRibica\Documents\Python-3.9.22\PCbuild\amd64\python.exe",
         r"C:\Users\IamMusavaRibica\Documents\python_compile.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    process.stdin.write(base64.b64encode(code.encode()).decode() + '\n')
    output, errors = process.communicate()
    if process.returncode == 0:
        return marshal.loads(base64.b64decode(output.encode()))
    raise ValueError(errors)

def resolve_const(value):
    if isinstance(value, (str, bytes, float, int, type(None), type(...))):
        return ast.Constant(value)
    if isinstance(value, (tuple, frozenset)):
        elts = []
        for e in value:
            r = resolve_const(e)
            if r is None:
                return
            elts.append(r)
        if isinstance(value, tuple):
            node = ast.Tuple(elts=elts)
            node.CONST_TUPLE = 1
        elif isinstance(value, frozenset):
            node = ast.Set(elts=elts)
            node.CONST_FROZENSET = 1
        return node

def get_context(node: InstructionNode, ctx_type: Type[T]) -> T:
    if node.opcode != -3:
        raise ValueError(f'get_context called with non-basic block: {node}')
    for c in reversed(node.contexts):
        if isinstance(c, ctx_type):
            return c

def is_closed(node: InstructionNode) -> bool:
    if node.opcode != -3:
        raise ValueError(f'is_closed called with non-basic block: {node}')
    return getattr(node, 'CLOSED', 0) == 1

def next_n_linear(node: InstructionNode, n: int, include_first: bool = False):
    coll: list[InstructionNode] = []
    if include_first:
        coll.append(node)
    curr = node
    while len(coll) < n:
        nnext = curr.next
        if nnext is None or nnext.graph.in_degree(nnext) > 1:
            return
        coll.append(nnext)
        curr = nnext
    return coll

def is_load_none(node: InstructionNode) -> bool:
    return node.opcode == -4 and isinstance(node.argval, ast.Constant) and node.argval.value is None

def check_node(node: InstructionNode, opname: str, argval: Any):
    return node.opname == opname and (node.argval == argval or check_equal_ast_node(node.argval, argval))

def check_equal_ast_node(node1, node2):
    if not isinstance(node1, ast.AST) or not isinstance(node2, ast.AST):
        return False
    if type(node1) != type(node2):
        return False
    if isinstance(node1, ast.Name):
        return node1.id == node2.id and type(node1.ctx) == type(node2.ctx)
    raise NotImplementedError('comparing AST nodes of type ' + type(node1).__name__)


def assert_except_handler_cleanup(jinem: InstructionNode, load_none: InstructionNode, store: InstructionNode, delete: InstructionNode, reraise: InstructionNode = None):
    if jinem.opcode != -13:
        raise ValueError(f'first argument is not *EXC_MATCH: {jinem}')
    assert load_none.opcode == -4 and isinstance(load_none.argval, ast.Constant) and load_none.argval.value is None
    assert store.opcode == -5 and isinstance(store.argval, ast.Name) and store.argval.id == jinem.err_variable
    assert delete.opname == 'DELETE_NAME' and delete.argval == jinem.err_variable
    if reraise is not None:
        assert reraise.opname == 'RERAISE'
