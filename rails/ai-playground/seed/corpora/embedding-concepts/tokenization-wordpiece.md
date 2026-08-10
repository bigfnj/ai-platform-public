# Subword splitting

Before text reaches a model it is chopped into subword units. The BERT family uses a greedy longest-match scheme over a fixed vocabulary, while newer models often use a byte-level or unigram scheme. Because the vocabulary and splitting rules differ, two model families produce different unit counts for the same sentence, which matters when you swap one encoder for another.
