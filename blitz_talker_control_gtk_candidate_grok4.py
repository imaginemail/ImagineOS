#!/usr/bin/env python3
import subprocess
import os
import re
import shlex
import time
import threading
import sys
import gi
import urllib.parse

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Pango', '1.0')
from gi.repository import Gtk, Gdk, GLib, Pango, GdkPixbuf

HOME = os.path.expanduser('~')
USER_ENV = '.user_env'
IMAGINE_ENV = '.imagine_env'
SYSTEM_ENV = '.system_env'
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEBUG_DIR = os.path.join(SCRIPT_DIR, '.debug')
os.makedirs(DEBUG_DIR, exist_ok=True)
DEBUG_FILE = os.path.join(DEBUG_DIR, 'window_matches.log')
open(DEBUG_FILE, 'w').close()

# Central configuration specification - single source of truth
CONFIG_SPEC = {
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
    'SHOT_DELAY': 'float',
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
    'SINGLE_XDOTOOL': 'str',
    'TARGET_DIR': 'str',
    'CAPTURE_ENABLED': 'str',
    'CAPTURE_TOOL': 'str',
    'CAPTURE_X_FROM_LEFT': 'str',
    'CAPTURE_Y_FROM_BOTTOM': 'str',
    'CAPTURE_RATIO': 'str',
    'FIRE_STACK_X_OFFSET': 'int',
    'FIRE_STACK_Y_OFFSET': 'int',
}

# Helper functions
def read_key(file, key, default=''):
    if not os.path.exists(file):
        return default
    try:
        with open(file, 'r', encoding='utf-8') as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith(f'{key}='):
                    v = line.split('=', 1)[1].strip()
                    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                        v = v[1:-1]
                    return v.strip()
    except Exception:
        pass
    return default

