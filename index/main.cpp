// Name: Shreyansh Arora
// Roll No: 24BCS10252
// Lab 4: Red-Black Tree + B-Tree

#include <iostream>
#include <vector>
#include <algorithm>

enum Color { RED, BLACK };

struct RBNode {
    int key;
    Color color;
    RBNode *left, *right, *parent;
    explicit RBNode(int k) : key(k), color(RED), left(nullptr), right(nullptr), parent(nullptr) {}
};

class RedBlackTree {
    RBNode* root = nullptr;

    void leftRotate(RBNode* x) {
        RBNode* y = x->right;
        x->right = y->left;
        if (y->left) y->left->parent = x;
        y->parent = x->parent;
        if (!x->parent)            root = y;
        else if (x == x->parent->left)  x->parent->left  = y;
        else                            x->parent->right = y;
        y->left   = x;
        x->parent = y;
    }

    void rightRotate(RBNode* x) {
        RBNode* y = x->left;
        x->left = y->right;
        if (y->right) y->right->parent = x;
        y->parent = x->parent;
        if (!x->parent)             root = y;
        else if (x == x->parent->right) x->parent->right = y;
        else                            x->parent->left  = y;
        y->right  = x;
        x->parent = y;
    }

    void fixInsert(RBNode* z) {
        while (z->parent && z->parent->color == RED) {
            RBNode* gp = z->parent->parent;
            if (z->parent == gp->left) {
                RBNode* uncle = gp->right;
                if (uncle && uncle->color == RED) {
                    z->parent->color = BLACK;
                    uncle->color     = BLACK;
                    gp->color        = RED;
                    z = gp;
                } else {
                    if (z == z->parent->right) { z = z->parent; leftRotate(z); }
                    z->parent->color = BLACK;
                    gp->color        = RED;
                    rightRotate(gp);
                }
            } else {
                RBNode* uncle = gp->left;
                if (uncle && uncle->color == RED) {
                    z->parent->color = BLACK;
                    uncle->color     = BLACK;
                    gp->color        = RED;
                    z = gp;
                } else {
                    if (z == z->parent->left) { z = z->parent; rightRotate(z); }
                    z->parent->color = BLACK;
                    gp->color        = RED;
                    leftRotate(gp);
                }
            }
        }
        root->color = BLACK;
    }

    void inorder(RBNode* n) const {
        if (!n) return;
        inorder(n->left);
        std::cout << n->key << "(" << (n->color == RED ? "R" : "B") << ") ";
        inorder(n->right);
    }

    void destroy(RBNode* n) {
        if (!n) return;
        destroy(n->left);
        destroy(n->right);
        delete n;
    }

public:
    ~RedBlackTree() { destroy(root); }

    void insert(int key) {
        RBNode* z = new RBNode(key);
        RBNode* p = nullptr;
        RBNode* cur = root;
        while (cur) {
            p = cur;
            cur = (key < cur->key) ? cur->left : cur->right;
        }
        z->parent = p;
        if (!p)             root = z;
        else if (key < p->key) p->left  = z;
        else                   p->right = z;
        fixInsert(z);
    }

    bool contains(int key) const {
        RBNode* cur = root;
        while (cur) {
            if (key == cur->key) return true;
            cur = (key < cur->key) ? cur->left : cur->right;
        }
        return false;
    }

    void print() const { inorder(root); std::cout << "\n"; }
};

class BTreeNode {
public:
    int* keys;
    int  t;
    BTreeNode** C;
    int  n;
    bool leaf;

    BTreeNode(int _t, bool _leaf) : t(_t), leaf(_leaf), n(0) {
        keys = new int[2 * t - 1];
        C    = new BTreeNode*[2 * t]();
    }
    ~BTreeNode() { delete[] keys; delete[] C; }

    BTreeNode* search(int k) {
        int i = 0;
        while (i < n && k > keys[i]) i++;
        if (i < n && keys[i] == k) return this;
        if (leaf) return nullptr;
        return C[i]->search(k);
    }

