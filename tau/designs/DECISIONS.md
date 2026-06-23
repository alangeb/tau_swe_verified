# Design Decisions

## Architecture (7)

| # | Decision | Rationale |
|---|----------|-----------|
| 1.1 | **Central `TauBot` orchestrator** — one class owns context, LLM, tools, loop detection, subagents | Single ownership, clear responsibility |
| 1.2 | **Message-driven architecture** — user input → context → LLM → tools → repeat | Simple, composable flow |
| 1.3 | **Single entry point** (`tau.py`) with identical `tau-dut.py` for testing | Clean separation of production and test variants |
| 1.4 | **System prompt from `AGENT.md`** — externalized, not hardcoded | Configurable behavior without code changes |
| 1.5 | **`AgentInitConfig`** — fully-resolved initialization parameters in `agent_init.py` via `resolve_agent_init()`; separates config resolution from agent construction | Clean init pipeline |
| 1.6 | **`CommandManager`** — unified command resolution and dispatch in `agent_commands.py`; resolves `.py` → builtin → `.md` priority with `CommandSource`/`CommandInfo` | Centralized command routing |
| 1.7 | **`InputHandler`** — manages stdin thread, signal handling, and input dispatch in `agent_input.py`; separates input loop from agent core | Decoupled input processing |

## Compression (12)

| # | Decision | Rationale |
|---|----------|-----------|
| 2.1 | **Eight sequential algorithms**: oversized_tool_redaction → drop_reasoning → last_transaction → tool_pruning → redact_blocks → tool_pruning_full → redact_blocks_full → full_reset | Ordered by impact: structural redaction first, then LLM summary of recent turns, then boundary-limited pruning/redaction, then full-context pruning/redaction, full reset last |
| 2.2 | **Fixed 50% byte boundary** — computed ONCE from original context, never moves during compression | Predictable, preserves recent context |
| 2.3 | **Right-to-left scanning** — compresses oldest blocks first to preserve KV cache prefix | KV cache efficiency |
| 2.4 | **Parameter consistency across LLM calls** — same model/tools/tool_choice/params; only `messages` varies → enables full KV cache reuse | Maximize cache hits |
| 2.5 | **Compression prompt is a constant string** — never changes between calls → prefix stability | KV cache prefix stability |
| 2.6 | **Tool pruning threshold: 100 bytes** — smaller outputs not worth pruning overhead | Cost-benefit tradeoff |
| 2.7 | **Minimum block size: 300 bytes** — avoid LLM call overhead on tiny blocks | Efficiency threshold |
| 2.8 | **Retry on short LLM responses (<10 bytes)** — compression summaries must be substantive | Quality gate |
| 2.9 | **Preserve `tool_call_id` and `name` when pruning** — maintains OpenAI spec compliance | API compatibility |
| 2.10 | **`compress_oversized_tool_redaction` as first algorithm** — strips oversized tool outputs (>20% of context) before other compression | Targets disproportionately large outputs first |
| 2.11 | **Two-tier boundary strategy** — steps 4–5 respect 50% boundary; steps 6–7 scan entire context | Graduated escalation: protect recent context first, then compress everything if needed |
| 2.12 | **`compress_drop_reasoning` as second algorithm** — strips reasoning fields before LLM summarization | Cheap, high-yield reduction before expensive LLM calls |

## LLM Layer (18)

