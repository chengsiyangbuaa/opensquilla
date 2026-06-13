"""Lightweight, model-free environment probe.

Reads a freshly cloned repo and produces a short "environment hints" block
to feed the prompt, so the agent does not burn iterations rediscovering the
language, package manager, and how the project installs and tests itself.
The biggest signal is CI config: it is the maintainers' own recipe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# marker file -> (language, package manager guess)
_LANG_MARKERS: list[tuple[str, str, str]] = [
    ("pyproject.toml", "Python", "pip/uv/poetry"),
    ("setup.py", "Python", "pip"),
    ("requirements.txt", "Python", "pip"),
    ("package.json", "JavaScript/TypeScript", "npm/yarn/pnpm"),
    ("go.mod", "Go", "go"),
    ("Cargo.toml", "Rust", "cargo"),
    ("pom.xml", "Java", "maven"),
    ("build.gradle", "Java/Kotlin", "gradle"),
    ("Gemfile", "Ruby", "bundler"),
    ("composer.json", "PHP", "composer"),
]

# lockfile -> definitive package manager
_LOCKFILES: dict[str, str] = {
    "uv.lock": "uv",
    "poetry.lock": "poetry",
    "Pipfile.lock": "pipenv",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
    "package-lock.json": "npm",
    "Cargo.lock": "cargo",
    "Gemfile.lock": "bundler",
    "composer.lock": "composer",
}


@dataclass
class EnvProbe:
    languages: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    ci_files: list[str] = field(default_factory=list)
    notable: list[str] = field(default_factory=list)  # devcontainer, Makefile...

    def as_hints(self) -> str:
        """Render a compact hints block for the prompt (empty if nothing found)."""
        if not (self.languages or self.package_managers or self.ci_files or self.notable):
            return ""
        lines = ["Environment hints (auto-detected — verify before relying on them):"]
        if self.languages:
            lines.append(f"- Language(s): {', '.join(self.languages)}")
        if self.package_managers:
            lines.append(f"- Package manager(s): {', '.join(self.package_managers)}")
        if self.ci_files:
            lines.append(
                "- CI config (often holds the canonical install/test commands): "
                + ", ".join(self.ci_files)
            )
        if self.notable:
            lines.append(f"- Also present: {', '.join(self.notable)}")
        return "\n".join(lines)


def probe(repo: Path) -> EnvProbe:
    """Inspect a repo's top level (and .github/workflows) for env signals."""
    result = EnvProbe()
    present = {p.name for p in repo.iterdir()} if repo.is_dir() else set()

    seen_lang: set[str] = set()
    seen_pm: set[str] = set()
    for marker, lang, pm in _LANG_MARKERS:
        if marker in present:
            if lang not in seen_lang:
                result.languages.append(lang)
                seen_lang.add(lang)
            if pm not in seen_pm:
                result.package_managers.append(pm)
                seen_pm.add(pm)

    for lockfile, pm in _LOCKFILES.items():
        if lockfile in present and pm not in seen_pm:
            # A lockfile pins the manager more precisely than the marker guess.
            result.package_managers.insert(0, pm)
            seen_pm.add(pm)

    workflows = repo / ".github" / "workflows"
    if workflows.is_dir():
        result.ci_files = sorted(
            f".github/workflows/{p.name}"
            for p in workflows.iterdir()
            if p.suffix in (".yml", ".yaml")
        )

    for extra in ("Makefile", "Dockerfile", ".devcontainer", "tox.ini", "noxfile.py"):
        if extra in present:
            result.notable.append(extra)

    return result
