# Third-party dependencies and boundaries

This repository's code is Apache-2.0. Runtime dependencies retain their own licenses.
Generate an exact lockfile/SBOM for a release and review upstream terms before shipping.

| Dependency / service | Role | Boundary |
|---|---|---|
| FastAPI / Pydantic / Uvicorn | local API | open-source runtime dependency |
| React / Vite / Framer Motion / Lucide | Web cockpit | open-source UI dependencies |
| MCP Python SDK | optional tool servers | protocol adapter; server enforces policy |
| AgentTeams / Matrix | collaboration plane | separately installed; no vendored secrets |
| Higress | optional gateway | separately deployed credential/routing boundary |
| Nacos 3.2+ | optional Skill registry | separately deployed version/review system |
| Alibaba Cloud official Skills | optional cloud operations | credentials and service cost external |
| PostgreSQL/PolarDB, MinIO/OSS | optional data/artifact profile | replaceable adapters |

No external model weights, private ego videos, checkpoints, or third-party datasets are
redistributed. Names such as RTX, Alibaba Cloud, Nacos, Higress, Matrix, and AgentTeams
belong to their respective owners.