| # | Decision | Rationale |
|---|----------|-----------|
| 3.1 | **`SimpleOpenAIClient` — stdlib-only HTTP client** — no external dependencies for LLM communication | Zero-dependency core |
| 3.2 | **Drop-in OpenAI client interface** — wraps raw API to match OpenAI SDK semantics | Familiar interface, easy migration |
| 3.3 | **Unified retry logic** — `_invoke_llm_with_retry` handles all retries, backoff, validation | Centralized error handling |
| 3.4 | **Post-parse recovery** — extract tool calls from text content when LLM misses structured format | Robustness against LLM quirks |
| 3.5 | **Validate before sending to API** — check tool call JSON, empty replies, length limits | Fail fast, save API calls |
| 3.6 | **`InvalidReplyError` for retryable violations** — fast-fail on first error | Clear error signaling |
| 3.7 | **XML-style and pipe-style tag constants** — centralized in `agent_llm.py` | Single source of truth |
| 3.8 | **`CacheTracker` with sliding window** — tracks prompt cache hit rates across session | Observability |
| 3.9 | **End-of-turn validation** — check for unclosed thinking tags, malformed tool-call syntax | Quality gate |
| 3.10 | **Pre-API field stripping** — remove non-LLM-relevant fields to avoid 400 errors | Defensive coding |
| 3.11 | **Defensive parsing** — `_safe_get`, deep copies, external tracking sets | Robust against malformed responses |
| 3.12 | **Graduated retry strategy** — thinking disabled after 5 failures | Adaptive behavior |
| 3.13 | **Bounded logging** — prevent console flooding | UX protection |
| 3.14 | **Cross-backend support** — handles both vLLM and llama.cpp formats | Backend agnostic |
| 3.15 | **Conservative post-parse** — only extracts clearly valid tool calls | Safety over flexibility |
| 3.16 | **`PrefixCacheTracker` in `agent_llm.py`** — tracks expected vs actual prefix cache hits, reports divergence with param change detection | Cache observability |
| 3.17 | **`LLMCallConfig` dataclass** — unified configuration for LLM invocations (model, messages, tools, tool_choice, stream, extra_kwargs) | Centralized call config |
| 3.18 | **In-place compression replaces truncation** — overflow recovery uses LLM-based compression on real context, eliminating redundant copy compression | Overflow tracking |

## Tooling (19)

| # | Decision | Rationale |
|---|----------|-----------|
| 4.1 | **Dynamic tool discovery** — scan `tools/` directory for modules with `name` + `run` attributes | No manual registration |
| 4.2 | **`ToolEntry` dataclass** — single source of truth for tool metadata | Clear structure |
| 4.3 | **`Args` dataclass per tool** — schema generated automatically via `_dataclass_to_json_schema` | Auto-schema, no manual maintenance |
| 4.4 | **`CMD_ALIASES` and `ARG_ALIASES`** — tool/argument aliasing for flexibility | LLM-friendly naming |
| 4.5 | **Signal-based tool execution** — `signal.setitimer()` with `SIGALRM` handler raises `ToolTimeout`; no daemon threads, no orphaned processes | Clean interruption, zero concurrency |
| 4.6 | **Tool validation: aliases first, then int coercion** — `normalize_tool_call` normalizes before execution; `fix_tool_call` is deprecated | Flexible input handling |
| 4.7 | **Oversized output to `LOG_DIR`** — `write_oversized_output()` in `agent_session.py` stores full output on disk, context stays small | Disk backup, token economy |
| 4.8 | **Tool errors via `AuditWriter`** — structured audit records in `LOG_DIR`, accumulates per session | Machine-readable error history |
| 4.9 | **`ToolFilter`: allowlist > blocklist, `fnmatch` wildcards** — deny-by-instructive-message | Flexible restriction |
| 4.10 | **Priority timeout resolution**: args > module `timeout` attr > `default_timeout` (180s) > `long_running_timeout` (86400s for fork/subagent) | Fine-grained control |
| 4.11 | **Process group isolation** — `start_new_session=True` on all `subprocess.run()` calls in tool modules; each tool manages its own child process cleanup; no centralized ProcessTracker | Zero orphaned processes |
| 4.12 | **Sequential batch execution** — maintains OpenAI message alternation | API compliance |
| 4.13 | **No exceptions escaped** — all errors returned as strings | Predictable error handling |
| 4.14 | **`difflib` suggestions** for unknown tools (cutoff=0.6) | Helpful error messages |
| 4.15 | **Comprehensive exception catching** — 7 exception types | Robust tool execution |
| 4.16 | **Dangerous command detection** — `bash` tool blocks destructive patterns (`rm -rf`, `sudo`, `git --force`) via `DANGEROUS_PATTERNS`; rejected on first attempt, allowed on double-call confirmation | Safety gate |
| 4.17 | **Sandbox validation** — `tools/lib/sandbox.py` enforces working directory boundaries via `check_path`/`validate_path`; paths outside cwd require double-call confirmation | Escape prevention |
| 4.18 | **`ToolModule` protocol** — formal protocol in `tools/__init__.py` requiring `metadata` (ToolMetadata), `Args` (dataclass), `run` (callable); validated via `_validate_tool_module()` | Structured tool registration |
| 4.19 | **Tool validation module** — `tools/validation.py` provides `normalize_tool_call`, `validate_tool_name`, `_get_tool_schema_info`, `_validate_tool_args`, `_generate_validation_error`; `fix_tool_call` is deprecated (delegates to `normalize_tool_call`) | Robust tool call normalization |

