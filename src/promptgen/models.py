"""
Data models for prompt generation (SPEC-002 §2).

Two frozen dataclasses that carry structured output from the
prompt-building functions and the title-echo LLM-as-judge call.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RewriteFeedback:
    """Structured rewrite instructions derived from a DetectorReport.

    Produced by ``build_feedback_from_report()`` — the sole constructor.
    Only rules present in ``report.failed_rules`` generate instructions;
    a passing report produces an empty list (nothing to fix).

    Fields
    ------
    instructions:
        Ordered human-readable instructions, each naming the specific
        matched text or metric values found (never a generic restatement
        of the rule name).  Fed verbatim into the style-pass prompt so
        the LLM knows precisely what to fix.
    """

    instructions: list[str]


@dataclass(frozen=True)
class TitleEchoJudgment:
    """Structured judgment from the title-echo LLM-as-judge call.

    The judge asks the LLM a direct yes/no question about whether a
    subheading is an "echo" of the immediately preceding content.
    Rather than raising on ambiguous output, the judgment stores a
    ``parse_warning`` string — this lets the caller decide how to
    handle the ambiguity (e.g. log it, ignore it, ask a human) instead
    of crashing the pipeline (see SPEC-002 §2 for rationale).

    Fields
    ------
    is_echo:
        ``True`` if the response clearly parsed to "yes", ``False``
        otherwise (including ambiguous cases — the caller should check
        ``parse_warning`` to distinguish a clear "no" from a parse
        failure).
    raw_response:
        The unparsed model output, kept for debugging and logging.
        Includes the full text the LLM returned.
    parse_warning:
        ``None`` if the response parsed cleanly to a yes/no answer.
        Set to a descriptive string (e.g. ``"Expected 'yes' or 'no',
        got: ..."``) when the output did not clearly match the expected
        format, so the caller can handle the ambiguity gracefully.
    """

    is_echo: bool
    raw_response: str
    parse_warning: str | None