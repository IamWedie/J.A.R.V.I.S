# Study Workspace — Design

- Date: 2026-11-09
- Status: Draft for user approval
- Builds on: secure core v3.0.0 (`main` at `01789fb`)
- Version target: 3.1.0

## Intent

The user studies from course material kept in folders under `Documents`. The
material spans PDF, Word, PowerPoint, Excel, plain text, source code, and
images. The user wants JARVIS to generate practice material — flashcards and
quizzes, whichever is asked for — from those files, and wants a workspace where
generated decks can be reviewed, reused, and deleted.

This is the first product feature on top of the secure core. It must preserve
the secure-core invariants: every filesystem read goes through a signed tool and
the broker, scope is fixed by the signed manifest, sensitive actions are
audited, and approvals are request-bound.

## Goals

- Pick one file or a folder under `Documents` and extract its study text.
- Show an extraction preview before generation: files read, formats, character
  counts, skipped files and reasons, and whether the material was truncated.
- Generate flashcards, a quiz, or both from the extracted material on request.
- Save decks locally and list, reopen, review, and delete them later.
- Review flashcards (flip, next/previous, shuffle) and take a scored quiz with
  explanations.
- Keep all file access inside `~/Documents` through a signed, restricted-lane
  tool; never log or persist source text.
- Stay usable with long material through bounded extraction and chunked
  generation, while plainly disclosing any partial coverage.

## Non-goals

- Reading web pages or URLs as study sources in this slice. `web_search` remains
  a separate tool; page fetching is a later feature.
- Auto-watching or indexing folders. Extraction runs only on explicit request.
- Legacy binary Office formats (`.doc`, `.ppt`, `.xls`), OpenDocument formats,
  or Apple formats.
- OCR for scanned PDFs. Image files use Windows OCR; PDFs need a text layer in
  this slice.
- Manual card editing, deck export (Anki/CSV), spaced repetition, or sharing.
- Voice-triggered generation from the chat prompt. The workspace is UI-first in
  v1; adding a natural-language trigger is deferred.
- A local/offline language model. Generation uses the configured AI provider.
- Game updates.

## Anti-goals

The feature fails even if it technically works when any of these happen:

- A file outside `Documents` is read, or a symlink/reparse point escapes the
  scope.
- Source text appears in the audit log, application logs, SQLite deck rows, or
  the UI preview for longer than the active session.
- A large folder is silently truncated and the user is not told.
- Model-generated text is inserted as HTML or executed.
- A deck save partially succeeds, leaving orphan cards or a half-written deck.
- The UI blocks or the WebSocket loop stalls while a long generation runs.

## Constraints

- The tool manifest is Ed25519-signed. Adding the extraction tool requires a
  manifest entry, code hashes, a re-sign with the private key at
  `C:\Users\wadia\.jarvis-signing\tools-2026-11.pem` (never committed), and a
  verifier run.
- Tool calls go through `core.security.broker.Broker.invoke`; file reads must not
  happen in HTTP handlers or command actions directly.
- `CommandRouter` is the only path for non-tool UI actions. New study actions
  are WebSocket commands, not new HTTP endpoints.
- The filesystem scope is fixed at signing time. This design uses `~/Documents`
  only. A study folder on another drive would need a future manifest change.
- Data written by JARVIS lives in the ACL-protected data directory
  (`core.config.data_dir()`). Deck content is model-generated personal data and
  gets the same ACL as the existing database.
- Test suite is hermetic: no real Documents writes, no real model calls, no live
  browser or audio tests. UI coverage is static plus Python-level command tests.
- Windows is the target platform (Windows OCR, Windows paths, Windows ACLs).

## Decisions made with the user

- Source: folders under `Documents`, or an exact file under `Documents`.
- Formats: PDF, Word, PowerPoint, Excel, text, source code, and images.
- Output: flashcards and quizzes, generated as the user asks.
- Shape: a persistent Study workspace, not a chat-only generator.
- All study folders will live under `Documents`; no additional filesystem root
  is needed.

## Approaches considered

### A. Chat-only generator — rejected

Add a file-reading tool and let the chat model produce cards in the
conversation. Smallest change, but nothing is saved, review means scrolling
chat, and long decks are painful. The user explicitly chose a workspace.

