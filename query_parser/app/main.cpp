// Name: Shreyansh Arora
// Roll No: 24BCS10252
// Lab 5: SQL Query Parser

#include <iostream>
#include <string>
#include <vector>
#include <cctype>
#include <stdexcept>
#include "types.h"
#include "lexer.h"
#include "expressions.h"
#include "select_stat.h"
using namespace std;


class DbParser {
public:
    explicit DbParser(vector<Token> toks) : tokens(move(toks)) {}

    SelectStatement parseSelect() {
        consume(TokenType::SELECT);
        string column = consume(TokenType::IDENTIFIER).text;
        consume(TokenType::FROM);
        string table = consume(TokenType::IDENTIFIER).text;
        consume(TokenType::WHERE);
        auto where = parseExpression();

        SelectStatement stmt;
        stmt.column    = column;
        stmt.tableName = table;
        stmt.whereFilter = where;
        return stmt;
    }

private:
    vector<Token> tokens;
    size_t pos = 0;

    Token& current() { return tokens[pos]; }

    Token consume(TokenType expected) {
        if (current().type != expected) throw runtime_error("Unexpected token: " + current().text);
        return tokens[pos++];
    }

    Expression* parseExpression() {
        auto left = parseAndExpr();
        while (current().type == TokenType::OR) {
            consume(TokenType::OR);
            auto right = parseAndExpr();
            left = new BinaryExpression("OR", left, right);
        }
        return left;
    }

    Expression* parseAndExpr() {
        auto left = parsePrimary();
        while (current().type == TokenType::AND) {
            consume(TokenType::AND);
            auto right = parsePrimary();
            left = new BinaryExpression("AND", left, right);
        }
        return left;
    }

    Expression* parsePrimary() {
        if (current().type == TokenType::LPAREN) {
            consume(TokenType::LPAREN);
            auto expr = parseExpression();
            consume(TokenType::RPAREN);
            return expr;
        }
        return parseCondition();
    }

    Expression* parseCondition() {
        string col  = consume(TokenType::IDENTIFIER).text;
        Expression* left = new ColumnRef(col);

        string op;
        switch (current().type) {
            case TokenType::GT:  op = ">";  consume(TokenType::GT);  break;
            case TokenType::LT:  op = "<";  consume(TokenType::LT);  break;
            case TokenType::GTE: op = ">="; consume(TokenType::GTE); break;
            case TokenType::LTE: op = "<="; consume(TokenType::LTE); break;
            case TokenType::EQ:  op = "=";  consume(TokenType::EQ);  break;
            case TokenType::NEQ: op = "!="; consume(TokenType::NEQ); break;
            default: throw runtime_error("Expected comparison operator");
        }

        int value = stoi(consume(TokenType::NUMBER).text);
        Expression* right = new Literal(value);
        return new BinaryExpression(op, left, right);
    }
};


struct Employee {
    string name;
    int id;
    int age;
    int salary;

    int getInt(const string& col) const {
        if (col == "id")     return id;
        if (col == "age")    return age;
        if (col == "salary") return salary;
        throw runtime_error("Unknown column: " + col);
    }

    string getString(const string& col) const {
        if (col == "name") return name;
        throw runtime_error("Unknown column: " + col);
    }
};


int evalInt(Expression* expr, const Employee& row) {
    if (auto* c = dynamic_cast<ColumnRef*>(expr)) return row.getInt(c->name);
    if (auto* l = dynamic_cast<Literal*>(expr))   return l->value;
    throw runtime_error("Cannot evaluate as int");
}

bool applyFilter(Expression* expr, const Employee& emp) {
    auto* bin = dynamic_cast<BinaryExpression*>(expr);
    if (!bin) throw runtime_error("Invalid expression");

    if (bin->op == "OR")  return applyFilter(bin->left, emp) || applyFilter(bin->right, emp);
    if (bin->op == "AND") return applyFilter(bin->left, emp) && applyFilter(bin->right, emp);

    int left  = evalInt(bin->left,  emp);
    int right = evalInt(bin->right, emp);
    if (bin->op == ">")  return left >  right;
    if (bin->op == "<")  return left <  right;
    if (bin->op == ">=") return left >= right;
    if (bin->op == "<=") return left <= right;
    if (bin->op == "=")  return left == right;
    if (bin->op == "!=") return left != right;
    throw runtime_error("Unknown operator: " + bin->op);
}


void execute(SelectStatement& stmt, vector<Employee>& employees) {
    cout << "Results for: " << stmt.column << " | table: " << stmt.tableName << "\n";
    for (const auto& emp : employees) {
        if (applyFilter(stmt.whereFilter, emp)) {
            if (stmt.column == "name")   cout << emp.name   << "\n";
            else if (stmt.column == "id")     cout << emp.id     << "\n";
            else if (stmt.column == "age")    cout << emp.age    << "\n";
            else if (stmt.column == "salary") cout << emp.salary << "\n";
        }
    }
}

void runQuery(const string& sql, vector<Employee>& employees) {
    cout << "\n[QUERY] " << sql << "\n";
    try {
        Lexer lexer(sql);
        auto tokens = lexer.tokenize();
        DbParser parser(tokens);
        SelectStatement stmt = parser.parseSelect();
        execute(stmt, employees);
    } catch (const exception& e) {
        cerr << "[ERROR] " << e.what() << "\n";
    }
}


int main() {
    vector<Employee> employees = {
        {"Kartik",   1, 20, 50000},
        {"Krishank", 2, 30, 80000},
        {"Sandip",   3, 15, 20000},
        {"Nitish",   4, 17, 25000},
        {"Kp",       5, 25, 60000},
    };

    runQuery("SELECT name FROM employees WHERE age < 18 OR id < 2", employees);
    runQuery("SELECT name FROM employees WHERE age >= 18 AND salary > 55000", employees);
    runQuery("SELECT name FROM employees WHERE (age < 18 OR age > 25) AND salary != 80000", employees);
    runQuery("SELECT salary FROM employees WHERE id = 3", employees);

    return 0;
}
