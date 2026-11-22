.PHONY: venv data data-heldout parser-build parser-test parser-bench rag-agent-run eval eval-v1 eval-heldout eval-7b eval-compare test demo docker-eval clean

venv:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install pybind11 numpy sentence-transformers ollama pydantic pytest matplotlib

data:
	.venv/bin/python -m synthesizer.generator --seed 42

# held-out set: same generator code, different seed (frozen; hashes in eval_results/heldout_manifest.json)
data-heldout:
	.venv/bin/python -m synthesizer.generator --seed 20261122 --out-dir data_heldout

parser-build:
	cmake -S parser -B parser/build -DCMAKE_BUILD_TYPE=Release
	cmake --build parser/build -j 8

parser-test:
	cmake -S parser -B parser/build_san -DCMAKE_BUILD_TYPE=Debug -DCELLTRACE_SANITIZE=ON -DCELLTRACE_BUILD_BENCH=OFF
	cmake --build parser/build_san -j 8 --target celltrace_tests
	./parser/build_san/celltrace_tests

parser-bench:
	cmake -S parser -B parser/build_release -DCMAKE_BUILD_TYPE=Release -DCELLTRACE_BUILD_TESTS=OFF
	cmake --build parser/build_release -j 8 --target bench_throughput
	./parser/build_release/bench_throughput

# v2 agent (absence/timing tools) on the original 86 incidents
eval:
	.venv/bin/python -m eval.run_eval --agent v2 --data-dir data --out-dir eval_results/v2_tools/original

eval-heldout:
	.venv/bin/python -m eval.run_eval --agent v2 --data-dir data_heldout --out-dir eval_results/v2_tools/heldout

# the pre-change agent, re-run under identical settings for the before/after comparison
eval-v1:
	.venv/bin/python -m eval.run_eval --agent v1 --data-dir data --out-dir eval_results/v1_rerun/original
	.venv/bin/python -m eval.run_eval --agent v1 --data-dir data_heldout --out-dir eval_results/v1_rerun/heldout

eval-7b:
	.venv/bin/python -m eval.run_eval --agent v2 --model qwen2.5:7b-instruct --data-dir data \
		--fault-types HO_MISSED_MEASUREMENT,PAGING_TIMEOUT,REG_TIMEOUT_DROPPED_NAS \
		--out-dir eval_results/v2_tools/7b_three_types

eval-compare:
	.venv/bin/python -m eval.compare

test:
	.venv/bin/python -m pytest tests/ -q

demo: data parser-build eval

docker-eval:
	docker compose build
	docker compose run --rm agent

clean:
	rm -rf parser/build parser/build_san parser/build_release .venv
