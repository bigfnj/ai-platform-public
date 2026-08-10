"""
Multi-slide translation test — spot-checks 5 content slides across weeks.
Run with: uv run python test_phase3_multi.py
"""
from src.translation_service.pdf_reader import read_slides
from src.translation_service.classifier import classify_slides
from src.translation_service.translator import translate_slide
from src.translation_service.script_builder import build_script, format_script_preview

PDF = "Food Service Industry - Behind the Scenes.pdf"

slides = read_slides(PDF)
classified = classify_slides(slides)

content_slides = [s for s in classified if s["type"] == "content"]

# Sample: first, last, and three spread across weeks
indices = [0, 9, 19, 29, len(content_slides) - 1]
samples = [content_slides[i] for i in indices]

for slide in samples:
    print(f"\nTranslating: [{slide['week']}] {slide['title']}")
    translation = translate_slide(slide)
    script = build_script(slide, translation)
    print(format_script_preview(slide, script))
    print()
