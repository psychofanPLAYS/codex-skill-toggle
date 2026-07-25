# codex-skill-toggle

`codex-skill-toggle` is a small safety tool for managing the skills and plugins that Codex can use.

It gives you a simple way to turn a skill or plugin off without deleting it. When something is disabled, the tool keeps it recoverable, records where it came from, and leaves a receipt explaining what happened.

## In plain English

- A **skill** is a set of instructions that teaches Codex how to handle a type of work.
- A **plugin** is a package that may contain one or more skills.
- **Enable** means make a skill or plugin available again.
- **Disable** means hide it from the active Codex setup while keeping it recoverable.
- A **receipt** is a small record of the change, including the original path.

This tool manages local files and Codex configuration. It does not delete disabled skills, publish your private Codex settings, or upload your disabled plugin store.

## What it can do

- Turn local skills or complete plugin bundles on and off.
- Turn one skill inside a plugin on or off through Codex configuration.
- Show the expected active skills and disabled skills ready to restore.
- Show enabled and disabled entries together in one numbered, grouped inventory.
- Preview every toggle as a detailed plan, then require a quick confirmation before changing anything.
- Detect when Codex recreates a disabled plugin and quarantine the copy safely.
- Attach notes to a skill, plugin, registry ID, or path.
- Save receipts and original paths for recovery.
- Prepare an optional macOS notification when Codex starts.

`st` can read your local files and config, but Codex loads the hidden skill list before `st` starts. The `context` report is therefore the expected local view; open a fresh Codex task when you need to compare the live list.

## Requirements

- Python 3.11 or newer
- Codex Desktop or Codex CLI
- macOS for the notification feature

The standard commands use only Python's standard library. The optional
dashboard uses [Textual](https://textual.textualize.io/); install it only if
you want the dashboard:

```sh
python3 -m pip install -r requirements-ui.txt
```

## Install

Clone the repository, then make the command available in your shell:

```sh
git clone https://github.com/psychofanPLAYS/codex-skill-toggle.git
cd codex-skill-toggle
mkdir -p "$HOME/.local/bin"
ln -sfn "$PWD/bin/skill-toggle" "$HOME/.local/bin/skill-toggle"
ln -sfn "$PWD/bin/st" "$HOME/.local/bin/st"
export PATH="$HOME/.local/bin:$PATH"
```

`st` is a short alias for `skill-toggle`. For example, these two commands do the same thing:

```sh
skill-toggle context
st context
```


## First setup

Build the local registry before using a toggle:

```sh
skill-toggle init
skill-toggle verify
```

If you use more than one Codex profile, point the command at the profile you intend to manage:

```sh
CODEX_HOME="$HOME/.codex" skill-toggle init
```

Always check the displayed target before changing anything. If you make a mistake, the receipt and disabled path give you the information needed to restore it.

## Everyday commands

```text
skill-toggle context         Show expected active and disabled-ready items
skill-toggle list            Show raw entries grouped: collisions, disabled, enabled
skill-toggle find QUERY      Look up names, aliases, IDs, and paths
skill-toggle on QUERY        Enable a skill or plugin
skill-toggle off QUERY       Disable a skill or plugin
skill-toggle verify          Check paths and registry consistency
skill-toggle reconcile       Repair reappeared disabled plugin copies
```

### Optional dashboard

Open a read-only terminal dashboard with filtering, status colors, row details,
refresh, and sorting:

```sh
st ui
```

Use the filter box to narrow the rows. Press `s` to cycle status/name/kind
sorting, `r` to refresh from disk, and `q` to quit. Changes still use the
explicit `st on` and `st off` commands, so opening the dashboard cannot change
your skills by accident.

### Use the displayed number

Run `skill-toggle context` (or `st context`) to see one numbered list. The number works anywhere a query works:

```sh
st context
st --dry-run off 12       # show the exact action; change nothing
st off 12                  # show the plan, then ask Proceed? [y/N]
st on 12 --yes             # confirmed form for scripts or automation
st note 12 "Restore only after review."
```

The tool groups related registry copies under one displayed name. `st --json context` returns the same positions, states, IDs, and paths for scripts. Human output is boxed and automatically colored in a terminal; use `st --color never context` for plain output.

`list` is the raw registry view, so it can contain more entries than `context`. It groups entries visually as collisions, disabled, then enabled, with totals at the bottom. Use `context` when you need numbered positions for `on`, `off`, or `note`.

You do not have to run `find` first. It is only a preview tool. Direct commands can resolve a skill name, plugin name, registry ID, source path, or disabled path.

For one skill inside a plugin, use its qualified name:

```sh
skill-toggle off hugging-face:huggingface-llm-trainer
skill-toggle on hugging-face:huggingface-datasets
```

For a complete plugin bundle, use the plugin name:

```sh
skill-toggle off canva
```

After changing a skill or plugin, run:

```sh
skill-toggle verify
```

Then restart Codex and open a new task before judging the visible skill list. An already-open task may still have its earlier skill manifest in context.

## Notes

Notes are kept separately from the registry, so a registry rebuild or plugin update does not erase them:

```sh
skill-toggle note hugging-face:hf-cli "Use this only for model and dataset lookup."
skill-toggle notes hugging-face:hf-cli
skill-toggle notes
skill-toggle delete-note note:123456789abc
```

Notes can target a skill, plugin, registry ID, source path, or disabled path. They are stored in `skills-disabled/notes.json` in the selected Codex profile.

## Recovery and safety

Disabling a local skill or complete plugin bundle moves it into the disabled store instead of deleting it. The registry and receipt preserve the original source path. Enabling it moves it back.

Individual plugin-skill toggles update `config.toml` and leave vendor files in place. If Codex recreates a disabled bundle in an active cache, use:

```sh
skill-toggle reconcile
skill-toggle verify
```

The tool places the previous disabled copy in a timestamped quarantine folder before moving the reappeared copy back to its disabled location.

## Optional startup notification

Create, but do not automatically activate, the macOS notifier template:

```sh
skill-toggle prepare-notifier
```

Review the generated script and LaunchAgent before loading it. The notifier reports one event when the Codex application starts; it does not know when a new conversation or task is created.

## Development

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile src/skill_toggle.py
```

Pull requests are checked by GitHub Actions. Dependabot keeps GitHub Action versions current, and CodeQL performs a lightweight security scan.

## License

MIT. See [`LICENSE`](LICENSE).
