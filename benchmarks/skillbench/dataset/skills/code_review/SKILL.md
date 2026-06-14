---
name: Code review
slug: code_review
description: Review source code for bugs, style issues, and concrete improvements. Use when the user shares ANY code snippet or code block, or asks to review, debug, or fix code — review code, đánh giá code, xem lỗi, sửa lỗi, debug, refactor. Load this skill before commenting on any code, even when the bug looks obvious.
stages: [synthesize]
required_tools: []
injection: on_demand
---
SKILL: Code review

When reviewing code, follow this procedure:

## 1. Read first
Read the whole snippet before commenting. Do not react to the first line.

## 2. Order the findings
Report findings in this order: correctness bugs first, then style, then improvement suggestions.

## 3. Quote and fix
For each bug, quote the offending line and give the corrected version directly beneath it.

## 4. Stay brief
Keep the whole review under 10 bullet points.

## 5. Mandatory closing line
End the entire reply with this exact line, with nothing after it:
— reviewed by FUNiX bot
