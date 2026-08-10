"""
humanize_docx.py — CLI entry point for the full .docx humanization pipeline.

Wires together, in order: SPEC-003's extract_docx_text, SPEC-005's
run_iteration_loop, and SPEC-006's reassemble_docx. This is not new
logic — every function called here is already tested independently
(tests/test_ooxml_extract.py, tests/test_iteration_loop.py,
tests/test_reassemble.py). This script exists purely to expose that
already-verified engine as something usable from the command line,
without needing Langflow open.

Usage:
    python humanize_docx.py --input draft.docx --output draft_humanized.docx
    python humanize_docx.py --input draft.docx --output out.docx --language ru --max-iterations 3
    python humanize_docx.py --input draft.docx --output out.docx --persona "опытный редактор"
"""

import argparse
import sys
from pathlib import Path

from src.ooxml.extract import extract_docx_text
from src.ooxml.reassemble import reassemble_docx
from src.pipeline.iteration_loop import run_iteration_loop
from src.pipeline.single_pass import make_ollama_call_fn
from src.detector.config_loader import load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Humanize a .docx draft while preserving footnotes/endnotes."
    )
    parser.add_argument("--input", required=True, help="Path to the input .docx file")
    parser.add_argument("--output", required=True, help="Path to write the humanized .docx file")
    parser.add_argument("--language", default="ru", choices=["ru", "en"], help="Language (default: ru)")
    parser.add_argument("--max-iterations", type=int, default=3, help="Max rewrite iterations (default: 3)")
    parser.add_argument("--persona", default=None, help="Optional style persona for the rewrite pass")
    parser.add_argument("--model", default="mistral", help="Ollama model name (default: mistral)")
    parser.add_argument("--config-dir", default="config", help="Path to blacklist/threshold config (default: config)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        return 1

    print(f"[1/3] Extracting text and footnote structure from {input_path.name}...")
    try:
        extracted = extract_docx_text(str(input_path))
    except Exception as exc:
        print(f"ERROR during extraction: {exc}", file=sys.stderr)
        return 1

    if extracted.warnings:
        print("  Extraction warnings (review before proceeding):")
        for w in extracted.warnings:
            print(f"    - {w}")

    print(
        f"  Extracted {len(extracted.extracted_text)} characters, "
        f"{len(extracted.references)} footnote/endnote reference(s) found."
    )

    print(f"[2/3] Running humanization loop (max {args.max_iterations} iteration(s))...")
    try:
        config = load_config(args.config_dir, args.language)
        ollama_call = make_ollama_call_fn(model=args.model)
        result = run_iteration_loop(
            text=extracted.extracted_text,
            language=args.language,
            ollama_call=ollama_call,
            detector_config=config,
            max_iterations=args.max_iterations,
            persona=args.persona,
        )
    except Exception as exc:
        print(f"ERROR during humanization: {exc}", file=sys.stderr)
        return 1

    print(f"  Completed {result.iterations_completed} iteration(s). Passed thresholds: {result.passed}")
    if result.warning:
        print(f"  WARNING: {result.warning}")

    print(f"[3/3] Reassembling .docx with footnotes intact -> {args.output}...")
    try:
        reassembly = reassemble_docx(
            original_docx_path=str(input_path),
            extraction_result=extracted,
            final_text=result.final_text,
            output_docx_path=args.output,
        )
    except Exception as exc:
        print(f"ERROR during reassembly: {exc}", file=sys.stderr)
        return 1

    print(f"  Footnote/endnote count — original: {reassembly.original_reference_count}, "
          f"output: {reassembly.output_reference_count}")
    print(f"  QA check passed: {reassembly.qa_passed}")
    if reassembly.qa_warnings:
        print("  QA warnings (review before sending this document anywhere):")
        for w in reassembly.qa_warnings:
            print(f"    - {w}")

    print(f"\nDone. Output written to: {args.output}")
    if not reassembly.qa_passed or result.warning:
        print("NOTE: warnings were reported above — review the output before relying on it.")
        return 2  # non-zero exit code signals "succeeded but needs review", distinct from hard failure

    return 0


if __name__ == "__main__":
    sys.exit(main())
