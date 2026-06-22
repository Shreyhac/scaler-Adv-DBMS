"""Table schema definitions.

MiniDB supports two column types, which is enough to demonstrate typed storage,
indexing and predicates without drowning in serialization detail:

* INT  -> 64-bit signed integer
* TEXT -> variable-length UTF-8 string
"""

INT = "INT"
TEXT = "TEXT"
VALID_TYPES = (INT, TEXT)


class Column:
    def __init__(self, name, col_type):
        col_type = col_type.upper()
        if col_type not in VALID_TYPES:
            raise ValueError(f"unsupported column type: {col_type}")
        self.name = name
        self.type = col_type

    def to_dict(self):
        return {"name": self.name, "type": self.type}

    @staticmethod
    def from_dict(d):
        return Column(d["name"], d["type"])

    def __repr__(self):
        return f"Column({self.name} {self.type})"


class Schema:
    def __init__(self, columns):
        self.columns = list(columns)
        self._index = {c.name: i for i, c in enumerate(self.columns)}

    def index_of(self, name):
        if name not in self._index:
            raise KeyError(f"no such column: {name}")
        return self._index[name]

    def column(self, name):
        return self.columns[self.index_of(name)]

    @property
    def names(self):
        return [c.name for c in self.columns]

    def to_dict(self):
        return {"columns": [c.to_dict() for c in self.columns]}

    @staticmethod
    def from_dict(d):
        return Schema([Column.from_dict(c) for c in d["columns"]])

    def __repr__(self):
        return f"Schema({', '.join(f'{c.name}:{c.type}' for c in self.columns)})"
