# Stage 2: real delegation and research

Stage 1 tested orchestration with fixtures. Stage 2 adds a real model and real public-web tools. It handles one research task, not a whole generated project plan. That arrives in Stage 3.

## Run it

From the repository root, use the existing Python 3.12 environment:

```bash
.venv/bin/python -m pip install -r requirements-mvp.txt
.venv/bin/python -m mvp.live "Compare LangGraph SQLite checkpoints with a JSON file for resuming agent work. Use official sources and recommend an approach." --run my-research
```

The command loads this repository's `.env` without overwriting exported environment variables. It needs `OPENROUTER_API_KEY`. It uses `MVP_MODEL`, then `OPENROUTER_MODEL`, then the existing default. No API key is printed. Search uses DDGS; no extra search key is needed. Queries go to external search services, page URLs are fetched, and the task plus retrieved text are sent to the configured model provider.

Choose a new run name to repeat. Existing results cannot be overwritten. A new run makes billable model calls. No messages are sent, meetings booked, apps controlled, or user files exposed to model tools.

Inspect results later without network/model calls:

```bash
.venv/bin/python -m mvp.live --show my-research
```

Each run writes under `.agent-state/mvp/live/<run>/`:

- `report.md`: findings with source links, recommendation, limitations, and the owner's review. Failed/blocked reports are explicitly NOT ACCEPTED.
- `run.json`: task, delegated assignments, attempts, sources with retrieved text/timestamps, numbered supporting passages, events, elapsed time and available token totals.
- `checkpoints.sqlite`: LangGraph conversation checkpoints for owner and research attempts.

The terminal shows model/delegation/search/read/review events. Ctrl-C cancels and saves partial work. `--timeout 300` is the default whole-run deadline (allowed range: 30–600 seconds). `--show` displays the last saved status, not proof a process is still active.

## What the code should do

1. A model-backed responsible agent receives the task and calls `delegate_research` with an assignment.
2. A separate model-backed Research agent calls `search_web`, then `read_source` using IDs from search results.
3. Research submits a structured brief: findings, source IDs, supporting passage IDs, recommendation and limitations.
4. Code checks that at least two source pages were read and each cited passage exists in the retrieved text. Search snippets alone cannot pass. Findings paraphrase the evidence rather than reproducing whole passages.
5. The responsible agent receives the brief, evidence text and validation issues. It checks relevance/support/coverage and can request one correction.
6. Acceptance must refer to the latest delegated attempt, which must pass the mechanical checks. Only then is the run marked completed. Otherwise it is blocked or failed, with partial results preserved.

The owner reviews meaning; the mechanical check only verifies source/passage provenance. A real passage can still be misinterpreted. Two pages are not necessarily independent sources. A completed run is a research artifact, not proof that a broader business goal was achieved.

## Reuse decisions

| Responsibility | Existing tool used |
| --- | --- |
| Agent/tool loops for both agents | LangChain `create_agent` |
| Tool schemas and argument validation | LangChain tools + Pydantic |
| Structured final answers | LangChain `ToolStrategy` |
| Model call/delegation limits | LangChain middleware |
| Model provider | `langchain-openrouter` / `ChatOpenRouter` |
| Conversation checkpoints | LangGraph `AsyncSqliteSaver` |
| Public-web search | DDGS |
| HTML main-text extraction | Trafilatura |
| HTTP transport | HTTPX (transitive dependency) |
| Terminal display | Rich |

Custom code connects these pieces and implements our responsibility/evidence rules. There is no new hand-written model/tool while-loop. Deep Agents was considered, but its filesystem/context harness is unnecessary for this bounded research task; the scoped agent-as-tool composition works directly through `create_agent`. Existing Gmail/Calendar code and its approvals are unchanged. Their current JSON-only provider is not repurposed as a tool-calling model.

Implementation:

- `mvp/research_agents.py`: structured results, evidence gate, framework-built owner and specialist.
- `mvp/research_tools.py`: small adapters over search/extraction, evidence collection and run-wide tool budgets.
- `mvp/live.py`: configuration, checkpoint wiring, progress, cancellation/deadline, and saved reports.
- `tests/test_mvp_research.py`: framework-level delegation/correction tests and failure/provenance checks using a scripted test model.

## Limits and current boundaries

- At most two research delegations, eight model calls per research attempt, five owner model calls, four searches and eight page requests per run. Framework limits fail the run rather than silently declaring success. Structured output corrections also consume the model budget.
- Model output is capped at 6,000 tokens per call. Requests use a 45-second configured provider timeout, no provider retries, and the whole-run deadline is an additional bound. `MVP_REASONING_EFFORT` defaults to `low`; provider/model support varies.
- Search is best-effort public metasearch and can fail or rate-limit. No hidden fixture fallback exists in live mode.
- The reader accepts public HTTP(S) HTML/text, checks destinations and redirects, limits each response to 1 MB, and supplies up to 14,000 extracted characters per page. It does not operate a browser, authenticate, or read PDFs. Standard DNS checks are not a hardened network-isolation environment.
- Source text is untrusted input. The prompts reject instructions embedded in it; the exposed tools are restricted to public research. These measures do not establish immunity to prompt injection or factual error.
- Application-side LangSmith tracing is disabled. Local framework checkpoints may include raw model responses and tool messages; the displayed activity log does not expose reasoning or credentials.
- Token totals include responses where the provider returned usage. Failed/cancelled calls may still be billed without a recorded total. Dollar cost is not estimated.
- Stage 2 does not expose live checkpoint resume yet. Partial results survive, but restarting a live task means a new run. Stage 1's saved pause/resume demo remains available. Production recovery and integrated project resume require the later stages.

## Sources used for the integration

- [LangChain agents](https://docs.langchain.com/oss/python/langchain/agents)
- [Built-in middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in)
- [ChatOpenRouter integration](https://docs.langchain.com/oss/python/integrations/chat/openrouter)
- [DDGS](https://github.com/deedy5/ddgs)
- [Trafilatura](https://trafilatura.readthedocs.io/en/latest/quickstart.html)
- [OpenRouter reasoning controls](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)

## Stage evaluation

The live `stage2-demo` run completed with the configured GLM model: one research delegation, two searches, three page reads and seven model calls. It produced five findings, source links, a recommendation, limitations and an owner review. The saved example is `.agent-state/mvp/live/stage2-demo/report.md` (local, ignored by Git).

The full suite passes: 123 tests, including 10 Stage 2 tests. Dependency checks also pass. Tests cover framework delegation, correction, invalid evidence, call limits, provider failure, timeout and cancellation.

Earlier live trials exposed truncated responses and brittle exact-quote validation. The final version sets low reasoning effort, allows 6,000 output tokens and uses retrieved passage IDs. This is a working demonstration, not a measured reliability benchmark; owner acceptance still needs human judgment for consequential conclusions.

## Next stage

Stage 3 takes a usable goal and generates a saved, editable plan with assigned responsible agents, completion criteria and dependencies. The plan pauses before any work begins. Its research steps use this Stage 2 execution path. Stage 4 then adds the living planner that changes unfinished work as results arrive.
