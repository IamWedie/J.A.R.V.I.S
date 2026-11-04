# Secure Core and Capability-Based Tooling

**Status:** Proposed  
**Date:** 2026-11-01  
**Repository:** `IamWedie/J.A.R.V.I.S` at `c1b13ec`  
**Scope:** Security foundation for a personal Windows assistant used for study, work, and gaming.

---

## 1. Intent

Make JARVIS safe to leave running on a personal laptop, while keeping the useful parts: web research, launching apps, focus timers, window control, and a foundation for gaming workflows.

The current app is a broad feature demo. It has no authentication boundary, it sends roughly 70 tool schemas to the model on every round, it lets any tool call happen directly in the server process, and it gives the model a large set of desktop, phone, and network capabilities. The security problem is architectural, not a handful of missing checks.

This design introduces four things:

1. A capability-based permission system.
2. Signed tool manifests with code integrity checks.
3. Isolated tool execution with explicit sandbox and privileged lanes.
4. Request-bound, tamper-evident audited approvals.

It also removes risky features from the default runtime until they are individually hardened.

---

## 2. What Exists Today

Relevant current behavior:

- `server.py` exposes an unauthenticated WebSocket at `/ws` and accepts approval commands from any connected client.
- CORS protects some HTTP reads but does not protect WebSockets or prevent cross-origin state-changing requests.
- `core/brain.py` owns a large `TOOLS` list and a direct `TOOL_FUNCTIONS` dispatch table.
- Only 11 tools are approval-gated. The voice approval parser checks approval words before denial words, so phrases like “I don’t approve” and “no, don’t do it” can approve destructive actions.
- Phone/ADB commands interpolate arguments into shell strings.
- The updater downloads and executes unsigned release assets and does not reject Windows drive-path zip entries.
- The LAN message receiver binds `0.0.0.0:9998`, accepts unauthenticated messages, and injects them into HTML.
- The vault writes a `.vault` file but leaves the plaintext `.env` in place.
- `voiceid.data_dir()` checks `getattr(os, "frozen", False)` instead of `sys.frozen`, so packaged builds can store memory and voice profiles in the app directory.
- Phone, Chromecast, LAN messaging, license server, and auto-update are all part of the default code path or product surface.
- Tests cover basic modules but not the security boundaries.

---

## 3. Goals

- **G1 — Authenticated control boundary.** Only the bundled UI and its trusted voice pipeline can send commands. Cross-origin clients cannot connect or submit state-changing requests.
- **G2 — Explicit tool authority.** Every tool has a signed manifest that declares its schema, implementation, code hashes, capabilities, risk, execution lane, and timeout.
- **G3 — Default deny.** A tool runs only if its manifest is valid, its feature is enabled, and the policy grants the required capabilities.
- **G4 — Trustworthy approvals.** Approvals are tied to one request and one session, are single-use, expire, and cannot be approved by voice for sensitive or critical actions.
- **G5 — Contained execution.** Non-interactive tools run in a restricted worker process with job-object limits. Interactive tools run in a separate privileged host, not inside the server process.
- **G6 — Auditable decisions.** Every authentication failure, approval, policy decision, tool invocation, and result is recorded in a tamper-evident, redacted audit log.
- **G7 — Secure default configuration.** Phone, Chromecast, LAN messaging, Telegram control, the license server, and auto-update are disabled by default and cannot open ports or import optional dependencies when disabled.
- **G8 — Preserve usability.** The UI remains a voice-first local assistant. Approvals are understandable and do not require a developer console.
- **G9 — Verifiable.** A clean Windows CI run installs the core dependencies and executes security regression tests without optional phone, cast, or OCR dependencies.

---

## 4. Non-Goals

- Building the gaming update integration in this phase. This design creates the safe foundation for it.
- Hardening phone, Chromecast, LAN messaging, Telegram remote control, the license server, or the updater. These are disabled, not deleted, and require their own threat model before re-enabling.
- VM-grade or container-grade isolation for every tool.
- Multi-user support, remote access, or cloud sync.
- Rewriting the LLM interaction layer or the visual design.
- Protecting against an attacker with administrator/SYSTEM access, a debugger, physical access, or control of the signing key.

---

## 5. Anti-Goals

The following outcomes are failures even if the code “works”:

- A security layer that silently allows access when verification or audit fails.
- An approval that any connected client can resolve.
- An unsigned or tampered tool accepted in a release build.
- API keys, PINs, tokens, or typed secrets written to logs or audit records.
- Calling an interactive desktop tool “sandboxed” when it still has full user access.
- Keeping a feature enabled merely because it already exists.

---

## 6. Threat Model

**In scope:**

- A malicious web page or other untrusted web content attempting to control `127.0.0.1:8741`.
- An unauthenticated client on the local network sending forged messages or probing the local message port.
- Prompt injection through web results, OCR text, screen content, or documents causing the model to request dangerous tools.
- Tampered tool metadata or tool source files.
- Approval confusion, replay, cross-request approval, and accidental destructive actions.
- Secrets left in plaintext at rest or leaked through logs.
- Tool crashes, hangs, or resource exhaustion taking down the assistant.

**Out of scope:**