### B. Command-routed study workspace — chosen

- One signed `study_extract` tool handles all formats behind one scope and one
  approval.
- New WebSocket commands (`study_scan`, `study_generate`, `study_list`,
  `study_get`, `study_delete`) route through `CommandRouter`.
- A local SQLite store persists decks under the data directory.
- The existing AI client generates structured JSON through a dedicated prompt.
- A Study tab in the existing UI handles source selection, preview, generation,
  deck list, review, and quiz.

This keeps the secure-core boundary intact, gives persistence, and lets the
user approve a folder once (allow-always grant) and then generate repeatedly.

### C. Auto-indexed study library — rejected

Watch `Documents` and pre-extract everything into a searchable index. More
automation, but much more privacy exposure, background CPU/I/O, and a larger
scope before the core workflow is proven.

Within approach B, one tool with a format dispatcher was chosen over one tool
per format. Separate tools would multiply manifest entries and approval prompts
for the same folder. The dispatcher stays readable by putting format readers in
`core/study/extractors.py`, which is listed in the tool's `code_files` and
therefore covered by the signature.

## Architecture

```
UI (Study tab)
  │  WebSocket commands
  ▼
CommandRouter ── audit command.request / command.result
  │
  ▼
StudyService
  ├── Broker.invoke("study_extract", {path, ...})   ← signed tool, restricted lane
  │     └── core.tools.study_tools → core.study.extractors
  ├── in-memory scan cache (session-owned, TTL, never persisted)
  ├── Brain.generate_study_material(...)             ← existing AI client
  ├── JSON validation / repair
  └── StudyStore (SQLite, data/study.db)
        └── decks, cards, quiz_questions
```

### New and changed files

| File | Purpose | Manifest-covered |
| --- | --- | --- |
| `core/tools/study_tools.py` | Manifest handler and argument validation | yes |
| `core/study/extractors.py` | Format readers and extraction limits | yes |
| `core/study/store.py` | SQLite schema and deck CRUD | no |
| `core/study/generation.py` | Prompt building, JSON validation, chunk merge | no |
| `core/study/service.py` | Scan cache and command orchestration | no |
| `core/brain.py` | Add `generate_study_material()` using the existing client | no |
| `core/security/commands.py` | Policies and dispatch entries | no |
| `core/security/features.py` | Add `study: bool = True` | no |
| `core/security/tool_manifest.json` | Add `study_extract` | signed |
| `server.py` | Wire command actions and study service lifecycle | no |
| `ui/index.html`, `ui/app.js`, `ui/style.css` | Study tab, review, quiz | no |
| `requirements.txt` | Add PDF/Office readers | no |
| `tests/study/*`, `tests/security/test_study_*.py` | Hermetic tests | no |

## Tool contract: `study_extract`

- Name: `study_extract`
- Title: "Extract study material"
- Handler: `core.tools.study_tools:extract_study_material`
- Lane: `restricted`
- Capabilities: `filesystem.read`
- Risk: `guarded`
- Feature: `study`, enabled by default
- Filesystem roots: `["~/Documents"]`
- Argument scope: `{"filesystem": ["path"]}`
- Timeout: 120 seconds
- Parameters:
  - `path` (string, required): a fully resolved absolute path under
    `Documents`. The tool contract is absolute-only; `StudyService` normalizes
    every user-facing form first (see Path normalization).
  - `recursive` (boolean, optional, default true): walk subfolders.
  - `max_files` (integer, optional, default 20, hard cap 40).
- Returns a JSON string:

```json
{
  "root": "Study\\Biology",
  "documents": [
    {"path": "chapter1.pdf", "format": "pdf", "chars": 8421, "pages": 12,
     "text": "..."}
  ],
  "skipped": [
    {"path": "scan.jpg", "reason": "ocr_unavailable"}
  ],
  "total_chars": 84210,
  "truncated": false,
  "limits": {"max_files": 20, "max_total_chars": 90000}
}
```

Rules:

- The broker enforces the root scope before dispatch. The handler defensively
  re-resolves and re-checks containment, and rejects reparse points.
