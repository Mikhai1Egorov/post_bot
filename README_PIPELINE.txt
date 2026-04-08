# 👤 USER_FLOW — Structured Specification

## 1. Purpose

Defines the complete user interaction flow inside the Telegram bot.

This flow ensures:

* predictable user experience
* clear step-by-step progression
* no hidden states
* deterministic behavior from input to result

---

## 2. Entry Point

### Step 1 — Start

User enters the Telegram bot.

### Step 2 — Language Selection

User selects interface language.

### System Behavior:

* all UI elements (buttons, messages, instructions) switch to selected language
* language is fixed for the session

---

## 3. Instruction Phase

User sees button:

```text
📘 How to use the bot
```

### On Click:

User receives:

* Excel template (canonical)
* README / instructions (localized)

### Important Rules:

* multiple template variants may exist
* each Excel MUST match its corresponding README
* template = source of truth for task parameters

---

## 4. Task Upload Phase

User sees button:

```text
📤 Upload tasks
```

### User Actions:

* fills Excel file
* uploads file to bot

---

## 5. Validation Phase

### Case 1 — ✅ Valid File + Sufficient Balance

User receives:

```text
✅ File accepted
Tasks are taken into processing
```

System:

* performs `RESERVE`
* starts pipeline

---

### Case 2 — ❌ Valid File but Insufficient Balance

Pipeline does NOT start.

User receives:

```text
❌ Not enough articles in balance

Required: N
Available: M

Please purchase a package and upload again 👇
```

---

### Case 3 — ❌ Validation Errors

Pipeline does NOT start.

User receives structured error report:

```text
❌ File contains errors

Row 2:
• channel — empty

Row 5:
• mode — invalid value

Fix the file and upload again 👇
```

Button:

```text
🔄 Upload tasks
```

---

## 6. Processing Phase

After successful validation + reserve:

* tasks are processed asynchronously
* user may receive no intermediate updates

---

## 7. Mode-Based Behavior

System behavior depends on `mode`.

---

### 7.1 🚀 MODE = instant

User receives NO notifications.

System:

* generates content
* publishes automatically

Final state:

```text
DONE
```

---

### 7.2 ✋ MODE = approval

After processing:

User receives:

```text
✅ Materials are ready
```

Buttons:

```text
🚀 Publish
📦 Download archive
```

---

## 8. User Actions (Approval Mode)

### Option 1 — Publish

* all articles are published
* tasks marked as `DONE`

---

### Option 2 — Download Archive

* user receives ZIP with HTML files
* tasks marked as `DONE`
* publication is skipped

---

## 9. Completion Rules

Task is considered completed when:

```text
Publish clicked OR Download archive clicked
```

---

## 10. Restart Flow

After completion, user can:

```text
📤 Upload tasks
```

Flow restarts from upload phase.

---

## 11. UX Principles

The system guarantees:

* single linear flow
* no hidden states
* explicit error messages
* clear next-step actions
* language consistency
* minimal cognitive load

---

## 12. End-to-End Flow (Compact)

```text
Start
→ Language selection
→ Download template
→ Upload Excel
→ Validation

→ if error:
   fix → upload again

→ if no balance:
   purchase → upload again

→ if success:
   reserve → processing

→ mode:
   instant → auto publish → DONE
   approval → user action → DONE
```

---

## 13. Key Insight

```text
User flow is strictly linear and state-driven.
Each step either moves forward or returns explicit feedback.
```

---

## 14. One-Line Summary

The system provides a linear, transparent user journey from Excel upload to content delivery, with clear validation, billing, and mode-based execution.
