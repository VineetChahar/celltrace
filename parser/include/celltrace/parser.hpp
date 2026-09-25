#pragma once
#include <stdexcept>
#include <string_view>

#include "celltrace/message.hpp"

namespace celltrace {

struct LineParseError : std::runtime_error {
    explicit LineParseError(const std::string& msg) : std::runtime_error(msg) {}
};

// Parses one log line (one JSON object) into a ParsedMessage. Throws
// LineParseError (missing/wrong-typed required envelope field) or
// json::ParseError (malformed/truncated JSON) -- callers ingesting a stream
// catch both and count the line as a parse failure rather than aborting.
ParsedMessage parse_line(std::string_view line);

}  // namespace celltrace
