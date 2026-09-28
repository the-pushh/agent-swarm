# Run the complete MVP

From this repository:

```bash
.venv/bin/python -m mvp.terminal
```

To open the saved live Go example directly:

```bash
.venv/bin/python -m mvp.terminal --run morning-demo
```

The saved `morning-demo` finished at plan v4 with two accepted reports, a preserved rejected attempt, one live recovery, and a model verdict of `goal_satisfied`. Read `brief-corrected` for the final research. The reviewer treated its 100-word recommendation as meeting the conciseness intent even though the corrective instruction said “under 100”; strict natural-language constraints still need human review.

The existing `.env` and model settings are reused. If recreating the environment, install `requirements-mvp.txt` using Python 3.12. Nothing needs hosting. Opening saved work is free of model calls; creating, starting, directing, recovering and checking can make billable calls.

## A quick walkthrough

1. Type your goal in chat and press Enter. The planner replies with proposed work; agents have not started yet.
2. Tell it what to change, or say **start** to run the next ready task. Say **start 4** to run task 4 from the displayed plan. A selected task waits for its dependencies; unavailable specialists cannot run. Each start runs one task, then reviews the plan and waits again if work remains.
3. Click **Inspect** when you want to see active agents and the proposed steps. The **Proposed work** tab shows numbered steps, assignments, completion criteria, and reports. The **Agents** tab always includes **Swarm**, the coordinator that proposes and reviews work. Its tile shows live planning status and saved decision summaries, even before task agents exist. The tab also shows a tile grid of assigned agents, labeled working, ready, waiting, or completed. Click a tile to see its live tool activity, review summary, and report. These are recorded actions and summaries, not private model reasoning. Click **Close** to return to just chat.
4. Once work finishes, chat shows the goal-check result. Send another change if needed.
5. Use `/open` to list saved chats, then `/open NAME` to reopen one. Use `/new` for a new goal.

Say **status** for progress, **stop** to stop work, and **resume** to recover interrupted work. Changes sent during execution are not applied: stop first, then send your change again. `/help` shows the short command list. Chat messages about changes go to the planner; this is not a general question-answering assistant.

Shortcuts: Ctrl-I inspect, Ctrl-N new chat, Ctrl-X stop, Ctrl-Q quit. The chat works alone; a wider terminal gives the optional inspector more space. No forms or tabs are required.

## Measurable goals

New projects propose a concrete finish line from your goal. For example, a first-dollar goal needs received customer revenue, while a 1,000-user goal needs a definition of a qualifying user and evidence of 1,000 such users. Personal-growth goals use observable practice or demonstrated skill. Explicit research-only requests still produce research plans.

Before starting, review the outcome and markers in chat. Use `/goal` or select the goal in Inspect for the metric, baseline (unknown unless supported), target, counting rule, measurement period, and required evidence. Proposed currency, timeframe, and other missing details must be disclosed as assumptions. Tell the planner to correct the proposal before the first **start**.

Starting approves the markers. Later plans may change tasks, but cannot change the finish line or weaken its evidence requirements. Every task is linked to markers it advances. The human-work evaluator receives those markers and checks alignment; intermediate work need not meet the entire project target.

The final goal review reports every marker separately with a measured value or **unknown**, cited task evidence, and whether it is met. It cannot mark the goal satisfied while any marker lacks proof or falls below its threshold. Revenue, user-growth, and personal-growth markers additionally require evaluator-accepted human evidence; a research report alone cannot prove growth. Submitted evidence is still subject to model judgment, not independent auditing.

Existing saved projects remain readable and are not assigned invented metrics. Start a new project to get the full objective proposal; existing markers survive reopen in the saved plan and project summary. Changing an already approved finish line requires a new project.

## Human work and evaluation

For goals requiring action after research, the planner assigns that action to **human** and links it to the research. There is no fixed task count or automated/human ratio. Assignments depend on the goal and connected capabilities. Starting the human task shows your instructions and completion criteria. The task waits; no agent performs the action for you.

After doing the work, send `done: [paste the work, code, or relevant output]`. Saying only `done` asks for evidence without making an evaluator call. The evaluator compares the submission with the linked research (including research inherited through earlier tasks), the task criteria, and your constraints.

- **Accepted:** the human task becomes completed and later dependent work can proceed.
- **Changes required:** chat lists corrections. Fix the work and send another `done: ...` submission.
- **Insufficient evidence:** supply the requested evidence; the task stays pending.

