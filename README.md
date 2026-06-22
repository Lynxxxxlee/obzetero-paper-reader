# obzetero-paper-reader

`obzetero-paper-reader` is a Codex skill for building an Obsidian paper-reading system from Zotero collections.

## 功能

- 从任意 Zotero 集合读取论文元数据、作者、摘要、标签和本地 PDF。
- 将论文同步到 Obsidian vault，按集合生成论文笔记、索引和阅读统计面板。
- 提取 PDF 全文，为 Codex 生成中文论文精读笔记提供输入。
- 使用 Zotero 标签同步阅读状态：
  - `已读` / `/已读` -> `read`
  - `阅读中` / `/阅读中` -> `reading`
  - `未读` / `/未读` -> `unread`
- 在 Obsidian 中维护：
  - `Collections/<集合名>/Papers/`
  - `Collections/<集合名>/Index.md`
  - `Paper Reading Dashboard.md`
  - `.obzetero/state.json`
- 支持把 Obsidian 状态写回 Zotero 标签，写回前会检查 Zotero 是否关闭并备份数据库。

## 用法

```bash
cd ~/.codex/skills/obzetero-paper-reader

python3 scripts/obzetero_sync.py scan --collection "LLM"
python3 scripts/obzetero_sync.py read --collection "LLM"
python3 scripts/obzetero_sync.py sync --collection "LLM"
python3 scripts/obzetero_sync.py index --collection "LLM"
```

写回 Zotero 标签前请先关闭 Zotero：

```bash
python3 scripts/obzetero_sync.py sync --collection "LLM" --write-zotero
```

默认 Obsidian vault 是 `~/Documents/LLM Papers`，可用 `--vault` 指定其他位置：

```bash
python3 scripts/obzetero_sync.py read --collection "LLM" --vault "~/Documents/My Vault"
```

## 安全边界

- `scan`、`read`、`index` 不写 Zotero 数据库。
- `sync` 默认不写 Zotero；只有传入 `--write-zotero` 才会写回阅读状态标签。
- Zotero 正在运行时，脚本会拒绝写库。
- 写回前会创建 `zotero.sqlite.<timestamp>.bak` 备份。
- 不应把 Zotero 数据库、PDF、Obsidian 私人笔记或密钥提交到此仓库。

## Skill 文件

- `SKILL.md`：Codex skill 入口说明。
- `scripts/obzetero_sync.py`：同步脚本。
- `references/workflow.md`：使用流程。
- `references/note-template.md`：论文笔记模板。
- `references/sync-rules.md`：阅读状态同步规则。
