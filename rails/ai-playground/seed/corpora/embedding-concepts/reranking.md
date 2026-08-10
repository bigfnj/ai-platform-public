# Second-stage scoring

A first retrieval pass over an index is fast but coarse: it scores each candidate independently. A heavier model can then read the request and each candidate together and re-order the shortlist, catching subtle relevance the first pass missed. This two-stage design keeps latency low by only applying the expensive joint model to the top handful of results rather than the whole collection.
