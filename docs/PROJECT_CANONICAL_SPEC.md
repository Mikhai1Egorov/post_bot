# Ã°Å¸â€œËœ PROJECT_CANONICAL_SPEC Ã¢â‚¬â€ Structured Specification

## 1. Purpose

Defines the full system architecture, behavior, and rules for processing Excel-based content generation tasks via Telegram bot.

The system performs:

* Excel intake
* validation
* billing (`RESERVE`)
* content generation via ChatGPT
* HTML rendering
* delivery via:

  * auto publishing (`instant`)
  * user action (`approval`)

---

## 2. Core System Goal

```text id="goal1"
Accept Excel Ã¢â€ â€™ validate Ã¢â€ â€™ reserve balance Ã¢â€ â€™ generate content Ã¢â€ â€™ render HTML Ã¢â€ â€™ publish or deliver Ã¢â€ â€™ complete
```

---

## 3. User Flow (Simplified)

```text id="uflow1"
Start -> Download Template -> Upload Excel -> Validation

Ã¢â€ â€™ if errors Ã¢â€ â€™ fix Ã¢â€ â€™ upload again
Ã¢â€ â€™ if no balance Ã¢â€ â€™ purchase Ã¢â€ â€™ upload again
Ã¢â€ â€™ if success Ã¢â€ â€™ RESERVE Ã¢â€ â€™ processing

Ã¢â€ â€™ mode:
   instant Ã¢â€ â€™ auto publish Ã¢â€ â€™ DONE
   approval Ã¢â€ â€™ user action Ã¢â€ â€™ DONE
```

### UX Principles

* linear flow
* no hidden states
* explicit errors
* clear next actions

---

## 4. Excel Contract

Excel is the **single source of task configuration**.

### Required Fields

* `channel`
* `topic`
* `keywords`
* `time_range`
* `response_language`
* `mode`

### Optional Fields

* `title`
* `style`
* `length`
* `include_image`
* `footer_text`
* `footer_link`
* `schedule_at`

### Constraints

* strict enum validation
* no undefined parameters allowed
* invalid values rejected before pipeline
* language enums are defined in `CONSTANTS_REGISTRY.md`
* `response_language` must use exactly 7 values: `en`, `ru`, `uk`, `es`, `zh`, `hi`, `ar`
* `include_image` accepts ONLY `TRUE` or `FALSE` in Excel (`1/0` is invalid)
* `schedule_at` canonical input is `YYYY-MM-DD HH:MM`; validator must also parse native Excel datetime serial values and normalize them before pipeline
* if `schedule_at` is set in the future, the task must wait in DB and cannot be claimed by a worker until due time
* if `schedule_at` is in the past, validation must fail with row-level error and pipeline must not start for that row

---

## 5. Billing Model

### Core Formula

```text id="bill1"
PURCHASE Ã¢â€ â€™ RESERVE Ã¢â€ â€™ CONSUME
```

### Rules

* validation does NOT charge
* RESERVE only after valid Excel + sufficient balance
* pipeline cannot start without RESERVE
* CONSUME marks processing start
* RELEASE returns reserved articles if cancelled early

---

## 6. Pipeline Architecture

### Stages

```text id="pipe1"
1. Upload intake
2. Validation
3. Billing check (RESERVE)
4. Task creation
5. Orchestrator
6. Preparation
7. Research
8. Prompt Resolver
9. Generation (ChatGPT)
10. Post-processing (HTML)
11. Publish / Approval
```

---

## 7. Task Lifecycle

### Core Flow

```text id="life1"
CREATED Ã¢â€ â€™ QUEUED Ã¢â€ â€™ PREPARING Ã¢â€ â€™ RESEARCHING Ã¢â€ â€™ GENERATING Ã¢â€ â€™ RENDERING
```

### Branching

**instant:**

```text id="life2"
Ã¢â€ â€™ PUBLISHING Ã¢â€ â€™ DONE
```

**approval:**

```text id="life3"
Ã¢â€ â€™ READY_FOR_APPROVAL Ã¢â€ â€™ (publish Ã¢â€ â€™ DONE | download Ã¢â€ â€™ DONE)
```

### Final States

* `DONE`
* `FAILED`
* `CANCELLED`

### Retry Loop (Transient Failures)

