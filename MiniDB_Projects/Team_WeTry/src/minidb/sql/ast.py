"""Abstract syntax tree node definitions for MiniDB SQL.

Kept deliberately small: a handful of statement nodes and a tiny expression
language (column refs, literals, comparison and boolean operators). This is
enough to exercise the full execution stack -- parser -> optimizer -> operators.
"""

from dataclasses import dataclass, field
from typing import Optional


# ---- expressions --------------------------------------------------------
@dataclass
class ColumnRef:
    name: str
    table: Optional[str] = None     # qualifier, e.g. t in t.id

    @property
    def qualified(self):
        return f"{self.table}.{self.name}" if self.table else self.name


@dataclass
class Literal:
    value: object                   # int or str
    type: str                       # "INT" or "TEXT"


@dataclass
class BinOp:
    op: str                         # = != < <= > >= AND OR
    left: object
    right: object


# ---- select-list items --------------------------------------------------
@dataclass
class Star:
    table: Optional[str] = None


@dataclass
class Aggregate:
    func: str                       # COUNT SUM MIN MAX AVG
    arg: object                     # ColumnRef or Star
    alias: Optional[str] = None


@dataclass
class SelectItem:
    expr: object                    # ColumnRef
    alias: Optional[str] = None


# ---- statements ---------------------------------------------------------
@dataclass
class CreateTable:
    name: str
    columns: list                   # list of (name, type)
    pk_column: Optional[str] = None


@dataclass
class Insert:
    table: str
    columns: Optional[list]         # explicit column list or None
    rows: list                      # list of list-of-Literal


@dataclass
class JoinClause:
    table: str
    on: object                      # BinOp
    alias: Optional[str] = None


@dataclass
class OrderBy:
    column: ColumnRef
    desc: bool = False


@dataclass
class Select:
    items: list                     # SelectItem / Star / Aggregate
    from_table: str
    from_alias: Optional[str] = None
    joins: list = field(default_factory=list)
    where: Optional[object] = None
    group_by: list = field(default_factory=list)   # list[ColumnRef]
    order_by: Optional[OrderBy] = None
    limit: Optional[int] = None


@dataclass
class Delete:
    table: str
    where: Optional[object] = None


@dataclass
class Update:
    table: str
    assignments: list               # list of (column_name, Literal)
    where: Optional[object] = None


@dataclass
class Begin:
    pass


@dataclass
class Commit:
    pass


@dataclass
class Rollback:
    pass
