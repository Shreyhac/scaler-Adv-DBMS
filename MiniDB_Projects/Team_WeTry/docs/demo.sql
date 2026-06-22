-- MiniDB demo script. Run interactively:
--   cd src && python -m minidb.cli /tmp/demo --mode mvcc
-- then paste these statements (one per line). Lines starting with \ are shell
-- commands, not SQL.

CREATE TABLE users (id INT PRIMARY KEY, name TEXT, age INT);
CREATE TABLE orders (oid INT PRIMARY KEY, uid INT, amount INT);

INSERT INTO users VALUES (1,'alice',30),(2,'bob',25),(3,'carol',35);
INSERT INTO orders VALUES (10,1,100),(11,1,250),(12,2,80),(13,3,400);

-- projection + filter + ordering
SELECT id, name, age FROM users WHERE age >= 30 ORDER BY age DESC;

-- aggregation with GROUP BY
SELECT uid, COUNT(*) AS n, SUM(amount) AS total FROM orders GROUP BY uid ORDER BY uid;

-- join (optimizer uses an index-nested-loop probing users.id)
SELECT u.name, o.amount FROM orders o JOIN users u ON u.id = o.uid WHERE o.amount > 90 ORDER BY o.amount DESC;

-- show the chosen plan + planner reasoning
\explain SELECT * FROM users WHERE id = 2
\explain SELECT * FROM users WHERE age = 30

-- explicit transaction
BEGIN;
UPDATE users SET age = 31 WHERE id = 1;
SELECT name, age FROM users WHERE id = 1;
COMMIT;

\tables
\quit
