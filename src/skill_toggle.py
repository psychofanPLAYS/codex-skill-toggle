#!/usr/bin/env python3
"""Reversible manual toggles for Codex local skills and plugin bundles."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REGISTRY_VERSION = 1
NOTES_VERSION = 1
RECEIPT_LINE = re.compile(r"^- `([^`]+)` -> `([^`]+)`")
SKILL_NAME = re.compile(r"^name:\s*[\"']?([^\"'\n]+)", re.MULTILINE)
PLUGIN_HEADER = re.compile(r'^\[plugins\."([^"]+)"\]\s*$')
ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RED = "\033[31m"
ANSI_CYAN = "\033[36m"
ANSI_BLUE = "\033[34m"
ANSI_ESCAPE = re.compile(r"\033\[[0-9;]*m")
STATE_ORDER = {"collision": 0, "disabled": 1, "enabled": 2, "mixed": 3, "missing": 4}
STATE_LABELS = {
    "collision": "COLLISIONS",
    "disabled": "DISABLED",
    "enabled": "ENABLED",
    "mixed": "MIXED",
    "missing": "MISSING",
}
STATE_COLORS = {
    "collision": ANSI_RED,
    "disabled": ANSI_YELLOW,
    "enabled": ANSI_GREEN,
    "mixed": ANSI_YELLOW,
    "missing": ANSI_RED,
}


def now_stamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d-%H%M%S%z")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def should_color(mode: str, as_json: bool) -> bool:
    """Choose color safely: JSON and redirected output never get ANSI bytes."""
    if as_json or mode == "never":
        return False
    return mode == "always" or sys.stdout.isatty()


def colorize(text: str, code: str, enabled: bool) -> str:
    return f"{code}{text}{ANSI_RESET}" if enabled else text


def format_box(title: str, lines: list[str], color: bool = False) -> list[str]:
    """Return a Unicode box whose visible columns stay aligned with ANSI color."""
    visible = [ANSI_ESCAPE.sub("", line) for line in lines]
    width = max([len(ANSI_ESCAPE.sub("", title)), *[len(line) for line in visible], 2])
    rendered_title = colorize(title, ANSI_BOLD + ANSI_CYAN, color)
    result = [f"┌─ {rendered_title} " + "─" * max(0, width - len(title)) + "┐"]
    for line, plain in zip(lines, visible):
        result.append(f"│ {line}{' ' * (width - len(plain))} │")
    result.append(f"└{'─' * (width + 2)}┘")
    return result


def paths(codex_home: Path) -> dict[str, Path]:
    return {
        "home": codex_home,
        "config": codex_home / "config.toml",
        "store": codex_home / "skills-disabled",
        "registry": codex_home / "skills-disabled" / "registry.json",
        "notes": codex_home / "skills-disabled" / "notes.json",
        "receipts": codex_home / "skills-disabled" / "receipts",
        "reports": codex_home / "skills-disabled" / "reports",
        "notifier_script": codex_home / "skills-disabled" / "bin" / "codex-skill-notifier.sh",
        "notifier_plist": codex_home / "skills-disabled" / "notifier" / "com.davejski.codex-skill-toggle-notifier.plist",
        "launch_agent": codex_home.parent / "Library/LaunchAgents" / "com.davejski.codex-skill-toggle-notifier.plist",
    }


def load_registry(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("version") != REGISTRY_VERSION:
        raise ValueError(f"unsupported registry version: {data.get('version')!r}")
    return data


def save_registry(path: Path, registry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def normalize(value: str) -> str:
    return " ".join(value.casefold().replace("_", "-").split())


def config_skill_state(config_path: Path, names: set[str], paths: set[str] | None = None) -> str | None:
    paths = paths or set()
    if not config_path.exists() or (not names and not paths):
        return None
    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    skill_header = re.compile(r"^\[\[skills\.config\]\]\s*$")
    for start, end, _ in config_blocks(lines, skill_header):
        block = "".join(lines[start:end])
        if any(f'name = "{name}"' in block for name in names) or any(
            f'path = "{path}"' in block for path in paths
        ):
            match = re.search(r"^enabled\s*=\s*(true|false)", block, re.MULTILINE)
            if match:
                return "enabled" if match.group(1) == "true" else "disabled"
    return None


def config_plugin_state(config_path: Path, plugin_id: str) -> str | None:
    if not config_path.exists() or not plugin_id:
        return None
    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    for start, end, match in config_blocks(lines, PLUGIN_HEADER):
        if match.group(1) != plugin_id:
            continue
        block = "".join(lines[start:end])
        value = re.search(r"^enabled\s*=\s*(true|false)", block, re.MULTILINE)
        if value:
            return "enabled" if value.group(1) == "true" else "disabled"
    return None


def config_plugin_state_for_source(config_path: Path, source: Path, plugin_name: str) -> str | None:
    states = {
        config_plugin_state(config_path, plugin_id)
        for plugin_id in plugin_ids_for(source, plugin_name)
    }
    states.discard(None)
    if states:
        return "enabled" if "enabled" in states else "disabled"
    configured_ids = configured_plugin_ids_all(config_path) or set()
    matching_ids = [
        plugin_id for plugin_id in configured_ids
        if plugin_id.split("@", 1)[0] == plugin_name
    ]
    matching_states = {config_plugin_state(config_path, plugin_id) for plugin_id in matching_ids}
    matching_states.discard(None)
    if matching_states:
        return "enabled" if "enabled" in matching_states else "disabled"
    return None


def entry_status(entry: dict, config_path: Path | None = None) -> str:
    if entry.get("toggle_mode") == "config":
        configured = config_skill_state(config_path, set(entry.get("config_names", []))) if config_path else None
        return configured or entry.get("state", "missing")
    source = Path(entry["source_path"])
    disabled = Path(entry["disabled_path"])
    if source.exists() and disabled.exists():
        return "collision"
    if source.exists():
        return "enabled"
    if disabled.exists():
        return "disabled"
    return "missing"


def resolve(query: str, registry: dict) -> list[dict]:
    needle = normalize(query)
    entries = registry.get("entries", [])

    def values(item: dict) -> set[str]:
        result = {
            item.get("id", ""),
            item.get("display_name", ""),
            item.get("plugin_name", ""),
            item.get("source_path", ""),
            item.get("disabled_path", ""),
        }
        result.update(item.get("aliases", []))
        result.update(item.get("skill_names", []))
        result.update(item.get("plugin_ids", []))
        return {normalize(value) for value in result if value}

    exact = [item for item in entries if needle in values(item)]
    if exact:
        skill_exact = [
            item
            for item in exact
            if item.get("kind") == "plugin_skill"
            and needle in {normalize(value) for value in [*item.get("skill_names", []), *item.get("config_names", [])]}
        ]
        if skill_exact:
            return skill_exact
        return exact
    return [
        item
        for item in entries
        if any(needle in value for value in values(item))
    ]


def verify_registry(registry: dict) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    seen_sources: set[str] = set()
    seen_disabled: set[str] = set()
    for item in registry.get("entries", []):
        item_id = item.get("id", "")
        source = str(Path(item["source_path"]).expanduser())
        disabled = str(Path(item["disabled_path"]).expanduser())
        if item_id in seen_ids:
            errors.append(f"duplicate id: {item_id}")
        if source in seen_sources:
            errors.append(f"duplicate source path: {source}")
        if disabled in seen_disabled:
            errors.append(f"duplicate disabled path: {disabled}")
        seen_ids.add(item_id)
        seen_sources.add(source)
        seen_disabled.add(disabled)
        source_exists = Path(source).exists()
        disabled_exists = Path(disabled).exists()
        if item.get("toggle_mode") == "config":
            if not source_exists:
                errors.append(f"config-only skill source missing: {item_id}")
            continue
        if source_exists and disabled_exists:
            errors.append(f"both source and disabled paths exist: {item_id}")
        if not source_exists and not disabled_exists:
            errors.append(f"neither source nor disabled path exists: {item_id}")
        if item.get("state") not in {"enabled", "disabled", "missing", "collision"}:
            errors.append(f"invalid recorded state for {item_id}: {item.get('state')!r}")
    return errors


def collision_archive_path(disabled: Path) -> Path:
    parts = disabled.parts
    try:
        index = parts.index("skills-disabled")
    except ValueError as error:
        raise ValueError(f"disabled path is outside skills-disabled: {disabled}") from error
    root = Path(*parts[: index + 1])
    relative = Path(*parts[index + 1 :])
    return root / "reappeared" / now_stamp() / relative


def move_entry(entry: dict, direction: str) -> Path | None:
    source = Path(entry["source_path"])
    disabled = Path(entry["disabled_path"])
    if direction == "disable":
        if not source.exists():
            if disabled.exists():
                return None
            raise FileNotFoundError(f"active source does not exist: {source}")
        if disabled.exists():
            archive = collision_archive_path(disabled)
            archive.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(disabled), str(archive))
        else:
            archive = None
        disabled.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(disabled))
        return archive
    if direction == "enable":
        if not disabled.exists():
            if source.exists():
                return
            raise FileNotFoundError(f"disabled bundle does not exist: {disabled}")
        if source.exists():
            raise FileExistsError(f"active source destination already exists: {source}")
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(disabled), str(source))
        return None
    raise ValueError(f"unknown move direction: {direction}")


def read_skill_names(root: Path, plugin_name: str | None = None) -> list[str]:
    names: set[str] = set()
    for skill_file in root.rglob("SKILL.md") if root.exists() else []:
        try:
            text = skill_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = SKILL_NAME.search(text)
        raw = (match.group(1).strip() if match else skill_file.parent.name)
        names.add(raw)
        if plugin_name and ":" not in raw:
            names.add(f"{plugin_name}:{raw}")
    return sorted(names)


def plugin_metadata(root: Path) -> tuple[str, str | None]:
    manifests = sorted(root.rglob(".codex-plugin/plugin.json")) if root.exists() else []
    if not manifests:
        return root.name, None
    manifest = manifests[0]
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return root.name, None
    return str(data.get("name") or root.name), str(data.get("version")) if data.get("version") else None


def plugin_ids_for(source: Path, plugin_name: str) -> list[str]:
    source_string = str(source)
    ids: set[str] = set()
    if "/plugins/cache/" in source_string:
        marketplace = source_string.split("/plugins/cache/", 1)[1].split("/", 1)[0]
        ids.add(f"{plugin_name}@{marketplace}")
        if marketplace == "openai-curated-remote":
            ids.add(f"{plugin_name}@openai-curated")
    if "openai-primary-runtime/plugins" in source_string:
        ids.add(f"{plugin_name}@openai-primary-runtime")
    if "codex-warp" in source_string:
        ids.add(f"{plugin_name}@codex-warp")
    if not ids:
        ids.add(plugin_name)
    return sorted(ids)


def source_plugin_id(source: Path, plugin_name: str) -> str:
    """Return the exact configured plugin id represented by an installed path."""
    source_string = str(source)
    if "/plugins/cache/" in source_string:
        marketplace = source_string.split("/plugins/cache/", 1)[1].split("/", 1)[0]
        return f"{plugin_name}@{marketplace}"
    if "openai-primary-runtime/plugins" in source_string:
        return f"{plugin_name}@openai-primary-runtime"
    if "codex-warp" in source_string:
        return f"{plugin_name}@codex-warp"
    return plugin_name


def stable_id(kind: str, source: Path) -> str:
    digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:12]
    return f"{kind}:{digest}"


def make_entry(source: Path, disabled: Path, *, confidence: str, kind: str | None = None) -> dict:
    metadata_root = disabled if disabled.exists() else source
    plugin_name, version = plugin_metadata(metadata_root)
    is_plugin = kind == "plugin_bundle" or (metadata_root / ".codex-plugin").exists() or bool(list(metadata_root.rglob(".codex-plugin/plugin.json")))
    kind = "plugin_bundle" if is_plugin else "local_skill"
    skill_names = read_skill_names(metadata_root, plugin_name if is_plugin else None)
    aliases = {plugin_name, *skill_names}
    if version:
        aliases.add(f"{plugin_name}@{version}")
    plugin_ids = plugin_ids_for(source, plugin_name) if is_plugin else []
    aliases.update(plugin_ids)
    return {
        "id": stable_id(kind, source),
        "kind": kind,
        "display_name": plugin_name,
        "plugin_name": plugin_name if is_plugin else None,
        "plugin_ids": plugin_ids,
        "skill_names": skill_names,
        "aliases": sorted(alias for alias in aliases if alias),
        "source_path": str(source),
        "disabled_path": str(disabled),
        "state": entry_status({"source_path": str(source), "disabled_path": str(disabled)}),
        "source_confidence": confidence,
        "version": version,
    }


def active_plugin_roots(codex_home: Path) -> list[tuple[str, Path]]:
    return [
        ("cache", codex_home / "plugins/cache"),
        ("tmp", codex_home / ".tmp/plugins/plugins"),
        ("runtime", codex_home.parent / ".cache/codex-runtimes/codex-primary-runtime/plugins"),
    ]


def configured_plugin_ids(config_path: Path) -> set[str] | None:
    if not config_path.exists():
        return None
    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    plugin_ids: set[str] = set()
    for start, end, match in config_blocks(lines, PLUGIN_HEADER):
        block = "".join(lines[start:end])
        if re.search(r"^enabled\s*=\s*true", block, re.MULTILINE):
            plugin_ids.add(match.group(1))
    return plugin_ids


def configured_plugin_ids_all(config_path: Path) -> set[str] | None:
    if not config_path.exists():
        return None
    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    return {match.group(1) for _, _, match in config_blocks(lines, PLUGIN_HEADER)}


def make_plugin_skill_entry(codex_home: Path, store: Path, package_root: Path, skill_dir: Path, plugin_name: str, config_path: Path) -> dict:
    skill_file = skill_dir / "SKILL.md"
    text = skill_file.read_text(encoding="utf-8", errors="replace")
    match = SKILL_NAME.search(text)
    raw_name = match.group(1).strip() if match else skill_dir.name
    qualified = raw_name if ":" in raw_name else f"{plugin_name}:{raw_name}"
    current_state = config_skill_state(config_path, {qualified}) or "enabled"
    return {
        "id": stable_id("plugin_skill", skill_dir),
        "kind": "plugin_skill",
        "toggle_mode": "config",
        "display_name": qualified,
        "plugin_name": plugin_name,
        "plugin_ids": plugin_ids_for(package_root, plugin_name),
        "skill_names": [qualified, raw_name],
        "config_names": [qualified],
        "aliases": [qualified, raw_name, skill_dir.name],
        "source_path": str(skill_dir),
        "disabled_path": str(store / "config-only" / plugin_name / skill_dir.name),
        "state": current_state,
        "source_confidence": "observed",
    }


def discover_active_entries(codex_home: Path, store: Path, config_path: Path) -> list[dict]:
    discovered: list[dict] = []
    local_roots = [("codex-skills", codex_home / "skills"), ("agents-skills", codex_home / ".agents/skills")]
    for tag, root in local_roots:
        if not root.exists():
            continue
        for skill_file in sorted(root.glob("*/SKILL.md")):
            skill_name = skill_file.parent.name
            disabled = store / "local" / skill_name
            if disabled.exists():
                disabled = store / "active" / "local" / tag / skill_name
            discovered.append(make_entry(skill_file.parent, disabled, confidence="observed", kind="local_skill"))

    seen_sources: set[Path] = set()
    allowed_plugin_ids = configured_plugin_ids(config_path)
    for tag, root in active_plugin_roots(codex_home):
        if not root.exists():
            continue
        for manifest in sorted(root.rglob(".codex-plugin/plugin.json")):
            package_root = manifest.parent.parent
            if package_root in seen_sources:
                continue
            seen_sources.add(package_root)
            plugin_name, _ = plugin_metadata(package_root)
            if (
                allowed_plugin_ids is not None
                and source_plugin_id(package_root, plugin_name) not in allowed_plugin_ids
            ):
                continue
            try:
                relative = package_root.relative_to(root)
            except ValueError:
                relative = Path(package_root.name)
            disabled = store / "active" / "plugins" / tag / relative
            discovered.append(make_entry(package_root, disabled, confidence="observed", kind="plugin_bundle"))
            for skill_file in sorted(package_root.rglob("SKILL.md")):
                discovered.append(make_plugin_skill_entry(codex_home, store, package_root, skill_file.parent, plugin_name, config_path))
    return discovered


def receipt_pairs(store: Path) -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    receipts = list(store.glob("*.md")) + list((store / "receipts").glob("*.md"))
    for receipt in sorted(receipts):
        for line in receipt.read_text(encoding="utf-8", errors="replace").splitlines():
            match = RECEIPT_LINE.match(line)
            if match:
                source = Path(match.group(1))
                disabled = Path(match.group(2))
                if source == store or store in source.parents:
                    continue
                pairs.append((source, disabled))
    return pairs


def infer_manual_source(home: Path, package_name: str, version: str | None) -> tuple[Path, str]:
    version = version or ""
    if package_name in {"documents", "presentations", "spreadsheets"}:
        return home.parent / ".cache/codex-runtimes/codex-primary-runtime/plugins/openai-primary-runtime/plugins" / package_name / version, "inferred"
    if package_name in {"netlify", "vercel"}:
        return home / ".tmp/plugins/plugins" / package_name / version, "inferred"
    if package_name in {"codex-warp", "warp"}:
        return home / "plugins/cache/codex-warp/warp" / version, "inferred"
    if package_name == "coderabbit":
        return home / "plugins/cache/openai-curated/coderabbit" / version, "inferred"
    return home / "skills" / package_name, "unknown"


def seed_registry(codex_home: Path) -> dict:
    location = paths(codex_home)
    store = location["store"]
    entries: dict[str, dict] = {}
    for source, disabled in receipt_pairs(store):
        if not disabled.exists():
            continue
        item = make_entry(source, disabled, confidence="observed")
        entries[item["disabled_path"]] = item

    excluded = {"local", "plugins", "receipts", "bin", "tests", "reappeared"}
    for child in sorted(store.iterdir()) if store.exists() else []:
        if child.name.startswith(".") or child.name in excluded or child.is_file():
            continue
        manifests = sorted(child.rglob(".codex-plugin/plugin.json"))
        if manifests:
            package_root = manifests[0].parent.parent
            plugin_name, manifest_version = plugin_metadata(package_root)
            source, confidence = infer_manual_source(codex_home, plugin_name, package_root.name)
            item = make_entry(source, package_root, confidence=confidence, kind="plugin_bundle")
            duplicate = any(
                existing.get("kind") == "plugin_bundle"
                and existing.get("display_name") == item.get("display_name")
                and existing.get("version") == manifest_version
                and existing.get("source_confidence") == "observed"
                for existing in entries.values()
            )
            if not duplicate:
                entries[item["disabled_path"]] = item
        elif (child / "SKILL.md").exists():
            source = codex_home / "skills" / child.name
            item = make_entry(source, child, confidence="inferred", kind="local_skill")
            entries[item["disabled_path"]] = item

    existing_sources = {item["source_path"] for item in entries.values()}
    for item in discover_active_entries(codex_home, store, location["config"]):
        if item.get("kind") == "plugin_skill":
            duplicate_keys = [
                key
                for key, existing in entries.items()
                if existing.get("kind") == "plugin_skill"
                and existing.get("display_name") == item.get("display_name")
            ]
            for key in duplicate_keys:
                del entries[key]
        if item["source_path"] in existing_sources:
            continue
        if item["disabled_path"] in entries:
            continue
        entries[item["disabled_path"]] = item
        existing_sources.add(item["source_path"])

    return {
        "version": REGISTRY_VERSION,
        "generated_at": iso_now(),
        "entries": sorted(entries.values(), key=lambda item: item["id"]),
    }


def config_backup(config: Path, home: Path) -> Path | None:
    if not config.exists():
        return None
    backup_dir = home / "profiles/_shared/backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"manual-skill-toggle-{now_stamp()}-config.toml"
    shutil.copy2(config, backup)
    return backup


def replace_enabled_in_block(lines: list[str], start: int, end: int, enabled: bool) -> bool:
    value = "true" if enabled else "false"
    for index in range(start, end):
        if re.match(r"^enabled\s*=", lines[index].strip()):
            newline = "\n" if lines[index].endswith("\n") else ""
            lines[index] = f"enabled = {value}{newline}"
            return True
    return False


def config_blocks(lines: list[str], header_pattern: re.Pattern[str]) -> list[tuple[int, int, re.Match[str]]]:
    starts = [(index, header_pattern.match(line)) for index, line in enumerate(lines)]
    starts = [(index, match) for index, match in starts if match]
    result = []
    for start, match in starts:
        end = len(lines)
        for index in range(start + 1, len(lines)):
            if lines[index].startswith("["):
                end = index
                break
        result.append((start, end, match))
    return result


def update_config(config: Path, entries: list[dict], enabled: bool) -> None:
    if not config.exists():
        return
    lines = config.read_text(encoding="utf-8").splitlines(keepends=True)
    changed = False
    plugin_ids = {plugin_id for item in entries for plugin_id in item.get("plugin_ids", [])}
    found_plugin_ids: set[str] = set()
    for start, end, match in config_blocks(lines, PLUGIN_HEADER):
        if match.group(1) in plugin_ids:
            found_plugin_ids.add(match.group(1))
            changed = replace_enabled_in_block(lines, start, end, enabled) or changed
    local_paths = {item["source_path"] for item in entries if item["kind"] == "local_skill"}
    config_names = {name for item in entries for name in item.get("config_names", [])}
    skill_header = re.compile(r"^\[\[skills\.config\]\]\s*$")
    for start, end, _ in config_blocks(lines, skill_header):
        block = "".join(lines[start:end])
        if any(f'path = "{path}"' in block for path in local_paths) or any(f'name = "{name}"' in block for name in config_names):
            changed = replace_enabled_in_block(lines, start, end, enabled) or changed
    missing_plugin_ids = sorted(plugin_ids - found_plugin_ids)
    if missing_plugin_ids:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        for plugin_id in missing_plugin_ids[:1]:
            lines.extend([f'\n[plugins."{plugin_id}"]\n', f"enabled = {'true' if enabled else 'false'}\n"])
            changed = True
    found_local_paths = {
        path
        for path in local_paths
        if any(f'path = "{path}"' in "".join(lines[start:end]) for start, end, _ in config_blocks(lines, skill_header))
    }
    missing_local_paths = sorted(local_paths - found_local_paths)
    for path in missing_local_paths:
        lines.extend(["\n[[skills.config]]\n", f'path = "{path}"\n', f"enabled = {'true' if enabled else 'false'}\n"])
        changed = True
    found_config_names = {
        name
        for name in config_names
        if any(f'name = "{name}"' in "".join(lines[start:end]) for start, end, _ in config_blocks(lines, skill_header))
    }
    for name in sorted(config_names - found_config_names):
        lines.extend(["\n[[skills.config]]\n", f'name = "{name}"\n', f"enabled = {'true' if enabled else 'false'}\n"])
        changed = True
    if changed:
        config.write_text("".join(lines), encoding="utf-8")


def write_operation_receipt(location: dict[str, Path], operation: str, entries: list[dict], backup: Path | None, quarantined: list[dict] | None = None) -> Path:
    location["receipts"].mkdir(parents=True, exist_ok=True)
    base = location["receipts"] / f"{now_stamp()}-{operation}"
    receipt = base.with_suffix(".json")
    counter = 2
    while receipt.exists():
        receipt = base.with_name(f"{base.name}-{counter}").with_suffix(".json")
        counter += 1
    payload = {
        "operation": operation,
        "timestamp": iso_now(),
        "config_backup": str(backup) if backup else None,
        "quarantined": quarantined or [],
        "entries": [
            {
                "id": item["id"],
                "source_path": item["source_path"],
                "disabled_path": item["disabled_path"],
                "state": item["state"],
            }
            for item in entries
        ],
    }
    receipt.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return receipt


def context_targets(location: dict[str, Path], registry: dict) -> list[dict]:
    """Build the single numbered inventory used by humans and numeric commands."""
    grouped: dict[tuple[str, str], list[dict]] = {}
    for item in registry.get("entries", []):
        key = (item.get("kind", "unknown"), item.get("display_name") or item.get("id", ""))
        grouped.setdefault(key, []).append(item)

    kind_order = {"local_skill": 0, "plugin_bundle": 1, "plugin_skill": 2}
    targets: list[dict] = []
    for (kind, name), items in sorted(
        grouped.items(), key=lambda pair: (kind_order.get(pair[0][0], 99), pair[0][1].casefold())
    ):
        states = {entry_status(item, location.get("config")) for item in items}
        state = states.pop() if len(states) == 1 else "mixed"
        if "collision" in states or state == "collision":
            state = "collision"
        targets.append({
            "position": len(targets) + 1,
            "name": name,
            "kind": kind,
            "state": state,
            "ids": [item["id"] for item in items],
            "source_paths": sorted({item["source_path"] for item in items}),
            "disabled_paths": sorted({item["disabled_path"] for item in items}),
            "entry_count": len(items),
            "query": name,
        })
    return targets


def select_entries(query: str, registry: dict, config_path: Path | None = None) -> list[dict]:
    if normalize(query).isdigit():
        position = int(normalize(query))
        location = {"config": config_path} if config_path else {}
        targets = context_targets(location, registry)
        if position < 1 or position > len(targets):
            raise SystemExit(f"position {position} is outside the displayed range 1-{len(targets)}")
        target = targets[position - 1]
        ids = set(target["ids"])
        return [item for item in registry.get("entries", []) if item["id"] in ids]
    matches = resolve(query, registry)
    if not matches:
        raise SystemExit(f"no skill or plugin matches {query!r}")
    exact = [item for item in matches if normalize(query) in {normalize(value) for value in [item.get("id", ""), *item.get("aliases", [])]}]
    if exact:
        return exact
    names = sorted({item.get("display_name") or item.get("id") for item in matches})
    if len(names) > 1:
        raise SystemExit(f"ambiguous query {query!r}; choose one of: {', '.join(names)}")
    return matches


def load_notes(path: Path) -> dict:
    if not path.exists():
        return {"version": NOTES_VERSION, "notes": []}
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("version") != NOTES_VERSION:
        raise ValueError(f"unsupported notes version: {data.get('version')!r}")
    return data


def save_notes(path: Path, notes: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(notes, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def add_note(location: dict[str, Path], registry: dict, query: str, text: str) -> dict:
    matches = select_entries(query, registry, location["config"])
    target_names = sorted({item.get("display_name") or item["id"] for item in matches})
    target_ids = sorted({item["id"] for item in matches})
    target_kind = matches[0].get("kind")
    created = iso_now()
    note_id = "note:" + hashlib.sha256(f"{created}\0{query}\0{text}".encode()).hexdigest()[:12]
    note = {
        "id": note_id,
        "target_name": target_names[0],
        "target_names": target_names,
        "target_ids": target_ids,
        "target_kind": target_kind,
        "text": text,
        "created_at": created,
        "updated_at": created,
    }
    notes = load_notes(location["notes"])
    notes["notes"].append(note)
    save_notes(location["notes"], notes)
    return note


def list_notes(location: dict[str, Path], registry: dict, query: str | None = None) -> list[dict]:
    notes = load_notes(location["notes"])["notes"]
    if not query:
        return sorted(notes, key=lambda item: item.get("updated_at", ""), reverse=True)
    matches = select_entries(query, registry, location["config"]) if normalize(query).isdigit() else resolve(query, registry)
    if not matches:
        exact_note = [item for item in notes if normalize(item["id"]) == normalize(query)]
        if exact_note:
            return exact_note
        raise SystemExit(f"no skill, plugin, or note matches {query!r}")
    ids = {item["id"] for item in matches}
    names = {normalize(item.get("display_name") or item["id"]) for item in matches}
    return [
        note for note in notes
        if ids.intersection(note.get("target_ids", []))
        or names.intersection(normalize(name) for name in note.get("target_names", []))
    ]


def delete_note(location: dict[str, Path], note_id: str) -> bool:
    notes = load_notes(location["notes"])
    remaining = [note for note in notes["notes"] if note.get("id") != note_id]
    if len(remaining) == len(notes["notes"]):
        return False
    notes["notes"] = remaining
    save_notes(location["notes"], notes)
    return True


def operation_plan(location: dict[str, Path], registry: dict, query: str, operation: str) -> list[str]:
    """Describe a pending operation without touching config or the filesystem."""
    entries = select_entries(query, registry, location["config"])
    desired = "enabled" if operation == "enable" else "disabled"
    pending = [item for item in entries if entry_status(item, location["config"]) != desired]
    lines = [f"ACTION: {operation.upper()} {query}", f"CONFIG: {location['config']}"]
    if not pending:
        lines.append(f"RESULT: no change needed; all {len(entries)} target(s) are already {desired}")
        return lines
    for item in pending:
        current = entry_status(item, location["config"])
        lines.extend([
            f"TARGET: {item.get('display_name') or item['id']} [{item['kind']}]",
            f"STATE: {current} -> {desired}",
        ])
        if item.get("toggle_mode") == "config":
            lines.append("MODE: config-only; no skill files will be moved")
        else:
            lines.append("MODE: move the top-level bundle and keep a receipt")
            lines.append(f"SOURCE: {item['source_path']}")
            lines.append(f"DISABLED / RESTORE PATH: {item['disabled_path']}")
        if item.get("source_confidence") == "unknown":
            lines.append("WARNING: restore source is unknown; enabling will be refused")
    lines.append(f"RESULT: {len(pending)} target(s) will change")
    return lines


def operate(location: dict[str, Path], registry: dict, query: str, operation: str) -> Path | None:
    entries = select_entries(query, registry, location["config"])
    if operation == "enable" and any(item.get("source_confidence") == "unknown" for item in entries):
        unknown = ", ".join(item["id"] for item in entries if item.get("source_confidence") == "unknown")
        raise SystemExit(f"source path is unknown for {unknown}; add it to registry.json before enabling")
    desired = "enabled" if operation == "enable" else "disabled"
    pending = [item for item in entries if entry_status(item, location["config"]) != desired]
    if not pending:
        return None
    for item in pending:
        current = entry_status(item, location["config"])
        if current not in {"enabled", "disabled"} and not (operation == "disable" and current == "collision"):
            raise SystemExit(f"cannot {operation} {item['id']}: current state is {entry_status(item)}")
    backup = config_backup(location["config"], location["home"])
    moved: list[dict] = []
    quarantined: list[dict] = []
    try:
        for item in pending:
            archived = None if item.get("toggle_mode") == "config" else move_entry(item, operation)
            if archived:
                quarantined.append({"id": item["id"], "archived_path": str(archived)})
            moved.append(item)
        update_config(location["config"], pending, operation == "enable")
    except Exception:
        for item in reversed(moved):
            move_entry(item, "enable" if operation == "disable" else "disable")
        raise
    for item in pending:
        item["state"] = desired
    registry["updated_at"] = iso_now()
    save_registry(location["registry"], registry)
    return write_operation_receipt(location, operation, pending, backup, quarantined)


def set_source(location: dict[str, Path], registry: dict, query: str, source: Path) -> None:
    matches = select_entries(query, registry, location["config"])
    if len(matches) != 1:
        raise SystemExit("set-source requires one exact registry entry; use its ID")
    item = matches[0]
    source = source.expanduser().resolve()
    if source == Path(item["disabled_path"]).expanduser().resolve():
        raise SystemExit("source path cannot equal disabled path")
    item["source_path"] = str(source)
    item["source_confidence"] = "manual"
    registry["updated_at"] = iso_now()
    save_registry(location["registry"], registry)
    print(f"updated {item['id']} source_path={source}")


def report_item(item: dict, observed_state: str) -> dict:
    return {
        "id": item["id"],
        "name": item.get("display_name") or item["id"],
        "kind": item.get("kind"),
        "state": observed_state,
        "source_path": item.get("source_path"),
        "disabled_path": item.get("disabled_path"),
        "toggle": f"skill-toggle on {item.get('display_name') or item['id']}",
    }


def build_context_report(location: dict[str, Path], registry: dict) -> dict:
    expected_active: list[dict] = []
    disabled_ready: list[dict] = []
    active_bundles: list[dict] = []
    disabled_bundles: list[dict] = []
    collisions: list[dict] = []
    for item in registry.get("entries", []):
        observed = entry_status(item, location["config"])
        rendered = report_item(item, observed)
        if observed == "collision":
            collisions.append(rendered)
        elif item.get("kind") == "plugin_bundle":
            if observed == "enabled":
                active_bundles.append(rendered)
            elif observed == "disabled":
                disabled_bundles.append(rendered)
        elif observed == "enabled":
            expected_active.append(rendered)
        elif observed == "disabled":
            disabled_ready.append(rendered)
    for collection in (expected_active, disabled_ready, active_bundles, disabled_bundles, collisions):
        collection.sort(key=lambda item: item["name"].casefold())
    return {
        "runtime_injection": {
            "status": "not-directly-introspectable",
            "note": (
                "st can read your files and config, but Codex loads its hidden skill list before st starts. "
                "This is the expected local view; open a fresh task to compare the live list."
            ),
        },
        "expected_active": expected_active,
        "disabled_ready": disabled_ready,
        "active_bundles": active_bundles,
        "disabled_bundles": disabled_bundles,
        "collisions": collisions,
        "entries": context_targets(location, registry),
    }


def reconcile(location: dict[str, Path], registry: dict) -> tuple[Path | None, list[dict]]:
    candidates: list[dict] = []
    for item in registry.get("entries", []):
        if entry_status(item, location["config"]) != "collision":
            continue
        if item.get("kind") == "plugin_bundle":
            desired = config_plugin_state_for_source(
                location["config"],
                Path(item["source_path"]),
                item.get("plugin_name") or "",
            )
        elif item.get("kind") == "local_skill":
            desired = config_skill_state(
                location["config"],
                set(),
                {item["source_path"]},
            )
        else:
            desired = None
        if desired == "disabled":
            candidates.append(item)
    if not candidates:
        return None, []
    backup = config_backup(location["config"], location["home"])
    quarantined: list[dict] = []
    for item in candidates:
        archived = move_entry(item, "disable")
        if archived:
            quarantined.append({"id": item["id"], "archived_path": str(archived)})
        item["state"] = "disabled"
    registry["updated_at"] = iso_now()
    save_registry(location["registry"], registry)
    receipt = write_operation_receipt(location, "reconcile", candidates, backup, quarantined)
    return receipt, candidates


def format_context_report(report: dict) -> str:
    lines = [
        "NUMBERED INVENTORY (use the position with st on/off/note)",
        "#.)  STATUS     KIND             NAME",
    ]
    for target in report.get("entries", []):
        lines.append(
            f"{target['position']:>3}.)  {target['state']:<9}  {target['kind']:<16}  {target['name']}"
        )
    lines.extend([
        "",
        f"NOTE: {report['runtime_injection']['note']}",
        "JSON: add --json for machine-readable positions, states, IDs, and paths.",
    ])
    return "\n".join(lines) + "\n"


def write_context_snapshot(location: dict[str, Path], report: dict) -> Path:
    location["reports"].mkdir(parents=True, exist_ok=True)
    snapshot = location["reports"] / f"context-{now_stamp()}.txt"
    snapshot.write_text(format_context_report(report), encoding="utf-8")
    return snapshot


def notify_context(location: dict[str, Path], report: dict) -> Path:
    snapshot = write_context_snapshot(location, report)
    active_names = [item["name"] for item in report["expected_active"]]
    preview = ", ".join(active_names[:8])
    if len(active_names) > 8:
        preview += f", +{len(active_names) - 8} more"
    message = (
        f"Expected active skills: {len(active_names)}. "
        f"Disabled-ready: {len(report['disabled_ready'])}. "
        f"{preview}. Full report: {snapshot}"
    )
    script = f'display notification {json.dumps(message)} with title "Codex skill report"'
    subprocess.run(["/usr/bin/osascript", "-e", script], check=False)
    return snapshot


def prepare_notifier(location: dict[str, Path]) -> tuple[Path, Path]:
    location["notifier_script"].parent.mkdir(parents=True, exist_ok=True)
    location["notifier_plist"].parent.mkdir(parents=True, exist_ok=True)
    home = location["home"]
    script = f'''#!/bin/zsh
set -u
codex_home={json.dumps(str(home))}
toggle="${{codex_home}}/skills-disabled/bin/skill-toggle.py"
state="${{codex_home}}/skills-disabled/reports/.codex-running"
while true; do
  if /usr/bin/pgrep -x Codex >/dev/null 2>&1; then
    if [[ ! -e "$state" ]]; then
      /usr/bin/env python3 "$toggle" --codex-home "$codex_home" notify >/dev/null 2>&1 || true
      /usr/bin/touch "$state"
    fi
  else
    /bin/rm -f "$state"
  fi
  /bin/sleep 3
done
'''
    plist = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.davejski.codex-skill-toggle-notifier</string>
  <key>ProgramArguments</key>
  <array><string>/bin/zsh</string><string>{location["notifier_script"]}</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>5</integer>
</dict>
</plist>
'''
    location["notifier_script"].write_text(script, encoding="utf-8")
    location["notifier_script"].chmod(0o755)
    location["notifier_plist"].write_text(plist, encoding="utf-8")
    return location["notifier_script"], location["notifier_plist"]


def print_entries(
    entries: list[dict],
    as_json: bool,
    color: bool = False,
    config_path: Path | None = None,
    total_label: str = "RAW ENTRIES",
) -> None:
    rendered = []
    for item in entries:
        rendered.append({**item, "observed_state": entry_status(item, config_path)})
    if as_json:
        print(json.dumps(rendered, indent=2))
        return
    lines = []
    ordered_states = sorted(
        {item["observed_state"] for item in rendered},
        key=lambda state: STATE_ORDER.get(state, 99),
    )
    for state in ordered_states:
        state_items = sorted(
            (item for item in rendered if item["observed_state"] == state),
            key=lambda item: ((item["display_name"] or item["id"]).casefold(), item["id"]),
        )
        state_color = STATE_COLORS.get(state, ANSI_RED)
        label = f"{STATE_LABELS.get(state, state.upper())} ({len(state_items)})"
        lines.append(colorize(label, ANSI_BOLD + state_color, color))
        for item in state_items:
            lines.append(
                f"  {colorize(f'{state:<9}', state_color, color)}  "
                f"{item['display_name'] or item['id']}  ({item['id']})"
            )
    counts = Counter(item["observed_state"] for item in rendered)
    lines.extend([
        "",
        colorize(f"TOTAL {total_label}: {len(rendered)}", ANSI_BOLD + ANSI_CYAN, color),
        (
            f"ENABLED: {counts.get('enabled', 0)}  |  "
            f"DISABLED: {counts.get('disabled', 0)}  |  "
            f"COLLISIONS: {counts.get('collision', 0)}"
        ),
        "Use st context for numbered actions.",
    ])
    print("\n".join(format_box("SKILL TOGGLE RESULTS", lines or ["no matches"], color)))


def print_context_report(report: dict, as_json: bool, color: bool = False) -> None:
    if as_json:
        print(json.dumps(report, indent=2))
        return
    runtime = report["runtime_injection"]
    lines = [
        "#.)  STATUS     KIND             NAME",
    ]
    for target in report.get("entries", []):
        state = target["state"]
        state_color = {
            "enabled": ANSI_GREEN,
            "disabled": ANSI_YELLOW,
            "mixed": ANSI_YELLOW,
            "collision": ANSI_RED,
            "missing": ANSI_RED,
        }.get(state, ANSI_RED)
        status = colorize(f"{state:<9}", state_color, color)
        lines.append(f"{target['position']:>3}.)  {status}  {target['kind']:<16}  {target['name']}")
    lines.extend([
        "",
        "Use the number: st --dry-run off 12, then st off 12.",
        "Use --yes for a non-interactive confirmed change; --json is machine-readable.",
    ])
    print("\n".join(format_box("CODEX SKILL TOGGLE", lines, color)))
    print(f"NOTE: {runtime['note']}")


def print_notes(notes: list[dict], as_json: bool, color: bool = False) -> None:
    if as_json:
        print(json.dumps(notes, indent=2))
        return
    if not notes:
        print("\n".join(format_box("SKILL NOTES", ["no notes"], color)))
        return
    lines = []
    for note in notes:
        lines.extend([
            f"{note['target_name']} [{note['target_kind']}]  {note['id']}",
            f"  {note['text']}",
            f"  updated: {note['updated_at']}",
        ])
    print("\n".join(format_box("SKILL NOTES", lines, color)))


def print_operation_plan(lines: list[str], color: bool) -> None:
    rendered = []
    for line in lines:
        if line.startswith("ACTION:"):
            rendered.append(colorize(line, ANSI_BOLD + ANSI_CYAN, color))
        elif line.startswith("WARNING:"):
            rendered.append(colorize(line, ANSI_RED, color))
        elif line.startswith("RESULT:"):
            rendered.append(colorize(line, ANSI_GREEN, color))
        else:
            rendered.append(line)
    print("\n".join(format_box("PLANNED SKILL CHANGE", rendered, color)))


def confirm_operation(lines: list[str], *, assume_yes: bool, dry_run: bool, color: bool) -> bool:
    print_operation_plan(lines, color)
    if dry_run:
        print("DRY RUN: no changes made")
        return False
    if assume_yes:
        return True
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("Refusing an unconfirmed change without a terminal. Re-run with --yes or --dry-run.", file=sys.stderr)
        raise SystemExit(2)
    try:
        answer = input("Proceed? [y/N] ").strip().casefold()
    except EOFError:
        answer = ""
    if answer not in {"y", "yes"}:
        print("Cancelled; no changes made.")
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    parser.add_argument("--json", action="store_true", help="emit JSON for list/find/verify")
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto", help="color human output")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt for a toggle")
    parser.add_argument("--dry-run", action="store_true", help="show a toggle plan without changing anything")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="build the registry from receipts and disabled folders")
    subparsers.add_parser("list", help="list every registered entry")
    subparsers.add_parser("context", aliases=["report"], help="show expected active skills and disabled-ready inventory")
    subparsers.add_parser("ui", help="open the optional Textual dashboard")
    subparsers.add_parser("reconcile", help="quarantine rehydrated copies of configured-disabled bundles")
    subparsers.add_parser("notify", help="save the context report and send a macOS notification")
    subparsers.add_parser("prepare-notifier", help="write, but do not arm, the app-restart notifier")
    note_parser = subparsers.add_parser("note", help="attach a note to a skill, plugin, registry ID, or path")
    note_parser.add_argument("query")
    note_parser.add_argument("text", nargs="+")
    notes_parser = subparsers.add_parser("notes", help="list notes, optionally filtered by skill or plugin")
    notes_parser.add_argument("query", nargs="?")
    delete_note_parser = subparsers.add_parser("delete-note", help="delete a note by note ID")
    delete_note_parser.add_argument("note_id")
    find_parser = subparsers.add_parser("find", help="find by plugin, skill, alias, or ID")
    find_parser.add_argument("query")
    verify_parser = subparsers.add_parser("verify", help="check path and registry consistency")
    verify_parser.add_argument("--strict", action="store_true")
    source_parser = subparsers.add_parser("set-source", help="record an exact restore path for one entry")
    source_parser.add_argument("query")
    source_parser.add_argument("source", type=Path)
    for command, aliases in (("enable", ["on"]), ("disable", ["off"])):
        command_parser = subparsers.add_parser(command, aliases=aliases, help=f"{command} a skill or plugin bundle")
        command_parser.add_argument("query")
        command_parser.add_argument("--yes", action="store_true", default=argparse.SUPPRESS, help="skip the confirmation prompt")
        command_parser.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS, help="show the plan without changing anything")

    args = parser.parse_args(argv)
    location = paths(args.codex_home.expanduser().resolve())
    color = should_color(args.color, args.json)
    if args.command == "init":
        registry = seed_registry(location["home"])
        save_registry(location["registry"], registry)
        print(f"wrote {location['registry']} with {len(registry['entries'])} entries")
        return 0

    if not location["registry"].exists():
        raise SystemExit(f"registry missing: run {Path(__file__).name} init")
    registry = load_registry(location["registry"])
    if args.command == "ui":
        from skill_toggle_ui import run_ui

        return run_ui(location, registry, build_context_report, load_registry)
    if args.command == "list":
        print_entries(registry["entries"], args.json, color, location["config"], "RAW ENTRIES")
        return 0
    if args.command == "find":
        print_entries(resolve(args.query, registry), args.json, color, location["config"], "MATCHES")
        return 0
    if args.command == "note":
        note = add_note(location, registry, args.query, " ".join(args.text))
        print(f"note added: {note['id']} -> {note['target_name']}")
        return 0
    if args.command == "notes":
        print_notes(list_notes(location, registry, args.query), args.json, color)
        return 0
    if args.command == "delete-note":
        if not delete_note(location, args.note_id):
            raise SystemExit(f"note not found: {args.note_id}")
        print(f"note deleted: {args.note_id}")
        return 0
    if args.command in {"context", "report"}:
        print_context_report(build_context_report(location, registry), args.json, color)
        return 0
    if args.command == "reconcile":
        receipt, repaired = reconcile(location, registry)
        if receipt is None:
            print("reconcile: no disabled collisions needing repair")
        else:
            print(f"reconcile: repaired {len(repaired)} collision(s); receipt {receipt}")
        return 0
    if args.command == "notify":
        snapshot = notify_context(location, build_context_report(location, registry))
        print(f"notification sent; full report {snapshot}")
        return 0
    if args.command == "prepare-notifier":
        script, plist = prepare_notifier(location)
        print(f"prepared notifier script {script}")
        print(f"prepared LaunchAgent template {plist}")
        print("not armed; load it only after reviewing the template")
        return 0
    if args.command == "verify":
        errors = verify_registry(registry)
        if args.json:
            print(json.dumps({"ok": not errors, "errors": errors}, indent=2))
        elif errors:
            print("\n".join(errors))
        else:
            print("OK")
        return 1 if errors else 0
    if args.command == "set-source":
        set_source(location, registry, args.query, args.source)
        return 0
    operation = {"on": "enable", "off": "disable"}.get(args.command, args.command)
    plan = operation_plan(location, registry, args.query, operation)
    if not confirm_operation(plan, assume_yes=args.yes, dry_run=args.dry_run, color=color):
        return 0
    receipt = operate(location, registry, args.query, operation)
    result = f"{operation}: no change needed" if receipt is None else f"{operation}: receipt {receipt}"
    print("\n".join(format_box("SKILL TOGGLE RESULT", [colorize(result, ANSI_GREEN, color)], color)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileExistsError, FileNotFoundError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
