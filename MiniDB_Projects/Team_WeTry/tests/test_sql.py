"""End-to-end SQL tests: DDL, DML, projection, joins, aggregation, optimizer."""


def test_crud(tmpdb):
    db = tmpdb()
    db.execute("CREATE TABLE users (id INT PRIMARY KEY, name TEXT, age INT)")
    db.execute("INSERT INTO users VALUES (1,'alice',30),(2,'bob',25),(3,'carol',35)")

    r = db.execute("SELECT id, name FROM users WHERE age > 26 ORDER BY id")
    assert r.rows == [(1, "alice"), (3, "carol")]

    db.execute("UPDATE users SET age = 26 WHERE id = 2")
    r = db.execute("SELECT age FROM users WHERE id = 2")
    assert r.rows == [(26,)]

    db.execute("DELETE FROM users WHERE id = 3")
    r = db.execute("SELECT COUNT(*) FROM users")
    assert r.rows == [(2,)]
    db.close()


def test_insert_with_columns_and_star(tmpdb):
    db = tmpdb()
    db.execute("CREATE TABLE t (a INT PRIMARY KEY, b TEXT, c INT)")
    db.execute("INSERT INTO t (c, a, b) VALUES (9, 1, 'x')")
    r = db.execute("SELECT * FROM t")
    assert r.columns == ["a", "b", "c"]
    assert r.rows == [(1, "x", 9)]
    db.close()


def test_join(tmpdb):
    db = tmpdb()
    db.execute("CREATE TABLE users (id INT PRIMARY KEY, name TEXT)")
    db.execute("CREATE TABLE orders (oid INT PRIMARY KEY, uid INT, amount INT)")
    db.execute("INSERT INTO users VALUES (1,'alice'),(2,'bob')")
    db.execute("INSERT INTO orders VALUES (10,1,100),(11,1,250),(12,2,80)")
    r = db.execute(
        "SELECT u.name, o.amount FROM orders o JOIN users u ON u.id = o.uid "
        "WHERE o.amount > 90 ORDER BY o.amount DESC")
    assert r.rows == [("alice", 250), ("alice", 100)]
    db.close()


def test_aggregation_group_by(tmpdb):
    db = tmpdb()
    db.execute("CREATE TABLE sales (id INT PRIMARY KEY, region TEXT, amt INT)")
    db.execute("INSERT INTO sales VALUES (1,'E',100),(2,'E',200),(3,'W',50)")
    r = db.execute(
        "SELECT region, SUM(amt) AS total, COUNT(*) AS n "
        "FROM sales GROUP BY region ORDER BY region")
    assert r.columns == ["region", "total", "n"]
    assert r.rows == [("E", 300, 2), ("W", 50, 1)]
    db.close()


def test_null_handling(tmpdb):
    db = tmpdb()
    db.execute("CREATE TABLE u (id INT PRIMARY KEY, name TEXT, age INT)")
    db.execute("INSERT INTO u VALUES (1,'alice',31),(2,'bob',25)")
    db.execute("INSERT INTO u (id, name) VALUES (3, 'nita')")   # age omitted
    # NULL round-trips
    assert db.execute("SELECT age FROM u WHERE id=3").rows == [(None,)]
    # comparison with NULL is not true -> row excluded
    assert db.execute("SELECT name FROM u WHERE age > 0 ORDER BY age").rows \
        == [("bob",), ("alice",)]
    # ORDER BY is NULL-safe (NULLs first)
    assert db.execute("SELECT name FROM u ORDER BY age").rows \
        == [("nita",), ("bob",), ("alice",)]
    # aggregates skip NULL; COUNT(*) counts all rows
    assert db.execute("SELECT MIN(age), COUNT(age), COUNT(*) FROM u").rows \
        == [(25, 2, 3)]
    db.close()


def test_optimizer_picks_index(tmpdb):
    db = tmpdb()
    db.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
    db.execute("INSERT INTO t VALUES (1,1),(2,2),(3,3),(4,4),(5,5)")
    plan = db.explain("SELECT * FROM t WHERE id = 3")
    assert "IndexScan" in plan
    plan2 = db.explain("SELECT * FROM t WHERE v = 3")
    assert "SeqScan" in plan2     # no index on v
    db.close()