    void insertNonFull(int k) {
        int i = n - 1;
        if (leaf) {
            while (i >= 0 && keys[i] > k) { keys[i + 1] = keys[i]; i--; }
            keys[i + 1] = k;
            n++;
        } else {
            while (i >= 0 && keys[i] > k) i--;
            if (C[i + 1]->n == 2 * t - 1) {
                splitChild(i + 1, C[i + 1]);
                if (keys[i + 1] < k) i++;
            }
            C[i + 1]->insertNonFull(k);
        }
    }

    void splitChild(int i, BTreeNode* y) {
        BTreeNode* z = new BTreeNode(y->t, y->leaf);
        z->n = t - 1;
        for (int j = 0; j < t - 1; j++) z->keys[j] = y->keys[j + t];
        if (!y->leaf)
            for (int j = 0; j < t; j++) z->C[j] = y->C[j + t];
        y->n = t - 1;
        for (int j = n; j >= i + 1; j--) C[j + 1] = C[j];
        C[i + 1] = z;
        for (int j = n - 1; j >= i; j--) keys[j + 1] = keys[j];
        keys[i] = y->keys[t - 1];
        n++;
    }

    void traverse(int depth = 0) {
        int i;
        for (i = 0; i < n; i++) {
            if (!leaf) C[i]->traverse(depth + 1);
            for (int s = 0; s < depth * 2; s++) std::cout << " ";
            std::cout << keys[i] << "\n";
        }
        if (!leaf) C[i]->traverse(depth + 1);
    }

    int findKey(int k) {
        int idx = 0;
        while (idx < n && keys[idx] < k) idx++;
        return idx;
    }

    void remove(int k) {
        int idx = findKey(k);
        if (idx < n && keys[idx] == k) {
            leaf ? removeFromLeaf(idx) : removeFromNonLeaf(idx);
        } else {
            if (leaf) { std::cout << "Key " << k << " not found.\n"; return; }
            bool last = (idx == n);
            if (C[idx]->n < t) fill(idx);
            (last && idx > n) ? C[idx - 1]->remove(k) : C[idx]->remove(k);
        }
    }

    void removeFromLeaf(int idx) {
        for (int i = idx + 1; i < n; i++) keys[i - 1] = keys[i];
        n--;
    }

    void removeFromNonLeaf(int idx) {
        int k = keys[idx];
        if (C[idx]->n >= t) {
            int pred = getPred(idx);
            keys[idx] = pred;
            C[idx]->remove(pred);
        } else if (C[idx + 1]->n >= t) {
            int succ = getSucc(idx);
            keys[idx] = succ;
            C[idx + 1]->remove(succ);
        } else {
            merge(idx);
            C[idx]->remove(k);
        }
    }

    int getPred(int idx) {
        BTreeNode* cur = C[idx];
        while (!cur->leaf) cur = cur->C[cur->n];
        return cur->keys[cur->n - 1];
    }

    int getSucc(int idx) {
        BTreeNode* cur = C[idx + 1];
        while (!cur->leaf) cur = cur->C[0];
        return cur->keys[0];
    }

    void fill(int idx) {
        if (idx != 0 && C[idx - 1]->n >= t)        borrowFromPrev(idx);
        else if (idx != n && C[idx + 1]->n >= t)   borrowFromNext(idx);
        else (idx != n) ? merge(idx) : merge(idx - 1);
    }

    void borrowFromPrev(int idx) {
        BTreeNode* child   = C[idx];
        BTreeNode* sibling = C[idx - 1];
        for (int i = child->n - 1; i >= 0; i--) child->keys[i + 1] = child->keys[i];
        if (!child->leaf)
            for (int i = child->n; i >= 0; i--) child->C[i + 1] = child->C[i];
        child->keys[0] = keys[idx - 1];
        if (!child->leaf) child->C[0] = sibling->C[sibling->n];
        keys[idx - 1] = sibling->keys[sibling->n - 1];
        child->n++;
        sibling->n--;
    }

