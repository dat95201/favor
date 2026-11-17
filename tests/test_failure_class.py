from favor.failure_class import (
    class_for_compile_error,
    class_for_failures,
    class_hash,
    normalize_compiler_error,
    parse_failing_tests,
)

FAILING_TESTS = """--- com.google.javascript.jscomp.TypeCheckTest::testBadInterface
junit.framework.AssertionFailedError: expected:<3> but was:<2>
\tat junit.framework.Assert.fail(Assert.java:47)
\tat junit.framework.Assert.assertEquals(Assert.java:280)
\tat com.google.javascript.jscomp.TypeCheckTest.testBadInterface(TypeCheckTest.java:4312)
--- org.apache.commons.lang.LocaleUtilsTest::testToLocale
java.lang.IllegalArgumentException: Invalid locale format: _GB
\tat org.apache.commons.lang.LocaleUtils.toLocale(LocaleUtils.java:116)
\tat org.apache.commons.lang.LocaleUtilsTest.testToLocale(LocaleUtilsTest.java:97)
"""


def test_parse_two_blocks():
    sigs = parse_failing_tests(FAILING_TESTS)
    assert len(sigs) == 2
    a, b = sigs
    assert a.test_id.endswith("testBadInterface")
    assert a.exception_type == "junit.framework.AssertionFailedError"
    assert a.expected == "3" and a.actual == "2"
    assert a.location == "TypeCheckTest.java:4312"
    assert b.exception_type == "java.lang.IllegalArgumentException"
    assert b.expected is None
    assert b.location == "LocaleUtilsTest.java:97"


def test_signature_separates_expected_actual():
    other = FAILING_TESTS.replace("but was:<2>", "but was:<7>")
    c1 = class_for_failures(parse_failing_tests(FAILING_TESTS))
    c2 = class_for_failures(parse_failing_tests(other))
    assert c1 != c2
    assert class_hash(c1) != class_hash(c2)


def test_class_is_order_insensitive():
    sigs = parse_failing_tests(FAILING_TESTS)
    assert class_for_failures(sigs) == class_for_failures(list(reversed(sigs)))


def test_failing_test_ids():
    c = class_for_failures(parse_failing_tests(FAILING_TESTS))
    assert c.failing_test_ids() == {
        "com.google.javascript.jscomp.TypeCheckTest::testBadInterface",
        "org.apache.commons.lang.LocaleUtilsTest::testToLocale",
    }


JAVAC = """    [javac] /work/Lang-1b/src/main/java/org/apache/commons/lang3/Foo.java:42: error: cannot find symbol
    [javac]         int x = size;
    [javac]                 ^
    [javac]   symbol:   variable size
"""


def test_compiler_error_normalization():
    msg = normalize_compiler_error(JAVAC)
    assert msg == "cannot find symbol: variable size"


def test_compile_classes_by_error():
    c1 = class_for_compile_error(JAVAC)
    c2 = class_for_compile_error(JAVAC.replace("size", "length"))
    assert c1 != c2
    assert c1.kind == "compile_error"


def test_message_normalization_strips_ids_and_paths():
    text = """--- a.b.CTest::t
java.lang.RuntimeException: object a.b.C@1f2e3d at /tmp/x/y.txt broke
\tat a.b.CTest.t(CTest.java:5)
"""
    sig = parse_failing_tests(text)[0]
    assert "@1f2e3d" not in sig.message_norm
    assert "/tmp/x" not in sig.message_norm