## Console & Communications (7)

| # | Decision | Rationale |
|---|----------|-----------|
| 5.1 | **Console output via standalone functions** — `agent_console_primitives.py` provides `_cw()`, `_role_color()`, `Colors`, `display_error()`, `display_warning()`, `display_success()`, `display_info()`; `agent_console.py` is a re-export facade; `agent_console_messages.py` holds declarative message templates; `agent_console_display.py` holds complex display functions; no singleton class | Consistent formatting, no global state |
| 5.2 | **Semantic color coding** — RED=error, YELLOW=warning, CYAN=status, GREEN=success, TEAL=reasoning | Visual clarity |
| 5.3 | **A2A via Unix domain sockets** — inter-agent communication protocol with JSON messages | Process-local, secure |
| 5.4 | **`InputMessage` factory pattern** — `from_a2a()`, `from_interactive()`, `from_command_line()` → unified input type | Source-agnostic processing |
| 5.5 | **Auto-timestamping via `__post_init__`** — all messages get timestamps automatically | Traceability |
| 5.6 | **Tool output truncation: 500 chars or 20 lines** (whichever hits first) — prevents console flooding | UX protection |
| 5.7 | **Dynamic `sys.stdout`** — supports output redirection | Testability |

## Subagent & Fork (11)

| # | Decision | Rationale |
|---|----------|-----------|
| 6.1 | **Subagent = blank slate** — fresh context, no parent history | Maximum isolation |
| 6.2 | **Fork = deep copy** — inherits full parent context + conversation | Context continuity |
| 6.3 | **Nesting depth threshold** — configurable limit on subagent/fork depth | Infinite recursion prevention |
| 6.4 | **Nesting restriction text injected into system prompt** — tells subagents their depth limit | Self-aware agents |
| 6.5 | **`_create_subagent` inherits parent config** — same LLM, same settings | Consistent behavior |
| 6.6 | **Unrestricted child tools by default** — children get full tool access unless filtered | Flexibility |
| 6.7 | **Fresh fork metadata** — not inherited from parent | Clean state |
| 6.8 | **`/fork {prompt} user message`** — signals fork context | Clear context markers |
| 6.9 | **Local imports** — avoids circular dependencies | Module independence |
| 6.10 | **Fork isolation via `_create_fork_isolation`** — each fork gets unique `fork_id` and isolated temp directory; cleaned up after completion | Resource isolation |
| 6.11 | **Forks are synchronous/blocking** — parent blocks until fork returns; no concurrent fork execution | Simplicity, predictable behavior |

## Delegate Mode (7)

