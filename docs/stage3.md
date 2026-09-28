# Stage 3: goal to reviewed plan

Stage 4 now extends new projects with automatic plan reviews after task results. See [Stage 4](stage4.md) for the current execution behavior and limits; the initial review/edit workflow below still applies.

Give the app a usable goal. It generates a small plan, saves it, and shows it as a terminal tree. Nothing executes until you explicitly start the reviewed version.

## Try it

Use the same Python 3.12 environment and `.env` as Stage 2. No new packages are needed.

```bash
.venv/bin/python -m mvp.project create "Research two tools for local meeting notes, then use the findings to recommend a small MVP for consultants." --run my-plan
.venv/bin/python -m mvp.project show my-plan
```

`create` makes model API calls. `show` reads saved state with no model or web calls. Each task shows its owner, specialist, instruction, completion criteria, and dependencies. Groups organize tasks; dependencies determine which results a task needs. The goal remains the root of the tree.

To edit, copy the saved plan to a separate file, change it in your editor, then submit it:

```bash
cp .agent-state/mvp/projects/my-plan/plan.json /tmp/my-plan-edit.json
# Edit /tmp/my-plan-edit.json with your editor.
.venv/bin/python -m mvp.project edit my-plan --file /tmp/my-plan-edit.json
```

Keep the `revision` in the copied document unchanged. Edit the contents of `plan`: steps, owners, assignments, criteria, assumptions and dependencies. Valid edits create a new version and return to review. Invalid or stale edits leave the saved plan untouched. `plan.json` is a generated export; edits take effect only through the edit command. The original goal is fixed for this run.

Start the version shown by `show` (for example, version 2 after one edit):

```bash
.venv/bin/python -m mvp.project start my-plan --revision 2
```

Starting makes billable model and public-web calls for research tasks. Tasks run one at a time in dependency order. Each uses Stage 2's responsible agent → Research specialist → evidence review path. The owner receives its role, assigned task, completion criteria and accepted dependency briefs. Task-specific research state and reports are saved under the project directory.

Only `research` is executable in this stage; it includes search and page reading. The planner may honestly assign `web_search`, `browser_use`, `computer_use`, `email`, `calendar`, `cron` or `human` when the goal requires them, but the tree marks these unavailable. On Start, those tasks block; their dependents do not run. Independent research can still finish. Existing email/calendar modules are not yet connected to this workflow.

## What to evaluate

- Does the plan cover your actual goal without replacing actions with research?
- Are the steps small and their owners and completion criteria useful?
- Do dependent steps explicitly identify the earlier work they need?
- Can you change a plan, close the process, and see the same version later?
- Does Start execute only the reviewed plan and produce useful research reports?

The first plan is a proposal, not a guarantee that the goal is achievable. Review semantic quality yourself: validation can catch missing references and cycles, but cannot prove that the model chose all the right dependencies or useful criteria.

## Code and reuse

- `mvp/planning.py`: Pydantic plan schema, LangChain structured planner, LangGraph draft/review/work graph. IDs, hierarchy, dependency references, cycles and assignments are validated. LangChain handles model format/validation corrections (at most three model calls).
- `mvp/project.py`: Rich tree, local CLI, version exports, per-project process lock and Stage 2 execution adapter.
- `tests/test_mvp_planning.py`: model correction, plan validation, review/edit/start, dependency context, unavailable work, stale edits and fresh-process persistence.

Reuses [LangChain structured output](https://docs.langchain.com/oss/python/langchain/structured-output) and [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts). SQLite is authoritative; JSON files are reviewable exports. The review node is separate from planning, so edits and Start do not rerun the planner.

Saved under `.agent-state/mvp/projects/<run>/`:

- `project.sqlite`: persisted workflow, review pause and task results.
- `plan.json`, `plan-v1.json`, etc.: current and historical plans.
- `state.json`: last saved project state.
- `tasks/<task-id>/`: Stage 2 report, source evidence and research checkpoints.

## Current limits

Plans contain at most 12 entries. Planning has a 180-second deadline and up to three model calls; each research task has Stage 2's own limits and 300-second deadline. There is no shared project dollar budget yet. Model settings follow Stage 2's `MVP_MODEL`, `OPENROUTER_MODEL`, and `MVP_REASONING_EFFORT` settings.

`tasks_completed` means all planned tasks were accepted by their responsible agents; it does not independently verify that the real-world goal is achieved. Reports can contain mistakes. A blocked task does not trigger automatic replanning yet.

Review pauses survive process exits. Interrupted execution saves partial files and checkpoints, but Stage 3 deliberately exposes no automatic crash recovery/retry command. Re-running Start on an already started project is rejected. Use a new project to retry; later recovery work will reconcile interrupted tasks without silently duplicating them. `show` reports checkpointed status, which may say running after a crash. One command at a time can access a project.

## Verified examples

- `stage3-review`: a live model-generated research → recommendation → email plan. Dependencies are explicit; human/email work is marked unavailable. Still paused, with no tasks run.
- `stage3-execution`: a live model-generated one-task plan, edited to version 2 in a separate command, then explicitly started. The existing research path searched twice, read three pages, and produced an owner-accepted report. Both plan versions remain saved.
- Automated coverage includes seven Stage 3 tests plus the previous suite, including fresh-process review/edit and passing dependency source links to downstream research. This is a functional checkpoint, not a planner-quality benchmark.

```bash
.venv/bin/python -m mvp.project show stage3-review
.venv/bin/python -m mvp.project show stage3-execution
```

## Next

Stage 4 adds the living planner: react to task results and blockers, split or revise remaining work, and preserve completed results. Stage 5 adds an interactive terminal interface. Stage 6 completes recovery and end-to-end goal checks.
