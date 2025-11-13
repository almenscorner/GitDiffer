#!/usr/bin/env python3

import re
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Any, Optional
from collections import defaultdict
import subprocess
import json
import os
import html
import sys
from enum import Enum
from argparse import ArgumentParser

parser = ArgumentParser(description="Parse git unified diffs into structured changes.")
parser.add_argument("--repo-path", help="Path to the git repository")
parser.add_argument("--compare-file-1", help="First file to compare")
parser.add_argument("--compare-file-2", help="Second file to compare")
parser.add_argument(
    "--unified", type=int, default=0, help="Number of unified diff context lines"
)
parser.add_argument(
    "--output-json", nargs="?", default=None, help="Optional output JSON file path"
)
parser.add_argument(
    "--suppress-deleted-files",
    action="store_true",
    help="Suppress fully deleted files in output",
)
parser.add_argument(
    "--diff-file",
    help="Path to file containing raw unified diff text (or '-' to read from stdin)",
)

args = parser.parse_args()

DIFF_GIT = re.compile(r"^\s*diff --git a/(.+?) b/(.+?)\s*$")
OLD_FILE_HDR = re.compile(r"^\s*---\s+(?:a/|/dev/null)(.+?)\s*$")
NEW_FILE_HDR = re.compile(r"^\s*\+\+\+\s+(?:b/|/dev/null)(.+?)\s*$")
HUNK_HEADER = re.compile(r"^\s*@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@")

DOCKER_INSTR = re.compile(r"^\s*([A-Z]+)\b(.*)$")
KV_PATTERNS = [re.compile(r'^\s*"?([A-Za-z0-9_.-]+)"?\s*[:=]\s*(.+?)\s*$')]
JSON_PAIR = re.compile(r'^\s*"([A-Za-z0-9_.-]+)"\s*:\s*(.+?)(,?\s*)$')

PLIST_KEY_RE = re.compile(r"<\s*key\s*>\s*([^<]+?)\s*<\s*/\s*key\s*>")
PLIST_STRING_RE = re.compile(r"<\s*string\s*>\s*(.*?)\s*<\s*/\s*string\s*>")

OLD_FILE_HDR = re.compile(r"^\s*---\s+(?:a/)?([^\t]+)")
NEW_FILE_HDR = re.compile(r"^\s*\+\+\+\s+(?:b/)?([^\t]+)")

_METADATA_PREFIXES = (
    "index ",
    "Index ",
    "rename ",
    "similarity ",
    "new file mode ",
    "deleted file mode ",
    "copy from ",
    "copy to ",
    "old mode ",
    "new mode ",
)


class ChangeType(str, Enum):
    DELETE = "delete"
    INSERT = "insert"
    REPLACE = "replace"
    UNKNOWN = "unknown"
    DELETE_SUMMARY = "delete-summary"
    FILE_DELETED = "file-deleted"


class ChangeContext(str, Enum):
    BLOCK = "block"
    KV = "kv"
    JSON_LIKE = "json-like"
    DOCKER = "docker"
    LINE = "line"
    PLIST_KV = "plist-kv"


def print_changes(changes, expand_tabs=True, tabsize=4):
    def r(lines):
        if not lines:
            return ""
        return (
            "\n".join([ln.expandtabs(tabsize) for ln in lines])
            if expand_tabs
            else "\n".join(lines)
        )

    for c in changes:
        print(f"\n--- {c.file} [{c.context}] old@{c.line_old} new@{c.line_new} ---")
        old_block = getattr(c, "old_lines", None) or (
            c.old.split("\n") if c.old else []
        )
        new_block = getattr(c, "new_lines", None) or (
            c.new.split("\n") if c.new else []
        )
        print("Property: " + (c.property or ""))
        if old_block:
            print("OLD: " + r(old_block))
        if new_block:
            print("NEW: " + r(new_block))
        print("-" * 60)


@dataclass
class Change:
    file: str
    property: Optional[str]
    old: Optional[str]
    new: Optional[str]
    line_old: Optional[int]
    line_new: Optional[int]
    context: str  # string for compatibility
    old_lines: List[str] = field(default_factory=list)
    new_lines: List[str] = field(default_factory=list)
    change_type: str = ChangeType.UNKNOWN.value
    # commit context (optional if available)
    commit_hash: Optional[str] = None
    commit_author: Optional[str] = None
    commit_date: Optional[str] = None
    commit_message: Optional[str] = None

    def to_dict(self, expand_tabs: bool = True, tabsize: int = 4) -> Dict[str, Any]:
        d = asdict(self)
        if self.context == ChangeContext.BLOCK.value:
            if self.old_lines:
                d["old_lines"] = [
                    (ln.expandtabs(tabsize) if expand_tabs else ln)
                    for ln in self.old_lines
                ]
            if self.new_lines:
                d["new_lines"] = [
                    (ln.expandtabs(tabsize) if expand_tabs else ln)
                    for ln in self.new_lines
                ]
            d.pop("old", None)
            d.pop("new", None)
        else:
            d.pop("old_lines", None)
            d.pop("new_lines", None)
        return d


def to_pretty_json(changes, expand_tabs=True, tabsize=4):
    """Serialize changes; commit metadata only lives on each change object."""
    change_list = [c.to_dict(expand_tabs, tabsize) for c in changes]
    return json.dumps(change_list, indent=2, ensure_ascii=False)


