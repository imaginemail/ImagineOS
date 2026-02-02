#!/usr/bin/env python3

"""
# GROK_GENERIC_FORMATTING_INSTRUCTIONS
#
# Language-agnostic readability spacing (apply to Python, Bash, C/C++, JS, Java, etc.)
# Rules (I will auto-detect language where possible):
# - Exactly one blank line before every standalone comment (or comment block)
# - Exactly one blank line before every function/method/class/struct definition
# - Exactly one blank line after major block closers (e.g., } in C++/JS, fi/done in Bash, end in Python if needed)
# - Collapse any double (or more) blank lines into one
# - In UI/setup code (e.g., GTK, main(), setup()), add one blank after logical groups (widget rows, cases, etc.)
# - Preserve all code, indentation, inline comments, and logic unchanged
# - No other changes (no style fixes beyond spacing)
#
# Optional hint: Add "# Language: Python" or "# Language: Bash" on next line if auto-detect fails.
"""

import subprocess
import os
import re
import shlex
import time
import threading
import sys
import gi
import urllib.parse
import datetime

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('Pango', '1.0')
from gi.repository import Gtk, Gdk, GLib, Pango

HOME = os.path.expanduser('~')
USER_ENV = '.user_env'
IMAGINE_ENV = '.imagine_env'
SYSTEM_ENV = '.system_env'

# -------------------------
# Utilities for env parsing
# -------------------------

def read_key(file, key, default=''):
    """Return the single-line value for key from file (legacy single-value helper)."""
    if not os.path.exists(file):
        return default
    with open(file, 'r', encoding='utf-8') as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith(f'{key}='):
                v = line.split('=', 1)[1].strip()
                if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                    v = v[1:-1]
                return v.strip()
    return default

def read_merged_key(key):
    """
    Read key from SYSTEM_ENV, IMAGINE_ENV, USER_ENV in that order and return the
    last value found across those files. If the key is not present in any file,
    return None (do not supply defaults).
    """
    value = None
    for path in (SYSTEM_ENV, IMAGINE_ENV, USER_ENV):
        if not os.path.exists(path):
            continue
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith(f'{key}='):
                    v = stripped.split('=', 1)[1].strip()
                    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                        v = v[1:-1]
                    value = v # keep last occurrence across files
    return value

def _unquote_one_line(val):
    """Strip matching surrounding quotes and collapse internal newlines to spaces."""
    if val is None:
        return ''
    v = val.strip()
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        v = v[1:-1]
    # collapse any real newlines into spaces (we do not support multiline prompts)
    v = v.replace('\r', '').replace('\n', ' ')
    return v.strip()

def _escape_for_env(val):
    """Escape backslashes and double quotes for safe double-quoted env values."""
    return val.replace('\\', '\\\\').replace('"', '\\"')

