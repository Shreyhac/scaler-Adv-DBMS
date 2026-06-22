"""Hand-written tokenizer for the MiniDB SQL subset."""

KEYWORDS = {
    "CREATE", "TABLE", "PRIMARY", "KEY", "INT", "TEXT",
    "INSERT", "INTO", "VALUES",
    "SELECT", "FROM", "WHERE", "JOIN", "INNER", "ON",
    "GROUP", "BY", "ORDER", "ASC", "DESC", "LIMIT",
    "DELETE", "UPDATE", "SET",
    "AND", "OR",
    "BEGIN", "COMMIT", "ROLLBACK", "TRANSACTION",
    "COUNT", "SUM", "MIN", "MAX", "AVG", "AS",
}

# Multi-char operators must be tried before single-char ones.
_TWO_CHAR_OPS = {"<=", ">=", "!=", "<>"}
_ONE_CHAR_OPS = set("=<>(),.*;")


class Token:
    __slots__ = ("type", "value")

    def __init__(self, type_, value):
        self.type = type_      # KEYWORD, IDENT, INT, STRING, OP, EOF
        self.value = value

    def __repr__(self):
        return f"Token({self.type},{self.value!r})"


class LexError(Exception):
    pass


def tokenize(sql):
    tokens = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c.isspace():
            i += 1
            continue
        # string literal '...'
        if c == "'":
            j = i + 1
            buf = []
            while j < n and sql[j] != "'":
                buf.append(sql[j])
                j += 1
            if j >= n:
                raise LexError("unterminated string literal")
            tokens.append(Token("STRING", "".join(buf)))
            i = j + 1
            continue
        # number (integers only). A leading '-' is part of the number only when
        # we're not right after a value (so "a-1" wouldn't be misread; we don't
        # support arithmetic anyway, but this keeps literals like (-5) working).
        prev = tokens[-1] if tokens else None
        after_value = prev is not None and (
            prev.type in ("INT", "STRING", "IDENT")
            or (prev.type == "OP" and prev.value == ")"))
        if c.isdigit() or (c == "-" and i + 1 < n and sql[i + 1].isdigit()
                           and not after_value):
            j = i + 1 if c == "-" else i
            while j < n and sql[j].isdigit():
                j += 1
            tokens.append(Token("INT", int(sql[i:j])))
            i = j
            continue
        # identifier / keyword
        if c.isalpha() or c == "_":
            j = i
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            word = sql[i:j]
            up = word.upper()
            if up in KEYWORDS:
                tokens.append(Token("KEYWORD", up))
            else:
                tokens.append(Token("IDENT", word))
            i = j
            continue
        # operators
        two = sql[i:i + 2]
        if two in _TWO_CHAR_OPS:
            tokens.append(Token("OP", "!=" if two == "<>" else two))
            i += 2
            continue
        if c in _ONE_CHAR_OPS:
            tokens.append(Token("OP", c))
            i += 1
            continue
        raise LexError(f"unexpected character {c!r} at position {i}")
    tokens.append(Token("EOF", None))
    return tokens
