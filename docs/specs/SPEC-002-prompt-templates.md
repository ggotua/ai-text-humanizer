# SPEC-002: Prompt Templates & Title-Echo LLM-as-Judge

Feature:      Converts SPEC-001's DetectorReport into concrete rewrite
              prompts (two-stage temperature approach) and implements
              title-echo detection as an LLM-as-judge call
Priority:     P2 (depends on SPEC-001's report structure)
Status:       Planning
Dependencies: SPEC-001 (DetectorReport, RuleMatch, PLACEHOLDER_TOKEN_PATTERN)
Related Docs: APP-OVERVIEW.md §2.2, §2.6; SPEC-001 §8 (what this spec needs
              from it); SPEC-004 (consumes these prompt-building functions
              to actually wire calls into the Langflow flow)

---

## 1. Overview

Two responsibilities, kept in one spec because they're both "turn structured
input into an LLM prompt/call":

1. **Prompt construction** (pure functions, no Ollama needed to test):
   convert a `DetectorReport` into specific, concrete rewrite instructions
   — never generic "be more human" — and assemble the two-stage
   (grammar-pass / style-pass) prompts per APP-OVERVIEW §2.3.
2. **Title-echo LLM-as-judge** (requires a live LLM call): given a
   subheading and the text immediately following it, ask the LLM a direct
   yes/no question and parse the answer into a structured judgment.

**Acceptance criteria:**
- Feedback text generated from a `DetectorReport` names specific matched
  phrases/positions, not vague categories
- Style-pass prompt always includes the placeholder-token preservation
  rule verbatim, regardless of whether the input text actually contains
  tokens (safe default — no need to conditionally check)
- Style-pass prompt never asks the model to add an introduction or
  conclusion; explicitly forbids it
- Title-echo judge function is testable with a fake/stub LLM call for
  parsing logic, independent of whether Ollama is actually running
- All prompt-building functions work in RU and EN

---

## 2. Interface Definition

```python
from dataclasses import dataclass
from typing import Callable, Literal

@dataclass(frozen=True)
class RewriteFeedback:
    instructions: list[str]   # ordered, human-readable, specific — fed into style-pass prompt verbatim

@dataclass(frozen=True)
class TitleEchoJudgment:
    is_echo: bool
    raw_response: str          # unparsed model output, kept for debugging/logging
    parse_warning: str | None  # set if the response didn't clearly parse to yes/no

def build_feedback_from_report(report: "DetectorReport", language: str) -> RewriteFeedback:
    """
    Converts SPEC-001's DetectorReport into a list of specific rewrite
    instructions. Only rules present in report.failed_rules generate
    instructions — a passing report produces an empty instructions list
    (nothing to fix). Each instruction names the actual matched text/
    values found, not a generic restatement of the rule name.
    """

def build_grammar_pass_prompt(text: str, language: str) -> str:
    """
    Stage 1 (low temperature ~0.3) prompt: fix grammar, logical flow,
    factual consistency ONLY. No stylistic instructions. Must include
    the placeholder-token preservation rule (see section 3).
    """

def build_style_pass_prompt(
    text: str,
    feedback: RewriteFeedback,
    language: str,
    persona: str | None = None,
) -> str:
    """
    Stage 2 (high temperature ~0.7-0.85) prompt: stylistic rewrite
    incorporating feedback.instructions verbatim, the placeholder-token
    rule, the meta-commentary prohibition, and an optional persona
    instruction if provided (per the temperature-staging technique —
    persona forces lower-probability vocabulary without losing coherence).
    """

def build_title_echo_prompt(heading: str, following_text: str, language: str) -> str:
    """
    Constructs a direct yes/no judge prompt: does following_text restate
    the heading rather than add data/example/narrative? Instructs the
    model to answer with ONLY "ДА"/"НЕТ" (ru) or "YES"/"NO" (en) as the
    first word of its response, to make parsing reliable.
    """

def judge_title_echo(
    heading: str,
    following_text: str,
    language: str,
    llm_call: Callable[[str], str],
) -> TitleEchoJudgment:
    """
    Builds the title-echo prompt, invokes llm_call(prompt) -> raw text
    response (llm_call is an injected dependency — a real Ollama-calling
    function in production, a fake/stub in unit tests), and parses the
    response. Parsing looks for "ДА"/"YES" or "НЕТ"/"NO" as the first
    word (case-insensitive, whitespace-stripped). If neither is found,
    returns is_echo=False with parse_warning set to a description of
    what was received instead — never raises on an unparseable response.
    """
```

---

## 3. Placeholder-Token Preservation Rule (embedded in every rewrite prompt)

Both `build_grammar_pass_prompt` and `build_style_pass_prompt` must include
this instruction verbatim (RU/EN versions), regardless of whether the
input text is known to contain tokens:

> RU: "Текст может содержать служебные метки вида [[FN:3]] или [[EN:7]] —
> это привязки к сноскам/концевым сноскам. Эти метки НЕЛЬЗЯ удалять,
> дублировать или терять. Каждая метка, присутствующая во входном тексте,
> должна присутствовать в вашем ответе ровно один раз — можно менять её
> положение внутри предложения, но не количество."
>
> EN: "The text may contain marker tokens like [[FN:3]] or [[EN:7]] —
> these anchor footnotes/endnotes. These tokens must NEVER be deleted,
> duplicated, or lost. Each token present in the input must appear in
> your output exactly once — you may reposition it within a sentence,
> but never change its count."

**Why always include it, even for token-free input:** simpler and safer
than conditionally checking — a missed conditional check is a class of bug
this avoids entirely. Cost is a few extra tokens per prompt call.

---

## 4. Feedback Construction — Concrete, Not Generic

Per rule category in `DetectorReport.failed_rules`, `build_feedback_from_report`
produces instructions following this pattern (RU shown, EN mirrors it):

- **cliche_blacklist:** "Обнаружены штампы: «важно отметить» (позиция 45),
  «играет ключевую роль» (позиция 210). Перефразируйте эти места, убрав
  штампы, не меняя смысл."
- **hedge_blacklist:** similar, naming the specific matched hedge phrases
- **rhythm_monotony:** "Предложения слишком однородны по длине (среднее:
  18 слов, разброс: 2.1). Чередуйте: после длинного сложного предложения
  ставьте короткое, до 5 слов."
