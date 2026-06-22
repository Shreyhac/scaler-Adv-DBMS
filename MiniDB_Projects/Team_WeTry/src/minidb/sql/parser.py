"""Recursive-descent parser producing the AST in ast.py.

Grammar (informal):

    statement := create | insert | select | delete | update
               | BEGIN | COMMIT | ROLLBACK
    create    := CREATE TABLE name '(' coldef (',' coldef)* ')'
    coldef    := name (INT|TEXT) [PRIMARY KEY]
    insert    := INSERT INTO name ['(' col (',' col)* ')'] VALUES rowvals (',' rowvals)*
    select    := SELECT selectlist FROM name (join)* [WHERE expr]
                 [GROUP BY col (',' col)*] [ORDER BY col [ASC|DESC]] [LIMIT int]
    delete    := DELETE FROM name [WHERE expr]
    update    := UPDATE name SET assign (',' assign)* [WHERE expr]
    expr      := orexpr ; orexpr := andexpr (OR andexpr)*
    andexpr   := cmp (AND cmp)* ; cmp := operand op operand
    operand   := column | literal | '(' expr ')'
"""

from . import ast
from .lexer import tokenize


class ParseError(Exception):
    pass


class Parser:
    def __init__(self, sql):
        self.tokens = tokenize(sql)
        self.pos = 0

    # ---- token helpers --------------------------------------------------
    def _peek(self):
        return self.tokens[self.pos]

    def _next(self):
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def _at_keyword(self, kw):
        t = self._peek()
        return t.type == "KEYWORD" and t.value == kw

    def _at_op(self, op):
        t = self._peek()
        return t.type == "OP" and t.value == op

    def _expect_keyword(self, kw):
        t = self._next()
        if t.type != "KEYWORD" or t.value != kw:
            raise ParseError(f"expected {kw}, got {t.value!r}")

    def _expect_op(self, op):
        t = self._next()
        if t.type != "OP" or t.value != op:
            raise ParseError(f"expected {op!r}, got {t.value!r}")

    def _expect_ident(self):
        t = self._next()
        if t.type == "IDENT":
            return t.value
        raise ParseError(f"expected identifier, got {t.value!r}")

    # ---- entry point ----------------------------------------------------
    def parse(self):
        t = self._peek()
        if t.type != "KEYWORD":
            raise ParseError(f"unexpected token {t.value!r}")
        kw = t.value
        if kw == "CREATE":
            stmt = self._parse_create()
        elif kw == "INSERT":
            stmt = self._parse_insert()
        elif kw == "SELECT":
            stmt = self._parse_select()
        elif kw == "DELETE":
            stmt = self._parse_delete()
        elif kw == "UPDATE":
            stmt = self._parse_update()
        elif kw == "BEGIN":
            self._next()
            if self._at_keyword("TRANSACTION"):
                self._next()
            stmt = ast.Begin()
        elif kw == "COMMIT":
            self._next()
            stmt = ast.Commit()
        elif kw == "ROLLBACK":
            self._next()
            stmt = ast.Rollback()
        else:
            raise ParseError(f"unsupported statement: {kw}")
        # optional trailing semicolon
        if self._at_op(";"):
            self._next()
        if self._peek().type != "EOF":
            raise ParseError(f"trailing tokens after statement: {self._peek().value!r}")
        return stmt

    # ---- statements -----------------------------------------------------
    def _parse_create(self):
        self._expect_keyword("CREATE")
        self._expect_keyword("TABLE")
        name = self._expect_ident()
        self._expect_op("(")
        columns = []
        pk = None
        while True:
            col_name = self._expect_ident()
            tt = self._next()
            if tt.type != "KEYWORD" or tt.value not in ("INT", "TEXT"):
                raise ParseError(f"expected column type, got {tt.value!r}")
            col_type = tt.value
            columns.append((col_name, col_type))
            if self._at_keyword("PRIMARY"):
                self._next()
                self._expect_keyword("KEY")
                pk = col_name
            if self._at_op(","):
                self._next()
                continue
            break
        self._expect_op(")")
        return ast.CreateTable(name, columns, pk)

    def _parse_insert(self):
        self._expect_keyword("INSERT")
        self._expect_keyword("INTO")
        table = self._expect_ident()
        cols = None
        if self._at_op("("):
            self._next()
            cols = [self._expect_ident()]
            while self._at_op(","):
                self._next()
                cols.append(self._expect_ident())
            self._expect_op(")")
        self._expect_keyword("VALUES")
        rows = [self._parse_value_row()]
        while self._at_op(","):
            self._next()
            rows.append(self._parse_value_row())
        return ast.Insert(table, cols, rows)

    def _parse_value_row(self):
        self._expect_op("(")
        vals = [self._parse_literal()]
        while self._at_op(","):
            self._next()
            vals.append(self._parse_literal())
        self._expect_op(")")
        return vals

    def _parse_literal(self):
        t = self._next()
        if t.type == "INT":
            return ast.Literal(t.value, "INT")
        if t.type == "STRING":
            return ast.Literal(t.value, "TEXT")
        raise ParseError(f"expected literal, got {t.value!r}")

    def _parse_select(self):
        self._expect_keyword("SELECT")
        items = self._parse_select_list()
        self._expect_keyword("FROM")
        from_table = self._expect_ident()
        from_alias = self._maybe_table_alias()
        joins = []
        while self._at_keyword("JOIN") or self._at_keyword("INNER"):
            if self._at_keyword("INNER"):
                self._next()
            self._expect_keyword("JOIN")
            jtable = self._expect_ident()
            jalias = self._maybe_table_alias()
            self._expect_keyword("ON")
            on = self._parse_expr()
            joins.append(ast.JoinClause(jtable, on, jalias))
        where = None
        if self._at_keyword("WHERE"):
            self._next()
            where = self._parse_expr()
        group_by = []
        if self._at_keyword("GROUP"):
            self._next()
            self._expect_keyword("BY")
            group_by.append(self._parse_column_ref())
            while self._at_op(","):
                self._next()
                group_by.append(self._parse_column_ref())
        order_by = None
        if self._at_keyword("ORDER"):
            self._next()
            self._expect_keyword("BY")
            col = self._parse_column_ref()
            desc = False
            if self._at_keyword("ASC"):
                self._next()
            elif self._at_keyword("DESC"):
                self._next()
                desc = True
            order_by = ast.OrderBy(col, desc)
        limit = None
        if self._at_keyword("LIMIT"):
            self._next()
            t = self._next()
            if t.type != "INT":
                raise ParseError("LIMIT requires an integer")
            limit = t.value
        return ast.Select(items, from_table, from_alias, joins, where,
                          group_by, order_by, limit)

    def _parse_select_list(self):
        items = []
        while True:
            items.append(self._parse_select_item())
            if self._at_op(","):
                self._next()
                continue
            break
        return items

    _AGG = {"COUNT", "SUM", "MIN", "MAX", "AVG"}

    def _parse_select_item(self):
        t = self._peek()
        if t.type == "OP" and t.value == "*":
            self._next()
            return ast.Star()
        if t.type == "KEYWORD" and t.value in self._AGG:
            func = self._next().value
            self._expect_op("(")
            if self._at_op("*"):
                self._next()
                arg = ast.Star()
            else:
                arg = self._parse_column_ref()
            self._expect_op(")")
            alias = self._maybe_alias()
            return ast.Aggregate(func, arg, alias)
        col = self._parse_column_ref()
        alias = self._maybe_alias()
        return ast.SelectItem(col, alias)

    def _maybe_alias(self):
        if self._at_keyword("AS"):
            self._next()
            return self._expect_ident()
        return None

    def _maybe_table_alias(self):
        """An optional table alias: `FROM users u` or `FROM users AS u`."""
        if self._at_keyword("AS"):
            self._next()
            return self._expect_ident()
        if self._peek().type == "IDENT":
            return self._next().value
        return None

    def _parse_column_ref(self):
        # ident optionally qualified: t.col or col, and t.* for star handled above
        first = self._expect_ident()
        if self._at_op("."):
            self._next()
            if self._at_op("*"):
                self._next()
                return ast.Star(table=first)
            name = self._expect_ident()
            return ast.ColumnRef(name, table=first)
        return ast.ColumnRef(first)

    def _parse_delete(self):
        self._expect_keyword("DELETE")
        self._expect_keyword("FROM")
        table = self._expect_ident()
        where = None
        if self._at_keyword("WHERE"):
            self._next()
            where = self._parse_expr()
        return ast.Delete(table, where)

    def _parse_update(self):
        self._expect_keyword("UPDATE")
        table = self._expect_ident()
        self._expect_keyword("SET")
        assignments = []
        while True:
            col = self._expect_ident()
            self._expect_op("=")
            val = self._parse_literal()
            assignments.append((col, val))
            if self._at_op(","):
                self._next()
                continue
            break
        where = None
        if self._at_keyword("WHERE"):
            self._next()
            where = self._parse_expr()
        return ast.Update(table, assignments, where)

    # ---- expressions ----------------------------------------------------
    def _parse_expr(self):
        return self._parse_or()

    def _parse_or(self):
        left = self._parse_and()
        while self._at_keyword("OR"):
            self._next()
            right = self._parse_and()
            left = ast.BinOp("OR", left, right)
        return left

    def _parse_and(self):
        left = self._parse_cmp()
        while self._at_keyword("AND"):
            self._next()
            right = self._parse_cmp()
            left = ast.BinOp("AND", left, right)
        return left

    _CMP_OPS = {"=", "!=", "<", "<=", ">", ">="}

    def _parse_cmp(self):
        if self._at_op("("):
            self._next()
            e = self._parse_expr()
            self._expect_op(")")
            return e
        left = self._parse_operand()
        t = self._peek()
        if t.type == "OP" and t.value in self._CMP_OPS:
            op = self._next().value
            right = self._parse_operand()
            return ast.BinOp(op, left, right)
        raise ParseError(f"expected comparison operator, got {t.value!r}")

    def _parse_operand(self):
        t = self._peek()
        if t.type in ("INT", "STRING"):
            return self._parse_literal()
        if t.type == "IDENT":
            return self._parse_column_ref()
        raise ParseError(f"expected column or literal, got {t.value!r}")


def parse(sql):
    return Parser(sql).parse()
