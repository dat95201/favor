from favor.java_ast import l1_key, l2_key, l1_normalize, l2_normalize

A = """public int add(int a, int b) {
    return a - b;
}"""
A_WS = """public int add(int a, int b) {
        // comment only
        return a   -   b;
}"""
B = """public int add(int a, int b) {
    int result = b - a;
    return result;
}"""
B_RENAMED = """public int add(int a, int b) {
    int total = b - a;
    return total;
}"""
C = """public int add(int a, int b) {
    return a * b;
}"""


def test_l1_ignores_whitespace_and_comments():
    assert l1_key(A) == l1_key(A_WS)


def test_l1_distinguishes_different_code():
    assert l1_key(A) != l1_key(B)
    assert l1_key(A) != l1_key(C)


def test_l2_alpha_rename_merges_renamed_locals():
    assert l1_key(B) != l1_key(B_RENAMED)
    assert l2_key(B) == l2_key(B_RENAMED)


def test_l2_without_rename_keeps_them_apart():
    assert l2_key(B, alpha_rename=False) != l2_key(B_RENAMED, alpha_rename=False)


def test_l1_implies_l2():
    assert l2_key(A) == l2_key(A_WS)


def test_l2_distinguishes_structure():
    assert l2_key(A) != l2_key(C)


def test_parameters_are_not_renamed_inconsistently():
    # parameters are declared locals too: consistent renaming keeps a/b aligned
    swapped = A.replace("a - b", "b - a")
    assert l2_key(A) != l2_key(swapped)


def test_normalize_is_deterministic():
    assert l1_normalize(A) == l1_normalize(A)
    assert l2_normalize(A) == l2_normalize(A)
