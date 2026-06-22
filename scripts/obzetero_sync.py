#!/usr/bin/env python3
"""Sync Zotero paper metadata/status with Obsidian notes.

This script intentionally uses only Python stdlib plus an optional `pdftotext`
binary for PDF extraction. It never writes Zotero unless the `sync` command
needs to update reading-status tags.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


STATUS_TAGS = {
    "已读": "read",
    "阅读中": "reading",
    "未读": "unread",
}
STATUS_TO_TAG = {value: key for key, value in STATUS_TAGS.items()}
STATUS_PRIORITY = {"reading": 3, "read": 2, "unread": 1}
INDEX_MARK = {"read": "x", "unread": " ", "reading": ">"}
MARK_STATUS = {"x": "read", "X": "read", " ": "unread", ">": "reading"}

DEFAULT_VAULT = Path("~/Documents/LLM Papers").expanduser()
DEFAULT_ZOTERO_DB = Path("~/Zotero/zotero.sqlite").expanduser()
DEFAULT_ZOTERO_STORAGE = Path("~/Zotero/storage").expanduser()
STATE_REL = ".obzetero/state.json"
BUNDLED_PYTHON = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"


@dataclass
class Paper:
    item_id: int
    key: str
    item_type: str
    title: str
    authors: list[str]
    date: str = ""
    doi: str = ""
    url: str = ""
    abstract: str = ""
    extra: str = ""
    tags: list[str] = field(default_factory=list)
    pdfs: list[Path] = field(default_factory=list)

    @property
    def status(self) -> str:
        statuses = [status_from_tag(tag) for tag in self.tags if status_from_tag(tag)]
        if not statuses:
            return "unread"
        return sorted(statuses, key=lambda s: STATUS_PRIORITY[s], reverse=True)[0]


def status_from_tag(tag: str) -> str | None:
    return STATUS_TAGS.get(tag.lstrip("/"))


def is_status_tag(tag: str) -> bool:
    return status_from_tag(tag) is not None


def slugify(value: str, limit: int = 110) -> str:
    value = re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return (value or "Untitled")[:limit]


def yaml_quote(value: Any) -> str:
    text = "" if value is None else str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def dedupe_date(value: str) -> str:
    parts = (value or "").split()
    if len(parts) == 2 and parts[0] == parts[1]:
        return parts[0]
    return value or ""


def db_uri(path: Path, immutable: bool) -> str:
    suffix = "?immutable=1" if immutable else ""
    return f"file:{path}{suffix}"


def connect(db: Path, readonly: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(db_uri(db, readonly), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_collection_id(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute(
        "select collectionID from collections where collectionName = ?",
        (name,),
    ).fetchone()
    if not row:
        raise SystemExit(f"Zotero collection not found: {name}")
    return int(row["collectionID"])


def creator_name(row: sqlite3.Row) -> str:
    first = row["firstName"] or ""
    last = row["lastName"] or ""
    if row["fieldMode"] == 1:
        return last or first
    return " ".join(part for part in [first, last] if part).strip()


def fetch_papers(db: Path, collection: str, storage: Path) -> list[Paper]:
    conn = connect(db, readonly=True)
    collection_id = fetch_collection_id(conn, collection)
    item_rows = conn.execute(
        """
        select i.itemID, i.key, it.typeName
        from collectionItems ci
        join items i on i.itemID = ci.itemID
        join itemTypes it on it.itemTypeID = i.itemTypeID
        where ci.collectionID = ?
        order by ci.orderIndex, i.itemID
        """,
        (collection_id,),
    ).fetchall()

    papers: list[Paper] = []
    for item in item_rows:
        fields = {
            row["fieldName"]: row["value"]
            for row in conn.execute(
                """
                select f.fieldName, idv.value
                from itemData id
                join fieldsCombined f on f.fieldID = id.fieldID
                join itemDataValues idv on idv.valueID = id.valueID
                where id.itemID = ?
                """,
                (item["itemID"],),
            )
        }
        authors = [
            name
            for name in (
                creator_name(row)
                for row in conn.execute(
                    """
                    select c.firstName, c.lastName, c.fieldMode
                    from itemCreators ic
                    join creators c on c.creatorID = ic.creatorID
                    where ic.itemID = ?
                    order by ic.orderIndex
                    """,
                    (item["itemID"],),
                )
            )
            if name
        ]
        tags = [
            row["name"]
            for row in conn.execute(
                """
                select t.name
                from itemTags it
                join tags t on t.tagID = it.tagID
                where it.itemID = ?
                order by t.name
                """,
                (item["itemID"],),
            )
        ]
        pdfs: list[Path] = []
        for row in conn.execute(
            """
            select i.key, ia.path, ia.contentType
            from itemAttachments ia
            join items i on i.itemID = ia.itemID
            where ia.parentItemID = ? and ia.contentType = 'application/pdf'
            order by i.key
            """,
            (item["itemID"],),
        ):
            raw_path = row["path"] or ""
            if raw_path.startswith("storage:"):
                pdfs.append(storage / row["key"] / raw_path.split(":", 1)[1])
            else:
                pdfs.append(Path(raw_path).expanduser())

        papers.append(
            Paper(
                item_id=int(item["itemID"]),
                key=item["key"],
                item_type=item["typeName"],
                title=fields.get("title") or item["key"],
                authors=authors,
                date=dedupe_date(fields.get("date", "")),
                doi=fields.get("DOI", ""),
                url=fields.get("url", ""),
                abstract=fields.get("abstractNote", ""),
                extra=fields.get("extra", ""),
                tags=tags,
                pdfs=pdfs,
            )
        )
    conn.close()
    return papers


def vault_paths(vault: Path, collection: str) -> dict[str, Path]:
    root = vault / "Collections" / slugify(collection)
    return {
        "root": root,
        "papers": root / "Papers",
        "index": root / "Index.md",
        "dashboard": vault / "Paper Reading Dashboard.md",
        "state": vault / STATE_REL,
        "extracts": root / ".extracts",
    }


def paper_note_path(vault: Path, collection: str, paper: Paper) -> Path:
    year = paper.date[:4] if paper.date else "n.d."
    return vault_paths(vault, collection)["papers"] / f"{slugify(paper.title)} ({year}).md"


def load_state(vault: Path) -> dict[str, Any]:
    path = vault / STATE_REL
    if not path.exists():
        return {"version": 1, "items": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(vault: Path, state: dict[str, Any]) -> None:
    path = vault / STATE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body = text[end + 5 :]
    data: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line or line.startswith("  "):
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"')
    return data, body


def section(text: str, heading: str) -> str:
    pattern = re.compile(rf"(^## {re.escape(heading)}\n)(.*?)(?=^## |\Z)", re.M | re.S)
    match = pattern.search(text)
    return match.group(2).strip() if match else ""


def replace_section(text: str, heading: str, content: str) -> str:
    block = f"## {heading}\n\n{content.rstrip()}\n\n"
    pattern = re.compile(rf"^## {re.escape(heading)}\n.*?(?=^## |\Z)", re.M | re.S)
    if pattern.search(text):
        return pattern.sub(block, text)
    return text.rstrip() + "\n\n" + block


def render_frontmatter(paper: Paper, status: str) -> str:
    authors = "\n".join(f"  - {yaml_quote(author)}" for author in paper.authors)
    return "\n".join(
        [
            "---",
            f"title: {yaml_quote(paper.title)}",
            f"zotero_key: {paper.key}",
            f"zotero_link: {yaml_quote('zotero://select/library/items/' + paper.key)}",
            f"item_type: {yaml_quote(paper.item_type)}",
            f"status: {status}",
            f"date: {yaml_quote(paper.date)}",
            f"doi: {yaml_quote(paper.doi)}",
            f"url: {yaml_quote(paper.url)}",
            "authors:",
            authors,
            "tags:",
            "  - zotero",
            "  - paper",
            "---",
            "",
        ]
    )


def render_metadata(paper: Paper) -> str:
    lines = [
        f"- Authors: {', '.join(paper.authors) or 'Unknown'}",
        f"- Date: {paper.date or 'Unknown'}",
        f"- Zotero: [{paper.key}](zotero://select/library/items/{paper.key})",
    ]
    if paper.doi:
        lines.append(f"- DOI: [{paper.doi}](https://doi.org/{paper.doi})")
    if paper.url:
        lines.append(f"- URL: {paper.url}")
    if paper.extra:
        lines.append(f"- Extra: {paper.extra}")
    if paper.pdfs:
        lines.append("- Local PDF:")
        for pdf in paper.pdfs:
            missing = "" if pdf.exists() else " (missing)"
            lines.append(f"  - [{pdf.name}](<{pdf}>)" + missing)
    return "\n".join(lines)


def empty_chinese_notes() -> str:
    return "\n".join(
        [
            "### 一句话结论",
            "",
            "待精读。",
            "",
            "### 核心贡献",
            "",
            "- ",
            "",
            "### 方法拆解",
            "",
            "- ",
            "",
            "### 技术细节",
            "",
            "- ",
            "",
            "### 实验设置",
            "",
            "- ",
            "",
            "### 结果解读",
            "",
            "- ",
            "",
            "### 局限性",
            "",
            "- ",
            "",
            "### 可复现性",
            "",
            "- ",
            "",
            "### 可借鉴点",
            "",
            "- ",
        ]
    )


def render_note(paper: Paper, status: str, existing: str | None = None) -> str:
    manual_notes = "- "
    questions = "- "
    actions = "- "
    auto_notes = empty_chinese_notes()
    if existing:
        manual_notes = section(existing, "MANUAL: My Notes") or manual_notes
        questions = section(existing, "MANUAL: Questions") or questions
        actions = section(existing, "MANUAL: Next Actions") or actions
        old_auto = section(existing, "AUTO: Chinese Reading Notes")
        if old_auto and "待精读" not in old_auto:
            auto_notes = old_auto

    body = "\n".join(
        [
            f"# {paper.title}",
            "",
            "## AUTO: Metadata",
            "",
            render_metadata(paper),
            "",
            "## AUTO: Chinese Reading Notes",
            "",
            auto_notes,
            "",
            "## SYNC: Zotero Note",
            "",
            "",
            "## MANUAL: My Notes",
            "",
            manual_notes,
            "",
            "## MANUAL: Questions",
            "",
            questions,
            "",
            "## MANUAL: Next Actions",
            "",
            actions,
            "",
            "## Abstract",
            "",
            paper.abstract or "No abstract in Zotero.",
            "",
        ]
    )
    return render_frontmatter(paper, status) + body


def write_notes(papers: list[Paper], vault: Path, collection: str, state: dict[str, Any]) -> None:
    paths = vault_paths(vault, collection)
    paths["papers"].mkdir(parents=True, exist_ok=True)
    for paper in papers:
        path = paper_note_path(vault, collection, paper)
        existing = path.read_text(encoding="utf-8") if path.exists() else None
        existing_meta, _ = parse_frontmatter(existing or "")
        status = existing_meta.get("status") or paper.status
        path.write_text(render_note(paper, status, existing), encoding="utf-8")
        state["items"][paper.key] = {
            **state.get("items", {}).get(paper.key, {}),
            "title": paper.title,
            "collection": collection,
            "note_path": str(path),
            "last_zotero_status": paper.status,
            "last_obsidian_status": status,
            "last_note_mtime": path.stat().st_mtime,
            "last_synced": int(time.time()),
        }


def parse_index_statuses(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    statuses: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.search(r"- \[([ xX>])\].*<!-- zotero_key: ([A-Z0-9]+) -->", line)
        if match:
            statuses[match.group(2)] = MARK_STATUS.get(match.group(1), "unread")
    return statuses


def write_index(papers: list[Paper], vault: Path, collection: str, state: dict[str, Any]) -> None:
    paths = vault_paths(vault, collection)
    paths["root"].mkdir(parents=True, exist_ok=True)
    previous = parse_index_statuses(paths["index"])
    lines = [
        f"# {collection} Reading Index",
        "",
        "| Status | Count |",
        "| --- | ---: |",
    ]
    statuses_by_key: dict[str, str] = {}
    for paper in papers:
        note = paper_note_path(vault, collection, paper)
        frontmatter, _ = parse_frontmatter(note.read_text(encoding="utf-8") if note.exists() else "")
        status = previous.get(paper.key) or frontmatter.get("status") or paper.status
        statuses_by_key[paper.key] = status
    counts = {status: list(statuses_by_key.values()).count(status) for status in ["read", "reading", "unread"]}
    lines.extend(
        [
            f"| 已读 | {counts['read']} |",
            f"| 阅读中 | {counts['reading']} |",
            f"| 未读 | {counts['unread']} |",
            "",
            "## Papers",
            "",
        ]
    )
    for paper in papers:
        status = statuses_by_key.get(paper.key, "unread")
        mark = INDEX_MARK.get(status, " ")
        stem = paper_note_path(vault, collection, paper).stem
        detail = " - ".join(part for part in [paper.date, ", ".join(paper.authors[:3])] if part)
        suffix = f" - {detail}" if detail else ""
        lines.append(f"- [{mark}] [[Papers/{stem}|{paper.title}]]{suffix} <!-- zotero_key: {paper.key} -->")
    paths["index"].write_text("\n".join(lines) + "\n", encoding="utf-8")

    for paper in papers:
        item_state = state.setdefault("items", {}).setdefault(paper.key, {})
        item_state["last_index_status"] = statuses_by_key.get(paper.key, "unread")


def write_dashboard(vault: Path, state: dict[str, Any]) -> None:
    items = state.get("items", {})
    by_collection: dict[str, list[dict[str, Any]]] = {}
    for item in items.values():
        by_collection.setdefault(item.get("collection", "Unknown"), []).append(item)
    lines = [
        "# Paper Reading Dashboard",
        "",
        f"Last updated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "| Collection | Total | 已读 | 阅读中 | 未读 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for collection, rows in sorted(by_collection.items()):
        statuses = [row.get("last_obsidian_status") or row.get("last_index_status") or "unread" for row in rows]
        lines.append(
            f"| {collection} | {len(rows)} | {statuses.count('read')} | {statuses.count('reading')} | {statuses.count('unread')} |"
        )
    lines.extend(["", "## Recent Items", ""])
    recent = sorted(items.values(), key=lambda row: row.get("last_synced", 0), reverse=True)[:20]
    for item in recent:
        path = item.get("note_path", "")
        title = item.get("title", "Untitled")
        if path:
            lines.append(f"- [{title}](<{path}>)")
        else:
            lines.append(f"- {title}")
    (vault / "Paper Reading Dashboard.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def zotero_running() -> bool:
    try:
        result = subprocess.run(["pgrep", "-x", "Zotero"], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return False
    return result.returncode == 0


def backup_db(db: Path) -> Path:
    backup = db.with_name(f"{db.name}.{time.strftime('%Y%m%d-%H%M%S')}.bak")
    shutil.copy2(db, backup)
    return backup


def ensure_tag(conn: sqlite3.Connection, tag: str) -> int:
    row = conn.execute("select tagID from tags where name = ?", (tag,)).fetchone()
    if row:
        return int(row["tagID"])
    conn.execute("insert into tags(name) values (?)", (tag,))
    return int(conn.execute("select last_insert_rowid() as id").fetchone()["id"])


def update_zotero_statuses(db: Path, updates: dict[int, str]) -> int:
    if not updates:
        return 0
    if zotero_running():
        raise SystemExit("Zotero is running. Close Zotero before writing reading-status tags.")
    backup = backup_db(db)
    conn = connect(db, readonly=False)
    changed = 0
    try:
        for item_id, status in updates.items():
            desired_tag = STATUS_TO_TAG[status]
            desired_tag_id = ensure_tag(conn, desired_tag)
            status_tag_ids = [
                int(row["tagID"])
                for row in conn.execute("select tagID, name from tags")
                if is_status_tag(str(row["name"]))
            ]
            if status_tag_ids:
                conn.execute(
                    f"delete from itemTags where itemID = ? and tagID in ({','.join('?' for _ in status_tag_ids)})",
                    (item_id, *status_tag_ids),
                )
            conn.execute(
                "insert or ignore into itemTags(itemID, tagID, type) values (?, ?, 0)",
                (item_id, desired_tag_id),
            )
            conn.execute(
                "update items set dateModified = CURRENT_TIMESTAMP, clientDateModified = CURRENT_TIMESTAMP, synced = 0 where itemID = ?",
                (item_id,),
            )
            changed += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"Zotero backup: {backup}")
    return changed


def sync_statuses(papers: list[Paper], vault: Path, collection: str, state: dict[str, Any], db: Path, write_zotero: bool) -> None:
    paths = vault_paths(vault, collection)
    index_status = parse_index_statuses(paths["index"])
    updates: dict[int, str] = {}
    for paper in papers:
        note = paper_note_path(vault, collection, paper)
        frontmatter, body = parse_frontmatter(note.read_text(encoding="utf-8") if note.exists() else "")
        obsidian_status = index_status.get(paper.key) or frontmatter.get("status")
        zotero_status = paper.status
        chosen = obsidian_status or zotero_status
        if chosen not in STATUS_TO_TAG:
            chosen = zotero_status
        if chosen != zotero_status:
            updates[paper.item_id] = chosen
        if note.exists() and frontmatter.get("status") != chosen:
            frontmatter_text = render_frontmatter(paper, chosen)
            note.write_text(frontmatter_text + body, encoding="utf-8")
        state.setdefault("items", {}).setdefault(paper.key, {}).update(
            {
                "last_zotero_status": zotero_status,
                "last_obsidian_status": chosen,
                "last_index_status": index_status.get(paper.key, chosen),
                "last_synced": int(time.time()),
            }
        )
    if write_zotero:
        changed = update_zotero_statuses(db, updates)
        print(f"Updated Zotero status tags: {changed}")
    elif updates:
        print(f"Pending Zotero tag updates: {len(updates)}. Re-run sync with --write-zotero after closing Zotero.")


def extract_pdf_text(pdf: Path) -> str:
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/bin/pdftotext"
        if bundled.exists():
            pdftotext = str(bundled)
    if not pdftotext:
        try:
            import pdfplumber  # type: ignore

            chunks = []
            with pdfplumber.open(str(pdf)) as doc:
                for page in doc.pages:
                    chunks.append(page.extract_text(x_tolerance=1, y_tolerance=3) or "")
            return "\n\n".join(chunks)
        except Exception:
            try:
                from pypdf import PdfReader  # type: ignore

                reader = PdfReader(str(pdf))
                return "\n\n".join(page.extract_text() or "" for page in reader.pages)
            except Exception as exc:
                raise RuntimeError("pdftotext/pdfplumber/pypdf not available or failed") from exc
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "paper.txt"
        subprocess.run([pdftotext, "-layout", str(pdf), str(out)], check=True)
        return out.read_text(encoding="utf-8", errors="ignore")


def write_extracts(papers: list[Paper], vault: Path, collection: str) -> None:
    extracts = vault_paths(vault, collection)["extracts"]
    extracts.mkdir(parents=True, exist_ok=True)
    for paper in papers:
        target = extracts / f"{paper.key}.txt"
        if not paper.pdfs:
            target.write_text("No local PDF attachment found.\n", encoding="utf-8")
            continue
        pdf = next((p for p in paper.pdfs if p.exists()), paper.pdfs[0])
        try:
            text = extract_pdf_text(pdf)
        except Exception as exc:
            text = f"PDF extraction failed for {pdf}: {exc}\n"
        target.write_text(text, encoding="utf-8")


def command_scan(args: argparse.Namespace) -> None:
    papers = fetch_papers(args.zotero_db, args.collection, args.zotero_storage)
    print(json.dumps(
        {
            "collection": args.collection,
            "count": len(papers),
            "papers": [
                {
                    "key": paper.key,
                    "title": paper.title,
                    "status": paper.status,
                    "tags": paper.tags,
                    "pdfs": [str(path) for path in paper.pdfs],
                }
                for paper in papers
            ],
        },
        ensure_ascii=False,
        indent=2,
    ))


def command_read(args: argparse.Namespace) -> None:
    papers = fetch_papers(args.zotero_db, args.collection, args.zotero_storage)
    state = load_state(args.vault)
    write_notes(papers, args.vault, args.collection, state)
    write_extracts(papers, args.vault, args.collection)
    write_index(papers, args.vault, args.collection, state)
    write_dashboard(args.vault, state)
    save_state(args.vault, state)
    print(f"Prepared notes and extracts for {len(papers)} papers.")


def command_index(args: argparse.Namespace) -> None:
    papers = fetch_papers(args.zotero_db, args.collection, args.zotero_storage)
    state = load_state(args.vault)
    write_index(papers, args.vault, args.collection, state)
    write_dashboard(args.vault, state)
    save_state(args.vault, state)
    print(f"Rebuilt index for {len(papers)} papers.")


def command_sync(args: argparse.Namespace) -> None:
    papers = fetch_papers(args.zotero_db, args.collection, args.zotero_storage)
    state = load_state(args.vault)
    write_notes(papers, args.vault, args.collection, state)
    sync_statuses(papers, args.vault, args.collection, state, args.zotero_db, args.write_zotero)
    write_index(papers, args.vault, args.collection, state)
    write_dashboard(args.vault, state)
    save_state(args.vault, state)
    print(f"Synced {len(papers)} papers.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync Zotero papers with Obsidian.")
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    parser.add_argument("--zotero-db", type=Path, default=DEFAULT_ZOTERO_DB)
    parser.add_argument("--zotero-storage", type=Path, default=DEFAULT_ZOTERO_STORAGE)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in [
        ("scan", command_scan),
        ("read", command_read),
        ("sync", command_sync),
        ("index", command_index),
    ]:
        cmd = sub.add_parser(name)
        cmd.add_argument("--collection", required=True)
        if name == "sync":
            cmd.add_argument("--write-zotero", action="store_true", help="Write Obsidian status back to Zotero tags.")
        cmd.set_defaults(func=func)
    return parser


def maybe_reexec_for_pdf() -> None:
    if "OBZETERO_NO_REEXEC" in os.environ:
        return
    if "read" not in sys.argv[1:]:
        return
    if Path(sys.executable) == BUNDLED_PYTHON or not BUNDLED_PYTHON.exists():
        return
    try:
        __import__("pdfplumber")
        return
    except Exception:
        env = dict(os.environ)
        env["OBZETERO_NO_REEXEC"] = "1"
        os.execve(str(BUNDLED_PYTHON), [str(BUNDLED_PYTHON), *sys.argv], env)


def main() -> None:
    maybe_reexec_for_pdf()
    parser = build_parser()
    args = parser.parse_args()
    args.vault = args.vault.expanduser()
    args.zotero_db = args.zotero_db.expanduser()
    args.zotero_storage = args.zotero_storage.expanduser()
    args.func(args)


if __name__ == "__main__":
    main()
