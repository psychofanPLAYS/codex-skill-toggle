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
- Detect when Codex recreates a disabled plugin and quarantine the copy safely.
- Attach notes to a skill, plugin, registry ID, or path.
- Save receipts and original paths for recovery.
- Prepare an optional macOS notification when Codex starts.

The exact skill list injected into an already-running Codex task is supplied by the Codex runtime. The local command cannot read that hidden task manifest directly. Its `context` report clearly labels itself as the expected configuration/filesystem view; confirm the exact injected list in a fresh Codex task.

## Requirements

- Python 3.11 or newer
- Codex Desktop or Codex CLI
- macOS for the notification feature

The tool uses only Python's standard library.

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
skill-toggle list            Show every registered item and its state
skill-toggle find QUERY      Look up names, aliases, IDs, and paths
skill-toggle on QUERY        Enable a skill or plugin
skill-toggle off QUERY       Disable a skill or plugin
skill-toggle verify          Check paths and registry consistency
skill-toggle reconcile       Repair reappeared disabled plugin copies
```

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
