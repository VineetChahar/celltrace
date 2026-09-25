#pragma once
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <variant>
#include <vector>

// A small hand-written recursive-descent JSON parser. We don't pull in a
// third-party JSON library because part of this project's point is to do the
// actual struct-building parse work in C++ -- and our log format's JSON shape
// (flat envelope + one nested `fields` object, at most one level of array-of-
// objects inside it) doesn't need a general-purpose library to handle well.
namespace celltrace::json {

class Value;
using Object = std::vector<std::pair<std::string, Value>>;
using Array = std::vector<Value>;

struct ParseError : std::runtime_error {
    size_t offset;
    ParseError(const std::string& msg, size_t off)
        : std::runtime_error(msg + " (at offset " + std::to_string(off) + ")"), offset(off) {}
};

class Value {
public:
    enum class Type { Null, Bool, Number, String, Object, Array };

    Value() : type_(Type::Null) {}
    Value(std::nullptr_t) : type_(Type::Null) {}
    explicit Value(bool b) : type_(Type::Bool), data_(b) {}
    explicit Value(double d) : type_(Type::Number), data_(d) {}
    explicit Value(std::string s) : type_(Type::String), data_(std::move(s)) {}
    explicit Value(Object o) : type_(Type::Object), data_(std::make_unique<Object>(std::move(o))) {}
    explicit Value(Array a) : type_(Type::Array), data_(std::make_unique<Array>(std::move(a))) {}

    Value(Value&&) = default;
    Value& operator=(Value&&) = default;
    Value(const Value&) = delete;
    Value& operator=(const Value&) = delete;

    Type type() const { return type_; }
    bool is_null() const { return type_ == Type::Null; }

    bool as_bool() const { return std::get<bool>(data_); }
    double as_number() const { return std::get<double>(data_); }
    const std::string& as_string() const { return std::get<std::string>(data_); }
    const Object& as_object() const { return *std::get<std::unique_ptr<Object>>(data_); }
    const Array& as_array() const { return *std::get<std::unique_ptr<Array>>(data_); }

    // Object lookup helper; returns nullptr if absent or not an Object.
    const Value* find(const std::string& key) const {
        if (type_ != Type::Object) return nullptr;
        for (const auto& [k, v] : as_object()) {
            if (k == key) return &v;
        }
        return nullptr;
    }

    // Moves a key's value out of this Object, leaving the source slot moved-from.
    // Throws std::runtime_error if this isn't an Object or the key is absent.
    Value take(const std::string& key) {
        if (type_ != Type::Object) throw std::runtime_error("take() called on a non-object Value");
        for (auto& [k, v] : *std::get<std::unique_ptr<Object>>(data_)) {
            if (k == key) return std::move(v);
        }
        throw std::runtime_error("take(): key not found: " + key);
    }

private:
    Type type_;
    std::variant<std::monostate, bool, double, std::string,
                 std::unique_ptr<Object>, std::unique_ptr<Array>>
        data_;
};

// Parses a single JSON value from `text`. Throws ParseError on malformed or
// truncated input, or if non-whitespace content trails the value (a log line
// with garbage appended is exactly the kind of "malformed" this project's
// unit tests care about, so we don't silently ignore it).
Value parse(std::string_view text);

}  // namespace celltrace::json
