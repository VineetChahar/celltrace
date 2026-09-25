#pragma once
#include <cstdint>
#include <string>

#include "celltrace/json.hpp"

namespace celltrace {

enum class Layer : uint8_t { RRC, NAS, PHY, UNKNOWN };

Layer layer_from_string(const std::string& s);
const char* layer_to_string(Layer l);

// A parsed log line, fully structured -- `fields` is a real parsed json::Value
// tree (built by the C++ parser), not a string Python would have to re-parse.
struct ParsedMessage {
    double ts = 0.0;
    std::string session_id;
    std::string ue_pseudo;
    Layer layer = Layer::UNKNOWN;
    std::string msg_type;
    std::string direction;
    std::string cell_id;
    json::Value fields;    // message-specific IE payload, already structured
    std::string raw_line;  // the exact original line, for citation grounding

    ParsedMessage() = default;
    ParsedMessage(ParsedMessage&&) = default;
    ParsedMessage& operator=(ParsedMessage&&) = default;
    ParsedMessage(const ParsedMessage&) = delete;
    ParsedMessage& operator=(const ParsedMessage&) = delete;

    bool operator<(const ParsedMessage& other) const { return ts < other.ts; }
};

}  // namespace celltrace
