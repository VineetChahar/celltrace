#include <catch2/catch_test_macros.hpp>

#include "celltrace/json.hpp"

using namespace celltrace::json;

TEST_CASE("parses scalars", "[json]") {
    REQUIRE(parse("42").as_number() == 42.0);
    REQUIRE(parse("-3.5e2").as_number() == -350.0);
    REQUIRE(parse("\"hello\"").as_string() == "hello");
    REQUIRE(parse("true").as_bool() == true);
    REQUIRE(parse("false").as_bool() == false);
    REQUIRE(parse("null").is_null());
}

TEST_CASE("parses strings with escapes", "[json]") {
    REQUIRE(parse(R"("a\"b")").as_string() == "a\"b");
    REQUIRE(parse(R"("line1\nline2")").as_string() == "line1\nline2");
    REQUIRE(parse(R"("tab\there")").as_string() == "tab\there");
    REQUIRE(parse(R"("back\\slash")").as_string() == "back\\slash");
}

TEST_CASE("parses flat object", "[json]") {
    Value v = parse(R"({"a": 1, "b": "two", "c": true, "d": null})");
    REQUIRE(v.type() == Value::Type::Object);
    REQUIRE(v.find("a")->as_number() == 1.0);
    REQUIRE(v.find("b")->as_string() == "two");
    REQUIRE(v.find("c")->as_bool() == true);
    REQUIRE(v.find("d")->is_null());
    REQUIRE(v.find("missing") == nullptr);
}

TEST_CASE("parses nested object and array", "[json]") {
    Value v = parse(R"({"outer": {"inner": 5}, "arr": [1, 2, {"x": "y"}]})");
    REQUIRE(v.find("outer")->find("inner")->as_number() == 5.0);
    const auto& arr = v.find("arr")->as_array();
    REQUIRE(arr.size() == 3);
    REQUIRE(arr[0].as_number() == 1.0);
    REQUIRE(arr[2].find("x")->as_string() == "y");
}

TEST_CASE("empty object and array", "[json]") {
    REQUIRE(parse("{}").as_object().empty());
    REQUIRE(parse("[]").as_array().empty());
}

TEST_CASE("rejects truncated object", "[json][malformed]") {
    REQUIRE_THROWS_AS(parse(R"({"a": 1)"), ParseError);
}

TEST_CASE("rejects truncated string", "[json][malformed]") {
    REQUIRE_THROWS_AS(parse(R"("unterminated)"), ParseError);
}

TEST_CASE("rejects truncated array", "[json][malformed]") {
    REQUIRE_THROWS_AS(parse("[1, 2,"), ParseError);
}

TEST_CASE("rejects missing colon", "[json][malformed]") {
    REQUIRE_THROWS_AS(parse(R"({"a" 1})"), ParseError);
}

TEST_CASE("rejects trailing comma", "[json][malformed]") {
    REQUIRE_THROWS_AS(parse(R"({"a": 1,})"), ParseError);
}

TEST_CASE("rejects bad literal", "[json][malformed]") {
    REQUIRE_THROWS_AS(parse("tru"), ParseError);
    REQUIRE_THROWS_AS(parse("nul"), ParseError);
}

TEST_CASE("rejects malformed number", "[json][malformed]") {
    REQUIRE_THROWS_AS(parse("--5"), ParseError);
    REQUIRE_THROWS_AS(parse("1.2.3"), ParseError);
}

TEST_CASE("rejects unescaped control character", "[json][malformed]") {
    std::string bad = "\"a\nb\"";  // literal newline inside a string
    REQUIRE_THROWS_AS(parse(bad), ParseError);
}

TEST_CASE("rejects empty input", "[json][malformed]") {
    REQUIRE_THROWS_AS(parse(""), ParseError);
    REQUIRE_THROWS_AS(parse("   "), ParseError);
}

TEST_CASE("take moves value out of object", "[json]") {
    Value v = parse(R"({"fields": {"x": 1}, "other": 2})");
    Value fields = v.take("fields");
    REQUIRE(fields.find("x")->as_number() == 1.0);
    REQUIRE(v.find("other")->as_number() == 2.0);
}
