# Comparing vectors

When two pieces of text are turned into vectors, their closeness is usually judged by the angle between them rather than their raw magnitude. If every vector is scaled to unit length first, the dot product and the angle-based measure become identical, which is why pipelines normalize before indexing. Straight-line distance also works but is sensitive to length, so angle-based scoring is the default for semantic search.