- Directory walks skip hidden entries, `~$*` Office lock files, `Thumbs.db`,
  `.git`, `node_modules`, `__pycache__`, virtual environments, and any reparse
  point. Skipped entries are reported with stable reason tokens.
- Recursion depth is capped at 6.
- Files larger than 15 MB on disk are skipped as `too_large`.
- Per-file extracted text is capped at 20,000 characters; total extracted text
  is capped at 90,000 characters. When a cap is hit, `truncated` is true and the
  UI must show a warning before generation.
- Extracted text is returned to the app but never written to the audit log or
  application log. Audit rows carry the tool name, redacted arguments (path),
  decision, and result status only.
- Unsupported extensions and unreadable/corrupt files are reported, not fatal,
  as long as at least one document yields text.

### Result size through the broker

`Broker.invoke` currently truncates every tool result to 8,000 characters for
the model-facing tool loop. That would cut the structured extraction payload and
break generation, so the broker gains one explicit parameter:

- `Broker.invoke(invocation, *, result_limit: int = 8000)`.
- Model-facing callers keep the 8,000-character default and see the current
  `...[truncated]` marker.
- `StudyService` is a trusted in-process orchestrator, not a model-facing caller,
  and passes `result_limit=STUDY_RESULT_LIMIT` (200,000). The extraction tool
  caps itself at 90,000 characters, so the payload always fits.
- The broker still audits the request/result the same way and still never logs
  result content. Only the truncation step changes.
- A hard maximum of 200,000 is enforced inside the broker; larger callers fail
  closed rather than allocate unbounded strings.

This is a narrow extension of the secure-core broker, covered by new tests: the
default remains 8,000, the study caller receives the full payload, and an
over-limit request is rejected.

### Path normalization

`resolve_within_roots` expects a real absolute path; it does not expand `~` or
resolve a relative path for the caller. Study inputs therefore pass through
`StudyService.normalize_study_path(raw)` before the broker is invoked:

- Accepted user forms:
  - absolute `C:\Users\<user>\Documents\...`
  - `Documents\Study\Biology` or `Study\Biology` (relative to `Documents`)
  - `~\Documents\...`
- The helper expands only a leading `~` (never environment variables), joins
  Documents-relative forms against the resolved `~/Documents`, and strips one
  leading `Documents` component from relative input.
- It rejects empty/control, UNC/device/root-relative, drive-relative, `..`,
  ADS/colon components, 8.3 short names, and trailing dot/space components
  before resolution, then proves the final absolute path with
  `resolve_within_roots(path, ["~/Documents"])`.
- The broker and tool receive only the resolved absolute path. Allow-always
  grants therefore key on the real folder, and repeat scans of the same folder
  reuse the grant.
- Tests cover every accepted form plus traversal, UNC, drive-relative, ADS,
  short-name, `~otheruser`, and outside-Documents rejections.

### Supported formats

| Family | Extensions | Reader | Notes |
| --- | --- | --- | --- |
| Text / notes | `.txt`, `.md`, `.rst`, `.csv`, `.log` | UTF-8 with BOM handling, `errors="replace"` | NUL byte in the first 8 KB → binary skip |
| Source code | `.c`, `.h`, `.cpp`, `.hpp`, `.cc`, `.cs`, `.java`, `.kt`, `.php`, `.py`, `.js`, `.ts`, `.jsx`, `.tsx`, `.rb`, `.go`, `.rs`, `.swift`, `.sql`, `.sh`, `.ps1`, `.html`, `.css`, `.json`, `.xml`, `.yaml`, `.yml`, `.ini`, `.toml` | same as text | treated as plain text |
| PDF | `.pdf` | `pypdf` | text layer only; no text layer → `no_text_layer`; 80-page cap per file |
| Word | `.docx` | `python-docx` | paragraphs plus table cell text |
| PowerPoint | `.pptx` | `python-pptx` | shape text frames plus speaker notes; 80-slide cap |
| Excel | `.xlsx`, `.xlsm` | `openpyxl` (read-only, data-only) | sheet name plus tab-separated rows; 10 sheets and 500 rows per sheet cap |
| Images | `.png`, `.jpg`, `.jpeg`, `.bmp`, `.webp`, `.tif`, `.tiff` | `winocr` + Pillow | optional dependency; absent → `ocr_unavailable` |

