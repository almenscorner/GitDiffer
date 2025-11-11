#!/usr/local/bin/python3

import re
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Any, Optional
from collections import defaultdict
import subprocess
import json
import os
import html
from enum import Enum
from argparse import ArgumentParser

parser = ArgumentParser(description="Parse git unified diffs into structured changes.")
parser.add_argument("--repo_path", help="Path to the git repository")
parser.add_argument("--compare-file-1", help="First file to compare")
parser.add_argument("--compare-file-2", help="Second file to compare")
parser.add_argument(
    "--unified", type=int, default=0, help="Number of unified diff context lines"
)
parser.add_argument(
    "--output_json", nargs="?", default=None, help="Optional output JSON file path"
)
parser.add_argument(
    "--suppress-deleted-files",
    action="store_true",
    help="Suppress fully deleted files in output",
)

args = parser.parse_args()

DIFF_GIT = re.compile(r"^diff --git a/(.+?) b/(.+?)\s*$")
OLD_FILE_HDR = re.compile(r"^---\s+(?:a/|/dev/null)(.+?)\s*$")
NEW_FILE_HDR = re.compile(r"^\+\+\+\s+(?:b/|/dev/null)(.+?)\s*$")
HUNK_HEADER = re.compile(r"^@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@")

DOCKER_INSTR = re.compile(r"^\s*([A-Z]+)\b(.*)$")
KV_PATTERNS = [re.compile(r'^\s*"?([A-Za-z0-9_.-]+)"?\s*[:=]\s*(.+?)\s*$')]
JSON_PAIR = re.compile(r'^\s*"([A-Za-z0-9_.-]+)"\s*:\s*(.+?)(,?\s*)$')

PLIST_KEY_RE = re.compile(r"<\s*key\s*>\s*([^<]+?)\s*<\s*/\s*key\s*>")
PLIST_STRING_RE = re.compile(r"<\s*string\s*>\s*(.*?)\s*<\s*/\s*string\s*>")

OLD_FILE_HDR = re.compile(r"^---\s+(?:a/)?([^\t]+)")
NEW_FILE_HDR = re.compile(r"^\+\+\+\s+(?:b/)?([^\t]+)")

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
    return json.dumps(
        [c.to_dict(expand_tabs, tabsize) for c in changes], indent=2, ensure_ascii=False
    )


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
) -> Change:
    if old_lines and new_lines:
        ctype = ChangeType.REPLACE.value
    elif old_lines and not new_lines:
        ctype = ChangeType.DELETE.value
    elif new_lines and not old_lines:
        ctype = ChangeType.INSERT.value
    else:
        ctype = ChangeType.UNKNOWN.value
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
    )


class DiffParser:
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

    def parse(self) -> List[Change]:
        for raw in self.lines:
            line = raw.rstrip("\n")
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
            # reset hunk context; keep old path if +++ not yet seen
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
        return any(line.startswith(p) for p in _METADATA_PREFIXES)

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
        if line.startswith("-"):
            body = line[1:]
            if not self.block_removed and not self.block_added:
                self.block_old_start = self.old_line
            self.block_removed.append(body)
            self.old_line += 1
        elif line.startswith("+"):
            body = line[1:]
            if not self.block_removed and not self.block_added:
                self.block_new_start = self.new_line
            if self.block_removed and self.block_new_start is None:
                self.block_new_start = self.new_line
            self.block_added.append(body)
            self.new_line += 1
        else:
            # context line
            self._flush_block()
            self.old_line += 1
            self.new_line += 1

    def _flush_block(self):
        if not self.block_removed and not self.block_added:
            return
        self.changes.append(
            _make_change(
                self.cur_file or "",
                self.block_removed,
                self.block_added,
                self.block_old_start,
                self.block_new_start,
            )
        )
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
    return DiffParser(diff_text).parse()


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
    changes = remove_trivial_structure_lines(changes)
    changes = annotate_plist_properties(changes, repo_path=repo_path)
    if suppress_deleted_files:
        changes = suppress_fully_deleted_files(changes, emit_summary=True)
    changes = simplify_single_line_replacements(changes)
    if add_context:
        changes = enrich_with_neighbor_context(changes, repo_path, before=1, after=1)
    return changes


if __name__ == "__main__":
    repo = args.repo_path
    if repo:
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
        print_changes(all_changes)
    else:
        print(
            "Please provide either --repo_path or both --compare-file-1 and --compare-file-2"
        )
        exit(1)

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            f.write(to_pretty_json(all_changes))
