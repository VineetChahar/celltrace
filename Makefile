.PHONY: venv data parser-build parser-test parser-bench rag-agent-run eval demo docker-eval clean

venv:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install pybind11 numpy sentence-transformers ollama pydantic pytest matplotlib

data:
	.venv/bin/python -m synthesizer.generator --seed 42

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

eval:
	.venv/bin/python -m eval.run_eval

demo: data parser-build eval

docker-eval:
	docker compose build
	docker compose run --rm agent

clean:
	rm -rf parser/build parser/build_san parser/build_release .venv
