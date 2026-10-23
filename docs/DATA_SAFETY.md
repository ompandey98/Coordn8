# Data safety — what Coordn8 owns, what you own, and what it promises

Coordn8 keeps two kinds of files apart and never mixes them. One kind belongs to the package and
is replaced every time you install or update. The other kind belongs to you, and the package
treats it as read-only except in the few narrow, backed-up ways listed below.

All paths come from one file, `scripts/lib/paths.py`. Nothing else in the package hard-codes a
location.

## The two zones

### 1. Package-owned — replaced on every install

These are the files Coordn8 ships. An install or update may overwrite or delete them freely,
because any edit you make to them would be lost on the next update anyway.

- the installed skill, slash command, agents and hooks (under `~/.claude/`, or wherever the host
  keeps them)
- the `coordn8` launcher and the package copy that `npx` downloads
- generated adapter files that consist entirely of Coordn8 content
- the package checkout itself

**If you want to change how Coordn8 behaves, don't edit these.** Put the change in your memory
(below) or in a project's `Coordn8/_research/decisions.md`, and it will survive updates.

### 2. User-owned — never overwritten

**Global user data: `~/.coordn8/`** (tests and sandboxes move it with `COORDN8_HOME`)

| Folder | What lives there |
|---|---|
| `memory/` | your typed memory: `entries.jsonl` (preferences, standing decisions, lessons, failure classes, facts, choices), `choices.jsonl` (what each build chose, so later builds avoid repeating it), and `index.md` (a short generated summary loaded at session start) |
| `library/` | palettes, kits, diagram forms and scenes you or Coordn8 invented (`inventions.jsonl`), and whether they were promoted |
| `profiles/` | saved brand profiles |
| `voices/` | downloaded local voice models; they survive reinstalls so you never download them twice |
| `refs/` | public reference docs fetched at install, each with its fetch date |
| `backups/` | timestamped copies taken before anything of yours is touched |
| `telemetry/` | local run history |
| `schema.json` | the version of the layout above, plus a record of every migration applied |

**Per-project data: every project's `Coordn8/` folder**, including the deliverables, `_build/`,
and `_research/` (the project memory: `project_map.md`, `decisions.md`, `slop_lessons.md`,
`failure_log.jsonl`, `requirements.md`, the retrieval index). This is yours too. Install,
update and uninstall never touch it.

**Your own files that Coordn8 integrates with** (for example an existing `AGENTS.md`,
`.cursor/rules/*`, `.kiro/steering/*`, an MCP config, a settings file) stay yours. Coordn8 edits
them only in the delimited, backed-up way described below.

## The guarantees

1. **Install, update and uninstall only create directories that are missing in your zone.**
   They never modify or delete an existing file in `~/.coordn8/` or in any project's
   `Coordn8/` folder. `schema.json` is created only if it is absent.
2. **Your own files are touched only through one door, `scripts/lib/safe_write.py`, in this
   order:** copy the file to `~/.coordn8/backups/<timestamp>/`; change *only* a block between
   `COORDN8:BEGIN` and `COORDN8:END` markers (or only Coordn8's own keys in a JSON file); check
   that every byte outside that block is unchanged, and abort if it isn't; write atomically, so
   a crash never leaves half a file. Reinstalling replaces only the block. Uninstalling removes
   only the block.
3. **Deleting your data needs two explicit words.** `uninstall` removes package files and the
   Coordn8 blocks it inserted. `uninstall --purge` prints exactly what it would delete, and then
   deletes `~/.coordn8/` only if you also pass `--yes`.
4. **Memory is append-only.** Every memory write adds one complete line to a `.jsonl` file in a
   single write. No existing line is ever edited or removed. If you change your mind, the new
   instruction is added as a new entry that *supersedes* the old one, along with its reason. The
   old entry stays on disk and shows up in `store.py list --all`. A damaged line is skipped
   and reported; it never stops the rest from loading.
5. **Conflicts are surfaced, not resolved behind your back.** If two current preferences or
   decisions on the same subject disagree, both are kept, and `store.py conflicts` (plus a line in
   the session-start index) shows them to you. What you say in the current conversation
   always outranks memory. When the two disagree, the disagreement is reported. Memory is never
   silently obeyed over what you just said.
6. **Stale memory is flagged.** An entry that came from a file records that file's hash. If the
   file changes or disappears, the entry is reported as stale before anyone relies on it.
7. **Migrations back up first, and can be undone.** When an update changes the shape of your
   data, `scripts/memory/migrate.py run`:
   - copies the whole user zone (everything except `backups/` and large voice model binaries)
     into `backups/<timestamp>-pre-migrate-v<old>-to-v<new>/`, with a checksum for every file;
   - converts your data, leaving the old files where they are (migration never deletes them);
   - records the step in `schema.json`.

   Running it again when nothing is pending changes nothing and takes no new backup.
   `migrate.py restore <backup>` checks the backup's checksums, takes a fresh safety backup of
   the current state, and then puts every file back byte-for-byte. If you run a newer data
   version than the package understands, it refuses to migrate and asks you to update.
8. **Doctor only reads.** `coordn8 doctor` reports where your data lives, its schema version,
   whether a migration is pending, how many memory entries, choices, inventions and backups you
   have, and how large the session index is (cap: 4 KB). It never writes or migrates anything.

## Schema history

| Version | Shape |
|---|---|
| 1 | preferences as a flat list of strings in `memory/preferences.json`; entries could lack `scope` and `confidence` |
| 2 | typed entries in `memory/entries.jsonl`: `id, type, scope, text, source_quote, source, date, confidence, supersedes, source_hash, source_path` (optional `subject`, `reason`). Migration from 1 turns each string into a `preference` entry with scope `global`, confidence `med`, source `migrate`, and the original string kept as its `source_quote` |

## Commands

```
python3 scripts/memory/store.py   list|add|supersede|export|index|conflicts|stale [--json]
python3 scripts/memory/migrate.py status|run|restore <backup-dir>
```

(The launcher exposes the store as `coordn8 memory ...` once that command is wired.)
