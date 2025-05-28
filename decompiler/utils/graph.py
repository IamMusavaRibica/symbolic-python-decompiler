import ast
import dis
import html
import os.path
from typing import Optional

import networkx
import pydotplus
from loguru import logger
from networkx import DiGraph

from decompiler.models import InstructionNode, ForLoopContext


def render_node(ins: InstructionNode, is_entry, **kwargs):
    label = f'{ins.offset}  {ins.opname}'.ljust(27)
    if getattr(ins, 'DO_NOT_CONSUME', 0):
        label = f'{ins.offset} {ins.opname}  [marker]'.ljust(27)
        kwargs['color'] = '#880000'
    if ins.opcode >= dis.HAVE_ARGUMENT:
        if ins.opname == 'UNPACK_EX':
            before = ins.argval & 255
            after = (ins.argval >> 8) & 255
            label += f' {before} {after}'
        else:
            label += html.escape(f' {ins.argval}')
    if ins.opname == '*FOR_LOOP_START':
        label += '<br/> for {} in {}'.format(
            html.escape(ast.unparse(ins.context.assignment)),
            html.escape(ast.unparse(ins.context.iterable)),
        )
    if ins.opname == '*EXC_MATCH':
        label += '<br/> except ' + html.escape(ast.unparse(ins.argval))
        if ins.err_variable is not None:
            label += f' as {ins.err_variable}:'
        else:
            label += ':'


    if ins.opname == '*LOAD_AST_EXPR':
        try:
            r = ast.unparse(ins.argval)
        except:
            logger.error(f'Failed to unparse {ins.argval!r}:\n{ast.dump(ins.argval, indent=2)}')
            r = str(ins.argval)
        r = html.escape(r)
        if len(r) > 30:
            label += '<br/>'
        label += r
    if ins.opname == '*STORE_VARIABLE':
        label += html.escape(ast.unparse(ins.argval))
    if ins.opname == '*BASIC_BLOCK':
        # label = label[label.find('  ') + 2:]  # basic blocks don't need offset
        label = '*BASIC_BLOCK '
        # logger.debug(f'ins: {ins}, contexts: {ins.contexts}')
        for c in reversed(ins.contexts):
            if isinstance(c, ForLoopContext):
                label += f' [for-{c.start_node.offset}]'
        if getattr(ins, 'CLOSED', 0):
            label += ' [closed]'
        label += '<br/> '
        if ins.argval:
            for k in ins.argval:
                ast.fix_missing_locations(k)
            rows = ast.unparse(ins.argval).split('\n')
            maxlen = max(len(r) for r in rows)
            rows = [html.escape(r.ljust(maxlen)) for r in rows]
            label += '<br/>' + '<br/>'.join(rows)
        else:
            label += '<br/> &lt;empty&gt;'
        kwargs['color'] = '#22ff22' if not getattr(ins, 'CLOSED', 0) else '#009900'
    if ins.opname == 'SETUP_FINALLY' and hasattr(ins, 'context'):
        label += '<br/> {} handlers'.format(len(ins.context.handlers))
    if ins.opname == '*IF_STMT':
        kwargs['color'] = 'orange'
        try:
            label += html.escape(ast.unparse(ins.argval))
        except:
            label += html.escape(str(ins.argval))
    if ins.opname in {'JUMP_ABSOLUTE', 'JUMP_FORWARD'}:
        kwargs['color'] = 'gray'
    if is_entry or ins.opname == 'RETURN_VALUE':
        kwargs['style'] = 'filled'
        kwargs['color'] = 'lightblue' if is_entry else 'orange'
    label = f'<{label}>'  # this hangs the program: f'<<{label}>>'
    return pydotplus.Node(hex(id(ins)), label=label, **kwargs)


