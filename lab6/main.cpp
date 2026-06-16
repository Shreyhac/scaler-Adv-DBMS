// Name: Shreyansh Arora
// Roll No: 24BCS10252
// Lab 6: Transaction Manager — MVCC + Two-Phase Locking + Deadlock Detection

#include <iostream>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include <list>
#include <string>
#include <atomic>
#include <stdexcept>
#include <cassert>

using TxID   = uint64_t;
using RowKey = std::string;

enum class TxStatus { ACTIVE, COMMITTED, ABORTED };

struct Transaction {
    TxID     id;
    TxID     snapshot_xid;
    TxStatus status = TxStatus::ACTIVE;
};

static std::atomic<TxID> g_next_xid{1};
static std::unordered_map<TxID, Transaction> g_txns;

TxID begin_tx() {
    TxID xid = g_next_xid.fetch_add(1);
    g_txns[xid] = {xid, xid, TxStatus::ACTIVE};
    std::cout << "[BEGIN]  T" << xid << "\n";
    return xid;
}

bool is_committed(TxID xid) {
    auto it = g_txns.find(xid);
    return it != g_txns.end() && it->second.status == TxStatus::COMMITTED;
}

bool is_aborted(TxID xid) {
    auto it = g_txns.find(xid);
    return it != g_txns.end() && it->second.status == TxStatus::ABORTED;
}

struct RowVersion {
    std::string value;
    TxID        xmin;
    TxID        xmax;
    RowVersion* prev;

    RowVersion(std::string v, TxID xm)
        : value(std::move(v)), xmin(xm), xmax(0), prev(nullptr) {}
};

bool is_visible(const Transaction& tx, const RowVersion* v) {
    if (!is_committed(v->xmin) || v->xmin > tx.snapshot_xid) return false;
    if (v->xmax == 0) return true;
    return is_aborted(v->xmax) || v->xmax > tx.snapshot_xid;
}

struct MVCCRow {
    RowVersion* head = nullptr;

    ~MVCCRow() {
        RowVersion* cur = head;
        while (cur) { auto* tmp = cur->prev; delete cur; cur = tmp; }
    }
};

enum class LockMode { SHARED, EXCLUSIVE };

struct LockRequest {
    TxID     txid;
    LockMode mode;
    bool     granted;
};

class LockManager {
public:
    bool acquire(TxID tx, const RowKey& key, LockMode mode) {
        auto& q = table_[key];
        for (auto& req : q) {
            if (req.txid == tx && req.granted) {
                if (mode == LockMode::SHARED || req.mode == LockMode::EXCLUSIVE) return true;
                if (q.size() == 1) { req.mode = LockMode::EXCLUSIVE; return true; }
            }
        }
        bool conflict = false;
        for (auto& req : q) {
            if (req.granted && req.txid != tx) {
                if (mode == LockMode::EXCLUSIVE || req.mode == LockMode::EXCLUSIVE)
                { conflict = true; break; }
            }
        }
        if (!conflict) {
            q.push_back({tx, mode, true});
            held_[tx].insert(key);
            return true;
        }
        for (auto& req : q) if (req.txid == tx && !req.granted) return false;
        q.push_back({tx, mode, false});
        waitlist_[tx] = key;
        return false;
    }

    void release_all(TxID tx) {
        for (const auto& key : held_[tx]) {
            auto& q = table_[key];
            q.remove_if([tx](const LockRequest& r){ return r.txid == tx; });
            for (auto& req : q) {
                if (req.granted) continue;
                bool ok = true;
                for (auto& other : q) {
                    if (other.granted && other.txid != req.txid) {
                        if (req.mode == LockMode::EXCLUSIVE || other.mode == LockMode::EXCLUSIVE)
                        { ok = false; break; }
                    }
                }
                if (ok) {
                    req.granted = true;
                    held_[req.txid].insert(key);
                    waitlist_.erase(req.txid);
                    std::cout << "[LOCK]   T" << req.txid << " granted " << key << "\n";
                }
            }
        }
        held_.erase(tx);
        waitlist_.erase(tx);
    }

    TxID detect_deadlock() {
        std::unordered_map<TxID, std::unordered_set<TxID>> wfg;
        for (auto& [waiting_tx, key] : waitlist_) {
            for (auto& req : table_[key])
                if (req.granted && req.txid != waiting_tx)
                    wfg[waiting_tx].insert(req.txid);
        }
        if (wfg.empty()) return 0;

        std::cout << "[WFG]    ";
        for (auto& [a, bs] : wfg)
            for (TxID b : bs) std::cout << "T" << a << "->T" << b << " ";
        std::cout << "\n";

        std::unordered_set<TxID> visited, rec;
        for (auto& [node, _] : wfg) {
            std::vector<TxID> path;
            if (dfs(node, wfg, visited, rec, path)) {
                TxID victim = *std::max_element(path.begin(), path.end());
                std::cout << "[DEADLOCK] aborting T" << victim << "\n";
                return victim;
            }
        }
        return 0;
    }

private:
    std::unordered_map<RowKey, std::list<LockRequest>>     table_;
    std::unordered_map<TxID,  std::unordered_set<RowKey>> held_;
    std::unordered_map<TxID,  RowKey>                      waitlist_;