def filter_or_summarize_deletes(
    changes: List[Change], hide_over_lines: Optional[int] = 20, summarize: bool = True
) -> List[Change]:
    out: List[Change] = []
    for c in changes:
        if (
            c.context == ChangeContext.BLOCK.value
            and c.change_type == ChangeType.DELETE.value
        ):
            size = len(c.old_lines)
            starts_at_top = c.line_old == 1
            if (
                hide_over_lines is not None and size >= hide_over_lines
            ) or starts_at_top:
                if summarize:
                    out.append(
                        Change(
                            file=c.file,
                            property=None,
                            old=None,
                            new=None,
                            line_old=c.line_old,
                            line_new=c.line_new,
                            context=ChangeContext.BLOCK.value,
                            old_lines=[],
                            new_lines=[],
                            change_type=ChangeType.DELETE_SUMMARY.value,
                        )
                    )
                continue
        out.append(c)
    return out


def suppress_fully_deleted_files(
    changes: List[Change], emit_summary: bool = False
) -> List[Change]:
    file_blocks = defaultdict(list)
    for ch in changes:
        file_blocks[ch.file].append(ch)

    out: List[Change] = []
    for file, blocks in file_blocks.items():
        all_deleted = all(b.change_type == ChangeType.DELETE.value for b in blocks)
        if all_deleted:
            if emit_summary:
                out.append(
                    Change(
                        file=file,
                        property=None,
                        old=None,
                        new=None,
                        line_old=1,
                        line_new=None,
                        context=ChangeContext.BLOCK.value,
                        old_lines=[],
                        new_lines=[],
                        change_type=ChangeType.FILE_DELETED.value,
                    )
                )
            continue
        out.extend(blocks)
    return out


def _make_change(
    file: str,
    old_lines: List[str],
    new_lines: List[str],
    line_old: Optional[int],
    line_new: Optional[int],
    commit_ctx: Optional[Dict[str, Optional[str]]] = None,
) -> Change:
    if old_lines and new_lines:
        ctype = ChangeType.REPLACE.value
    elif old_lines and not new_lines:
        ctype = ChangeType.DELETE.value
    elif new_lines and not old_lines:
        ctype = ChangeType.INSERT.value
    else:
        ctype = ChangeType.UNKNOWN.value
    commit_ctx = commit_ctx or {}
    return Change(
        file=(file or "").strip(),
        property=None,
        old="\n".join(old_lines) if old_lines else None,
        new="\n".join(new_lines) if new_lines else None,
        line_old=line_old,
        line_new=line_new,
        context=ChangeContext.BLOCK.value,
        old_lines=old_lines[:],
        new_lines=new_lines[:],
        change_type=ctype,
        commit_hash=commit_ctx.get("hash"),
        commit_author=commit_ctx.get("author"),
        commit_date=commit_ctx.get("date"),
        commit_message=commit_ctx.get("message"),
    )


