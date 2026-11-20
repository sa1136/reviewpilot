"""Parse unified diff hunks, number new side lines, and apply skip rules.

GitHub's `patch` field for a PR file is a unified diff without the `diff --git` / `---` / `+++`
headers: it starts directly with `@@ -a,b +c,d @@`.
"""

import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import PurePosixPath

from reviewpilot.models import FileDiff, SkippedFile

HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


@dataclass
class Hunk:
    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str] = field(default_factory=list)  # raw body lines, prefix included


def parse_hunks(patch: str) -> list[Hunk]:
    hunks: list[Hunk] = []
    for line in patch.splitlines():
        match = HUNK_HEADER.match(line)
        if match:
            old_start, old_count, new_start, new_count, _ = match.groups()
            hunks.append(
                Hunk(
                    header=line,
                    old_start=int(old_start),
                    old_count=int(old_count) if old_count is not None else 1,
                    new_start=int(new_start),
                    new_count=int(new_count) if new_count is not None else 1,
                )
            )
        elif hunks:
            hunks[-1].lines.append(line)
        # Anything before the first hunk header (e.g. git headers) is ignored.
    return hunks


def number_hunks(hunks: list[Hunk]) -> tuple[str, set[int], set[int]]:
    """Render hunks with new side line numbers; return (text, valid_lines, added_lines).

    Format, one diff line per output line:
        "  12 + added code"     added line 12 on the new side
        "  13   context code"   unchanged line 13 on the new side
        "     - removed code"   deleted line, no new side number
    """
    out: list[str] = []
    valid: set[int] = set()
    added: set[int] = set()
    for hunk in hunks:
        out.append(hunk.header)
        new_line = hunk.new_start
        for raw in hunk.lines:
            if raw.startswith("\\"):  # "\ No newline at end of file"
                continue
            prefix, text = raw[:1], raw[1:]
            if prefix == "+":
                out.append(f"{new_line:>5} + {text}")
                valid.add(new_line)
                added.add(new_line)
                new_line += 1
            elif prefix == "-":
                out.append(f"{'':>5} - {text}")
            else:  # context line (" ") or an empty line inside a hunk
                out.append(f"{new_line:>5}   {text}")
                valid.add(new_line)
                new_line += 1
    return "\n".join(out), valid, added


def added_code(hunks: list[Hunk]) -> dict[int, str]:
    """New side line number -> code for every added line (without the leading `+`)."""
    out: dict[int, str] = {}
    for hunk in hunks:
        new_line = hunk.new_start
        for raw in hunk.lines:
            if raw.startswith("\\"):
                continue
            if raw.startswith("+"):
                out[new_line] = raw[1:]
            if not raw.startswith("-"):
                new_line += 1
    return out


def should_skip(path: str, skip_globs: tuple[str, ...]) -> bool:
    name = PurePosixPath(path).name
    return any(fnmatch(path, g) or fnmatch(name, g) for g in skip_globs)


def _fit_hunks(hunks: list[Hunk], max_chars: int) -> tuple[list[Hunk], bool]:
    """Keep whole hunks while the raw patch stays under max_chars."""
    kept: list[Hunk] = []
    size = 0
    for hunk in hunks:
        hunk_size = len(hunk.header) + sum(len(line) + 1 for line in hunk.lines) + 1
        if size + hunk_size > max_chars:
            return kept, True
        kept.append(hunk)
        size += hunk_size
    return kept, False


def build_file_diffs(
    files: list[dict],
    skip_globs: tuple[str, ...],
    max_files: int,
    max_patch_chars: int,
) -> tuple[list[FileDiff], list[SkippedFile]]:
    """Turn GitHub's PR file list into reviewable FileDiffs plus a list of what was skipped."""
    diffs: list[FileDiff] = []
    skipped: list[SkippedFile] = []
    for f in files:
        path = f["filename"]
        status = f.get("status", "modified")
        patch = f.get("patch")

        if should_skip(path, skip_globs):
            skipped.append(SkippedFile(path=path, reason="matches skip rules"))
            continue
        if status == "removed":
            skipped.append(SkippedFile(path=path, reason="file removed"))
            continue
        if not patch:
            skipped.append(
                SkippedFile(path=path, reason="no patch (binary, too large, or pure rename)")
            )
            continue
        if len(diffs) >= max_files:
            skipped.append(SkippedFile(path=path, reason=f"over the {max_files} file limit"))
            continue

        hunks, truncated = _fit_hunks(parse_hunks(patch), max_patch_chars)
        if not hunks:
            skipped.append(SkippedFile(path=path, reason=f"patch over {max_patch_chars} chars"))
            continue

        numbered, valid, added = number_hunks(hunks)
        diffs.append(
            FileDiff(
                path=path,
                status=status,
                patch=patch,
                numbered_patch=numbered,
                valid_lines=valid,
                added_lines=added,
                added_code=added_code(hunks),
                previous_path=f.get("previous_filename"),
                truncated=truncated,
            )
        )
    return diffs, skipped
