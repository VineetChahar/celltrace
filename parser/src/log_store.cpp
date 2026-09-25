#include "celltrace/log_store.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <fstream>
#include <stdexcept>
#include <thread>

#include "celltrace/parser.hpp"
#include "celltrace/ring_buffer.hpp"

namespace celltrace {

std::vector<ParsedMessage>& LogStore::store_for(Layer l) {
    switch (l) {
        case Layer::RRC: return rrc_;
        case Layer::NAS: return nas_;
        case Layer::PHY: return phy_;
        default: throw std::invalid_argument("store_for: UNKNOWN layer");
    }
}

const std::vector<ParsedMessage>& LogStore::store_for(Layer l) const {
    return const_cast<LogStore*>(this)->store_for(l);
}

void LogStore::sort_all() {
    auto by_ts = [](const ParsedMessage& a, const ParsedMessage& b) { return a.ts < b.ts; };
    std::sort(rrc_.begin(), rrc_.end(), by_ts);
    std::sort(nas_.begin(), nas_.end(), by_ts);
    std::sort(phy_.begin(), phy_.end(), by_ts);
}

namespace {

// One producer (reads lines from `path`, pushes into `ring`) and one consumer
// (pops lines, parses, appends to `out`) running concurrently. Backpressure is
// a yield-spin on both sides -- appropriate here because both sides are CPU-
// bound and short-lived per item; a blocking condvar would add latency that
// doesn't buy anything at this queue depth (benchmarked, see LEARNING.md).
void run_pipeline(const std::string& path, std::vector<ParsedMessage>& out,
                   std::atomic<size_t>& lines_read, std::atomic<size_t>& parsed_ok,
                   std::atomic<size_t>& parse_errors) {
    constexpr size_t kRingCapacity = 4096;
    SpscRingBuffer<std::string> ring(kRingCapacity);
    std::atomic<bool> producer_done{false};

    std::thread producer([&] {
        std::ifstream in(path);
        std::string line;
        while (std::getline(in, line)) {
            lines_read.fetch_add(1, std::memory_order_relaxed);
            while (!ring.try_push(line)) {
                std::this_thread::yield();
            }
        }
        producer_done.store(true, std::memory_order_release);
    });

    while (true) {
        auto item = ring.try_pop();
        if (item.has_value()) {
            try {
                out.push_back(parse_line(*item));
                parsed_ok.fetch_add(1, std::memory_order_relaxed);
            } catch (const std::exception&) {
                parse_errors.fetch_add(1, std::memory_order_relaxed);
            }
        } else if (producer_done.load(std::memory_order_acquire)) {
            // One more drain pass: producer may have set the flag between our
            // try_pop() and its store, leaving items still in the ring.
            while (auto tail_item = ring.try_pop()) {
                try {
                    out.push_back(parse_line(*tail_item));
                    parsed_ok.fetch_add(1, std::memory_order_relaxed);
                } catch (const std::exception&) {
                    parse_errors.fetch_add(1, std::memory_order_relaxed);
                }
            }
            break;
        } else {
            std::this_thread::yield();
        }
    }
    producer.join();
}

}  // namespace

LogStore::IngestStats LogStore::ingest_files(const std::string& rrc_path, const std::string& nas_path,
                                              const std::string& phy_path) {
    auto t0 = std::chrono::steady_clock::now();
    std::atomic<size_t> lines_read{0}, parsed_ok{0}, parse_errors{0};

    std::thread t_rrc(run_pipeline, std::cref(rrc_path), std::ref(rrc_), std::ref(lines_read),
                       std::ref(parsed_ok), std::ref(parse_errors));
    std::thread t_nas(run_pipeline, std::cref(nas_path), std::ref(nas_), std::ref(lines_read),
                       std::ref(parsed_ok), std::ref(parse_errors));
    std::thread t_phy(run_pipeline, std::cref(phy_path), std::ref(phy_), std::ref(lines_read),
                       std::ref(parsed_ok), std::ref(parse_errors));
    t_rrc.join();
    t_nas.join();
    t_phy.join();

    sort_all();
    auto t1 = std::chrono::steady_clock::now();

    IngestStats stats;
    stats.lines_read = lines_read.load();
    stats.parsed_ok = parsed_ok.load();
    stats.parse_errors = parse_errors.load();
    stats.elapsed_s = std::chrono::duration<double>(t1 - t0).count();
    return stats;
}

LogStore::IngestStats LogStore::ingest_lines(Layer layer, const std::vector<std::string>& lines) {
    auto& out = store_for(layer);
    IngestStats stats;
    auto t0 = std::chrono::steady_clock::now();
    for (const auto& line : lines) {
        ++stats.lines_read;
        try {
            out.push_back(parse_line(line));
            ++stats.parsed_ok;
        } catch (const std::exception&) {
            ++stats.parse_errors;
        }
    }
    sort_all();
    auto t1 = std::chrono::steady_clock::now();
    stats.elapsed_s = std::chrono::duration<double>(t1 - t0).count();
    return stats;
}

std::vector<const ParsedMessage*> LogStore::query(Layer layer, const std::string& session_id, double t_start,
                                                    double t_end) const {
    const auto& store = store_for(layer);
    std::vector<const ParsedMessage*> out;
    auto lo = std::lower_bound(store.begin(), store.end(), t_start,
                                [](const ParsedMessage& m, double t) { return m.ts < t; });
    for (auto it = lo; it != store.end() && it->ts <= t_end; ++it) {
        if (it->session_id == session_id) out.push_back(&*it);
    }
    return out;
}

size_t LogStore::size(Layer layer) const { return store_for(layer).size(); }

}  // namespace celltrace
