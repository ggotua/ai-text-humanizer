"""Langflow adapter for the single-pass text humanizer (SPEC-004 Р вЂ™Р’В§4).

This is a thin wrapper exposing the pure-Python engine
(``src.pipeline.single_pass.run_single_pass``) as a Langflow Component.
It is verified against the live running Langflow 1.11.1 UI Р Р†Р вЂљРІР‚Сњ the import
paths below are confirmed correct for the installed version and must not
be substituted.

This file is NOT expected to be testable via pytest Р Р†Р вЂљРІР‚Сњ it requires the
Langflow runtime to load.  The engine logic it wraps is fully unit-tested
in ``tests/test_single_pass.py``.
"""

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, DropdownInput, Output
from lfx.schema.data import Data

from src.pipeline.single_pass import run_single_pass, make_ollama_call_fn
from src.detector.config_loader import load_config


class HumanizerPipelineComponent(Component):
    display_name = "Text Humanizer (single pass)"
    description = "Runs one grammar+style rewrite pass using the local detector and Ollama."
    icon = "code"
    name = "HumanizerPipelineComponent"

    inputs = [
        MessageTextInput(name="input_text", display_name="Input Text"),
        DropdownInput(name="language", display_name="Language", options=["ru", "en"], value="ru"),
        MessageTextInput(name="persona", display_name="Persona (optional)", value=""),
        MessageTextInput(name="ollama_model", display_name="Ollama Model", value="mistral"),
    ]

    outputs = [
        Output(display_name="Humanized Text", name="output_text", method="build_output"),
    ]

    def build_output(self) -> Data:
        config = load_config("config", self.language)
        ollama_call = make_ollama_call_fn(model=self.ollama_model)
        persona = self.persona if self.persona else None
        result = run_single_pass(
            text=self.input_text,
            language=self.language,
            ollama_call=ollama_call,
            detector_config=config,
            persona=persona,
        )
        return Data(value=result.final_text)
