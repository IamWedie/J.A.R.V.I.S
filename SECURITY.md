# JARVIS 3.1 Security Model

This document states what J.A.R.V.I.S defends against, exactly which
mechanisms do the work, and where each piece of data lives. It describes
the personal, source-run build that ships in this repository; no feature
described as "isolated" or "restricted" is sandboxed at the virtualization
level, and wording throughout avoids claiming a security property that
does not exist.

## Threat boundary

**Inside the boundary (defended):**

- Untrusted web content: URLs are checked against a strict egress
  allowlist before any tool may fetch or open them.
- Unauthenticated network clients: every HTTP and WebSocket route requires
  a per-launch bearer token; no network listener is opened for it.
- Other local user accounts: data directories get best-effort ACLs
  limiting access to the current user, and secrets are DPAPI-protected to
  the user's own Windows session.
- Anything described as malicious that crosses the authenticated UI
  boundary (unauthed API calls, replayed approvals, tampered policy or
  audit files).

**Outside the boundary (assumed lost):**

- Native processes running as the *same* Windows user. Such a process can
  read the launch token (e.g. from the browser or process memory), read
  every data file, and call DPAPI. Defending against same-user code
  requires true virtualization isolation, which JARVIS does not attempt.
- A DBA/administrator-level compromise of the machine, or physical disk
  access.

## Authenticated local UI

- A fresh random **launch token** is generated per app launch. It is not
  stored in a reusable persisted secret; it exists only for this process
  lifetime and is injected into the served page as a meta tag.
- Every `/api/*` route (except `/api/health`) is checked by middleware for
  the `X-JARVIS-Token` header; failures get a bare `401` with no detail.
- The WebSocket requires authentication **before any command is
  processed**: the client first sends `{cmd: "auth", token: ...}`. Any
  other command before auth is ignored, and repeated failures close the
  socket.
- Origin requests from outside the local app's own origins are rejected.
- Exactly **one authenticated control session** is active at a time. When
  a second window authenticates, the older session is closed, its pending
  approvals are cancelled, and its socket gets a `4001` close code.
- Responses carry hardened security headers (no-store, frame denial, and
  friends, in `core/security/http.py`).

## Signed tool manifest

- Tools are declared in `core/security/tool_manifest.json`. Each entry
  carries the tool's risk, capabilities, filesystem roots, egress
  allowlist, and per-file SHA-256 hashes for the tool's implementation.
- The manifest is **Ed25519-signed**; the signature is verified against a
  compiled public keyring (`core/security/tool_pubkeys.py`). The private
  signing key is held offline by the maintainer and its compromise is out
  of scope for this boundary; **never commit the private key**. Keep an
  offline backup of it (`scripts/sign_tool_manifest.py`,
  `scripts/rotate_tool_key.py`).
- At every startup the manifest is verified, and in source mode each
  tool's code files are hashed against their manifest entries. **Frozen
  (packaged) builds fail closed**: an unverifiable manifest leaves the
  registry empty and every tool call denies. Source builds tolerate a
  missing signature for development, but the shipped verifier always
  exits nonzero unless `verified=True`.
- A tool never runs without both (a) a verified manifest entry and (b) a
  policy ALLOW verdict (see below).

## Capability policy and grants

- **Default deny**: tools not explicitly allowed by manifest maximums and
  policy cannot run.
- Scope checks resolve every filesystem path inside the manifest roots
  (no escaping), every host against the egress allowlist, and every app
  target against the app allowlist *and* the configured shortcut sets.
- The policy file (`security_policy.json`) is an HMAC-SHA256 envelope
  bound to the audit key. Any unrecoverable defect marks the engine
  `corrupt`, which denies every tool and refuses all mutations; the
  engine **never repairs or widens** policy state.
- Guarded tools the policy answers ASK for can be granted a narrow,
  stored scope ("always allow Music file search") from the approval
  prompt ("Allow always"). Grants have an expiry, are listed in Settings
  with a revoke button, and every add or revoke is audited as
  `policy.change` with an explicit `decision` field (`"grant"` or
  `"revoke"`). A reset (below) archives the key-signed policy file, which
  invalidates all old grants.

## Study workspace

- Study files are read only through the signed `study_extract` tool. Its
  manifest entry declares `filesystem.read`, risk `guarded`, the
  `restricted` lane, feature `study`, one allowed root (`~/Documents`),
  and an argument scope limiting `filesystem` to `path`; its two code
  files are SHA-256-pinned like every other tool.
- The tool refuses paths outside Documents, reparse points/junctions,
  hidden and temporary files, and oversized inputs. It performs no
  network access. Images are OCR'd only when the optional `winocr`
  package is installed; scanned PDFs without a text layer are reported as
  skipped instead of being OCR'd in this release.