New runtime dependencies: `pypdf`, `python-docx`, `python-pptx`, `openpyxl`.
`winocr` stays optional; image extraction is skipped with a clear reason when it
is not installed. Pin the new packages at implementation time after verifying
they install on Windows/Python 3.12.

## Commands

All commands require an authenticated, non-revoked session and are routed by
`CommandRouter`. All study commands set `requires_audit=True`. `study_delete`
also sets `requires_confirm=True`.

| Command | Payload | Success event |
| --- | --- | --- |
| `study_scan` | `{op_id?, path, recursive?}` | `study_scan_result` |
| `study_generate` | `{op_id?, scan_id, mode, cards?, questions?, title?}` | `study_generate_result` |
| `study_list` | `{op_id?}` | `study_list_result` |
| `study_get` | `{op_id?, deck_id}` | `study_deck` |
| `study_delete` | `{op_id?, deck_id, confirm: true}` | `study_deleted` |

Every failure emits `study_error` with `{op_id, stage, code, message}`. Every
success event echoes the same `op_id`. The client generates an `op_id` per
action (or the server generates one when absent) so the UI can correlate
responses and ignore stale events.

### Asynchronous command contract

`CommandRouter.handle` awaits its action, and the WebSocket loop awaits the
router. A synchronous `study_scan` would deadlock the approval flow: the broker
waits for a decision while the loop cannot read the `approval_response` message.
The study actions therefore follow the existing `chat` pattern:

- `study_scan` and `study_generate` validate the payload, spawn a tracked
  `asyncio.Task`, and return from the command action immediately. The router's
  `CommandResult` means "accepted", not "finished".
- The task emits `study_status` (`stage: extracting` / `generating`, optional
  chunk progress), then the result or error event.
- The approval dialog is shown by the broker inside the task; the loop stays
  free to process `approval_response`, and the task resumes when the broker gets
  the decision.
- `study_list`, `study_get`, and `study_delete` are short and synchronous, but
  still emit their event from the action.
- One tracked task per session. A second scan or generation while one is active
  returns an immediate `study_error` with `study_busy`.
- On disconnect, logout, revocation, or session replacement, the server cancels
  the session's task and clears its scan cache. Cancellation drops the source
  text from memory and prevents a pending save. A save that has already
  committed before cancellation is durable and appears in the next
  `study_list`; the UI treats that as a normal saved deck.

### Scan cache

- `study_scan` calls the broker with `study_extract`. A guarded tool default
  produces an `ASK`; the existing approval dialog handles it, including
  allow-always scoped grants for the resolved folder.
- The scan result keeps the extracted text in an in-memory, session-owned cache
  keyed by `scan_id`. TTL is 15 minutes. There is one active scan per session:
  a new successful scan replaces the previous entry and invalidates its
  `scan_id`. The cache is never written to disk and is cleared on disconnect,
  logout, revocation, or session replacement.
- `study_generate` requires a `scan_id` owned by the same session and not
  expired. An expired or unknown id returns `study_scan_expired` /
  `study_scan_unknown` and the UI asks for a fresh scan.
- `study_generate` re-validates the cached path and uses the scanned snapshot;
  the UI shows the scan time so the snapshot is explicit.
- Commands do not echo user payloads into the audit log; only the command name
  is recorded, matching the existing `CommandRouter` behavior. Deck save and
  delete append `study.deck_saved` / `study.deck_deleted` audit rows containing
  the deck id and counts, never card or source text. A save or delete that
  cannot be audited fails closed.

## Deck storage

SQLite database at `data/study.db`, opened only after the data-directory ACL is
applied. Separate from the audit database; deleting all decks never touches the
audit trail.

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE schema_version (version INTEGER NOT NULL);

