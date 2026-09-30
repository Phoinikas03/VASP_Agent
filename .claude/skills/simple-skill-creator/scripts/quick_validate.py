#!/usr/bin/env python3
"""
Quickly validate whether SKILL.md meets the basic format requirements.

Usage: python scripts/quick_validate.py <skill_dir>
Exit code: 0 = valid, 1 = invalid
"""
import sys
import re
import yaml
from pathlib import Path


def validate_skill(skill_path) -> tuple[bool, str]:
    skill_path = Path(skill_path)
    skill_md = skill_path / "SKILL.md"

    if not skill_md.exists():
        return False, "SKILL.md does not exist"

    content = skill_md.read_text(encoding="utf-8")

    if not content.startswith("---"):
        return False, "Missing YAML frontmatter (must start with ---)"

    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return False, "Invalid frontmatter format (closing --- not found)"

    try:
        fm = yaml.safe_load(match.group(1))
        if not isinstance(fm, dict):
            return False, "frontmatter must be a YAML dictionary"
    except yaml.YAMLError as e:
        return False, f"Failed to parse frontmatter YAML: {e}"

    ALLOWED = {"name", "description", "license", "allowed-tools", "metadata", "compatibility", "version"}
    unexpected = set(fm.keys()) - ALLOWED
    if unexpected:
        return False, (
            f"frontmatter contains disallowed fields: {', '.join(sorted(unexpected))}. "
            f"Allowed fields: {', '.join(sorted(ALLOWED))}"
        )

    if "name" not in fm:
        return False, "frontmatter is missing the 'name' field"
    if "description" not in fm:
        return False, "frontmatter is missing the 'description' field"

    name = str(fm["name"]).strip()
    if not re.match(r"^[a-z0-9-]+$", name):
        return False, f"name '{name}' must be kebab-case (lowercase letters, digits, hyphens)"
    if name.startswith("-") or name.endswith("-") or "--" in name:
        return False, f"name '{name}' cannot start/end with a hyphen or contain consecutive hyphens"
    if len(name) > 64:
        return False, f"name is too long ({len(name)} characters), max 64"

    description = str(fm["description"]).strip()
    if "<" in description or ">" in description:
        return False, "description cannot contain angle brackets < >"
    if len(description) > 1024:
        return False, f"description is too long ({len(description)} characters), max 1024"

    compatibility = fm.get("compatibility", "")
    if compatibility and len(str(compatibility)) > 500:
        return False, f"compatibility is too long ({len(str(compatibility))} characters), max 500"

    return True, f"✓ SKILL '{name}' format is valid"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/quick_validate.py <skill_dir>")
        sys.exit(1)

    valid, message = validate_skill(sys.argv[1])
    print(message)
    sys.exit(0 if valid else 1)