class DiffWithCommitParser:
    def __init__(self, diff_text: str):
        self.lines = diff_text.splitlines()
        self.changes: List[Change] = []
        self.cur_file: Optional[str] = None
        self.old_line: Optional[int] = None
        self.new_line: Optional[int] = None
        self.block_removed: List[str] = []
        self.block_added: List[str] = []
        self.block_old_start: Optional[int] = None
        self.block_new_start: Optional[int] = None
        self.current_commit: Dict[str, Optional[str]] = {}
        # Only capture commit hash/author/date; message handled later via git or ignored.
        # Enhanced attribution: support diff blocks that appear BEFORE their commit header.
        # We buffer such changes in pending_changes and assign them when the commit header arrives.
        # If a commit header appears with no pending changes, we switch to forward assignment mode
        # (standard git log -p ordering) and attribute subsequent hunks immediately.
        self.pending_changes: List[Change] = []
        self.forward_assignment: bool = False

    def parse(self) -> List[Change]:
        for raw in self.lines:
            line = raw.rstrip("\n")
            m_commit = _COMMIT_LINE.match(line)
            if m_commit:
                self._flush_block()
                commit_obj = {
                    "hash": m_commit.group(1),
                    "author": None,
                    "date": None,
                    "message": None,
                }
                if self.pending_changes:
                    # Assign buffered (previous) diff hunks to this commit (commit-after-diff ordering)
                    for ch in self.pending_changes:
                        ch.commit_hash = commit_obj["hash"]
                    self.pending_changes.clear()
                    # This commit header acted as a closing header; do not enable forward assignment yet.
                    self.forward_assignment = False
                    self.current_commit = commit_obj
                else:
                    # Standard ordering: commit header precedes its hunks.
                    self.forward_assignment = True
                    self.current_commit = commit_obj
                continue
            if self.current_commit:
                m_author = _AUTHOR_LINE.match(line)
                if m_author:
                    self.current_commit["author"] = m_author.group(1).strip()
                    # Update author for already-emitted changes of this commit lacking author
                    for ch in self.changes:
                        if (
                            ch.commit_hash == self.current_commit.get("hash")
                            and not ch.commit_author
                        ):
                            ch.commit_author = self.current_commit["author"]
                    for ch in self.pending_changes:
                        if (
                            ch.commit_hash == self.current_commit.get("hash")
                            and not ch.commit_author
                        ):
                            ch.commit_author = self.current_commit["author"]
                    continue
                m_date = _DATE_LINE.match(line)
                if m_date:
                    self.current_commit["date"] = m_date.group(1).strip()
                    for ch in self.changes:
                        if (
                            ch.commit_hash == self.current_commit.get("hash")
                            and not ch.commit_date
                        ):
                            ch.commit_date = self.current_commit["date"]
                    for ch in self.pending_changes:
                        if (
                            ch.commit_hash == self.current_commit.get("hash")
                            and not ch.commit_date
                        ):
                            ch.commit_date = self.current_commit["date"]
                    continue

            if self._try_start_file(line):
                continue
            if self._is_metadata(line):
                continue
            if self._try_hunk(line):
                continue
            if not self._in_hunk():
                continue
            self._consume_line(line)
        self._flush_block()
        return self.changes

    def _try_start_file(self, line: str) -> bool:
        m_git = DIFF_GIT.match(line)
        if m_git:
            self._reset_file()
            self.cur_file = m_git.group(2).strip()
            return True
        m_old = OLD_FILE_HDR.match(line)
        if m_old:
            self._reset_hunk()
            if not self.cur_file:
                self.cur_file = m_old.group(1).strip()
            return True
        m_new = NEW_FILE_HDR.match(line)
        if m_new:
            self._reset_hunk()
            self.cur_file = m_new.group(1).strip()
            return True
        return False

    def _is_metadata(self, line: str) -> bool:
        return any(line.lstrip().startswith(p) for p in _METADATA_PREFIXES)

    def _try_hunk(self, line: str) -> bool:
        m_hunk = HUNK_HEADER.match(line)
        if m_hunk:
            self._flush_block()
            self.old_line = int(m_hunk.group(1))
            self.new_line = int(m_hunk.group(3))
            return True
        return False

    def _in_hunk(self) -> bool:
        return (
            self.cur_file is not None
            and self.old_line is not None
            and self.new_line is not None
        )

    def _consume_line(self, line: str):
        s = line.lstrip()
        if s.startswith("-"):
            if not self.block_removed and not self.block_added:
                self.block_old_start = self.old_line
            self.block_removed.append(s[1:])
            self.old_line += 1
        elif s.startswith("+"):
            if not self.block_removed and not self.block_added:
                self.block_new_start = self.new_line
            if self.block_removed and self.block_new_start is None:
                self.block_new_start = self.new_line
            self.block_added.append(s[1:])
            self.new_line += 1
        else:
            self._flush_block()
            self.old_line += 1
            self.new_line += 1

    def _flush_block(self):
        if not self.block_removed and not self.block_added:
            return
        change = _make_change(
            self.cur_file or "",
            self.block_removed,
            self.block_added,
            self.block_old_start,
            self.block_new_start,
            commit_ctx=(
                self.current_commit
                if self.forward_assignment and self.current_commit
                else None
            ),
        )
        self.changes.append(change)
        if not self.forward_assignment:
            # Keep for potential commit header appearing later.
            self.pending_changes.append(change)
        self.block_removed.clear()
        self.block_added.clear()
        self.block_old_start = None
        self.block_new_start = None

    def _reset_hunk(self):
        self.old_line = None
        self.new_line = None

    def _reset_file(self):
        self._flush_block()
        self._reset_hunk()


def parse_unified_diff(diff_text: str) -> List[Change]:
    return DiffWithCommitParser(diff_text).parse()


_COMMIT_LINE = re.compile(r"^\s*commit\s+([0-9a-fA-F]{7,40})\s*$")
_AUTHOR_LINE = re.compile(r"^\s*Author:\s*(.+)$")
_DATE_LINE = re.compile(r"^\s*Date:\s*(.+)$")