- A native process running as the same Windows user. Such a process can already read the user profile, obtain the local token, and access the DPAPI vault.
- A native process running as another local user with loopback access. Browser-based cross-origin access is still blocked by the token; native local IPC identity is a later hardening step.
- An attacker with administrator/SYSTEM privileges, a debugger, or physical access.
- A compromised signing key.
- A malicious tool that has been correctly signed by a trusted key.
- Zero-day vulnerabilities in Python, Windows, or third-party packages.
- The cloud LLM provider reading prompts. That is a disclosed data-processing boundary, not something this design prevents.

**Trust boundary:** The per-launch token and Origin check protect against untrusted web content and unauthenticated network clients. They are not a defense against a native local process, which can fetch `/` directly and read the token. The policy file, audit key, and signed tool files are protected by the Windows user profile, not against that process. This limitation is explicit so the design does not claim more isolation than it delivers.

---

## 7. Approaches Considered

### A. Boundary patches only

Add WebSocket authentication, fix the approval parser, sanitize ADB arguments, verify updates, and lock down the LAN receiver. Keep all features.

- **Pro:** fastest, no feature loss.
- **Con:** leaves a broad, unaudited tool surface and does not address prompt injection or approval confusion structurally. Rejected.

### B. Secure core with a capability broker

Harden the core boundary, introduce signed manifests and a capability policy, isolate execution, audit decisions, and disable features that are not needed for study/work/gaming.

- **Pro:** matches the actual use case, reduces attack surface, and creates a foundation for safe additions.
- **Con:** temporarily removes phone, TV, LAN, and update features. Chosen.

### C. Full zero-trust rebuild

Run every tool in an OS-level container or AppContainer with separate identities, signed packages, and a remote broker.

- **Pro:** strongest isolation.
- **Con:** too large for the first release; interactive desktop, audio, and input tools need the user’s session and cannot be fully containerized without significant platform work. Deferred to a later hardening phase.

**Chosen approach:** B, with the signed-manifest and audit elements of C. Execution isolation is practical process isolation first; OS-level containment is a follow-up.

---

## 8. Architecture

```text
Bundled UI (WebView2)
        │  local HTTP + token
        │  WebSocket auth handshake
        ▼
FastAPI server ──► Session/auth ──► Command router
                                      │
                                      ▼
                                    Brain
                                      │  enabled schemas only
                                      ▼
                                 Tool Broker
                    ┌─────────────────┼─────────────────┐
                    │                 │                 │
              Manifest verifier  Policy engine   Approval manager
                    │                 │                 │
                    └─────────────────┼─────────────────┘
                                      ▼
                                  Audit log
                                      │
                    ┌─────────────────┴─────────────────┐
                    ▼                                   ▼
          Restricted worker                     Privileged host
       (job object, sanitized env)          (desktop/audio/input tools)
```

No tool is imported and called directly by `core/brain.py`. The brain asks the registry for enabled schemas and sends every invocation through the broker.

---

## 9. Components

### 9.1 Authentication and session boundary

**Startup token**

- Generate a 32-byte random token with `secrets.token_urlsafe(32)` at process start.
- Keep it only in memory. Rotate on every launch.
- Serve `/` as a dynamic `HTMLResponse` that injects `<meta name="jarvis-token" content="...">` into `ui/index.html`; never log the token.
- Add no-cache headers and avoid caching the token in browser storage.

**WebSocket**

- Keep the server bound to `127.0.0.1`.
- Validate the `Origin` header against `http://127.0.0.1:8741` and `http://localhost:8741` as defense-in-depth.
- Require the first client message within 5 seconds:
  ```json
  {"cmd": "auth", "token": "..."}
  ```
- Compare with `secrets.compare_digest`. Reject missing, malformed, or incorrect tokens with close code `1008`.
- Allow no commands other than `auth` before authentication.
- Allow at most 3 failed attempts per connection, then close.
- The token is mandatory even if `Origin` is missing or spoofed. Origin checking alone is not an authentication mechanism.

**HTTP**

