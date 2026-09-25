#include <catch2/catch_test_macros.hpp>

#include "celltrace/parser.hpp"

using namespace celltrace;

static const char* kValidRrc =
    R"({"ts": 12.5, "session_id": "sess_00001", "ue_pseudo": "ue_deadbeef", "layer": "RRC", )"
    R"("msg_type": "RRCSetupRequest", "direction": "UE->gNB", "cell_id": "cell_00", )"
    R"("fields": {"ue-Identity": "0000000001", "establishmentCause": "mo-Data"}})";

TEST_CASE("parses a valid RRC line", "[parser]") {
    ParsedMessage m = parse_line(kValidRrc);
    REQUIRE(m.ts == 12.5);
    REQUIRE(m.session_id == "sess_00001");
    REQUIRE(m.ue_pseudo == "ue_deadbeef");
    REQUIRE(m.layer == Layer::RRC);
    REQUIRE(m.msg_type == "RRCSetupRequest");
    REQUIRE(m.direction == "UE->gNB");
    REQUIRE(m.cell_id == "cell_00");
    REQUIRE(m.fields.find("establishmentCause")->as_string() == "mo-Data");
    REQUIRE(m.raw_line == kValidRrc);
}

TEST_CASE("parses a PHY line with nested-free fields", "[parser]") {
    const char* line =
        R"({"ts": 1.0, "session_id": "s1", "ue_pseudo": "ue_1", "layer": "PHY", )"
        R"("msg_type": "measurement", "direction": "N/A", "cell_id": "cell_00", )"
        R"("fields": {"rsrp_dbm": -95.5, "rsrq_db": -12.0, "sinr_db": 3.2}})";
    ParsedMessage m = parse_line(line);
    REQUIRE(m.layer == Layer::PHY);
    REQUIRE(m.fields.find("rsrp_dbm")->as_number() == -95.5);
}

TEST_CASE("parses fields with nested array of objects", "[parser]") {
    const char* line =
        R"({"ts": 2.0, "session_id": "s1", "ue_pseudo": "ue_1", "layer": "RRC", )"
        R"("msg_type": "MeasurementReport", "direction": "UE->gNB", "cell_id": "cell_00", )"
        R"("fields": {"measResultServingCell": {"rsrp": -90.0}, )"
        R"("measResultNeighCells": [{"physCellId": "cell_01", "rsrp": -84.0}]}})";
    ParsedMessage m = parse_line(line);
    const auto& neigh = m.fields.find("measResultNeighCells")->as_array();
    REQUIRE(neigh.size() == 1);
    REQUIRE(neigh[0].find("physCellId")->as_string() == "cell_01");
}

TEST_CASE("rejects missing required envelope field", "[parser][malformed]") {
    const char* line = R"({"session_id": "s1", "ue_pseudo": "ue_1", "layer": "RRC", )"
                        R"("msg_type": "X", "direction": "N/A", "cell_id": "c", "fields": {}})";
    REQUIRE_THROWS_AS(parse_line(line), LineParseError);
}

TEST_CASE("rejects wrong-typed field", "[parser][malformed]") {
    const char* line = R"({"ts": "not-a-number", "session_id": "s1", "ue_pseudo": "ue_1", )"
                        R"("layer": "RRC", "msg_type": "X", "direction": "N/A", "cell_id": "c", "fields": {}})";
    REQUIRE_THROWS_AS(parse_line(line), LineParseError);
}

TEST_CASE("rejects unrecognized layer", "[parser][malformed]") {
    const char* line = R"({"ts": 1.0, "session_id": "s1", "ue_pseudo": "ue_1", )"
                        R"("layer": "MYSTERY", "msg_type": "X", "direction": "N/A", "cell_id": "c", "fields": {}})";
    REQUIRE_THROWS_AS(parse_line(line), LineParseError);
}

TEST_CASE("rejects missing fields object", "[parser][malformed]") {
    const char* line = R"({"ts": 1.0, "session_id": "s1", "ue_pseudo": "ue_1", )"
                        R"("layer": "RRC", "msg_type": "X", "direction": "N/A", "cell_id": "c"})";
    REQUIRE_THROWS_AS(parse_line(line), LineParseError);
}

TEST_CASE("rejects truncated line (cut mid-stream write)", "[parser][malformed]") {
    std::string truncated(kValidRrc);
    truncated.resize(truncated.size() / 2);
    REQUIRE_THROWS(parse_line(truncated));
}

TEST_CASE("rejects empty line", "[parser][malformed]") {
    REQUIRE_THROWS(parse_line(""));
}

TEST_CASE("rejects non-object top level", "[parser][malformed]") {
    REQUIRE_THROWS_AS(parse_line("[1, 2, 3]"), LineParseError);
}
