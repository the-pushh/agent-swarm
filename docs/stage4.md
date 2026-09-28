# Stage 4: a living plan

Stages 5 and 6 now add the interactive terminal and recovery. See [the current testing guide](morning-test.md); its recovery instructions supersede the earlier limitation below.

The planner now reviews each finished, failed or blocked task. It can keep the plan, change unfinished work, split a task into smaller steps, or pause with a reason. The next task is chosen from the updated dependencies.

## Try it

The commands are the same as Stage 3. New projects enable the living planner automatically:

```bash
.venv/bin/python -m mvp.project create "Your usable goal here" --run living-test
.venv/bin/python -m mvp.project show living-test
.venv/bin/python -m mvp.project start living-test --revision 1
```

Initial planning still pauses before Start. Once started, valid automatic revisions take effect without another approval prompt. The terminal prints the reason and plan version. Each task retains Stage 2's responsible-agent review and evidence checks.

To add a constraint when the command has finished or paused:

```bash
.venv/bin/python -m mvp.project revise living-test "Use only official documentation for the remaining research." --revision 2
```

Use the current version shown by `show`, not necessarily 2. The planner considers the constraint and existing results, then returns to plan review (or pauses if it cannot proceed). Inspect the plan before using Start again. Starting resumes remaining work; completed tasks are not rerun. Constraints accumulate and are passed to later research as well as the planner.

Stage 3 projects retain their static behavior until you explicitly use `revise`. This can upgrade a saved review, blocked, paused or task-completed project. It cannot recover a process killed mid-execution. The initial goal and its completion criteria remain fixed; a different goal needs a new project.

## What to evaluate

- After a blocker, does the new route still work toward the same goal?
- Does splitting a step update the steps that depend on it?
- Are completed reports preserved without repeat work?
- Does a new constraint change the remaining work appropriately?
- Does the planner explain when it cannot proceed instead of pretending the goal is complete?

The JSON-edit workflow from Stage 3 still works at review pauses. After any work has run, edits must preserve attempted task definitions and completed tasks. Replace unfinished attempted work with fresh IDs instead. A plan cannot delete accepted work or weaken the original completion criteria during execution.

## Saved history

Inside `.agent-state/mvp/projects/<run>/`:

- `plan-vN.json`: every exported plan version, including automatic revisions.
- `changes.json`: planner decisions, triggering events, exact patches and resulting versions. A keep decision records a review without creating a new plan version.
- `state.json` and `project.sqlite`: cumulative constraints, results, review counts and history.
- `tasks/<task-id>/`: original research artifacts and evidence. Replacement tasks use fresh directories. Removed attempts remain in results and are shown beneath the active plan.

SQLite remains authoritative. JSON exports are refreshed at graph updates. No existing research artifact is rewritten by a plan patch.

## Architecture and reuse

`mvp/replanning.py` defines a small patch: base revision, reason, keep/revise/pause, changed steps and removed IDs. It reuses LangChain's structured-output loop and validation feedback; there is no custom model loop. Pydantic and the existing Plan validator check the resulting hierarchy and dependency graph. A patch must match the current version, preserve completed work, avoid rewriting attempted IDs, and stay within limits.

`mvp/planning.py` adds a reflection node after task results to the existing LangGraph workflow. The scheduler reads the current plan each time, skips attempted tasks, and selects a task only after its dependencies complete. Unsupported tasks block without invoking a tool. Revisions can reroute a public-research task to the research specialist; they cannot make unavailable email/calendar/computer tools executable.

`mvp/project.py` adds the constraint command, streams saved plan versions, and displays the reason for changes. It uses the existing OpenRouter integration and SQLite checkpointer. No new dependency is required.

References: [LangChain structured output](https://docs.langchain.com/oss/python/langchain/structured-output), [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence).

## Bounds and limitations

- At most 8 planner reviews per project, including user constraints and failed reviews; each review allows up to 3 model calls and 180 seconds.
- At most 12 attempted tasks, 12 active plan entries and 24 lifetime step IDs. Each research task still has its own Stage 2 limits. There is no combined dollar budget.
- Reaching a limit, failing patch validation after model corrections, or a provider failure pauses the project and preserves its current plan/results. Limits do not silently reset when adding a constraint.
- Failed or blocked work stays recorded. A retry must have a fresh ID and a meaningful changed approach; the planner is instructed against futile retries, but semantic quality remains a model judgment.
- Validation checks graph structure and preservation. It cannot prove that a revised plan fully covers the goal, that a claim is true, or that the planner chose the best route. `tasks_completed` refers to the active planned tasks, not independently verified goal achievement.
- Work remains sequential. New constraints are accepted between commands, not while a task holds the project lock. Durable mid-execution crash recovery is still Stage 6.
- Research is still the only connected specialist. This stage improves planning, not the list of external actions the app can perform.

## Verified checkpoint

All 136 tests pass (including six Stage 4 tests), and dependency checks pass. Tests cover patch validation feedback, splitting and rewiring a blocked task, preservation of completed results across restarts, limits, pause handling, constraints and saved versions.

The live `stage4-demo` started from a model-generated plan. For the blocker test, its public research task was deliberately assigned to the unavailable `web_search` specialist during plan review. The actual capability check blocked it. The model then replaced it with a fresh research task in v3, and that task produced an accepted sourced report. This tests a real capability blocker, not an unexpected external outage.

A subsequent constraint generated v4 with an async-checkpoint follow-up that depends on the completed task. The completed task definition stayed identical; only one research execution directory exists. V4 is intentionally waiting for review, so the additional research has not run:

```bash
.venv/bin/python -m mvp.project show stage4-demo
```

Task splitting was verified with framework tests; the live model chose a one-task replacement. These examples establish working behavior, not a reliability benchmark across goals.

## Next

Stage 5 adds interactive terminal navigation: inspect the goal map, see changes and evidence, and direct the project without switching between commands and JSON files.
