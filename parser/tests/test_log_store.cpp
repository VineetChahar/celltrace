#include <catch2/catch_test_macros.hpp>

#include "celltrace/log_store.hpp"

using namespace celltrace;

static std::string line(double ts, std::string session, std::string ue, std::string layer, std::string mt) {
    return "{\"ts\": " + std::to_string(ts) + ", \"session_id\": \"" + session + "\", \"ue_pseudo\": \"" + ue +
           "\", \"layer\": \"" + layer + "\", \"msg_type\": \"" + mt +
           "\", \"direction\": \"N/A\", \"cell_id\": \"cell_00\", \"fields\": {}}";
}

TEST_CASE("ingest_lines counts malformed lines without aborting", "[log_store][malformed]") {
    LogStore store;
    std::vector<std::string> lines = {
        line(5.0, "s1", "ue_1", "RRC", "A"),
        "not json",
        line(1.0, "s1", "ue_1", "RRC", "B"),   // out of order relative to previous line
        "{\"ts\": 2.0, \"session_id\": \"s1\"",  // truncated
        line(3.0, "s2", "ue_2", "RRC", "C"),
    };
    auto stats = store.ingest_lines(Layer::RRC, lines);
    REQUIRE(stats.lines_read == 5);
    REQUIRE(stats.parsed_ok == 3);
    REQUIRE(stats.parse_errors == 2);
    REQUIRE(store.size(Layer::RRC) == 3);
}

TEST_CASE("query returns results sorted by ts despite out-of-order ingestion", "[log_store]") {
    LogStore store;
    std::vector<std::string> lines = {
        line(9.0, "s1", "ue_1", "RRC", "third"),
        line(1.0, "s1", "ue_1", "RRC", "first"),
        line(5.0, "s1", "ue_1", "RRC", "second"),
        line(100.0, "s1", "ue_1", "RRC", "outside_window"),
    };
    store.ingest_lines(Layer::RRC, lines);
    auto results = store.query(Layer::RRC, "s1", 0.0, 10.0);
    REQUIRE(results.size() == 3);
    REQUIRE(results[0]->msg_type == "first");
    REQUIRE(results[1]->msg_type == "second");
    REQUIRE(results[2]->msg_type == "third");
}

TEST_CASE("query filters by session_id", "[log_store]") {
    LogStore store;
    std::vector<std::string> lines = {
        line(1.0, "sA", "ue_1", "RRC", "a1"),
        line(2.0, "sB", "ue_2", "RRC", "b1"),
        line(3.0, "sA", "ue_1", "RRC", "a2"),
    };
    store.ingest_lines(Layer::RRC, lines);
    auto results = store.query(Layer::RRC, "sA", 0.0, 100.0);
    REQUIRE(results.size() == 2);
    REQUIRE(results[0]->msg_type == "a1");
    REQUIRE(results[1]->msg_type == "a2");
}

TEST_CASE("query on empty store returns empty", "[log_store]") {
    LogStore store;
    REQUIRE(store.query(Layer::PHY, "nope", 0.0, 1.0).empty());
}

#ifdef CELLTRACE_FIXTURES_DIR
TEST_CASE("ingest_files handles a real mixed-quality file via the threaded pipeline", "[log_store][integration]") {
    LogStore store;
    std::string fixtures = CELLTRACE_FIXTURES_DIR;
    auto stats = store.ingest_files(fixtures + "/mixed_rrc.jsonl", fixtures + "/empty.jsonl", fixtures + "/empty.jsonl");
    REQUIRE(stats.lines_read == 6);
    REQUIRE(stats.parsed_ok == 4);
    REQUIRE(stats.parse_errors == 2);
    auto results = store.query(Layer::RRC, "sA", 0.0, 100.0);
    REQUIRE(results.size() == 3);
    REQUIRE(results[0]->ts == 1.0);
    REQUIRE(results[1]->ts == 4.0);
    REQUIRE(results[2]->ts == 5.0);
}
#endif
