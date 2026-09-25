#pragma once
#include <string>
#include <vector>

#include "celltrace/message.hpp"

namespace celltrace {

class LogStore {
public:
    struct IngestStats {
        size_t lines_read = 0;
        size_t parsed_ok = 0;
        size_t parse_errors = 0;
        double elapsed_s = 0.0;
        double messages_per_sec() const { return elapsed_s > 0 ? parsed_ok / elapsed_s : 0.0; }
    };

    // Streams the three per-layer log files through the ring-buffer pipeline
    // (one producer thread reading lines + one consumer thread parsing them,
    // per file, all three pipelines running concurrently) and builds the
    // queryable, time-sorted per-layer stores.
    IngestStats ingest_files(const std::string& rrc_path, const std::string& nas_path, const std::string& phy_path);

    // Also used directly by unit tests to feed synthetic lines (including
    // malformed/truncated/out-of-order ones) without going through files.
    IngestStats ingest_lines(Layer layer, const std::vector<std::string>& lines);

    std::vector<const ParsedMessage*> query(Layer layer, const std::string& session_id,
                                             double t_start, double t_end) const;

    size_t size(Layer layer) const;

private:
    std::vector<ParsedMessage> rrc_, nas_, phy_;

    std::vector<ParsedMessage>& store_for(Layer l);
    const std::vector<ParsedMessage>& store_for(Layer l) const;
    void sort_all();
};

}  // namespace celltrace