def parse_commit_metadata(diff_text: str) -> List[Dict[str, Any]]:
    """Extract commit metadata (hash/author/date/message) from a git log -p style text.

    Logic:
    - Detect 'commit <hash>' lines.
    - Capture Author/Date.
    - Commit message lines are those starting with exactly 4 spaces (git default) after a blank separator.
    - Stop message collection at first line beginning with 'diff --git ' OR next 'commit '.
    - Ignore diff content so message doesn't absorb patch text.
    - Body lines keep internal blank lines (represented as '').
    """
    lines = diff_text.splitlines()
    commits: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    collecting = False
    message_lines: List[str] = []
    for i, raw in enumerate(lines):
        line = raw.rstrip("\n")
        m_commit = _COMMIT_LINE.match(line)
        if m_commit:
            # finalize previous
            if current:
                # Filter out any diff/patch lines accidentally captured
                filtered: List[str] = []
                for ml in message_lines:
                    if (
                        ml.startswith("diff --git ")
                        or ml.startswith("index ")
                        or ml.startswith("@@ ")
                        or ml.startswith("--- ")
                        or ml.startswith("+++ ")
                    ):
                        break
                    if ml.startswith("+") or ml.startswith("-"):
                        break
                    filtered.append(ml)
                current["message"] = "\n".join(filtered).strip()
                commits.append(current)
            current = {
                "hash": m_commit.group(1),
                "author": None,
                "date": None,
                "message": "",
            }
            collecting = False
            message_lines = []
            continue
        if not current:
            continue
        # Author / Date
        m_author = _AUTHOR_LINE.match(line)
        if m_author:
            current["author"] = m_author.group(1).strip()
            continue
        m_date = _DATE_LINE.match(line)
        if m_date:
            current["date"] = m_date.group(1).strip()
            continue
        # End conditions for message collection
        if line.startswith("diff --git "):
            if collecting:
                collecting = False
            continue
        if _COMMIT_LINE.match(line):  # safety (should have matched earlier)
            if collecting:
                collecting = False
            continue
        # Decide when to start collecting: a blank line followed by a 4-space indented line.
        if not collecting:
            if (
                line.strip() == ""
                and i + 1 < len(lines)
                and lines[i + 1].startswith("    ")
            ):
                collecting = True
            continue
        # We are collecting: accept only lines starting with 4 spaces OR blank lines.
        if line.startswith("    "):
            message_lines.append(line[4:])
        elif line.strip() == "":
            message_lines.append("")
        else:
            # Non-indented non-blank line ends message section.
            collecting = False
    # finalize last
    if current:
        filtered = []
        for ml in message_lines:
            if (
                ml.startswith("diff --git ")
                or ml.startswith("index ")
                or ml.startswith("@@ ")
                or ml.startswith("--- ")
                or ml.startswith("+++ ")
            ):
                break
            if ml.startswith("+") or ml.startswith("-"):
                break
            filtered.append(ml)
        current["message"] = "\n".join(filtered).strip()
        commits.append(current)
    return commits


def strip_trailing_comma(s: str) -> str:
    return s[:-1] if s.endswith(",") else s


def strip_quotes(s: str) -> str:
    if (s.startswith('"') and s.endswith('"')) or (
        s.startswith("'") and s.endswith("'")
    ):
        return s[1:-1]
    return s


def list_changed_files(repo_path: str) -> List[dict]:
    """
    Returns [{'status':'M|A|D|R', 'old':<old or None>, 'new':<new>}]
    Uses -z to be robust to spaces/tabs, and --find-renames to track moves.
    """
    cmd = [
        "git",
        "-C",
        repo_path,
        "diff",
        "--name-status",
        "-z",
        "--find-renames",
        "--diff-filter=ACDMRT",
    ]
    out = subprocess.run(cmd, capture_output=True, text=False).stdout  # bytes
    parts = out.split(b"\x00")
    i = 0
    recs: List[dict] = []
    while i < len(parts) and parts[i]:
        status = parts[i].decode("utf-8", errors="replace")
        i += 1
        if status.startswith("R"):  # e.g., R100
            oldp = parts[i].decode("utf-8", errors="replace")
            i += 1
            newp = parts[i].decode("utf-8", errors="replace")
            i += 1
            recs.append({"status": "R", "old": oldp, "new": newp})
        else:
            path = parts[i].decode("utf-8", errors="replace")
            i += 1
            s = status[0]
            if s in ("M", "A"):
                recs.append({"status": s, "old": None, "new": path})
            elif s == "D":
                recs.append({"status": "D", "old": path, "new": path})
            else:
                recs.append({"status": status, "old": None, "new": path})
    return recs


def batch_paths(paths: List[str], batch_size: int = 200) -> List[List[str]]:
    return [paths[i : i + batch_size] for i in range(0, len(paths), batch_size)]


def simplify_single_line_replacements(changes: List[Change]) -> List[Change]:
    simplified: List[Change] = []
    for c in changes:
        is_single_old = len(c.old_lines) == 1
        is_single_new = len(c.new_lines) == 1
        single_line_mutation = c.change_type in (
            ChangeType.REPLACE.value,
            ChangeType.INSERT.value,
            ChangeType.DELETE.value,
        ) and (is_single_old or is_single_new)

        if single_line_mutation:
            old_line = c.old_lines[0].strip() if is_single_old else None
            new_line = c.new_lines[0].strip() if is_single_new else None

            def match_kv(s: Optional[str]):
                if s is None:
                    return None
                # Try JSON "key": value first, then generic key: value / key=value
                for pat in [JSON_PAIR] + KV_PATTERNS:
                    m = pat.match(s)
                    if m:
                        key = m.group(1)
                        val = m.group(2).strip().rstrip(",")
                        return key, val
                return None

            m_old = match_kv(old_line)
            m_new = match_kv(new_line)

            # Promote to KV if either side matches (and keys align when both present)
            if (
                (m_old and m_new and m_old[0] == m_new[0])
                or (m_old and not m_new)
                or (m_new and not m_old)
            ):
                key = (m_new or m_old)[0]
                old_val = m_old[1] if m_old else None
                new_val = m_new[1] if m_new else None
                simplified.append(
                    Change(
                        file=c.file,
                        property=key,
                        old=old_val,
                        new=new_val,
                        line_old=c.line_old,
                        line_new=c.line_new,
                        context=ChangeContext.KV.value,
                        change_type=c.change_type,
                    )
                )
                continue

        # Fallback to existing REPLACE simplification (unchanged behavior)
        if (
            c.change_type == ChangeType.REPLACE.value
            and len(c.old_lines) == 1
            and len(c.new_lines) == 1
        ):
            old_line = c.old_lines[0].strip()
            new_line = c.new_lines[0].strip()
            for pat in KV_PATTERNS + [JSON_PAIR]:
                mo_old = pat.match(old_line)
                mo_new = pat.match(new_line)
                if mo_old and mo_new and mo_old.group(1) == mo_new.group(1):
                    key = mo_old.group(1)
                    old_val = mo_old.group(2).strip().rstrip(",")
                    new_val = mo_new.group(2).strip().rstrip(",")
                    simplified.append(
                        Change(
                            file=c.file,
                            property=key,
                            old=old_val,
                            new=new_val,
                            line_old=c.line_old,
                            line_new=c.line_new,
                            context=ChangeContext.KV.value,
                            change_type=ChangeType.REPLACE.value,
                        )
                    )
                    break
            else:
                simplified.append(c)
        else:
            simplified.append(c)
    return simplified


