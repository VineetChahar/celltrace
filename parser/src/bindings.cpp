#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "celltrace/log_store.hpp"

namespace py = pybind11;
using namespace celltrace;

namespace {

// Recursively converts an already-parsed json::Value into a native Python
// object. This is where "Python only consumes already-structured output"
// actually happens -- there is no second JSON parse anywhere in this path.
py::object to_python(const json::Value& v) {
    switch (v.type()) {
        case json::Value::Type::Null:
            return py::none();
        case json::Value::Type::Bool:
            return py::bool_(v.as_bool());
        case json::Value::Type::Number:
            return py::float_(v.as_number());
        case json::Value::Type::String:
            return py::str(v.as_string());
        case json::Value::Type::Array: {
            py::list out;
            for (const auto& item : v.as_array()) out.append(to_python(item));
            return out;
        }
        case json::Value::Type::Object: {
            py::dict out;
            for (const auto& [k, val] : v.as_object()) out[py::str(k)] = to_python(val);
            return out;
        }
    }
    return py::none();
}

py::dict message_to_python(const ParsedMessage& m) {
    py::dict d;
    d["ts"] = m.ts;
    d["session_id"] = m.session_id;
    d["ue_pseudo"] = m.ue_pseudo;
    d["layer"] = layer_to_string(m.layer);
    d["msg_type"] = m.msg_type;
    d["direction"] = m.direction;
    d["cell_id"] = m.cell_id;
    d["fields"] = to_python(m.fields);
    d["raw_line"] = m.raw_line;
    return d;
}

}  // namespace

PYBIND11_MODULE(celltrace_parser, mod) {
    mod.doc() = "C++ streaming parser for CellTrace 5G RRC/NAS/PHY logs";

    py::class_<LogStore::IngestStats>(mod, "IngestStats")
        .def_readonly("lines_read", &LogStore::IngestStats::lines_read)
        .def_readonly("parsed_ok", &LogStore::IngestStats::parsed_ok)
        .def_readonly("parse_errors", &LogStore::IngestStats::parse_errors)
        .def_readonly("elapsed_s", &LogStore::IngestStats::elapsed_s)
        .def("messages_per_sec", &LogStore::IngestStats::messages_per_sec);

    py::class_<LogStore>(mod, "LogStore")
        .def(py::init<>())
        .def("ingest_files", &LogStore::ingest_files,
             py::arg("rrc_path"), py::arg("nas_path"), py::arg("phy_path"))
        .def("size", [](const LogStore& s, const std::string& layer) {
            return s.size(layer_from_string(layer));
        })
        .def("query", [](const LogStore& s, const std::string& layer, const std::string& session_id,
                          double t_start, double t_end) {
            auto results = s.query(layer_from_string(layer), session_id, t_start, t_end);
            py::list out;
            for (const auto* m : results) out.append(message_to_python(*m));
            return out;
        }, py::arg("layer"), py::arg("session_id"), py::arg("t_start"), py::arg("t_end"));
}
