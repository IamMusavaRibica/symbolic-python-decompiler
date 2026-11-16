"""Render an instruction graph as an image (needs ``pydotplus`` and the graphviz executables).

For a quick look, :func:`decompiler.graph.dump.dump_code` gives the same
information as text and needs neither.
"""
from __future__ import annotations

import html
import os
from typing import Callable, Optional

from decompiler.graph.builder import CodeGraph
from decompiler.graph.dump import _arg_text, _children, _opname
from decompiler.graph.node import InstructionNode, NEXT, TRUE, FALSE, JUMP

_EDGE_STYLE = {
    NEXT: dict(style='dashed', color='black'),
    TRUE: dict(style='solid', color='#008800', label='T'),
    FALSE: dict(style='solid', color='#cc0000', label='F'),
    JUMP: dict(style='solid', color='black', label='J'),
}


def render_graph(graph: CodeGraph, filename: str, directory: str = '_graphs',
                 annotate: Optional[Callable[[InstructionNode], Optional[str]]] = None) -> str:
    """Write ``directory/filename`` (format taken from the extension, svg by default).

    ``annotate`` may return an extra line of text for a node, e.g. the statement
    the decompiler attached to it.
    """
    import pydotplus

    dot = pydotplus.Dot(graph_type='digraph', rankdir='TB')
    children = _children(graph, '')
    dot_nodes = {}
    for node in graph.nodes:
        label = f'{node.index}  {_opname(node)}'
        arg = _arg_text(node, children)
        if arg:
            label += ' ' + html.escape(arg if len(arg) <= 60 else arg[:57] + '...')
        if not node.is_pseudo:
            label += f'  <font color="gray50">@{node.offset}</font>'
        extra = annotate(node) if annotate else None
        if extra:
            label += '<br/>' + html.escape(extra)
        attrs = dict(shape='box', fontname='Consolas')
        if node.is_pseudo:
            attrs.update(style='filled', color='lightblue')
        elif node.opname in ('RETURN_VALUE', 'RAISE_VARARGS', 'RERAISE'):
            attrs.update(style='filled', color='orange')
        elif node.back_jumps:
            attrs.update(color='#880088', penwidth='2')
        dot_nodes[node] = pydotplus.Node(f'n{node.index}', label=f'<{label}>', **attrs)
        dot.add_node(dot_nodes[node])
    for node in graph.nodes:
        for label, succ in node.successors():
            dot.add_edge(pydotplus.Edge(dot_nodes[node], dot_nodes[succ], fontname='Consolas', **_EDGE_STYLE[label]))

    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, filename)
    fmt = os.path.splitext(filename)[1].lstrip('.') or 'svg'
    dot.write(path, format=fmt)
    return path