def _read_file_lines(repo_path: str, rel_path: str) -> Optional[List[str]]:
    """
    Read working-tree file lines as fallback. If it doesn't exist (deleted),
    try HEAD version via `git show` to get context.
    """
    abs_path = os.path.join(repo_path, rel_path)
    if os.path.exists(abs_path):
        try:
            with open(abs_path, "r", encoding="utf-8") as fh:
                return fh.read().splitlines()
        except UnicodeDecodeError:
            return None
    try:
        out = subprocess.run(
            ["git", "-C", repo_path, "show", f"HEAD:{rel_path}"],
            capture_output=True,
            text=True,
        ).stdout
        return out.splitlines()
    except Exception:
        return None


def _find_preceding_plist_key(
    lines: List[str], start_line_1_based: int, search_back: int = 20
) -> Optional[str]:
    """
    Search upward from (1-based) start_line for the nearest <key>...</key>.
    """
    if not lines or start_line_1_based is None:
        return None
    i = max(0, start_line_1_based - 2)
    lo = max(0, i - search_back)
    while i >= lo:
        m = PLIST_KEY_RE.search(lines[i])
        if m:
            return m.group(1).strip()
        i -= 1
    return None


def annotate_plist_properties(changes: List[Change], repo_path: str) -> List[Change]:
    """
    For single-line <string> changes in plist-like XML, attach `property` from nearest <key>,
    normalize entity-escaped values, and set context to 'plist-kv'.
    """
    out: List[Change] = []
    cache: Dict[str, Optional[List[str]]] = {}
    for ch in changes:
        if (
            ch.context != ChangeContext.BLOCK.value
            or len(ch.old_lines) not in (0, 1)
            or len(ch.new_lines) not in (0, 1)
        ):
            out.append(ch)
            continue

        old_str = None
        new_str = None
        if ch.old_lines:
            mo = PLIST_STRING_RE.search(ch.old_lines[0].strip())
            if mo:
                old_str = html.unescape(mo.group(1))
        if ch.new_lines:
            mn = PLIST_STRING_RE.search(ch.new_lines[0].strip())
            if mn:
                new_str = html.unescape(mn.group(1))

        if old_str is None and new_str is None:
            out.append(ch)
            continue

        if ch.file not in cache:
            cache[ch.file] = _read_file_lines(repo_path, ch.file)
        file_lines = cache[ch.file]
        if not file_lines:
            out.append(ch)
            continue

        anchor_line = ch.line_new if ch.line_new is not None else ch.line_old
        key = _find_preceding_plist_key(file_lines, anchor_line or 1)
        if not key and anchor_line:
            upper = min(len(file_lines) - 1, (anchor_line - 1) + 3)
            for j in range(anchor_line - 1, upper + 1):
                m2 = PLIST_KEY_RE.search(file_lines[j])
                if m2:
                    key = m2.group(1).strip()
                    break

        if key:
            out.append(
                Change(
                    file=ch.file,
                    property=key,
                    old=old_str,
                    new=new_str,
                    line_old=ch.line_old,
                    line_new=ch.line_new,
                    context=ChangeContext.PLIST_KV.value,
                    old_lines=ch.old_lines,
                    new_lines=ch.new_lines,
                    change_type=ch.change_type,
                )
            )
        else:
            out.append(ch)
    return out


def remove_trivial_structure_lines(changes: List[Change]) -> List[Change]:
    structural_line = re.compile(
        r"^\s*[\)\]\}]+;?\s*$"
    )  # only closing brackets, optional semicolon
    cleaned: List[Change] = []
    for ch in changes:
        if ch.context != ChangeContext.BLOCK.value or ch.change_type not in (
            ChangeType.INSERT.value,
            ChangeType.DELETE.value,
            ChangeType.REPLACE.value,
        ):
            cleaned.append(ch)
            continue

        old_filtered = [
            ln
            for ln in (ch.old_lines or [])
            if ln.strip() and not structural_line.match(ln)
        ]
        new_filtered = [
            ln
            for ln in (ch.new_lines or [])
            if ln.strip() and not structural_line.match(ln)
        ]

        # If nothing changed, keep as-is
        if old_filtered == (ch.old_lines or []) and new_filtered == (
            ch.new_lines or []
        ):
            cleaned.append(ch)
            continue

        # If both sides become empty, drop this change
        if not old_filtered and not new_filtered:
            continue

        # Recompute change_type after filtering
        if old_filtered and new_filtered:
            ctype = ChangeType.REPLACE.value
        elif old_filtered:
            ctype = ChangeType.DELETE.value
        else:
            ctype = ChangeType.INSERT.value

        cleaned.append(
            Change(
                file=ch.file,
                property=ch.property,
                old="\n".join(old_filtered) if old_filtered else None,
                new="\n".join(new_filtered) if new_filtered else None,
                line_old=ch.line_old,
                line_new=ch.line_new,
                context=ch.context,
                old_lines=old_filtered,
                new_lines=new_filtered,
                change_type=ctype,
            )
        )
    return cleaned


