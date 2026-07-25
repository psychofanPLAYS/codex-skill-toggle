import importlib.util
import io
import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory


SCRIPT = Path(__file__).parents[1] / "src" / "skill_toggle.py"
SPEC = importlib.util.spec_from_file_location("skill_toggle", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)

UI_SCRIPT = Path(__file__).parents[1] / "src" / "skill_toggle_ui.py"
UI_SPEC = importlib.util.spec_from_file_location("skill_toggle_ui", UI_SCRIPT)
ui = importlib.util.module_from_spec(UI_SPEC)
assert UI_SPEC.loader is not None
UI_SPEC.loader.exec_module(ui)


def make_entry(tmp_path, *, name="canva", skill_names=None):
    source = tmp_path / "active" / name
    disabled = tmp_path / "disabled" / name
    return {
        "id": f"plugin:{name}:test",
        "kind": "plugin_bundle",
        "display_name": name,
        "plugin_name": name,
        "plugin_ids": [f"{name}@openai-curated"],
        "skill_names": skill_names or [f"{name}:brand-check"],
        "aliases": [name, f"{name}:brand-check"],
        "source_path": str(source),
        "disabled_path": str(disabled),
        "state": "disabled",
        "source_confidence": "observed",
    }


class SkillToggleTests(unittest.TestCase):
    def test_ui_rows_sort_collision_disabled_enabled_then_name(self):
        report = {"entries": [
            {"position": 1, "state": "enabled", "kind": "local_skill", "name": "zeta", "entry_count": 1, "ids": ["z"]},
            {"position": 2, "state": "collision", "kind": "plugin_bundle", "name": "beta", "entry_count": 2, "ids": ["b1", "b2"]},
            {"position": 3, "state": "disabled", "kind": "plugin_skill", "name": "alpha", "entry_count": 1, "ids": ["a"]},
        ]}

        rows = ui.build_rows(report)

        self.assertEqual([row["name"] for row in rows], ["beta", "alpha", "zeta"])
        self.assertEqual([row["position"] for row in rows], [1, 2, 3])

    def test_resolve_exact_skill_returns_all_copies(self):
        with TemporaryDirectory() as temp:
            tmp_path = Path(temp)
            registry = {"version": 1, "entries": [
                make_entry(tmp_path / "one"),
                make_entry(tmp_path / "two"),
            ]}

            matches = module.resolve("canva:brand-check", registry)

            self.assertEqual(len(matches), 2)

    def test_resolve_unknown_query_is_empty(self):
        with TemporaryDirectory() as temp:
            registry = {"version": 1, "entries": [make_entry(Path(temp))]}

            self.assertEqual(module.resolve("does-not-exist", registry), [])

    def test_move_entry_round_trip(self):
        with TemporaryDirectory() as temp:
            item = make_entry(Path(temp))
            source = Path(item["source_path"])
            disabled = Path(item["disabled_path"])
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text("name: canva:brand-check\n", encoding="utf-8")

            module.move_entry(item, "disable")
            self.assertFalse(source.exists())
            self.assertTrue((disabled / "SKILL.md").exists())

            item["state"] = "disabled"
            module.move_entry(item, "enable")
            self.assertTrue(source.joinpath("SKILL.md").exists())
            self.assertFalse(disabled.exists())

    def test_verify_rejects_source_and_disabled_paths_both_present(self):
        with TemporaryDirectory() as temp:
            item = make_entry(Path(temp))
            Path(item["source_path"]).mkdir(parents=True)
            Path(item["disabled_path"]).mkdir(parents=True)

            errors = module.verify_registry({"version": 1, "entries": [item]})

            self.assertTrue(any("both source and disabled" in error for error in errors))

    def test_update_config_adds_missing_plugin_toggle(self):
        with TemporaryDirectory() as temp:
            config = Path(temp) / "config.toml"
            config.write_text("model = \"test\"\n", encoding="utf-8")
            item = make_entry(Path(temp))

            module.update_config(config, [item], False)

            text = config.read_text(encoding="utf-8")
            self.assertIn('[plugins."canva@openai-curated"]', text)
            self.assertIn("enabled = false", text)

    def test_operate_enable_and_disable_round_trip(self):
        with TemporaryDirectory() as temp:
            home = Path(temp) / "codex"
            source = home / "plugins" / "cache" / "openai-curated" / "canva"
            disabled = home / "skills-disabled" / "plugins" / "cache" / "openai-curated" / "canva"
            config = home / "config.toml"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text("model = \"test\"\n", encoding="utf-8")
            item = make_entry(home)
            item.update({"source_path": str(source), "disabled_path": str(disabled)})
            registry = {"version": 1, "entries": [item]}
            location = module.paths(home)
            location["registry"].parent.mkdir(parents=True, exist_ok=True)
            module.save_registry(location["registry"], registry)

            disabled.mkdir(parents=True)
            (disabled / "marker").write_text("bundle", encoding="utf-8")
            receipt = module.operate(location, registry, "canva", "enable")

            self.assertTrue(receipt and receipt.exists())
            self.assertTrue((source / "marker").exists())
            self.assertIn("enabled = true", config.read_text(encoding="utf-8"))

            module.operate(location, registry, "canva", "disable")
            self.assertTrue((disabled / "marker").exists())
            self.assertIn("enabled = false", config.read_text(encoding="utf-8"))

    def test_disable_quarantines_a_rehydrated_collision(self):
        with TemporaryDirectory() as temp:
            home = Path(temp) / "codex"
            source = home / "plugins" / "cache" / "openai-curated" / "canva"
            disabled = home / "skills-disabled" / "plugins" / "cache" / "openai-curated" / "canva"
            config = home / "config.toml"
            source.mkdir(parents=True)
            disabled.mkdir(parents=True)
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text('[plugins."canva@openai-curated"]\nenabled = true\n', encoding="utf-8")
            item = make_entry(home)
            item.update({"source_path": str(source), "disabled_path": str(disabled)})
            registry = {"version": 1, "entries": [item]}
            location = module.paths(home)
            location["registry"].parent.mkdir(parents=True, exist_ok=True)
            module.save_registry(location["registry"], registry)
            (source / "active").write_text("new", encoding="utf-8")
            (disabled / "archived").write_text("old", encoding="utf-8")

            receipt = module.operate(location, registry, "canva", "disable")

            self.assertTrue(receipt and receipt.exists())
            self.assertTrue((disabled / "active").exists())
            self.assertFalse(source.exists())
            self.assertTrue(list((home / "skills-disabled" / "reappeared").rglob("archived")))

    def test_seed_registry_indexes_active_local_skill(self):
        with TemporaryDirectory() as temp:
            home = Path(temp) / "codex"
            active = home / "skills" / "model-search"
            active.mkdir(parents=True)
            (active / "SKILL.md").write_text(
                "---\nname: model-search\ndescription: Use when searching models\n---\n",
                encoding="utf-8",
            )

            registry = module.seed_registry(home)

            matches = module.resolve("model-search", registry)
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["state"], "enabled")
            self.assertTrue(matches[0]["disabled_path"].startswith(str(home / "skills-disabled")))

    def test_seed_registry_indexes_active_plugin_bundle(self):
        with TemporaryDirectory() as temp:
            home = Path(temp) / "codex"
            plugin = home / "plugins" / "cache" / "example" / "demo" / "1.0.0"
            plugin.mkdir(parents=True)
            (plugin / ".codex-plugin").mkdir()
            (plugin / ".codex-plugin" / "plugin.json").write_text(
                '{"name":"demo","version":"1.0.0","skills":"./skills"}\n',
                encoding="utf-8",
            )
            (plugin / "skills").mkdir()
            (plugin / "skills" / "demo-skill").mkdir()
            (plugin / "skills" / "demo-skill" / "SKILL.md").write_text(
                "---\nname: demo-skill\ndescription: Use when testing\n---\n",
                encoding="utf-8",
            )

            registry = module.seed_registry(home)

            matches = module.resolve("demo:demo-skill", registry)
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["state"], "enabled")

    def test_plugin_skill_toggle_uses_config_without_moving_bundle(self):
        with TemporaryDirectory() as temp:
            home = Path(temp) / "codex"
            config = home / "config.toml"
            config.parent.mkdir(parents=True)
            config.write_text(
                '[plugins."hugging-face@openai-curated"]\nenabled = true\n',
                encoding="utf-8",
            )
            skill_dir = home / "plugins" / "cache" / "openai-curated" / "hugging-face" / "1.0.0" / "skills" / "huggingface-llm-trainer"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: huggingface-llm-trainer\ndescription: Use when training\n---\n",
                encoding="utf-8",
            )
            item = {
                "id": "plugin-skill:hf-trainer",
                "kind": "plugin_skill",
                "toggle_mode": "config",
                "display_name": "hugging-face:huggingface-llm-trainer",
                "plugin_name": "hugging-face",
                "plugin_ids": ["hugging-face@openai-curated"],
                "skill_names": ["hugging-face:huggingface-llm-trainer"],
                "config_names": ["hugging-face:huggingface-llm-trainer"],
                "aliases": ["huggingface-llm-trainer"],
                "source_path": str(skill_dir),
                "disabled_path": str(home / "skills-disabled" / "config-only" / "huggingface-llm-trainer"),
                "state": "enabled",
                "source_confidence": "observed",
            }
            registry = {"version": 1, "entries": [item]}
            location = module.paths(home)
            location["registry"].parent.mkdir(parents=True, exist_ok=True)
            module.save_registry(location["registry"], registry)

            module.operate(location, registry, "hugging-face:huggingface-llm-trainer", "disable")

            self.assertTrue(skill_dir.exists())
            self.assertIn('name = "hugging-face:huggingface-llm-trainer"', config.read_text(encoding="utf-8"))
            self.assertIn("enabled = false", config.read_text(encoding="utf-8"))

    def test_seed_registry_prefers_exactly_enabled_plugin_variant(self):
        with TemporaryDirectory() as temp:
            home = Path(temp) / "codex"
            config = home / "config.toml"
            config.parent.mkdir(parents=True)
            config.write_text(
                '[plugins."hugging-face@chatgpt-global"]\nenabled = false\n'
                '[plugins."hugging-face@openai-curated"]\nenabled = true\n',
                encoding="utf-8",
            )
            for marketplace in ("chatgpt-global", "openai-curated"):
                plugin = home / "plugins" / "cache" / marketplace / "hugging-face" / "1.0.0"
                (plugin / ".codex-plugin").mkdir(parents=True)
                (plugin / ".codex-plugin" / "plugin.json").write_text(
                    '{"name":"hugging-face","version":"1.0.0"}\n',
                    encoding="utf-8",
                )
                skill = plugin / "skills" / "llm-trainer"
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(
                    "---\nname: huggingface-llm-trainer\ndescription: Use when training\n---\n",
                    encoding="utf-8",
                )

            registry = module.seed_registry(home)
            matches = module.resolve("hugging-face:huggingface-llm-trainer", registry)

            self.assertEqual(len(matches), 1)
            self.assertIn("openai-curated", matches[0]["source_path"])

    def test_active_skill_wrapper_exists_and_points_to_toggle_script(self):
        wrapper = Path(__file__).parents[1] / "bin" / "skill-toggle"

        self.assertTrue(wrapper.exists())
        text = wrapper.read_text(encoding="utf-8")
        self.assertIn("src/skill_toggle.py", text)

    def test_operation_receipts_never_overwrite_same_second(self):
        with TemporaryDirectory() as temp:
            location = module.paths(Path(temp) / "codex")
            item = make_entry(Path(temp))
            with patch.object(module, "now_stamp", return_value="fixed-stamp"):
                first = module.write_operation_receipt(location, "disable", [item], None)
                second = module.write_operation_receipt(location, "disable", [item], None)

            self.assertNotEqual(first, second)
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())

    def test_context_report_separates_expected_active_and_disabled_ready(self):
        with TemporaryDirectory() as temp:
            home = Path(temp) / "codex"
            active_source = home / "skills" / "active-skill"
            active_source.mkdir(parents=True)
            (active_source / "SKILL.md").write_text("name: active-skill\n", encoding="utf-8")
            disabled_source = home / "skills-disabled" / "local" / "disabled-skill"
            disabled_source.mkdir(parents=True)
            (disabled_source / "SKILL.md").write_text("name: disabled-skill\n", encoding="utf-8")
            registry = {
                "version": 1,
                "entries": [
                    {
                        "id": "local:active",
                        "kind": "local_skill",
                        "display_name": "active-skill",
                        "aliases": ["active-skill"],
                        "source_path": str(active_source),
                        "disabled_path": str(home / "skills-disabled" / "local" / "active-skill"),
                        "state": "enabled",
                    },
                    {
                        "id": "local:disabled",
                        "kind": "local_skill",
                        "display_name": "disabled-skill",
                        "aliases": ["disabled-skill"],
                        "source_path": str(home / "skills" / "disabled-skill"),
                        "disabled_path": str(disabled_source),
                        "state": "disabled",
                    },
                ],
            }

            report = module.build_context_report(module.paths(home), registry)

            self.assertEqual([item["name"] for item in report["expected_active"]], ["active-skill"])
            self.assertEqual([item["name"] for item in report["disabled_ready"]], ["disabled-skill"])
            self.assertEqual(report["runtime_injection"]["status"], "not-directly-introspectable")

    def test_reconcile_repairs_disabled_bundle_collision(self):
        with TemporaryDirectory() as temp:
            home = Path(temp) / "codex"
            source = home / "plugins" / "cache" / "openai-curated" / "canva"
            disabled = home / "skills-disabled" / "plugins" / "cache" / "openai-curated" / "canva"
            source.mkdir(parents=True)
            disabled.mkdir(parents=True)
            (source / "new").write_text("new", encoding="utf-8")
            (disabled / "old").write_text("old", encoding="utf-8")
            config = home / "config.toml"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text('[plugins."canva@openai-curated"]\nenabled = false\n', encoding="utf-8")
            item = make_entry(home)
            item.update({"source_path": str(source), "disabled_path": str(disabled)})
            registry = {"version": 1, "entries": [item]}
            location = module.paths(home)
            location["registry"].parent.mkdir(parents=True, exist_ok=True)
            module.save_registry(location["registry"], registry)

            receipt, repaired = module.reconcile(location, registry)

            self.assertTrue(receipt and receipt.exists())
            self.assertEqual(len(repaired), 1)
            self.assertFalse(source.exists())
            self.assertTrue((disabled / "new").exists())
            self.assertTrue(list((home / "skills-disabled" / "reappeared").rglob("old")))

    def test_prepare_notifier_writes_reviewable_unarmed_files(self):
        with TemporaryDirectory() as temp:
            home = Path(temp) / "codex"
            location = module.paths(home)

            script, plist = module.prepare_notifier(location)

            self.assertTrue(script.exists())
            self.assertTrue(plist.exists())
            self.assertTrue(script.stat().st_mode & 0o111)
            self.assertIn("pgrep -x Codex", script.read_text(encoding="utf-8"))
            self.assertIn("com.davejski.codex-skill-toggle-notifier", plist.read_text(encoding="utf-8"))

    def test_reconcile_repairs_staging_copy_using_plugin_name_fallback(self):
        with TemporaryDirectory() as temp:
            home = Path(temp) / "codex"
            source = home / ".tmp" / "plugins" / "plugins" / "google-drive"
            disabled = home / "skills-disabled" / "plugins" / "tmp" / "google-drive"
            source.mkdir(parents=True)
            disabled.mkdir(parents=True)
            (source / "new").write_text("new", encoding="utf-8")
            (disabled / "old").write_text("old", encoding="utf-8")
            config = home / "config.toml"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text('[plugins."google-drive@openai-curated"]\nenabled = false\n', encoding="utf-8")
            item = make_entry(home, name="google-drive")
            item.update({"source_path": str(source), "disabled_path": str(disabled)})
            registry = {"version": 1, "entries": [item]}
            location = module.paths(home)
            location["registry"].parent.mkdir(parents=True, exist_ok=True)
            module.save_registry(location["registry"], registry)

            module.reconcile(location, registry)

            self.assertFalse(source.exists())
            self.assertTrue((disabled / "new").exists())

    def test_note_attaches_to_logical_skill_across_registry_refresh(self):
        with TemporaryDirectory() as temp:
            home = Path(temp) / "codex"
            source = home / "skills" / "model-search"
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text("name: model-search\n", encoding="utf-8")
            item = {
                "id": "local:old",
                "kind": "local_skill",
                "display_name": "model-search",
                "aliases": ["model-search"],
                "source_path": str(source),
                "disabled_path": str(home / "skills-disabled" / "local" / "model-search"),
                "state": "enabled",
            }
            registry = {"version": 1, "entries": [item]}
            location = module.paths(home)
            location["registry"].parent.mkdir(parents=True, exist_ok=True)
            module.save_registry(location["registry"], registry)

            note = module.add_note(location, registry, "model-search", "Keep model and dataset lookup read-only.")
            refreshed = {"version": 1, "entries": [{**item, "id": "local:new"}]}
            notes = module.list_notes(location, refreshed, "model-search")

            self.assertTrue(note["id"].startswith("note:"))
            self.assertEqual(len(notes), 1)
            self.assertEqual(notes[0]["text"], "Keep model and dataset lookup read-only.")

    def test_note_can_attach_to_a_plugin_position_and_list_all_notes(self):
        with TemporaryDirectory() as temp:
            home = Path(temp) / "codex"
            item = make_entry(home, name="canva")
            registry = {"version": 1, "entries": [item]}
            location = module.paths(home)
            location["registry"].parent.mkdir(parents=True, exist_ok=True)
            module.save_registry(location["registry"], registry)

            module.add_note(location, registry, item["id"], "Disabled by policy; restore only on request.")

            notes = module.list_notes(location, registry)

            self.assertEqual(len(notes), 1)
            self.assertEqual(notes[0]["target_name"], "canva")

    def test_box_formatter_adds_color_only_when_requested(self):
        plain = module.format_box("CODEX SKILL TOGGLE", ["enabled  model-search"], color=False)
        colored = module.format_box("CODEX SKILL TOGGLE", ["enabled  model-search"], color=True)

        self.assertTrue(plain[0].startswith("┌"))
        self.assertTrue(plain[-1].startswith("└"))
        self.assertNotIn("\033[", "\n".join(plain))
        self.assertIn("\033[", "\n".join(colored))

    def test_context_snapshot_uses_human_positions_and_plain_note(self):
        report = {
            "runtime_injection": {"status": "not-directly-introspectable", "note": "simple explanation"},
            "entries": [{"position": 1, "state": "enabled", "kind": "local_skill", "name": "model-search"}],
        }

        text = module.format_context_report(report)

        self.assertIn("1.)  enabled", text)
        self.assertIn("NOTE: simple explanation", text)

    def test_color_mode_auto_disables_when_output_is_not_a_tty(self):
        with patch.object(module.sys.stdout, "isatty", return_value=False):
            self.assertFalse(module.should_color("auto", False))
        self.assertTrue(module.should_color("always", False))
        self.assertFalse(module.should_color("never", False))

    def test_context_targets_combine_states_and_assign_positions(self):
        with TemporaryDirectory() as temp:
            home = Path(temp) / "codex"
            active = home / "skills" / "active-skill"
            disabled = home / "skills-disabled" / "local" / "disabled-skill"
            active.mkdir(parents=True)
            disabled.mkdir(parents=True)
            (active / "SKILL.md").write_text("name: active-skill\n", encoding="utf-8")
            (disabled / "SKILL.md").write_text("name: disabled-skill\n", encoding="utf-8")
            registry = {"version": 1, "entries": [
                {"id": "local:active", "kind": "local_skill", "display_name": "active-skill", "aliases": ["active-skill"], "source_path": str(active), "disabled_path": str(home / "skills-disabled" / "local" / "active-skill"), "state": "enabled"},
                {"id": "local:disabled", "kind": "local_skill", "display_name": "disabled-skill", "aliases": ["disabled-skill"], "source_path": str(home / "skills" / "disabled-skill"), "disabled_path": str(disabled), "state": "disabled"},
            ]}

            targets = module.context_targets(module.paths(home), registry)

            self.assertEqual([target["position"] for target in targets], [1, 2])
            self.assertEqual({target["state"] for target in targets}, {"enabled", "disabled"})
            self.assertEqual(module.select_entries("2", registry)[0]["display_name"], "disabled-skill")

    def test_print_entries_groups_states_and_prints_totals(self):
        with TemporaryDirectory() as temp:
            home = Path(temp)
            items = []
            for name, state in (("z-enabled", "enabled"), ("a-disabled", "disabled"), ("m-collision", "collision")):
                item = make_entry(home, name=name)
                source = Path(item["source_path"])
                disabled = Path(item["disabled_path"])
                if state in {"enabled", "collision"}:
                    source.mkdir(parents=True)
                if state in {"disabled", "collision"}:
                    disabled.mkdir(parents=True)
                items.append(item)
            output = io.StringIO()
            with patch("sys.stdout", output):
                module.print_entries(items, False, False)

            text = output.getvalue()
            self.assertLess(text.index("COLLISIONS (1)"), text.index("DISABLED (1)"))
            self.assertLess(text.index("DISABLED (1)"), text.index("ENABLED (1)"))
            self.assertIn("TOTAL RAW ENTRIES: 3", text)
            self.assertIn("Use st context for numbered actions", text)
            self.assertNotIn("1.)", text)

    def test_operation_plan_explains_file_and_config_actions(self):
        with TemporaryDirectory() as temp:
            home = Path(temp) / "codex"
            source = home / "plugins" / "cache" / "openai-curated" / "canva"
            disabled = home / "skills-disabled" / "plugins" / "cache" / "openai-curated" / "canva"
            source.mkdir(parents=True)
            (source / "marker").write_text("bundle", encoding="utf-8")
            item = make_entry(home)
            item.update({"source_path": str(source), "disabled_path": str(disabled)})
            registry = {"version": 1, "entries": [item]}
            location = module.paths(home)

            plan = module.operation_plan(location, registry, item["id"], "disable")

            self.assertIn("disable", " ".join(plan).lower())
            self.assertIn(str(source), "\n".join(plan))
            self.assertIn(str(disabled), "\n".join(plan))


if __name__ == "__main__":
    unittest.main()