| # | Decision | Rationale |
|---|----------|-----------|
| 7.1 | **Delegate mode via `/delegate` command** (`commands/delegate.py`) — LLM instructions enforce read-only behavior; no tool filter changes (preserves prefix caching); orchestrator plans and delegates via fork/subagent | Efficiency + cache safety |
| 7.2 | **Delegate uses LLM instructions only** — `DELEGATE_INSTRUCTIONS` in `commands/delegate.py` tells the orchestrator to plan and delegate; tool restrictions are enforced via prompt, not ToolFilter; avoids prefix cache breaks from changing `tool_filter` | LLM-enforced delegation |
| 7.3 | **No tool filter changes in delegate mode** — `tool_filter` is NOT modified; prefix caching stays intact; LLM instructions enforce read-only behavior | Prefix cache preservation |
| 7.4 | **`DELEGATE_INSTRUCTIONS` injected into context** — self-correcting behavior via prompt instructions | Enforced pattern |
| 7.5 | **`end_turn` as explicit loop terminator** — no hard iteration limit | Flexible orchestration |
| 7.6 | **`force_end_turn` as loop exit signal** — `agent.force_end_turn` set by `end_turn` tool to break the delegate loop | Explicit termination |
| 7.7 | **Delegate loop: `invoke_with_tools` → check `force_end_turn`** — simple polling loop with no side effects | Clean state management |

## Input & Interaction (5)

| # | Decision | Rationale |
|---|----------|-----------|
| 8.1 | **Multiline input with `#` prefix** — two blank lines to end | Natural editing |
| 8.2 | **Two-level Ctrl+C** — graceful shutdown → force exit | User control |
| 8.3 | **Thread-safe everywhere** — `OutputCapture` with `threading.Lock`, `queue.Queue` for input | Concurrency safety |
| 8.4 | **System-wide flags** (`_interrupted`, `_exit_requested`) — cooperative cross-thread shutdown | Clean termination |
| 8.5 | **`InputHandler` stdin daemon thread** — reads stdin in background with `select()`; dispatches `/commands`, `!shell`, and regular input | Non-blocking input |

## Commands (13)

| # | Decision | Rationale |
|---|----------|-----------|
| 9.1 | **Three-tier dispatch: .py → builtin → .md** — Python commands override builtins, builtins override markdown | Flexible extension hierarchy |
| 9.2 | **Python commands: full agent access** — `run(agent, args)` with no return value, manages own context | Arbitrary program logic |
| 9.3 | **Markdown commands: prompt templates** — YAML frontmatter, placeholder substitution, multi-prompt chains | Easy authoring |
| 9.4 | **Dynamic placeholder substitution** — `${time}`, `${date}`, `${datetime}` resolved at load time | Flexible prompts |
| 9.5 | **Dynamic discovery, no caching** — fresh scan every call | Always up-to-date |
| 9.6 | **Conflict resolution** — .py wins over .md for same name, with console warning | Predictable precedence |
| 9.7 | **Simple YAML parsing** — only `description:` field | Minimal complexity |
| 9.8 | **Relative default directory** (`commands/`) | Portable |
| 9.9 | **Three-category help display** — /help and /commands show builtins, .py, .md separately | Clear visibility |
| 9.10 | **`ralph` command** — iterative task execution with explicit `<complete>` tag confirmation; maintains task state in JSON files under `~/.local/tau/ralph/` | Structured task workflow |
| 9.11 | **`plan` command** — hierarchical task plan management (create, add, complete, block, unblock, status, next, progress, update, delete, clear) | Task organization |
| 9.12 | **`CommandSource` enum** — tracks origin (builtin, .py, .md) for each resolved command | Debugging & precedence |
| 9.13 | **`CommandManager.dispatch` recursion guard** — `MAX_MD_COMMAND_RECURSION` prevents infinite .md command chains | Safety against recursive prompts |

## Skills (4)

| # | Decision | Rationale |
|---|----------|-----------|
| 10.1 | **Skills loaded from `SKILLS_DIR`** — markdown files with category metadata | Easy authoring |
| 10.2 | **Cached skill list** — loaded once, cached after first call | Performance |
| 10.3 | **Fuzzy/case-insensitive skill matching** — flexible lookup | User-friendly |
| 10.4 | **Skill execution via fork** — inherits full context + skill content as instructions | Context-aware execution |

