---
name: obzetero-paper-reader
description: Read papers from Zotero collections, extract local PDFs, generate Chinese intensive-reading notes, sync them into Obsidian, and keep Zotero reading-status tags in sync with Obsidian note frontmatter and collection indexes. Use when the user asks to automatically read, summarize,精读, index, count, or bidirectionally sync Zotero papers with Obsidian.
---

# ObZetero Paper Reader

Use this skill to turn a Zotero collection into an Obsidian reading system with Chinese paper notes, reading status, and indexes.

Default paths:

- Zotero database: `~/Zotero/zotero.sqlite`
- Obsidian vault: `~/Documents/LLM Papers`

## Workflow

1. Read `references/workflow.md` before doing a full sync or paper-reading run.
2. Run the helper script from this skill folder:

```bash
python3 scripts/obzetero_sync.py scan --collection "LLM"
python3 scripts/obzetero_sync.py read --collection "LLM"
python3 scripts/obzetero_sync.py sync --collection "LLM"
python3 scripts/obzetero_sync.py index --collection "LLM"
```

Use `--vault "/path/to/vault"` when the target vault is not `~/Documents/LLM Papers`.

## Reading Status

Zotero reading status is represented by ordinary tags:

- `已读` means `read`
- `阅读中` means `reading`
- `未读` means `unread`

Existing nested tags such as `/已读`, `/阅读中`, and `/未读` are also recognized when reading Zotero status. Writeback uses plain tags unless the user asks for a different tag scheme.

Obsidian notes use frontmatter:

```yaml
status: unread
```

Collection indexes use:

- `- [x]` for read
- `- [ ]` for unread
- `- [>]` for reading

Only one status should remain per Zotero item. If multiple status tags exist, normalize by priority: `阅读中`, then `已读`, then `未读`.

## Paper Notes

Use `references/note-template.md` as the section contract. Keep user-written sections intact. Update only the automatic sections unless the user explicitly asks to rewrite their manual notes.

For Chinese intensive-reading content:

1. Extract the PDF text with `read`.
2. Read enough of the extracted text to understand the paper.
3. Fill the `AUTO: Chinese Reading Notes` section in Chinese.
4. Preserve `MANUAL:` sections.
5. Rebuild indexes after note edits.

## Safety

- Scan commands are read-only for Zotero.
- Zotero writeback is only for reading-status tags and only when running `sync`.
- If Zotero is running, do not write to `zotero.sqlite`; ask the user to close Zotero or use a read-only pass.
- Before any Zotero write, the helper script creates a timestamped `.bak` copy of `zotero.sqlite`.
- Never store API keys, Zotero database copies, PDFs, or private Obsidian notes inside the skill folder.

## References

- `references/workflow.md`: end-to-end procedure.
- `references/note-template.md`: required note structure.
- `references/sync-rules.md`: status sync and conflict rules.
