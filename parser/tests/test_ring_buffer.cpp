#include <atomic>
#include <catch2/catch_test_macros.hpp>
#include <thread>
#include <vector>

#include "celltrace/ring_buffer.hpp"

using celltrace::SpscRingBuffer;

TEST_CASE("rounds capacity up to a power of two", "[ring_buffer]") {
    SpscRingBuffer<int> rb(10);
    REQUIRE(rb.capacity() == 16);
}

TEST_CASE("single-thread FIFO order preserved", "[ring_buffer]") {
    SpscRingBuffer<int> rb(8);
    for (int i = 0; i < 5; ++i) REQUIRE(rb.try_push(i));
    for (int i = 0; i < 5; ++i) {
        auto v = rb.try_pop();
        REQUIRE(v.has_value());
        REQUIRE(*v == i);
    }
    REQUIRE_FALSE(rb.try_pop().has_value());
}

TEST_CASE("try_push fails when full", "[ring_buffer]") {
    SpscRingBuffer<int> rb(4);  // rounds to 4
    for (int i = 0; i < 4; ++i) REQUIRE(rb.try_push(i));
    REQUIRE_FALSE(rb.try_push(99));  // full
    REQUIRE(rb.try_pop().has_value());
    REQUIRE(rb.try_push(99));  // room again after one pop
}

TEST_CASE("try_pop fails when empty", "[ring_buffer]") {
    SpscRingBuffer<std::string> rb(4);
    REQUIRE_FALSE(rb.try_pop().has_value());
}

TEST_CASE("concurrent producer/consumer preserves order and loses nothing", "[ring_buffer][concurrency]") {
    constexpr int kN = 200000;
    SpscRingBuffer<int> rb(1024);
    std::vector<int> consumed;
    consumed.reserve(kN);

    std::thread producer([&] {
        for (int i = 0; i < kN; ++i) {
            while (!rb.try_push(i)) std::this_thread::yield();
        }
    });
    std::thread consumer([&] {
        int received = 0;
        while (received < kN) {
            auto v = rb.try_pop();
            if (v.has_value()) {
                consumed.push_back(*v);
                ++received;
            } else {
                std::this_thread::yield();
            }
        }
    });
    producer.join();
    consumer.join();

    REQUIRE(consumed.size() == static_cast<size_t>(kN));
    for (int i = 0; i < kN; ++i) REQUIRE(consumed[static_cast<size_t>(i)] == i);
}
