# Instruction prefixes

Some retrieval models are trained expecting a short instruction glued to the front of a search phrase, marking it as a lookup rather than a passage. Adding that lead-in can sharpen results for models trained with it and can hurt models that were not, so the right prompting is model-specific and worth testing rather than assuming. Passages are usually embedded plainly, without the lead-in.
