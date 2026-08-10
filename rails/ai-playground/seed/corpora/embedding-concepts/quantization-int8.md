# Shrinking model weights

A model's parameters are stored at full precision by default, which is large and slow on a processor. Mapping those weights to eight-bit integers cuts the file to roughly a quarter of its size and speeds up inference, with only a small quality cost. This makes a mid-sized encoder practical to ship as a single file that runs without a graphics card.