- The five study commands map to segregated capabilities: `study_scan`,
  `study_list`, and `study_get` use `study.read`; `study_generate` uses
  `study.write`; `study_delete` uses `study.delete` and additionally
  requires `{confirm: true}`. Every study command is audited before
  dispatch (`command.request`) and when the router accepts it
  (`command.result`); scan and generation then continue in a tracked task.
  Those command rows carry the command name and capability, never paths,
  deck ids, titles, or source text. Deck save/delete events add only the
  deck id and card/question counts.
- Extracted source text lives only in the in-memory scan cache
  for 15 minutes. It is never written to `audit.db`, `study.db`, or
  server logs; scan previews expose relative file names, formats,
  character counts, and skip reasons only. Tool-request audit rows may
  contain the validated absolute source path (truncated like every other
  argument), but never file contents or extracted text.
- `study.db` stores generated deck content the user chose to create:
  title, source path, source summary (file counts and truncation flag),
  mode, creation time, flashcards, and quiz questions/options/answers/
  explanations. Deleting a deck deletes that row and its children.
- Generating a deck sends the selected text to the configured AI provider.
  The Study UI states this before the Generate button. Nothing else about
  the files leaves the machine.

## Approvals

- ASK-verdict actions surface a request-bound approval UI: a random
  `request_id` shown to the user, owned by the authenticating session,
  single-use, and expiring on timeout. Approvals cannot be resolved for
  another session or another request id; closing/cancelling clears them.
- Sensitive and critical actions are **never** approved by voice, only in
  the UI. Voice approval exists only for guarded actions with an enrolled
  voice match; a rejected voice match denies the action.
- Destructive actions (memory wipe, audit reset) additionally require
  explicit in-UI confirmation dialogs.
- No tool executes if its mandatory pre-execution audit write fails.

## Audit log

- Every decision path writes a SQLite row in `audit.db`,
  **hash-chained** (`prev_hash` -> `entry_hash = SHA256(prev_hash ||
  canonical JSON)`), with the chain tip HMAC-bound: each row carries
  `HMAC-SHA256(key, entry_hash)` so edits are detectable without the key.
  The log lives in the data dir (`audit.db` + `audit.key`, the latter
  DPAPI-protected).

- Arguments are **redacted** before hashing: keys matching
  `apikey|token|secret|password|pin|licensekey` are replaced with
  `[REDACTED]`, and everything else is truncated, so raw secrets never
  appear in the chain or in logs.

- Retention: at startup the runtime prunes events older than **90 days**;
  pruning re-anchors the chain so it stays verifiable.

- **Tools:** `scripts/audit_verify.py` verifies a chain read-only (never
  creates the DB or key, exits nonzero on tampering or a dropped table);
  `scripts/audit_export.py` verifies, then exports NDJSON;
  `scripts/audit_reset.py` archives and resets with `--confirm`. The same
  archive-and-reset helper serves both the script and the HTTP endpoint
  (server must be stopped for the script).

- **Audit-locked recovery:** if the audit key is unreadable, startup
  enters `audit_locked`; the status endpoint and Settings show it, and the
  reset button appears only in that state. Confirming the reset archives
  the old DB, key, and policy into `audit_archive/` (UTC microsecond
  names), creates a new key/DB, writes one `audit.reset` event, and
  writes `audit_recovery.json` pointing at every archived file. Archived
  DB/key/policy files are **never deleted or overwritten**. The old
  policy is archived because it is HMAC-bound to the retired key, so old
  grants must be re-approved.

## Disabled features

The phone (ADB) control, Chromecast cast controller, LAN messaging,
Telegram bot, license server client, and auto-updater are **off by
default** (`core/security/features.py`). When disabled, their optional
modules are not imported, no ADB/command runs, and no extra port is
opened by JARVIS. Don't rely on them being on: check `feature.toggle` and
`/api/security/status`, which always lists what is actually disabled.
Where ADB is ever used, every model-reachable argument is validated or
quoted, but again — always off by default.

## Execution lanes

Tools run through lanes: restricted/egress-capable lanes run in Windows
**Job Objects** (process/object limits only), and the privileged host
lane talks to the main process via an **anonymous pipe**. None of these
is sandboxed at the OS virtualization level; they bound resource use and
isolate file handles, nothing more.

## Data locations

- Packaged (frozen) builds: `%LOCALAPPDATA%\JARVIS` (`audit.db`,
  `audit.key`, `security_policy.json`, `study.db`, vault DB, memories DB,
  settings).
- Source runs: the repository root (the directory containing
  `server.py`, where `audit.db`, `audit.key`, `security_policy.json`,
  `study.db`, and `.env` live; path helpers in `core/config.py`).
- Secrets vault: DPAPI-encrypted store for API keys and the like, with
  ACLs best-effort limited to the current user.
- On audit reset: the archived DB/key/policy land under
  `%LOCALAPPDATA%\JARVIS\audit_archive\` (or the source-run data
  directory) and are never removed; `audit_recovery.json` records
  absolute paths.
