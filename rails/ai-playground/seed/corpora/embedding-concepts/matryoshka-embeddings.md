# Matryoshka representation learning

Matryoshka representation learning trains a model so the most important information is packed into the leading coordinates of every vector. Because of that front-loading, you can keep just the first 256 or 128 numbers of a 768-length output and still retrieve well, trading a little accuracy for a much smaller footprint and faster comparison. No re-training or separate model is required; you simply slice the output and renormalize before storing it in the index.
