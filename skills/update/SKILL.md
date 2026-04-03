---
name: mega-code-update
description: "Update MEGA-Code skills from the latest repo version"
allowed-tools: Bash, Read
---

# Update

## Setup

```bash
MEGA_DIR="$(cat ~/.local/share/mega-code/pkg-breadcrumb 2>/dev/null)"
if [ -z "$MEGA_DIR" ] || [ ! -f "$MEGA_DIR/pyproject.toml" ]; then
  MEGA_DIR="$HOME/.local/share/mega-code/pkg"
  if [ ! -f "$MEGA_DIR/pyproject.toml" ]; then
    rm -rf "$MEGA_DIR"
    git clone --depth 1 --branch codex "${MEGA_CODE_REPO_URL:-https://github.com/wisdomgraph/mega-code.git}" "$MEGA_DIR"
  fi
fi
if [ -n "${MEGA_CODE_REPO_URL:-}" ]; then
  _CURRENT_ORIGIN="$(git -C "$MEGA_DIR" remote get-url origin 2>/dev/null)"
  if [ "$_CURRENT_ORIGIN" != "$MEGA_CODE_REPO_URL" ]; then
    echo "Updating origin: $_CURRENT_ORIGIN -> $MEGA_CODE_REPO_URL"
    git -C "$MEGA_DIR" remote set-url origin "$MEGA_CODE_REPO_URL"
  fi
fi
# Pull latest before anything else so new modules are available
echo "Pulling latest in $MEGA_DIR ..."
if ! git -C "$MEGA_DIR" pull 2>/dev/null; then
  echo "Pull failed (diverged history), re-cloning..."
  rm -rf "$MEGA_DIR"
  git clone --depth 1 --branch codex "${MEGA_CODE_REPO_URL:-https://github.com/wisdomgraph/mega-code.git}" "$MEGA_DIR"
fi
# Re-bootstrap in case dependencies changed
bash "$MEGA_DIR/scripts/codex-bootstrap.sh" "$MEGA_DIR"
export MEGA_CODE_AGENT="codex"
export MEGA_CODE_DATA_DIR="$HOME/.local/share/mega-code"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$MEGA_DIR/.uv-cache}"
```

## Workflow

### Step 1 — Sync existing skills

```bash
uv run --directory "$MEGA_DIR" python -m mega_code.client.update --userdir "$HOME" --project_dir "$(pwd)" --mega_dir "$MEGA_DIR"
```

Show the sync summary to the user.

### Step 2 — Check for new skills

If the output contains a line starting with `NEW_SKILLS:`, parse the JSON array after the prefix.

If there are new skills available, use `request_user_input` (multi-select) to ask:

```
New skills are available that aren't installed yet:

1. mega-code-foo
2. mega-code-bar
...

Which skills would you like to install?
```

Options should list each new skill name, plus a "None — skip" option.

If the user selects "None" or cancels, say "No new skills installed." and stop.

### Step 3 — Install selected skills

Join the selected skill names with commas and run:

```bash
uv run --directory "$MEGA_DIR" python -m mega_code.client.update --userdir "$HOME" --project_dir "$(pwd)" --mega_dir "$MEGA_DIR" --install-skills <COMMA_SEPARATED_NAMES>
```

Show the install summary to the user.
