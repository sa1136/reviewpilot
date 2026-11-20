from reviewpilot.config import DEFAULT_SKIP_GLOBS
from reviewpilot.diff import build_file_diffs, number_hunks, parse_hunks, should_skip

MULTI_HUNK = """\
@@ -1,4 +1,5 @@
 import os
+import sys

 def main():
     pass
@@ -20,3 +21,4 @@ def helper():
     x = 1
-    y = 2
+    y = 3
+    z = 4
     return x"""


def build(files, max_files=40, max_patch_chars=12000):
    return build_file_diffs(files, DEFAULT_SKIP_GLOBS, max_files, max_patch_chars)


def test_multiple_hunks_numbering_and_valid_lines():
    hunks = parse_hunks(MULTI_HUNK)
    assert [(h.old_start, h.old_count, h.new_start, h.new_count) for h in hunks] == [
        (1, 4, 1, 5),
        (20, 3, 21, 4),
    ]

    numbered, valid, added = number_hunks(hunks)
    assert added == {2, 22, 23}
    assert valid == {1, 2, 3, 4, 5, 21, 22, 23, 24}
    lines = numbered.splitlines()
    assert lines[0] == "@@ -1,4 +1,5 @@"
    assert lines[2] == "    2 + import sys"
    assert "      -     y = 2" in lines
    assert "   22 +     y = 3" in lines
    assert lines[-1] == "   24       return x"


def test_added_only_new_file():
    patch = "@@ -0,0 +1,3 @@\n+a = 1\n+b = 2\n+c = 3"
    diffs, skipped = build([{"filename": "new.py", "status": "added", "patch": patch}])
    assert skipped == []
    (d,) = diffs
    assert d.valid_lines == d.added_lines == {1, 2, 3}
    assert d.numbered_patch.splitlines()[1] == "    1 + a = 1"


def test_deleted_only_hunk_has_no_valid_lines():
    patch = "@@ -5,2 +4,0 @@\n-old_a()\n-old_b()"
    hunks = parse_hunks(patch)
    numbered, valid, added = number_hunks(hunks)
    assert valid == set() and added == set()
    assert numbered.splitlines()[1:] == ["      - old_a()", "      - old_b()"]


def test_removed_file_is_skipped():
    patch = "@@ -1,2 +0,0 @@\n-x = 1\n-y = 2"
    diffs, skipped = build([{"filename": "gone.py", "status": "removed", "patch": patch}])
    assert diffs == []
    assert skipped[0].reason == "file removed"


def test_renamed_file_with_changes():
    patch = "@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2"
    files = [
        {
            "filename": "src/new_name.py",
            "previous_filename": "src/old_name.py",
            "status": "renamed",
            "patch": patch,
        }
    ]
    diffs, _ = build(files)
    (d,) = diffs
    assert d.path == "src/new_name.py"
    assert d.previous_path == "src/old_name.py"
    assert d.added_lines == {2}


def test_pure_rename_without_patch_is_skipped():
    files = [{"filename": "b.py", "previous_filename": "a.py", "status": "renamed"}]
    diffs, skipped = build(files)
    assert diffs == []
    assert "no patch" in skipped[0].reason


def test_binary_file_without_patch_is_skipped():
    diffs, skipped = build([{"filename": "data/model.bin", "status": "added"}])
    assert diffs == []
    assert "no patch" in skipped[0].reason


def test_lockfile_and_build_output_are_skipped():
    patch = "@@ -1 +1 @@\n-a\n+b"
    files = [
        {"filename": "frontend/package-lock.json", "status": "modified", "patch": patch},
        {"filename": "uv.lock", "status": "modified", "patch": patch},
        {"filename": "web/dist/app.js", "status": "modified", "patch": patch},
        {"filename": "app.py", "status": "modified", "patch": patch},
    ]
    diffs, skipped = build(files)
    assert [d.path for d in diffs] == ["app.py"]
    assert {s.path for s in skipped} == {"frontend/package-lock.json", "uv.lock", "web/dist/app.js"}
    assert all(s.reason == "matches skip rules" for s in skipped)


def test_should_skip_images_but_not_source():
    assert should_skip("assets/logo.PNG".lower(), DEFAULT_SKIP_GLOBS)
    assert should_skip("docs/plan.pdf", DEFAULT_SKIP_GLOBS)
    assert not should_skip("src/build_utils.py", DEFAULT_SKIP_GLOBS)


def test_hunk_without_counts_defaults_to_one():
    (hunk,) = parse_hunks("@@ -3 +3 @@\n-a\n+b")
    assert (hunk.old_count, hunk.new_count) == (1, 1)


def test_no_newline_marker_is_ignored():
    patch = "@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n\\ No newline at end of file"
    numbered, valid, _ = number_hunks(parse_hunks(patch))
    assert valid == {1}
    assert "No newline" not in numbered


def test_file_cap():
    patch = "@@ -1 +1 @@\n-a\n+b"
    files = [{"filename": f"f{i}.py", "status": "modified", "patch": patch} for i in range(5)]
    diffs, skipped = build(files, max_files=3)
    assert len(diffs) == 3
    assert [s.path for s in skipped] == ["f3.py", "f4.py"]


def test_patch_size_cap_keeps_whole_hunks():
    first = "@@ -1 +1 @@\n-a\n+b"
    second = "@@ -50 +50 @@\n-" + "x" * 200 + "\n+" + "y" * 200
    files = [{"filename": "big.py", "status": "modified", "patch": first + "\n" + second}]
    diffs, _ = build(files, max_patch_chars=100)
    (d,) = diffs
    assert d.truncated
    assert d.valid_lines == {1}


def test_patch_with_oversized_first_hunk_is_skipped():
    patch = "@@ -1 +1 @@\n-" + "x" * 500 + "\n+" + "y" * 500
    diffs, skipped = build(
        [{"filename": "huge.py", "status": "modified", "patch": patch}], max_patch_chars=100
    )
    assert diffs == []
    assert "patch over" in skipped[0].reason


def test_added_code_maps_added_lines_to_code():
    from reviewpilot.diff import added_code

    assert added_code(parse_hunks(MULTI_HUNK)) == {
        2: "import sys",
        22: "    y = 3",
        23: "    z = 4",
    }
