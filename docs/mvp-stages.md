# Living goal map: staged implementation

This supersedes the earlier niche/product plan in `research/collaborators-mvp.md` for the current MVP. Input is an already usable goal. The interface is local and terminal-only. There is no Codex CLI runtime, hosted UI, or intent-discovery onboarding.

## Reporting at every checkpoint

Stop at the end of each stage for user testing and direction. Report:

1. What the code was intended to contain.
2. What behavior was expected.
3. What was actually verified, with limitations.
4. How to run that checkpoint.
5. What the next stage adds.

## Stage map

| Stage | Scope | Observable checkpoint |
| --- | --- | --- |
| 1 — Reuse foundation | Select framework; execute an offline delegation/blocker/revision/resume proof with durable state. | A fresh process resumes blocked work without repeating a completed branch. |
| 2 — Real agent execution | Reuse LangChain's agent/tool loop over an API model; validate provider compatibility and tool arguments; add bounded real research/search with citations. | A responsible agent delegates a real job, checks the specialist result, and saves an artifact. |
| 3 — Goal to initial plan | Model-generated task hierarchy, separate dependencies, owner assignments, completion criteria, editing and initial review. | An unseen goal produces a valid saved plan; no task starts before Start. |
| 4 — Living planner | Event-triggered model revisions, task splitting, pending-task scheduling, limits, validated versioned patches. | A genuine blocker or changed constraint alters remaining work while preserving completed evidence. |
| 5 — Interactive terminal | Textual chat for goals, approval and changes; one optional inspector for agents, proposed work and reports. | Operate the entire workflow from the terminal. |
| 6 — Integrated demo and recovery | End-to-end validation, cancellation and restart behavior, clear unsupported capabilities and goal-completion checks. | Goal → reviewed plan → real work → revision → deliverables, with recoverable state. |

Stage 1 uses a deliberately tiny fixed plan to test the framework. It is not the full dynamic planner. Later stages build on the verified checkpointing rather than treating fixture decisions as agent intelligence.

## Framework selection, checked 2026-09-28

**Selected: LangGraph for orchestration**, using its SQLite checkpointer, interrupts, Send fan-out and compiled subgraphs. The reusable workflow remains stable while project tasks will become versioned data. We do not implement a replacement checkpoint engine or agent runner.

- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview): stateful orchestration and mixing deterministic/model-driven steps. Direct LangGraph use does not require the higher-level LangChain package.
- [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts): persisted pauses and explicit resume. Interrupted nodes restart, so work before an interrupt can replay. Our fixture performs no external writes and keeps the pause separate from specialist work.
- [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence): checkpointed graph execution. This does not establish exactly-once external side effects; future provider writes still need idempotency/reconciliation.
- [CrewAI Flows](https://docs.crewai.com/en/concepts/flows): considered as another workflow approach. We chose LangGraph for explicit state transitions and a direct fit to the pause/resume exercise, not because CrewAI cannot support the product. CrewAI was documentation-reviewed, not runtime-benchmarked.
- [Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview): provides a higher-level harness with delegation/context facilities on the same ecosystem. Do not rebuild those facilities speculatively. Evaluate its scoped subagent support versus LangChain `create_agent` in Stage 2; it is not installed or claimed verified in Stage 1.

Installed and pinned in `requirements-mvp.txt`: `langgraph==1.2.12`, `langgraph-checkpoint-sqlite==3.1.1`, `rich==15.0.0`. Use the existing Python 3.12 `.venv`; the Mac's system Python 3.9 is not the MVP runtime. Top-level versions are pinned; the transitive environment is not a full production lockfile.

## What is reused versus new

| Responsibility | Reused implementation | New product code |
| --- | --- | --- |
| Execution branching | LangGraph graph + Send | Decide which project tasks are eligible |
| Delegation | Compiled owner subgraph | Allowed specialist assignment and acceptance policy |
| Persistence/restart | LangGraph SqliteSaver | Stable run ID and CLI lifecycle checks |
| Pause/resume | LangGraph interrupt + Command | Review choices and revision semantics |
| Terminal tree | Rich Tree | Task labels/status projection |
| Future model/tool loop | LangChain or Deep Agents, compatibility-tested in Stage 2 | Scoped prompts, tools and completion checks |
| Email/calendar | Existing domain modules | Specialist adapters; retain existing approval gates |

The existing `providers/openrouter.py` only returns JSON text and deliberately rejects native tool calls. Stage 2 must use a compatible framework model integration or a tested adapter; simply passing that function into a tool-calling agent will not suffice. Do not replace the existing email/calendar behavior to make this work.

## Stage 1 code and expected result

- `mvp/workflow.py`: framework graph, owner subgraph, initial review and one supported revision.
- `mvp/fixture.py`: explicit scripted research results and fixed example plan. No API keys, web search, or model calls.
- `mvp/__main__.py`: local start/status/resume commands and Rich tree.
- `tests/test_mvp_workflow.py`: lifecycle and fresh-process recovery tests.

Expected sequence:

1. `start`: save plan v1 and pause before delegating; both attempts are zero.
2. Resume with `start`: run two independent owner subgraphs. Positioning completes. Wholesale production blocks on minimum order quantity. Both attempts are one.
3. Exit and use `status` in a new process: the same state and pending question remain.
4. Resume with `local-printers`: save plan v2, retry only production, finish. Positioning attempts remain one; production attempts become two.
5. Reusing a run ID, resuming a finished run, or submitting an invalid choice fails without resetting the saved work.

Run from the repository root:

```bash
.venv/bin/python -m pip install -r requirements-mvp.txt
.venv/bin/python -m mvp start --run my-first-demo
.venv/bin/python -m mvp resume my-first-demo start
.venv/bin/python -m mvp status my-first-demo
.venv/bin/python -m mvp resume my-first-demo local-printers
```

Choose a new run ID to repeat. `stop` is available at both review points. Add `--json` before the subcommand for saved state and pending questions. Checkpoints live in ignored `.agent-state/mvp/stage1.sqlite`; override using `--db PATH` before the subcommand. For this checkpoint, run one CLI command at a time against a given database; concurrent writers and duplicate starts are not yet an application-level supported mode.

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_mvp_workflow.py' -v
.venv/bin/python -m unittest discover -s tests
```

The acceptance check validates only that artifact/evidence fields are present. It does not evaluate source quality. The two attempts and six recorded delegation/verdict events demonstrate logical work was preserved across normal process exits; no crash-time exactly-once guarantee is claimed. Files or real provider writes are not part of this fixture. The owner and revision decisions are scripted and explicitly labelled.

Verified at this checkpoint: all 113 tests passed (108 existing + 5 new), including CLI invocations in separate processes. `pip check` reported no broken requirements. The manual `stage1-check` run finished at plan v2 with positioning attempted once and production twice. Existing email/calendar implementation files were not edited.

## Next: Stage 2

Wire an API-backed framework agent with scoped tools, check actual tool-call compatibility using the configured provider, and choose a real search/retrieval tool after inspecting available configuration without printing secrets. Reuse framework step limits and tool validation rather than implementing another while-loop. Prove real delegation with a useful cited artifact. Keep the Stage 1 fixture as an offline regression check. Initial arbitrary-goal planning then follows in Stage 3.

Stage 2 implementation and evaluation instructions are now in [stage2.md](stage2.md). The offline Stage 1 commands remain unchanged.

Stage 3 implementation and evaluation instructions are in [stage3.md](stage3.md). It generates and reviews real plans, then connects research tasks to Stage 2.

Stage 4 implementation and evaluation instructions are in [stage4.md](stage4.md). New projects use event-triggered revisions with preserved completed work and bounded execution.

Stages 5 and 6 are implemented. See [the complete testing guide](morning-test.md) for the interactive terminal, recovery, final goal checks and current limitations.