def read_merged_key(key):
    value = None
    for path in (SYSTEM_ENV, IMAGINE_ENV, USER_ENV):
        if not os.path.exists(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith(f'{key}='):
                        v = stripped.split('=', 1)[1].strip()
                        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                            v = v[1:-1]
                        value = v
        except Exception:
            pass
    return value

GLOBAL_DEBUG = int(read_merged_key('DEBUG_DAEMON_ECHO') or 0)
print(f"[STARTUP] DEBUG_DAEMON_ECHO = {GLOBAL_DEBUG}")

def log_debug(section, content):
    if GLOBAL_DEBUG:
        print(f"[DEBUG {section}] {content}")
        with open(DEBUG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"[{section}]\n{content}\n\n")

def _unquote_one_line(val):
    if val is None:
        return ''
    v = val.strip()
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        v = v[1:-1]
    v = v.replace('\r', '').replace('\n', ' ')
    return v.strip()

def _escape_for_env(val):
    return val.replace('\\', '\\\\').replace('"', '\\"')

def load_user_prompts(user_env_path=USER_ENV):
    prompts = []
    has_default_prompt = False
    default_prompt_value = None
    if not os.path.exists(user_env_path):
        return prompts, has_default_prompt, default_prompt_value
    try:
        with open(user_env_path, 'r', encoding='utf-8') as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith('PROMPT='):
                    v = line.split('=', 1)[1].strip()
                    prompts.append(_unquote_one_line(v))
                elif line.startswith('DEFAULT_PROMPT='):
                    v = line.split('=', 1)[1].strip()
                    default_prompt_value = _unquote_one_line(v)
                    has_default_prompt = True
    except Exception:
        pass
    return prompts, has_default_prompt, default_prompt_value

def choose_prompts(system_env_path=SYSTEM_ENV, user_env_path=USER_ENV):
    user_prompts, has_default_prompt, default_prompt_value = load_user_prompts(user_env_path)
    if user_prompts or has_default_prompt:
        if has_default_prompt:
            return [default_prompt_value], True, True
        else:
            return user_prompts, True, False
    sys_env = load_env_multiline(system_env_path)
    if 'DEFAULT_PROMPT' in sys_env and sys_env['DEFAULT_PROMPT'] != '':
        return [_unquote_one_line(sys_env['DEFAULT_PROMPT'])], False, False
    return [''], False, False

def load_env_multiline(path):
    env = {}
    if not os.path.exists(path):
        return env
    try:
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
    except Exception:
        pass
    return env

def load_flags(key):
    val = None
    for path in (SYSTEM_ENV, IMAGINE_ENV, USER_ENV):
        if not os.path.exists(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            pattern = rf'(?s){re.escape(key)}\s*=\s*["\']\s*(.*?)\s*["\']'
            match = re.search(pattern, content)
            if match:
                val = match.group(1)
        except Exception:
            pass
    if val is None:
        return []
    val = re.sub(r'\\\s*$', '', val)
    val = re.sub(r'\\\s*\n\s*', ' ', val)
    return shlex.split(val)

def update_env(file, key, value):
    lines = []
    found = False
    if os.path.exists(file):
        try:
            with open(file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip().startswith(f'{key}='):
                        lines.append(f'{key}="{value}"\n')
                        found = True
                    else:
                        lines.append(line)
        except Exception:
            return
    if not found:
        lines.append(f'{key}="{value}"\n')
    try:
        with open(file, 'w', encoding='utf-8') as f:
            f.writelines(lines)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass

def get_urls_from_input(input_str):
    urls = []
    input_str = input_str.strip()
    if os.path.isfile(input_str):
        try:
            with open(input_str, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.split('#', 1)[0].strip()
                    if line:
                        urls.append(line)
        except Exception:
            pass
    else:
        parts = re.split(r'[,\s]+', input_str)
        urls = [u.strip().strip('"\'') for u in parts if u.strip().strip('"\'')]
    return urls if urls else [input_str.strip().strip('"\'')]

def clipboard_set(text):
    try:
        subprocess.run(['xclip', '-selection', 'clipboard'], input=text.encode('utf-8'), check=True)
    except Exception:
        pass

def validate_config():
    optional_keys = ['CAPTURE_ENABLED', 'CAPTURE_TOOL', 'CAPTURE_X_FROM_LEFT', 'CAPTURE_Y_FROM_BOTTOM', 'CAPTURE_RATIO', 'FIRE_STACK_X_OFFSET', 'FIRE_STACK_Y_OFFSET']
    required_keys = {k: v for k, v in CONFIG_SPEC.items() if k not in optional_keys}

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
        except Exception:
            invalid.append(f"{key} (invalid {typ}: {v})")

    if missing or invalid:
        lines = []
        if missing:
            lines.append("Missing: " + ", ".join(missing))
        if invalid:
            lines.append("Invalid: " + ", ".join(invalid))
        raise RuntimeError("; ".join(lines))

class UnifiedApp(Gtk.Window):
    def __init__(self):
        super().__init__(title="Blitz Talker")
        self.set_default_size(-1, -1)
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.add(self.paned)

        gun_frame = Gtk.Frame(label="Gun Panel")
        gun_frame.set_size_request(380, -1)
        self.gun = BlitzControl(self)
        gun_frame.add(self.gun)
        self.paned.pack2(gun_frame, resize=False, shrink=False)

        editor_scrolled = Gtk.ScrolledWindow()
        editor_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.editor = EnvGxiEditor(self.gun)
        editor_scrolled.add(self.editor)
        self.paned.pack1(editor_scrolled, resize=True, shrink=True)

        GLib.idle_add(lambda: self.paned.set_position(self.paned.get_allocated_width() - 380) or False)

class BlitzControl(Gtk.Box):
    def __init__(self, parent_app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.parent_app = parent_app
        self.set_border_width(2)
        log_debug("INIT", "BlitzControl init start")

        # In-memory firing flag
        self.firing = False

        if os.path.exists(IMAGINE_ENV):
            update_env(IMAGINE_ENV, 'FIRE_MODE', 'N')

        # URL entry row
        url_box = Gtk.Box(spacing=1)
        self.pack_start(url_box, False, False, 0)

        url_label = Gtk.Label(label="Target URL(s):")
        url_label.set_size_request(60, -1)
        url_box.pack_start(url_label, False, False, 0)

        url_scroll = Gtk.ScrolledWindow()
        url_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        url_scroll.set_hexpand(False)
        url_scroll.set_vexpand(False)

        self.url_entry = Gtk.Entry()
        self.url_entry.set_hexpand(True)
        merged_url = read_merged_key('DEFAULT_URL')
        if merged_url is None:
            raise RuntimeError("Configuration error: DEFAULT_URL not set in any env file.")
        self.url_entry.set_text(merged_url)
        url_scroll.add(self.url_entry)
        url_box.pack_start(url_scroll, True, True, 0)

        pick_url_btn = Gtk.Button(label="Pick File")
        pick_url_btn.connect("clicked", self.on_pick_url_file)
        url_box.pack_start(pick_url_btn, False, False, 0)

        # Prompt entry row
        prompt_box = Gtk.Box(spacing=1)
        self.pack_start(prompt_box, False, False, 0)

        prompt_label = Gtk.Label(label="Prompt(s):")
        prompt_label.set_size_request(60, -1)
        prompt_box.pack_start(prompt_label, False, False, 0)

        prompt_scroll = Gtk.ScrolledWindow()
        prompt_scroll.set_hexpand(False)
        prompt_scroll.set_vexpand(False)
        prompt_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.prompt_view = Gtk.TextView()
        self.prompt_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.prompt_buffer = self.prompt_view.get_buffer()

        self.prompt_list, self.prompt_is_custom, self.from_default_prompt = choose_prompts()
        prompt_text = '\n'.join(self.prompt_list)
        self.prompt_buffer.set_text(prompt_text)
        self.prompt_buffer.connect("changed", self.on_prompt_changed)

        prompt_scroll.add(self.prompt_view)
        prompt_box.pack_start(prompt_scroll, True, True, 0)

        pick_prompt_btn = Gtk.Button(label="Pick File")
        pick_prompt_btn.connect("clicked", self.on_pick_prompt_file)
        prompt_box.pack_start(pick_prompt_btn, False, False, 0)

        # Spinbutton controls
        controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)
        self.pack_start(controls_box, False, False, 0)

        fire_hbox = Gtk.Box(spacing=1)
        fire_label = Gtk.Label(label="Rounds:")
        fire_hbox.pack_start(fire_label, False, False, 0)
        fire_adj = Gtk.Adjustment(value=int(read_merged_key('FIRE_COUNT')), lower=1, upper=999, step_increment=1)
        self.fire_spin = Gtk.SpinButton(adjustment=fire_adj)
        fire_hbox.pack_start(self.fire_spin, True, True, 0)
        controls_box.pack_start(fire_hbox, True, True, 0)

        stage_hbox = Gtk.Box(spacing=1)
        stage_label = Gtk.Label(label="Targets:")
        stage_hbox.pack_start(stage_label, False, False, 0)
        stage_adj = Gtk.Adjustment(value=int(read_merged_key('STAGE_COUNT')), lower=1, upper=2000, step_increment=1)
        self.stage_spin = Gtk.SpinButton(adjustment=stage_adj)
        stage_hbox.pack_start(self.stage_spin, True, True, 0)
        controls_box.pack_start(stage_hbox, True, True, 0)

        self.status_label = Gtk.Label(label="Ready")
        self.pack_start(self.status_label, False, False, 0)

        # Button creation helper
        def create_action_button(label_text, callback):
            btn = Gtk.Button(label=label_text)
            btn.connect("clicked", callback)
            return btn

        # Action buttons row
        btn_box = Gtk.Box(spacing=1)
        self.pack_start(btn_box, False, False, 0)

        btn_box.pack_start(create_action_button("STAGE", self.on_stage), False, False, 0)
        self.fire_btn = create_action_button("FIRE", self.on_fire)
        btn_box.pack_start(self.fire_btn, False, False, 0)
        btn_box.pack_start(create_action_button("EDIT", self.on_edit), False, False, 0)
        btn_box.pack_start(create_action_button("QUIT", self.on_quit), False, False, 0)

        self.daemon_thread = None
        self.url_input_str = merged_url
        self.url_list = get_urls_from_input(self.url_input_str)

        # CSS for compact UI
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
        * {
            min-width: 0px;
            min-height: 0px;
        }
        button {
            padding: 0px 3px;
            font-size: 8pt;
        }
        entry, spinbutton {
            padding: 0px;
            font-size: 8pt;
        }
        label {
            padding: 0px;
            font-size: 8pt;
        }
        textview text {
            font-size: 8pt;
        }
        paned {
            padding: 0px;
        }
        frame {
            padding: 0px;
        }
        box {
            padding: 0px;
        }
        scrolledwindow {
            padding: 0px;
        }
        flowboxchild {
            padding: 8px;
        }
        flowboxchild label {
            font-size: 7pt;
        }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self._loaded_snapshot = {
            'URL_INPUT_STR': self.url_entry.get_text(),
            'PROMPTS': self.prompt_buffer.get_text(self.prompt_buffer.get_start_iter(), self.prompt_buffer.get_end_iter(), False),
            'FIRE_COUNT': str(self.fire_spin.get_value_as_int()),
            'STAGE_COUNT': str(self.stage_spin.get_value_as_int()),
        }

        self.update_fire_button()
        self.url_entry.connect("changed", self.on_url_changed)
        GLib.idle_add(self.auto_load_current_gxi)
        log_debug("INIT", "BlitzControl init complete")

    def on_prompt_changed(self, *args):
        self.prompt_is_custom = True

    def on_url_changed(self, widget):
        self.url_input_str = self.url_entry.get_text().strip()
        self.url_list = get_urls_from_input(self.url_input_str)
        log_debug("URL INPUT CHANGED", f"Input str: {self.url_input_str} | Parsed list count: {len(self.url_list)} | Sample: {self.url_list[:3] if self.url_list else 'empty'}")
        self.parent_app.editor.sync_from_gun()

    def auto_load_current_gxi(self):
        self.parent_app.editor.sync_from_gun()

    def update_fire_button(self):
        self.fire_btn.set_label("STOP" if self.firing else "FIRE")

    def save_all(self):
        log_debug("SAVE ALL", "Starting save_all")
        if not hasattr(self, '_loaded_snapshot'):
            return

        url_input_str = self.url_entry.get_text()
        current_prompts = self.prompt_buffer.get_text(self.prompt_buffer.get_start_iter(), self.prompt_buffer.get_end_iter(), False)
        current_fire = str(int(self.fire_spin.get_value()))
        current_stage = str(int(self.stage_spin.get_value()))

        # Prompts migration: if custom (from .user_env), migrate to .gxi STAGE_1 and clear .user_env
        if self.prompt_is_custom:
            current_prompt_lines = [line.strip() for line in current_prompts.splitlines() if line.strip()]
            if current_prompt_lines:
                self.write_gxi_prompt(current_prompt_lines, make_active=self.from_default_prompt)

            # Clear all PROMPT= and DEFAULT_PROMPT from .user_env
            if os.path.exists(USER_ENV):
                lines = []
                with open(USER_ENV, 'r', encoding='utf-8') as f:
                    for line in f:
                        stripped = line.strip()
                        if not stripped.startswith('PROMPT=') and not stripped.startswith('DEFAULT_PROMPT='):
                            lines.append(line)
                with open(USER_ENV, 'w', encoding='utf-8') as f:
                    f.writelines(lines)

            # Reset flags
            self.prompt_is_custom = False
            self.from_default_prompt = False

        # URL save
        sys_url = read_key(SYSTEM_ENV, 'DEFAULT_URL', None)
        if url_input_str != self._loaded_snapshot.get('URL_INPUT_STR', ''):
            log_debug("SAVE URL INPUT", f"Changed to {url_input_str}")
            if sys_url is None or url_input_str != sys_url:
                update_env(USER_ENV, 'DEFAULT_URL', url_input_str)
            else:
                if os.path.exists(USER_ENV):
                    lines = []
                    with open(USER_ENV, 'r', encoding='utf-8') as f:
                        for line in f:
                            if not line.strip().startswith('DEFAULT_URL='):
                                lines.append(line)
                    with open(USER_ENV, 'w', encoding='utf-8') as f:
                        f.writelines(lines)

        # STAGE_COUNT save
        sys_stage = read_key(SYSTEM_ENV, 'STAGE_COUNT', None)
        if current_stage != self._loaded_snapshot.get('STAGE_COUNT', ''):
            log_debug("SAVE STAGE COUNT", current_stage)
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

        # FIRE_COUNT save
        sys_fire = read_key(SYSTEM_ENV, 'FIRE_COUNT', None)
        if current_fire != self._loaded_snapshot.get('FIRE_COUNT', ''):
            log_debug("SAVE FIRE COUNT", current_fire)
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

        self._loaded_snapshot.update({
            'URL_INPUT_STR': url_input_str,
            'PROMPTS': current_prompts,
            'FIRE_COUNT': current_fire,
            'STAGE_COUNT': current_stage,
        })

        self.url_input_str = url_input_str
        self.url_list = get_urls_from_input(self.url_input_str)

        self.auto_load_current_gxi()
        log_debug("SAVE ALL", "Complete")

    def on_pick_url_file(self, widget):
        log_debug("PICK URL FILE", "Dialog opening")
        self.save_all()
        dialog = Gtk.FileChooserDialog(title="Pick URL File", parent=self.parent_app, action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        filter_text = Gtk.FileFilter()
        filter_text.set_name("Text files")
        filter_text.add_mime_type("text/plain")
        dialog.add_filter(filter_text)
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            filename = dialog.get_filename()
            log_debug("PICK URL FILE", f"Selected: {filename}")
            self.url_entry.set_text(filename)
            self.save_all()
            self.auto_load_current_gxi()
        dialog.destroy()

    def on_pick_prompt_file(self, widget):
        log_debug("PICK PROMPT FILE", "Dialog opening")
        self.save_all()
        dialog = Gtk.FileChooserDialog(title="Pick Prompt File", parent=self.parent_app, action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        filter_text = Gtk.FileFilter()
        filter_text.set_name("Text files")
        filter_text.add_mime_type("text/plain")
        dialog.add_filter(filter_text)
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            filename = dialog.get_filename()
            log_debug("PICK PROMPT FILE", f"Selected: {filename}")
            with open(filename, 'r', encoding='utf-8') as f:
                text = f.read()
            self.prompt_buffer.set_text(text)
            self.prompt_is_custom = True
            self.save_all()
            self.auto_load_current_gxi()
        dialog.destroy()

    def on_stage(self, widget):
        log_debug("STAGE CLICKED", "Starting STAGE")
        self.save_all()
        self.status_label.set_text("Staging windows...")

        stage_delay = float(read_merged_key('STAGE_DELAY'))
        grid_start_delay = float(read_merged_key('GRID_START_DELAY'))

        # Gentle close of existing browsers
        self.gentle_target_op('kill')

        # Remove old window list
        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), read_merged_key('WINDOW_LIST'))
        if os.path.exists(file_path):
            os.remove(file_path)

        num = int(read_merged_key('STAGE_COUNT'))
        urls = self.url_list
        if not urls:
            msg = "No URLs provided for staging."
            subprocess.call(['gxmessage', msg, '-title', 'Stage Error', '-center', '-buttons', 'OK:0'])
            self.status_label.set_text("Ready")
            return

        cmd_base = [read_merged_key('BROWSER')] + load_flags('BROWSER_FLAGS_HEAD') + load_flags('BROWSER_FLAGS_MIDDLE') + load_flags('BROWSER_FLAGS_TAIL')
        if GLOBAL_DEBUG:
            log_debug("STAGE LAUNCH BASE", f"Browser: {read_merged_key('BROWSER')} | Head: {load_flags('BROWSER_FLAGS_HEAD')} | Middle: {load_flags('BROWSER_FLAGS_MIDDLE')} | Tail: {load_flags('BROWSER_FLAGS_TAIL')} | Base cmd: {' '.join(shlex.quote(p) for p in cmd_base)}")

        for i in range(num):
            url = urls[i % len(urls)]
            cmd = cmd_base + [url]
            if load_flags('BROWSER_FLAGS_TAIL'):
                cmd[-2] = cmd[-2] + cmd[-1]
                cmd.pop()
            cmd_str = ' '.join(shlex.quote(p) for p in cmd)
            if GLOBAL_DEBUG:
                log_debug(f"STAGE LAUNCH {i+1}/{num}", f"URL: {url} | Full cmd: {cmd_str}")
            try:
                subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
            except Exception as e:
                log_debug("STAGE LAUNCH FAIL", f"URL: {url} | Error: {e}")
                subprocess.call(['gxmessage', f"Failed to launch browser window:\n\nCommand: {cmd_str}\n\nError: {e}", '-title', 'Launch Error', '-center', '-buttons', 'OK:0'])
            time.sleep(stage_delay)

        GLib.timeout_add(int(grid_start_delay * 1000), lambda: self.grid_windows(num) or False)

    def on_fire(self, widget=None):
        log_debug("FIRE CLICKED", "FIRE button pressed")
        self.save_all()

        if not self.firing:
            log_debug("FIRE START", "Starting daemon thread")
            self.firing = True
            update_env(IMAGINE_ENV, 'FIRE_MODE', 'Y')

            # Stack all windows to center before firing
            try:
                output = subprocess.check_output(['xdotool', 'getdisplaygeometry']).decode().strip()
                sw, sh = map(int, output.split())
            except Exception:
                sw, sh = 1920, 1080

            target_width = int(read_merged_key('DEFAULT_WIDTH'))
            target_height = int(read_merged_key('DEFAULT_HEIGHT'))
            center_x = (sw - target_width) // 2
            center_y = (sh - target_height) // 2

            offset_x = int(read_merged_key('FIRE_STACK_X_OFFSET') or 0)
            offset_y = int(read_merged_key('FIRE_STACK_Y_OFFSET') or 0)

            stack_x = center_x + offset_x
            stack_y = center_y + offset_y

            live_windows_file = read_merged_key('WINDOW_LIST')
            live_windows_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), live_windows_file) if live_windows_file and not os.path.isabs(live_windows_file) else live_windows_file

            if live_windows_file and os.path.exists(live_windows_file):
                with open(live_windows_file, 'r', encoding='utf-8') as f:
                    window_ids = [line.strip() for line in f if line.strip()]
                if window_ids:
                    for wid in window_ids:
                        cmd = ['xdotool', 'windowsize', '--sync', wid, str(target_width), str(target_height),
                               'windowmove', wid, str(stack_x), str(stack_y)]
                        cmd_str = ' '.join(shlex.quote(p) for p in cmd)
                        if GLOBAL_DEBUG:
                            log_debug("FIRE STACK", f"WID {wid} | Pos {stack_x},{stack_y} | Size {target_width}x{target_height} | CMD: {cmd_str}")
                        subprocess.run(cmd, capture_output=True)

            self.daemon_thread = threading.Thread(target=self.daemon_thread_func, daemon=True)
            self.daemon_thread.start()
            self.update_fire_button()
            self.status_label.set_text("Firing...")
        else:
            log_debug("FIRE STOP", "Stopping daemon")
            self.firing = False
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
            self.auto_load_current_gxi()

        current = self.prompt_buffer.get_text(self.prompt_buffer.get_start_iter(), self.prompt_buffer.get_end_iter(), False)
        proc = subprocess.Popen(
            ['yad', '--text-info', '--on-top', '--editable', '--title=Edit Prompt',
             '--width=900', '--height=600', '--button=Save:0', '--button=Cancel:1'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        )
        out, _ = proc.communicate(current)
        if proc.returncode == 0:
            self.prompt_buffer.set_text(out.strip())
            self.prompt_is_custom = True
            self.save_all()
            self.auto_load_current_gxi()

    def on_quit(self, widget):
        self.save_all()

        # Gentle close of browsers
        self.gentle_target_op('kill')

        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), read_merged_key('WINDOW_LIST'))
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError as e:
                if GLOBAL_DEBUG:
                    log_debug("QUIT CLEAN FAIL", f"{file_path} | {e.errno}: {e.strerror}")

        Gtk.main_quit()

    def grid_windows(self, expected_num):
        patterns = [p.strip().strip('"').strip("'").lower() for p in read_merged_key('WINDOW_PATTERNS').split(',') if p.strip()]
        if GLOBAL_DEBUG:
            log_debug("GRID PATTERNS", patterns)

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

            matched = sorted(matched, key=int)

            if GLOBAL_DEBUG:
                matched_titles = []
                for wid in matched:
                    try:
                        title = subprocess.check_output(['xdotool', 'getwindowname', wid], text=True).strip()
                        matched_titles.append(f"{wid}: {title}")
                    except:
                        matched_titles.append(f"{wid}: <title failed>")
                log_debug(f"GRID ATTEMPT {attempt}/{max_tries}", f"Matched {len(matched)}/{expected_num} | Titles: {matched_titles}")

            if len(matched) >= expected_num:
                self._grid_ids(matched)
                self.status_label.set_text("Ready")
                return False

            if stagnant_count >= stagnant_limit:
                if last_matched:
                    last_matched = sorted(last_matched, key=int)
                    self._grid_ids(last_matched)
                self.status_label.set_text("Ready")
                return False

            time.sleep(grid_start_delay)

        if last_matched:
            last_matched = sorted(last_matched, key=int)
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

        target_width = int(read_merged_key('DEFAULT_WIDTH'))
        target_height = int(read_merged_key('DEFAULT_HEIGHT'))
        target_overlap = int(read_merged_key('MAX_OVERLAP_PERCENT'))
        margin = 20
        available_width = sw - 2 * margin
        available_height = sh - 2 * margin
        n = len(ids)
        if n == 0:
            return

        ids = sorted(ids, key=int)

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
        list_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), list_path) if not os.path.isabs(list_path) else list_path

        with open(list_path, 'w', encoding='utf-8') as f:
            for idx, wid in enumerate(ids):
                r = idx // cols
                c = idx % cols
                x = int(x_start + c * step_x)
                y = int(y_start + r * step_y)
                try:
                    cmd = ['xdotool', 'windowsize', wid, str(target_width), str(target_height),
                           'windowmove', wid, str(x), str(y)]
                    cmd_str = ' '.join(shlex.quote(p) for p in cmd)
                    if GLOBAL_DEBUG:
                        log_debug("GRID POSITION", f"WID {wid} | Pos {x},{y} | Size {target_width}x{target_height} | CMD: {cmd_str}")
                    subprocess.run(cmd, capture_output=True, text=True)
                except Exception as e:
                    log_debug("GRID ERROR", f"WID {wid} | Error: {e}")
                    subprocess.call(['gxmessage', f"Failed to size/move window {wid}:\n\nError: {e}", '-title', 'Grid Error', '-center', '-buttons', 'OK:0'])
                f.write(wid + '\n')

        self.gentle_target_op('activate', sync=True)

        if self.prompt_is_custom:
            self.write_gxi_prompt()

        self.auto_load_current_gxi()

        auto_fire_val = read_merged_key('AUTO_FIRE')
        if auto_fire_val in ('1', 'Y', 'true', 'True'):
            time.sleep(5)
            self.on_fire(None)

    def gentle_target_op(self, op_type, sync=True, delay=None):
        if delay is None:
            delay_val = read_merged_key('TARGET_OP_DELAY')
            delay = float(delay_val) if delay_val is not None else 1.0

        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), read_merged_key('WINDOW_LIST'))
        target = read_merged_key('BROWSER')

        if not os.path.exists(file_path):
            if op_type == 'kill' and target:
                subprocess.run(['pkill', '-f', target], check=False)
                if GLOBAL_DEBUG:
                    log_debug("GENTLE KILL PKILL (no list)", target)
            else:
                msg = f"Window list file not found for {op_type}:\n\n{file_path}"
                subprocess.call(['gxmessage', msg, '-title', 'Target Op Error', '-center', '-buttons', 'OK:0'])
            return

        with open(file_path, 'r', encoding='utf-8') as f:
            window_ids = [line.strip() for line in f if line.strip()]
        if not window_ids:
            return

        if GLOBAL_DEBUG:
            titles = []
            for wid in window_ids:
                try:
                    title = subprocess.check_output(['xdotool', 'getwindowname', wid], text=True).strip()
                    titles.append(f"{wid}: {title}")
                except:
                    titles.append(f"{wid}: <title failed>")
            log_debug(f"GENTLE {op_type.upper()} WINDOWS", titles)

        for wid in window_ids:
            if op_type == 'activate':
                sync_flag = '--sync' if sync else ''
                act_cmd = ['xdotool', 'windowactivate', sync_flag, str(wid)]
                act_str = ' '.join(shlex.quote(p) for p in act_cmd)
                if GLOBAL_DEBUG:
                    log_debug("GENTLE ACTIVATE", f"WID {wid} | CMD: {act_str}")
                subprocess.run(act_cmd, capture_output=True, text=True)

            elif op_type == 'kill':
                hex_wid = f"0x{int(wid):08x}"
                close_cmd = ['wmctrl', '-i', '-c', hex_wid]
                close_str = ' '.join(shlex.quote(p) for p in close_cmd)
                if GLOBAL_DEBUG:
                    log_debug("GENTLE CLOSE WMCTRL", f"WID {wid} HEX {hex_wid} | CMD: {close_str}")
                subprocess.run(close_cmd, capture_output=True)

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
        single_xdotool = read_merged_key('SINGLE_XDOTOOL') in ('Y', '1', 'true', 'True')
        debug = int(read_merged_key('DEBUG_DAEMON_ECHO') or 0)

        # Prompt fetched once at start
        start, end = self.prompt_buffer.get_bounds()
        prompt = self.prompt_buffer.get_text(start, end, False).strip()

        system_default = read_merged_key('DEFAULT_PROMPT') or ''
        is_harvest_only = not self.prompt_is_custom and prompt == system_default.strip()

        target_width = int(read_merged_key('DEFAULT_WIDTH'))
        target_height = int(read_merged_key('DEFAULT_HEIGHT'))

        prompt_x_from_left = read_merged_key('PROMPT_X_FROM_LEFT') or '50%'
        prompt_y_from_bottom = read_merged_key('PROMPT_Y_FROM_BOTTOM') or '10%'

        if '%' in prompt_x_from_left:
            relative_x = int(target_width * int(prompt_x_from_left.rstrip('%')) / 100)
        else:
            relative_x = int(prompt_x_from_left)
        if '%' in prompt_y_from_bottom:
            pixels_from_bottom = int(target_height * int(prompt_y_from_bottom.rstrip('%')) / 100)
        else:
            pixels_from_bottom = int(prompt_y_from_bottom)
        relative_y = target_height - pixels_from_bottom

        # Capture configuration
        gxi_dir = read_merged_key('TARGET_DIR')
        if not gxi_dir:
            gxi_dir = '.imagine_targets'
        gxi_dir = os.path.expanduser(gxi_dir)
        os.makedirs(gxi_dir, exist_ok=True)

        capture_mode = int(read_merged_key('CAPTURE_ENABLED') or '0')
        capture_tool = read_merged_key('CAPTURE_TOOL') or 'maim'

        capture_x_from_left_str = read_merged_key('CAPTURE_X_FROM_LEFT') or '50%'
        capture_y_from_bottom_str = read_merged_key('CAPTURE_Y_FROM_BOTTOM') or '10%'
        capture_ratio_str = read_merged_key('CAPTURE_RATIO') or '50%'
        if '%' in capture_x_from_left_str:
            capture_left_edge = int(target_width * int(capture_x_from_left_str.rstrip('%')) / 100)
        else:
            capture_left_edge = int(capture_x_from_left_str)
        if '%' in capture_y_from_bottom_str:
            capture_bottom_edge = int(target_height * int(capture_y_from_bottom_str.rstrip('%')) / 100)
        else:
            capture_bottom_edge = int(capture_y_from_bottom_str)
        if '%' in capture_ratio_str:
            capture_ratio = float(capture_ratio_str.rstrip('%')) / 100
        else:
            capture_ratio = float(capture_ratio_str)
        capture_ratio = max(0.1, min(1.0, capture_ratio))

        capture_side = int(target_width * capture_ratio)
        rect_x = capture_left_edge
        rect_y = target_height - capture_bottom_edge - capture_side
        rect_geometry = f"{capture_side}x{capture_side}+{rect_x}+{rect_y}"

        # Stack position (from on_fire)
        try:
            output = subprocess.check_output(['xdotool', 'getdisplaygeometry']).decode().strip()
            sw, sh = map(int, output.split())
        except Exception:
            sw, sh = 1920, 1080

        center_x = (sw - target_width) // 2
        center_y = (sh - target_height) // 2

        offset_x = int(read_merged_key('FIRE_STACK_X_OFFSET') or 0)
        offset_y = int(read_merged_key('FIRE_STACK_Y_OFFSET') or 0)

        stack_x = center_x + offset_x
        stack_y = center_y + offset_y

        # Single absolute mouse move to prompt position in stack
        absolute_x = stack_x + relative_x
        absolute_y = stack_y + relative_y

        subprocess.run(['xdotool', 'mousemove', str(absolute_x), str(absolute_y)], capture_output=True)
        if debug:
            print(f"DEBUG: Single absolute mouse move to prompt in stack ({absolute_x}, {absolute_y})")

        def update_status(text):
            GLib.idle_add(lambda: self.status_label.set_text(text) or False)

        update_status("Firing... 0 shots")

        live_windows_file = read_merged_key('WINDOW_LIST')
        if not live_windows_file:
            msg = "No WINDOW_LIST configured; aborting daemon."
            subprocess.call(['gxmessage', msg, '-title', 'Daemon Error', '-center', '-buttons', 'OK:0'])
            update_status("Ready")
            return

        live_windows_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), live_windows_file) if not os.path.isabs(live_windows_file) else live_windows_file

        captured_urls = set()
        urls = self.url_list
        log_debug("DAEMON URL LIST", f"Count: {len(urls)} | Sample: {urls[:3] if urls else 'empty'}")

        active_prompt = prompt  # the fired prompt

        for round_num in range(1, fire_count + 1):
            log_debug(f"ROUND START {round_num}/{fire_count}", "Beginning round")
            if round_num > 1:
                time.sleep(round_delay)

            if not os.path.exists(live_windows_file) or os.stat(live_windows_file).st_size == 0:
                log_debug("ROUND SKIP", "No window list file or empty")
                continue

            with open(live_windows_file, 'r', encoding='utf-8') as f:
                window_ids = [line.strip() for line in f if line.strip()]
            if not window_ids:
                log_debug("ROUND SKIP", "No window IDs")
                continue

            log_debug(f"ROUND {round_num}", f"Processing {len(window_ids)} windows")

            for idx, wid in enumerate(window_ids, start=1):
                if not self.firing:
                    log_debug("DAEMON STOP", "self.firing = False - stopping")
                    break

                try:
                    log_debug(f"TARGET {idx}/{len(window_ids)} WID {wid}", "Starting processing")

                    if debug:
                        active = subprocess.check_output(['xdotool', 'getactivewindow'], text=True).strip()
                        log_debug(f"TARGET {idx} BEFORE", f"ACTIVE: {active}")

                    # Scroll clicks (3 wheel downs) to position input box
                    subprocess.run(['xdotool', 'click', '--repeat', '3', '4'], capture_output=True)

                    # Single left click to raise/focus window
                    subprocess.run(['xdotool', 'click', '--clearmodifiers', '--window', str(wid), '1'], capture_output=True)

                    # Capture per window
                    if capture_mode > 0:
                        cycle_idx = idx - 1
                        cycle_url = urls[cycle_idx % len(urls)]
                        log_debug("CAPTURE CYCLE", f"Window idx {idx} | Cycle idx {cycle_idx % len(urls)} | URL: {cycle_url}")

                        safe_name = urllib.parse.quote(cycle_url, safe='')
                        capture_path = os.path.join(gxi_dir, f"{safe_name}.png")

                        if cycle_url in captured_urls:
                            log_debug("CAPTURE SKIP", f"Already captured this run for URL {cycle_url}")
                        else:
                            do_capture = capture_mode == 2 or (capture_mode == 1 and not os.path.exists(capture_path))
                            if do_capture:
                                if capture_tool == 'maim':
                                    cmd = ['maim', '--hidecursor', '-g', rect_geometry, '-i', str(wid), capture_path]
                                elif capture_tool == 'import':
                                    cmd = ['import', '-window', str(wid), '-crop', f"{capture_side}x{capture_side}+{rect_x}+{rect_y}", capture_path]
                                elif capture_tool == 'scrot':
                                    cmd = ['scrot', '-a', f"{rect_x},{rect_y},{capture_side},{capture_side}", '-u', capture_path]
                                else:
                                    cmd = ['maim', '--hidecursor', '-g', rect_geometry, '-i', str(wid), capture_path]

                                cmd_str = ' '.join(shlex.quote(p) for p in cmd)
                                log_debug("CAPTURE EXEC", f"Path: {capture_path} | CMD: {cmd_str}")
                                subprocess.run(cmd, capture_output=True)
                                print("CAPTURED thumbnail to", capture_path)
                                GLib.idle_add(self.parent_app.editor.refresh_targets)
                                captured_urls.add(cycle_url)
                            else:
                                log_debug("CAPTURE SKIP", f"Mode 1 - exists: {capture_path}")

                    # Interaction
                    if not is_harvest_only:
                        log_debug("INTERACTION START", "Custom prompt - proceeding")

                        if prompt != '~' and prompt != '#':
                            try:
                                clipboard_set(prompt)
                            except Exception as e:
                                log_debug("CLIPBOARD FAIL", str(e))
                                subprocess.call(['gxmessage', f"ERROR: Clipboard set failed\n\n{e}", '-title', 'Clipboard Error', '-center', '-buttons', 'OK:0'])

                        if single_xdotool:
                            key_cmd_base = ['xdotool', 'key', '--window', str(wid), '--clearmodifiers']
                            if prompt != '~' and prompt != '#':
                                key_cmd = key_cmd_base + ['ctrl+a', 'ctrl+v', 'Return']
                            elif prompt == '~':
                                key_cmd = key_cmd_base + ['ctrl+a', 'Delete', 'Return']
                            elif prompt == '#':
                                key_cmd = key_cmd_base + ['Return']
                            else:
                                key_cmd = []

                            if key_cmd:
                                cmd_str = ' '.join(shlex.quote(p) for p in key_cmd)
                                log_debug("INTERACTION KEY STACKED", cmd_str)
                                subprocess.run(key_cmd, capture_output=True)
                        else:
                            if prompt != '~' and prompt != '#':
                                subprocess.run(['xdotool', 'key', '--window', str(wid), '--clearmodifiers', 'ctrl+a'], capture_output=True)
                                subprocess.run(['xdotool', 'key', '--window', str(wid), '--clearmodifiers', 'ctrl+v'], capture_output=True)
                                subprocess.run(['xdotool', 'key', '--window', str(wid), '--clearmodifiers', 'Return'], capture_output=True)
                            elif prompt == '~':
                                subprocess.run(['xdotool', 'key', '--window', str(wid), '--clearmodifiers', 'ctrl+a'], capture_output=True)
                                subprocess.run(['xdotool', 'key', '--window', str(wid), '--clearmodifiers', 'Delete'], capture_output=True)
                                subprocess.run(['xdotool', 'key', '--window', str(wid), '--clearmodifiers', 'Return'], capture_output=True)
                            elif prompt == '#':
                                subprocess.run(['xdotool', 'key', '--window', str(wid), '--clearmodifiers', 'Return'], capture_output=True)

                        time.sleep(shot_delay)

                        self.write_gxi_prompt(active_prompt=active_prompt)

                    total_shots += 1
                    time.sleep(inter_window_delay)
                    update_status(f"Firing round {round_num}/{fire_count} — {total_shots} shots fired")

                except Exception as e:
                    log_debug("TARGET ERROR", f"WID {wid} | {e}")
                    subprocess.call(['gxmessage', f"Unexpected error processing window {wid}:\n\n{e}", '-title', 'Daemon Error', '-center', '-buttons', 'OK:0'])

        log_debug("DAEMON COMPLETE", f"Total shots: {total_shots} | Unique thumbnails: {len(captured_urls)}")
        update_status(f"Done — {total_shots} shots fired")
        self.firing = False
        update_env(IMAGINE_ENV, 'FIRE_MODE', 'N')
        GLib.idle_add(self.update_fire_button)
        GLib.timeout_add(5000, lambda: self.status_label.set_text("Ready") or False)
        GLib.idle_add(lambda: self.grid_windows(int(read_merged_key('STAGE_COUNT'))) or False)

    def write_gxi_prompt(self, active_prompt=None):
        if active_prompt is None:
            start, end = self.prompt_buffer.get_bounds()
            current_prompts = self.prompt_buffer.get_text(start, end, False)
            active_prompt = current_prompts.strip()

        if not active_prompt:
            return

        urls = self.url_list
        if not urls:
            return

        unique_urls = set(urls)
        if not unique_urls:
            return

        gxi_dir = read_merged_key('TARGET_DIR')
        if not gxi_dir:
            gxi_dir = '.imagine_targets'
        gxi_dir = os.path.expanduser(gxi_dir)
        try:
            os.makedirs(gxi_dir, exist_ok=True)
        except Exception:
            return

        fired_stage = "1"
        stage_marker = f"STAGE_{fired_stage}"
        history_marker = f".history_{fired_stage}"

        append_line = active_prompt

        for url in unique_urls:
            safe_name = urllib.parse.quote(url, safe='') + '.gxi'
            gxi_path = os.path.join(gxi_dir, safe_name)

            lines = []
            stage_found = False
            history_index = None
            u_stage_found = False

            if os.path.exists(gxi_path):
                try:
                    with open(gxi_path, 'r', encoding='utf-8') as f:
                        for raw_line in f:
                            line = raw_line.rstrip('\n')
                            lines.append(line)
                            stripped = line.strip()
                            if stripped == stage_marker:
                                stage_found = True
                            if stage_found and stripped.startswith('@'):
                                lines[-1] = stripped[1:]  # strip old active marker
                            if stripped == history_marker:
                                history_index = len(lines) - 1
                            if stripped == 'STAGE_U':
                                u_stage_found = True
                except Exception:
                    lines = []

            # Remove matching prompt from STAGE_U (active or plain)
            new_lines = []
            in_u_stage = False
            for line in lines:
                stripped = line.strip()
                if stripped == 'STAGE_U':
                    in_u_stage = True
                    new_lines.append(line)
                    continue
                if in_u_stage and (stripped in ['STAGE_1', 'STAGE_2', 'STAGE_3', 'STAGE_U'] or stripped.startswith('.history_')):
                    in_u_stage = False
                if in_u_stage:
                    prompt_text = stripped.lstrip('@').strip()
                    if prompt_text == active_prompt:
                        continue  # remove matching prompt from STAGE_U
                new_lines.append(line)

            lines = new_lines

            # Append to STAGE_1
            if history_index is not None:
                lines.insert(history_index + 1, append_line)
            else:
                lines += ['', stage_marker, append_line, history_marker, append_line]

            if stage_found:
                for i in range(len(lines)):
                    if lines[i].strip() == stage_marker:
                        if i + 1 >= len(lines) or not lines[i + 1].strip():
                            lines.insert(i + 1, append_line)
                        break
            else:
                lines += ['', stage_marker, append_line, history_marker, append_line]

            try:
                with open(gxi_path, 'w', encoding='utf-8') as f:
                    f.writelines(line + '\n' for line in lines)
            except Exception:
                pass

