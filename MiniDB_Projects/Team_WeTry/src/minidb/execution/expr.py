"""Row representation and expression evaluation for the executor.

A row flowing through the operator pipeline is a plain dict keyed by the
*qualified* column name "alias.column" -> value. Using the alias (table name or
the AS-alias) as a prefix lets joins merge rows without name clashes and lets
predicates reference either `t.col` or a bare `col` (when unambiguous).
"""

from ..sql import ast


def resolve_column(row, ref):
    """Look up a ColumnRef in a row dict. Qualified refs match exactly; bare
    refs must match exactly one column across all sources."""
    if ref.table is not None:
        key = f"{ref.table}.{ref.name}"
        if key not in row:
            raise KeyError(f"unknown column {ref.qualified}")
        return row[key]
    matches = [k for k in row if k.split(".", 1)[1] == ref.name]
    if not matches:
        raise KeyError(f"unknown column {ref.name}")
    if len(matches) > 1:
        raise KeyError(f"ambiguous column {ref.name} (matches {matches})")
    return row[matches[0]]


def eval_expr(row, node):
    """Evaluate an expression node against a row, returning a python value
    (for operands) or a bool (for comparisons / boolean ops)."""
    if isinstance(node, ast.Literal):
        return node.value
    if isinstance(node, ast.ColumnRef):
        return resolve_column(row, node)
    if isinstance(node, ast.BinOp):
        op = node.op
        if op == "AND":
            return bool(eval_expr(row, node.left)) and bool(eval_expr(row, node.right))
        if op == "OR":
            return bool(eval_expr(row, node.left)) or bool(eval_expr(row, node.right))
        left = eval_expr(row, node.left)
        right = eval_expr(row, node.right)
        # SQL three-valued logic (simplified): any comparison involving NULL is
        # UNKNOWN, which a WHERE/ON predicate treats as "not true" -> False.
        if left is None or right is None:
            return False
        if op == "=":
            return left == right
        if op == "!=":
            return left != right
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
        raise ValueError(f"unknown operator {op}")
    raise TypeError(f"cannot evaluate node {node!r}")


def eval_predicate(row, node):
    """Evaluate a WHERE/ON predicate to a bool (None predicate == always true)."""
    if node is None:
        return True
    return bool(eval_expr(row, node))
