# Splitting documents

Long files are broken into smaller passages before they are turned into vectors. Passages that are too large blur several ideas into one fuzzy point; passages that are too small lose the surrounding meaning. A little overlap between neighbors keeps sentences that straddle a boundary from being orphaned. The right passage length depends on the model's window and the density of the source material.