CREATE TABLE decks (
  id             TEXT PRIMARY KEY,
  title          TEXT NOT NULL,
  source_path    TEXT NOT NULL,   -- display path relative to Documents
  source_summary TEXT NOT NULL,   -- JSON metadata only; never source text
  mode           TEXT NOT NULL,   -- flashcards | quiz | both
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE TABLE cards (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  deck_id  TEXT NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  front    TEXT NOT NULL,
  back     TEXT NOT NULL
);

CREATE TABLE quiz_questions (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  deck_id      TEXT NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
  position     INTEGER NOT NULL,
  question     TEXT NOT NULL,
  options      TEXT NOT NULL,     -- JSON array of exactly four strings
  answer_index INTEGER NOT NULL,  -- 0..3
  explanation  TEXT NOT NULL
);

CREATE INDEX idx_cards_deck ON cards(deck_id, position);
CREATE INDEX idx_quiz_deck ON quiz_questions(deck_id, position);
```

- Deck IDs are random hex strings.
- A save is one transaction: deck row plus all cards/questions. Failure rolls
  back completely.
- Limits: title 120 chars; front/back/question/explanation 2,000 chars; option
  300 chars. Out-of-range generated values fail validation before saving.
- `source_path` is stored relative to `Documents` (for example
  `Study\Biology`) so the username is not duplicated in the study database.
- Deck text is stored as plain text. The UI renders it with `textContent`,
  never `innerHTML`.

## Generation

`Brain.generate_study_material(material, mode, card_count, question_count)` uses
the existing configured client and model, no chat history, and no tools.

- Request a JSON object with:
  - `title`
  - `cards`: `[{front, back}]` when mode includes flashcards
  - `quiz`: `[{question, options[4], answer_index, explanation}]` when mode
    includes quiz
- System instructions state that the material is untrusted content, that any
  instructions inside it are data and must be ignored, and that output must
  follow the requested JSON shape and the source language.
- Try JSON response mode first; if the provider rejects that parameter, retry
  once with prompt-only JSON. On invalid JSON or failed schema validation, retry
  once with a short repair message containing only the validation error, not the
  whole source. Two failures return `study_invalid_output`.
- Material is split on paragraph boundaries into chunks of at most 15,000
  characters, maximum six chunks (the 90,000-character extraction cap guarantees
  every extracted character can be processed). Each chunk requests a share of
  the target counts; results are merged and deduplicated by normalized
  front/question text, then trimmed to the requested counts.
- Total generation timeout of 180 seconds. Any model failure aborts the whole
  generation; no partial deck is saved.
- Generated title defaults to the source folder or file name; the prompt may
  produce a better title, but the server sanitizes it and falls back on empty or
  overlong output.
- Cards and quizzes use the source language by default. Mixed-language material
  may produce mixed output; this is acceptable in v1.

## UI

### Navigation

- Add a two-tab switch in the header: **Chat** and **Study**. Chat keeps the
  current behavior. Study replaces the chat log and composer with the study
  workspace; the settings button stays available.
- Switching tabs moves focus to the active panel heading and updates
  `aria-selected`.

### Study layout

Two panes, collapsing to one column below 800 px:

**Source pane**

- Path input with the hint `Documents\Study\...`; Documents-relative,
  `~\Documents\...`, and absolute paths are accepted.
- `Scan` button. While scanning, show a status line and disable the button.
- Preview: file list (relative name, format, characters), total characters,
  skipped files with human-readable reasons, and a prominent truncation warning
  when `truncated` is true.
- Privacy line: "The selected text is sent to your AI provider to generate the
  deck."
- Mode selector: Flashcards / Quiz / Both.
- Counts: cards (default 15) and questions (default 8), constrained to the
  allowed ranges.
- `Generate` button, disabled until a successful scan and while a generation is
  running.
- Deck list: title, date, source, card/question counts. Click opens; delete
  requires confirmation.

### Status and event handling

- The panel waits for `study_scan_result`, `study_generate_result`,
  `study_list_result`, `study_deck`, `study_deleted`, `study_status`, and
  `study_error`; the command acknowledgement alone only means "accepted".
- Each action gets an `op_id`; events without the current `op_id` are ignored.
- Scan and generate show a status line and disable their controls while active.
  A `study_busy` error leaves the current preview intact.
- On disconnect or session replacement, the panel clears the active preview and
  any pending operation; the server cancels the corresponding task.

**Workspace pane**

- Empty state explains the workflow.
- Deck view: title, source, scan warning if the deck was built from truncated
  material, and tabs for Cards / Quiz.
- Cards: one card at a time, front shown first, flip with click or
  Enter/Space, previous/next, shuffle, position indicator.
- Quiz: one question at a time, four options, submit, immediate correctness and
  explanation, next, final score, restart.
- Delete button with a confirm step.

### Accessibility and safety

- All controls are real buttons/inputs with labels; status uses `aria-live`.
- Generated text is rendered with `textContent`; no HTML or Markdown execution.
- Path and deck content are never placed in `innerHTML`.
- Keyboard operation covers tab switching, card flip, quiz options, and quiz
  navigation.
- English and Arabic labels are added to the existing client-side translations;
  language switching updates the study UI without reloading.
- No live browser test in this project; static tests assert IDs, labels,
  translation keys, and the absence of `innerHTML` for deck content.

## Security and privacy

- **Scope**: only `~/Documents`, enforced by the manifest and re-checked in the
  handler. Reparse points skipped. No UNC, device, drive-relative, or `..`
  paths (existing `resolve_within_roots` rules).
- **Approval**: `study_extract` is guarded, so the broker asks unless a scoped
  grant exists. The user can allow the folder once with allow-always.
- **Audit**: `command.request`/`command.result` for every study command;
  `tool.request`/`tool.result` for extraction. Source text is never an audit
  field. Extracted text is not logged by the service.
- **Data at rest**: deck rows live in `data/study.db` under the existing
  data-directory ACL. Source text is not stored. Scan cache is memory only and
  is dropped when the session ends or the task is cancelled.
- **Result handling**: the broker's default 8,000-character truncation stays for
  all model-facing calls; only the trusted StudyService raises it to the
  200,000-character hard cap. The extraction tool caps itself at 90,000.
- **Egress**: the only outbound path is the existing AI provider call. No new
  egress allowlist entries and no new network tool.
- **Untrusted content**: source documents and model output are treated as data.
  The prompt delimits material and instructs the model to ignore embedded
  instructions. Model output is schema-validated before storage and rendered as
  text.
- **Failure behavior**: audit failure blocks study commands; extraction failure
  blocks generation; invalid model output is not saved; corrupt `study.db`
  surfaces a clear error and does not affect the audit database.

## Error handling

Stable error codes returned to the UI:

- `study_path_invalid`, `study_path_outside`, `study_no_supported_files`,
  `study_empty_text`, `study_scan_expired`, `study_scan_unknown`,
  `study_busy`, `study_model_unavailable`, `study_model_error`,
  `study_invalid_output`, `study_not_found`, `study_db_error`,
  `study_confirm_required`.

Messages are short and actionable. Path errors do not echo raw paths beyond
what the user entered. Skipped-file reasons use stable tokens that the UI maps
to English/Arabic text.

## Testing strategy

- **Extractors** (hermetic `tmp_path`): text, Markdown, code, binary skip,
  corrupt files, unsupported extensions, per-file and total caps, truncation
  flag, depth limit, hidden/temp exclusions. Tiny PDF, DOCX, PPTX, and XLSX
  fixtures are generated in the test process or embedded as bytes; image OCR is
  mocked. No real Documents access.
- **Tool and manifest**: `study_extract` spec validates; signature covers both
  code files; tool is disabled when `study` is off; broker denies a path outside
  the root; guarded policy asks; audit rows contain no extracted text; restricted
  lane is used. Broker result-limit tests: default 8,000, study caller gets the
  full payload, over-200,000 request fails closed.
- **Path normalization**: every accepted form (`Documents\...`, relative,
  `~\Documents\...`, absolute) resolves correctly; traversal, UNC, device,
  drive-relative, ADS, short-name, `~otheruser`, and outside-Documents forms are
  rejected before the broker is called.
- **Store**: create/list/get/delete, cascade, transaction rollback, limits,
  corrupt database error path, data-directory placement.
- **Generation**: mocked model returns valid JSON, invalid JSON then valid,
  invalid twice, unsupported JSON mode fallback, chunking and dedupe, prompt
  contains delimiters and ignores embedded instructions, timeout, no API key.
- **Commands**: unauthenticated/revoked session rejected; audit failure blocks;
  confirm required for delete; scan cache session isolation, TTL, replacement by
  a new scan; `study_busy`; `op_id` echo; event delivery for every success/error
  path; disconnect cancels an in-flight scan/generation and leaves no pending
  deck; malformed payloads.
- **UI static**: study panel IDs and controls exist; translation keys exist in
  both languages; no `innerHTML` use for deck content; tab wiring, event
  listeners, and `op_id` handling present.
- **Packaging**: `jarvis.spec` hidden imports include the new core modules and
  the existing CI build job succeeds on `main`.
- **Regression**: full suite, `compileall`, manifest verifier, CI on Windows.
- No live browser/audio verification, per project constraint.

## Rollout

- Version 3.1.0; update `CHANGELOG.md` and `README.md`.
- Update `SECURITY.md`: `~/Documents` extraction scope, audit behavior, deck
  storage, privacy note about sending extracted text to the AI provider, and the
  optional OCR dependency.
- Add dependencies to `requirements.txt`; keep `winocr` optional and document
  the image limitation.
- Re-sign the manifest (32 tools expected: 31 current + `study_extract`) and run
  `scripts/verify_tool_manifest.py`.
- Update `jarvis.spec` hidden imports for the dynamically loaded
  `core.tools.study_tools` plus `core.study.extractors`, `core.study.store`,
  `core.study.generation`, and `core.study.service`. The PDF/Office libraries are
  statically imported by the extractor module, so PyInstaller follows them;
  `winocr` is added only when `importlib.util.find_spec("winocr")` succeeds. The
  existing CI build job on `main` is the frozen-build check.
- CI installs the new dependencies from the pinned requirements; no workflow
  change beyond what already exists.
- New `study.db` is created on first use; no migration is required. Existing
  data directories and audit databases are untouched.

## Deferred

- Scanned-PDF OCR (needs rasterization plus OCR).
- Legacy `.doc/.ppt/.xls`, OpenDocument, and Apple formats.
- Voice/natural-language trigger from chat ("make flashcards from ...").
- Deck editing, export to Anki/CSV, spaced repetition, and statistics.
- Handwriting, math, and diagram understanding.
- Folder auto-indexing and background extraction.
- Offline/local model generation.
- Multi-user profiles or sharing.
- Additional filesystem roots outside `Documents`.

## Builder calls

These are not user decisions; they are implementation choices recorded so the
reviewer can check them:

1. SQLite over JSON files for decks: atomic saves, simple list/delete, and the
   existing ACL/data-directory story outweigh the small schema overhead.
2. One `study_extract` tool with a format dispatcher rather than one tool per
   format: one scope and one approval per scan.
3. Scan-then-generate with an in-memory cache rather than reading files twice:
   avoids a second approval and guarantees the deck matches the preview.
4. Store source paths relative to `Documents` in the deck database; absolute
   paths remain in audit arguments, which are already ACL-protected.
5. Cap extraction at 90,000 characters and generation at six chunks so every
   extracted character can be processed; disclose truncation instead of silently
   dropping content.
6. Default 15 cards and 8 questions; user-adjustable within 5–40 and 3–20.
7. UI-first in v1; chat/voice triggering is deferred until the workspace is
   proven.
8. Add an explicit `result_limit` to `Broker.invoke` (default 8,000) instead of
   chunking the extraction result: internal orchestration needs the full payload,
   model-facing behavior stays unchanged, and the broker keeps a hard 200,000
   cap.
9. Study actions are acknowledge-then-event background tasks: a synchronous
   command would deadlock the approval dialog because the WebSocket loop awaits
   the router.
10. Normalize user paths in `StudyService`, not in the broker or tool. The broker
    and manifest keep their existing absolute-path contract; this avoids widening
    the security-sensitive scope matcher.
11. One active scan per session. A new scan replaces the old one; going back
    means rescanning, which usually reuses the allow-always grant.
12. Cancel in-flight study tasks on disconnect/logout/revocation. A committed
    deck is durable; an uncommitted one is discarded with the source text.
13. The frozen build is in scope: `jarvis.spec` gets hidden imports and the
    existing `main` build job is the check.
