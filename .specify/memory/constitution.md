<!--
Sync Impact Report
- Version change: 1.0.0 → 1.1.0
- Modified principles:
  - Modularity and Clear Boundaries → Modularity, Clear Boundaries, and Message-Passing
- Added sections:
  - Async & Event-Driven Discipline
- Removed sections: None
- Templates requiring updates:
  - ✅ .specify/templates/plan-template.md
  - ✅ .specify/templates/tasks-template.md
  - ✅ .specify/templates/spec-template.md (no change needed)
  - ✅ .specify/templates/checklist-template.md (no change needed)
  - ✅ .specify/templates/agent-file-template.md (no change needed)
  - ⚠ .specify/templates/commands/*.md (folder not present in this repo)
- Follow-up TODOs:
  - TODO(RATIFICATION_DATE): Original adoption date is unknown; set this once confirmed.
-->

# Prediction Trading Framework Constitution

## Core Principles

### Readability First
We optimize for humans reading code.

- Prefer clear names over short names.
- Prefer straightforward control flow over cleverness.
- Keep functions small and single-purpose.
- Avoid deep nesting and dense one-liners when they reduce comprehension.
- When in doubt, choose the option a new contributor will understand fastest.

### Simplicity (YAGNI by Default)
We build the smallest thing that works, then iterate.

- Implement only what is required for the current task and scope.
- Avoid premature abstractions; refactor after patterns are proven.
- If complexity is necessary, document why a simpler approach was rejected.

### Comments Explain “Why”, Not “What”
We write lots of comments, but only the useful kind.

- Add comments for: intent, trade-offs, invariants, edge cases, and non-obvious domain rules.
- Public functions/classes/modules MUST have docstrings (or equivalent) describing purpose and
  constraints.
- Do not restate the code. If a comment only repeats what the code obviously does, delete it.
- Keep comments current; outdated comments are worse than no comments.

### Follow Existing Code Style
Consistency beats personal preference.

- Match existing naming, layout, patterns, and error handling in the surrounding code.
- Use existing utilities/helpers instead of re-inventing similar ones.
- If the codebase lacks a convention for a new area, introduce the smallest consistent convention
  and apply it locally (do not reformat unrelated code).

### Modularity, Clear Boundaries, and Message-Passing
Design code so it can change without collateral damage, especially across async boundaries.

- Separate concerns (I/O at the edges; pure logic in the middle where possible).
- Keep modules focused; avoid “god” files and cross-cutting tangles.
- Prefer explicit inputs/outputs over hidden globals.
- Avoid circular dependencies; if boundaries are unclear, define an interface and depend on that.
- Prefer decoupling via **commands/events** over calling across layers directly when that reduces
  coupling and makes async behavior easier to reason about.

### Async & Event-Driven Discipline
Async code adds failure modes; we treat concurrency, queues, and events as first-class design
constraints.

- **No blocking in the event loop**: any blocking I/O MUST be isolated at the edges (thread/process
  offload or a dedicated sync boundary).
- **Single-writer state**: stateful components MUST have a clear owner; other components interact
  via messages (commands/events) rather than shared mutable state.
- **Event/command contracts are APIs**: message payloads MUST be versionable, documented, and stable
  (prefer small, normalized models; ignore unknown fields when practical).

## Code Quality Standards

- Keep changes small and reviewable.
- Prefer explicitness over magic.
- Favor stable, boring dependencies; add new dependencies only when they clearly reduce
  complexity and maintenance.
- Error messages MUST be actionable (what failed, why, and what to do next).

## Workflow & Review Gates

- Every change MUST pass a “Constitution Check” in review:
  - Is it readable?
  - Is it simple (or is added complexity justified)?
  - Are the important “why” comments present and correct?
  - Does it follow existing style?
  - Are boundaries modular and clear?
  - If async/event-driven: are message contracts explicit, and are backpressure/retry/cancellation
    handled intentionally?
- Reviewers are expected to request simplification/refactoring when code is correct but hard to
  understand.
- Prefer incremental delivery: merge value in small steps rather than large rewrites.

## Governance

- This constitution is the source of truth for how we build and review changes.
- Amendments:
  - Proposed as a PR editing `.specify/memory/constitution.md`
  - Must include an updated Sync Impact Report at the top of the file
  - Must update any dependent templates that reference constitution rules
- Versioning:
  - MAJOR: breaking change to governance or removal/redefinition of a principle
  - MINOR: new principle or materially expanded guidance
  - PATCH: clarification/wording-only improvements
- Compliance:
  - Every PR review MUST check for constitution compliance.
  - If a PR intentionally violates a principle, it MUST justify the exception and explain why it
    is still the best trade-off.

**Version**: 1.1.0 | **Ratified**: TODO(RATIFICATION_DATE): set original adoption date | **Last Amended**: 2026-02-03
