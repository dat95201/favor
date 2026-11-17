import pytest

from favor.extraction import Rejection, _single_changed_method

BUGGY = """package p;

public class C {
    private int n;

    public int add(int a, int b) {
        return a - b;
    }

    public int sub(int a, int b) {
        return a - b;
    }
}
"""


def test_one_method_changed():
    fixed = BUGGY.replace("return a - b;\n    }\n\n    public int sub", "return a + b;\n    }\n\n    public int sub")
    mb, mf = _single_changed_method(BUGGY, fixed)
    assert mb.name == "add"
    assert "a - b" in mb.src and "a + b" in mf.src


def test_two_methods_changed_rejected():
    fixed = BUGGY.replace("a - b", "a + b")  # changes add and sub
    with pytest.raises(Rejection):
        _single_changed_method(BUGGY, fixed)


def test_change_outside_method_rejected():
    fixed = BUGGY.replace("private int n;", "private long n;")
    with pytest.raises(Rejection):
        _single_changed_method(BUGGY, fixed)


def test_insertion_inside_method_ok():
    fixed = BUGGY.replace(
        "public int add(int a, int b) {\n        return a - b;",
        "public int add(int a, int b) {\n        int c = a;\n        return c + b;",
    )
    mb, mf = _single_changed_method(BUGGY, fixed)
    assert mb.name == "add"


def test_no_change_rejected():
    with pytest.raises(Rejection):
        _single_changed_method(BUGGY, BUGGY)
