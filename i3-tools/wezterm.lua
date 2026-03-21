local wezterm = require 'wezterm'
local mux = wezterm.mux
local config = {}

config.front_end = "Software"
config.max_fps = 10
config.animation_fps = 1
-- config.dpi = 96

-- 1. DEFINE THE DOMAIN
-- This is all we need to create the persistent server
config.unix_domains = {
  { name = 'unix' },
}

-- 2. HELPER FUNCTION
-- This checks if we're launching without a specific command
-- (e.g., `wezterm` or `wezterm start`)
local function is_default_startup(cmd)
  if not cmd then
    -- Started as just `wezterm`
    return true
  end
  if cmd.args and #cmd.args > 0 then
    -- Started with specific args, like `wezterm ssh...`
    return false
  end
  -- Started as `wezterm start` or `wezterm start --cwd .`
  return true
end

-- 3. THE CORRECT STARTUP EVENT
-- This runs on every launch and checks what to do
wezterm.on('gui-startup', function(cmd)
  if is_default_startup(cmd) then
    -- This is the magic.
    -- We don't attach. We don't return false.
    -- We just tell WezTerm: "That default command you were
    -- about to run? I want it to target the 'unix' domain."
    local domain = mux.get_domain('unix')
    if domain then
      mux.set_default_domain(domain)
    end
  end
end)

-- ... add your other configs (fonts, colors, etc.) below ...
-- config.font_size = 10.0

-- This block hides the scrollbar when you're in an
-- alternate screen (like vim) or when there's no scrollback.
wezterm.on("update-status", function(window, pane)
  local overrides = window:get_config_overrides() or {}

  local dims = pane:get_dimensions()
  local has_scrollback = dims.scrollback_rows > dims.viewport_rows

  local want_scrollbar
  if dims.is_alternate_screen or not has_scrollback then
    want_scrollbar = false
  else
    want_scrollbar = true
  end

  -- Only set overrides when the value actually changes to avoid repaint loops
  if overrides.enable_scroll_bar ~= want_scrollbar then
    overrides.enable_scroll_bar = want_scrollbar
    window:set_config_overrides(overrides)
  end
end)



-- Custom code to let us rename tab groups (window)

-- These lines are required for the clickable title to work
config.enable_tab_bar = true
config.use_fancy_tab_bar = true

-- This block handles formatting the title and making it clickable.
-- It goes at the top level (not inside the 'config' table).
wezterm.on('format-workspace-title', function(workspace, window)
  -- Get the auto-generated title directly from the active pane.
  local auto_title = workspace.active_pane.title
  if not auto_title or auto_title == '' then
    auto_title = '???' -- Fallback text if no title is available
  end

  -- Get our custom prefix from the user data, if it exists
  local custom_prefix = workspace.data.my_custom_prefix

  local title_text
  if custom_prefix and custom_prefix ~= '' then
    -- If we have a prefix, combine them: "My Project : vim"
    title_text = ' ' .. custom_prefix .. ' : ' .. auto_title .. ' '
  else
    -- No prefix, just use the auto-title: "vim"
    title_text = ' ' .. auto_title .. ' '
  end

  -- Return the formatted title, wrapped in a clickable action
  return {
    {
      -- This makes the whole title clickable
      Click = {
        action = wezterm.action.PromptInputLine {
          description = 'Enter Custom Workspace Prefix (leave empty to clear):',

          -- This callback function runs after you press Enter
          action = wezterm.action_callback(function(window, pane, text)
            if not text or text == '' then
              -- If text is empty, clear our custom prefix
              window:active_workspace():set_user_data({
                my_custom_prefix = nil,
              })
            else
              -- Otherwise, store the text in the workspace's 'user_data'
              window:active_workspace():set_user_data({
                my_custom_prefix = text,
              })
            end
          end),
        },
        -- Triggers on a left-mouse-button click
        mouse_buttons = { 'Left' },
      },
    },
    -- This is the actual text to display
    { Text = title_text },
  }
end)

local function get_dpi()
  local success, stdout, stderr = wezterm.run_child_process { "xrdb", "-query" }

  if success then
    local dpi = tonumber(string.match(stdout, "Xft%.dpi:%s*(%d+)"))
    return dpi or 96
  end
  return 96
end


-- LOGIC: scale font based on DPI
-- 96 DPI = 11.0pt font. 192 DPI = 22.0pt font.
local dpi = get_dpi()


-- 2. Define your "Base" size (what you like at standard 96 DPI)
local BASE_FONT_SIZE = 11.0

-- 3. Calculate Scale Factor
-- e.g., at 192 DPI, scale is 2.0. At 96 DPI, scale is 1.0.
local scale = dpi / 96.0

-- 4. Apply Scaling
-- Main content usually scales by itself with system DPI, but if you want to force it:
config.font_size = BASE_FONT_SIZE * scale

-- Tabs often need manual help. We multiply base size by our scale factor.
config.window_frame = {
  font_size = BASE_FONT_SIZE * scale,
  -- You can also scale the height of the tab bar if needed:
  -- font_size = (BASE_FONT_SIZE * scale),
}


wezterm.log_info(string.format("Check: DPI=%d, Scale=%.2f, Tab Font=%.2f, Main Font=%.2f", dpi, scale, config.window_frame.font_size, config.font_size))

-- EVENT: Force a redraw on reload
-- This fires whenever the config is reloaded (via our 'touch' command).
-- NOTE: Disabled to prevent repaint loops with update-status handler.
-- Uncomment if you need a one-time forced refresh after config changes.
-- wezterm.on('window-config-reloaded', function(window, pane)
--   window:set_config_overrides(window:get_config_overrides() or {})
-- end)

local act = wezterm.action

config.keys = {
  -- Add this line to your existing keys list
  { key = 'k', mods = 'CMD', action = act.ResetTerminal },
    { key = 'f', mods = 'CMD', action = wezterm.action.TogglePaneZoomState },
}

return config