def load_user_prompts(user_env_path=USER_ENV):
    """
    Return a list of prompts found in .user_env.
    Behavior:
      - Each line that starts with PROMPT= yields one prompt entry.
      - Quoted values are unquoted; any embedded newlines are collapsed to spaces.
    """
    prompts = []
    if not os.path.exists(user_env_path):
        return prompts
    with open(user_env_path, 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('PROMPT='):
                v = line.split('=', 1)[1].strip()
                prompts.append(_unquote_one_line(v))
    return prompts

def load_env_multiline(path):
    """
    Conservative loader for DEFAULT_PROMPT and other keys that may be single-line.
    Returns a dict of keys present in the file (value may be empty string).
    """
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.rstrip('\n')
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$', line)
            if not m:
                continue
            key = m.group(1)
            rest = m.group(2).lstrip()
            if rest == '':
                env[key] = ''
                continue
            if (rest.startswith('"') and rest.endswith('"')) or (rest.startswith("'") and rest.endswith("'")):
                env[key] = rest[1:-1]
            else:
                env[key] = rest.split('#', 1)[0].strip()
    return env

def choose_prompts(system_env_path=SYSTEM_ENV, user_env_path=USER_ENV):
    """
    Precedence:
      1. PROMPT lines in user_env (one-per-line) -> return list
      2. DEFAULT_PROMPT in user_env -> single prompt (converted to single-line)
      3. DEFAULT_PROMPT in system_env -> single prompt
      4. else -> [''] (single explicit empty prompt)
    """
    user_prompts = load_user_prompts(user_env_path)
    if user_prompts:
        return user_prompts
    usr_env = load_env_multiline(user_env_path)
    sys_env = load_env_multiline(system_env_path)
    if 'DEFAULT_PROMPT' in usr_env and usr_env['DEFAULT_PROMPT'] != '':
        return [_unquote_one_line(usr_env['DEFAULT_PROMPT'])]
    if 'DEFAULT_PROMPT' in sys_env and sys_env['DEFAULT_PROMPT'] != '':
        return [_unquote_one_line(sys_env['DEFAULT_PROMPT'])]
    return ['']

# -------------------------
# Flag loader (existing)
# -------------------------

def load_flags(key):
    val = None
    for path in (SYSTEM_ENV, IMAGINE_ENV, USER_ENV):
        if not os.path.exists(path):
            continue
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        pattern = rf'(?s){re.escape(key)}\s*=\s*["\']\s*(.*?)\s*["\']'
        match = re.search(pattern, content)
        if match:
            val = match.group(1)  # last match wins (user overrides)
    if val is None:
        return []
    val = re.sub(r'\\\s*$', '', val)
    val = re.sub(r'\\\s*\n\s*', ' ', val)
    return shlex.split(val)

# -------------------------
# Env updater (existing)
# -------------------------

def update_env(file, key, value):
    lines = []
    found = False
    if os.path.exists(file):
        with open(file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip().startswith(f'{key}='):
                    lines.append(f'{key}="{value}"\n')
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f'{key}="{value}"\n')
    with open(file, 'w', encoding='utf-8') as f:
        f.writelines(lines)
        f.flush()
        os.fsync(f.fileno())

# -------------------------
# URL / prompt helpers
# -------------------------

def get_urls_from_input(input_str):
    urls = []
    input_str = input_str.strip()
    if os.path.isfile(input_str):
        with open(input_str, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.split('#', 1)[0].strip()
                if line:
                    urls.append(line)
    else:
        cleaned = re.sub(r'[,\s]+', ' ', input_str)
        parts = cleaned.split()
        urls = [u for u in parts if u]
    return urls if urls else [input_str]

def get_prompts_from_input(input_str):
    prompts = []
    input_str = input_str.strip()
    if os.path.isfile(input_str):
        with open(input_str, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.split('#', 1)[0].strip()
                if line:
                    prompts.append(line)
    else:
        if input_str:
            prompts = [input_str]
        else:
            prompts = ['']
    return prompts

# -------------------------
# Configuration validation
# -------------------------

def validate_config():
    """
    Ensure required configuration keys exist and are well-formed.
    If any required key is missing or invalid, raise RuntimeError and do not attempt to fix.
    This enforces the user's request: fail loudly and immediately.
    """
    required_keys = {
        # key: expected type ('int', 'float', 'str', 'bool')
        'BROWSER': 'str',
        'WINDOW_LIST': 'str',
        'DEFAULT_URL': 'str',
        'DEFAULT_PROMPT': 'str',
        'WINDOW_PATTERNS': 'str',
        'DEFAULT_WIDTH': 'int',
        'DEFAULT_HEIGHT': 'int',
        'MAX_OVERLAP_PERCENT': 'int',
        'GRID_START_DELAY': 'float',
        'STAGE_DELAY': 'float',
        'ROUND_DELAY': 'float',
        'INTER_WINDOW_DELAY': 'float',
        'TARGET_OP_DELAY': 'float',
        'PANEL_DEFAULT_TITLE': 'str',
        'PANEL_DEFAULT_WIDTH': 'int',
        'PANEL_DEFAULT_HEIGHT': 'int',
        'PANEL_DEFAULT_X_OFFSET': 'int',
        'PANEL_DEFAULT_Y_OFFSET': 'int',
        'STAGE_COUNT': 'int',
        'FIRE_COUNT': 'int',
        'FIRE_MODE': 'str',
        'DEBUG_DAEMON_ECHO': 'int',
        'PROMPT_X_FROM_LEFT': 'str',
        'PROMPT_Y_FROM_BOTTOM': 'str',
    }
    missing = []
    invalid = []
    for key, typ in required_keys.items():
        val = read_merged_key(key)
        if val is None:
            missing.append(key)
            continue
        v = str(val).strip()
        if v == '':
            invalid.append(f"{key} (empty)")
            continue
        try:
            if typ == 'int':
                int(v)
            elif typ == 'float':
                float(v)
            elif typ == 'bool':
                if v not in ('0', '1', 'true', 'false', 'True', 'False'):
                    invalid.append(f"{key} (invalid bool: {v})")
            elif typ == 'str':
                pass
        except Exception:
            invalid.append(f"{key} (invalid {typ}: {v})")
    if missing or invalid:
        lines = []
        if missing:
            lines.append("Missing configuration keys: " + ", ".join(missing))
        if invalid:
            lines.append("Invalid configuration values: " + ", ".join(invalid))
        # Fail loudly and immediately
        raise RuntimeError("; ".join(lines))

# -------------------------
# Clipboard helper (xclip)
# -------------------------

def clipboard_set(text):
    """
    Set the X selection clipboard using xclip. This function will attempt to call
    xclip -selection clipboard and write the bytes. If xclip is not available or
    the call fails, raise RuntimeError (we do not silently fallback).
    """
    try:
        p = subprocess.run(['xclip', '-selection', 'clipboard'], input=text.encode('utf-8'), check=True)
    except FileNotFoundError:
        raise RuntimeError("Required tool 'xclip' not found. Install xclip and retry.")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to set clipboard via xclip: {e}")

# -------------------------
# .gxi writer
# -------------------------

class BlitzControl(Gtk.Window):

    def write_gxi(self):
        url = self.url_entry.get_text().strip()
        if not url:
            return

        safe_name = urllib.parse.quote(url, safe='') + '.gxi'
        gxi_dir = os.path.join(HOME, '.imagine_targets')
        os.makedirs(gxi_dir, exist_ok=True)
        gxi_path = os.path.join(gxi_dir, safe_name)

        # Current active prompt from UI
        start, end = self.prompt_buffer.get_bounds()
        active_prompt = self.prompt_buffer.get_text(start, end, False).strip()

        # Env prompts for STAGE_U
        env_prompts = load_user_prompts()

        created = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')

        stage_u = '# STAGE_U: Unsorted / Legacy Prompts\n'
        if env_prompts:
            stage_u += f'@{env_prompts[0]}\n' if env_prompts else ''
            for p in env_prompts:
                stage_u += f'{p}\n'

        skeleton = f"""TARGET_URL={url}
TARGET_BORN={created}
TARGET_DESC=a freeform text block with a reasonable character limit, newlines allowed

(blank prompt special characters; ~ = xdotool key ctrl-a, Del, Return, # = xdotool key return. activated by @ in front, same as stages prompts are.)
~
#

{stage_u}

STAGE_0
@{active_prompt}

.history_0

STAGE_1

.history_1

STAGE_2

.history_2

STAGE_3

.history_3
"""

        if not os.path.exists(gxi_path):
            with open(gxi_path, 'w', encoding='utf-8') as f:
                f.write(skeleton)
        else:
            # Update active in STAGE_0, append to .history_0
            lines = []
            in_history_0 = False
            with open(gxi_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip() == '.history_0':
                        in_history_0 = True
                    if in_history_0 and line.strip().startswith('STAGE_'):
                        in_history_0 = False
                    if line.strip().startswith('@'):
                        line = f'@{active_prompt}\n' if active_prompt else '@\n'
                    lines.append(line)

            # Append fired prompt to history
            lines.append(f'{active_prompt}\n' if active_prompt else '\n')

            with open(gxi_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)

    def __init__(self):
        super().__init__(title=".Blitz Talker.")
        self.set_keep_above(True)
        self.set_border_width(4)
        self.set_resizable(True) # Allow user mouse resize

        # Force safe start state (we still update the env file but only if it exists)
        if os.path.exists(IMAGINE_ENV):
            update_env(IMAGINE_ENV, 'FIRE_MODE', 'N')

        # Apply panel settings (read via merged keys; validate_config ensures presence)
        panel_title = read_merged_key('PANEL_DEFAULT_TITLE')
        panel_w = int(read_merged_key('PANEL_DEFAULT_WIDTH'))
        panel_h = int(read_merged_key('PANEL_DEFAULT_HEIGHT'))
        self.set_title(panel_title)
        self.set_default_size(panel_w, panel_h) # Initial size from config

        try:
            output = subprocess.check_output(['xdotool', 'getdisplaygeometry']).decode().strip()
            sw, sh = map(int, output.split())
        except Exception:
            sw, sh = 1920, 1080

        pos_x = sw - panel_w - int(read_merged_key('PANEL_DEFAULT_X_OFFSET'))
        pos_y = sh - panel_h - int(read_merged_key('PANEL_DEFAULT_Y_OFFSET'))
        if pos_x < 0: pos_x = 0
        if pos_y < 0: pos_y = 0
        self.move(pos_x, pos_y)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add(box)

        # URL row: scrolled entry to prevent long URLs from forcing width
        url_box = Gtk.Box(spacing=2)
        box.pack_start(url_box, False, False, 0)

        url_label = Gtk.Label(label="Target URL(s):")
        url_label.set_size_request(60, -1)
        url_box.pack_start(url_label, False, False, 0)

        url_scroll = Gtk.ScrolledWindow()
        url_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        url_scroll.set_hexpand(False)

        self.url_entry = Gtk.Entry()
        self.url_entry.set_hexpand(True)

        # MERGED READ: prefer the last defined value across system, imagine, user
        merged_url = read_merged_key('DEFAULT_URL')
        if merged_url is None:
            raise RuntimeError("Configuration error: DEFAULT_URL not set in any env file.")
        self.url_entry.set_text(merged_url)

        url_scroll.add(self.url_entry)
        url_box.pack_start(url_scroll, True, True, 0)

        pick_url_btn = Gtk.Button(label="Pick File")
        pick_url_btn.connect("clicked", self.on_pick_url_file)
        url_box.pack_start(pick_url_btn, False, False, 0)

        # Prompt row: TextView in scrolled window for wrapping + expand
        prompt_box = Gtk.Box(spacing=2)
        box.pack_start(prompt_box, True, True, 0)

        prompt_label = Gtk.Label(label="Prompt(s):")
        prompt_label.set_size_request(60, -1)
        prompt_box.pack_start(prompt_label, False, False, 0)

        prompt_scroll = Gtk.ScrolledWindow()
        prompt_scroll.set_hexpand(False)
        prompt_scroll.set_vexpand(True)
        prompt_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.prompt_view = Gtk.TextView()
        self.prompt_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.prompt_buffer = self.prompt_view.get_buffer()

        prompts = choose_prompts()
        prompt_text = '\n'.join(prompts) if prompts else ''
        if not prompt_text.strip():
            merged_prompt = read_merged_key('DEFAULT_PROMPT')
            if merged_prompt is None:
                raise RuntimeError("Configuration error: DEFAULT_PROMPT not set in any env file.")
            prompt_text = merged_prompt
        self.prompt_buffer.set_text(prompt_text)

        prompt_scroll.add(self.prompt_view)
        prompt_box.pack_start(prompt_scroll, True, True, 0)

        pick_prompt_btn = Gtk.Button(label="Pick File")
        pick_prompt_btn.connect("clicked", self.on_pick_prompt_file)
        prompt_box.pack_start(pick_prompt_btn, False, False, 0)

        # Horizontal row for the spin controls
        controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.pack_start(controls_box, False, False, 0)

        # Rounds
        fire_hbox = Gtk.Box(spacing=2)
        fire_label = Gtk.Label(label="Rounds:")
        fire_hbox.pack_start(fire_label, False, False, 0)

        fire_adj = Gtk.Adjustment(value=int(read_merged_key('FIRE_COUNT')), lower=1, upper=999, step_increment=1)
        self.fire_spin = Gtk.SpinButton(adjustment=fire_adj)
        fire_hbox.pack_start(self.fire_spin, True, True, 0)
        controls_box.pack_start(fire_hbox, True, True, 0)

        # Targets
        stage_hbox = Gtk.Box(spacing=2)
        stage_label = Gtk.Label(label="Targets:")
        stage_hbox.pack_start(stage_label, False, False, 0)

        stage_adj = Gtk.Adjustment(value=int(read_merged_key('STAGE_COUNT')), lower=1, upper=2000, step_increment=1)
        self.stage_spin = Gtk.SpinButton(adjustment=stage_adj)
        stage_hbox.pack_start(self.stage_spin, True, True, 0)
        controls_box.pack_start(stage_hbox, True, True, 0)

        self.status_label = Gtk.Label(label="Ready")
        box.pack_start(self.status_label, False, False, 0)

        btn_box = Gtk.Box(spacing=8)
        box.pack_start(btn_box, False, False, 0)

        stage_btn = Gtk.Button(label="STAGE")
        stage_btn.connect("clicked", self.on_stage)
        btn_box.pack_start(stage_btn, False, False, 0)

        self.fire_btn = Gtk.Button(label="FIRE")
        self.fire_btn.connect("clicked", self.on_fire)
        btn_box.pack_start(self.fire_btn, False, False, 0)

        edit_btn = Gtk.Button(label="EDIT")
        edit_btn.connect("clicked", self.on_edit)
        btn_box.pack_start(edit_btn, False, False, 0)

        quit_btn = Gtk.Button(label="QUIT")
        quit_btn.connect("clicked", self.on_quit)
        btn_box.pack_start(quit_btn, False, False, 0)

        self.daemon_thread = None

        # Minimize buttons
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
        button {
            min-width: 0px;
            min-height: 0px;
            padding: 4px 8px;
        }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Initialize loaded snapshot with current (initial) UI values
        start_iter, end_iter = self.prompt_buffer.get_bounds()
        initial_prompts_text = self.prompt_buffer.get_text(start_iter, end_iter, False)

        self._loaded_snapshot = {
            'DEFAULT_URL': self.url_entry.get_text(),
            'PROMPTS': initial_prompts_text,
            'FIRE_COUNT': str(self.fire_spin.get_value_as_int()),
            'STAGE_COUNT': str(self.stage_spin.get_value_as_int()),
        }

        # Initial UI update
        self.update_fire_button()

    def update_fire_button(self):
        mode = read_merged_key('FIRE_MODE')
        if mode is None:
            raise RuntimeError("Configuration error: FIRE_MODE not set.")
        self.fire_btn.set_label("STOP" if mode == 'Y' else "FIRE")

    def save_all(self):
        """
        Commit only values that changed since load and only write overrides that
        differ from .system_env. Update the in-memory snapshot after writes.
        """
        # Ensure snapshot exists
        if not hasattr(self, '_loaded_snapshot'):
            raise RuntimeError("Internal error: loaded snapshot missing; cannot save safely.")

        # Current UI values
        current_url = self.url_entry.get_text()
        start_iter, end_iter = self.prompt_buffer.get_bounds()
        current_prompts = self.prompt_buffer.get_text(start_iter, end_iter, False)
        current_fire = str(int(self.fire_spin.get_value()))
        current_stage = str(int(self.stage_spin.get_value()))

        # Helper to read system value (explicitly from SYSTEM_ENV only)
        def system_val(key):
            v = read_key(SYSTEM_ENV, key, None)
            return v

        # --- PROMPTS handling (user_env PROMPT= lines) ---
        if current_prompts != self._loaded_snapshot.get('PROMPTS', ''):
            # If changed vs snapshot, decide whether to write overrides
            # Build the list of prompt lines to write if they are overrides of system
            sys_prompts = []
            sys_default = system_val('DEFAULT_PROMPT')
            if sys_default is not None and str(sys_default).strip() != '':
                sys_prompts = [_unquote_one_line(sys_default)]

            ui_lines = current_prompts.splitlines()
            ui_join = '\n'.join(ui_lines)
            sys_join = '\n'.join(sys_prompts) if sys_prompts else ''

            if ui_join == sys_join:
                # Remove any PROMPT= lines from .user_env if they exist (we want no override)
                if os.path.exists(USER_ENV):
                    lines = []
                    with open(USER_ENV, 'r', encoding='utf-8') as f:
                        for line in f:
                            if not line.strip().startswith('PROMPT='):
                                lines.append(line)
                    with open(USER_ENV, 'w', encoding='utf-8') as f:
                        f.writelines(lines)
            else:
                # Write PROMPT= lines to .user_env as the override
                lines = []
                if os.path.exists(USER_ENV):
                    with open(USER_ENV, 'r', encoding='utf-8') as f:
                        for line in f:
                            if not line.strip().startswith('PROMPT='):
                                lines.append(line)

                if len(ui_lines) == 0:
                    lines.append('PROMPT=""\n')
                else:
                    for p in ui_lines:
                        p = p.rstrip('\r')
                        if p == '':
                            lines.append('PROMPT=""\n')
                        else:
                            lines.append(f'PROMPT="{_escape_for_env(p)}"\n')

                with open(USER_ENV, 'w', encoding='utf-8') as f:
                    f.writelines(lines)

        # --- DEFAULT_URL and STAGE_COUNT to .user_env ---
        sys_url = system_val('DEFAULT_URL')
        if current_url != self._loaded_snapshot.get('DEFAULT_URL', ''):
            if sys_url is None or current_url != sys_url:
                update_env(USER_ENV, 'DEFAULT_URL', current_url)
            else:
                # If equal to system, remove any override in user env
                if os.path.exists(USER_ENV):
                    lines = []
                    with open(USER_ENV, 'r', encoding='utf-8') as f:
                        for line in f:
                            if not line.strip().startswith('DEFAULT_URL='):
                                lines.append(line)
                    with open(USER_ENV, 'w', encoding='utf-8') as f:
                        f.writelines(lines)

        sys_stage = system_val('STAGE_COUNT')
        if current_stage != self._loaded_snapshot.get('STAGE_COUNT', ''):
            if sys_stage is None or current_stage != sys_stage:
                update_env(USER_ENV, 'STAGE_COUNT', current_stage)
            else:
                if os.path.exists(USER_ENV):
                    lines = []
                    with open(USER_ENV, 'r', encoding='utf-8') as f:
                        for line in f:
                            if not line.strip().startswith('STAGE_COUNT='):
                                lines.append(line)
                    with open(USER_ENV, 'w', encoding='utf-8') as f:
                        f.writelines(lines)

        # --- FIRE_COUNT to .imagine_env ---
        sys_fire = system_val('FIRE_COUNT')
        if current_fire != self._loaded_snapshot.get('FIRE_COUNT', ''):
            if sys_fire is None or current_fire != sys_fire:
                update_env(IMAGINE_ENV, 'FIRE_COUNT', current_fire)
            else:
                if os.path.exists(IMAGINE_ENV):
                    lines = []
                    with open(IMAGINE_ENV, 'r', encoding='utf-8') as f:
                        for line in f:
                            if not line.strip().startswith('FIRE_COUNT='):
                                lines.append(line)
                    with open(IMAGINE_ENV, 'w', encoding='utf-8') as f:
                        f.writelines(lines)

        # After successful writes, update the snapshot to current values
        self._loaded_snapshot.update({
            'DEFAULT_URL': current_url,
            'PROMPTS': current_prompts,
            'FIRE_COUNT': current_fire,
            'STAGE_COUNT': current_stage,
        })

        # Write/update .gxi with current state
        self.write_gxi()

    def on_pick_url_file(self, widget):
        self.save_all()
        dialog = Gtk.FileChooserDialog(title="Pick URL File", parent=self, action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)

        filter_text = Gtk.FileFilter()
        filter_text.set_name("Text files")
        filter_text.add_mime_type("text/plain")
        dialog.add_filter(filter_text)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            filename = dialog.get_filename()
            self.url_entry.set_text(filename)
            self.save_all()
        dialog.destroy()

    def on_pick_prompt_file(self, widget):
        self.save_all()
        dialog = Gtk.FileChooserDialog(title="Pick Prompt File", parent=self, action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)

        filter_text = Gtk.FileFilter()
        filter_text.set_name("Text files")
        filter_text.add_mime_type("text/plain")
        dialog.add_filter(filter_text)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            filename = dialog.get_filename()
            with open(filename, 'r', encoding='utf-8') as f:
                text = f.read()
            self.prompt_buffer.set_text(text)
            self.save_all()
        dialog.destroy()

    def on_stage(self, widget):
        self.save_all()
        self.status_label.set_text("Staging windows...")

        # Cache repeated delay values once at the start of staging
        stage_delay = float(read_merged_key('STAGE_DELAY'))
        grid_start_delay = float(read_merged_key('GRID_START_DELAY'))

        # Kill old target windows
        target = read_merged_key('BROWSER')

        # --- DEBUG INSERT ---
        #debug = int(read_merged_key('DEBUG_DAEMON_ECHO'))
        debug = 0
        if debug:
            direct_browser = read_key(SYSTEM_ENV, 'BROWSER', '(not set in .system_env)')
            msg = (
                f"Killing old browser windows...\n\n"
                f"Merged BROWSER key value (target): {target}\n"
                f"Direct from .system_env: {direct_browser}"
            )
            subprocess.call([
                'gxmessage', msg,
                '-title', 'Kill Old Windows - Browser Debug',
                '-center',
                '-buttons', 'OK:0'
            ])
            # --- END DEBUG INSERT ---

        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), read_merged_key('WINDOW_LIST'))
        if os.path.exists(file_path):
            self.gentle_target_op('kill', sync=True)

        try:
            subprocess.run(['pkill', '-f', target], check=False)
        except Exception:
            pass

        # Clean old live_windows file
        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), read_merged_key('WINDOW_LIST'))
        if os.path.exists(file_path):
            os.remove(file_path)

        num = int(read_merged_key('STAGE_COUNT'))
        url_input = read_merged_key('DEFAULT_URL')
        urls = get_urls_from_input(url_input)

        if not urls:
            msg = "No URLs provided for staging."
            subprocess.call(['gxmessage', msg, '-title', 'Stage Error', '-center', '-buttons', 'OK:0'])
            self.status_label.set_text("Ready")
            return

        cmd_base = [read_merged_key('BROWSER')] + load_flags('BROWSER_FLAGS_HEAD') + load_flags('BROWSER_FLAGS_MIDDLE') + load_flags('BROWSER_FLAGS_TAIL')

        # Echo flag sections individually
        browser = read_merged_key('BROWSER')
        head_flags = load_flags('BROWSER_FLAGS_HEAD')
        middle_flags = load_flags('BROWSER_FLAGS_MIDDLE')
        tail_flags = load_flags('BROWSER_FLAGS_TAIL')

        print("Browser executable:", shlex.quote(browser))
        print("Head flags:", ' '.join(shlex.quote(f) for f in head_flags) or "(none)")
        print("Middle flags:", ' '.join(shlex.quote(f) for f in middle_flags) or "(none)")
        print("Tail flags:", ' '.join(shlex.quote(f) for f in tail_flags) or "(none)")

        cmd_base_str = ' '.join(shlex.quote(p) for p in cmd_base)
        print("Base command:", cmd_base_str)

        for i in range(num):
            url = urls[i % len(urls)]
            cmd = cmd_base + [url]
            if tail_flags: # reuse the earlier loaded list to avoid extra calls
                cmd[-2] = cmd[-2] + cmd[-1]
                cmd.pop()

            # Echo the precise final command
            cmd_str = ' '.join(shlex.quote(p) for p in cmd)
            print(f"Launching window {i+1} with URL: {url}")
            print("Full command:", cmd_str)

            try:
                subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
            except Exception as e:
                msg = f"Failed to launch browser window:\n\nCommand: {cmd_str}\n\nError: {e}"
                subprocess.call(['gxmessage', msg, '-title', 'Launch Error', '-center', '-buttons', 'OK:0'])

            time.sleep(stage_delay)

        GLib.timeout_add(int(grid_start_delay * 1000), lambda: self.grid_windows(num) or False)

    def on_fire(self, widget=None):
        self.save_all()
        if read_merged_key('FIRE_MODE') == 'N':
            update_env(IMAGINE_ENV, 'FIRE_MODE', 'Y')
            self.daemon_thread = threading.Thread(target=self.daemon_thread_func, daemon=True)
            self.daemon_thread.start()
            self.update_fire_button()
            self.status_label.set_text("Firing...")
        else:
            update_env(IMAGINE_ENV, 'FIRE_MODE', 'N')
            self.update_fire_button()
            self.status_label.set_text("Stopped")

    def on_edit(self, widget):
        self.save_all()

        current = self.url_entry.get_text()
        proc = subprocess.Popen(
            ['yad', '--text-info', '--on-top', '--editable', '--title=Edit Target URL(s)',
             '--width=800', '--height=500', '--button=Save:0', '--button=Cancel:1'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        )
        out, _ = proc.communicate(current)
        if proc.returncode == 0:
            self.url_entry.set_text(out.strip())
            self.save_all()

        start_iter, end_iter = self.prompt_buffer.get_bounds()
        current = self.prompt_buffer.get_text(start_iter, end_iter, False)
        proc = subprocess.Popen(
            ['yad', '--text-info', '--on-top', '--editable', '--title=Edit Prompt',
             '--width=900', '--height=600', '--button=Save:0', '--button=Cancel:1'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        )
        out, _ = proc.communicate(current)
        if proc.returncode == 0:
            self.prompt_buffer.set_text(out.strip())
            self.save_all()

    def on_quit(self, widget):
        self.save_all()
        target = read_merged_key('BROWSER')

        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), read_merged_key('WINDOW_LIST'))
        if os.path.exists(file_path):
            self.gentle_target_op('kill', sync=True)

        try:
            subprocess.run(['pkill', '-f', target], check=False)
        except Exception:
            pass

        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), read_merged_key('WINDOW_LIST'))
        if os.path.exists(file_path):
            os.remove(file_path)

        Gtk.main_quit()

    def grid_windows(self, expected_num):
        patterns = [p.strip().strip('"').strip("'").lower() for p in read_merged_key('WINDOW_PATTERNS').split(',') if p.strip()]

        # Cache the repeated delay value once at the start of gridding
        grid_start_delay = float(read_merged_key('GRID_START_DELAY'))

        max_tries = 30
        last_total_windows = -1
        stagnant_limit = 3
        stagnant_count = 0
        last_matched = []

        for attempt in range(1, max_tries + 1):
            result = subprocess.run(['xdotool', 'search', '--onlyvisible', '.'], capture_output=True, text=True)
            all_ids = result.stdout.strip().splitlines() if result.returncode == 0 else []

            if len(all_ids) == last_total_windows:
                stagnant_count += 1
            else:
                stagnant_count = 0
            last_total_windows = len(all_ids)

            matched = []
            for wid in all_ids:
                result = subprocess.run(['xdotool', 'getwindowname', wid], capture_output=True, text=True)
                name = result.stdout.strip().lower()
                if any(p in name for p in patterns):
                    matched.append(wid)

            if matched:
                last_matched = matched[:]

            if len(matched) >= expected_num:
                self._grid_ids(matched)
                self.status_label.set_text("Ready")
                return False

            if stagnant_count >= stagnant_limit:
                if last_matched:
                    self._grid_ids(last_matched)
                self.status_label.set_text("Ready")
                return False

            time.sleep(grid_start_delay)

        if last_matched:
            self._grid_ids(last_matched)
        self.status_label.set_text("Ready")
        return False

    def _grid_ids(self, ids):
        result = subprocess.run(['xdotool', 'getdisplaygeometry'], capture_output=True, text=True)
        try:
            screen = result.stdout.strip().split()
            sw, sh = int(screen[0]), int(screen[1])
        except:
            sw, sh = 1920, 1080

        # Cache target sizing values once at the start of gridding
        target_width = int(read_merged_key('DEFAULT_WIDTH'))
        target_height = int(read_merged_key('DEFAULT_HEIGHT'))
        target_overlap = int(read_merged_key('MAX_OVERLAP_PERCENT'))

        margin = 20
        available_width = sw - 2 * margin
        available_height = sh - 2 * margin

        n = len(ids)
        if n == 0:
            return

        effective_step = max(1, int(target_width * (100 - target_overlap) / 100))
        max_cols_by_width = 1 + (available_width - target_width) // effective_step if available_width >= target_width else 1

        desired_rows = 2
        desired_cols = (n + desired_rows - 1) // desired_rows

        if desired_cols <= max_cols_by_width:
            cols = desired_cols
            rows = desired_rows
        else:
            cols = max_cols_by_width
            rows = (n + cols - 1) // cols

        if cols == 1:
            step_x = 0
            total_grid_width = target_width
        else:
            min_total_width = target_width + (cols - 1) * effective_step
            if min_total_width <= available_width:
                extra_space = available_width - target_width
                step_x = extra_space // (cols - 1)
                if step_x < effective_step:
                    step_x = effective_step
            else:
                step_x = max(1, (available_width - target_width) // (cols - 1))
            total_grid_width = target_width + (cols - 1) * step_x

        vertical_effective_step = max(1, int(target_height * (100 - target_overlap) / 100))
        if rows == 1:
            step_y = 0
        else:
            min_total_height = target_height + (rows - 1) * vertical_effective_step
            if min_total_height <= available_height:
                extra_vspace = available_height - target_height
                step_y = extra_vspace // (rows - 1)
                if step_y < vertical_effective_step:
                    step_y = vertical_effective_step
            else:
                step_y = max(1, (available_height - target_height) // (rows - 1))

        x_start = margin + max(0, (available_width - total_grid_width) // 2)
        y_start = margin + max(0, (available_height - (target_height + (rows - 1) * step_y)) // 2)

        list_path = read_merged_key('WINDOW_LIST')
        with open(list_path, 'w', encoding='utf-8') as f:
            for idx, wid in enumerate(ids):
                r = idx // cols
                c = idx % cols
                x = int(x_start + c * step_x)
                y = int(y_start + r * step_y)
                try:
                    cmd = ['xdotool', 'windowsize', wid, str(target_width), str(target_height)]
                    subprocess.run(cmd, capture_output=True, text=True)
                    move_cmd = ['xdotool', 'windowmove', wid, str(x), str(y)]
                    subprocess.run(move_cmd, capture_output=True, text=True)
                except Exception as e:
                    msg = f"Failed to size/move window {wid}:\n\nError: {e}"
                    subprocess.call(['gxmessage', msg, '-title', 'Grid Error', '-center', '-buttons', 'OK:0'])
                f.write(wid + '\n')

        self.gentle_target_op('activate', sync=True)

        # Initial .gxi creation after grid complete
        self.write_gxi()

        auto_fire_val = read_merged_key('AUTO_FIRE')
        if auto_fire_val in ('1', 'Y', 'true', 'True'):
            time.sleep(5)  # hard-coded test delay
            self.on_fire(None)  # trigger same as FIRE button click

    def gentle_target_op(self, op_type, sync=True, delay=None):
        """
        Unified window operation: activate or kill windows from list.
        op_type: 'activate' or 'kill'
        sync: True/False for --sync on activate
        delay: optional float; if None, uses TARGET_OP_DELAY from config (fallback 1.0)
        """
        if delay is None:
            delay_val = read_merged_key('TARGET_OP_DELAY')
            delay = float(delay_val) if delay_val is not None else 1.0

        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), read_merged_key('WINDOW_LIST'))
        if not os.path.exists(file_path):
            msg = f"Window list file not found:\n\n{file_path}"
            subprocess.call(['gxmessage', msg, '-title', 'Target Op Error', '-center', '-buttons', 'OK:0'])
            return

        with open(file_path, 'r', encoding='utf-8') as f:
            window_ids = [line.strip() for line in f if line.strip()]

        if not window_ids:
            return

        for wid in window_ids:
            # Always activate first (with optional sync)
            sync_flag = '--sync' if sync else ''
            act_cmd = ['xdotool', 'windowactivate', sync_flag, wid]
            subprocess.run(act_cmd, capture_output=True, text=True)

            if op_type == 'kill':
                close_cmd = ['xdotool', 'key', '--clearmodifiers', 'alt+F4']
                result = subprocess.run(close_cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    cmd_str = ' '.join(shlex.quote(p) for p in close_cmd)
                    msg = (
                        f"Failed to close window {wid}\n\n"
                        f"Command executed:\n{cmd_str}\n\n"
                        f"Return code: {result.returncode}\n"
                        f"stdout:\n{result.stdout.strip() or '(empty)'}\n\n"
                        f"stderr:\n{result.stderr.strip() or '(empty)'}\n\n"
                        "Window may still be open."
                    )
                    subprocess.call(['gxmessage', msg, '-title', 'xdotool Close Failure',
                                    '-timeout', '10', '-center', '-buttons', 'OK:0'])

            time.sleep(delay)

    def daemon_thread_func(self):
        total_shots = 0
        fire_count = int(read_merged_key('FIRE_COUNT'))

        # Cache repeated values once at the start of the daemon
        round_delay = float(read_merged_key('ROUND_DELAY'))
        inter_window_delay_val = read_merged_key('INTER_WINDOW_DELAY')
        inter_window_delay = float(inter_window_delay_val) if inter_window_delay_val else 0.0
        shot_delay_val = read_merged_key('SHOT_DELAY')
        shot_delay = float(shot_delay_val) if shot_delay_val else 0.0
        prompt_x_from_left = read_merged_key('PROMPT_X_FROM_LEFT') or '50%'
        prompt_y_from_bottom = read_merged_key('PROMPT_Y_FROM_BOTTOM') or '10%'

        self.gentle_target_op('activate', sync=True)

        def _parse_shell_output(text):
            d = {}
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                if '=' in line:
                    k, v = line.split('=', 1)
                elif ':' in line:
                    k, v = line.split(':', 1)
                else:
                    continue
                d[k.strip().upper()] = v.strip()
            return d

        def update_status(text):
            GLib.idle_add(lambda: self.status_label.set_text(text) or False)

        update_status("Firing... 0 shots")

        # Read window list path and ensure it's absolute
        live_windows_file = read_merged_key('WINDOW_LIST')
        if not live_windows_file:
            msg = "No WINDOW_LIST configured; aborting daemon."
            subprocess.call(['gxmessage', msg, '-title', 'Daemon Error', '-center', '-buttons', 'OK:0'])
            update_status("Ready")
            return

        live_windows_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), live_windows_file) if not os.path.isabs(live_windows_file) else live_windows_file

        for round_num in range(1, fire_count + 1):
            if round_num > 1:
                time.sleep(round_delay)

            if not os.path.exists(live_windows_file) or os.stat(live_windows_file).st_size == 0:
                continue

            with open(live_windows_file, 'r', encoding='utf-8') as f:
                window_ids = [line.strip() for line in f if line.strip()]

            # Current active prompt from UI (single shot now)
            start, end = self.prompt_buffer.get_bounds()
            prompt = self.prompt_buffer.get_text(start, end, False).strip()

            if not prompt:
                continue

            for idx, wid in enumerate(window_ids, start=1):
                if read_merged_key('FIRE_MODE') == 'N':
                    break

                try:
                    # Single activate to raise this exact window
                    subprocess.run(['xdotool', 'windowactivate', wid], capture_output=True, text=True)

                    # Save mouse
                    mouse_cmd = ['xdotool', 'getmouselocation', '--shell']
                    result = subprocess.run(mouse_cmd, capture_output=True, text=True)
                    mouse_dict = _parse_shell_output(result.stdout)

                    if (result.returncode != 0 or
                        not mouse_dict or
                        'X' not in mouse_dict or
                        'Y' not in mouse_dict):
                        cmd_str = ' '.join(shlex.quote(p) for p in mouse_cmd)
                        msg = (
                            f"ERROR: Failed to get mouse location for window ID {wid}\n\n"
                            f"Command executed:\n{cmd_str}\n\n"
                            f"Return code: {result.returncode}\n"
                            f"stdout:\n{result.stdout.strip() or '(empty)'}\n\n"
                            f"stderr:\n{result.stderr.strip() or '(empty)'}\n\n"
                            f"Parsed dict:\n{mouse_dict}"
                        )
                        subprocess.call([
                            'gxmessage', msg,
                            '-title', 'xdotool Mouse Failure',
                            '-center', '-buttons', 'OK:0'
                        ])
                        saved_x = saved_y = 0
                    else:
                        saved_x = int(mouse_dict['X'])
                        saved_y = int(mouse_dict['Y'])

                    # Get geometry
                    geom_cmd = ['xdotool', 'getwindowgeometry', '--shell', wid]
                    result = subprocess.run(geom_cmd, capture_output=True, text=True)
                    geom_dict = _parse_shell_output(result.stdout)

                    if (result.returncode != 0 or
                        not geom_dict or
                        'WIDTH' not in geom_dict or
                        'HEIGHT' not in geom_dict):
                        cmd_str = ' '.join(shlex.quote(p) for p in geom_cmd)
                        msg = (
                            f"ERROR: Failed to get valid window geometry for window ID {wid}\n\n"
                            f"Command executed:\n{cmd_str}\n\n"
                            f"Return code: {result.returncode}\n"
                            f"stdout:\n{result.stdout.strip() or '(empty)'}\n\n"
                            f"stderr:\n{result.stderr.strip() or '(empty)'}\n\n"
                            f"Parsed geometry dict:\n{geom_dict}"
                        )
                        subprocess.call([
                            'gxmessage', msg,
                            '-title', 'xdotool Geometry Failure',
                            '-center', '-buttons', 'OK:0'
                        ])

                    width = int(geom_dict['WIDTH'])
                    height = int(geom_dict['HEIGHT'])

                    if '%' in prompt_x_from_left:
                        click_x = int(width * int(prompt_x_from_left.rstrip('%')) / 100)
                    else:
                        click_x = int(prompt_x_from_left)

                    if '%' in prompt_y_from_bottom:
                        pixels_from_bottom = int(height * int(prompt_y_from_bottom.rstrip('%')) / 100)
                    else:
                        pixels_from_bottom = int(prompt_y_from_bottom)

                    click_y = height - pixels_from_bottom

                    # Move mouse to prompt location
                    move_cmd = ['xdotool', 'mousemove', '--window', wid, str(click_x), str(click_y)]
                    result = subprocess.run(move_cmd, capture_output=True, text=True)
                    if result.returncode != 0:
                        cmd_str = ' '.join(shlex.quote(p) for p in move_cmd)
                        msg = (
                            f"ERROR: Failed to move mouse in window {wid}\n\n"
                            f"Command executed:\n{cmd_str}\n\n"
                            f"Return code: {result.returncode}\n"
                            f"stdout:\n{result.stdout.strip() or '(empty)'}\n\n"
                            f"stderr:\n{result.stderr.strip() or '(empty)'}"
                        )
                        subprocess.call([
                            'gxmessage', msg,
                            '-title', 'xdotool Mouse Failure',
                            '-center', '-buttons', 'OK:0'
                        ])

                    self.set_keep_above(False)

                    # three scrolls to position screen content; more reliable than 'home'
                    click_cmd = ['xdotool', 'click', '--clearmodifiers', '--window', wid, '4', '4', '4']
                    subprocess.run(click_cmd, capture_output=True, text=True)
                    time.sleep(shot_delay) # 2. after three scrolls

                    # single left click
                    click1_cmd = ['xdotool', 'click', '--clearmodifiers', '--window', wid, '1']
                    subprocess.run(click1_cmd, capture_output=True, text=True)
                    time.sleep(shot_delay)

                    # Single paste (no burst)
                    success = False
                    if prompt == '~':
                        key_cmd = ['xdotool', 'key', '--clearmodifiers', '--window', wid, 'ctrl+a', 'Delete', 'Return']
                        proc_key = subprocess.run(key_cmd, capture_output=True, text=True)
                        if proc_key.returncode == 0:
                            success = True
                    elif prompt == '#':
                        key_cmd = ['xdotool', 'key', '--clearmodifiers', '--window', wid, 'Return']
                        proc_key = subprocess.run(key_cmd, capture_output=True, text=True)
                        if proc_key.returncode == 0:
                            success = True
                    else:
                        try:
                            clipboard_set(prompt)
                        except RuntimeError as e:
                            msg = (
                                f"ERROR: Failed to set clipboard for prompt in window {wid}\n\n"
                                f"Prompt: {repr(prompt)}\n\n"
                                f"Error: {e}"
                            )
                            subprocess.call([
                                'gxmessage', msg,
                                '-title', 'Clipboard Error',
                                '-center', '-buttons', 'OK:0'
                            ])
                            try:
                                clipboard_set('')
                            except:
                                pass
                            # skip delay on clipboard fail
                        else:
                            key_cmd = ['xdotool', 'key', '--clearmodifiers', '--window', wid, 'ctrl+a', 'ctrl+v', 'Return']
                            proc_key = subprocess.run(key_cmd, capture_output=True, text=True)
                            if proc_key.returncode != 0:
                                cmd_str = ' '.join(shlex.quote(p) for p in key_cmd)
                                msg = (
                                    f"ERROR: Paste key sequence failed in window {wid}\n\n"
                                    f"Command executed:\n{cmd_str}\n\n"
                                    f"Return code: {proc_key.returncode}\n"
                                    f"stdout:\n{proc_key.stdout.strip() or '(empty)'}\n\n"
                                    f"stderr:\n{proc_key.stderr.strip() or '(empty)'}"
                                )
                                subprocess.call([
                                    'gxmessage', msg,
                                    '-title', 'xdotool Key Failure',
                                    '-center', '-buttons', 'OK:0'
                                ])
                            else:
                                success = True

                    if success:
                        time.sleep(shot_delay)

                        # Write/update .gxi after each successful shot
                        self.write_gxi()

                    total_shots += 1
                    update_status(f"Firing round {round_num}/{fire_count} — {total_shots} shots fired")

                    # restore mouse
                    restore_cmd = ['xdotool', 'mousemove', str(saved_x), str(saved_y)]
                    result = subprocess.run(restore_cmd, capture_output=True, text=True)
                    if result.returncode != 0:
                        cmd_str = ' '.join(shlex.quote(p) for p in restore_cmd)
                        msg = (
                            f"ERROR: Failed to restore mouse position\n\n"
                            f"Command executed:\n{cmd_str}\n\n"
                            f"Return code: {result.returncode}\n"
                            f"stdout:\n{result.stdout.strip() or '(empty)'}\n\n"
                            f"stderr:\n{result.stderr.strip() or '(empty)'}"
                        )
                        subprocess.call([
                            'gxmessage', msg,
                            '-title', 'xdotool Mouse Restore Failure',
                            '-center', '-buttons', 'OK:0'
                        ])

                    self.set_keep_above(True)

                except Exception as e:
                    msg = f"Unexpected error processing window {wid}:\n\n{e}"
                    subprocess.call(['gxmessage', msg, '-title', 'Daemon Error', '-center', '-buttons', 'OK:0'])

                # Inter-window delay: pause after finishing one window (success or error) before starting the next
                time.sleep(inter_window_delay)

        update_status(f"Done — {total_shots} shots fired")
        update_env(IMAGINE_ENV, 'FIRE_MODE', 'N')
        GLib.idle_add(self.update_fire_button)
        GLib.timeout_add(5000, lambda: self.status_label.set_text("Ready") or False)

if __name__ == '__main__':
    # Enforce strict validation: fail loudly and immediately.
    try:
        validate_config()
    except Exception as e:
        # Print a clear, loud error and exit non-zero so the user must fix configuration.
        print("FATAL CONFIGURATION ERROR:", file=sys.stderr)
        print(str(e), file=sys.stderr)
        sys.exit(2)

    win = BlitzControl()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
