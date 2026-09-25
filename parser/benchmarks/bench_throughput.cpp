// Sustained streaming-ingestion throughput benchmark.
//
// Generates a synthetic RRC/NAS/PHY log set at increasing sizes and measures
// messages/sec through the real ring-buffer -> parse -> store pipeline
// (celltrace::LogStore::ingest_files), the same code path the agent's
// LogStore uses at startup. Numbers are printed as a markdown table so they
// can be pasted straight into the README.
#include <chrono>
#include <cstdio>
#include <fstream>
#include <random>
#include <string>
#include <vector>

#include "celltrace/log_store.hpp"

using namespace celltrace;

static void write_synthetic_file(const std::string& path, const char* layer, int n) {
    std::ofstream out(path);
    std::mt19937 rng(42);
    std::uniform_real_distribution<double> jitter(0.0, 0.05);
    double ts = 0.0;
    for (int i = 0; i < n; ++i) {
        ts += jitter(rng);
        out << "{\"ts\": " << ts << ", \"session_id\": \"sess_" << (i % 500)
            << "\", \"ue_pseudo\": \"ue_" << (i % 500) << "\", \"layer\": \"" << layer
            << "\", \"msg_type\": \"BenchMsg\", \"direction\": \"UE->gNB\", \"cell_id\": \"cell_00\", "
            << "\"fields\": {\"a\": " << i << ", \"b\": \"value_" << i << "\", \"nested\": {\"x\": 1, \"y\": 2}}}\n";
    }
}

int main() {
    std::printf("| total messages | wall time (s) | messages/sec |\n");
    std::printf("|---:|---:|---:|\n");
    for (int n_per_layer : {50000, 200000, 500000}) {
        std::string rrc = "/tmp/celltrace_bench_rrc.jsonl";
        std::string nas = "/tmp/celltrace_bench_nas.jsonl";
        std::string phy = "/tmp/celltrace_bench_phy.jsonl";
        write_synthetic_file(rrc, "RRC", n_per_layer);
        write_synthetic_file(nas, "NAS", n_per_layer);
        write_synthetic_file(phy, "PHY", n_per_layer);

        LogStore store;
        auto stats = store.ingest_files(rrc, nas, phy);
        std::printf("| %zu | %.3f | %.0f |\n", stats.parsed_ok, stats.elapsed_s, stats.messages_per_sec());
    }
    return 0;
}
