// Name: Shreyansh Arora
// Roll No: 24BCS10252
// Lab 3: Clock Sweep Buffer Pool

#include <iostream>
#include <vector>
#include <unordered_map>
#include <thread>

template<typename T>
class ClockSweep {
public:
    ClockSweep(int maxNumber) : maxCacheSize(maxNumber), hand(0) {
        frames.resize(maxNumber, {T{}, 0, false});
    }

    T getKey(T key) {
        auto it = indexMap.find(key);
        if (it != indexMap.end()) {
            frames[it->second].refBit = 1;
            std::cout << "[HIT]  key=" << key << " -> frame " << it->second << "\n";
            return key;
        }
        putKey(key);
        return key;
    }

    void putKey(T key) {
        while (frames[hand].occupied && frames[hand].refBit == 1) {
            frames[hand].refBit = 0;
            hand = (hand + 1) % maxCacheSize;
        }

        if (frames[hand].occupied) {
            std::cout << "[EVICT] key=" << frames[hand].key << " from frame " << hand << "\n";
            indexMap.erase(frames[hand].key);
        }

        frames[hand] = {key, 1, true};
        indexMap[key] = hand;
        std::cout << "[MISS] key=" << key << " loaded into frame " << hand << "\n";
        hand = (hand + 1) % maxCacheSize;
    }

    void printState() {
        std::cout << "\n--- Buffer Pool State (hand=" << hand << ") ---\n";
        for (int i = 0; i < (int)maxCacheSize; i++) {
            if (frames[i].occupied)
                std::cout << "Frame[" << i << "] key=" << frames[i].key
                          << " ref=" << frames[i].refBit
                          << (i == hand ? " <-- hand" : "") << "\n";
            else
                std::cout << "Frame[" << i << "] [empty]"
                          << (i == hand ? " <-- hand" : "") << "\n";
        }
        std::cout << "-----------------------------------------------\n\n";
    }

private:
    struct Frame {
        T key;
        int refBit;
        bool occupied;
    };

    uint maxCacheSize{0u};
    std::thread bgClockThread;
    int hand;

    std::vector<Frame> frames;
    std::unordered_map<T, int> indexMap;
};

int main() {
    ClockSweep<int> clockSweep(4);

    std::vector<int> accesses = {1, 2, 3, 4, 1, 2, 5, 1, 2, 3, 4, 5};
    std::cout << "Access sequence: 1 2 3 4 1 2 5 1 2 3 4 5\n\n";

    for (int page : accesses) {
        clockSweep.getKey(page);
    }

    clockSweep.printState();
    return 0;
}