## Background Processes (TMUX) (3)

| # | Decision | Rationale |
|---|----------|-----------|
| 11.1 | **Session naming convention: `tmux-agent-{uuid}`** — auto-generated UUID, prefix for filtering | Unique identification |
| 11.2 | **Session lifecycle: new → exec → capture/tail → kill** — full lifecycle management | Complete control |
| 11.3 | **Kill all via prefix filter** — `tmux-agent-*` pattern for bulk cleanup | Efficient cleanup |

## Web Interaction (7)

| # | Decision | Rationale |
|---|----------|-----------|
| 12.1 | **Crawl4AI first-attempt with native fallback** — `fetch` tries Crawl4AI `/md` endpoint first, falls back to native HTML-to-markdown conversion | Flexible extraction |
| 12.2 | **SearXNG for searching** — `web_search` uses SearXNG for privacy-friendly search | Privacy |
| 12.3 | **Cache flag** — `cache=True` default in tool schema; Crawl4AI first-attempt uses `"c": "0"` to disable its cache; native fetch fallback uses local file cache with TTL | Fresh data by default |
| 12.4 | **Subagent/fork context recommended** — web fetching should be delegated for isolation; advisory only, not enforced | Isolation guidance |
| 12.5 | **Single URL → `/md` endpoint** (markdown), Multiple URLs → `/crawl` endpoint (JSON) | Optimized endpoints |
| 12.6 | **Filter types**: raw/fit/bm25/llm — configurable extraction strategies | Flexible extraction |
| 12.7 | **Multi-engine search** — `search` tool uses SearXNG → DuckDuckGo HTML → Mojeek cascade; `lookup` uses Wikipedia API + DuckDuckGo Instant Answer | Redundant search coverage |

## Loop Detection (6)

| # | Decision | Rationale |
|---|----------|-----------|
| 13.1 | **Dual-strategy**: consecutive repeat + Shannon entropy | Comprehensive detection |
| 13.2 | **Repeat threshold: 3** (configurable) | Sensitivity tuning |
| 13.3 | **Entropy threshold: 1.5 bits** (hardcoded in `agent_loop_detect.py`, not configurable via `LoopDetectionConfig`) | Diversity threshold |
| 13.4 | **Rolling window: 30 calls** (configurable, min 10 for entropy) | Context window |
| 13.5 | **Stats observability** via `get_stats()` | Monitoring |
| 13.6 | **Escalation levels** — `LoopDetector` tracks warning levels 1–4 with separate repeat and entropy templates; triggers `LoopEscalationManager` for reflection injection and recovery | Progressive intervention |

## A2A Protocol (14)

| # | Decision | Rationale |
|---|----------|-----------|
| 14.1 | **JSON over Unix domain sockets** — all inter-agent messages are JSON-encoded, sent via `AF_UNIX` | Process-local, secure |
| 14.2 | **Socket naming: `/tmp/taua2a-{PID}.sock`** — each agent gets a unique socket path based on its PID | Collision-free |
| 14.3 | **Two request types: `agent_card` (sync) and `query` (async)** — metadata is immediate, queries are queued | Lightweight discovery |
| 14.4 | **Request ID correlation (UUID)** — every query gets a unique `id`, responses carry the same `id` | Response matching |
| 14.5 | **Acknowledgment pattern** — server sends `{"type": "queued"}` immediately, then `{"type": "response"}` later | Client confirmation |
| 14.6 | **Daemon thread for accept loop** — `_accept_loop` runs as `daemon=True` | Clean shutdown |
| 14.7 | **Per-client daemon threads** — each connection spawns its own thread | Concurrent clients |
| 14.8 | **`threading.Event` for startup synchronization** — `_ready` event set after bind/listen | Startup coordination |
| 14.9 | **Socket timeout of 1.0s in accept loop** — allows periodic checking of `self.running` flag | Graceful shutdown |
| 14.10 | **`SO_REUSEADDR` on server socket** — prevents "address already in use" on restart | Restart safety |
| 14.11 | **Scan `/tmp` with glob `taua2a-*.sock`** — filesystem-based discovery | Simple discovery |
| 14.12 | **Active-only default for `list_agents` display** — `_filter_active_agents` strips non-active by default | Clean output |
| 14.13 | **`json.JSONDecoder.raw_decode()` for streaming** — handles concatenated/fragmented JSON | Robust parsing |
| 14.14 | **A2A CLI mode** (`a2a_cli_mode`) — no agent created for discovery queries; short-circuits to direct socket communication for `--list`, `--card`, `--query` | Efficient discovery |
| 14.15 | **Heartbeat protocol** — server sends periodic heartbeats (`HEARTBEAT_INTERVAL=5s`) while polling for response; client uses idle-based timeout (`HEARTBEAT_IDLE_TIMEOUT=30s`) instead of wall-clock timeout; slow agents work indefinitely as long as heartbeats flow; dead server detected via missed heartbeat | Replaces fixed timeouts with adaptive liveness detection |

