import ast
import copy
import itertools
from typing import Optional

import networkx
from loguru import logger
from networkx import DiGraph

from decompiler import InstructionNode, resolve_const, resolve_unaryop, resolve_binop, find_linear_path, merge_nodes, \
    render_graph
from decompiler.models import ForLoopContext, TryContext
from decompiler.utils.bytecode import get_context, is_closed, next_n_linear, check_node, assert_except_handler_cleanup, \
    is_load_none
from decompiler.utils.graph import insert_between


class GraphTransformer:
    def __init__(self, decompiler, graph: DiGraph):
        self.decompiler = decompiler
        self.graph = graph
        self._basic_blocks_cache = [self.decompiler.entrypoint]

        self.transformations = {
            0: [self._replace_loads, self._replace_stores, self._eliminate_forwarder_jump],
            1: [],
            2: [self._unary_operation, self._binary_operation, self._build_list_set_tuple, self._append_to_set_list,
                self._list_to_tuple, self._update_set_list, self._call_function, self._unpack_seq_ex, self._build_map,
                self._dict_update, self._jinem, self._build_slice, self._formatted_value, self._build_string,
                self._load_attr, self._call_method],
            3: [self._named_expr, self._for_loops]
        }

    def merge_identical_nodes(self):
        """
        Two nodes u and v are identical if they have the same opcode, same
        implicit successor and same jump target. To merge them, we can
        keep u and redirect all in_edges from v into u.
        :param graph:
        :return: boolean indicating whether the graph was changed
        """
        MERGABLE_NODES = {'POP_JUMP_IF_FALSE', 'POP_JUMP_IF_TRUE'}
        has_changes = False
        counted = {i: [] for i in MERGABLE_NODES}
        for instr in list(self.graph.nodes):
            if instr.opname in MERGABLE_NODES:
                counted[instr.opname].append(instr)

        for nodes in counted.values():
            for u, v in itertools.combinations(nodes, 2):
                # check for existence in graph because we might have already merged them earlier in this loop
                if u in self.graph and v in self.graph and u.next == v.next and u.target == v.target:
                    assert set(self.graph.successors(u)) == set(self.graph.successors(v))
                    for v_pred in list(self.graph.predecessors(v)):
                        self.graph.add_edge(v_pred, u, edge_type=self.graph[v_pred][v]['edge_type'])
                        self.graph.remove_edge(v_pred, v)
                    self.graph.remove_node(v)
                    has_changes = True
        if has_changes:
            logger.warning('Identical nodes merged!')
        return has_changes

    def empty_basic_block(self, contexts: Optional[list] = None) -> InstructionNode:
        ins = InstructionNode(self.graph, -3, l := [], l, None)
        ins.contexts = contexts or []
        return ins

    def transform(self, order: int):
        changed = False
        if order == 0:
            changed = self.merge_identical_nodes()

        transformations = self.transformations.get(order, [])
        for instr in list(self.graph.nodes):
            for op in transformations:
                changed |= op(instr)
            # if any(op(instr) for op in transformations):
            #     return True
        return changed

    def construct_basic_blocks(self):
        c = False
        for bb in reversed(self._basic_blocks_cache):
            c |= self._construct_basic_block(bb)
        return c

    def _mark_closed(self, bb: InstructionNode):
        assert bb.opcode == -3, 'cannot mark as closed something that is not a basic block'
        assert self.graph.out_degree(bb) == 0, 'cannot mark a basic block as closed if it has successors'
        bb.CLOSED = 1
        self._basic_blocks_cache.remove(bb)

    def _construct_basic_block(self, entry_bb: InstructionNode):
        """
        Starting from the entrypoint, construct expressions, statements, ...
        entry_bb(*BASIC_BLOCK) -> node1(any) -> node2(any)
        """
        # TODO: some transformations are unchecked/unsafe, need to assure in_degree(node1) == 1 and similar
        assert entry_bb.opcode == -3

        try:
            node1 = entry_bb.next
        except networkx.exception.NetworkXError as e:
            logger.error(f'graph error: {e!r}')
            logger.debug(f'this is the entry_bb: {entry_bb}')
            raise

        if node1 is None:
            if not is_closed(entry_bb):
                logger.warning(f'no next after basic block which is not marked as closed: {entry_bb}')
            return False

        if getattr(node1, 'DO_NOT_CONSUME', 0) == 1:
            return False
        if self.graph.in_degree(node1) != 1:
            ...
            # if node1.opcode != -6:
            #     logger.warning(f'node1 has more than one predecessor: {node1}')
            # render_graph(self.graph, 'node1.svg')
            # raise RuntimeError

        node2 = node1.next

        if node2 and self.graph.out_degree(node1) == self.graph.out_degree(entry_bb) == self.graph.in_degree(node1) == self.graph.in_degree(node2) == 1:
            if node1.opcode == -4 and node2.opcode == -5:  # assignment top-level statement
                targets = [node2.argval]
                value = node1.argval
                while isinstance(value, ast.NamedExpr):
                    targets.insert(0, value.target)
                    value = value.value
                assign = ast.Assign(targets, value)
                entry_bb.argval.append(assign)
                merge_nodes(self.graph, entry_bb, node2)
                self.graph.remove_nodes_from([node1, node2])
                return True
            if node1.opcode == -4 and node2.opname == 'POP_TOP':  # expression top-level statement
                expr = ast.Expr(value=node1.argval)
                entry_bb.argval.append(expr)
                merge_nodes(self.graph, entry_bb, node2)
                self.graph.remove_nodes_from([node1, node2])
                return True

        # return statement
        if node2 and node2.opname == 'RETURN_VALUE' and node1.opcode == -4 and self.graph.in_degree(node1) == 1:
            # finalize the decompilation
            if not self.decompiler.toplevel:
                entry_bb.argval.append(ast.Return(value=node1.argval))
            else:
                assert is_load_none(node1), 'expected toplevel return value to be None'
            self.graph.remove_nodes_from([node1, node2])
            self._mark_closed(entry_bb)
            return True


        # delete statements
        if node1.opname == 'DELETE_NAME':
            entry_bb.argval.append(ast.Delete(targets=[ast.Name(node1.argval, ast.Del())]))
            merge_nodes(self.graph, entry_bb, node1)
            self.graph.remove_node(node1)
            return True
        if node1.opcode == -4 and node2 and node2.opname == 'DELETE_ATTR':
            entry_bb.argval.append(ast.Delete(targets=[ast.Attribute(node1.argval, node2.argval, ast.Del())]))
            merge_nodes(self.graph, entry_bb, node2)
            self.graph.remove_nodes_from([node1, node2])
            return True
        if node2 and node1.opcode == node2.opcode == -4 and node2.next and node2.next.opname == 'DELETE_SUBSCR':
            entry_bb.argval.append(ast.Delete(targets=[ast.Subscript(node1.argval, node2.argval, ast.Del())]))
            merge_nodes(self.graph, entry_bb, node2.next)
            self.graph.remove_nodes_from([node1, node2, node2.next])
            return True

        # raise statements
        if node1.opname == 'RAISE_VARARGS':
            # raise
            assert node1.argval == 0
            entry_bb.argval.append(ast.Raise())
            merge_nodes(self.graph, entry_bb, node1)
            self.graph.remove_node(node1)
            return True
        if node2 and node2.opname == 'RAISE_VARARGS' and node1.opcode == -4:
            # raise <node1.argval>
            assert node2.argval == 1
            entry_bb.argval.append(ast.Raise(exc=node1.argval))
            merge_nodes(self.graph, entry_bb, node2)
            self.graph.remove_nodes_from([node1, node2])
            return True
        if node2 and node1.opcode == node2.opcode == -4:
            node3 = node2.next
            if node3 and node3.opname == 'RAISE_VARARGS':
                # raise node1.argval from node2.argval
                assert node3.argval == 2
                entry_bb.argval.append(ast.Raise(exc=node1.argval, cause=node2.argval))
                merge_nodes(self.graph, entry_bb, node3)
                self.graph.remove_nodes_from([node1, node2, node3])
                return True

        # import statements
        # https://github.com/python/cpython/blob/3.9/Python/compile.c#L3183-L3330

        # simple 'import a [as b]' where the 'as' part is present only if a does not have dots
        # node1(*LOAD_AST_EXPR(0)) -> node2(*LOAD_AST_EXPR(None)) -> node3(IMPORT_NAME) -> node4(*STORE_VARIABLE)
        if (next4nodes := next_n_linear(entry_bb, 4)) and (
            next4nodes[0].opcode == next4nodes[1].opcode == -4 and
            next4nodes[2].opname == 'IMPORT_NAME' and next4nodes[3].opcode == -5
        ):
            assert isinstance(next4nodes[0].argval, ast.Constant) and next4nodes[0].argval.value == 0
            assert isinstance(next4nodes[1].argval, ast.Constant) and next4nodes[1].argval.value is None
            assert isinstance(next4nodes[3].argval, ast.Name)
            if '.' not in next4nodes[2].argval and next4nodes[2].argval != next4nodes[3].argval.id:
                alias = ast.alias(name=next4nodes[2].argval, asname=next4nodes[3].argval.id)
            else:
                alias = ast.alias(name=next4nodes[2].argval)
            entry_bb.argval.append(ast.Import(names=[alias]))
            merge_nodes(self.graph, entry_bb, next4nodes[3])
            self.graph.remove_nodes_from(next4nodes)
            return True

        # import a.b.c as d
        # this generates a lot of opcodes based on how many dots in the import
        if next4nodes and (
            next4nodes[0].opcode == next4nodes[1].opcode == -4 and
            isinstance(next4nodes[1].argval, ast.Constant) and next4nodes[1].argval.value is None
            and next4nodes[2].opname == 'IMPORT_NAME' and next4nodes[3].opname == 'IMPORT_FROM'
        ):
            # run the assertions on this chain of nodes
            names = next4nodes[2].argval.split('.')
            num_dots = len(names) - 1
            nodes = next_n_linear(next4nodes[2], num_dots*3)
            if nodes is None:
                logger.warning('false positive for "import a.b.c as d" matching rule')
            else:
                i = iter(nodes)
                for idx, (n1, n2, n3) in enumerate(zip(i, i, i)):
                    # logger.debug(f'{idx=}, {n1=}, {n2=}, {n3=}')
                    if n1.opname != 'IMPORT_FROM' or n1.argval != names[idx+1] or n3.opname != 'POP_TOP':
                        logger.warning('import statement weirdness (expected IMPORT_FROM -> x -> POP_TOP)')
                        break
                    if idx == num_dots-1:  # last three
                        if not (n2.opcode == -5 and isinstance(n2.argval, ast.Name)):
                            logger.warning('import statement weirdness (expected IMPORT_FROM -> STORE_VARIABLE -> POP_TOP)')
                            break
                    else:
                        if not (n2.opname == 'ROT_TWO'):
                            logger.warning('import statement weirdness (expected IMPORT_FROM -> ROT_TWO -> POP_TOP)')
                            break
                else:
                    entry_bb.argval.append(ast.Import(names=[ast.alias(next4nodes[2].argval, nodes[-2].argval.id)]))
                    merge_nodes(self.graph, entry_bb, nodes[-1])
                    self.graph.remove_nodes_from(next4nodes[:3])
                    self.graph.remove_nodes_from(nodes)
                    return True

        # from import
        # this also has a lot of opcodes if many names are imported
        if next4nodes and (
            next4nodes[0].opcode == next4nodes[1].opcode == -4 and
            isinstance(next4nodes[0].argval, ast.Constant) and isinstance(next4nodes[0].argval.value, int)
            and isinstance(next4nodes[1].argval, ast.Tuple) and
            next4nodes[2].opname == 'IMPORT_NAME' and next4nodes[3].opname == 'IMPORT_FROM'
        ):
            imported_names = []
            for e in next4nodes[1].argval.elts:
                assert isinstance(e, ast.Constant) and isinstance(e.value, str)
                imported_names.append(e.value)
            num_names = len(imported_names)
            nodes = next_n_linear(next4nodes[-2], num_names*2 + 1)
            names = []
            if nodes is None:
                logger.warning('false positive for "from a import b, c, d" matching rule')
            else:
                assert nodes[-1].opname == 'POP_TOP'
                i = iter(nodes[:-1])
                for idx, (n1, n2) in enumerate(zip(i, i)):
                    if n1.opname != 'IMPORT_FROM' or n2.opcode != -5:
                        logger.warning('from import weirdness (expected IMPORT_FROM -> STORE_VARIABLE)')
                        break
                    assert isinstance(n2.argval, ast.Name)
                    if n1.argval != imported_names[idx]:
                        logger.warning(f'from import weirdness (imported name mismatch, expected {imported_names[idx]}, got {n1.argval})')
                        break
                    if n1.argval != n2.argval.id:
                        names.append(ast.alias(n1.argval, n2.argval.id))
                    else:
                        names.append(ast.alias(n1.argval))
                else:
                    entry_bb.argval.append(ast.ImportFrom(next4nodes[2].argval, names, next4nodes[0].argval.value))
                    merge_nodes(self.graph, entry_bb, nodes[-1])
                    self.graph.remove_nodes_from(next4nodes[:3])
                    self.graph.remove_nodes_from(nodes)
                    return True

        # star import (from ..a.b import *)
        if next4nodes and (
            next4nodes[0].opcode == next4nodes[1].opcode == -4 and
            isinstance(next4nodes[0].argval, ast.Constant) and isinstance(next4nodes[0].argval.value, int)
            and isinstance(t := next4nodes[1].argval, ast.Tuple) and len(t.elts) == 1
            and isinstance(t.elts[0], ast.Constant) and t.elts[0].value == '*'
            and next4nodes[2].opname == 'IMPORT_NAME' and next4nodes[3].opname == 'IMPORT_STAR'
        ):
            entry_bb.argval.append(ast.ImportFrom(next4nodes[2].argval, [ast.alias('*')], next4nodes[0].argval.value))
            merge_nodes(self.graph, entry_bb, next4nodes[-1])
            self.graph.remove_nodes_from(next4nodes)
            return True



        # inplace operations (AugAssigns)
        # https://github.com/python/cpython/blob/3.9/Python/compile.c#L5139

        # inplace add to a regular name, e.g.  a += b
        # node1(*LOAD_AST_EXPR) -> node2(*LOAD_AST_EXPR) -> node3(INPLACE_OP) -> node4(*STORE_VARIABLE)
        if (nodes := next_n_linear(entry_bb, 4)) and (
            nodes[0].opcode == nodes[1].opcode == -4
            and nodes[2].opname.startswith('INPLACE_')
            and nodes[3].opcode == -5
        ):
            assert isinstance(nodes[0].argval, ast.Name)
            assert isinstance(nodes[3].argval, ast.Name)
            assert nodes[0].argval.id == nodes[3].argval.id
            entry_bb.argval.append(ast.AugAssign(
                target=nodes[3].argval,
                op=resolve_binop(nodes[2]),
                value=nodes[1].argval,
            ))
            merge_nodes(self.graph, entry_bb, nodes[3])
            self.graph.remove_nodes_from(nodes)
            return True

        # inplace add to an attribute, e.g.  a.b += c
        # node1(*LOAD_AST_EXPR) -> node2(DUP_TOP) -> node3(LOAD_ATTR) -> node4(*LOAD_AST_EXPR) -> \
        #   -> node5(INPLACE_OP) -> node6(ROT_TWO) -> node7(STORE_ATTR)
        if (nodes := next_n_linear(entry_bb, 7)) and (
            nodes[0].opcode == -4 and nodes[1].opname == 'DUP_TOP' and nodes[2].opname == 'LOAD_ATTR'
            and nodes[3].opcode == -4and nodes[4].opname.startswith('INPLACE_')
            and nodes[5].opname == 'ROT_TWO' and nodes[6].opname == 'STORE_ATTR'
        ):
            assert nodes[2].argval == nodes[6].argval
            entry_bb.argval.append(ast.AugAssign(
                target=ast.Attribute(nodes[0].argval, nodes[2].argval, ast.Store()),
                op=resolve_binop(nodes[4]),
                value=nodes[3].argval,
            ))
            merge_nodes(self.graph, entry_bb, nodes[6])
            self.graph.remove_nodes_from(nodes)
            return True

        # inplace add to a subscript, e.g.  a[b] += c
        # node1(*LOAD_AST_EXPR) -> node2(*LOAD_AST_EXPR) -> node3(DUP_TOP_TWO) -> node4(BINARY_SUBSCR) -> \
        #   -> node5(*LOAD_AST_EXPR) -> node6(INPLACE_OP) -> node7(ROT_THREE) -> node8(STORE_SUBSCR)
        if (nodes := next_n_linear(entry_bb, 8)) and (
            nodes[0].opcode == nodes[1].opcode == nodes[4].opcode == -4 and nodes[5].opname.startswith('INPLACE_')
            and nodes[2].opname == 'DUP_TOP_TWO' and nodes[3].opname == 'BINARY_SUBSCR'
            and nodes[6].opname == 'ROT_THREE' and nodes[7].opname == 'STORE_SUBSCR'
        ):
            entry_bb.argval.append(ast.AugAssign(
                target=ast.Subscript(nodes[0].argval, nodes[1].argval, ast.Store()),
                op=resolve_binop(nodes[5]),
                value=nodes[4].argval,
            ))
            merge_nodes(self.graph, entry_bb, nodes[7])
            self.graph.remove_nodes_from(nodes)
            return True


        # add basic blocks after *FOR_LOOP_START
        if node1.opcode == -6 and not hasattr(node1, 'bb_inserted'):
            node1.bb_inserted = 1
            ctx = node1.context
            ctx.for_bb = for_bb = self.empty_basic_block(entry_bb.contexts.copy() + [ctx])
            # else_bb DOES NOT inherit the for loop context!
            ctx.else_bb = else_bb = self.empty_basic_block(entry_bb.contexts.copy())
            insert_between(self.graph, node1, for_bb, node1.next)
            insert_between(self.graph, node1, else_bb, node1.target)
            self._basic_blocks_cache.append(for_bb)
            self._basic_blocks_cache.append(else_bb)
            return True



        # Try except finally statements:

        # add basic blocks after SETUP_FINALLY
        if node1.opname == 'SETUP_FINALLY' and not hasattr(node1, 'bb_inserted'):
            node1.bb_inserted = 1
            ctx = node1.context = TryContext(node1)
            ctx.try_bb = try_body_bb = self.empty_basic_block(entry_bb.contexts.copy() + [ctx])
            ctx.except_bb = except_body_bb = self.empty_basic_block(entry_bb.contexts.copy() + [ctx])
            insert_between(self.graph, node1, try_body_bb, node1.next)
            insert_between(self.graph, node1, except_body_bb, node1.target)
            self._basic_blocks_cache.append(try_body_bb)
            self._basic_blocks_cache.append(except_body_bb)
            return True

        # SETUP_FINALLY -> *BASIC_BLOCK -> POP_BLOCK -> [finally block begins]
        if node1.opname == 'SETUP_FINALLY' and hasattr(node1, 'bb_inserted'):
            ctx = node1.context
            try_bb = node1.next
            after_try = try_bb and try_bb.next
            if after_try and after_try.opname == 'POP_BLOCK':
                finally_bb_start = after_try.next
                assert finally_bb_start is not None
                ctx.finally_bb = finally_bb = self.empty_basic_block(entry_bb.contexts.copy() + [ctx])
                # close the initial basic block
                self.graph.remove_node(after_try)
                self._mark_closed(try_bb)  # TODO: this is suspicious, do we really have to mark it as closed here?
                self.graph.add_edge(node1, finally_bb, edge_type='transient')
                self.graph.add_edge(finally_bb, finally_bb_start, edge_type='implicit')
                self._basic_blocks_cache.append(finally_bb)
                return True

        # TODO: are we using this?
        if node1.opname == 'SETUP_FINALLY' and hasattr(node1, 'context'):
            ctx = node1.context
            after_exc = ctx.except_bb.next
            # print(ctx.try_bb is not None, ctx.except_bb is not None, ctx.finally_bb is not None, after_exc is not None)
            if ctx.handlers == [] and is_closed(ctx.try_bb) and ctx.try_bb and ctx.except_bb and ctx.finally_bb \
                    and after_exc and after_exc.opname == 'RERAISE' and after_exc.next == ctx.finally_bb.next:
                # TODO: undefined behavior since RERAISE.next should not be an implicit successor?
                logger.warning(f'TODO: Assert these basic blocks are equal! \n{ast.unparse(ctx.except_bb.argval)}\n\n{ast.unparse(ctx.except_bb.argval)}')
                ast_try = ast.Try(
                    body=ctx.try_bb.argval, handlers=[], orelse=[],
                    finalbody=ctx.finally_bb.argval
                )
                entry_bb.argval.append(ast_try)
                self.graph.add_edge(entry_bb, after_exc.next, edge_type='implicit')
                self.graph.remove_nodes_from([node1, ctx.try_bb, ctx.except_bb, ctx.finally_bb, after_exc])
                self._basic_blocks_cache.remove(ctx.except_bb)
                self._basic_blocks_cache.remove(ctx.finally_bb)
                return True

        # *EXC_MATCH - prepare the basic blocks
        def parse_exc_match(exc_match):
            exc_match.parsed = 1
            after_exc = exc_match.next
            if after_exc.opcode == -9:  # If the exception is saved to a variable, there's always a *PSEUDOTRY
                assert exc_match.err_variable is not None
                bb = self.empty_basic_block(entry_bb.contexts.copy())
                insert_between(self.graph, after_exc, bb, after_exc.next)
                self._basic_blocks_cache.append(bb)
                cleanup = next_n_linear(after_exc.target, 4, True)
                assert_except_handler_cleanup(exc_match, *cleanup)
                self.graph.remove_nodes_from(cleanup)
            else:  # except E1: (without the 'as V1' part)
                assert exc_match.err_variable is None
                bb = self.empty_basic_block(entry_bb.contexts.copy())
                insert_between(self.graph, exc_match, bb, exc_match.next)
                self._basic_blocks_cache.append(bb)

        if node1.opcode == -13 and not hasattr(node1, 'parsed'):
            curr = node1
            # parse them all in one go
            while curr.opcode == -13 and not hasattr(curr, 'parsed'):
                parse_exc_match(curr)
                curr = curr.target
            return True

        if node1.opcode == -13 and hasattr(node1, 'parsed'):
            exc_match = node1
            pseudotry = node2
            if pseudotry.opcode == -9:
                # check if the handler body is fully parsed
                # handler cleanup: https://github.com/python/cpython/blob/3.9/Python/compile.c#L3129
                n1, n2 = next_n_linear(handler_bb := pseudotry.next, 2)
                if n1.opname == 'POP_BLOCK':
                    assert n2.opname == 'POP_EXCEPT'
                    cleanup = next_n_linear(n2, 3)  # no RERAISE here
                    assert_except_handler_cleanup(exc_match, *cleanup)
                    # logger.info('*EXC_MATCH handler body is fully parsed!')

                    # remove the pseudotry node
                    self.graph.add_edge(exc_match, handler_bb, edge_type='implicit')
                    self.graph.remove_node(pseudotry)

                    # remove other nodes
                    self.graph.add_edge(handler_bb, cleanup[2].next, edge_type='implicit')
                    self.graph.remove_nodes_from([n1, n2] + cleanup)
                    exc_match.complete = 1  # mark the *EXC_MATCH as complete TODO: is this used anywhere?
                    # logger.debug(f'*EXC_MATCH (with variable) marked as complete: {exc_match}')
                    return True
            # no pseudotry meaning we don't save the exception to a variable
            elif node2.opcode == -3 and node2.next and node2.next.opname == 'POP_EXCEPT':
                pop_except = node2.next
                self.graph.add_edge(node2, pop_except.next, edge_type='implicit')
                self.graph.remove_node(pop_except)
                exc_match.complete = 1
                # logger.debug(f'*EXC_MATCH (without variable) marked as complete: {exc_match}')
                return True

        if node1.opcode == -13 and hasattr(node1, 'complete'):
            ctx = get_context(entry_bb, TryContext)
            # logger.debug(f'ctx: {ctx}')
            handler_body = node1.next
            last = handler_body.next
            if handler_body.opcode == -3 and last and self.graph.in_degree(last) > 1:
                ctx.handlers.append((node1, handler_body))
                self.graph.add_edge(entry_bb, node1.target, edge_type='implicit')
                self.graph.remove_nodes_from([node1, handler_body])
                self._basic_blocks_cache.remove(handler_body)
                # logger.info(f'Added an *EXC_MATCH handler: {node1}')
                return True

        # SETUP_FINALLY => BASIC_BLOCK -> 3x POP_TOP  the beginning of a bare except: block
        if node1.opname == 'SETUP_FINALLY' and node1.target.opcode == -3:
            next3nodes = next_n_linear(except_bb := node1.target, 3)
            if next3nodes and next3nodes[0].opname == next3nodes[1].opname == next3nodes[2].opname == 'POP_TOP':
                ctx = get_context(node1.target, TryContext)
                assert isinstance(ctx, TryContext)
                merge_nodes(self.graph, except_bb, next3nodes[-1])
                self.graph.remove_nodes_from(next3nodes)
                return True

        # check if we can create a try statement
        if node1.opname == 'SETUP_FINALLY':
            try_bb = node1.next
            except_bb = node1.target
            else_bb = node1.context.finally_bb
            after_except = except_bb and except_bb.next
            next_node = else_bb and else_bb.next
            # logger.debug(f'\ntry_bb: {try_bb}\nexcept_bb: {except_bb}\nelse_bb: {else_bb}\nafter_except: {after_except}\nnext_node: {next_node}')
            if else_bb and try_bb.opcode == except_bb.opcode == else_bb.opcode == -3 and is_closed(try_bb) and \
                after_except.opname in {'POP_EXCEPT', 'RERAISE'} and after_except.next == next_node:
                handlers = []
                for exc_match_node, handler_body in node1.context.handlers:
                    if exc_match_node.err_variable is not None:
                        handlers.append(ast.ExceptHandler(
                            type=exc_match_node.argval,
                            name=exc_match_node.err_variable,
                            body=handler_body.argval
                        ))
                    else:
                        handlers.append(ast.ExceptHandler(type=exc_match_node.argval, body=handler_body.argval))

                # no 'except:' block
                finalbody = []
                if after_except.opname == 'RERAISE':
                    # This has a finally block and it should have been parsed by another transformation above
                    assert except_bb.argval == [], 'a finally block should not appear here'
                else:
                    handlers.append(ast.ExceptHandler(body=except_bb.argval))

                ast_try = ast.Try(
                    body=try_bb.argval,
                    handlers=handlers,
                    orelse=else_bb.argval,
                    finalbody=finalbody
                )
                entry_bb.argval.append(ast_try)

                # \n{ast.dump(ast_try, indent=2)}
                # logger.debug(f'Added a Try statement: \ndecompiled as \n{ast.unparse(ast_try)}')

                self.graph.add_edge(entry_bb, next_node, edge_type='implicit')
                self.graph.remove_nodes_from([node1, try_bb, except_bb, else_bb, after_except])
                self._basic_blocks_cache.remove(except_bb)
                self._basic_blocks_cache.remove(else_bb)
                return True









        # create an *IF_STMT
        # entry_bb(*BASIC_BLOCK) -> node1(*LOAD_AST_EXPR) -> node2(*POP_JUMP_IF_TRUE/FALSE)
        if node2 and self.graph.out_degree(node1) == self.graph.in_degree(node2) == self.graph.in_degree(node1) == 1:
            if node1.opcode == -4 and node2.opname in {'POP_JUMP_IF_FALSE', 'POP_JUMP_IF_TRUE'}:
                test = node1.argval
                if node2.opname == 'POP_JUMP_IF_TRUE':
                    test = ast.UnaryOp(op=ast.Not(), operand=test)

                # insert the block between node2 and node2.next & node2.target respectively
                node2_next = node2.next
                node2_target = node2.target

                # shortcut for ast.IfExp nodes (ternary if expressions)
                if node2_next.opcode == node2_target.opcode == -4 and node2_next.next == node2_target.next \
                    and self.graph.in_degree(node2_next) == self.graph.in_degree(node2_target) == 1:
                    after = node2_next.next

                    node2.opcode = -4
                    node2.argval = ast.IfExp(test, node2_next.argval, node2_target.argval)

                    self.graph.remove_nodes_from([node1, node2_next, node2_target])
                    self.graph.add_edge(entry_bb, node2, edge_type='implicit')
                    self.graph.add_edge(node2, after, edge_type='implicit')
                    return True

                # 'next' points to the if block and 'target' to the else block
                # regardless of whether the test is negated
                if_block = self.empty_basic_block(entry_bb.contexts.copy())
                else_block = self.empty_basic_block(entry_bb.contexts.copy())
                if_block.INSIDE_FOR_LOOP = getattr(entry_bb, 'INSIDE_FOR_LOOP', 0)
                else_block.INSIDE_FOR_LOOP = getattr(entry_bb, 'INSIDE_FOR_LOOP', 0)


                self.graph.remove_edge(node2, node2_next)
                self.graph.add_edge(node2, if_block, edge_type='implicit')
                self.graph.add_edge(if_block, node2_next, edge_type='implicit')

                self.graph.remove_edge(node2, node2_target)
                self.graph.add_edge(node2, else_block, edge_type='explicit')
                self.graph.add_edge(else_block, node2_target, edge_type='implicit')

                node2.opcode = -11
                node2.argval = test

                # connect predecessors of 'test' to the if block and remove 'test' node
                for pred in list(self.graph.predecessors(node1)):
                    self.graph.add_edge(pred, node2, edge_type=self.graph[pred][node1]['edge_type'])
                self.graph.remove_node(node1)

                self._basic_blocks_cache.append(if_block)
                self._basic_blocks_cache.append(else_block)
                return True

        # Transform an *IF_STMT into an ast.If statement. This can be done in three cases labeled a) b) c) below.
        # a) both are closed, in this case the resulting basic block is also closed
        # b) one of if_bb or else_bb is closed (e.g. by a 'continue' statement) and the other has multiple implicit predecessors
        # c) if_bb and else_bb both point to the same next node, or
        if node1.opcode == -11:
            if_bb = node1.next
            else_bb = node1.target
            if_bb_next = if_bb.next
            else_bb_next = else_bb.next

            def make_if(_test, _if_bb, _else_bb) -> ast.If:
                if not (_body := _if_bb.argval):  # the 'if' may not be empty, but 'else' can be
                    _body = [ast.Pass()]
                return ast.If(test=_test, body=_body, orelse=_else_bb.argval)

            # a) both if_bb and else_bb are closed
            if getattr(if_bb, 'CLOSED', 0) == getattr(else_bb, 'CLOSED', 0) == 1:  # DONE
                assert if_bb not in self._basic_blocks_cache
                assert else_bb not in self._basic_blocks_cache
                entry_bb.argval.append(make_if(node1.argval, if_bb, else_bb))
                self.graph.remove_nodes_from([node1, if_bb, else_bb])
                self._mark_closed(entry_bb)  # in this case the resulting block is also closed
                return True

            # b) the entire if statement is parsed, that is if both if_bb and else_bb have the same successor
            if if_bb_next is not None and if_bb_next == (after := else_bb.next):
                entry_bb.argval.append(make_if(node1.argval, if_bb, else_bb))
                self.graph.add_edge(entry_bb, after, edge_type='implicit')
                self.graph.remove_nodes_from([node1, if_bb, else_bb])
                self._basic_blocks_cache.remove(if_bb)
                self._basic_blocks_cache.remove(else_bb)
                return True

            # c) one of the (if_bb, else_bb) is closed and the other one has multiple implicit predecessors
            #    (note that if it has a single predecessor, it should have been merged earlier in this function)
            # TODO: check `self.graph.in_degree(if_bb_next) > 1`  instead of this len function??
            for bb1, bb2 in ((if_bb, else_bb), (else_bb, if_bb)):
                if (bb1_next := bb1.next) is not None and is_closed(bb2) and \
                    len([p for p in self.graph.predecessors(bb1_next)
                         if self.graph[p][bb1_next]['edge_type'] == 'implicit']) > 1:
                    assert bb2 not in self._basic_blocks_cache
                    assert bb2.next is None
                    entry_bb.argval.append(make_if(node1.argval, if_bb, else_bb))
                    self.graph.add_edge(entry_bb, bb1_next, edge_type='implicit')
                    self.graph.remove_nodes_from([node1, bb1, bb2])
                    self._basic_blocks_cache.remove(bb1)
                    return True

        # if the basic block points back to a for loop, it's a continue statement
        if (ctx := get_context(entry_bb, ForLoopContext)) and node1.opcode == -6 and node1 == ctx.start_node:
            self.graph.remove_edge(entry_bb, node1)
            entry_bb.argval.append(ast.Continue())
            self._mark_closed(entry_bb)
            return True

        # if the basic block points to a POP_TOP and POP_TOP.next points to the same block as
        # FOR_LOOP's "else" basic block's next, then we have a break statement
        if (ctx := get_context(entry_bb, ForLoopContext)) and node1.opname == 'POP_TOP':
            after_for = node1.next
            if ctx.after_for is None:
                ctx.after_for = after_for
            else:
                if not ctx.after_for == after_for:
                    logger.warning(f'for loop context mismatch: \n{ctx.after_for}\n!=\n{after_for}\n\n{entry_bb = }')
                    logger.info(f'contexts: {entry_bb.contexts}')
                    render_graph(self.graph, 'forloopcontextmismatch.svg')
                    logger.info(f'have this context: {ctx}')
                    logger.info(f'POP_TOP\'s successor is {after_for}')
                    logger.info(f'but the context\'s expected one is {ctx.after_for}')
                    raise RuntimeError

            after_for.DO_NOT_CONSUME = 1

            # we cannot use single_prev because both the POP_TOP and the for_else_bb (that we're looking for)
            # as well as possibly some other nodes point to the 'after_for' node.
            for_else_bb = ctx.else_bb
            assert for_else_bb is not None
            if for_else_bb and for_else_bb.opcode == -3:
                for_loop_start = for_else_bb.single_prev
                if for_loop_start and for_loop_start.opcode == -6:
                    entry_bb.argval.append(ast.Break())
                    self.graph.remove_node(node1)
                    self._mark_closed(entry_bb)
                    return True

        # add a For loop
        # node1(*FOR_LOOP_START) -> node2(*BASIC_BLOCK / for loop body)
        #             \-target => else_bb(*BASIC_BLOCK) -> *FOR_LOOP_END
        if node1.opcode == -6 and node2.opcode == -3 and is_closed(node2):
            else_bb = node1.target
            ctx: ForLoopContext = node1.context
            assert ctx.for_bb == node2
            assert ctx.else_bb == else_bb, 'do we need this assertion?'
            # marker = else_bb.next
            # this is when there's another control flow statement in the else block, which causes
            # the else block to be closed
            marker = ctx.after_for
            if marker is None:
                # logger.warning(f'marker for {ctx} is None, does the for loop have a break statement? continuing with marker = {else_bb.next }')
                marker = else_bb.next

            ast_for = ctx.ast_for(target=ctx.assignment, iter=ctx.iterable, body=ctx.for_bb.argval, orelse=else_bb.argval)
            if else_bb.opcode == -3 and getattr(marker, 'DO_NOT_CONSUME', 0) == 1:
                entry_bb.argval.append(ast_for)
                self.graph.add_edge(entry_bb, marker, edge_type='implicit')
                self.graph.remove_nodes_from([node1, node2, else_bb])
                if not is_closed(else_bb):
                    self._basic_blocks_cache.remove(else_bb)
                del marker.DO_NOT_CONSUME
                return True

            if else_bb.opcode == -3 and marker is None and is_closed(ctx.for_bb) and is_closed(else_bb):
                entry_bb.argval.append(ast_for)
                self.graph.remove_nodes_from([node1, node2, else_bb])
                self._mark_closed(entry_bb)
                return True

            if else_bb.opcode == -3 and marker is not None:
                assert is_closed(ctx.for_bb)
                # in this case, the for loop does not have a break statement
                # so else_bb is just the code that follows the for loop
                ast_for = ctx.ast_for(target=ctx.assignment, iter=ctx.iterable, body=ctx.for_bb.argval, orelse=[])
                entry_bb.argval.append(ast_for)
                entry_bb.argval.extend(else_bb.argval)
                self.graph.add_edge(entry_bb, marker, edge_type='implicit')
                self.graph.remove_nodes_from([node1, node2, else_bb])
                self._basic_blocks_cache.remove(else_bb)
                return True

        # the 'with' statement
        # https://github.com/python/cpython/blob/3.9/Python/compile.c#L4933
        if node1.opcode == -4 and node2 and node2.opname == 'SETUP_WITH':
            opt_vars = node2.next
            assert opt_vars.opcode == -5 or opt_vars.opname == 'POP_TOP'
            if not hasattr(node2, 'bb_inserted'):
                node2.bb_inserted = 1
                with_bb = self.empty_basic_block(entry_bb.contexts.copy())
                insert_between(self.graph, opt_vars, with_bb, opt_vars.next)
                self._basic_blocks_cache.append(with_bb)
                return True
            with_bb = opt_vars.next
            pb = with_bb.next
            if pb.opname == 'POP_BLOCK':
                ln = pb.next  # load none
                n1 = ln.next  # DUP_TOP
                n2 = n1.next  # DUP_TOP
                cf = n2.next  # CALL_FUNCTION
                n3 = cf.next  # POP_TOP

                wes = node2.target  # WITH_EXCEPT_START
                jmp = wes.next  # POP_JUMP_IF_TRUE
                rer = jmp.next  # RERAISE
                m1 = jmp.target  # POP_TOP
                assert rer.next == m1  # TODO: accessing reraise.next!
                m2 = m1.next  # POP_TOP
                m3 = m2.next  # POP_TOP
                me = m3.next  # POP_EXCEPT
                m4 = me.next  # POP_TOP
                assert n3.opname == m1.opname == m2.opname == m3.opname == m4.opname == 'POP_TOP'
                assert me.opname == 'POP_EXCEPT'
                assert jmp.opname == 'POP_JUMP_IF_TRUE'
                assert rer.opname == 'RERAISE'
                assert wes.opname == 'WITH_EXCEPT_START'
                assert cf.opname == 'CALL_FUNCTION'
                assert n1.opname == n2.opname == 'DUP_TOP' and is_load_none(ln)
                assert find_linear_path(self.graph, pb, n3) == [pb, ln, n1, n2, cf, n3]
                assert find_linear_path(self.graph, m1, m4) == [m1, m2, m3, me, m4]
                assert self.graph.in_degree(wes) == self.graph.in_degree(jmp) == self.graph.in_degree(rer) == self.graph.in_degree(m1)-1 == 1
                assert n3.next == m4.next

                if with_bb.argval == []:
                    with_bb.argval.append(ast.Pass())
                ast_with = ast.With(
                    items=[ast.withitem(context_expr=node1.argval, optional_vars=opt_vars.argval if opt_vars.opcode == -5 else None)],
                    body=with_bb.argval
                )
                entry_bb.argval.append(ast_with)
                self.graph.add_edge(entry_bb, n3.next, edge_type='implicit')
                self.graph.remove_nodes_from([node1, node2, opt_vars, with_bb, pb, ln, n1, n2, cf, n3, wes, jmp, rer, m1, m2, m3, me, m4])
                self._basic_blocks_cache.remove(with_bb)
                return True

        # 'async with' statement
        # https://github.com/python/cpython/blob/3.9/Python/compile.c#L4831
        if node1.opcode == -4 and node2 and node2.opname == 'BEFORE_ASYNC_WITH':
            ga = node2.next  # GET_AWAITABLE
            ln = ga.next  # load None
            yf = ln.next  # YIELD_FROM
            saw = yf.next  # SETUP_ASYNC_WITH
            opt_vars = saw.next  # *STORE_VARIABLE or POP_TOP
            assert opt_vars.opcode == -5 or opt_vars.opname == 'POP_TOP'
            if not hasattr(node2, 'bb_inserted'):
                node2.bb_inserted = 1
                with_bb = self.empty_basic_block(entry_bb.contexts.copy())
                insert_between(self.graph, opt_vars, with_bb, opt_vars.next)
                self._basic_blocks_cache.append(with_bb)
                return True
            with_bb = opt_vars.next
            pb = with_bb.next
            if pb.opname == 'POP_BLOCK':
                ln1 = pb.next  # load None
                dt1 = ln1.next  # DUP_TOP
                dt2 = dt1.next  # DUP_TOP
                cf = dt2.next  # CALL_FUNCTION
                ga1 = cf.next  # GET_AWAITABLE
                ln2 = ga1.next  # load None
                yf1 = ln2.next  # YIELD_FROM
                m0 = yf1.next  # POP_TOP

                wes = saw.target  # WITH_EXCEPT_START
                ga2 = wes.next  # GET_AWAITABLE
                ln3 = ga2.next  # load None
                yf2 = ln3.next  # YIELD_FROM
                jmp = yf2.next  # POP_JUMP_IF_TRUE
                rer = jmp.next  # RERAISE
                m1 = jmp.target  # POP_TOP
                assert rer.next == m1  # TODO: accessing reraise.next!
                m2 = m1.next  # POP_TOP
                m3 = m2.next  # POP_TOP
                me = m3.next  # POP_EXCEPT
                m4 = me.next  # POP_TOP

                assert is_load_none(ln) and is_load_none(ln1) and is_load_none(ln2) and is_load_none(ln3)
                assert dt1.opname == dt2.opname == 'DUP_TOP'
                assert ga.opname == ga1.opname == ga2.opname == 'GET_AWAITABLE'
                assert cf.opname == 'CALL_FUNCTION'
                assert yf.opname == yf1.opname == yf2.opname == 'YIELD_FROM'
                assert m0.opname == m1.opname == m2.opname == m3.opname == m4.opname == 'POP_TOP'
                assert rer.opname == 'RERAISE'
                assert jmp.opname == 'POP_JUMP_IF_TRUE'
                assert wes.opname == 'WITH_EXCEPT_START'
                assert m0.next == m4.next
                assert find_linear_path(self.graph, pb, m0) == [pb, ln1, dt1, dt2, cf, ga1, ln2, yf1, m0]
                assert find_linear_path(self.graph, m1, m4) == [m1, m2, m3, me, m4]
                assert find_linear_path(self.graph, wes, jmp) == [wes, ga2, ln3, yf2, jmp]
                assert self.graph.in_degree(jmp) == self.graph.in_degree(rer) == self.graph.in_degree(m1)-1 == 1
                if with_bb.argval == []:
                    with_bb.argval.append(ast.Pass())
                ast_with = ast.AsyncWith(
                    items=[ast.withitem(context_expr=node1.argval, optional_vars=opt_vars.argval if opt_vars.opcode == -5 else None)],
                    body=with_bb.argval
                )
                entry_bb.argval.append(ast_with)
                self.graph.add_edge(entry_bb, m0.next, edge_type='implicit')
                self.graph.remove_nodes_from([node1, node2, opt_vars, with_bb, pb, ln, dt1, dt2, cf, ga1, ln2, yf1, m0,
                                               wes, ga2, ln3, yf2, jmp, rer, m1, m2, m3, me, m4, yf, saw, ga, ln1])
                self._basic_blocks_cache.remove(with_bb)
                return True

        return False

    def _eliminate_forwarder_jump(self, instr: InstructionNode):
        """
        A forwarder jump is an unconditional jump with a single predecessor
        """
        if 'JUMP_ABSOLUTE' != instr.opname != 'JUMP_FORWARD':
            return False

        for pred in list(self.graph.predecessors(instr)):
            self.graph.add_edge(pred, instr.target, edge_type=self.graph[pred][instr]['edge_type'])
            self.graph.remove_edge(pred, instr)

        self.graph.remove_edge(instr, instr.target)
        assert self.graph.out_degree(instr) == 0
        self.graph.remove_node(instr)
        return True

    def _replace_loads(self, instr: InstructionNode):
        """
        Replace LOAD_CONST and LOAD_NAME with their AST equivalent, represented by LOAD_AST_EXPR pseudo opcode.
        """
        if instr.opname == 'LOAD_CONST':
            if r := resolve_const(instr.argval):
                instr.opcode = -4
                instr.argval = r
                return True
            logger.warning(f'skipped LOAD_CONST of type {type(instr.argval)}')
        elif instr.opname == 'LOAD_NAME':
            instr.opcode = -4
            instr.argval = ast.Name(instr.argval, ast.Load())
            return True
        return False

    def _replace_stores(self, instr: InstructionNode):
        """
        Replace STORE_NAME, STORE_ATTR, STORE_SUBSCR with a *STORE_VARIABLE pseudo opcode whose argval can be
        a Name, an Attribute, a Subscript or a Tuple* representing the target for storing the value.
        *Tuple is created in UNPACK_SEQUENCE and UNPACK_EX transformations.
        """
        if instr.opname in {'STORE_NAME', 'STORE_GLOBAL', 'STORE_FAST', 'STORE_DEREF'}:
            instr.origin = instr.opname
            instr.opcode = -5
            instr.argval = ast.Name(instr.argval, ast.Store())
            return True
        if instr.opname == 'STORE_ATTR':
            target = instr.single_prev
            if target and target.opcode == -4 and self.graph.in_degree(instr) == 1:
                target.opcode = -5
                target.argval = ast.Attribute(target.argval, instr.argval, ast.Store())
                merge_nodes(self.graph, target, instr)
                self.graph.remove_node(instr)
                return True
        if instr.opname == 'STORE_SUBSCR':
            _slice = instr.single_prev
            target = _slice.single_prev
            if target and _slice and target.opcode == _slice.opcode == -4 and \
                    find_linear_path(self.graph, target, instr) == [target, _slice, instr]:
                subscript = ast.Subscript(value=target.argval, slice=_slice.argval, ctx=ast.Store())
                target.opcode = -5
                target.argval = subscript
                merge_nodes(self.graph, target, instr)
                self.graph.remove_nodes_from([_slice, instr])
                return True
        return False

    def _unary_operation(self, instr: InstructionNode):
        """
        val(*LOAD_AST_EXPR) -> instr(UNARY_OP)
        """
        if instr.opname.startswith('UNARY_'):
            if self.graph.in_degree(instr) == 1 and (val := instr.single_prev) is not None:
                val.argval = ast.UnaryOp(resolve_unaryop(instr), val.argval)
                self.graph.add_edge(val, instr.next, edge_type='implicit')
                self.graph.remove_node(instr)
                return True
        return False

    def _binary_operation(self, instr: InstructionNode):
        """
        left(*LOAD_AST_EXPR) -> right(*LOAD_AST_EXPR) -> instr(BINARY_OP)
        """
        if instr.opname.startswith('BINARY_') and instr.opname != 'BINARY_SUBSCR':
            right = instr.single_prev
            left = right.single_prev
            if left and right and left.opcode == right.opcode == -4 and \
                    self.graph.in_degree(instr) == self.graph.in_degree(right) == 1:
                # we modify the first node, 'left' in this case
                left.argval = ast.BinOp(left.argval, resolve_binop(instr), right.argval)
                self.graph.add_edge(left, instr.next, edge_type='implicit')
                self.graph.remove_nodes_from([right, instr])
                return True
        if instr.opname == 'BINARY_SUBSCR':
            _slice = instr.single_prev
            target = _slice.single_prev
            if target and _slice and target.opcode == _slice.opcode == -4 and \
                    find_linear_path(self.graph, target, instr) == [target, _slice, instr]:
                target.argval = ast.Subscript(value=target.argval, slice=_slice.argval, ctx=ast.Load())
                merge_nodes(self.graph, target, instr)
                self.graph.remove_nodes_from([_slice, instr])
                return True
        return False

    def _build_list_set_tuple(self, instr: InstructionNode):
        ast_types = {
            'BUILD_LIST': ast.List,
            'BUILD_SET': ast.Set,
            'BUILD_TUPLE': ast.Tuple
        }
        if instr.opname in ast_types:
            if (num_elts := instr.argval) > 0:
                node1 = instr.nth_implicit_prev(num_elts)
                nodes = find_linear_path(self.graph, node1, instr)
                if nodes and all(i.opcode == -4 for i in nodes[:-1]):
                    elts = [i.argval for i in nodes[:-1]]
                    node1.argval = ast_types[instr.opname](elts=elts, ctx=ast.Load())
                    merge_nodes(self.graph, node1, instr)
                    self.graph.remove_nodes_from(nodes[1:])
                    return True
            else:
                if instr.opcode == 'BUILD_SET':
                    logger.warning(f'if an empty set literal ends up in the final code, it will be \x7b*()\x7d which compiles differently (from {instr})')
                instr.argval = ast_types[instr.opname](elts=[], ctx=ast.Load())
                instr.opcode = -4
        return False

    def _update_set_list(self, instr: InstructionNode):
        ast_types = {
            'SET_UPDATE': ast.Set,
            'LIST_EXTEND': ast.List,
        }
        if instr.opname in ast_types:
            right = instr.single_prev
            left = right.single_prev
            if left and right and left.opcode == right.opcode == -4 and find_linear_path(self.graph, left, instr) == [left, right, instr]:
                assert isinstance(left.argval, ast_types[instr.opname])
                if hasattr(right.argval, 'CONST_FROZENSET'):# or hasattr(right.argval, 'CONST_TUPLE'):
                    left.argval.elts.extend(right.argval.elts)
                else:
                    left.argval.elts.append(ast.Starred(right.argval, ast.Load()))
                merge_nodes(self.graph, left, instr)
                self.graph.remove_nodes_from([right, instr])
                return True
        return False

    def _append_to_set_list(self, instr: InstructionNode):
        ast_types = {
            'LIST_APPEND': ast.List,
            'SET_ADD': ast.Set,
        }
        if instr.opname in ast_types:
            right = instr.single_prev
            left = right.single_prev
            if left and right and left.opcode == right.opcode == -4 and \
                    find_linear_path(self.graph, left, instr) == [left, right, instr]:
                assert isinstance(left.argval, ast_types[instr.opname])
                left.argval.elts.append(right.argval)
                merge_nodes(self.graph, left, instr)
                self.graph.remove_nodes_from([right, instr])
                return True
        return False

    def _list_to_tuple(self, instr: InstructionNode):
        if instr.opname == 'LIST_TO_TUPLE':
            prev = instr.single_prev
            if prev and prev.opcode == -4:
                assert isinstance(prev.argval, ast.List)
                prev.argval = ast.Tuple(prev.argval.elts, ast.Load())
                merge_nodes(self.graph, prev, instr)
                self.graph.remove_node(instr)
                return True
        return False

    def _build_map(self, instr: InstructionNode):
        """
        node0(*LOAD_AST_EXPR) -> node1(*LOAD_AST_EXPR) -> ... -> instr(*BUILD_MAP)
        """
        if instr.opname == 'BUILD_MAP':
            if (num_elts := instr.argval) > 0:
                node0 = instr.nth_implicit_prev(num_elts * 2)
                nodes = node0 and find_linear_path(self.graph, node0, instr)
                if nodes and all(i.opcode == -4 for i in nodes[:-1]):
                    assert len(nodes) == instr.argval * 2 + 1
                    keys = [i.argval for i in nodes[0:-1:2]]
                    values = [i.argval for i in nodes[1:-1:2]]
                    node0.opcode = -4
                    node0.argval = ast.Dict(keys, values)
                    merge_nodes(self.graph, node0, instr)
                    self.graph.remove_nodes_from(nodes[1:])
                    return True
            else:
                instr.argval = ast.Dict([], [])
                instr.opcode = -4
        elif instr.opname == 'BUILD_CONST_KEY_MAP':
            if (num_elts := instr.argval) > 0:
                node0 = instr.nth_implicit_prev(num_elts+1)
                nodes = node0 and find_linear_path(self.graph, node0, instr)
                if nodes and all(i.opcode == -4 for i in nodes[:-1]):
                    *values, keys = nodes[:-1]
                    assert keys.opcode == -4 and isinstance(keys.argval, ast.Tuple)
                    dict_keys = copy.deepcopy(keys.argval.elts)
                    dict_values = [copy.deepcopy(v.argval) for v in values]
                    node0.opcode = -4
                    node0.argval = ast.Dict(dict_keys, dict_values)
                    merge_nodes(self.graph, node0, instr)
                    self.graph.remove_nodes_from(nodes[1:])
                    return True
        return False

    def _dict_update(self, instr: InstructionNode):
        """
        target(*LOAD_AST_EXPR) -> value(*LOAD_AST_EXPR) -> instr(DICT_UPDATE)
        """
        # TODO: I guess the decompiler doesn't care about DICT_MERGE raising an exception on duplicate keys?
        # https://docs.python.org/3.9/library/dis.html#opcode-DICT_MERGE
        if instr.opname == 'DICT_UPDATE' or instr.opname == 'DICT_MERGE':

            assert instr.argval == 1  # cpython 3.9 always generates 1 here (the docs mentioning TOS1[-i] are wrong)
            value = instr.single_prev  # what to update it with
            target = value.single_prev  # the dict to update
            if target and value and target.opcode == value.opcode == -4 and \
                    find_linear_path(self.graph, target, instr) == [target, value, instr]:
                assert isinstance(target.argval, ast.Dict)
                # logger.debug(f'updating {ast.unparse(target.argval)} with {ast.unparse(value.argval)}')
                if isinstance(value.argval, ast.Dict):
                    # so we don't end up with  {**g, **h, **{'k': 11, 'k2': 1212}}
                    target.argval.keys.extend(value.argval.keys)
                    target.argval.values.extend(value.argval.values)
                else:
                    target.argval.keys.append(None)
                    target.argval.values.append(value.argval)
                merge_nodes(self.graph, target, instr)
                self.graph.remove_nodes_from([value, instr])
                return True
        return False

    def _call_function(self, instr: InstructionNode):
        if instr.opname == 'CALL_FUNCTION':
            num_args = instr.argval
            node1 = instr.nth_implicit_prev(num_args + 1)
            nodes = find_linear_path(self.graph, node1, instr)
            if nodes and all(i.opcode == -4 for i in nodes[:-1]):
                args = [i.argval for i in nodes[1:-1]]
                node1.argval = ast.Call(func=node1.argval, args=args, keywords=[])
                merge_nodes(self.graph, node1, instr)
                self.graph.remove_nodes_from(nodes[1:])
                return True
        elif instr.opname == 'CALL_FUNCTION_KW':
            num_args = instr.argval
            node1 = instr.nth_implicit_prev(num_args + 2)
            nodes = find_linear_path(self.graph, node1, instr)
            if nodes and all(i.opcode == -4 for i in nodes[:-1]):
                *args, kwargs_tuple = nodes[1:-1]
                kwargs_names = [c.value for c in kwargs_tuple.argval.elts]
                num_pos_args = num_args - len(kwargs_names)
                pos_args = [i.argval for i in args[:num_pos_args]]
                kw_args = [ast.keyword(kw, i.argval) for kw, i in zip(kwargs_names, args[num_pos_args:])]

                node1.argval = ast.Call(func=node1.argval, args=pos_args, keywords=kw_args)
                merge_nodes(self.graph, node1, instr)
                self.graph.remove_nodes_from(nodes[1:])
                return True
        elif instr.opname == 'CALL_FUNCTION_EX':
            flags = instr.argval
            if flags & 1:
                kwargs = instr.single_prev
                posargs = kwargs and kwargs.single_prev
                func = posargs and posargs.single_prev
                if func.opcode == posargs.opcode == kwargs.opcode == -4:
                    assert isinstance(kwargs.argval, ast.Dict)
                    assert isinstance(posargs.argval, ast.Tuple)
                    keywords = []
                    for k, v in zip(kwargs.argval.keys, kwargs.argval.values):
                        if k is not None:  # if k is None then it's a starred (unpacked) argument
                            assert isinstance(k, ast.Constant) and isinstance(k.value, str)
                            k = k.value
                        keywords.append(ast.keyword(k, v))
                    ast_call = ast.Call(func=func.argval, args=posargs.argval.elts, keywords=keywords)
                    func.argval = ast_call
                    merge_nodes(self.graph, func, instr)
                    self.graph.remove_nodes_from([posargs, kwargs, instr])
                    return True
            else:
                raise NotImplementedError('CALL_FUNCTION_EX with flags lowest bit not set')

        return False

    def _unpack_seq_ex(self, instr: InstructionNode):
        """
        seq(*LOAD_AST_EXPR) -> instr(UNPACK_EX) -> ...targets(*STORE_VARIABLE)
        we transform into:
        seq(*LOAD_AST_EXPR) -> instr(*STORE_VARIABLE)
        """
        if instr.opname not in {'UNPACK_EX', 'UNPACK_SEQUENCE'}:
            return False
        if instr.opname == 'UNPACK_EX':
            before = instr.argval & 255
            after = (instr.argval >> 8) & 255
        else:
            before = instr.argval
            after = -1
        # actually, we don't care about what precedes the UNPACK_EX
        # seq = instr.single_prev
        # if seq is None or seq.opcode != -4: return False
        num_targets = before + after + 1
        curr = instr
        targets = []  # a list of *STORE_VARIABLE pseudo opcodes
        for _ in range(num_targets):
            curr = curr.next
            if curr.opcode != -5:  # accept only *STORE_VARIABLEs
                return False
            targets.append(curr)
        if find_linear_path(self.graph, instr, curr) != [instr] + targets:
            return False

        if instr.opname == 'UNPACK_EX':  # before, x, after  ->  x is the starred one
            targets[before].argval = ast.Starred(targets[before].argval, ast.Store())
        targets_tuple = ast.Tuple(elts=[i.argval for i in targets], ctx=ast.Store())

        # Instead of generating an entire NamedExpr, we will emit a *STORE_VARIABLE
        # with this tuple as the targets, and let _named_expr handle everything else
        instr.opcode = -5
        instr.argval = targets_tuple
        merge_nodes(self.graph, instr, targets[-1])
        self.graph.remove_nodes_from(targets)
        return True

    def _named_expr(self, instr: InstructionNode):
        """
        node0(*LOAD_AST_EXPR) -> instr(DUP_TOP) -> node2(*STORE_VARIABLE)
        """
        if instr.opname == 'DUP_TOP':
            node0 = instr.single_prev
            node2 = instr.next
            if node0 and node2 and node0.opcode == -4 and node2.opcode == -5 and \
                    find_linear_path(self.graph, node0, node2) == [node0, instr, node2]:
                target = node2.argval

                node0.argval = ast.NamedExpr(target, node0.argval)
                merge_nodes(self.graph, node0, node2)
                self.graph.remove_nodes_from([instr, node2])
                return True
        return False

    def _for_loops(self, instr: InstructionNode):
        """
        iterable(*LOAD_AST_EXPR) -> instr(GET_ITER) -> for_node(FOR_ITER) -> assignment(*STORE_VARIABLE)
        We check for GET_ITER because FOR_ITER may have multiple predecessors (from continue statements)
        In case of an async for loop, it is:
        iterable(*LOAD_AST_EXPR) -> instr(GET_AITER) -> for_start(SETUP_FINALLY)
          -> GET_ANEXT -> load None -> YIELD_FROM -> POP_BLOCK -> assignment(*STORE_VARIABLE)
        The for_start points to: END_ASYNC_FOR -> [else_block]
        """
        if instr.opname == 'GET_ITER':
            iterable = instr.single_prev
            for_iter_node = instr.next
            assignment = for_iter_node.next
            if self.graph.in_degree(instr) == self.graph.in_degree(assignment) == 1 and \
                iterable.opcode == -4 and for_iter_node.opname == 'FOR_ITER' and assignment.opcode == -5:

                # connect iterable's predecessors to FOR_LOOP and drop iterable and instr
                for p in list(self.graph.predecessors(iterable)):
                    self.graph.add_edge(p, for_iter_node, edge_type=self.graph[p][iterable]['edge_type'])
                self.graph.remove_nodes_from([iterable, instr])

                for_iter_node.opcode = -6  # *FOR_LOOP_START
                for_iter_node.context = ForLoopContext(for_iter_node, assignment.argval, iterable.argval)

                # remove the assignment node, we have stored it already
                self.graph.add_edge(for_iter_node, assignment.next, edge_type='implicit')
                self.graph.remove_node(assignment)
                return True
        if instr.opname == 'GET_AITER':
            iterable = instr.single_prev
            for_start = instr.next
            assert for_start.opname == 'SETUP_FINALLY'
            n1 = for_start.next  # GET_ANEXT
            ln = n1.next  # load None
            yf = ln.next  # YIELD_FROM
            pb = yf.next  # POP_BLOCK
            assignment = pb.next
            eaf = for_start.target  # END_ASYNC_FOR
            if assignment.opcode == -5 and iterable.opcode == -4 and eaf.opname == 'END_ASYNC_FOR' and \
                    find_linear_path(self.graph, n1, assignment) == [n1, ln, yf, pb, assignment] \
                    and n1.opname == 'GET_ANEXT' and is_load_none(ln) and yf.opname == 'YIELD_FROM' and pb.opname == 'POP_BLOCK':
                for p in list(self.graph.predecessors(iterable)):
                    self.graph.add_edge(p, for_start, edge_type=self.graph[p][iterable]['edge_type'])
                self.graph.remove_nodes_from([iterable, instr])

                for_start.opcode = -6
                for_start.context = ForLoopContext(for_start, assignment.argval, iterable.argval, is_async=True)

                self.graph.add_edge(for_start, assignment.next, edge_type='implicit')
                self.graph.add_edge(for_start, eaf.next, edge_type='explicit')
                self.graph.remove_nodes_from([eaf, n1, ln, yf, pb, assignment])
                return True
        return False

    def _jinem(self, instr: InstructionNode):
        # return False
        if instr.opname == 'JUMP_IF_NOT_EXC_MATCH':
            exc = instr.single_prev
            dt = exc and exc.single_prev
            next4 = next_n_linear(instr, 4)
            if next4 and dt and dt.opname == 'DUP_TOP' and exc.opcode == -4:
                assert self.graph.in_degree(exc) == self.graph.in_degree(instr) == 1
                # logger.debug(f'\nnext3: {next3}')
                assert next4[0].opname == next4[2].opname == 'POP_TOP'
                if next4[1].opcode == -5:  # store variable
                    assert isinstance(next4[1].argval, ast.Name)
                    exc_var = next4[1].argval.id
                    # after next3[2] we have a new SETUP_FINALLY -> this handler's body -> POP_BLOCK -> POP_EXCEPT
                    # from the SETUP_FINALLY's target we get Load None -> Store to 'name', Del 'name'
                    # (name is the exception's variable name)
                    # this load store delete happens on both branches, but the explicit jump one has RERAISE at the end!
                elif next4[1].opname == 'POP_TOP':  # except Exception: without saving it as a variable
                    exc_var = None
                    # after next3[2] we have a basic block (except handler's body) and a POP_EXCEPT
                else:
                    raise RuntimeError(f'unexpected instruction after JUMP_IF_NOT_EXC_MATCH: {next4[1]}')

                # if the exception is saved to a variable, a pseudo-SETUP_FINALLY should follow:
                # https://github.com/python/cpython/blob/3.9/Python/compile.c#L3121
                if exc_var is not None:
                    assert next4[3].opname == 'SETUP_FINALLY'
                    next4[3].opcode = -9


                # as always, transform the first node
                dt.opcode = -13
                dt.err_variable = exc_var
                dt.argval = exc.argval
                # logger.info(f'\n{dt.argval = }\n{dt.err_variable = }')
                self.graph.add_edge(dt, instr.target, edge_type='explicit')
                merge_nodes(self.graph, dt, next4[2])
                self.graph.remove_nodes_from([exc, instr] + next4[:3])
                return True

        return False

    def _build_slice(self, instr: InstructionNode):
        if instr.opname == 'BUILD_SLICE':
            p1 = instr.nth_implicit_prev(instr.argval)
            nodes = find_linear_path(self.graph, p1, instr)
            if nodes and all(i.opcode == -4 for i in nodes[:-1]):
                args = [i.argval for i in nodes[:-1]]
                for i, el in enumerate(args):
                    if isinstance(el, ast.Constant) and el.value is None:
                        args[i] = None
                if instr.argval == 2:
                    args.append(None)
                ast_slice = ast.Slice(lower=args[0], upper=args[1], step=args[2])
                p1.argval = ast_slice
                p1.opcode = -4
                merge_nodes(self.graph, p1, instr)
                self.graph.remove_nodes_from(nodes[1:])
                return True
        return False

    def _formatted_value(self, instr: InstructionNode):
        _convmap = {str: 115, ascii: 97, repr: 114}
        if instr.opname == 'FORMAT_VALUE':
            fmt_func, has_fmt = instr.argval
            node1 = instr.nth_implicit_prev(1 + has_fmt)
            nodes = find_linear_path(self.graph, node1, instr)
            if nodes and all(i.opcode == -4 for i in nodes[:-1]):
                ast_fv = ast.FormattedValue(
                    value=nodes[0].argval,
                    conversion=_convmap.get(fmt_func, -1),
                    format_spec=nodes[1].argval if has_fmt else None
                )
                node1.argval = ast_fv
                node1.opcode = -14
                merge_nodes(self.graph, node1, instr)
                self.graph.remove_nodes_from(nodes[1:])
                return True
        return False

    def _build_string(self, instr: InstructionNode):
        if instr.opname == 'BUILD_STRING':
            num_elts = instr.argval
            node1 = instr.nth_implicit_prev(num_elts)
            nodes = find_linear_path(self.graph, node1, instr)
            if nodes and all(i.opcode in {-4, -14} for i in nodes[:-1]):
                for n in nodes:
                    if n.opcode == -4:
                        assert isinstance(n.argval, ast.Constant) and isinstance(n.argval.value, str)  # TODO: bytes, maybe?
                node1.argval = ast.JoinedStr(values=[i.argval for i in nodes[:-1]])
                node1.opcode = -4
                merge_nodes(self.graph, node1, instr)
                self.graph.remove_nodes_from(nodes[1:])
                return True
        return False

    def _load_attr(self, instr: InstructionNode):
        if instr.opname == 'LOAD_ATTR':
            target = instr.single_prev
            if target and target.opcode == -4 and self.graph.in_degree(instr) == 1:
                attr = ast.Attribute(value=target.argval, attr=instr.argval, ctx=ast.Load())
                target.argval = attr
                merge_nodes(self.graph, target, instr)
                self.graph.remove_node(instr)
                return True
        return False

    def _call_method(self, instr: InstructionNode):
        """
        n1(*LOAD_AST_EXPR) -> LOAD_METHOD -> [...arguments...] -> instr(CALL_METHOD)
        """
        if instr.opname == 'CALL_METHOD':
            n1 = instr.nth_implicit_prev(instr.argval + 2)
            nodes = find_linear_path(self.graph, n1, instr)
            if nodes and all(i.opcode == -4 for i in nodes[2:-1]):
                assert nodes[1].opname == 'LOAD_METHOD'
                method = ast.Attribute(value=nodes[0].argval, attr=nodes[1].argval, ctx=ast.Load())
                args = [i.argval for i in nodes[2:-1]]
                n1.argval = ast.Call(func=method, args=args, keywords=[])
                merge_nodes(self.graph, n1, instr)
                self.graph.remove_nodes_from(nodes[1:])
                return True
        return False