Human assignments appear in Proposed work, not in the Agents tab. Inspect → Agents has a separate Evaluator tile for work verification. Selecting the tile shows submitted evidence and all evaluation summaries. Pending handoffs, rejected submissions and feedback survive restart. Each evidence submission can make billable model calls, limited to three calls and 180 seconds per evaluation.

Evaluation checks pasted material; it does not browse submitted links, read local files, run code, or independently inspect accounts or physical work. Results retain that verification scope. Existing plans are not silently rewritten; request a human follow-up in chat while the plan is awaiting review if an older plan lacks one.

## Test recovery

Start a new research goal, let it begin, and say **stop**. After it stops, say **resume**. You can quit and reopen before resuming.

Recovery reconciles saved work:

- A task that saved an accepted report before interruption is adopted without repeating its research calls.
- A partly finished or unverifiable task is marked failed with its artifacts preserved. The living planner may choose a fresh task ID and another approach, or pause. It never overwrites the old attempt.
- Interrupted planning resumes to a plan review; it does not skip the initial Start gate.
- If the app was killed abruptly, reopen the project so the SQLite checkpoint is inspected before recovering.

Recovery is at project/task boundaries. It does not resume midway through an individual model response. An interrupted planning/review call may be called again and billed. Research retries use new IDs and consume the existing project budget. This is not an exactly-once system for external writes; those tools are not connected.

## What is actually implemented

- Model-generated plans with responsible owners and specialist assignments.
- Reviewed plan editing, dependencies and a living planner with bounded revisions.
- Real public-web research, evidence checks and owner review.
- Chat for goals, plan approval and changes; one optional inspector for active agents, proposed work and reports.
- Durable project state, cancellation, task reconciliation and restart.
- A final model review against the original goal and constraints, with `goal_satisfied` or `needs_attention`, plus a saved `summary.md` linking the reports.

Research searches and reads public pages. Human tasks are performed by you, with a model evaluator checking your submitted evidence against prior research. Email, calendar, cron and browser/computer control are still unavailable as automated specialists. The planner cannot turn researching an action into proof that the action was performed. Existing email/calendar tools elsewhere in this repository remain separate.

Goal assessment is a model judgment over saved briefs and available supporting passages. It may still miss errors. Source references alone are not proof of factual correctness. The live test exposed a JSON API-version mix-up and an unfinished recommendation; review prompts were tightened and the example received a separate corrective follow-up, preserving the original evidence and review history.

## Saved files and CLI

Projects live under `.agent-state/mvp/projects/<run>/`. They include the conversation in `chat.json`, `project.sqlite`, `state.json`, every `plan-vN.json`, `changes.json`, `summary.md`, and per-task reports/evidence. Never put API keys in goals or task text.

The CLI remains available:

```bash
.venv/bin/python -m mvp.project show morning-demo
.venv/bin/python -m mvp.project recover YOUR_RUN
.venv/bin/python -m mvp.project check YOUR_RUN
.venv/bin/python -m mvp.project revise YOUR_RUN "Your new constraint" --revision N
```

`/check` is available in chat after execution, including older projects that ended at `tasks_completed`. A missing requirement is reported; the user can add direction to address it. The checker itself does not silently create new work.

Limits remain visible and persisted: 8 planner reviews, 12 task attempts, 12 active steps, 24 lifetime IDs, 3 recovery commands and 3 goal reviews per project. Each model-backed planner/goal review permits up to 3 calls with a 180-second deadline; each research task uses Stage 2's own limits and 300-second deadline. There is no aggregate dollar budget. Limits do not reset on reopen. One command may mutate a project at a time.

## Verification

All 155 tests passed, including the existing email/calendar regressions; dependency checks passed. The suite covers normal planning/revision behavior plus real Textual interactions, cancellation, reopening, malformed/partial artifacts, interrupted planning, and abrupt process death after saving a research result but before graph commit. That crash test confirms that recovery adopts the accepted artifact without calling research again. UI layouts are also checked through rendered screenshots, and the app was launched and quit in a real terminal.

The live Go example also exercised an actual cancellation and recovery during plan review. An intermediate correction was rejected and preserved; the planner's attempt to depend on a blocked task exposed a missing check, which is now enforced for direct and indirect dependencies. The saved revision history includes that development-time failure rather than hiding it.

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m pip check
```

Reusable infrastructure: [Textual](https://textual.textualize.io/), [LangChain](https://docs.langchain.com/oss/python/langchain/overview), [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview), Rich, DDGS, Trafilatura and OpenRouter. Stage 5 adds `mvp/terminal.py`; Stage 6 adds `mvp/recovery.py`, `mvp/completion.py`, synchronous checkpoint writes and CLI recovery/check commands.
