---
name: stock-ai-quality-review
description: Quality review workflow for Stock AI. Use when reviewing code, docs, architecture, tests, PRs, or subagent output for bugs, regressions, missing tests, WhatsApp-core coupling, prompt/business logic leakage, multi-tenant data leakage, unsafe AI actions, scheduler/idempotency risks, or incomplete verification.
---

# Stock AI Quality Review

## Overview

Use this skill for review-first passes. Findings are the main output: bugs, risks, regressions, missing tests, and architecture violations.

## Review Priorities

1. Channel coupling: WhatsApp/Kapso must not leak into Assistant Core.
2. AI safety: LLM must not execute DB writes or critical actions directly.
3. Business logic: deterministic rules must live in services.
4. Multi-tenancy: every business query must be scoped by `business_id`.
5. Confirmation: low-confidence or critical saves must require confirmation.
6. Tests: core, adapters, services, and repositories need isolated tests.
7. Scheduler: jobs must be idempotent and timezone-aware.

## Output Format

Return findings first, ordered by severity.

For each finding include:

- severity
- file/line when available
- issue
- impact
- suggested fix

If no findings are found, say so explicitly and list residual risks or missing verification.

## Red Flags

- `kapso` imported inside `app/assistant/`.
- LLM provider imported inside business modules.
- Prompts containing margin formulas or supplier rules.
- Queries without `business_id`.
- Tests that require OpenAI, Kapso, or network access by default.
- Expiration records that overwrite older quantities with different dates.
