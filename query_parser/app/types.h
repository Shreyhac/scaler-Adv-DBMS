
#pragma once
#include <string>
enum class TokenType {
    SELECT, FROM, WHERE, OR, AND, IDENTIFIER, NUMBER,
    GT, LT, GTE, LTE, EQ, NEQ, LPAREN, RPAREN, END
};
struct Token {
    TokenType type;
    std::string text;
};