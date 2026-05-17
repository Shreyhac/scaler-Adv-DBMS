#include <chrono>
#include <condition_variable>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

template <typename T>
class ClockSweep {
public:
    explicit ClockSweep(std::size_t maxCacheSize,
                        std::chrono::milliseconds sweepInterval = std::chrono::milliseconds(500))
        : maxCacheSize_(maxCacheSize),
          sweepInterval_(sweepInterval),
          slots_(maxCacheSize),
          hand_(0),
          stop_(false) {
        if (maxCacheSize == 0)
            throw std::invalid_argument("Cache capacity must be greater than 0");
        bgClockThread_ = std::thread(&ClockSweep::sweepLoop, this);
    }

    ~ClockSweep() {
        {
            std::lock_guard<std::mutex> lk(mu_);
            stop_ = true;
        }
        cv_.notify_all();
        if (bgClockThread_.joinable())
            bgClockThread_.join();
    }

    ClockSweep(const ClockSweep&) = delete;
    ClockSweep& operator=(const ClockSweep&) = delete;

    // Returns the key if found in cache (sets reference bit); returns T{} on miss
    T getKey(const T& key) {
        std::lock_guard<std::mutex> lk(mu_);
        auto it = keyIndex_.find(key);
        if (it == keyIndex_.end())
            return T{};
        slots_[it->second].refBit = true;
        return slots_[it->second].key;
    }

    // Inserts key into cache; evicts a victim via clock sweep if cache is full
    void putKey(const T& key) {
        std::lock_guard<std::mutex> lk(mu_);
        auto it = keyIndex_.find(key);
        if (it != keyIndex_.end()) {
            slots_[it->second].refBit = true;
            return;
        }

        // Look for a free slot first
        for (std::size_t scanned = 0; scanned < slots_.size(); ++scanned) {
            if (!slots_[hand_].occupied) {
                slots_[hand_] = {key, true, true};
                keyIndex_[key] = hand_;
                hand_ = (hand_ + 1) % slots_.size();
                return;
            }
            hand_ = (hand_ + 1) % slots_.size();
        }

        // All slots occupied — run clock sweep to find victim
        std::size_t victim = findVictimLocked();
        keyIndex_.erase(slots_[victim].key);
        slots_[victim] = {key, true, true};
        keyIndex_[key] = victim;
        hand_ = (victim + 1) % slots_.size();
    }

    void debugPrint(const std::string& label) {
        std::lock_guard<std::mutex> lk(mu_);
        std::cout << "[" << label << "]\n";
        for (std::size_t i = 0; i < slots_.size(); ++i) {
            const auto& s = slots_[i];
            std::cout << "  slot[" << i << "]"
                      << (i == hand_ ? " <hand>" : "      ")
                      << " occupied=" << s.occupied
                      << " refBit=" << s.refBit;
            if (s.occupied)
                std::cout << " key=" << s.key;
            std::cout << "\n";
        }
    }

private:
    struct Slot {
        T key{};
        bool refBit{false};
        bool occupied{false};
    };

    std::size_t maxCacheSize_;
    std::chrono::milliseconds sweepInterval_;
    std::vector<Slot> slots_;
    std::unordered_map<T, std::size_t> keyIndex_;
    std::size_t hand_;
    std::mutex mu_;
    std::condition_variable cv_;
    bool stop_;
    std::thread bgClockThread_;

    // Caller must hold mu_
    std::size_t findVictimLocked() {
        for (std::size_t passes = 0; passes < 2 * slots_.size(); ++passes) {
            Slot& s = slots_[hand_];
            if (s.occupied && !s.refBit)
                return hand_;
            if (s.occupied && s.refBit)
                s.refBit = false;  // second chance: clear bit, keep going
            hand_ = (hand_ + 1) % slots_.size();
        }
        return hand_;  // fallback
    }

    void sweepLoop() {
        std::unique_lock<std::mutex> lk(mu_);
        while (!stop_) {
            if (cv_.wait_for(lk, sweepInterval_, [this] { return stop_; }))
                break;
            // Periodically age frames by clearing reference bits
            for (auto& s : slots_)
                if (s.occupied && s.refBit)
                    s.refBit = false;
        }
    }
};

// ---------------------------------------------------------------------------
// Demo
// ---------------------------------------------------------------------------

int main() {
    std::cout << "=== Clock Sweep Buffer Cache Demo ===\n\n";

    // Demo 1: Basic put/get with cache size 3
    {
        std::cout << "-- Demo 1: put/get, cache size = 3 --\n";
        ClockSweep<int> cache(3);

        cache.putKey(10);
        cache.putKey(20);
        cache.putKey(30);
        cache.debugPrint("after inserting 10, 20, 30");

        std::cout << "getKey(20) = " << cache.getKey(20) << "  (hit expected)\n";
        std::cout << "getKey(99) = " << cache.getKey(99) << "  (miss expected, returns 0)\n\n";
    }

    // Demo 2: Eviction — insert beyond capacity; key 10 (no ref bit) should be evicted
    {
        std::cout << "-- Demo 2: eviction, cache size = 3 --\n";
        ClockSweep<int> cache(3);

        cache.putKey(1);
        cache.putKey(2);
        cache.putKey(3);
        // Access 2 and 3 to set their reference bits
        cache.getKey(2);
        cache.getKey(3);
        cache.debugPrint("before inserting 4 (1 has no ref bit, is victim)");

        cache.putKey(4);
        cache.debugPrint("after inserting 4");

        std::cout << "getKey(1) = " << cache.getKey(1) << "  (evicted, miss expected)\n";
        std::cout << "getKey(4) = " << cache.getKey(4) << "  (hit expected)\n\n";
    }

    // Demo 3: Duplicate insertion updates reference bit, does not add new slot
    {
        std::cout << "-- Demo 3: duplicate insert --\n";
        ClockSweep<int> cache(3);

        cache.putKey(100);
        cache.putKey(200);
        cache.putKey(100);  // duplicate
        cache.debugPrint("after inserting 100, 200, 100 (dup)");
        std::cout << "getKey(100) = " << cache.getKey(100) << "  (hit expected)\n\n";
    }

    // Demo 4: String keys — clock sweep evicts the first unprotected frame
    {
        std::cout << "-- Demo 4: string keys --\n";
        ClockSweep<std::string> cache(2);

        cache.putKey(std::string("alpha"));  // slot 0, refBit=1
        cache.putKey(std::string("beta"));   // slot 1, refBit=1
        // Both slots full; sweep clears refBits in two passes.
        // Slot 0 (alpha) is the first found with refBit=0 → evicted.
        cache.putKey(std::string("gamma"));
        cache.debugPrint("after inserting alpha, beta, gamma (alpha evicted)");

        std::cout << "getKey(\"alpha\") = \"" << cache.getKey(std::string("alpha")) << "\"  (evicted, miss)\n";
        std::cout << "getKey(\"beta\")  = \"" << cache.getKey(std::string("beta"))  << "\"  (survived, hit)\n";
        std::cout << "getKey(\"gamma\") = \"" << cache.getKey(std::string("gamma")) << "\"  (hit)\n\n";
    }

    std::cout << "=== All demos complete ===\n";
    return 0;
}