## Entry Point (7)

| # | Decision | Rationale |
|---|----------|-----------|
| 15.1 | **Identical `tau.py` and `tau-dut.py`** — two copies of the same entry point | Test isolation |
| 15.2 | **Line-buffered stdout** — `sys.stdout.reconfigure(line_buffering=True)` at module level | Interactive responsiveness |
| 15.3 | **Pre-parser for `--llm`** — resolves LLM group before building full parser | Correct help text |
| 15.4 | **`None` defaults for `--base-url`, `--model`, `--ctx`** — distinguish "user set" vs "group default" | Runtime switching |
| 15.5 | **`nargs="*"` for positional `inputs`** — zero or more inputs | Flexible invocation |
| 15.6 | **A2A client flags short-circuit to `a2a_cli_mode()`** — no agent created | Efficient discovery |
| 15.7 | **`--keep-alive` flag** — for A2A server mode | Headless operation |

## Think Tool (5)

| # | Decision | Rationale |
|---|----------|-----------|
| 16.1 | **Read-only fork** — `Think` spawns a fork with restricted tool access | Safe reasoning |
| 16.2 | **Allowlist of safe tools** — `glob`, `file_read`, `head`, `wc`, `pyscan`, `pyanalyze`, `grep`, `info`, `skill` | Read-only operations |
| 16.3 | **`THINK_PROMPT` constant** — pre-defined prompt for focused thinking | Consistent behavior |
| 16.4 | **No arguments required** — task is implicit in context | Simple interface |
| 16.5 | **`THINK_TOOL_ALLOWLIST` in `tools/think.py`** — explicit `frozenset` of allowed tools; `_build_safe_fallback` provides graceful degradation when fork fails | Safety + resilience |

## Cross-Cutting Principles (9)

| # | Decision | Rationale |
|---|----------|-----------|
| 17.1 | **`force_end_turn` mechanism** — any tool can terminate the current turn | Explicit control |
| 17.2 | **Graceful degradation** — features fail silently, agent continues operating | Resilience |
| 17.3 | **No external dependencies for core** — stdlib-only where possible | Zero-dependency core |
| 17.4 | **Heartbeat system** — idle detection with configurable interval | Self-monitoring |
| 17.5 | **`SubAgentResult` captures output + token metrics** — structured result containers | Observability |
| 17.6 | **Atomic single-append writes** — no explicit file locking | Concurrency safety |
| 17.7 | **Config source annotations** — `[env]`, `[file]` transparency in status display | Configuration visibility |
| 17.8 | **`ErrorRateTracker`** — thread-safe sliding window error rate tracking with burst detection and alert thresholds in `agent_session.py` | Proactive error monitoring |
| 17.9 | **`TokenTracker`** — centralized token accounting in `agent_token_tracker.py` with session-wide and per-turn counters; integrates `CacheTracker` for cache hit rates | Granular token observability |