* retryable external failures return task to `QUEUED`
* worker claim atomically moves `QUEUED -> PREPARING` before execution (duplicate-pick protection)
* worker claim is schedule-aware: tasks with future `scheduled_publish_at` are not claimable yet
* max retry attempts: `3`
* after the retry limit task remains in `FAILED`
---

## 8. Mode Logic

### Ã°Å¸Å¡â‚¬ instant

* no user interaction
* automatic publishing when task execution starts
* if `schedule_at` is in the future, execution waits until due time and then publishes

---

### Ã¢Å“â€¹ approval

* batch creation
* user decision required:

```text id="mode1"
Publish OR Download
```

* both actions Ã¢â€ â€™ `DONE`
* download Ã¢â€ â€™ publication = `SKIPPED`

---

## 9. Database Principles

### Source of Truth

```text id="db1"
article_balance_ledger Ã¢â€ â€™ balance truth
uploads/tasks Ã¢â€ â€™ pipeline state
```

### Aggregates

* `user_article_balances` = cached state

### Key Entities

* users
* uploads
* tasks
* ledger
* artifacts
* publications
* batches

---

## 10. Prompt System

### Formula

```text id="prompt1"
FINAL PROMPT =
SYSTEM_INSTRUCTIONS
+ STYLE_TEMPLATE (from PROMPT_TEMPLATE_REGISTRY.txt)
+ MASTER_PROMPT_TEMPLATE
+ TASK_DATA
+ CONTENT_LENGTH_RULES
+ OPTIONAL_BLOCKS (from LENGTH-BLOCKS.txt)
```

### Supported Styles

* `journalistic`
* `simple`
* `expert`

Resolved to concrete style templates:

* `JOURNALIST_PROMPT_STYLE.txt`
* `SIMPLE_PROMPT_STYLE.txt`
* `EXPERT_PROMPT_STYLE.txt`

### Rules

* tone derived ONLY from style
* no extra parameters allowed
* strict language enforcement

---

## 11. Content Rules

### Length

* `short`
* `medium`
* `long`

### Structure

* title required
* subheadings required
* paragraphs required

### HTML

* tags: `h1`, `h2`, `h3`, `p`, `ul`, `li`
* unified template
* conditional blocks:

  * image
  * footer
  * schedule

---

## 12. Output Artifacts

Per task:

* generation record (`raw_output_text`)
* render result (`body_html`, `preview_text`)
* artifacts:

  * HTML
  * PREVIEW

Per batch:

* ZIP archive

Publication:

* publication record (if applicable)

---

## 13. Definition of Done

### instant

```text id="done1"
Published Ã¢â€ â€™ DONE
```

### approval

```text id="done2"
Publish OR Download Ã¢â€ â€™ DONE
```

---

## 14. Critical System Rules

* Excel = task contract
* DB = single source of state
* Ledger = single source of balance
* No RESERVE Ã¢â€ â€™ no pipeline
* All statuses stored in DB
* Errors are always visible to user
* Preparation cannot invent parameters

---

## 15. Cleanup

### Background Jobs

* remove temp files
* remove old ZIP archives
* clean intermediate artifacts
* stale recovery marks FAILED only for explicitly identified stale task_ids
* Bulk status-only recovery is disabled by default (fail-safe)

### Must NOT delete

* final outputs
* ledger records
* publication history

---

## 16. Core System Idea

```text id="core1"
Excel = contract
DB = state
Pipeline = execution
Prompt system = generation
HTML/artifacts = output
```

---

## 17. One-Line Summary

The system processes Excel-defined tasks through a validated, ledger-based, state-driven pipeline to generate and deliver structured content via automated or user-controlled flows.


## 18. Runtime Adapter Contracts

Runtime uses a single GPT provider for both research and generation.

Runtime env keys:
* `DB_HOST`
* `DB_PORT`
* `DB_NAME`
* `DB_USER`
* `DB_PASSWORD`
* `OPENAI_API_KEY`
* `OPENAI_RESEARCH_MODEL`
* `OUTBOUND_TIMEOUT_SECONDS`

Behavior:
* Research stage and Generation stage both use the same GPT token.
* No separate `RESEARCH_API_URL` / `LLM_API_URL` / `PUBLISHER_API_URL` adapters are required.
* Publish stage posts directly to Telegram channel/chat in `instant` mode when `TELEGRAM_BOT_TOKEN` is configured; approval mode uses ZIP + user action flow.
* Missing GPT token causes explicit startup/runtime failure (no silent fallback).








