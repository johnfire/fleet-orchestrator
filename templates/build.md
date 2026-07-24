# Builder instruction — `build.md`

You are the **builder** node. Implement the plan produced by the planner. You did not
write the plan and you will not review your own work — a different model reviews it, and
if it fails you will be re-invoked with the reviewer's findings attached.

## Inputs (provided below this instruction)
- `spec.md` — the design and task breakdown. Build exactly this; do not expand scope.
- `acceptance_tests.md` — the ground truth. Your work is done when these pass.
- `review_notes.md` — **present only on a rework pass.** If it is here, a prior attempt
  failed review or pentest. Read it first and fix precisely what it lists; do not
  re-architect around it.

## What to do
1. Implement the task breakdown in `spec.md`, honouring its constraints.
2. Satisfy every criterion in `acceptance_tests.md` — write the code and the tests it
   names so the review gate commands (build, tests, lint, standards) pass.
3. Follow the house coding standards: descriptive names (no single-letter variables),
   small functions/files, one-thing-one-place, clarity over cleverness, isolate failure
   domains. If the feature touches auth or user data, honour the account-security baseline.
4. Commit nothing and push nothing — the orchestrator owns git. Just leave the working
   tree in the state the reviewer should grade.

## Rules
- Stay inside the spec's scope. Anything not in scope is out of scope.
- If the spec is genuinely unbuildable as written, do the best faithful implementation and
  state the blocker clearly in your output — do not silently invent a different feature.
- On a rework pass, address the findings; changing unrelated code makes the loop diverge.
