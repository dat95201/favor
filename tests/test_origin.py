from favor.origin import classify_run, origin_table


def patch(i, ch, ctx=(), named=(), fb=None, mem_all=(), mem_sel=(), l4=None):
    return {
        "bug": "B-1", "config": "CR", "run_idx": 0,
        "patch_index": i, "class_hash": ch,
        "context_patch_indexes": list(ctx),
        "feedback_named_tests": list(named),
        "feedback_line": fb,
        "memory_classes_all": list(mem_all),
        "memory_classes_selected": list(mem_sel),
        "l4": l4 or {"kind": "tests", "detail": [["T::t1", "E", "m", "1", "2", "T.java:1"]]},
    }


def test_in_context_reported():
    p0 = patch(0, "x", named=["T::t1"], fb="T::t1 fails")
    p1 = patch(1, "x", ctx=[0])
    out = classify_run([p0, p1], has_memory=False)
    assert len(out) == 1
    assert out[0]["origin"] == "in_context_reported"
    assert out[0]["earliest_index"] == 0


def test_in_context_unreported():
    p0 = patch(0, "x", named=["T::other"], fb="T::other fails")
    p1 = patch(1, "x", ctx=[0])
    out = classify_run([p0, p1], has_memory=False)
    assert out[0]["origin"] == "in_context_unreported"


def test_lost_at_restart_with_memory_split():
    p0 = patch(0, "x", named=["T::t1"], fb="...")
    p1 = patch(1, "x", ctx=[], mem_all=["x", "y"], mem_sel=["y"])
    p2 = patch(2, "x", ctx=[], mem_all=["x"], mem_sel=["x"])
    out = classify_run([p0, p1, p2], has_memory=True)
    assert out[0]["origin"] == "lost_at_restart"
    assert out[0]["memory_split"] == "in_memory_not_selected"
    assert out[1]["memory_split"] == "in_memory_selected"


def test_earliest_occurrence_is_used():
    p0 = patch(0, "x", named=["T::t1"], fb="...")
    p1 = patch(1, "x", ctx=[0])
    p2 = patch(2, "x", ctx=[1])  # earliest (0) NOT in context -> lost at restart
    out = classify_run([p0, p1, p2], has_memory=False)
    assert out[1]["origin"] == "lost_at_restart"


def test_compile_error_class_counts_feedback_as_reported():
    l4 = {"kind": "compile_error", "detail": ["cannot find symbol"]}
    p0 = patch(0, "c", fb="does not compile", l4=l4)
    p1 = patch(1, "c", ctx=[0], l4=l4)
    out = classify_run([p0, p1], has_memory=False)
    assert out[0]["origin"] == "in_context_reported"


def test_origin_table_shares():
    recs = [
        {"origin": "in_context_reported", "memory_split": None},
        {"origin": "lost_at_restart", "memory_split": "in_memory_selected"},
    ]
    t = origin_table(recs)
    assert t["l4_repeats"] == 2
    assert t["in_context_reported"] == 1
    assert t["lost_at_restart_share"] == 0.5
    assert t["in_memory_selected"] == 1
