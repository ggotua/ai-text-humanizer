"""
app.py — Streamlit web interface for the .docx humanization pipeline.

Calls the same already-tested engine functions used by humanize_docx.py
and the Langflow adapter directly — no Langflow runtime involved. Langflow
remains useful as a prompt-prototyping tool, but this interface doesn't
depend on a Langflow server being up.

Run with:
    streamlit run app.py
"""

import tempfile
from pathlib import Path

import streamlit as st

from src.detector.config_loader import load_config
from src.ooxml.extract import extract_docx_text
from src.ooxml.reassemble import reassemble_docx
from src.pipeline.iteration_loop import run_iteration_loop
from src.pipeline.single_pass import make_ollama_call_fn

st.set_page_config(page_title="AI Text Humanizer", page_icon="📝")
st.title("📝 AI Text Humanizer")
st.caption("Локальный, приватный гуманизатор текста с сохранением сносок")

with st.sidebar:
    st.header("Настройки")
    language = st.selectbox("Язык", ["ru", "en"], index=0)
    max_iterations = st.number_input("Макс. итераций", min_value=1, max_value=10, value=3)
    persona = st.text_input("Персона (опционально)", value="")
    model = st.text_input("Модель Ollama", value="mistral")
    config_dir = st.text_input("Папка конфига", value="config")

uploaded_file = st.file_uploader("Загрузите .docx файл", type=["docx"])

if uploaded_file is not None:
    if st.button("Гуманизировать", type="primary"):
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / uploaded_file.name
            input_path.write_bytes(uploaded_file.getvalue())
            output_path = Path(tmp_dir) / f"humanized_{uploaded_file.name}"

            progress = st.empty()
            try:
                progress.info("Шаг 1/3: извлечение текста и структуры сносок...")
                extracted = extract_docx_text(str(input_path))
                if extracted.warnings:
                    st.warning(
                        "Предупреждения извлечения:\n"
                        + "\n".join(f"- {w}" for w in extracted.warnings)
                    )
                st.write(f"Найдено сносок/концевых сносок: {len(extracted.references)}")

                progress.info(f"Шаг 2/3: гуманизация текста (до {max_iterations} итераций)...")
                config = load_config(config_dir, language)
                ollama_call = make_ollama_call_fn(model=model)
                with st.spinner("Модель работает, это может занять несколько минут..."):
                    result = run_iteration_loop(
                        text=extracted.extracted_text,
                        language=language,
                        ollama_call=ollama_call,
                        detector_config=config,
                        max_iterations=int(max_iterations),
                        persona=persona if persona else None,
                    )
                st.write(
                    f"Завершено итераций: {result.iterations_completed}, "
                    f"прошло пороги: {result.passed}"
                )
                if result.warning:
                    st.warning(result.warning)

                progress.info("Шаг 3/3: сборка .docx с сохранением сносок...")
                reassembly = reassemble_docx(
                    original_docx_path=str(input_path),
                    extraction_result=extracted,
                    final_text=result.final_text,
                    output_docx_path=str(output_path),
                )
                progress.empty()

                if reassembly.qa_passed:
                    st.success(
                        f"Готово! Сноски сохранены: "
                        f"{reassembly.output_reference_count}/{reassembly.original_reference_count}"
                    )
                else:
                    st.error(
                        "QA-проверка не прошла — проверьте документ вручную перед использованием:"
                    )
                    for w in reassembly.qa_warnings:
                        st.write(f"- {w}")

                st.download_button(
                    label="Скачать результат",
                    data=output_path.read_bytes(),
                    file_name=f"humanized_{uploaded_file.name}",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            except Exception as exc:
                progress.empty()
                st.error(f"Ошибка: {exc}")
