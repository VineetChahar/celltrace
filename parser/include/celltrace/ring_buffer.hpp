#pragma once
#include <atomic>
#include <cstddef>
#include <optional>
#include <vector>

namespace celltrace {

// Lock-free single-producer/single-consumer ring buffer, fixed capacity
// (rounded up to a power of two so index wraparound is a mask, not a modulo).
// head/tail are each on their own cache line to avoid false sharing between
// the producer thread (writes tail_) and the consumer thread (writes head_);
// see LEARNING.md for why that padding matters in practice, not just theory.
template <typename T>
class SpscRingBuffer {
public:
    explicit SpscRingBuffer(size_t capacity_hint) {
        size_t cap = 1;
        while (cap < capacity_hint) cap <<= 1;
        capacity_ = cap;
        mask_ = cap - 1;
        buf_.resize(cap);
    }

    // Producer side. Returns false (drops nothing -- caller should retry/spin)
    // if the buffer is currently full.
    bool try_push(T item) {
        size_t tail = tail_.load(std::memory_order_relaxed);
        size_t head = head_.load(std::memory_order_acquire);
        if (tail - head >= capacity_) return false;  // full
        buf_[tail & mask_] = std::move(item);
        tail_.store(tail + 1, std::memory_order_release);
        return true;
    }

    // Consumer side. Returns std::nullopt if the buffer is currently empty.
    std::optional<T> try_pop() {
        size_t head = head_.load(std::memory_order_relaxed);
        size_t tail = tail_.load(std::memory_order_acquire);
        if (head == tail) return std::nullopt;  // empty
        T item = std::move(buf_[head & mask_]);
        head_.store(head + 1, std::memory_order_release);
        return item;
    }

    size_t capacity() const { return capacity_; }

private:
    static constexpr size_t kCacheLine = 64;
    std::vector<T> buf_;
    size_t capacity_;
    size_t mask_;
    alignas(kCacheLine) std::atomic<size_t> head_{0};
    alignas(kCacheLine) std::atomic<size_t> tail_{0};
};

}  // namespace celltrace
