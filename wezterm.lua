-- UBIK-TUI — WezTerm config
-- Drop this file at: ~/.config/wezterm/wezterm.lua (Linux/Mac)
--                or: %APPDATA%\wezterm\wezterm.lua   (Windows)

local wezterm = require 'wezterm'
local mux = wezterm.mux
local config = wezterm.config_builder()

-- ── Auto-launch ubik-tui on startup ──────────────────────────

local ubik_tui_cmd

if wezterm.target_triple:find('windows') then
  ubik_tui_cmd = {
    'powershell.exe', '-NoLogo', '-Command',
    'cd "$env:USERPROFILE\\workspace\\UBIK-TUI"; .venv\\Scripts\\ubik-tui.exe'
  }
elseif wezterm.target_triple:find('darwin') then
  ubik_tui_cmd = {
    '/bin/zsh', '-lc',
    'cd ~/workspace/UBIK-TUI && .venv/bin/ubik-tui'
  }
else
  ubik_tui_cmd = {
    '/bin/bash', '-lc',
    'cd ~/workspace/UBIK-TUI && .venv/bin/ubik-tui'
  }
end

wezterm.on('gui-startup', function(cmd)
  local _, _, window = mux.spawn_window({
    args = ubik_tui_cmd,
    set_environment_variables = {
      TERM = 'xterm-256color',
    },
  })
  window:gui_window():maximize()
end)

-- ── Appearance ────────────────────────────────────────────────

config.color_scheme = 'Tokyo Night'

config.font = wezterm.font('JetBrains Mono', { weight = 'Regular' })
config.font_size = 13.0

config.window_padding = {
  left = '1cell',
  right = '1cell',
  top = '0.5cell',
  bottom = '0.5cell',
}

config.window_decorations = 'TITLE | RESIZE'

config.hide_tab_bar_when_only_one_tab = true

config.window_background_opacity = 0.97

-- ── Behavior ──────────────────────────────────────────────────

config.scrollback_lines = 10000
config.enable_scroll_bar = false

-- Ctrl+Shift+N → new window (useful to open a plain shell alongside)
config.keys = {
  {
    key = 'n',
    mods = 'CTRL|SHIFT',
    action = wezterm.action.SpawnWindow,
  },
}

return config
