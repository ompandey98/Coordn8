# Adding Support for a New Tool

Coordn8 is designed to be adaptable to any AI coding tool. Because every adapter
is *generated* from the brain and must embed it verbatim, adding a tool is a
small, safe change in one place.

## 1. Add a generator function

Open [`build/generate_adapters.py`](../build/generate_adapters.py) and add a
function that returns `{relative_path: file_contents}`. Reuse the helpers:

```python
def gen_mytool(brain: str, version: str) -> dict[str, str]:
    front = "---\n... any front-matter your tool requires ...\n---\n\n"
    hint = (
        "Invoked as a MyTool <thing>. Detect shell/ffmpeg/browser and pick the "
        "richest tier. Open at the repo root."
    )
    body = front + preamble("MyTool", hint, version) + fenced(brain)
    return {"mytool/coordn8.ext": body}
```

Two rules that keep the guarantee intact:

- The brain must go inside `fenced(brain)` — this is the byte-for-byte region the
  verbatim test compares. Never modify `brain` before fencing it.
- Keep the wrapper (`preamble` + front-matter) minimal, so the brain dominates
  the context window and retains maximum instruction authority.

Then register it: add `gen_mytool` to the `GENERATORS` list.

## 2. Add an installer target

In [`install.sh`](../install.sh), add an `install_mytool()` that copies the
generated file to your tool's config location, and a `case` branch for it. Add it
to the `all` branch too.

## 3. Regenerate and verify

```bash
python3 build/generate_adapters.py         # writes the new adapter
python3 build/generate_adapters.py --check # must pass
python3 tests/run_tests.py                 # verbatim test now covers your adapter automatically
```

The verbatim test iterates over *all* generated adapters, so your new one is
checked without touching the test file.

## 4. (Optional) expose it via npx

`bin/coordn8.js` delegates `init` to `install.sh`, so `npx github:ompandey98/coordn8 init mytool`
works as soon as the installer knows the target — no CLI change needed.

## Notes on tool-specific formats

Adapter formats change over time. When adding a tool, confirm the *current* expected
location and front-matter from that tool's own docs rather than assuming — e.g.
skill front-matter fields, rule file extensions, steering-doc inclusion modes.
The existing adapters (Claude Code skill/command, Cursor `.mdc`, Codex `AGENTS.md`,
Kiro steering) are working references for the shape.