## Context Management (7)

| # | Decision | Rationale |
|---|----------|-----------|
| 18.1 | **`TauContext._fork_metadata` separation** — fork metadata (pending tool calls, fork identity) stored separately from conversation history | Clean compression while preserving fork state |
| 18.2 | **Context validation after every mutation** (`_validate_on_mutation`) — enforces alternating USER/ASSISTANT turns, matching tool call/result pairs, no orphaned tool calls | API compliance |
| 18.3 | **`close_turn` mechanism** — ensures context ends in valid terminal state after incomplete turns | Graceful recovery |
| 18.4 | **`is_synthetic_message()` unified detection** — single function checks `SYNTHETIC_PREFIX` marker on any message (user or assistant); replaces old `_is_synthetic_user_message()` | Eliminated redundant detection logic |
| 18.5 | **Synthetic message protocol** — all system-injected messages use `SYNTHETIC_PREFIX = "[SYSTEM-SYNTHETIC: "` marker; `make_synthetic_user(category, content)` factory creates them; recovery paths inject synthetic user messages (NOT tool calls) to maintain OpenAI alternation; `is_synthetic_message()` detects them; `get_last_real_user_prompt()` finds real user boundaries; synthetic messages excluded from consecutive-role validation; `TauContext.append_synthetic_user()` and `TauContext.cleanup_synthetic()` are public methods for synthetic message operations; `cleanup_synthetic()` removes bridges WITHOUT merging (merge is explicit via `merge_consecutive_assistants()`) | Structured, consistent recovery; prevents context pollution; proper encapsulation |
| 18.6 | **OpenAI alternation INVARIANT** — `system → user ↔ assistant ↔ tool`; consecutive same-role messages are FORBIDDEN; synthetic bridges are NON-NEGOTIABLE (removing them breaks API compliance); any change bypassing bridges MUST prove alternation is maintained and pass all tests in `test_context_synthetic_bridge.py` + `test_recover_invalid_end_of_turn.py` | Prevents architectural oscillation; enforces API contract |
| 18.7 | **Explicit `merge_consecutive_assistants()`** — `cleanup_synthetic()` removes bridges ONLY (no merge); `merge_consecutive_assistants()` is public and caller-controlled; merges **assistant messages** (content, tool_calls deduplicated by ID, reasoning, refusal, usage_metadata summed); merges **user messages** gracefully (content concatenated, warning logged); does NOT merge tool messages (each has unique tool_call_id); `close_turn()` explicitly calls `merge_consecutive_assistants()` after `cleanup_synthetic()` | **Assistant-only merge was expanded to user merge: synthetic bridges are always user-role messages inserted after assistant messages, so removing them can only create consecutive assistant pairs. Consecutive user messages can also appear from tool-result / post-parse edge cases. Graceful merge prevents crashes while logging warnings for debugging.** Explicit merge chosen over auto-merge and no-merge: auto-merge hides symptoms by embedding policy in cleanup; no-merge ignores the alternation problem entirely. Explicit merge separates data cleanup from policy decision: cleanup removes internal synthetic bridges, merge is a visible, auditable caller-controlled step. Prevents architectural oscillation by making the merge decision explicit and documented. |
| 18.8 | **End-turn recovery redesign** — `_recovery_active` flag (reset at start of `invoke_with_tools_loop()`, set in `_recover_from_missing_end_turn()`); `last_substantive_response` only updates when NOT in recovery mode (prevents recovery responses from clobbering the original); empty responses still trigger recovery; reminder includes first 40 chars preview of stored response; **content-only end-of-turn was REVERTED** (broke subagent tests — subagents returned early without calling `end_turn`) | **Recovery lock prevents the "clobbering bug" where recovery responses overwrote the original good response. Preview in reminder gives the model clear context about what ENDTURN will resolve to, reducing confusion. Content-only end-of-turn was reverted because it caused subagents to short-circuit the turn loop before completing their work.** The recovery mechanism is a "last resort" for cases where the model keeps failing to call `end_turn`. All responses still require explicit `end_turn` (except via `force_end_turn`). |
| 18.9 | **NO syntax examples in LLM-facing messages** — all messages sent to the LLM (system prompt, tool metadata, recovery reminders, error messages, synthetic user messages) MUST NOT show Python-style function-call syntax like `tool_name(param='value')`. The LLM learns the tool interface from the JSON schema, not from examples. Showing syntax examples creates a contradiction: AGENT.md says "never describe tool calls as plain text" but our examples look exactly like plain-text tool calls. The LLM may try to reproduce the shown syntax as text instead of using the native tool-calling interface, causing malformed tool calls. Use natural language instead: "call the end_turn tool with message='ENDTURN'" → "call the end_turn tool with the message parameter". Applies to: `AGENT.md` (system prompt), `tools/end_turn.py` (tool metadata + error messages), `agent_core.py` (recovery reminders), `agent_loop_escalation.py` (escalation messages), `agent_tool_executor.py` (tool errors). Tool error messages describing what the LLM did wrong (e.g., "Original call: tool_name(args)") are acceptable — they describe the error, not teach syntax. | Prevents LLM confusion between natural language instructions and tool-calling syntax; eliminates contradiction with "never describe tool calls as plain text" rule; reduces malformed tool-call output |