def diff_batch(repo_path: str, paths: List[str], unified: int = 0) -> str:
    if not paths:
        return ""
    cmd = [
        "git",
        "-C",
        repo_path,
        "diff",
        f"--unified={unified}",
        "--no-color",
        "--no-ext-diff",
        "--minimal",
        "--",
        *paths,
    ]
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def diff_files(file1: str, file2: str, unified: int = 0) -> str:
    cmd = [
        "diff",
        f"-U{unified}",
        file1,
        file2,
    ]
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def enrich_with_neighbor_context(
    changes: List[Change], repo_path: str, before: int = 1, after: int = 1
) -> List[Change]:
    cache: Dict[str, Optional[List[str]]] = {}
    out: List[Change] = []
    for ch in changes:
        if ch.context != ChangeContext.BLOCK.value:
            out.append(ch)
            continue
        if ch.file not in cache:
            cache[ch.file] = _read_file_lines(repo_path, ch.file)
        lines = cache[ch.file]
        if not lines:
            out.append(ch)
            continue
        start = (ch.line_new if ch.line_new is not None else ch.line_old) or 1
        # 1-based to 0-based
        idx = max(0, start - 1)
        pre = lines[max(0, idx - before) : idx]
        post = (
            lines[idx + len(ch.new_lines) : idx + len(ch.new_lines) + after]
            if ch.new_lines
            else lines[idx : idx + after]
        )
        # Attach as synthetic properties
        ch_extra = Change(
            file=ch.file,
            property=ch.property,
            old=ch.old,
            new=ch.new,
            line_old=ch.line_old,
            line_new=ch.line_new,
            context=ch.context,
            old_lines=ch.old_lines,
            new_lines=ch.new_lines,
            change_type=ch.change_type,
        )
        # Embed context lines via property extensions (or add new fields if desired)
        if pre:
            ch_extra.old_lines = pre + ch_extra.old_lines
        if post:
            ch_extra.new_lines = ch_extra.new_lines + post
        out.append(ch_extra)
    return out


def process_changes(
    changes: List[Change],
    repo_path: str,
    suppress_deleted_files: bool = True,
    add_context: bool = True,
) -> List[Change]:
    # Remove exact duplicate change blocks (same file, lines, content, property)
    changes = deduplicate_repeated_changes(changes)
    changes = remove_trivial_structure_lines(changes)
    changes = annotate_plist_properties(changes, repo_path=repo_path)
    if suppress_deleted_files:
        changes = suppress_fully_deleted_files(changes, emit_summary=True)
    changes = simplify_single_line_replacements(changes)
    if add_context:
        changes = enrich_with_neighbor_context(changes, repo_path, before=1, after=1)
    return changes


def deduplicate_repeated_changes(changes: List[Change]) -> List[Change]:
    """Collapse identical changes that appear multiple times for the same file.

    Two changes are considered identical if all of these match:
      - file
      - change_type
      - context
      - property
      - line_old / line_new (may be None)
      - old_lines content sequence
      - new_lines content sequence
      - (for non-block contexts) old/new scalar values
    """
    seen = set()
    deduped: List[Change] = []
    for ch in changes:
        key = (
            ch.file,
            ch.change_type,
            ch.context,
            ch.property,
            ch.line_old,
            ch.line_new,
            tuple(ch.old_lines),
            tuple(ch.new_lines),
            ch.old if ch.context != ChangeContext.BLOCK.value else None,
            ch.new if ch.context != ChangeContext.BLOCK.value else None,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ch)
    return deduped


def enrich_missing_commit_info(changes: List[Change], repo_path: str) -> List[Change]:
    """Populate commit_* fields for changes that lack them by querying last commit per file.

    Uses `git log -n 1 -- <file>`; if a file has no history (new, unstaged), leaves fields None.
    """
    if not repo_path:
        return changes
    unique_files = {c.file for c in changes if c.file}
    commit_cache: Dict[str, Dict[str, Optional[str]]] = {}
    for fp in unique_files:
        if fp in commit_cache:
            continue
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    repo_path,
                    "log",
                    "-n",
                    "1",
                    "--pretty=format:%H%n%an <%ae>%n%ad%n%s%n%b",
                    "--",
                    fp,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.stdout.strip():
                lines = result.stdout.splitlines()
                commit_cache[fp] = {
                    "hash": lines[0] if len(lines) > 0 else None,
                    "author": lines[1] if len(lines) > 1 else None,
                    "date": lines[2] if len(lines) > 2 else None,
                    "message": "\n".join(lines[3:]).strip() if len(lines) > 3 else None,
                }
            else:
                commit_cache[fp] = {}
        except Exception:
            commit_cache[fp] = {}
    for ch in changes:
        if not ch.commit_hash and ch.file in commit_cache:
            meta = commit_cache[ch.file]
            ch.commit_hash = meta.get("hash")
            ch.commit_author = meta.get("author")
            ch.commit_date = meta.get("date")
            ch.commit_message = meta.get("message")
    return changes