- Require `X-JARVIS-Token` on all `/api/*` endpoints except a minimal unauthenticated health endpoint.
- Keep CORS restricted to the two local origins.
- The UI adds the token from the meta tag to every `fetch` request.
- Remove `/api/approve`; approvals happen only over the authenticated WebSocket.
- Add security headers: `Content-Security-Policy`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`.

**Sessions**

- Each WebSocket connection is a session with a random session ID.
- Only one authenticated control session is active at a time. A new authenticated connection replaces the previous one after a short grace period; this keeps voice approvals and audit attribution unambiguous.
- Approval requests are bound to the session and source that created them.
- Disconnecting a session cancels its pending approvals and audits the cancellation.
- The in-process voice pipeline uses a trusted local principal but still needs the authenticated UI session for sensitive approvals. If no session exists, the broker asks the app to show the window and waits up to 10 seconds before denying.
- Because the UI is served with the token, a native local process can read it; that process is outside the threat model. A cross-origin web page cannot read the token because of the same-origin policy and the restricted CORS policy.

### 9.2 Signed tool manifests

Use one signed manifest bundle at `core/security/tool_manifest.json` plus a detached signature at `tool_manifest.sig`. A single bundle is simpler to maintain than one signature per tool; per-tool signing can come later if third-party plugins are supported.

Each tool entry contains:

- `name`
- `title`
- `description`
- `handler` (`module:function`)
- `parameters` (JSON Schema)
- `capabilities` (list)
- `risk` (`safe`, `guarded`, `sensitive`, `critical`)
- `lane` (`restricted`, `privileged`, `egress`)
- `timeout_seconds`
- `enabled_by_default`
- `version`
- `code_files` with SHA-256 hashes
- optional `filesystem_roots`
- optional `egress_allowlist`
- optional `privacy_notes`

**Signing**

- Use Ed25519, already available through the project’s `cryptography` dependency.
- Canonicalize the manifest with sorted keys, compact separators, UTF-8, and no trailing whitespace.
- Sign the canonical bytes; store the signature and key ID in `tool_manifest.sig`.
- Compile the trust keyring into a Python module constant, or a read-only resource covered by the executable signature. Do not load the public key from a writable JSON data file.
- Keep the private signing key outside the repository. Provide:
  - `scripts/rotate_tool_key.py` to create or rotate a keypair.
  - `scripts/sign_tool_manifest.py` to compute code hashes and sign the manifest.
  - `scripts/verify_tool_manifest.py` for CI.

**Verification**

- In source development, verify the signature first, then each listed source file hash.
- Normalize line endings (`\r\n` → `\n`) before hashing so Windows and CI checkouts agree.
- In frozen PyInstaller builds, tool code lives inside the PYZ/executable, not as standalone `.py` files. Per-file source hashes do not describe the executed bytes. Frozen builds therefore verify the manifest signature, the embedded public key, and the executable’s Authenticode signature. `code_files` hashes apply to source/dev runs and external tool packages.
- Import handlers only after verification, using absolute package imports. Remove writable directories from `sys.path` so a sibling file cannot shadow `core.tools`.
- On failure: disable that tool, write a `manifest.reject` audit event, and show a clear UI warning. The app still starts with safe built-ins.
- Release/frozen builds reject unsigned manifests unconditionally.
- Source development may allow unsigned manifests only when `JARVIS_DEV=1`; this is logged and never allowed in a frozen build.
- Release binaries are Authenticode-signed separately. Manifest verification protects tool metadata and external tool packages; it is not a substitute for a compromised application binary.

### 9.3 Tool registry

- Replace `TOOLS` and `TOOL_FUNCTIONS` in `core/brain.py` with a `ToolRegistry`.
- The registry filters by:
  - valid manifest,
  - feature flag,
  - policy enablement,
  - optional dependency availability.
- `brain.py` receives only the schemas of enabled tools. The model never sees disabled tools.
- Handlers are resolved from the verified manifest, not from a global dictionary.
- Direct imports of phone, cast, and network modules move behind feature flags or lazy handler imports.
- Remove duplicate `where_is_device` schema. Add the missing `web_search` schema.

### 9.4 Capability model

Capabilities are strings with optional scopes. Scope matching is required for filesystem, network, app launch, and phone capabilities.

| Capability | Meaning |
|---|---|
| `system.read` | Read CPU, memory, battery, disk, app lists |
| `system.control` | Volume, brightness, media keys |
| `system.power` | Lock, sleep, restart, shut down |
| `process.launch` | Start an application, optionally scoped to an app ID |
| `process.terminate` | Close an application |
| `window.control` | Focus, minimize, snap, arrange windows |
| `input.keyboard` | Type into the focused window |
| `input.pointer` | Click, move, scroll |
| `clipboard.read` | Read clipboard contents |
| `clipboard.write` | Write clipboard contents |
| `filesystem.read` | Read files, scoped to allowed roots |
| `filesystem.write` | Write files, scoped to allowed roots |
| `filesystem.delete` | Delete files or folders, scoped |
| `network.read` | Search or fetch web content |
| `network.egress` | Send local data to an external destination |
| `screen.capture` | Capture the screen |
| `screen.ocr` | Extract text from the screen |
| `audio.input` | Microphone access |
| `audio.output` | Speaker access |
| `memory.read` | Read conversation/fact memory |
| `memory.write` | Write conversation/fact memory |
| `data.egress` | Send local content to the cloud model |
| `media.control` | Chromecast/TV control |
| `phone.control` | Phone device control |
| `phone.comms` | Calls, SMS, contacts |
| `security.admin` | Policy, manifests, audit, or security settings |
| `audit.read` | Read the audit log |

**Data-egress semantics**

- `network.read` covers a tool reading from a fixed external service. A search query is sent to that service by definition; fixed-endpoint search tools are pre-allowlisted.
- `network.egress` and `data.egress` cover a tool sending local content to a third party (Telegram, LAN messaging, SMS, arbitrary webhooks). These tools are disabled in the secure core.
- The cloud LLM boundary is separate: every chat turn sends the prompt and any tool results to the configured model provider. That is disclosed and cannot be eliminated without changing the product. Tools that return especially sensitive local content (screen OCR, clipboard, memory) are marked sensitive and require approval before their output is sent.
- `describe_screen` and `ocr_screenshot` are privacy-sensitive because their output goes to the cloud model; they are not treated as harmless read-only tools.

**Risk tiers**

- `safe`: read-only local data, no external side effect.
- `guarded`: reversible local action or read-only network use.
- `sensitive`: destructive, persistent, privacy-impacting, or outbound action.
- `critical`: security administration, phone communications, or actions that can cause irreversible remote effects.

The manifest risk must be at least as high as the highest risk implied by its capabilities. Policy may raise a tool’s effective risk, never lower it.

**Policy**

- Built-in default policy lives in code and is versioned.
- User overrides live in `%LOCALAPPDATA%\JARVIS\security_policy.json`.
- Unknown capabilities, tools, or malformed policy entries default to deny.
- A policy error denies, never allows.
- Defaults for the personal profile:
  - `safe` capabilities: allow.
  - `guarded`: allow only for explicitly trusted scopes or fixed-endpoint tools reviewed in the manifest; otherwise ask.
  - `sensitive`: ask in the UI.
  - `critical`: deny except for explicit, audited admin actions.
- Fixed-endpoint tools such as `web_search` and `morning_briefing` may be pre-allowlisted by tool, not by capability, because the user cannot point them at an arbitrary destination. Their endpoints and allowlists are reviewed and signed.
- “Always allow” is available only for guarded actions with a scope (for example, a specific app, folder, or domain). It is not offered for destructive capabilities in this release.
- Persistent grants have an expiry (default 30 days), a source, a creation time, and a revocation path.
- The settings UI lists active grants and allows revocation.

**Policy integrity**

- The policy file is trusted local configuration, not an OS security boundary. A same-user native process can edit it; that process is outside the threat model.
- The app writes the file atomically and wraps it in an HMAC envelope using the audit key so accidental corruption or partial writes are detected. An invalid envelope causes deny-all and a visible recovery prompt.
- Policy changes made through the app go through the authenticated UI or CLI and are audited as `policy.change`.
- The manifest’s `filesystem_roots`, `egress_allowlist`, and app allowlist are hard maxima. The policy can narrow them but cannot widen them.

**Scope matching**

- **Filesystem:** resolve to an absolute path, normalize case for Windows, reject UNC/device paths, and verify `os.path.commonpath([root, target]) == root` on the same drive. Resolve existing symlinks/reparse points before the check. A manifest root is a hard maximum; a grant may only select a subdirectory. Tests include `..`, drive-relative paths, UNC paths, 8.3 short names, junctions, and case variants.
- **Domains:** lowercase, IDNA-encode, strip a trailing dot, and match either the exact host or an explicitly declared `*.example.com` wildcard. Wildcards do not match the bare domain or multiple labels. Only ports 80/443 are allowed unless the manifest declares another port. Future URL-fetch tools must reject loopback, link-local, private, and resolved-private addresses to prevent SSRF.
- **Apps:** resolve a fuzzy display name to a canonical executable path or AppUserModelID before checking the allowlist. A display-name match alone is never sufficient.
- **Phone:** E.164 phone numbers and package-name regexes. Disabled in the secure core; these rules apply only when phone support is re-enabled.

### 9.5 Approval manager

Every approval request contains:

- `request_id` (UUID4)
- `session_id`
- `source` (`ui`, `voice`)
- `tool`
- `capabilities`
- `risk`
- human-readable description
- redacted argument summary
- `created_at`
- `expires_at` (60 seconds)

**Rules**

- The request is sent to the authenticated UI session and written to the audit log before the assistant waits.
- The UI shows tool, risk, capabilities, and a plain-language description.
- The UI response is `{"cmd": "approval_response", "request_id": "...", "decision": "allow" | "deny"}`.
- The server checks that the request exists, belongs to the same session, is not expired, and has not been resolved.
- Approval is single-use. Replay, unknown IDs, and cross-session IDs are rejected and audited.
- Timeout, disconnect, malformed response, or ambiguous voice input results in deny.
- Sensitive and critical actions require a UI decision. Voice cannot approve them.
- Guarded actions may be approved by voice only when a voice profile is enrolled and the approval utterance matches that profile. If voice lock is not enrolled, guarded actions require UI approval.
- Guarded actions may be approved by voice using exact phrases:
  - `"approve once"`
  - `"yes, approve"`
  - `"cancel"` / `"deny"`
- Voice parsing checks denial first and uses anchored word-boundary matching. If both approval and denial markers appear, deny.
- Bare “ok”, “sure”, “go”, “yes”, and “do it” are not sufficient.
- No global `approval_future`; all approvals are request-scoped.
- If no authenticated UI session exists, the broker shows the app window and waits up to 10 seconds for a session. If none appears, the request is denied.
- PIN can be added later as a second factor for critical UI actions. It is not a voice fallback in this release.

### 9.6 Audit log

Use SQLite at `%LOCALAPPDATA%\JARVIS\audit.db`.

**Events**

- `auth.success`
- `auth.failure`
- `session.open`
- `session.close`
- `manifest.load`
- `manifest.reject`
- `policy.decision`
- `policy.change`
- `approval.requested`
- `approval.resolved`
- `approval.expired`
- `tool.request`
- `tool.result`
- `tool.error`
- `tool.timeout`
- `feature.toggle`
- `secret.migrated`
- `audit.export`

**Record shape**

- `id`
- `ts`
- `event_type`
- `session_id`
- `source`
- `tool`
- `capabilities` (JSON)
- `risk`
- `request_id`
- `args_hash`
- `args_redacted`
- `decision`
- `approver`
- `result_status`
- `error_code`
- `prev_hash`
- `entry_hash`
- `hmac`

Use a hash chain: `entry_hash = SHA-256(prev_hash || canonical_event_json)`. Use an HMAC key stored in the DPAPI vault to make offline tampering detectable. The audit writer serializes writes with a lock and transaction.

**Privacy**

- Do not store raw API keys, PINs, tokens, or typed secrets.
- Store a hash of the canonical arguments and a redacted, human-readable summary.
- If verbose auditing is enabled for local debugging, it is opt-in, audited, and documented as sensitive.

**Failure behavior**

- If the audit write fails, deny the tool invocation. The assistant may still chat, but it cannot perform actions it cannot audit.
- On startup, mark any `tool.request` without a result as `unknown` and audit the recovery.
- Provide `scripts/audit_verify.py` and an export endpoint/CLI.
- Default retention: 90 days, configurable.

**Key-loss recovery**

- If the DPAPI vault or audit HMAC key cannot be read, enter **audit-locked mode**: chat is available, but every tool invocation is denied.
- The UI explains the problem and offers one explicit action: archive the old database and start a new audit chain with a new key.
- Never silently delete the old database. The reset is recorded in the new chain and in a local recovery marker. The old database remains available for export.
- This prevents a routine Windows profile reset from either bricking the assistant forever or silently destroying the old evidence.

**Limitation:** a local administrator can delete the database. The hash chain and HMAC detect modification, not deletion. The threat model excludes that attacker.

### 9.7 Execution lanes and sandboxing

**Broker**

All tool calls go through one broker that performs, in order:

1. Registry lookup.
2. Manifest/feature check.
3. JSON Schema argument validation.
4. Capability and scope validation.
5. Policy decision.
6. Approval, if required.
7. Audit write.
8. Dispatch to the correct lane.
9. Timeout and cancellation.
10. Result redaction and audit result.

**Lane selection**

| Lane | Process model | State | Examples |
|---|---|---|---|
| `core` | Broker process | Stateful services and simple OS calls | Reminders, memory, `launch_app`, `open_url`, power actions, volume, morning briefing with a reminder snapshot |
| `restricted` | Isolated worker | No persistent state | `system_info`, `top_processes`, `search_files` |
| `privileged` | Isolated desktop host | No app state | Screen capture/OCR, clipboard, input, window focus |
| `egress` | Isolated worker | No persistent state | `web_search` |
| `disabled` | Not loaded | None | Phone, cast, LAN messaging, Telegram, updater |

Every tool entry selects exactly one lane. Network capability is enforced by the policy regardless of lane; the `egress` lane is used for network-only tools that do not need desktop or stateful access.

**Core lane**

For narrow, signed, stateful services and simple OS calls:

- Runs in the broker process so reminders and memory have one owner.
- No shell invocation. Use argument lists and validated structured values.
- Per-call timeout and audit.
- Not sandboxed; capabilities are deliberately narrow.
- Stateful services persist to SQLite (reminders, memory) instead of process memory alone.
- `morning_briefing` receives a read-only reminder snapshot from the scheduler and performs its fixed-endpoint network read through the egress guard.

**Restricted lane**

For non-interactive tools that do not need the desktop or audio device:

- Separate process using `multiprocessing` spawn.
- Windows Job Object with:
  - kill-on-job-close,
  - process memory limit (256 MB default),
  - active process limit of 1,
  - die-on-unhandled-exception.
- Sanitized environment with no API keys, tokens, PINs, or license secrets.
- Temporary working directory under the app data sandbox folder.
- Per-call timeout; the broker kills and restarts the worker after a timeout or crash.
- No child-process creation. No shell invocation.
- Tools in this lane must not require interactive desktop access.

This lane is a crash/resource sandbox, not a security boundary against malicious signed code. The signed code is trusted; the isolation prevents a bug or hang from taking down the assistant.

**Privileged lane**

For interactive tools that need the user’s desktop, audio device, clipboard, or input injection:

- Separate child process, but with the user’s normal desktop access.
- Parent and child communicate over an anonymous `multiprocessing.Pipe` with a random handshake nonce. There is no localhost listener, static token, or default-DACL named pipe for this host.
- The host is long-lived for the session and restarted after a crash. Cancellation sends a control message; if the host does not stop within the grace period, the broker terminates the process.
- No shell interpolation. Arguments are passed as lists or validated structured values.
- Capability-gated and audited like every other lane.
- Sensitive actions require UI approval.
- This lane is explicitly **not** called sandboxed in user-facing text.

**Egress lane**

For network-only tools:

- The manifest declares `network.read` and/or `network.egress` plus an allowlist.
- URL-accepting tools validate the destination against the allowlist.
- Tools with fixed endpoints are reviewed and signed; the broker does not permit arbitrary URL fetches.
- The broker cannot track whether an argument originated in untrusted web/OCR content. Taint tracking is not part of this release. Prompt injection is contained by disabling egress tools by default, keeping `open_url` allowlisted, and requiring UI approval for typing and clipboard writes. The design does not claim prompt injection is eliminated.
- OS-level network isolation is future work; this release enforces egress at the capability and tool boundary.

**Filesystem rules**

- Resolve every path with `Path.resolve()` and check it is inside an allowed root.
- Reject UNC paths, drive-relative paths, symlink/reparse-point escapes, and `..` traversal.
- Scope deletes to explicitly granted roots.
- Never accept a path directly from untrusted model output without validation.

**Timeouts and cancellation**

- Every tool has a per-manifest timeout.
- A timeout kills the worker, records `tool.timeout`, and returns a safe error.
- Long-running interactive actions are cancellable; cancellation is audited.

### 9.8 Secure core feature flags

Defaults:

| Flag | Default | Behavior |
|---|---:|---|
| `FEATURE_WEB_SEARCH` | 1 | Web search tool and schema enabled |
| `FEATURE_VOICE_ENROLLMENT` | 1 | Local voice enrollment enabled |
| `FEATURE_GAMING` | 1 | Gaming-related app launch; updater integration later |
| `FEATURE_PHONE` | 0 | No ADB imports, no phone tools, no phone connection |
| `FEATURE_CAST` | 0 | No pychromecast import, no cast tools |
| `FEATURE_LAN_MESSAGING` | 0 | No receiver on port 9998, no LAN tools |
| `FEATURE_TELEGRAM` | 0 | No Telegram bot or remote control |
| `FEATURE_LICENSE_SERVER` | 0 | License server is not started or shipped in the personal build |
| `FEATURE_AUTO_UPDATE` | 0 | No automatic download or install; manual updates only |

Rules:

- Disabled features must not import optional modules at startup.
- Disabled features must not open ports or start background listeners.
- Disabled feature code remains in the repository behind lazy imports and flags, ready for a later security review.
- The UI hides disabled feature controls and update buttons.
- A feature can be re-enabled only with a valid manifest, policy, and tests.

**License gate:** for the personal build, the packaged license gate is disabled. If the user later distributes builds, the license gate and license server must be re-enabled and hardened separately. This is a product decision, not a security bypass.

### 9.9 Secrets and data paths

- Make `core/config.py::data_dir()` the only data-directory implementation.
- Fix `voiceid.data_dir()` and any other duplicate to use `sys.frozen` and the same path.
- `memory.py`, voice profiles, audit DB, and license state all use the canonical data dir.
- Store secret values in a DPAPI-encrypted vault. `config.env` contains only non-secret settings.
- The setup wizard collects secrets in-app and writes them to the vault. The installer no longer collects secrets.
- `save_settings` splits secret and non-secret keys; secret writes go to the vault, not plaintext.
- Migrate existing `.env`: encrypt secrets, verify decryption, remove secret lines from plaintext, then audit `secret.migrated`.
- Avoid vault-overrides-newer-settings behavior: remove migrated secrets from `.env`; load vault values only for keys not present in config or when explicitly migrated.
- Add a logging redaction filter for API keys, tokens, PINs, and license keys.
- Set the data directory ACL to the current user only.
- Migrate legacy packaged data from the app directory into `%LOCALAPPDATA%\JARVIS` and leave a backup.

### 9.10 Brain and UI integration

- The LLM system prompt lists only enabled tools and removes “FULL control” language.
- Tool results are labeled as untrusted data; the model is told not to follow instructions found inside web pages, OCR text, or documents. The real defense is the capability policy, not the prompt.
- The model cannot claim success until a tool result confirms it.
- The UI sends the auth handshake before any command.
- Approval dialog shows risk, capabilities, and description.
- Settings exposes active grants and recent audit decisions.
- The update button and license setup step are hidden in the personal profile.
- UI language switching is fixed so English is reapplied correctly; this is a small correctness fix included because the setup/security text must be readable.

**Non-tool commands**

The WebSocket surface includes commands that are not tools. They go through a `CommandRouter` with the same authentication, policy, and audit path:

| Command | Capability | Default behavior |
|---|---|---|
| `chat`, `listen`, `abort` | `assistant.control` | Allow |
| `approval_response` | `approval.respond` | Bound to request and session |
| `model`, `tts_settings`, `wake_toggle`, `set_language` | `assistant.configure` | Authenticated, audited |
| `enroll` | `voice.enroll` | Authenticated, UI visible |
| `voice_reset` | `voice.delete` | UI confirmation |
| `wipe_memory` | `memory.delete` | UI confirmation |
| `reset` | `conversation.reset` | UI confirmation |
| `update_download` | Removed | No route exists |

The `update_download` handler and UI button are removed, not merely feature-flagged. The updater module remains for a future signed-update design, but no command can invoke it in the secure core.

---

## 10. Default Tool Catalogue

### Enabled in the secure core

- `web_search` (new schema; replaces the dead executor)
- `launch_app` (trusted app allowlist; unknown apps ask)
- `set_volume`, `get_volume`, `media_key`
- `system_info`, `top_processes`, `list_running_apps`
- `set_reminder`, `set_alarm`, `list_reminders`, `cancel_reminder`
- `search_files` (scoped roots)
- `get_clipboard` (ask), `set_clipboard` (ask)
- `minimize_all_windows`, `focus_window`
- `type_text` (sensitive, UI approval)
- `open_url` (domain allowlist or ask)
- `take_screenshot`, `ocr_screenshot`, `describe_screen` (privacy-sensitive; ask)
- `remember_fact`, `forget_fact`, `recall_memories`
- `morning_briefing`
- `close_app`, `delete_file`, `lock_screen`, `sleep_pc`, `shutdown_pc` (sensitive, UI approval)

### Disabled by default

- All `phone_*` tools
- All `cast_*` tools
- `where_is_device`, `list_network_devices`
- `net_send_message`, `net_broadcast`, `net_read_messages`
- `telegram_notify`
- Network messaging/cast discovery
- Auto-update download/install
- License administration

This reduces the model-visible tool count from roughly 70 to approximately 25.

---

## 11. Security Invariants

These must always hold:

1. No WebSocket command executes before successful token authentication.
2. No HTTP state-changing endpoint executes without the token header.
3. Every non-tool command goes through the CommandRouter’s authentication, policy, and audit path.
4. No tool runs without a verified manifest and a policy allow/ask decision.
5. No approval resolves a request other than the one identified by `request_id` in the same session.
6. No sensitive/critical action is approved by voice.
7. No tool runs if the audit write fails.
8. No path escapes an allowed root, and manifest scopes remain hard maxima.
9. No disabled feature opens a port or imports its optional dependency.
10. No secret is written to logs or audit arguments.
11. No automatic update path exists in the secure core.
12. No sandboxed label is applied to a privileged interactive tool.
13. No native same-user process is claimed to be prevented from controlling the app; that boundary is documented.

---

## 12. Failure Behavior

- Missing/invalid token: close connection, audit, no commands.
- Invalid manifest: disable the tool; continue with safe tools; show UI warning.
- Policy error or invalid HMAC envelope: deny, use built-in deny-by-default policy, and show a recovery prompt.
- Audit error or audit-locked mode: deny tool execution; chat remains available.
- Approval timeout, disconnect, ambiguity, or malformed response: deny.
- No authenticated UI session for a sensitive approval: show the window, wait 10 seconds, then deny.
- Sandbox timeout or crash: kill, restart, return error, audit.
- Optional dependency missing: disable that feature with a warning; do not crash startup.
- Network unavailable: return a tool error; do not fall back to an insecure path.
- Audit HMAC key lost: audit-locked mode with explicit archive-and-reset recovery.
- Corrupt policy file: use built-in deny-by-default policy and warn.

---

## 13. Migration

1. Generate the tool manifest from the current enabled tools and sign it.
2. Add the token handshake to the server and UI in the same change; old unauthenticated clients stop working by design.
3. Replace direct brain dispatch with the registry and broker.
4. Move approvals to request IDs and update the UI dialog.
5. Disable feature flags and remove optional imports from startup.
6. Migrate secrets and data paths; verify with tests.
7. Add security regression tests and clean-install CI.
8. Bump the version and document the new security model and disabled features.

Existing users must re-approve any persistent grants. Pending approvals are cancelled on upgrade.

---

## 14. Testing and Verification

### Unit tests

- WebSocket rejects missing, malformed, and incorrect tokens.
- WebSocket rejects cross-origin clients; accepts the bundled origin with a valid token.
- A second control session replaces the old one only after the grace period; stale sessions cannot approve.
- HTTP state-changing endpoints reject requests without the token header.
- Approval parser denies “I don’t approve”, “no, don’t do it”, “not ok”, “don’t confirm”.
- Approval parser accepts only exact guarded phrases.
- Sensitive/critical voice approval is refused.
- Voice approval without an enrolled voice profile is refused and falls back to UI.
- Approval replay, cross-session, expired, and unknown request IDs are denied.
- No-UI-session approval opens the window and denies after the timeout.
- Policy defaults deny unknown capabilities/tools.
- Scoped grants allow only their scope and can be revoked.
- Manifest scope is a hard maximum; a wider policy grant is rejected.
- Path matching rejects `..`, UNC/device paths, drive-relative paths, short-name aliases, junction escapes, and case variants.
- Domain matching rejects lookalike suffixes, wildcard TLDs, trailing-dot mismatches, and non-declared ports.
- Manifest signature, code hash, compiled public key, and unsigned-release rejection.
- Policy HMAC envelope detects corruption and denies all on failure.
- Audit hash chain verification and tamper detection.
- Audit key loss enters audit-locked mode and requires explicit reset.
- ADB argument injection is blocked (with the disabled phone feature exercised directly).
- Sandbox timeout, memory limit, sanitized environment, and worker restart.
- Worker IPC rejects a wrong handshake nonce; no localhost listener is opened.
- Non-tool commands (`wipe_memory`, `voice_reset`, `update_download`) follow the command policy and audit path.
- Data-dir consistency in frozen and source modes.
- Secret migration removes plaintext after successful encryption.

### Integration tests

- Start the app in test mode, connect with the token, send a chat, approve a guarded action, verify execution and audit.
- Start a malicious page on another local port, attempt a WebSocket connection, verify rejection.
- Verify disabled features do not import `pychromecast`, do not open port 9998, and do not create phone connections.
- Verify clean install with core requirements only.

### CI

- Windows runner.
- Install core requirements and `pytest`.
- Run the full suite.
- Run `scripts/verify_tool_manifest.py`.
- Fail on unsigned manifests in release mode.
- Keep optional phone/cast/OCR tests in a separate job.

### Manual checks

- Browser DevTools: inspect WS handshake, token absence from logs, approval request binding.
- Audit export and verification.
- Sandbox kill/restart behavior.
- UI approval dialog readability and keyboard accessibility.

---

## 15. Rollout Plan

Each phase leaves the app runnable and the security tests green.

1. **Boundary first:** auth token, Origin checks, HTTP token, disable risky routes, audit skeleton.
2. **Registry and manifests:** signed manifest, code hashes, registry, brain integration, tool schema reduction.
3. **Policy and approvals:** capability policy, scoped grants, request-bound approvals, voice parser, UI dialog.
4. **Isolation:** restricted worker, privileged host, timeouts, ADB safe runner (phone still disabled).
5. **Secrets and data:** vault migration, data-dir unification, installer changes, logging redaction.
6. **Verification and docs:** clean-install CI, audit tooling, migration notes, security documentation.

No gaming update feature work until phase 6 is complete.

---

## 16. Risks and Tradeoffs

- **Local process boundary.** A native process running as the same Windows user is out of scope. It can read the token, policy, and DPAPI-protected data. The design protects against web content, network clients, and prompt injection, not a compromised user session.
- **Sandbox limits.** The restricted lane protects against crashes and resource abuse, not malicious signed code. True OS containment is a later project.
- **Frozen-build integrity.** In a PyInstaller build, code integrity depends on Authenticode; manifest hashes cover source/dev and external tool packages. A compromised signed binary is not detected by the manifest.
- **Policy trust.** The policy file is trusted user configuration. The HMAC detects corruption, not a same-user attacker.
- **No taint tracking.** The broker cannot prove that a model argument came from untrusted web/OCR content. Egress tools are disabled in the secure core, and dangerous local actions require approval; prompt injection is contained, not eliminated.
- **Approval friction.** Users may find UI-only approval for sensitive actions slow. The safe default is intentional; we can tune scoped grants after real use.
- **Signing key management.** Losing the private key means new tool manifests cannot be signed. The key must be backed up offline.
- **Disabled features.** Phone, TV, LAN, and updates disappear temporarily. This is the price of a safe default.
- **Manifest maintenance.** Every tool change requires re-signing. Provide scripts and CI checks so this is routine.
- **Audit privacy.** Hashing arguments reduces leak risk but also limits forensic detail. Verbose auditing is opt-in.
- **Windows complexity.** Job objects, multiprocessing spawn, and PyInstaller require careful testing; `freeze_support()` must be called in the entrypoint.

---

## 17. Decisions Made for This Design

- Use one signed manifest bundle, not per-tool signature files, for the first release.
- Use Ed25519 and the existing `cryptography` dependency.
- Compile the manifest public key into the signed application; do not load it from a writable data file.
- Require a per-launch in-memory token; do not persist it.
- Keep Origin validation as defense-in-depth, not as the primary auth mechanism.
- Treat a native same-user process as outside the threat model; the token protects web content and network clients, not a local native process.
- Use request-bound approvals with UUIDs and session ownership.
- Allow only one authenticated control session at a time.
- Allow voice approval only for guarded actions, only with an enrolled voice match; sensitive and critical require UI.
- Require destructive actions to use UI confirmation only in the first release; no spoken PIN.
- Use process isolation and Windows Job Objects for the restricted lane; do not claim VM-grade sandboxing.
- Run interactive desktop/audio/input tools in a separate privileged host over an anonymous pipe, not a localhost endpoint.
- Route non-tool WebSocket commands through the CommandRouter; remove `update_download` entirely.
- Disable phone, cast, LAN messaging, Telegram, license server, and auto-update by default.
- Keep disabled code in the repository behind flags rather than deleting it.
- Use a 90-day audit retention default.
- Use `jsonschema` for tool argument validation (small, well-maintained dependency) or an internal validator with equivalent coverage if dependency review rejects it.
- Treat manifest scopes as hard maxima; policy can narrow but never widen them.
- Enter audit-locked mode if the audit key is unavailable; require an explicit archive-and-reset.
- Keep the personal build’s license gate off; distribution builds must re-enable and harden it.

---

## 18. What Is Left to the Builder

- Choose the manifest build/signing workflow details (developer machine vs CI).
- Define the exact initial trusted app/domain/path allowlists.
- Decide the worker restart/backoff policy.
- Use one persistent restricted worker per lane for the session; restart after a crash or timeout.
- Tune default approval thresholds after a short usability trial.
- Plan the gaming launcher integration separately, after this security foundation is complete.

---

## 19. Approval Needed

This design is ready for approval. It takes the secure-core route and includes the requested capability-based permissions, signed tool manifests, isolated execution, and audited approvals.

The first release requires UI confirmation for destructive actions. There is no spoken PIN fallback. If that and the documented local-process boundary are acceptable, the next step is the implementation plan.
