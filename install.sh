#!/usr/bin/env bash
# UBIK-TUI installer — installs WezTerm + deploys config + creates desktop shortcut
set -e

UBIK_TUI_DIR="$(cd "$(dirname "$0")" && pwd)"
WEZTERM_CFG_DIR="$HOME/.config/wezterm"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  UBIK-TUI Installer"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. Install WezTerm ─────────────────────────────────────────
if ! command -v wezterm &>/dev/null; then
  echo "[1/4] Installing WezTerm…"

  if [[ "$OSTYPE" == "darwin"* ]]; then
    if command -v brew &>/dev/null; then
      brew install --cask wezterm
    else
      echo "  → Download manually: https://wezfurlong.org/wezterm/installation.html"
      exit 1
    fi
  else
    # Linux — flatpak (universal) or .deb
    if command -v flatpak &>/dev/null; then
      flatpak install --user -y flathub org.wezfurlong.wezterm
      # wrapper so 'wezterm' is in PATH
      mkdir -p "$HOME/.local/bin"
      cat > "$HOME/.local/bin/wezterm" <<'WRAP'
#!/usr/bin/env bash
exec flatpak run org.wezfurlong.wezterm "$@"
WRAP
      chmod +x "$HOME/.local/bin/wezterm"
    elif command -v apt &>/dev/null; then
      curl -fsSL https://apt.fury.io/wez/gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/wezterm-fury.gpg
      echo 'deb [signed-by=/usr/share/keyrings/wezterm-fury.gpg] https://apt.fury.io/wez/ * *' \
        | sudo tee /etc/apt/sources.list.d/wezterm.list
      sudo apt update -qq && sudo apt install -y wezterm
    else
      echo "  → Download manually: https://wezfurlong.org/wezterm/installation.html"
      exit 1
    fi
  fi
else
  echo "[1/4] WezTerm already installed — skipping"
fi

# ── 2. Deploy wezterm.lua config ───────────────────────────────
echo "[2/4] Deploying WezTerm config…"
mkdir -p "$WEZTERM_CFG_DIR"

if [[ -f "$WEZTERM_CFG_DIR/wezterm.lua" ]]; then
  cp "$WEZTERM_CFG_DIR/wezterm.lua" "$WEZTERM_CFG_DIR/wezterm.lua.bak"
  echo "  → Existing config backed up to wezterm.lua.bak"
fi

cp "$UBIK_TUI_DIR/wezterm.lua" "$WEZTERM_CFG_DIR/wezterm.lua"
echo "  → Config deployed to $WEZTERM_CFG_DIR/wezterm.lua"

# ── 3. Install Python deps ────────────────────────────────────
echo "[3/4] Installing Python deps…"
cd "$UBIK_TUI_DIR"

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

UBIK_SDK_DIR="$(dirname "$UBIK_TUI_DIR")/UBIK-SDK"
UBIK_CLI_DIR="$(dirname "$UBIK_TUI_DIR")/UBIK-CLI"

if [[ -d "$UBIK_SDK_DIR" ]]; then
  .venv/bin/pip install -q -e "$UBIK_SDK_DIR"
fi
if [[ -d "$UBIK_CLI_DIR" ]]; then
  .venv/bin/pip install -q -e "$UBIK_CLI_DIR"
fi

.venv/bin/pip install -q -e .
echo "  → Python deps installed"

# ── 4. Desktop shortcut ────────────────────────────────────────
echo "[4/4] Creating desktop shortcut…"
DESKTOP_FILE="$HOME/Desktop/UBIK-TUI.desktop"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Name=UBIK-TUI
Exec=wezterm start --config-file $WEZTERM_CFG_DIR/wezterm.lua
Icon=utilities-terminal
Type=Application
Terminal=false
Categories=Development;
EOF
chmod +x "$DESKTOP_FILE"
echo "  → Shortcut created at $DESKTOP_FILE"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Done! Launch: double-click UBIK-TUI on your desktop"
echo "  Or: wezterm start --config-file $WEZTERM_CFG_DIR/wezterm.lua"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