def override_commit_messages_subject_only(
    changes: List[Change], repo_path: Optional[str]
) -> List[Change]:
    """Replace any existing commit_message with the commit subject only (first line).

    This queries git for each unique commit_hash. If repo_path is not a git repo
    or the hash cannot be resolved, leaves the existing message unchanged.
    """
    if not changes:
        return changes
    if not repo_path:
        repo_path = "."
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        return changes
    unique_hashes = {c.commit_hash for c in changes if c.commit_hash}
    subject_cache: Dict[str, str] = {}
    for h in unique_hashes:
        if h in subject_cache:
            continue
        try:
            result = subprocess.run(
                ["git", "-C", repo_path, "show", "-s", "--format=%s", h],
                capture_output=True,
                text=True,
                check=False,
            )
            subject_cache[h] = (
                result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
            )
        except Exception:
            subject_cache[h] = ""
    for ch in changes:
        if (
            ch.commit_hash
            and ch.commit_hash in subject_cache
            and subject_cache[ch.commit_hash]
        ):
            ch.commit_message = subject_cache[ch.commit_hash]
    return changes


def trim_commit_messages_to_subject(changes: List[Change]) -> List[Change]:
    """Ensure commit_message is only the first line (subject)."""
    for c in changes:
        if c.commit_message:
            first = c.commit_message.splitlines()[0].strip()
            c.commit_message = first
    return changes


def consolidate_file_commit_attribution(changes: List[Change]) -> List[Change]:
    """Heuristic (two-pass style) commit attribution fix."""
    by_file: Dict[str, List[Change]] = {}
    for ch in changes:
        by_file.setdefault(ch.file, []).append(ch)
    for file, file_changes in by_file.items():
        # Find last change with a commit hash
        last_with_commit = None
        for fc in reversed(file_changes):
            if fc.commit_hash:
                last_with_commit = fc
                break
        if not last_with_commit:
            continue
        target_hash = last_with_commit.commit_hash
        target_author = last_with_commit.commit_author
        target_date = last_with_commit.commit_date
        target_message = last_with_commit.commit_message
        # If more than one distinct commit present OR some changes lack hash, unify.
        distinct_hashes = {c.commit_hash for c in file_changes if c.commit_hash}
        if len(distinct_hashes) <= 1 and all(c.commit_hash for c in file_changes):
            continue  # nothing to unify
        for fc in file_changes:
            if fc.commit_hash != target_hash:
                fc.commit_hash = target_hash
                fc.commit_author = target_author
                fc.commit_date = target_date
                fc.commit_message = target_message
            if not fc.commit_hash:
                fc.commit_hash = target_hash
                fc.commit_author = target_author
                fc.commit_date = target_date
                fc.commit_message = target_message
    return changes


def inject_commit_messages(
    changes: List[Change], commits: Optional[List[Dict[str, Any]]]
) -> List[Change]:
    """Populate commit_message (and missing hash/author/date if feasible) from parsed commit metadata.

    Behavior:
    - Build a map hash -> {author,date,message} from commits list.
    - For each change with commit_hash but empty commit_message, fill message (full body) then leave trimming to later.
    - If a change has no commit_hash and there is exactly one commit parsed, assign that commit wholesale.
    - Otherwise leave missing commit hashes untouched (ambiguous multi-commit scenario).
    """
    if not commits:
        return changes
    commit_map: Dict[str, Dict[str, Optional[str]]] = {}
    for c in commits:
        h = c.get("hash")
        if not h:
            continue
        commit_map[h] = {
            "author": c.get("author"),
            "date": c.get("date"),
            "message": c.get("message"),
        }
    single_commit = commits[0] if len(commits) == 1 else None
    for ch in changes:
        if ch.commit_hash and not ch.commit_message:
            meta = commit_map.get(ch.commit_hash)
            if meta and meta.get("message"):
                ch.commit_message = meta.get("message")
            # Backfill author/date if still missing
            if meta:
                if not ch.commit_author and meta.get("author"):
                    ch.commit_author = meta.get("author")
                if not ch.commit_date and meta.get("date"):
                    ch.commit_date = meta.get("date")
        elif not ch.commit_hash and single_commit:
            # Assign the only commit present (fallback heuristic)
            ch.commit_hash = single_commit.get("hash")
            ch.commit_author = single_commit.get("author")
            ch.commit_date = single_commit.get("date")
            ch.commit_message = single_commit.get("message")
    return changes