def render_graph(nx_graph: DiGraph, filename: str):
    dot = pydotplus.Dot(graph_type='digraph', rankdir='TB')

    nodedict = {}
    for node in nx_graph.nodes:
        is_entry = nx_graph.in_degree(node) == 0 and node.opcode == -3
        pd = render_node(node, is_entry, shape='box', fontname='Consolas')
        if is_entry:
            sub = pydotplus.Subgraph('sub', rank='source')
            sub.add_node(pd)
            dot.add_subgraph(sub)
        else:
            dot.add_node(pd)
        nodedict[node] = pd

    for node in nx_graph.nodes:
        if node.opcode == -6:  # connect implicit AFTER_FOR
            pd = nodedict[node]
            after_for = node.context.after_for
            if after_for is not None:
                dot.add_edge(pydotplus.Edge(pd, nodedict[after_for], style='dotted', color='gray'))
        # elif node.opname == 'SETUP_FINALLY' and hasattr(node, 'context'):
        #     pd = nodedict[node]
        #     finally_block = node.context.finally_bb
        #     if finally_block is not None:
        #         dot.add_edge(pydotplus.Edge(pd, nodedict[finally_block], style='dotted', color='gray'))

    for edge in nx_graph.edges(data=True):
        src = nodedict[edge[0]]
        dst = nodedict[edge[1]]
        e_style = {
            'implicit': 'dashed',
            'explicit': 'solid',
            'transient': 'dotted',
        }[edge[2]['edge_type']]
        color = {
            'implicit': 'black',
            'explicit': 'black',
            'transient': 'gray',
        }[edge[2]['edge_type']]
        dot.add_edge(pydotplus.Edge(src, dst, style=e_style, color=color))

    not os.path.isdir('_graphs') and os.makedirs('_graphs')
    dot.write('_graphs/' + filename, format='svg')


def get_implicit_predecessors(graph: DiGraph, node: InstructionNode):
    matches = []
    for edge in graph.in_edges(node, data=True):
        if edge[2]['edge_type'] == 'implicit':
            matches.append(edge[0])
    return matches


def exists_implicit_path(graph: DiGraph, start: InstructionNode, end: InstructionNode):
    trace = [start]
    while trace[-1] is not None and trace[-1] != end:
        trace.append(trace[-1].next)
    return trace if trace[-1] == end else []


def get_single_predecessor(node: Optional[InstructionNode]) -> Optional[InstructionNode]:
    if node is None:
        return None
    preds = list(node.graph.predecessors(node))
    # print('preds:', preds)
    return preds[0] if len(preds) == 1 else None


def assert_only_predecessors(node: InstructionNode, *candidates: InstructionNode):
    candidates = list(candidates)
    predecessors = list(node.graph.predecessors(node))
    for p in predecessors:
        if p not in candidates:
            raise GraphAssertionException(f'{p} is not in provided candidates')
    for c in candidates:
        if c not in predecessors:
            raise GraphAssertionException(f'{c} is not a predecessor of {node}')


class GraphAssertionException(Exception):
    pass


def find_linear_path(graph: DiGraph, start: InstructionNode, end: InstructionNode):
    assert start != end, 'start and end cannot be the same'
    assert start is not None, 'start cannot be None'
    assert end is not None, 'end cannot be None'
    if not networkx.has_path(graph, start, end):
        return []
    path = networkx.shortest_path(graph, start, end)
    if type(path) == dict:
        raise RuntimeError('Shortest path returned a dict instead of a list')
    for node in path[1:-1]:
        if graph.in_degree(node) != 1 or graph.out_degree(node) != 1:
            return []
    return path

def merge_nodes(graph: DiGraph, node1: InstructionNode, node2: InstructionNode):
    # connect node1 to node2's successors
    assert node1 in graph and node2 in graph
    for succ in list(graph.successors(node2)):
        edge_attrs = graph.get_edge_data(node2, succ)
        graph.add_edge(node1, succ, **edge_attrs)

def insert_between(graph: DiGraph, node1, new_node, node2, edge_type='implicit'):
    assert node1 in graph and node2 in graph
    assert graph.has_edge(node1, node2)
    graph.add_edge(node1, new_node, edge_type=graph[node1][node2]['edge_type'])
    graph.add_edge(new_node, node2, edge_type=edge_type)
    graph.remove_edge(node1, node2)