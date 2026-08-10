"""
Phase 4 test — generate audio for one slide (Host) and save to output/test/.
Run with: uv run python test_phase4.py
"""
from src.translation_service.pdf_reader import read_slides
from src.translation_service.classifier import classify_slides
from src.translation_service.translator import translate_slide
from src.translation_service.script_builder import build_script, format_script_preview
from src.translation_service.tts_engine import generate_slide_audio

PDF = "Food Service Industry - Behind the Scenes.pdf"

slides = read_slides(PDF)
classified = classify_slides(slides)
content_slides = [s for s in classified if s["type"] == "content"]

test_slide = content_slides[0]  # Host, Week 1

print(f"Slide: {test_slide['title']}")
translation = translate_slide(test_slide)
script = build_script(test_slide, translation)

print(format_script_preview(test_slide, script))
print()
print("Generating audio...")

generate_slide_audio(script, "output/test/slide_04_host.wav")
print("\nDone. Listen to output/test/slide_04_host.wav")
