# Serverless CPU inference

An exported graph can run through a portable runtime directly on the processor, with the model file sitting next to the application and no daemon, driver, or network hop involved. This is ideal for a small tool that must stay self-contained and offline, and pairs well with eight-bit weights to keep the file small and the latency low.
