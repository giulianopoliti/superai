# AGENTS.md

## Project Context

Stock AI is an operational assistant for supermarkets in Argentina. The first channel will likely be WhatsApp through Kapso, but the product must be built as a channel-agnostic Assistant Core.

Authoritative planning document:

- `docs/PLAN_ACCION_TECNICO.md`

Codex collaboration guide:

- `docs/CODEX_AGENTES_Y_SKILLS.md`

## Architecture Rules

- Do not build "a WhatsApp bot"; build an Assistant Core with channel adapters.
- Keep Kapso/WhatsApp isolated in `channels/` or equivalent adapter code.
- The core must work with normalized `AssistantRequest` and `AssistantResponse` models.
- AI interprets intent and extracts structured data; backend services validate and execute actions.
- Do not let AI write directly to the database.
- Put business rules in deterministic services, not in prompts.
- Version prompts as files under `app/assistant/prompts/`.
- Use English for code, modules, DB tables, fields, intents, and APIs.
- Use Spanish for user-facing assistant replies, UI labels, and operational copy for Argentina.
- Preserve multi-tenant design from the beginning with `business_id`.

## Current Repo State

- There is no backend scaffold yet.
- There is no frontend scaffold yet.
- There are no install/build/test commands yet.
- Do not install dependencies until the base implementation plan is approved.
- Do not create migrations until the persistence approach is chosen.

## Recommended Project Skills

Use project-local skills from `.agents/skills/` when the task matches:

- `$stock-ai-architecture`: architecture decisions, sprint planning, core/adapters/providers boundaries.
- `$stock-ai-backend-core`: FastAPI core, schemas, assistant engine, intent router, endpoint design.
- `$stock-ai-data-model`: Postgres/Supabase schema, repositories, multi-tenant rules, vencimientos/expiration batches.
- `$stock-ai-prompts`: prompt files, structured outputs, confidence/clarification behavior, LLM provider boundaries.
- `$stock-ai-channel-adapters`: WhatsApp/Kapso, CLI, web adapters, webhook normalization.
- `$stock-ai-quality-review`: review for bugs, tests, regressions, coupling, missing validation.

## Subagent Coordination

- Spawn subagents only when the user explicitly asks for parallel agents or delegation.
- Keep the main agent responsible for architecture decisions and integration.
- Give each subagent a clear file ownership boundary.
- Do not assign two subagents to edit the same files in parallel.
- Prefer subagents for bounded research, independent modules, tests, and reviews.
- Ask workers to list changed files in their final response.
- Review and integrate worker output before continuing to the next sprint.

## OpenAI Documentation

Always use official OpenAI developer documentation when working with OpenAI APIs, Codex, skills, plugins, MCP, structured outputs, audio, vision, embeddings, or agents. If the OpenAI Docs MCP server is available, prefer it; otherwise use official OpenAI documentation pages.

## Do Not Do Yet

- Do not build a frontend or desktop app.
- Do not implement invoice/PDF/image extraction yet.
- Do not add pgvector before there is real content to retrieve.
- Do not add Cloudflare Durable Objects, Queues, or Workflows until the scheduler/provider abstraction proves the need.
- Do not couple reminders, vencimientos, providers, or prices to WhatsApp-specific payloads.
