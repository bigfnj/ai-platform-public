# Approximate nearest-neighbor graphs

Scanning every stored vector for each request does not scale. A navigable small-world graph links each point to a handful of neighbors across several layers, so a search hops from a coarse entry point down to the closest matches in logarithmic time. It returns approximate rather than exact neighbors, trading a sliver of recall for enormous speed on millions of items.