    void borrowFromNext(int idx) {
        BTreeNode* child   = C[idx];
        BTreeNode* sibling = C[idx + 1];
        child->keys[child->n] = keys[idx];
        if (!child->leaf) child->C[child->n + 1] = sibling->C[0];
        keys[idx] = sibling->keys[0];
        for (int i = 1; i < sibling->n; i++) sibling->keys[i - 1] = sibling->keys[i];
        if (!sibling->leaf)
            for (int i = 1; i <= sibling->n; i++) sibling->C[i - 1] = sibling->C[i];
        child->n++;
        sibling->n--;
    }

    void merge(int idx) {
        BTreeNode* child   = C[idx];
        BTreeNode* sibling = C[idx + 1];
        child->keys[t - 1] = keys[idx];
        for (int i = 0; i < sibling->n; i++) child->keys[i + t] = sibling->keys[i];
        if (!child->leaf)
            for (int i = 0; i <= sibling->n; i++) child->C[i + t] = sibling->C[i];
        for (int i = idx + 1; i < n; i++) keys[i - 1] = keys[i];
        for (int i = idx + 2; i <= n; i++) C[i - 1] = C[i];
        child->n += sibling->n + 1;
        n--;
        delete sibling;
    }
};

class BTree {
    BTreeNode* root = nullptr;
    int t;

    void deleteTree(BTreeNode* node) {
        if (!node) return;
        if (!node->leaf)
            for (int i = 0; i <= node->n; i++) deleteTree(node->C[i]);
        delete node;
    }

public:
    explicit BTree(int _t) : t(_t) {}
    ~BTree() { deleteTree(root); }

    void insert(int k) {
        if (!root) {
            root = new BTreeNode(t, true);
            root->keys[0] = k;
            root->n = 1;
            return;
        }
        if (root->n == 2 * t - 1) {
            BTreeNode* s = new BTreeNode(t, false);
            s->C[0] = root;
            s->splitChild(0, root);
            s->C[s->keys[0] < k ? 1 : 0]->insertNonFull(k);
            root = s;
        } else {
            root->insertNonFull(k);
        }
    }

    BTreeNode* search(int k) { return root ? root->search(k) : nullptr; }

    void remove(int k) {
        if (!root) { std::cout << "B-Tree is empty.\n"; return; }
        root->remove(k);
        if (root->n == 0) {
            BTreeNode* old = root;
            root = root->leaf ? nullptr : root->C[0];
            delete old;
        }
    }

    void traverse() { if (root) root->traverse(); }
};

int main() {
    std::cout << "========================================\n";
    std::cout << "  Red-Black Tree\n";
    std::cout << "========================================\n";
    RedBlackTree rbt;
    for (int k : {10, 20, 30, 15, 25, 5, 1}) rbt.insert(k);
    std::cout << "In-order (key/color): ";
    rbt.print();
    std::cout << "Contains 15? " << (rbt.contains(15) ? "yes" : "no") << "\n";
    std::cout << "Contains 99? " << (rbt.contains(99) ? "yes" : "no") << "\n";

    std::cout << "\n========================================\n";
    std::cout << "  B-Tree (degree t=3)\n";
    std::cout << "========================================\n";
    BTree bt(3);
    for (int k : {1, 3, 7, 10, 11, 13, 14, 15, 18, 16, 19, 24, 25, 26, 21, 4, 5, 20, 22, 2, 17, 12, 6})
        bt.insert(k);

    std::cout << "B-Tree (indented):\n";
    bt.traverse();

    std::cout << "\nSearch 18: " << (bt.search(18) ? "FOUND" : "NOT FOUND") << "\n";
    std::cout << "Search 99: " << (bt.search(99) ? "FOUND" : "NOT FOUND") << "\n";

    for (int k : {6, 13, 7, 4}) {
        std::cout << "\nRemoving " << k << "...\n";
        bt.remove(k);
        bt.traverse();
    }

    return 0;
}
