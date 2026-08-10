# One card, many models

A single graphics card has a fixed pool of memory, so loading several large models at once will not fit. A broker serializes access, keeping one heavy generative model resident at a time and evicting it when another is needed, while a small always-on encoder co-resides. This lets many features share one card without exhausting its memory.
