"""Interactive REPL for MiniDB.

Usage:
    python -m minidb.cli [data_dir] [--mode mvcc|2pl]

Commands:
    <sql>;            run a SQL statement (CREATE/INSERT/SELECT/UPDATE/DELETE)
    BEGIN; COMMIT; ROLLBACK;   explicit transaction control
    \\explain <sql>    show the chosen query plan
    \\tables           list tables
    \\mode             show concurrency-control mode
    \\help             show this help
    \\quit             exit (flushes + checkpoints)
"""

import sys

from .engine import Engine, MiniDBError
from .txn.lock_manager import DeadlockError


HELP = __doc__


def _print_result(res):
    if res is None:
        return
    if res.columns is not None:
        widths = [len(c) for c in res.columns]
        for row in res.rows:
            for i, v in enumerate(row):
                widths[i] = max(widths[i], len(str(v)))
        header = " | ".join(c.ljust(widths[i]) for i, c in enumerate(res.columns))
        print(header)
        print("-+-".join("-" * w for w in widths))
        for row in res.rows:
            print(" | ".join(str(v).ljust(widths[i]) for i, v in enumerate(row)))
        print(f"({len(res.rows)} row{'s' if len(res.rows) != 1 else ''})")
    else:
        extra = f" {res.rowcount}" if res.rowcount is not None else ""
        print(f"{res.message}{extra}")


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    data_dir = "minidb_data"
    mode = "mvcc"
    i = 0
    while i < len(argv):
        if argv[i] == "--mode":
            mode = argv[i + 1]
            i += 2
        else:
            data_dir = argv[i]
            i += 1

    db = Engine(data_dir, mode=mode)
    session_txn = None
    print(f"MiniDB ready (dir={data_dir}, mode={mode}). Type \\help for help.")

    try:
        while True:
            prompt = "minidb*> " if session_txn else "minidb> "
            try:
                line = input(prompt)
            except EOFError:
                print()
                break
            line = line.strip()
            if not line or line.startswith("--"):  # blank or SQL line comment
                continue

            if line.startswith("\\"):
                cmd, _, rest = line[1:].partition(" ")
                if cmd in ("quit", "q", "exit"):
                    break
                elif cmd == "help":
                    print(HELP)
                elif cmd == "tables":
                    for name, ti in db.catalog.tables.items():
                        cols = ", ".join(f"{c.name} {c.type}" for c in ti.schema.columns)
                        pk = f" PK={ti.pk_column}" if ti.pk_column else ""
                        print(f"  {name}({cols}){pk}")
                elif cmd == "mode":
                    print(f"  concurrency control: {db.mode}")
                elif cmd == "explain":
                    try:
                        print(db.explain(rest, txn=session_txn))
                    except (MiniDBError, Exception) as e:
                        print(f"error: {e}")
                else:
                    print(f"unknown command \\{cmd}")
                continue

            # strip trailing semicolon for the parser-friendly path
            sql = line.rstrip(";").strip()
            up = sql.upper()
            try:
                if up == "BEGIN" or up == "BEGIN TRANSACTION":
                    session_txn = db.begin_transaction()
                    print("BEGIN")
                elif up == "COMMIT":
                    db.commit(session_txn)
                    session_txn = None
                    print("COMMIT")
                elif up == "ROLLBACK":
                    db.abort(session_txn)
                    session_txn = None
                    print("ROLLBACK")
                else:
                    res = db.execute(sql, txn=session_txn)
                    _print_result(res)
            except DeadlockError as e:
                print(f"aborted (deadlock): {e}")
                if session_txn:
                    db.abort(session_txn)
                    session_txn = None
            except Exception as e:
                print(f"error: {e}")
                # auto-commit statements already rolled themselves back.
    finally:
        if session_txn:
            db.abort(session_txn)
        db.close()
        print("bye.")


if __name__ == "__main__":
    main()
