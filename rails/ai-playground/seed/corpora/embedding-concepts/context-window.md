# Sequence length limits

Every encoder has a maximum number of units it can read at once. Anything past that ceiling is silently cut off, so the tail of a long passage never influences its vector. Older encoders stop at 512 units; newer ones stretch to a few thousand, which lets a whole page be represented without truncation. Knowing the ceiling tells you how aggressively to split your documents.