def assign_missing_commits_by_proximity(
    diff_text: str, changes: List[Change], commits: Optional[List[Dict[str, Any]]]
) -> List[Change]:
    """Assign commit metadata to changes lacking commit_hash by proximity in the raw diff text.

    Strategy:
    - Build an ordered list of (line_index, commit_hash) for commit headers.
    - For each change without commit_hash, locate the first occurrence of its 'diff --git' line.
      Then find the next commit header that appears AFTER that diff line (indicating commit-after-diff ordering).
    - If found, assign that commit's hash/author/date/message (full message, trimming later).

    This complements the streaming parser which handles commit-before-diff ordering; this function
    cleans up remaining commit-after-diff cases that slipped through (e.g., when diff sections precede
    their commit header, but buffering did not capture due to complex aggregation or interleaving).
    """
    if not commits:
        return changes
    lines = diff_text.splitlines()
    commit_positions: List[tuple] = []
    commit_meta_map: Dict[str, Dict[str, Optional[str]]] = {}
    for i, raw in enumerate(lines):
        m = _COMMIT_LINE.match(raw)
        if m:
            h = m.group(1)
            commit_positions.append((i, h))
    for c in commits:
        h = c.get("hash")
        if h:
            commit_meta_map[h] = {
                "author": c.get("author"),
                "date": c.get("date"),
                "message": c.get("message"),
            }
    if not commit_positions:
        return changes
    # Precompile diff line patterns per file for efficiency
    file_first_line_index: Dict[str, int] = {}
    for idx, raw in enumerate(lines):
        m = DIFF_GIT.match(raw)
        if m:
            b_path = m.group(2).strip()
            if b_path not in file_first_line_index:
                file_first_line_index[b_path] = idx
    for ch in changes:
        if ch.commit_hash or not ch.file:
            continue
        diff_idx = file_first_line_index.get(ch.file)
        if diff_idx is None:
            continue
        # Find first commit header after diff_idx
        chosen_hash = None
        for c_line_idx, h in commit_positions:
            if c_line_idx > diff_idx:
                chosen_hash = h
                break
        if not chosen_hash:
            continue
        meta = commit_meta_map.get(chosen_hash, {})
        ch.commit_hash = chosen_hash
        ch.commit_author = meta.get("author")
        ch.commit_date = meta.get("date")
        ch.commit_message = meta.get("message")
    return changes


if __name__ == "__main__":
    repo = args.repo_path
    commits_for_embedding: Optional[List[Dict[str, Any]]] = None
    if args.diff_file:
        if args.diff_file == "-":
            patch = sys.stdin.read()
        else:
            with open(args.diff_file, "r", encoding="utf-8") as df:
                patch = df.read()
        all_changes = parse_unified_diff(patch)
        all_changes = process_changes(
            all_changes,
            repo_path=".",  # not needed for plist context; use cwd
            suppress_deleted_files=args.suppress_deleted_files,
            add_context=False,
        )
        # Enrich commit info if not present
        all_changes = enrich_missing_commit_info(all_changes, repo_path=".")
        commits_for_embedding = parse_commit_metadata(patch)
        # Assign missing commit hashes for commit-after-diff ordering using proximity scan.
        all_changes = assign_missing_commits_by_proximity(
            patch, all_changes, commits_for_embedding
        )
        # Two-pass heuristic consolidation (Option A): unify per-file commits to last commit seen for that file.
        all_changes = consolidate_file_commit_attribution(all_changes)
        # After consolidation, fill any remaining missing commit info again (in case we populated hashes only)
        all_changes = enrich_missing_commit_info(all_changes, repo_path=".")
        # Inject commit messages (and fallback hash if only one commit) before subject trim.
        all_changes = inject_commit_messages(all_changes, commits_for_embedding)
        # Force subject-only commit messages
        all_changes = override_commit_messages_subject_only(all_changes, repo_path=".")
        all_changes = trim_commit_messages_to_subject(all_changes)
        print_changes(all_changes)
    elif repo:
        files = list_changed_files(repo)
        to_diff = [rec["new"] for rec in files if rec["status"] in ("M", "A", "R")]
        all_changes: List[Change] = []
        for group in batch_paths(to_diff, batch_size=200):
            patch = diff_batch(
                repo, group, unified=args.unified
            )  # use 1 line of context
            all_changes.extend(parse_unified_diff(patch))
        all_changes = process_changes(
            all_changes,
            repo_path=repo,
            suppress_deleted_files=args.suppress_deleted_files,
            add_context=False,
        )
        all_changes = enrich_missing_commit_info(all_changes, repo_path=repo)
        all_changes = override_commit_messages_subject_only(all_changes, repo_path=repo)
        all_changes = trim_commit_messages_to_subject(all_changes)
        print_changes(all_changes)
    elif args.compare_file_1 and args.compare_file_2:
        patch = diff_files(
            args.compare_file_1, args.compare_file_2, unified=args.unified
        )
        all_changes = parse_unified_diff(patch)
        all_changes = process_changes(
            all_changes,
            repo_path=".",
            suppress_deleted_files=args.suppress_deleted_files,
            add_context=False,
        )
        all_changes = enrich_missing_commit_info(all_changes, repo_path=".")
        all_changes = override_commit_messages_subject_only(all_changes, repo_path=".")
        all_changes = trim_commit_messages_to_subject(all_changes)
        print_changes(all_changes)
    else:
        print(
            "Please provide either --repo_path, --diff-file, or both --compare-file-1 and --compare-file-2"
        )
        exit(1)

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            f.write(to_pretty_json(all_changes))
