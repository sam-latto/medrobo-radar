## Problem
<!-- What user or product problem does this solve? Link the issue: "Closes #12" -->

## What changed
<!-- The change in plain language. Note any decision worth recording in docs/PRD_v3.md section 5. -->

## How it was tested
<!-- Automated tests (which ones, results), manual checks (screens, phone width), data checks. -->

## Risk and rollback
<!-- What could go wrong (privacy, cost, sources, sign-in)? How to undo it. -->

## Checklist
- [ ] Acceptance criteria in the linked issue are met
- [ ] Tests pass (`python -m pytest`; `RUN_DB_TESTS=1` for database changes; `npx tsc --noEmit` and `npx eslint .` in `web/`)
- [ ] Screens follow `docs/DESIGN.md` and work at 375px
- [ ] No secrets or personal data in the diff
- [ ] PRD updated if a product decision changed
