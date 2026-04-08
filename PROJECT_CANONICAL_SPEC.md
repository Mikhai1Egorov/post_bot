# 📘 PROJECT_CANONICAL_SPEC — Structured Specification

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
Accept Excel → validate → reserve balance → generate content → render HTML → publish or deliver → complete
```

---

## 3. User Flow (Simplified)

```text id="uflow1"
Start → Language → Download Template → Upload Excel → Validation

→ if errors → fix → upload again
→ if no balance → purchase → upload again
→ if success → RESERVE → processing

→ mode:
   instant → auto publish → DONE
   approval → user action → DONE
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
* `search_language`
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
* `search_language` and `response_language` must use exactly 7 values: `en`, `ru`, `uk`, `es`, `zh`, `hi`, `ar`
* `include_image` accepts ONLY `TRUE` or `FALSE` in Excel (`1/0` is invalid)
* `schedule_at` canonical input is `YYYY-MM-DD HH:MM`; validator must also parse native Excel datetime serial values and normalize them before pipeline

---

## 5. Billing Model

### Core Formula

```text id="bill1"
PURCHASE → RESERVE → CONSUME
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
CREATED → QUEUED → PREPARING → RESEARCHING → GENERATING → RENDERING
```

### Branching

**instant:**

```text id="life2"
→ PUBLISHING → DONE
```

**approval:**

```text id="life3"
→ READY_FOR_APPROVAL → (publish → DONE | download → DONE)
```

### Final States

* `DONE`
* `FAILED`
* `CANCELLED`

### Retry Loop (Transient Failures)

* retryable external failures return task to `QUEUED`
* worker claim atomically moves `QUEUED -> PREPARING` before execution (duplicate-pick protection)
* max retry attempts: `3`
* after the retry limit task remains in `FAILED`
---

## 8. Mode Logic

### 🚀 instant

* no user interaction
* automatic publishing
* immediate completion

---

### ✋ approval

* batch creation
* user decision required:

```text id="mode1"
Publish OR Download
```

* both actions → `DONE`
* download → publication = `SKIPPED`

---

## 9. Database Principles

### Source of Truth

```text id="db1"
article_balance_ledger → balance truth
uploads/tasks → pipeline state
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
Published → DONE
```

### approval

```text id="done2"
Publish OR Download → DONE
```

---

## 14. Critical System Rules

* Excel = task contract
* DB = single source of state
* Ledger = single source of balance
* No RESERVE → no pipeline
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

External integrations are adapter-based and optional by configuration.

Runtime env keys:
* `RESEARCH_API_URL`
* `LLM_API_URL`
* `PUBLISHER_API_URL`
* `OUTBOUND_API_TOKEN`
* `OUTBOUND_TIMEOUT_SECONDS`

If an adapter URL is missing, runtime fails explicitly at call time (no silent fallback).

HTTP JSON contracts:

Research request:
* `topic`
* `keywords`
* `time_range`
* `search_language`

Research response:
* `sources`: list of objects with `source_url` (required), optional `source_title`, `source_language_code`, `published_at` (ISO), `source_payload_json`

LLM request:
* `model_name`
* `prompt`
* `response_language`

LLM response:
* `text` (non-empty)

Publish request:
* `channel`
* `html`
* `scheduled_for` (ISO or null)

Publish response:
* optional `external_message_id`
* optional `payload` (object)
