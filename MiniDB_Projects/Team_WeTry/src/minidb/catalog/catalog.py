"""System catalog: table and index metadata.

For simplicity the catalog is persisted as a small JSON sidecar file next to the
database file (rather than in system tables inside the DB itself). This keeps
metadata human-readable and easy to reason about; storing the catalog in heap
pages is noted as future work. The data pages themselves remain the source of
truth for which pages belong to a table (via the page header's table_id), so a
stale catalog can never lose committed row data.
"""

import json
import os

from ..record.schema import Schema


class TableInfo:
    def __init__(self, table_id, name, schema, pk_column=None, indexes=None):
        self.table_id = table_id
        self.name = name
        self.schema = schema
        self.pk_column = pk_column          # name of primary-key column or None
        self.indexes = indexes or []        # column names with a secondary index

    def to_dict(self):
        return {
            "table_id": self.table_id,
            "name": self.name,
            "schema": self.schema.to_dict(),
            "pk_column": self.pk_column,
            "indexes": self.indexes,
        }

    @staticmethod
    def from_dict(d):
        return TableInfo(
            d["table_id"], d["name"], Schema.from_dict(d["schema"]),
            d.get("pk_column"), d.get("indexes", []))


class Catalog:
    def __init__(self, path):
        self.path = path
        self.tables = {}            # name -> TableInfo
        self._next_table_id = 1
        if os.path.exists(path):
            self._load()

    def _load(self):
        with open(self.path) as f:
            data = json.load(f)
        self._next_table_id = data.get("next_table_id", 1)
        for td in data.get("tables", []):
            ti = TableInfo.from_dict(td)
            self.tables[ti.name] = ti

    def save(self):
        data = {
            "next_table_id": self._next_table_id,
            "tables": [ti.to_dict() for ti in self.tables.values()],
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)      # atomic on POSIX

    def create_table(self, name, schema, pk_column=None):
        if name in self.tables:
            raise ValueError(f"table already exists: {name}")
        table_id = self._next_table_id
        self._next_table_id += 1
        ti = TableInfo(table_id, name, schema, pk_column)
        if pk_column is not None:
            ti.indexes.append(pk_column)
        self.tables[name] = ti
        self.save()
        return ti

    def get_table(self, name):
        if name not in self.tables:
            raise KeyError(f"no such table: {name}")
        return self.tables[name]

    def has_table(self, name):
        return name in self.tables
