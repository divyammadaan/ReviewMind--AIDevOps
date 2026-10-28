"""Split source files into semantic chunks (functions, classes, module preamble).

Primary implementation uses the standard library's `ast` module.

Why `ast` rather than tree-sitter, which the proposal named: for Python
specifically, `ast` is the language's own parser -- it cannot disagree with the
interpreter about what a function is -- and it has no wheel-availability risk on
Python 3.14. tree-sitter remains the right choice for multi-language support and
is kept behind this same interface (see `chunk_file`'s dispatch). This deviation
is recorded in docs/RESEARCH_DESIGN.md §8.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Chunk:
    """One semantic unit of code, with the location needed to cite it."""

    file: str
    name: str          # e.g. "get_display_name" or "UserService.save"
    kind: str          # function | async_function | class | module_preamble
    start_line: int    # 1-based, inclusive
    end_line: int      # 1-based, inclusive
    text: str
    docstring: Optional[str] = None
    signature: str = ""

    @property
    def ident(self) -> str:
        return f"{self.file}:{self.name}:{self.start_line}"

    def header(self) -> str:
        """Compact one-line description used when packing chunks into a prompt."""
        return f"{self.file}:{self.start_line}-{self.end_line} ({self.kind} {self.name})"


def _signature(node: ast.AST, source_lines: list[str]) -> str:
    line = source_lines[node.lineno - 1].strip() if node.lineno <= len(source_lines) else ""
    return line.rstrip(":")


def chunk_python_source(source: str, file_label: str) -> list[Chunk]:
    """Chunk Python source. Returns [] with no exception on a syntax error.

    A syntax error is expected for some PR diffs and must not abort a review,
    so it degrades to 'no chunks' rather than propagating.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    lines = source.splitlines()
    chunks: list[Chunk] = []

    def emit(node: ast.AST, name: str, kind: str) -> None:
        start = node.lineno
        end = getattr(node, "end_lineno", start) or start
        # Include immediately preceding decorators in the chunk.
        for dec in getattr(node, "decorator_list", []):
            start = min(start, dec.lineno)
        chunks.append(Chunk(
            file=file_label,
            name=name,
            kind=kind,
            start_line=start,
            end_line=end,
            text="\n".join(lines[start - 1:end]),
            docstring=ast.get_docstring(node) if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ) else None,
            signature=_signature(node, lines),
        ))

    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            emit(node, node.name, "function")
        elif isinstance(node, ast.AsyncFunctionDef):
            emit(node, node.name, "async_function")
        elif isinstance(node, ast.ClassDef):
            emit(node, node.name, "class")
            # Also emit methods individually: a caller usually needs one method,
            # not an entire class, and finer chunks retrieve more precisely.
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    kind = "function" if isinstance(sub, ast.FunctionDef) else "async_function"
                    emit(sub, f"{node.name}.{sub.name}", kind)

    # Module preamble: imports and module-level constants before the first def.
    first_def = min((c.start_line for c in chunks), default=len(lines) + 1)
    preamble = "\n".join(lines[:first_def - 1]).strip()
    if preamble:
        chunks.insert(0, Chunk(
            file=file_label, name="<module>", kind="module_preamble",
            start_line=1, end_line=max(1, first_def - 1),
            text="\n".join(lines[:first_def - 1]),
        ))

    return chunks


def chunk_file(path: Path, repo_root: Optional[Path] = None) -> list[Chunk]:
    """Chunk one file, dispatching on extension."""
    label = str(path.relative_to(repo_root)) if repo_root else str(path)
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    if path.suffix == ".py":
        return chunk_python_source(source, label)
    # Other languages: tree-sitter would plug in here. Until then we do not
    # pretend to chunk them -- returning [] is honest and visible in the index stats.
    return []


def chunk_repo(
    repo_root: Path,
    include_suffixes: tuple[str, ...] = (".py",),
    skip_dirs: tuple[str, ...] = ("__pycache__", ".git", ".venv", "node_modules", "dataset"),
) -> list[Chunk]:
    """Chunk every supported file under `repo_root`.

    `skip_dirs` is matched against the path RELATIVE to `repo_root`. Matching
    absolute path parts instead would make indexing depend on where the repo
    happens to live -- e.g. a corpus stored under `experiments/dataset/` had
    every file silently skipped because "dataset" appeared in its absolute path.
    """
    repo_root = repo_root.resolve()
    out: list[Chunk] = []
    for path in sorted(repo_root.rglob("*")):
        if not path.is_file() or path.suffix not in include_suffixes:
            continue
        try:
            rel_parts = path.resolve().relative_to(repo_root).parts
        except ValueError:
            continue
        # Only the DIRECTORY components matter, not the filename itself.
        if any(part in skip_dirs for part in rel_parts[:-1]):
            continue
        out.extend(chunk_file(path.resolve(), repo_root))
    return out
