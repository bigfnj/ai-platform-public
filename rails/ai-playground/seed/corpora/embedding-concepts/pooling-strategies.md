# From tokens to one vector

An encoder emits one vector per unit, but retrieval needs a single vector for the whole passage. Two common ways to collapse them are taking the special leading marker's vector, or averaging every unit's vector while ignoring padding. The choice is baked into how a model was trained, so reproducing a model's results means matching its collapsing recipe exactly.
