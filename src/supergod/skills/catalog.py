"""Curated skill catalog sourced from external agent libraries."""

from __future__ import annotations

from pathlib import Path


SKILLS_DIR = Path(__file__).resolve().parent
LIBRARY_DIR = SKILLS_DIR / "library"
INDEX_PATH = LIBRARY_DIR / "index.json"
AGENTS_DIR = LIBRARY_DIR / "agents"


PACK_DEFINITIONS: dict[str, dict] = {
    "core-dev": {
        "description": "General product engineering execution patterns.",
        "keywords": [
            "feature",
            "implement",
            "backend",
            "frontend",
            "api",
            "endpoint",
            "refactor",
            "test",
            "requirements",
            "prd",
        ],
        "skills": [
            "prd-analyzer",
            "dependency-resolver",
            "feature-implementer",
            "backend-developer",
            "frontend-developer",
            "refactor-specialist",
            "test-generator",
            "tdd-implementer",
            "integration-test-runner",
            "docs-researcher",
        ],
    },
    "review-qa": {
        "description": "Quality, security, architecture, and contract review flows.",
        "keywords": [
            "review",
            "security",
            "performance",
            "quality",
            "audit",
            "vulnerability",
            "schema",
            "contract",
            "owasp",
        ],
        "skills": [
            "architecture-reviewer",
            "code-review-backend",
            "code-review-frontend",
            "code-review-integration",
            "code-review-performance",
            "code-review-security",
            "code-review-api-contracts",
            "quality-inspector",
            "performance-auditor",
            "schema-drift-detector",
            "security-analyst",
        ],
    },
    "orchestration": {
        "description": "Task decomposition, failure analysis, and execution coordination.",
        "keywords": [
            "orchestrate",
            "pipeline",
            "dependency",
            "failure",
            "rollback",
            "checkpoint",
            "state",
            "distributed",
        ],
        "skills": [
            "context-manager",
            "pipeline-state-tracker",
            "error-recovery-agent",
            "rollback-agent",
            "service-chain-debugger",
            "cross-system-analyzer",
            "conclusion-synthesizer",
            "llm-orchestrator",
            "log-aggregator",
            "env-sync-validator",
        ],
    },
    "infra-ops": {
        "description": "Deployments, systems, networking, and platform operations.",
        "keywords": [
            "deploy",
            "docker",
            "infrastructure",
            "server",
            "systemd",
            "network",
            "firewall",
            "ssl",
            "database",
            "vpn",
            "tunnel",
        ],
        "skills": [
            "docker-container-admin",
            "app-deploy-admin",
            "database-admin",
            "systemd-service-manager",
            "linux-server-hardening",
            "nginx-ssl-proxy",
            "network-diagnostics",
            "network-routing-admin",
            "firewall-admin",
            "vpn-tunnel-admin",
            "tunnel-manager",
            "infrastructure-validator",
        ],
    },
    "ml-media": {
        "description": "Video, diffusion, GPU, and media-processing pipelines.",
        "keywords": [
            "video",
            "ffmpeg",
            "model",
            "diffusion",
            "comfyui",
            "swarmui",
            "cuda",
            "gpu",
            "vast",
            "lora",
        ],
        "skills": [
            "ffmpeg-expert",
            "comfyui-expert",
            "swarmui-expert",
            "cuda-environment-expert",
            "diffusion-dependency-manager",
            "gpu-provider-monitor",
            "instance-bootstrap",
            "vast-instance-setup",
            "wan-model-downloader",
            "civitai-model-downloader",
        ],
    },
    "project-i2v": {
        "description": "Project-specific i2v patterns for optional targeted tasks.",
        "keywords": [
            "i2v",
            "swarmui",
            "provider",
            "playground",
            "prompt",
            "social",
            "vastai",
        ],
        "skills": [
            "i2v-add-endpoint",
            "i2v-add-hook",
            "i2v-add-model",
            "i2v-add-page",
            "i2v-add-video-provider",
            "i2v-add-postprocess",
            "i2v-bulk-pipeline",
            "i2v-debug-pipeline",
            "i2v-debug-flow",
            "i2v-schema-check",
            "i2v-ffmpeg-ops",
            "i2v-docker-deploy",
        ],
    },
}


BASE_PACKS: tuple[str, ...] = ("core-dev",)