- **lexical_diversity:** "Словарь повторяется (distinct-2: 0.31, порог:
  0.4). Используйте более разнообразную лексику, избегайте повтора одних
  и тех же слов в соседних предложениях."
- **parallelism:** "Обнаружен повторяющийся синтаксический паттерн «X, Y и
  Z»: [примеры из diversity.parallelism_matches]. Разбейте на отдельные
  предложения или буллеты."
- **meta_commentary_opening / meta_commentary_closing** (informational,
  always included regardless of pass/fail per SPEC-001 §6's note that
  these don't affect `passed`): "Начало/конец текста содержит шаблонную
  фразу «давайте разберём». Уберите её — не добавляйте вступление или
  заключение, если это не запрошено."

If `report.passed` is True and there are no meta-commentary matches either,
`instructions` is an empty list — `build_style_pass_prompt` should still
produce a valid prompt (just without a feedback section), not a broken one.

---

## 5. Title-Echo Prompt Design

The prompt asks a single direct question and requests a single-word
answer as the first token of the response, to keep parsing simple and
robust against a 7B model's tendency to add extra commentary:

> RU template: "Заголовок: «{heading}»\nСледующий текст: «{following_text}»\n
> Вопрос: пересказывает ли следующий текст заголовок другими словами, не
> добавляя данных, примеров или нарратива? Ответьте одним словом: ДА или
> НЕТ. Затем, если хотите, кратко поясните."
>
> EN template: mirrors this in English.

`following_text` should exclude any placeholder tokens before being
inserted into the prompt (strip via SPEC-001's `strip_placeholder_tokens`)
— the judge doesn't need to see `[[FN:n]]` tokens, they're irrelevant to
whether text echoes a heading and could confuse a small model's judgment.

---

## 6. Error Handling Requirements

- `judge_title_echo` never raises due to an unparseable LLM response —
  degrades to `is_echo=False` with `parse_warning` set (a false negative
  here just means one echo goes uncaught, not a broken pipeline).
- `judge_title_echo` does NOT catch exceptions raised BY `llm_call` itself
  (e.g. a connection error to Ollama) — that's the caller's (SPEC-004's)
  responsibility, since retry/timeout policy belongs to the orchestration
  layer, not this spec.
- `build_feedback_from_report` must not raise on a report with an empty
  `failed_rules` — returns `RewriteFeedback(instructions=[])`, a valid value.

---

## 7. Testing Requirements

For prompt-building functions (no Ollama needed):
```
test_build_feedback_empty_report_returns_empty_instructions()
test_build_feedback_cliche_matches_named_specifically()
test_build_feedback_rhythm_monotony_includes_actual_stats()
test_build_feedback_parallelism_includes_matched_examples()
test_build_feedback_meta_commentary_included_even_when_passed_true()
test_grammar_pass_prompt_includes_placeholder_rule_always()
test_grammar_pass_prompt_no_stylistic_instructions()
test_style_pass_prompt_includes_placeholder_rule_always()
test_style_pass_prompt_forbids_intro_and_conclusion()
test_style_pass_prompt_includes_feedback_instructions_verbatim()
test_style_pass_prompt_with_persona_includes_persona_text()
test_style_pass_prompt_without_persona_omits_persona_section()
test_title_echo_prompt_strips_placeholder_tokens_from_following_text()
```

For `judge_title_echo` (fake `llm_call`, no real Ollama needed):
```
test_judge_title_echo_parses_da_as_true()
test_judge_title_echo_parses_net_as_false()
test_judge_title_echo_parses_yes_as_true_english()
test_judge_title_echo_unparseable_response_returns_false_with_warning()
test_judge_title_echo_case_insensitive_parsing()
```

**Separate integration test** (marked `@pytest.mark.integration`, skipped
by default, run manually with `pytest -m integration` when Ollama is
running with `mistral` pulled):
```
test_judge_title_echo_against_real_ollama_obvious_echo_case()
```
This one real-model test exists to catch the case where the prompt design
itself doesn't work well against actual Mistral output — the fake-based
unit tests only prove the parsing logic is correct, not that Mistral
reliably answers in the expected format.

---

## 8. What SPEC-004 Depends On From This Spec

- `build_grammar_pass_prompt` / `build_style_pass_prompt` — SPEC-004 wires
  these into the actual Ollama component calls with the two temperature
  settings
- `judge_title_echo` with a REAL `llm_call` implementation (a thin Ollama
  HTTP wrapper) — SPEC-004 provides that real implementation; this spec
  only requires the function accept any callable matching the signature
- `RewriteFeedback` structure — SPEC-004's iteration loop (SPEC-005) will
  call `build_feedback_from_report` again each iteration with the latest
  `DetectorReport`, and needs to know the shape doesn't change between calls
