# Physical AI — Omniverse, Isaac, and Cosmos

Physical AI is NVIDIA's term for AI that perceives, reasons about, and acts in the real world, making
things that move — cars, trucks, factories, warehouses, and robots — autonomous and embodied. It is
built on two foundational libraries: Omniverse as the digital-twin operating system for simulation,
and Cosmos as the world foundation models for physical AI. Because collecting and labeling real-world
robot and sensor data is slow and expensive, physical AI relies heavily on simulation and synthetic
data to train, test, and validate systems before real-world deployment.

Omniverse provides prebuilt capabilities for building simulation-ready virtual worlds, including
OpenUSD interoperability, RTX rendering, physics, and sensor simulation for cameras, lidar, and radar.
OpenUSD (Universal Scene Description) is an open, extensible framework for describing, composing,
simulating, and collaborating in 3D worlds, invented by Pixar and now stewarded by the Alliance for
OpenUSD. It is the common scene-description language that unites CAD, simulation assets, and real-world
telemetry into one physically accurate view, which is what makes industrial digital twins possible.

The Isaac platform applies this to robotics. Isaac Sim is an open-source reference framework built on
Omniverse for robotics simulation, testing, and synthetic-data generation in physically based
environments. Isaac Lab is an open-source, GPU-accelerated framework built on Isaac Sim for robot
learning, running massively parallel reinforcement and imitation learning across many simulated
environments at once. Isaac GR00T is an open reference platform and foundation model for general-
purpose humanoid robots: a generalist sensorimotor policy that developers adapt through post-training
to a specific robot and task, and that runs on the Jetson Thor edge computer. GR00T has advanced
through successive open releases toward commercially viable real-world deployment.

Cosmos is a platform of generative world foundation models, tokenizers, guardrails, and a data
pipeline, purpose-built to accelerate physical AI by generating physics-aware synthetic video for
robots and autonomous vehicles. Its model families are Cosmos Predict (generate future world states
from text, image, video, or sensor input), Cosmos Transfer (photorealistic, physics-grounded
simulation and augmentation), and Cosmos Reason (spatiotemporal understanding and physical reasoning).
Cosmos world models generate photoreal, physics-based synthetic data to train and evaluate physical-AI
systems, adopted by robotics and autonomous-vehicle developers.

For a systems integrator, physical AI is the same enablement motion applied to embodied systems: help
a partner stand up a simulation-first workflow in Omniverse and Isaac, generate synthetic training
data with Cosmos, and deploy learned policies to edge robots, connecting real-world sensor and
telemetry pipelines to the models. It is where real-time, low-latency data meets accelerated AI.

## Sources (NVIDIA)
- Physical AI / digital twins: https://blogs.nvidia.com/blog/openusd-digital-twins-industrial-physical-ai/
- Omniverse: https://www.nvidia.com/en-us/omniverse/
- OpenUSD: https://www.nvidia.com/en-us/glossary/openusd/
- Isaac Sim: https://developer.nvidia.com/isaac/sim
- Isaac Lab: https://developer.nvidia.com/isaac/lab
- Isaac GR00T: https://developer.nvidia.com/isaac/gr00t
- Cosmos: https://www.nvidia.com/en-us/ai/cosmos/
