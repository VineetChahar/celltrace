#include "celltrace/json.hpp"

#include <cmath>
#include <cstdlib>

namespace celltrace::json {

namespace {

class Parser {
public:
    explicit Parser(std::string_view text) : s_(text), i_(0) {}

    Value parse_value() {
        skip_ws();
        if (i_ >= s_.size()) throw ParseError("unexpected end of input", i_);
        char c = s_[i_];
        if (c == '{') return parse_object();
        if (c == '[') return parse_array();
        if (c == '"') return Value(parse_string());
        if (c == 't' || c == 'f') return parse_bool();
        if (c == 'n') return parse_null();
        if (c == '-' || (c >= '0' && c <= '9')) return parse_number();
        throw ParseError(std::string("unexpected character '") + c + "'", i_);
    }

    void check_fully_consumed() {
        skip_ws();
        if (i_ != s_.size()) throw ParseError("trailing content after JSON value", i_);
    }

private:
    std::string_view s_;
    size_t i_;

    void skip_ws() {
        while (i_ < s_.size() && (s_[i_] == ' ' || s_[i_] == '\t' || s_[i_] == '\n' || s_[i_] == '\r')) ++i_;
    }

    char peek() const {
        if (i_ >= s_.size()) throw ParseError("unexpected end of input", i_);
        return s_[i_];
    }

    void expect(char c) {
        if (i_ >= s_.size()) throw ParseError(std::string("expected '") + c + "' but hit end of input", i_);
        if (s_[i_] != c) throw ParseError(std::string("expected '") + c + "' but got '" + s_[i_] + "'", i_);
        ++i_;
    }

    Value parse_object() {
        expect('{');
        Object obj;
        skip_ws();
        if (i_ < s_.size() && s_[i_] == '}') { ++i_; return Value(std::move(obj)); }
        while (true) {
            skip_ws();
            std::string key = parse_string();
            skip_ws();
            expect(':');
            Value v = parse_value();
            obj.emplace_back(std::move(key), std::move(v));
            skip_ws();
            if (i_ >= s_.size()) throw ParseError("truncated object", i_);
            if (s_[i_] == ',') { ++i_; continue; }
            if (s_[i_] == '}') { ++i_; break; }
            throw ParseError("expected ',' or '}' in object", i_);
        }
        return Value(std::move(obj));
    }

    Value parse_array() {
        expect('[');
        Array arr;
        skip_ws();
        if (i_ < s_.size() && s_[i_] == ']') { ++i_; return Value(std::move(arr)); }
        while (true) {
            arr.push_back(parse_value());
            skip_ws();
            if (i_ >= s_.size()) throw ParseError("truncated array", i_);
            if (s_[i_] == ',') { ++i_; continue; }
            if (s_[i_] == ']') { ++i_; break; }
            throw ParseError("expected ',' or ']' in array", i_);
        }
        return Value(std::move(arr));
    }

    std::string parse_string() {
        skip_ws();
        expect('"');
        std::string out;
        while (true) {
            if (i_ >= s_.size()) throw ParseError("truncated string", i_);
            char c = s_[i_++];
            if (c == '"') break;
            if (c == '\\') {
                if (i_ >= s_.size()) throw ParseError("truncated escape sequence", i_);
                char e = s_[i_++];
                switch (e) {
                    case '"': out.push_back('"'); break;
                    case '\\': out.push_back('\\'); break;
                    case '/': out.push_back('/'); break;
                    case 'b': out.push_back('\b'); break;
                    case 'f': out.push_back('\f'); break;
                    case 'n': out.push_back('\n'); break;
                    case 'r': out.push_back('\r'); break;
                    case 't': out.push_back('\t'); break;
                    case 'u': {
                        if (i_ + 4 > s_.size()) throw ParseError("truncated \\u escape", i_);
                        // Codepoint value isn't needed for this project's fields (all
                        // ASCII); we validate the 4 hex digits and re-emit the escape
                        // verbatim rather than doing full UTF-16 surrogate handling.
                        for (int k = 0; k < 4; ++k) {
                            char h = s_[i_ + k];
                            bool hex = (h >= '0' && h <= '9') || (h >= 'a' && h <= 'f') || (h >= 'A' && h <= 'F');
                            if (!hex) throw ParseError("invalid \\u escape", i_ + k);
                        }
                        out += "\\u";
                        out += s_.substr(i_, 4);
                        i_ += 4;
                        break;
                    }
                    default:
                        throw ParseError(std::string("invalid escape character '") + e + "'", i_ - 1);
                }
            } else if (static_cast<unsigned char>(c) < 0x20) {
                throw ParseError("unescaped control character in string", i_ - 1);
            } else {
                out.push_back(c);
            }
        }
        return out;
    }

    Value parse_bool() {
        if (s_.substr(i_, 4) == "true") { i_ += 4; return Value(true); }
        if (s_.substr(i_, 5) == "false") { i_ += 5; return Value(false); }
        throw ParseError("invalid literal (expected true/false)", i_);
    }

    Value parse_null() {
        if (s_.substr(i_, 4) == "null") { i_ += 4; return Value(nullptr); }
        throw ParseError("invalid literal (expected null)", i_);
    }

    Value parse_number() {
        size_t start = i_;
        if (i_ < s_.size() && s_[i_] == '-') ++i_;
        if (i_ >= s_.size() || !std::isdigit(static_cast<unsigned char>(s_[i_])))
            throw ParseError("invalid number", i_);
        while (i_ < s_.size() && std::isdigit(static_cast<unsigned char>(s_[i_]))) ++i_;
        if (i_ < s_.size() && s_[i_] == '.') {
            ++i_;
            if (i_ >= s_.size() || !std::isdigit(static_cast<unsigned char>(s_[i_])))
                throw ParseError("invalid number (bad fraction)", i_);
            while (i_ < s_.size() && std::isdigit(static_cast<unsigned char>(s_[i_]))) ++i_;
        }
        if (i_ < s_.size() && (s_[i_] == 'e' || s_[i_] == 'E')) {
            ++i_;
            if (i_ < s_.size() && (s_[i_] == '+' || s_[i_] == '-')) ++i_;
            if (i_ >= s_.size() || !std::isdigit(static_cast<unsigned char>(s_[i_])))
                throw ParseError("invalid number (bad exponent)", i_);
            while (i_ < s_.size() && std::isdigit(static_cast<unsigned char>(s_[i_]))) ++i_;
        }
        std::string tok(s_.substr(start, i_ - start));
        return Value(std::strtod(tok.c_str(), nullptr));
    }
};

}  // namespace

Value parse(std::string_view text) {
    Parser p(text);
    Value v = p.parse_value();
    p.check_fully_consumed();
    return v;
}

}  // namespace celltrace::json
