# Third-party components

Everything Coordn8 downloads, vendors or calls, with its licence and where it comes from. Nothing here is a hosted AI
service: every model listed runs locally after a one-time download, and nothing you narrate or build is sent anywhere.

| Component | Version | Licence | Source | Used for | How it is used |
|---|---|---|---|---|---|
| @playwright/mcp | 0.0.83 | Apache-2.0 | github.com/microsoft/playwright-mcp | browser automation and screenshots | MCP server, registered in the host config, not bundled |
| mcp-server-fetch | 2026.10.19 | MIT | github.com/modelcontextprotocol/servers (src/fetch) | fetching current public docs as Markdown | MCP server, registered, not bundled |
| @modelcontextprotocol/server-filesystem | 2.0.0 | Apache-2.0 AND MIT | github.com/modelcontextprotocol/servers (src/filesystem) | project-scoped file access (needs Node ≥ 24) | MCP server, registered, not bundled |
| threenative-blender-mcp | 0.1.6 | MIT AND GPL-2.0-or-later | github.com/ThreeNativeHQ/threenative (packages/blender-mcp) | driving a local Blender, only when Blender is installed | MCP server, registered, not bundled |
| dagre (@dagrejs/dagre, bundles @dagrejs/graphlib 4.0.5) | 3.1.1 | MIT | npmjs.com/package/@dagrejs/dagre | layered-graph layout for diagrams | vendored by `fetch_visual_libs.py`, sha256-pinned |
| GSAP | 3.13.0 | GSAP standard no-charge licence | gsap.com | eased scene motion | vendored, sha256-pinned |
| D3 | 7.9.0 | ISC | d3js.org | data-driven charts and graphs | vendored, sha256-pinned |
| Rough.js | 4.6.6 | MIT | roughjs.com | hand-drawn style | vendored, version recorded |
| anime.js | 3.2.2 | MIT | animejs.com | lightweight keyframe motion | vendored, sha256-pinned |

## Local voice engines (installed at run time into ~/.coordn8/voices/, never shipped in the package)

| Component | Version | Licence | Source | Used for |
|---|---|---|---|---|
| Kokoro-82M weights (fp16 ONNX export) + voice pack | v1.0 (`kokoro-v1.0.fp16.onnx` sha256 f3a290d3…77ac96, `voices-v1.0.bin` sha256 bca610b8…1fbf7d) | Apache-2.0 | GitHub release assets of the kokoro-onnx project, tag `model-files-v1.1` | neural narration voice (default engine) |
| kokoro-onnx | 0.6.1 | MIT | PyPI | Kokoro runtime (in its own Python 3.11 venv) |
| onnxruntime | 1.31.0 | MIT | PyPI | model inference (both venvs) |
| phonemizer | 3.4.0 | GPL-3.0-or-later | PyPI | text → phonemes for Kokoro (dependency of kokoro-onnx) |
| espeakng-loader | 0.2.4 | MIT wrapper; bundles the espeak-ng 1.52.0 library + data, GPL-3.0 | PyPI | phonemizer backend |
| soundfile | 0.14.0 | BSD-3-Clause | PyPI | wav writing in the Kokoro worker |
| numpy | 2.4.6 | BSD-3-Clause (+0BSD, MIT, Zlib, CC0 parts) | PyPI | arrays (both venvs) |
| piper-tts | 1.8.0 | GPL-3.0-or-later | PyPI | fast neural engine, run through its own command line (arm's length) |
| Piper voice en_US-ljspeech-medium | repo rev 5041a7a4 | dataset public domain (LJ Speech); voice trained from scratch | piper-voices model repository | voice |
| Piper voice en_GB-cori-medium | repo rev 5041a7a4 | dataset public domain (LibriVox); trained from scratch | piper-voices model repository | voice |
| Piper voice en_US-norman-medium | repo rev 5041a7a4 | dataset public domain (LibriVox); trained from scratch | piper-voices model repository | voice |
| macOS `say` | OS | Apple (part of macOS, not redistributed) | operating system | last-fallback voice |

Notes: the last MIT piper-tts (1.2.0) needs piper-phonemize 1.1.0, which publishes no macOS arm64 wheel, so it cannot
install on Apple-silicon Macs; 1.8.0 (GPL) is used as a separately installed tool. Piper voices with non-commercial or
research-only data (hfc_female, hfc_male, ryan, semaine; lessac and voices fine-tuned from it) were skipped.
Small runtime deps (attrs, cffi, cloudpickle, dlinfo, flatbuffers, joblib, packaging, protobuf, pycparser,
typing_extensions, pathvalidate) are MIT / BSD / Apache-2.0 / PSF-2.0 / MIT-0.

The layout library is also bundled at `scripts/render/vendor/dagre.min.js` (byte-identical to the npm 3.1.1 `dist/dagre.min.js`, sha256 3152d214…) with its MIT licence text at `scripts/render/vendor/dagre.LICENSE`, so diagram layout works offline from a fresh install.