class EnvGxiEditor(Gtk.Notebook):
    def __init__(self, gun):
        super().__init__()
        self.gun = gun

        self.widget_types = {}
        for key, py_type in CONFIG_SPEC.items():
            if py_type == 'str':
                self.widget_types[key] = 'entry'
            elif py_type == 'int':
                self.widget_types[key] = 'spin_int'
            elif py_type == 'float':
                self.widget_types[key] = 'spin_float'

        self.env_files = {
            'System': SYSTEM_ENV,
            'User': USER_ENV,
            'Imagine': IMAGINE_ENV
        }

        self.env_widgets = {}
        self.system_edit_checkbox = None
        self.system_save_btn = None
        self.system_widgets = []

        for name, path in self.env_files.items():
            tab = self.create_fancy_env_tab(name, path)
            self.append_page(tab, Gtk.Label(label=name))

        all_tab = self.create_all_fancy_env_tab()
        self.append_page(all_tab, Gtk.Label(label="All Env"))

        gxi_tab = self.create_fancy_gxi_tab()
        self.append_page(gxi_tab, Gtk.Label(label=".gxi Targets"))

    def create_fancy_env_tab(self, name, path):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        box.set_border_width(1)
        scrolled.add(box)

        self.env_widgets[name] = {}
        current = load_env_multiline(path)

        if name == 'System':
            # Checkbox to enable editing .system_env
            edit_box = Gtk.Box(spacing=5)
            self.system_edit_checkbox = Gtk.CheckButton(label="Enable editing of .system_env (use with caution)")
            self.system_edit_checkbox.set_active(False)
            self.system_edit_checkbox.connect("toggled", self.on_system_edit_toggled)
            edit_box.pack_start(self.system_edit_checkbox, False, False, 0)
            box.pack_start(edit_box, False, False, 5)

        for key in CONFIG_SPEC:
            value = current.get(key, '')
            label = Gtk.Label(label=key + ":")
            label.set_xalign(0)
            box.pack_start(label, False, False, 0)

            widget_type = self.widget_types[key]
            if widget_type == 'entry':
                widget = Gtk.Entry()
                widget.set_text(value)
            elif widget_type == 'spin_int':
                try:
                    num_val = int(value or 0)
                except ValueError:
                    num_val = 0
                adj = Gtk.Adjustment(value=num_val, lower=0, upper=9999, step_increment=1)
                widget = Gtk.SpinButton(adjustment=adj)
            elif widget_type == 'spin_float':
                try:
                    num_val = float(value or 0.0)
                except ValueError:
                    num_val = 0.0
                adj = Gtk.Adjustment(value=num_val, lower=0, upper=1000, step_increment=0.1)
                widget = Gtk.SpinButton(adjustment=adj, digits=2)
            else:
                widget = Gtk.Entry()
                widget.set_text(value)

            if name == 'System':
                widget.set_sensitive(False)
                self.system_widgets.append(widget)

            box.pack_start(widget, False, False, 0)
            self.env_widgets[name][key] = widget

        btn_box = Gtk.Box(spacing=1)
        box.pack_start(btn_box, False, False, 0)

        if name != 'System':
            save_btn = Gtk.Button(label="Save " + name)
            save_btn.connect("clicked", lambda w, p=path, n=name: self.save_fancy_env(p, n))
            btn_box.pack_start(save_btn, False, False, 0)
        else:
            self.system_save_btn = Gtk.Button(label="Save System")
            self.system_save_btn.set_sensitive(False)
            self.system_save_btn.connect("clicked", lambda w: self.save_fancy_env(SYSTEM_ENV, name))
            btn_box.pack_start(self.system_save_btn, False, False, 0)

        return scrolled

    def on_system_edit_toggled(self, checkbox):
        enabled = checkbox.get_active()
        for widget in self.system_widgets:
            widget.set_sensitive(enabled)
        if self.system_save_btn:
            self.system_save_btn.set_sensitive(enabled)

    def save_fancy_env(self, path, name):
        lines = []
        for key in CONFIG_SPEC:
            widget = self.env_widgets[name][key]
            if isinstance(widget, Gtk.Entry):
                value = widget.get_text()
            elif isinstance(widget, Gtk.SpinButton):
                value = str(widget.get_value_as_int() if self.widget_types[key] == 'spin_int' else widget.get_value())
            else:
                value = ''
            line = f'{key}="{value}"'
            lines.append(line + '\n')

        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not any(stripped.startswith(k + '=') for k in CONFIG_SPEC):
                        lines.append(line)

        with open(path, 'w', encoding='utf-8') as f:
            f.writelines(lines)

        self.gun.save_all()

    def create_all_fancy_env_tab(self):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        box.set_border_width(1)
        scrolled.add(box)

        all_widgets = {}
        merged = {}
        for path in (SYSTEM_ENV, IMAGINE_ENV, USER_ENV):
            merged.update(load_env_multiline(path))

        for key in CONFIG_SPEC:
            value = merged.get(key, '')
            label = Gtk.Label(label=key + ":")
            label.set_xalign(0)
            box.pack_start(label, False, False, 0)

            widget_type = self.widget_types[key]
            if widget_type == 'entry':
                widget = Gtk.Entry()
                widget.set_text(value)
            elif widget_type == 'spin_int':
                try:
                    num_val = int(value or 0)
                except ValueError:
                    num_val = 0
                adj = Gtk.Adjustment(value=num_val, lower=0, upper=9999, step_increment=1)
                widget = Gtk.SpinButton(adjustment=adj)
            elif widget_type == 'spin_float':
                try:
                    num_val = float(value or 0.0)
                except ValueError:
                    num_val = 0.0
                adj = Gtk.Adjustment(value=num_val, lower=0, upper=1000, step_increment=0.1)
                widget = Gtk.SpinButton(adjustment=adj, digits=2)
            else:
                widget = Gtk.Entry()
                widget.set_text(value)

            box.pack_start(widget, False, False, 0)
            all_widgets[key] = widget

        btn_box = Gtk.Box(spacing=1)
        box.pack_start(btn_box, False, False, 0)

        save_btn = Gtk.Button(label="Save All Env")
        save_btn.connect("clicked", lambda w: self.save_all_fancy_env(all_widgets))
        btn_box.pack_start(save_btn, False, False, 0)

        return scrolled

    def save_all_fancy_env(self, widgets):
        lines = []
        for key in CONFIG_SPEC:
            widget = widgets[key]
            if isinstance(widget, Gtk.Entry):
                value = widget.get_text()
            elif isinstance(widget, Gtk.SpinButton):
                value = str(widget.get_value_as_int() if self.widget_types[key] == 'spin_int' else widget.get_value())
            lines.append(f'{key}="{value}"\n')

        with open(USER_ENV, 'w', encoding='utf-8') as f:
            f.writelines(lines)

        self.gun.save_all()

    def create_fancy_gxi_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)

        # Refresh button
        controls_box = Gtk.Box(spacing=1)
        box.pack_start(controls_box, False, False, 0)

        refresh_btn = Gtk.Button(label="Refresh Gallery")
        refresh_btn.connect("clicked", self.refresh_targets)
        controls_box.pack_start(refresh_btn, False, False, 0)

        # Gallery
        gallery_scroll = Gtk.ScrolledWindow()
        gallery_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        gallery_scroll.set_vexpand(True)

        self.gallery = Gtk.FlowBox()
        self.gallery.set_valign(Gtk.Align.START)
        self.gallery.set_max_children_per_line(20)
        self.gallery.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.gallery.connect("child-activated", self.on_gallery_item_activated)
        gallery_scroll.add(self.gallery)
        box.pack_start(gallery_scroll, True, True, 0)

        # TARGET_DESC
        desc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        box.pack_start(desc_box, False, False, 0)

        desc_label = Gtk.Label(label="TARGET_DESC:")
        desc_label.set_xalign(0)
        desc_box.pack_start(desc_label, False, False, 0)

        desc_scroll = Gtk.ScrolledWindow()
        desc_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        desc_scroll.set_vexpand(True)
        desc_scroll.set_min_content_height(100)

        self.desc_view = Gtk.TextView()
        self.desc_view.set_wrap_mode(Gtk.WrapMode.WORD)
        desc_scroll.add(self.desc_view)
        desc_box.pack_start(desc_scroll, True, True, 0)

        # All stages in one scrollable area
        stages_scroll = Gtk.ScrolledWindow()
        stages_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        stages_scroll.set_vexpand(True)

        stages_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        stages_box.set_border_width(10)
        stages_scroll.add(stages_box)
        box.pack_start(stages_scroll, True, True, 0)

        self.stage_listboxes = {}
        self.stage_checkbuttons = []
        stages = ['STAGE_U', 'STAGE_1', 'STAGE_2', 'STAGE_3']
        for stage in stages:
            # Stage label
            stage_label = Gtk.Label()
            stage_label.set_markup(f"<b>{stage}</b>")
            stage_label.set_xalign(0)
            stages_box.pack_start(stage_label, False, False, 0)

            # Listbox for this stage
            listbox = Gtk.ListBox()
            listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
            scrolled_list = Gtk.ScrolledWindow()
            scrolled_list.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scrolled_list.set_min_content_height(150)
            scrolled_list.add(listbox)
            stages_box.pack_start(scrolled_list, True, True, 0)

            self.stage_listboxes[stage] = listbox

        # Shared buttons at bottom
        btn_box = Gtk.Box(spacing=8)
        box.pack_start(btn_box, False, False, 0)

        add_btn = Gtk.Button(label="Add Prompt")
        add_btn.connect("clicked", self.add_prompt)

        delete_btn = Gtk.Button(label="Delete Selected")
        delete_btn.connect("clicked", self.delete_selected)

        activate_btn = Gtk.Button(label="Activate in Gun")
        activate_btn.connect("clicked", self.activate_selected)

        save_btn = Gtk.Button(label="Save .gxi")
        save_btn.connect("clicked", self.save_current_gxi)

        btn_box.pack_start(add_btn, False, False, 0)
        btn_box.pack_start(delete_btn, False, False, 0)
        btn_box.pack_start(activate_btn, False, False, 0)
        btn_box.pack_start(save_btn, False, False, 0)

        self.current_gxi_path = None
        self.refresh_targets()
        self.sync_from_gun()

        return box

    def get_current_stage_listbox(self):
        for stage in ['STAGE_1', 'STAGE_2', 'STAGE_3', 'STAGE_U']:
            listbox = self.stage_listboxes[stage]
            if listbox.get_selected_row():
                return listbox
        return self.stage_listboxes['STAGE_1']

    def add_prompt(self, widget):
        listbox = self.get_current_stage_listbox()

        dialog = Gtk.Dialog(title="Add Prompt", parent=self.get_toplevel())
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OK, Gtk.ResponseType.OK)

        entry = Gtk.Entry()
        box = dialog.get_content_area()
        box.pack_start(entry, True, True, 0)
        dialog.show_all()

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            prompt = entry.get_text().strip()
            if prompt:
                row = Gtk.ListBoxRow()
                hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
                check = Gtk.CheckButton()
                check.connect("toggled", self.on_check_toggled, check)
                self.stage_checkbuttons.append(check)
                label = Gtk.Label(label=prompt)
                label.set_line_wrap(True)
                label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                label.set_max_width_chars(60)
                label.set_xalign(0)
                hbox.pack_start(check, False, False, 0)
                hbox.pack_start(label, True, True, 0)
                row.add(hbox)
                row.prompt = prompt
                row.check = check
                listbox.add(row)
                listbox.show_all()

        dialog.destroy()

    def on_check_toggled(self, button, checked_button):
        if button.get_active():
            for check in self.stage_checkbuttons:
                if check != checked_button:
                    check.set_active(False)

    def delete_selected(self, widget):
        listbox = self.get_current_stage_listbox()
        selected = listbox.get_selected_row()
        if selected:
            if hasattr(selected, 'check') and selected.check in self.stage_checkbuttons:
                self.stage_checkbuttons.remove(selected.check)
            listbox.remove(selected)

    def activate_selected(self, widget):
        listbox = self.get_current_stage_listbox()
        selected = listbox.get_selected_row()
        if selected:
            prompt = selected.prompt
            self.gun.prompt_buffer.set_text(prompt)
            self.gun.save_all()

    def on_gallery_item_activated(self, flowbox, child):
        url = child.url
        self.gun.url_entry.set_text(url)
        self.load_gxi_by_url(url)

    def refresh_targets(self, widget=None):
        for child in self.gallery.get_children():
            self.gallery.remove(child)

        gxi_dir = read_merged_key('TARGET_DIR')
        if not gxi_dir:
            gxi_dir = '.imagine_targets'
        gxi_dir = os.path.expanduser(gxi_dir)
        if not os.path.exists(gxi_dir):
            return

        placeholder = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 128, 128)
        placeholder.fill(0x808080ff)

        files = sorted(os.listdir(gxi_dir))
        for filename in files:
            if filename.endswith('.gxi'):
                decoded = urllib.parse.unquote(filename[:-4])

                thumb_path = os.path.join(gxi_dir, filename[:-4] + '.png')
                thumb = placeholder
                if os.path.exists(thumb_path):
                    try:
                        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(thumb_path, 128, 128, True)
                        thumb = pixbuf
                    except Exception:
                        pass
                else:
                    uuid = decoded.split('/')[-1] if '/' in decoded else 'unknown'
                    if uuid != 'unknown':
                        old_thumb_path = os.path.join(gxi_dir, f"{uuid}.png")
                        if os.path.exists(old_thumb_path):
                            try:
                                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(old_thumb_path, 128, 128, True)
                                thumb = pixbuf
                            except Exception:
                                pass

                item_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                image = Gtk.Image.new_from_pixbuf(thumb)
                item_box.pack_start(image, False, False, 0)

                caption = Gtk.Label(label=decoded)
                caption.set_line_wrap(True)
                caption.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                caption.set_max_width_chars(30)
                caption.set_xalign(0.5)
                item_box.pack_start(caption, False, False, 0)

                child = Gtk.FlowBoxChild()
                child.add(item_box)
                child.url = decoded
                self.gallery.insert(child, -1)

        self.gallery.show_all()

    def load_gxi_by_url(self, url):
        if not url:
            return

        safe_name = urllib.parse.quote(url, safe='') + '.gxi'
        gxi_dir = read_merged_key('TARGET_DIR')
        if not gxi_dir:
            gxi_dir = '.imagine_targets'
        gxi_dir = os.path.expanduser(gxi_dir)
        path = os.path.join(gxi_dir, safe_name)
        if not os.path.exists(path):
            return

        self.current_gxi_path = path

        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        desc_match = re.search(r'^TARGET_DESC=(.*?)(\n\n|\Z)', content, re.MULTILINE | re.DOTALL)
        desc = desc_match.group(1) if desc_match else ''
        self.desc_view.get_buffer().set_text(desc)

        self.stage_checkbuttons = []
        for stage in self.stage_listboxes:
            listbox = self.stage_listboxes[stage]
            for row in listbox.get_children():
                listbox.remove(row)

        current_stage = None
        in_history = False
        at_count = 0
        active_prompt = None

        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('.'):
                if stripped.startswith('.history_'):
                    in_history = True
                continue

            if stripped in ['STAGE_U', 'STAGE_1', 'STAGE_2', 'STAGE_3']:
                current_stage = stripped
                in_history = False
            elif current_stage and not in_history:
                is_active = stripped.startswith('@')
                prompt = stripped.lstrip('@').strip()
                if prompt:
                    if is_active:
                        at_count += 1
                        active_prompt = prompt

                    listbox = self.stage_listboxes[current_stage]
                    row = Gtk.ListBoxRow()
                    hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
                    check = Gtk.CheckButton()
                    check.connect("toggled", self.on_check_toggled, check)
                    self.stage_checkbuttons.append(check)
                    if is_active and at_count == 1:
                        check.set_active(True)
                    label = Gtk.Label(label=prompt)
                    label.set_line_wrap(True)
                    label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                    label.set_max_width_chars(60)
                    label.set_xalign(0)
                    hbox.pack_start(check, False, False, 0)
                    hbox.pack_start(label, True, True, 0)
                    row.add(hbox)
                    row.prompt = prompt
                    row.check = check
                    listbox.add(row)

        for stage in self.stage_listboxes:
            self.stage_listboxes[stage].show_all()

        if at_count > 1:
            for check in self.stage_checkbuttons:
                check.set_active(False)
        elif at_count == 1:
            self.gun.prompt_buffer.set_text(active_prompt)
            self.gun.prompt_is_custom = True

    def save_current_gxi(self, widget):
        if not self.current_gxi_path:
            return

        desc_buffer = self.desc_view.get_buffer()
        desc = desc_buffer.get_text(desc_buffer.get_start_iter(), desc_buffer.get_end_iter(), False)

        stage_prompts = {}
        active_prompt = None
        for stage in ['STAGE_U', 'STAGE_1', 'STAGE_2', 'STAGE_3']:
            listbox = self.stage_listboxes[stage]
            prompts = []
            for row in listbox.get_children():
                check = row.check
                prompt = row.prompt
                prompts.append(prompt)
                if check.get_active():
                    active_prompt = prompt
            stage_prompts[stage] = prompts

        lines = []
        with open(self.current_gxi_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('TARGET_DESC='):
                    lines.append(f'TARGET_DESC={desc}\n')
                elif line.strip() in ['STAGE_U', 'STAGE_1', 'STAGE_2', 'STAGE_3']:
                    current = line.strip()
                    lines.append(line)
                    lines.append(f".history_{current[-1]}\n")
                    for p in stage_prompts[current]:
                        if p == active_prompt:
                            lines.append(f'@{p}\n')
                        else:
                            lines.append(f'{p}\n')
                elif not line.strip().startswith('.history_'):
                    lines.append(line)

        with open(self.current_gxi_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)

        self.gun.save_all()

    def sync_from_gun(self):
        url_input_str = self.gun.url_entry.get_text().strip()
        if not url_input_str:
            return

        for child in self.gallery.get_children():
            if child.url == url_input_str or (os.path.isfile(url_input_str) and child.url in get_urls_from_input(url_input_str)):
                self.gallery.select_child(child)
                child.grab_focus()
                break

        self.load_gxi_by_url(url_input_str)

if __name__ == '__main__':
    try:
        validate_config()
    except Exception as e:
        print("FATAL CONFIGURATION ERROR:", file=sys.stderr)
        print(str(e), file=sys.stderr)
        sys.exit(2)

    app = UnifiedApp()
    app.connect("destroy", Gtk.main_quit)
    app.show_all()
    Gtk.main()
