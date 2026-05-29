# Terra AI — Full Stack Engineer, Scientific Modeling Tools

**URL:** https://www.terraai.com/careers (LinkedIn, >100 applicants)
**Location:** Palo Alto, CA / Remote
**Stage:** Seed ($3.4M, 2023, Khosla Ventures + Rio Tinto); Series A likely closed mid-2025; scaling ~15 → ~30 by end of 2026

---

## Company Context

- **Platform:** 3-layer AI stack for subsurface geoscience: (1) diffusion-based 3D geological model generation, (2) surrogate simulation (100,000x faster than physics sim), (3) RL decision agent (MCTS + deep RL) for drill/abandon decisions
- **Clients:** Rio Tinto (investor + client), Ero Copper, Ramaco Resources
- **Results claimed:** 40% drilling time/cost reduction, 90% more accurate targeting under cover
- **Culture:** Stanford research origins (SISL); multidisciplinary by design; rigor-first (CTO = former exec director, Stanford Center for AI Safety)
- **Inflection point:** Shifting from R&D/prototyping → productization and scaling

## Key People

| Name | Role | Background |
|---|---|---|
| John Mern, PhD | CEO & Co-founder | Stanford SISL; intelligent decision-making for large-scale systems |
| Anthony Corso, PhD | CTO & Co-founder | Stanford CSAI exec director; safety-critical algorithmic decision-making |
| Markus Zechner, PhD | GM of Reservoirs | 20+ yrs across carbon storage, geothermal, EOR |
| William Davis, PhD | Senior Computational Scientist | UC Berkeley; Scripps geophysics fellow |
| Kyle Clark | Engineering Manager | Cloud architecture, Kubernetes, workload automation |

---

## JD Summary

**What they want:** Engineer who bridges product-quality software engineering with scientific computing. Take working scientific code and make it robust, maintainable, reproducible, and extensible.

**Core asks:**
- Refactoring and modularization of internal modeling stacks
- Testing strategies for scientific software (golden tests, invariants, property-based)
- Config management for reproducible/debuggable runs
- Orchestration pipelines for simulation ensembles and data validation
- APIs/interfaces that productize working examples
- Python (production-grade) + Julia (learn quickly)
- ML integration at artifact/IO level (PyTorch preferred; TF/JAX also relevant)
- Performance profiling and optimization

**Nice-to-haves:**
- Geophysics / SimPEG / survey simulation
- Reservoir simulation (Eclipse, JutulDarcy)
- PDE-based problems in HPC
- Fortran/C++
- Batch pipelines / data-intensive systems

---

## Strategic Positioning (Simon)

Resume narrative: "The engineer who makes scientific workflows production-grade" — not data scientist, not ML researcher. Productionized glacier LOO-CV pipeline, modular hex grid reused across 3 projects, config-driven UTF, simulation ensemble orchestration (sf_majick). Geoscience domain depth via DAS/DTS/geophysics MSc speaks to Markus Zechner's reservoirs team. Julia gap is real but soft per JD language.

Resume template: `data/resume_terra_ai.tex`
