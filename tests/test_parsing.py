from favor.parsing import parse_response

METHOD = """public int add(int a, int b) {
    return a + b;
}"""


def test_single_fenced_block():
    text = f"Here is the fix:\n```java\n{METHOD}\n```\nDone."
    out = parse_response(text, m=1)
    assert len(out) == 1 and out[0].parse_ok
    assert "a + b" in out[0].code


def test_unfenced_method():
    out = parse_response(METHOD, m=1)
    assert out[0].parse_ok


def test_garbage_is_unparseable():
    out = parse_response("I cannot fix this, sorry!", m=1)
    assert len(out) == 1 and not out[0].parse_ok


def test_multi_with_probabilities():
    text = (
        f"Fix 1:\n```java\n{METHOD}\n```\nProbability: 0.6\n\n"
        f"Fix 2:\n```java\n{METHOD.replace('a + b', 'b + a')}\n```\nProbability: 0.3\n"
    )
    out = parse_response(text, m=3)
    ok = [p for p in out if p.parse_ok]
    assert len(ok) == 2
    assert ok[0].probability == 0.6
    assert ok[1].probability == 0.3


def test_percent_probability():
    text = f"```java\n{METHOD}\n```\nProbability: 60%\n"
    out = parse_response(text, m=2)
    assert out[0].probability == 0.6


def test_m_caps_parsed_patches():
    blocks = "\n".join(
        f"```java\n{METHOD.replace('a + b', f'a + b + {i}')}\n```" for i in range(5)
    )
    out = parse_response(blocks, m=3)
    assert sum(1 for p in out if p.parse_ok) == 3


def test_broken_block_marked_not_ok():
    text = "```java\npublic int add(int a { return; \n```"
    out = parse_response(text, m=1)
    assert not out[0].parse_ok


def test_full_class_with_one_method_is_extracted():
    text = f"```java\nclass Calc {{\n{METHOD}\n}}\n```"
    out = parse_response(text, m=1)
    assert out[0].parse_ok and "a + b" in out[0].code