    bool dfs(TxID node,
             const std::unordered_map<TxID, std::unordered_set<TxID>>& g,
             std::unordered_set<TxID>& visited,
             std::unordered_set<TxID>& rec,
             std::vector<TxID>& path) {
        visited.insert(node); rec.insert(node); path.push_back(node);
        auto it = g.find(node);
        if (it != g.end()) {
            for (TxID nb : it->second) {
                if (!visited.count(nb)) {
                    if (dfs(nb, g, visited, rec, path)) return true;
                } else if (rec.count(nb)) return true;
            }
        }
        rec.erase(node); path.pop_back();
        return false;
    }
};

class TransactionManager {
public:
    std::string read(TxID tx, const RowKey& key) {
        assert_active(tx);
        if (!locks_.acquire(tx, key, LockMode::SHARED)) {
            std::cout << "[READ]   T" << tx << " waiting on " << key << "\n";
            TxID v = locks_.detect_deadlock();
            if (v) abort_tx(v);
            return "";
        }
        std::cout << "[READ]   T" << tx << " reads " << key << " -> ";
        auto it = db_.find(key);
        if (it == db_.end()) { std::cout << "(not found)\n"; return ""; }
        const Transaction& t = g_txns[tx];
        for (RowVersion* v = it->second->head; v; v = v->prev) {
            if (is_visible(t, v)) { std::cout << "\"" << v->value << "\"\n"; return v->value; }
        }
        std::cout << "(no visible version)\n";
        return "";
    }

    void write(TxID tx, const RowKey& key, const std::string& value) {
        assert_active(tx);
        if (!locks_.acquire(tx, key, LockMode::EXCLUSIVE)) {
            std::cout << "[WRITE]  T" << tx << " waiting on " << key << "\n";
            TxID v = locks_.detect_deadlock();
            if (v) abort_tx(v);
            return;
        }
        if (!db_.count(key)) db_[key] = new MVCCRow();
        MVCCRow* row = db_[key];
        for (RowVersion* v = row->head; v; v = v->prev) {
            if (v->xmax == 0 && is_visible(g_txns[tx], v)) { v->xmax = tx; break; }
        }
        RowVersion* nv = new RowVersion(value, tx);
        nv->prev  = row->head;
        row->head = nv;
        written_[tx].push_back(key);
        std::cout << "[WRITE]  T" << tx << " writes " << key << " = \"" << value << "\"\n";
    }

    void commit(TxID tx) {
        assert_active(tx);
        g_txns[tx].status = TxStatus::COMMITTED;
        locks_.release_all(tx);
        std::cout << "[COMMIT] T" << tx << "\n";
    }

    void abort_tx(TxID tx) {
        if (g_txns[tx].status != TxStatus::ACTIVE) return;
        g_txns[tx].status = TxStatus::ABORTED;
        for (auto& key : written_[tx]) {
            auto it = db_.find(key);
            if (it == db_.end()) continue;
            MVCCRow* row = it->second;
            RowVersion* prev = nullptr;
            RowVersion* cur  = row->head;
            while (cur) {
                if (cur->xmin == tx) {
                    RowVersion* del = cur;
                    (prev ? prev->prev : row->head) = cur->prev;
                    cur = cur->prev;
                    delete del;
                } else {
                    if (cur->xmax == tx) cur->xmax = 0;
                    prev = cur; cur = cur->prev;
                }
            }
        }
        written_.erase(tx);
        locks_.release_all(tx);
        std::cout << "[ABORT]  T" << tx << "\n";
    }

    ~TransactionManager() { for (auto& [k, r] : db_) delete r; }

private:
    std::unordered_map<RowKey, MVCCRow*>          db_;
    LockManager                                    locks_;
    std::unordered_map<TxID, std::vector<RowKey>> written_;

    void assert_active(TxID tx) {
        if (g_txns[tx].status != TxStatus::ACTIVE)
            throw std::runtime_error("T" + std::to_string(tx) + " is not active");
    }
};

int main() {
    TransactionManager tm;

    std::cout << "=== mvcc ===\n";
    {
        TxID t1 = begin_tx();
        tm.write(t1, "balance", "1000");
        tm.commit(t1);

        TxID t2 = begin_tx();
        TxID t3 = begin_tx();
        tm.write(t2, "balance", "900");
        tm.read(t3, "balance");
        tm.commit(t2);
        tm.read(t3, "balance");
        tm.commit(t3);
    }

    std::cout << "\n=== 2pl ===\n";
    {
        TxID t4 = begin_tx();
        TxID t5 = begin_tx();
        tm.write(t4, "row_A", "val_A");
        tm.read(t5, "row_A");
        tm.commit(t4);
    }

    std::cout << "\n=== deadlock ===\n";
    {
        TxID t6 = begin_tx();
        TxID t7 = begin_tx();
        tm.write(t6, "x", "v1");
        tm.write(t7, "y", "v2");
        tm.write(t6, "y", "v3");
        tm.write(t7, "x", "v4");
        if (g_txns[t6].status == TxStatus::ACTIVE) tm.commit(t6);
        if (g_txns[t7].status == TxStatus::ACTIVE) tm.commit(t7);
    }

    return 0;
}