## Configuration (4)

| # | Decision | Rationale |
|---|----------|-----------|
| 19.1 | **`LLMGroup` for multi-LLM support** — named groups with independent model, api_base, params; switchable via `--llm` CLI flag or `/llm` command | Flexible deployment |
| 19.2 | **Environment variable overrides** (`TAU_*` prefix) — all config keys overridable via environment variables | Deployment flexibility |
| 19.3 | **Config resolution order** — `tau.json` → env overrides → dataclass defaults | Predictable precedence |
| 19.4 | **`PathSecurityConfig`** — configurable path whitelist for sandbox validation via `allowed_paths` in config | Flexible sandbox boundaries |

## Logging & Disk Management (2)

| # | Decision | Rationale |
|---|----------|-----------|
| 20.1 | **No log rotation** — audit logs and context files are never rotated, deleted, or compressed by the system | Simplicity, no data loss risk, operator responsibility |
| 20.2 | **Oversized tool output to disk** — `write_oversized_output()` stores large outputs in `LOG_DIR`, context stays small | Disk backup, token economy |

## Model Health (3)

| # | Decision | Rationale |
|---|----------|-----------|
| 21.1 | **`ModelHealthMonitor` circuit breaker** — `agent_model_health.py` tracks LLM server health via `CircuitState` (closed/open/half_open); blocks calls when circuit is open, tests recovery in half_open state | Prevents cascading failures during server outages |
| 21.2 | **`HealthStatus` sliding window** — tracks consecutive failures/successes, total counts, last error timestamps; configurable thresholds via `HealthMonitorConfig` | Granular health observability |
| 21.3 | **`get_health_monitor()` singleton** — per-`base_url` monitor instance; `reset_health_monitor()` for manual reset; dashboard export to disk | Centralized health tracking without global state |

## Phantom Detection (3)

| # | Decision | Rationale |
|---|----------|-----------|
| 22.1 | **Phantom tool call detection** — `agent_phantom_detect.py` detects tool-call-like XML tags that postparse missed; raises `InvalidReplyError` to trigger retry | Catches LLM-generated fake tool calls before they pollute context |
| 22.2 | **Configurable rules via `phantom_rules.json`** — `PhantomRules` dataclass: suffix/prefix patterns, command keywords, whitelist tags, confidence threshold; loaded from file with graceful fallback | Adaptable detection without code changes |
| 22.3 | **Levenshtein scoring** — `_score_phantom()` computes edit distance against known tool names; confidence threshold filters false positives | Precision over recall for phantom detection |
