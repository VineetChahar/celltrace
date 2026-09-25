#include "celltrace/parser.hpp"

#include "celltrace/json.hpp"

namespace celltrace {

Layer layer_from_string(const std::string& s) {
    if (s == "RRC") return Layer::RRC;
    if (s == "NAS") return Layer::NAS;
    if (s == "PHY") return Layer::PHY;
    return Layer::UNKNOWN;
}

const char* layer_to_string(Layer l) {
    switch (l) {
        case Layer::RRC: return "RRC";
        case Layer::NAS: return "NAS";
        case Layer::PHY: return "PHY";
        default: return "UNKNOWN";
    }
}

namespace {

const json::Value& require(const json::Value& root, const char* key) {
    const json::Value* v = root.find(key);
    if (v == nullptr) throw LineParseError(std::string("missing required field '") + key + "'");
    return *v;
}

double require_number(const json::Value& root, const char* key) {
    const json::Value& v = require(root, key);
    if (v.type() != json::Value::Type::Number)
        throw LineParseError(std::string("field '") + key + "' is not a number");
    return v.as_number();
}

std::string require_string(const json::Value& root, const char* key) {
    const json::Value& v = require(root, key);
    if (v.type() != json::Value::Type::String)
        throw LineParseError(std::string("field '") + key + "' is not a string");
    return v.as_string();
}

}  // namespace

ParsedMessage parse_line(std::string_view line) {
    json::Value root = json::parse(line);
    if (root.type() != json::Value::Type::Object)
        throw LineParseError("top-level JSON value is not an object");

    ParsedMessage msg;
    msg.ts = require_number(root, "ts");
    msg.session_id = require_string(root, "session_id");
    msg.ue_pseudo = require_string(root, "ue_pseudo");
    msg.layer = layer_from_string(require_string(root, "layer"));
    if (msg.layer == Layer::UNKNOWN) throw LineParseError("unrecognized layer value");
    msg.msg_type = require_string(root, "msg_type");
    msg.direction = require_string(root, "direction");
    msg.cell_id = require_string(root, "cell_id");

    const json::Value* fields = root.find("fields");
    if (fields == nullptr) throw LineParseError("missing required field 'fields'");
    if (fields->type() != json::Value::Type::Object)
        throw LineParseError("field 'fields' is not an object");
    msg.fields = root.take("fields");

    msg.raw_line = std::string(line);
    return msg;
}

}  // namespace celltrace
