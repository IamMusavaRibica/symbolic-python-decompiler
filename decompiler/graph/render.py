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
                 annotate: Optional[Callable[[InstructionNode], Optional[str]]] = None,
                 collapse: bool = False) -> str:
    """Write ``directory/filename`` (format taken from the extension, svg by default).

    ``annotate`` may return an extra line of text for a node, e.g. the statement
    the decompiler attached to it.  With ``collapse`` a run of straight-line
    instructions is drawn as one box, so that only the control flow stands out.
    """
    import pydotplus

    dot = pydotplus.Dot(graph_type='digraph', rankdir='TB')
    children = _children(graph, '')

    def line(node):
        text = f'{node.index}  {_opname(node)}'
        arg = _arg_text(node, children)
        if arg:
            text += ' ' + html.escape(arg if len(arg) <= 60 else arg[:57] + '...')
        if not node.is_pseudo:
            text += f'  <font color="gray50">@{node.offset}</font>'
        extra = annotate(node) if annotate else None
        if extra:
            text += '<br/>' + html.escape(extra)
        return text

    # a run: the nodes drawn in one box, in order
    runs = []
    run_of = {}
    for node in graph.nodes:
        pred = node.preds[0][0] if len(node.preds) == 1 and node.preds[0][1] == NEXT else None
        if collapse and pred is not None and pred in run_of and len(pred.out) == 1 \
                and not pred.is_pseudo and not node.is_pseudo and not node.back_jumps:
            run_of[node] = run_of[pred]
            run_of[node].append(node)
        else:
            run_of[node] = [node]
            runs.append(run_of[node])

    dot_nodes = {}
    for run in runs:
        first, last = run[0], run[-1]
        label = '<br align="left"/>'.join(line(n) for n in run) + ('<br align="left"/>' if len(run) > 1 else '')
        attrs = dict(shape='box', fontname='Consolas')
        if first.is_pseudo:
            attrs.update(style='filled', color='lightblue')
        elif last.opname in ('RETURN_VALUE', 'RAISE_VARARGS', 'RERAISE'):
            attrs.update(style='filled', color='orange')
        elif first.back_jumps:
            attrs.update(color='#880088', penwidth='2')
        box = pydotplus.Node(f'n{first.index}', label=f'<{label}>', **attrs)
        dot.add_node(box)
        for node in run:
            dot_nodes[node] = box
    for run in runs:
        for label, succ in run[-1].successors():
            dot.add_edge(pydotplus.Edge(dot_nodes[run[-1]], dot_nodes[succ], fontname='Consolas',
                                        **_EDGE_STYLE[label]))

    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, filename)
    fmt = os.path.splitext(filename)[1].lstrip('.') or 'svg'
    dot.write(path, format=fmt)
    return path
