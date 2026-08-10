"""
Phase 3 test — translate one slide and print the full script.
Run with: uv run python test_phase3.py
"""
from src.translation_service.pdf_reader import read_slides
from src.translation_service.classifier import classify_slides
from src.translation_service.translator import translate_slide
from src.translation_service.script_builder import build_script, format_script_preview

PDF = "Food Service Industry - Behind the Scenes.pdf"

slides = read_slides(PDF)
classified = classify_slides(slides)

content_slides = [s for s in classified if s["type"] == "content"]

# Use the first content slide (Host) as the test case
test_slide = content_slides[0]
print(f"Testing with slide: {test_slide['title']}")
print(f"  Bullets: {test_slide['bullets']}")
print(f"  Paragraphs: {test_slide['paragraphs']}")
print()

print("Translating via Qwen2.5 32B...")
translation = translate_slide(test_slide)

print("Translation result:")
print(f"  term_es:      {translation['term_es']}")
print(f"  sentences_es: {translation['sentences_es']}")
print()

script = build_script(test_slide, translation)
print(format_script_preview(test_slide, script))
