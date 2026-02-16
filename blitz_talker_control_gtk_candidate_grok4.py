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
import shutil
import argparse
from datetime import datetime

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Pango', '1.0')
from gi.repository import Gtk, Gdk, GLib, Pango, GdkPixbuf

USER_ENV = '.user_env'
IMAGINE_ENV = '.imagine_env'
SYSTEM_ENV = '.system_env'
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEBUG_DIR = os.path.join(SCRIPT_DIR, '.debug')
os.makedirs(DEBUG_DIR, exist_ok=True)

RUNTIME_KEYS = {'FIRE_MODE'}

CAT_FILE = 1 << 0
CAT_WINDOW = 1 << 1
CAT_INPUT = 1 << 2
CAT_DAEMON = 1 << 3
CAT_GUI = 1 << 4
CAT_INIT = 1 << 5
CAT_GEOM = 1 << 6

GLOBAL_DEBUG_MASK = CAT_FILE | CAT_WINDOW | CAT_INPUT | CAT_DAEMON | CAT_GUI | CAT_INIT | CAT_GEOM

def log_debug(category, content):
    if GLOBAL_DEBUG_MASK & category:
        print(f"[DEBUG {category_name(category)}] {content}")

def category_name(bit):
    names = {
        CAT_FILE: "FILE",
        CAT_WINDOW: "WINDOW",
        CAT_INPUT: "INPUT",
        CAT_DAEMON: "DAEMON",
        CAT_GUI: "GUI",
        CAT_INIT: "INIT",
        CAT_GEOM: "GEOM",
    }
    return names.get(bit, "UNKNOWN")

log_debug(CAT_INIT, "Script boot - full debug enabled by default")

def _temp_read_key(file, key):
    full_path = os.path.join(SCRIPT_DIR, file)
    if not os.path.exists(full_path):
        return None
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip().startswith(f'{key}='):
                    v = line.split('=', 1)[1]
                    v = v.split('#', 1)[0].strip().strip('"\'')
                    return v
    except:
        pass
    return None

def _temp_read_merged_key(key):
    for file in (USER_ENV, IMAGINE_ENV, SYSTEM_ENV):
        val = _temp_read_key(file, key)
        if val is not None:
            return val
    return None

debug_cats_str = _temp_read_merged_key('DEBUG_CATEGORIES')
if debug_cats_str is not None:
    debug_cats_str = debug_cats_str.strip().strip('"').strip("'")
    if debug_cats_str:
        debug_cats = [c.strip().lower() for c in debug_cats_str.split(',') if c.strip()]
        cat_map = {
            'file': CAT_FILE,
            'window': CAT_WINDOW,
            'input': CAT_INPUT,
            'daemon': CAT_DAEMON,
            'gui': CAT_GUI,
            'init': CAT_INIT,
            'geom': CAT_GEOM,
        }
        if 'off' in debug_cats:
            GLOBAL_DEBUG_MASK = 0
            log_debug(CAT_INIT, "DEBUG_CATEGORIES=off - all logging disabled")
        else:
            for cat in debug_cats:
                if cat in cat_map:
                    GLOBAL_DEBUG_MASK &= ~cat_map[cat]
                    log_debug(CAT_INIT, f"Disabled debug category: {cat}")

log_debug(CAT_INIT, f"Final debug mask: 0x{GLOBAL_DEBUG_MASK:02x}")

def normalize_for_compare(v):
    v = v.strip()
    try:
        f = float(v)
        if f.is_integer():
            return str(int(f))
        return f'{f:.10f}'.rstrip('0').rstrip('.')
    except ValueError:
        return v

def read_key(file, key):
    full_path = os.path.join(SCRIPT_DIR, file)
    if not os.path.exists(full_path):
        log_debug(CAT_FILE, f"Read missing: {full_path}")
        return None
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            log_debug(CAT_FILE, f"Read open: {full_path}")
            for line in f:
                if line.strip().startswith(f'{key}='):
                    v = line.split('=', 1)[1]
                    v = v.split('#', 1)[0].strip().strip('"\'')
                    log_debug(CAT_FILE, f"Read key {key} = {v} from {full_path}")
                    return v
            log_debug(CAT_FILE, f"Read key {key} not found in {full_path}")
    except Exception as e:
        log_debug(CAT_FILE, f"Read error {full_path}: {e}")
    return None

def read_merged_key(key):
    source = None
    for file in (USER_ENV, IMAGINE_ENV, SYSTEM_ENV):
        val = read_key(file, key)
        if val is not None:
            source = file
            break
    if val is not None:
        log_debug(CAT_FILE, f"Merged key {key} = {val} from {source}")
        return val
    log_debug(CAT_FILE, f"Merged key {key} not found in any file")
    return None

def safe_int(val, default=0):
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default

def safe_float(val, default=0.0):
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def percent_to_pixels(percent_str, dimension):
    if '%' in percent_str:
        return int(dimension * int(percent_str.rstrip('%')) / 100)
    return int(percent_str)

def load_env_multiline(path):
    env = {}
    full_path = os.path.join(SCRIPT_DIR, path)
    if not os.path.exists(full_path):
        return env
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            log_debug(CAT_FILE, f"Load env multiline open: {full_path}")
            lines = f.readlines()
        i = 0
        while i < len(lines):
            line = lines[i].rstrip('\n')
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                i += 1
                continue
            m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$', line)
            if not m:
                i += 1
                continue
            key = m.group(1)
            value = m.group(2)
            while i + 1 < len(lines):
                next_line = lines[i + 1].rstrip('\n')
                next_stripped = next_line.strip()
                if next_stripped.startswith('#') or re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=', next_line):
                    break
                value += '\n' + next_line
                i += 1
                if not next_line.rstrip().endswith('\\'):
                    break
            value = value.split('#', 1)[0]
            value = value.replace('\\\n', '')
            value = re.sub(r'\s*\n\s*', ' ', value)
            value = value.strip()
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1].strip()
            env[key] = value
            log_debug(CAT_FILE, f"Parsed from {path}: {key} = {env[key]}")
            i += 1
    except Exception as e:
        log_debug(CAT_FILE, f"Load env multiline error {full_path}: {e}")
    return env

def get_merged_multiline(key):
    for file in (USER_ENV, IMAGINE_ENV, SYSTEM_ENV):
        env = load_env_multiline(file)
        if key in env:
            log_debug(CAT_FILE, f"Merged multiline {key} = {repr(env[key])} from {file}")
            return env[key]
    log_debug(CAT_FILE, f"Merged multiline {key} not found")
    return ''

def load_flags(key):
    val = get_merged_multiline(key)
    val = val.replace('\\', ' ')
    val = re.sub(r'\s+', ' ', val)
    flags = shlex.split(val) if val else []
    log_debug(CAT_FILE, f"Loaded flags for {key}: {flags}")
    return flags

def update_env(file, key, value):
    full_path = os.path.join(SCRIPT_DIR, file)
    lines = []
    removed_old = 0
    if os.path.exists(full_path):
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                old_lines = f.readlines()
            for line in old_lines:
                if line.strip().startswith(f'{key}='):
                    removed_old += 1
                    continue
                lines.append(line)
        except Exception as e:
            log_debug(CAT_FILE, f"Update env read error {full_path}: {e}")
            lines = []
    lines.append(f'{key}="{value}"\n')
    try:
        with open(full_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
            f.flush()
            os.fsync(f.fileno())
            log_debug(CAT_FILE, f"Write {key} = {value} to {full_path} (removed {removed_old} old lines)")
    except Exception as e:
        log_debug(CAT_FILE, f"Write error {full_path}: {e}")

def prune_user_env():
    system_values = load_env_multiline(SYSTEM_ENV)
    full_path = os.path.join(SCRIPT_DIR, USER_ENV)
    if not os.path.exists(full_path):
        log_debug(CAT_FILE, f"Prune skip: {full_path} missing")
        return
    lines = []
    removed = 0
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            old_lines = f.readlines()
        log_debug(CAT_FILE, f"Prune start: {full_path} has {len(old_lines)} lines")
        for raw_line in old_lines:
            line = raw_line.rstrip('\n')
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                lines.append(raw_line)
                continue
            m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$', line)
            if not m:
                lines.append(raw_line)
                continue
            key = m.group(1)
            if key.startswith(('ENV_EDITOR_', 'GXI_EDITOR_', 'PANEL_DEFAULT_')):
                lines.append(raw_line)
                continue
            rest = m.group(2).lstrip()
            value_part = rest.split('#', 1)[0].strip()
            parsed_value = value_part.strip('"\'')
            if key not in system_values:
                lines.append(raw_line)
                continue
            sys_val = system_values[key]
            norm_user = normalize_for_compare(parsed_value)
            norm_sys = normalize_for_compare(sys_val)
            log_debug(CAT_FILE, f"Comparing {key}: parsed_user='{parsed_value}' norm_user='{norm_user}' sys='{sys_val}' norm_sys='{norm_sys}'")
            if key == 'DEFAULT_PROMPT':
                lines.append(raw_line)
                continue
            if norm_user == norm_sys:
                log_debug(CAT_FILE, f"Prune redundant {key} = {norm_user} from {full_path}")
                removed += 1
                continue
            lines.append(raw_line)
        if removed > 0:
            with open(full_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            log_debug(CAT_FILE, f"Prune complete: removed {removed} redundant lines from {full_path}")
        else:
            log_debug(CAT_FILE, f"Prune complete: no redundant lines in {full_path}")
    except Exception as e:
        log_debug(CAT_FILE, f"Prune error {full_path}: {e}")

def prune_imagine_env():
    system_values = load_env_multiline(SYSTEM_ENV)
    full_path = os.path.join(SCRIPT_DIR, IMAGINE_ENV)
    if not os.path.exists(full_path):
        log_debug(CAT_FILE, f"Prune skip: {full_path} missing")
        return
    lines = []
    removed = 0
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            old_lines = f.readlines()
        log_debug(CAT_FILE, f"Prune start: {full_path} has {len(old_lines)} lines")
        for raw_line in old_lines:
            line = raw_line.rstrip('\n')
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                lines.append(raw_line)
                continue
            m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$', line)
            if not m:
                lines.append(raw_line)
                continue
            key = m.group(1)
            if key in RUNTIME_KEYS:
                lines.append(raw_line)
                continue
            rest = m.group(2).lstrip()
            value_part = rest.split('#', 1)[0].strip()
            parsed_value = value_part.strip('"\'')
            if key not in system_values:
                lines.append(raw_line)
                continue
            sys_val = system_values[key]
            norm_imagine = normalize_for_compare(parsed_value)
            norm_sys = normalize_for_compare(sys_val)
            log_debug(CAT_FILE, f"Comparing {key}: parsed_imagine='{parsed_value}' norm_imagine='{norm_imagine}' sys='{sys_val}' norm_sys='{norm_sys}'")
            if norm_imagine == norm_sys:
                log_debug(CAT_FILE, f"Prune redundant {key} = {norm_imagine} from {full_path}")
                removed += 1
                continue
            lines.append(raw_line)
        if removed > 0:
            with open(full_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            log_debug(CAT_FILE, f"Prune complete: removed {removed} redundant lines from {full_path}")
        else:
            log_debug(CAT_FILE, f"Prune complete: no redundant lines in {full_path}")
    except Exception as e:
        log_debug(CAT_FILE, f"Prune error {full_path}: {e}")

def dedupe_and_prune_startup():
    log_debug(CAT_INIT, "Startup dedupe and prune start")
    prune_user_env()
    prune_imagine_env()
    log_debug(CAT_INIT, "Startup dedupe and prune complete")

def get_clipboard():
    try:
        return subprocess.check_output(['xclip', '-selection', 'clipboard', '-o'], timeout=2).decode('utf-8').strip()
    except Exception as e:
        log_debug(CAT_DAEMON, f"Clipboard read error: {e}")
        return ''

def clipboard_set(text):
    try:
        subprocess.run(['xclip', '-selection', 'clipboard'], input=text.encode('utf-8'), check=True)
    except:
        pass

def xdo_resize_move(wid, width, height, x, y):
    cmd = ['xdotool', 'windowsize', '--sync', wid, str(width), str(height),
           'windowmove', wid, str(x), str(y)]
    if GLOBAL_DEBUG_MASK & CAT_WINDOW:
        log_debug(CAT_WINDOW, f"XDO RESIZE/MOVE | WID {wid} | Size {width}x{height} | Pos {x},{y}")
    subprocess.run(cmd, capture_output=True)

def get_urls_from_input(input_str):
    input_str = input_str.strip()
    urls = []
    if os.path.isfile(input_str):
        try:
            with open(input_str, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.split('#', 1)[0].strip().strip('"\'')
                    if line:
                        urls.append(line)
        except Exception as e:
            log_debug(CAT_FILE, f"Failed to read URL file {input_str}: {e}")
    else:
        parts = re.split(r'[,\s]+', input_str)
        for p in parts:
            p = p.strip().strip('"\'')
            if p:
                urls.append(p)
    return urls

def parse_gxi(path):
    if not os.path.exists(path):
        return [], {'U':[], '1':[], '2':[], '3':[]}, {'U':[], '1':[], '2':[], '3':[]}
    header_lines = []
    prompts = {'U':[], '1':[], '2':[], '3':[]}
    histories = {'U':[], '1':[], '2':[], '3':[]}
    current_stage = None
    in_history = False
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(('TARGET_URL=', 'BORN_ON=', 'TARGET_DESC=')):
                header_lines.append(line)
            elif stripped in ['STAGE_U', 'STAGE_1', 'STAGE_2', 'STAGE_3']:
                current_stage = stripped[6:]
                in_history = False
            elif stripped.startswith('.history_'):
                in_history = True
            elif current_stage:
                if in_history:
                    histories[current_stage].append(line.rstrip('\n'))
                else:
                    prompts[current_stage].append(line.rstrip('\n'))
            else:
                header_lines.append(line)
    except Exception as e:
        log_debug(CAT_FILE, f"Failed to parse GXI {path}: {e}")
    return header_lines, prompts, histories

def write_gxi(path, header_lines, prompts, histories):
    new_lines = header_lines[:3] if len(header_lines) >= 3 else header_lines
    new_lines.append("\n")
    for stage in ['U', '1', '2', '3']:
        new_lines.append(f"STAGE_{stage}\n")
        for p in prompts.get(stage, []):
            new_lines.append(p + '\n')
        history_marker = '.history_U' if stage == 'U' else f".history_{stage}"
        new_lines.append(f"{history_marker}\n")
        for h in histories.get(stage, []):
            new_lines.append(h + '\n')
        new_lines.append("\n")
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        log_debug(CAT_FILE, f"Wrote merged GXI to {path}")
    except Exception as e:
        log_debug(CAT_FILE, f"Failed to write GXI {path}: {e}")

def apply_overflow(prompts):
    order = ['U', '1', '2', '3']
    for i in range(len(order) - 1):
        stage = order[i]
        while len(prompts[stage]) > 5:
            overflow = prompts[stage].pop()
            prompts[order[i+1]].insert(0, overflow)

class BlitzControl(Gtk.Box):
    def __init__(self, parent_app, startup_source=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.parent_app = parent_app
        self.set_border_width(10)
        self.firing = False
        self.daemon_thread = None
        self.current_url = None
        self.active_urls = set()
        self.current_wids = []
        self.cycle_urls = []
        self.target_dir = read_merged_key('TARGET_DIR')
        if not self.target_dir:
            sys.exit("ERROR: TARGET_DIR not defined in env files - set it and restart")
        self.target_dir = os.path.join(SCRIPT_DIR, self.target_dir)
        os.makedirs(self.target_dir, exist_ok=True)
        self.archive_dir = read_merged_key('ARCHIVE_DIR')
        if not self.archive_dir:
            sys.exit("ERROR: ARCHIVE_DIR not defined in env files - set it and restart")
        self.archive_dir = os.path.join(SCRIPT_DIR, self.archive_dir)
        os.makedirs(self.archive_dir, exist_ok=True)
        self.current_dir = self.target_dir
        self.is_archive = False
        self.target_list_file = os.path.join(SCRIPT_DIR, read_merged_key('TARGET_LIST') or 'live_windows.txt')
        if os.path.exists(self.target_list_file):
            try:
                with open(self.target_list_file, 'r', encoding='utf-8') as f:
                    self.active_urls = {line.strip() for line in f if line.strip()}
            except:
                pass
        self.gxi_paths = {}
        self.active_checks = {}
        self.row_widgets = {}
        self.current_gxi_path = None
        self.current_histories = {}
        self.updating_merged = False
        self.system_override_enabled = False
        self.debug_checks = []
        update_env(IMAGINE_ENV, 'FIRE_MODE', 'N')

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.pack_start(main_box, True, True, 0)

        live_prompt_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        main_box.pack_start(live_prompt_row, False, False, 0)

        prompt_btn = Gtk.Button()
        prompt_btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        prompt_title = Gtk.Label(label="Live Prompt:")
        prompt_push = Gtk.Label()
        prompt_push.set_markup("<small>PUSH TO ALL ACTIVE</small>")
        prompt_btn_box.pack_start(prompt_title, False, False, 0)
        prompt_btn_box.pack_start(prompt_push, False, False, 0)
        prompt_btn.add(prompt_btn_box)
        prompt_btn.connect("clicked", self.on_push_prompt)
        live_prompt_row.pack_start(prompt_btn, False, False, 0)

        live_prompt_scrolled = Gtk.ScrolledWindow()
        live_prompt_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        live_prompt_scrolled.set_min_content_height(72)
        live_prompt_scrolled.set_hexpand(True)
        self.live_prompt_view = Gtk.TextView()
        self.live_prompt_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.live_prompt_view.set_editable(True)
        live_prompt_scrolled.add(self.live_prompt_view)
        live_prompt_row.pack_start(live_prompt_scrolled, True, True, 0)

        live_comment_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        main_box.pack_start(live_comment_row, False, False, 0)

        comment_btn = Gtk.Button()
        comment_btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        comment_title = Gtk.Label(label="Live Comment:")
        comment_push = Gtk.Label()
        comment_push.set_markup("<small>PUSH TO ALL ACTIVE</small>")
        comment_btn_box.pack_start(comment_title, False, False, 0)
        comment_btn_box.pack_start(comment_push, False, False, 0)
        comment_btn.add(comment_btn_box)
        comment_btn.connect("clicked", self.on_push_comment)
        live_comment_row.pack_start(comment_btn, False, False, 0)

        live_comment_scrolled = Gtk.ScrolledWindow()
        live_comment_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        live_comment_scrolled.set_min_content_height(72)
        live_comment_scrolled.set_hexpand(True)
        self.live_comment_view = Gtk.TextView()
        self.live_comment_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.live_comment_view.set_editable(True)
        live_comment_scrolled.add(self.live_comment_view)
        live_comment_row.pack_start(live_comment_scrolled, True, True, 0)

        prompt_toggle_box = Gtk.Box(spacing=8)
        main_box.pack_start(prompt_toggle_box, False, False, 0)
        self.send_prompt_check = Gtk.CheckButton(label="Send Prompt (uncheck to regen existing prompt in browser)")
        self.send_prompt_check.set_active(True)
        prompt_toggle_box.pack_start(self.send_prompt_check, False, False, 0)

        bottom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        bottom_box.set_homogeneous(False)
        main_box.pack_start(bottom_box, False, False, 0)

        stage_btn = Gtk.Button(label="STAGE")
        stage_btn.connect("clicked", self.on_stage)
        bottom_box.pack_start(stage_btn, False, False, 0)

        self.fire_btn = Gtk.Button(label="FIRE")
        self.fire_btn.connect("clicked", self.on_fire)
        bottom_box.pack_start(self.fire_btn, False, False, 0)

        spinner_stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bottom_box.pack_start(spinner_stack, False, False, 0)

        rounds_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        rounds_label = Gtk.Label(label="Rounds:")
        rounds_box.pack_start(rounds_label, False, False, 0)
        fire_count_val = safe_int(read_merged_key('FIRE_COUNT') or 1)
        self.fire_spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(value=fire_count_val, lower=1, upper=999, step_increment=1))
        self.fire_spin.set_width_chars(4)
        rounds_box.pack_start(self.fire_spin, False, False, 0)
        spinner_stack.pack_start(rounds_box, False, False, 0)

        targets_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        targets_label = Gtk.Label(label="Targets:")
        targets_box.pack_start(targets_label, False, False, 0)
        stage_count_val = safe_int(read_merged_key('STAGE_COUNT') or 24)
        self.stage_spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(value=stage_count_val, lower=1, upper=2000, step_increment=1))
        self.stage_spin.set_width_chars(4)
        targets_box.pack_start(self.stage_spin, False, False, 0)
        spinner_stack.pack_start(targets_box, False, False, 0)

        self.env_toggle = Gtk.Button(label="ENV Editor ▲")
        self.env_toggle.connect("clicked", self.toggle_env_panel)
        bottom_box.pack_start(self.env_toggle, False, False, 0)

        self.gxi_toggle = Gtk.Button(label="GXI Manager ▲")
        self.gxi_toggle.connect("clicked", self.toggle_gxi_panel)
        bottom_box.pack_start(self.gxi_toggle, False, False, 0)

        quit_btn = Gtk.Button(label="QUIT")
        quit_btn.connect("clicked", self.on_quit)
        bottom_box.pack_start(quit_btn, False, False, 0)

        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
        window { background-color: #2b2b2b; color: #f0f0f0; }
        label.heading { font-weight: bold; color: #ffffff; padding: 8px; }
        label.key-label { font-weight: bold; background-color: #3a3a3a; padding: 8px; }
        frame { margin: 4px; border-radius: 4px; }
        frame.system-column { background-color: #353535; }
        frame.imagine-column { background-color: #404040; }
        frame.user-column { background-color: #505050; }
        frame.merged-column { background-color: #606060; }
        textview, entry, spinbutton { background-color: #454545; color: #f0f0f0; border: 1px solid #555; }
        textview text, entry text { color: #f0f0f0; }
        button { background-color: #555; color: #fff; border-radius: 4px; }
        button:hover { background-color: #666; }
        eventbox { background-color: #2e2e2e; margin: 4px; border-radius: 4px; border: 2px solid transparent; }
        eventbox.active-row { background-color: #335533; border-color: #66ff66; }
        eventbox.selected { background-color: #333366; border-color: #6666ff; }
        eventbox.selected.active-row { background-color: #444477; border-color: #9999ff; }
        button.missing-thumb {
            border: 2px dashed #888888;
            background-color: transparent;
        }
        """)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.current_url = read_merged_key('DEFAULT_CURRENT_URL')

        if startup_source:
            self.handle_startup_source(startup_source)

        self.env_window = Gtk.Window(title="Environment Editor")
        self.env_window.set_resizable(True)
        self.env_window.connect("delete-event", lambda w, e: self.hide_and_save_editor(w, 'ENV') or True)
        self.env_window.connect("configure-event", lambda w, e: self.save_editor_geometry(w, 'ENV_EDITOR') or False)
        env_scrolled = Gtk.ScrolledWindow()
        env_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        env_grid = Gtk.Grid()
        env_grid.set_column_spacing(12)
        env_grid.set_row_spacing(8)
        env_grid.set_border_width(10)
        env_scrolled.add(env_grid)
        self.env_window.add(env_scrolled)
        self.system_env = load_env_multiline(SYSTEM_ENV)
        self.imagine_env = load_env_multiline(IMAGINE_ENV)
        self.user_env = load_env_multiline(USER_ENV)
        self.all_keys = sorted(set(self.system_env.keys()) | set(self.imagine_env.keys()) | set(self.user_env.keys()))
        self.int_keys = {
            "STAGE_COUNT", "FIRE_COUNT", "TARGET_WIDTH", "TARGET_HEIGHT", "CAPTURE_MODE",
            "MAX_OVERLAP_PERCENT", "FIRE_STACK_X_OFFSET", "FIRE_STACK_Y_OFFSET"
        }
        self.float_keys = {
            "STAGE_DELAY", "GRID_START_DELAY", "ROUND_DELAY", "INTER_TARGET_DELAY",
            "SHOT_DELAY", "TARGET_OP_DELAY"
        }
        self.bool_keys = {"AUTO_FIRE"}
        headers = ["Key", ".system_env", ".imagine_env", ".user_env", "Merged (effective)"]
        for col, header_text in enumerate(headers):
            header = Gtk.Label(label=header_text)
            header.get_style_context().add_class("heading")
            header.set_xalign(0)
            env_grid.attach(header, col, 0, 1, 1)
        self.value_widgets = {key: {} for key in self.all_keys}
        row = 1
        for key in self.all_keys:
            key_label = Gtk.Label(label=key)
            key_label.set_xalign(0)
            key_label.get_style_context().add_class("key-label")
            env_grid.attach(key_label, 0, row, 1, 1)

            if key == 'DEBUG_CATEGORIES':
                categories = ['file', 'window', 'input', 'daemon', 'gui', 'init', 'geom']
                cap_labels = ['FILE', 'WINDOW', 'INPUT', 'DAEMON', 'GUI', 'INIT', 'GEOM']

                # system column
                system_val = self.system_env.get(key, '')
                system_widget = Gtk.TextView()
                system_widget.get_buffer().set_text(system_val)
                system_widget.set_editable(False)
                system_frame = Gtk.Frame()
                system_frame.add(system_widget)
                env_grid.attach(system_frame, 1, row, 1, 1)
                self.value_widgets[key]['system'] = system_widget

                # imagine column
                imagine_val = self.imagine_env.get(key, '')
                imagine_widget = Gtk.TextView()
                imagine_widget.get_buffer().set_text(imagine_val)
                imagine_widget.set_editable(False)
                imagine_frame = Gtk.Frame()
                imagine_frame.add(imagine_widget)
                env_grid.attach(imagine_frame, 2, row, 1, 1)
                self.value_widgets[key]['imagine'] = imagine_widget

                # user column: checkboxes in 2-row grid (4 + 3)
                user_grid = Gtk.Grid()
                user_grid.set_column_spacing(12)
                user_grid.set_row_spacing(4)
                user_grid.set_column_homogeneous(True)
                user_val = self.user_env.get(key, '')
                user_cats = [c.strip().lower() for c in user_val.split(',') if c.strip()]
                if 'off' in user_cats:
                    user_cats = []
                for idx, (cap, lower) in enumerate(zip(cap_labels, categories)):
                    check = Gtk.CheckButton(label=cap)
                    check.set_active(lower in user_cats)
                    check.connect("toggled", self.on_debug_check_toggled, key)
                    row_idx = 0 if idx < 4 else 1
                    col_idx = idx % 4
                    user_grid.attach(check, col_idx, row_idx, 1, 1)
                    self.debug_checks.append(check)
                user_frame = Gtk.Frame()
                user_frame.add(user_grid)
                env_grid.attach(user_frame, 3, row, 1, 1)

                # hidden TextView for actual user value
                hidden_user = Gtk.TextView()
                hidden_user.get_buffer().set_text(user_val)
                hidden_user.set_visible(False)
                self.value_widgets[key]['user'] = hidden_user

                # merged column
                merged_val = user_val or imagine_val or system_val
                merged_widget = Gtk.TextView()
                merged_widget.get_buffer().set_text(merged_val)
                merged_widget.set_editable(False)
                merged_frame = Gtk.Frame()
                merged_frame.add(merged_widget)
                env_grid.attach(merged_frame, 4, row, 1, 1)
                self.value_widgets[key]['merged'] = merged_widget

            else:
                columns = [
                    ("system", self.system_env.get(key, ''), True),
                    ("imagine", self.imagine_env.get(key, ''), False),
                    ("user", self.user_env.get(key, ''), False),
                    ("merged", self.user_env.get(key) or self.imagine_env.get(key) or self.system_env.get(key, ''), False),
                ]
                for col_idx, (col_name, val, readonly) in enumerate(columns, start=1):
                    frame = Gtk.Frame()
                    frame.get_style_context().add_class(f"{col_name}-column")
                    frame.set_border_width(4)
                    if key in self.int_keys:
                        init_val = safe_int(val)
                        adj = Gtk.Adjustment(value=init_val, lower=0, upper=999999, step_increment=1)
                        widget = Gtk.SpinButton(adjustment=adj, climb_rate=0.0, digits=0)
                    elif key in self.float_keys:
                        init_val = safe_float(val)
                        adj = Gtk.Adjustment(value=init_val, lower=0.0, upper=999.0, step_increment=0.1)
                        widget = Gtk.SpinButton(adjustment=adj, climb_rate=0.0, digits=2)
                    elif key in self.bool_keys:
                        widget = Gtk.CheckButton()
                        active = str(val).lower() in ('1', 'y', 'true', 'yes', 'on')
                        widget.set_active(active)
                    else:
                        widget = Gtk.TextView()
                        widget.set_wrap_mode(Gtk.WrapMode.WORD)
                        buffer = widget.get_buffer()
                        buffer.set_text(val)
                    if isinstance(widget, (Gtk.SpinButton, Gtk.CheckButton)):
                        widget.set_sensitive(not readonly)
                    else:
                        widget.set_editable(not readonly)
                    frame.add(widget)
                    env_grid.attach(frame, col_idx, row, 1, 1)
                    self.value_widgets[key][col_name] = widget
                    if isinstance(widget, Gtk.SpinButton):
                        widget.connect("value-changed", self.on_value_changed, key, col_name)
                    elif isinstance(widget, Gtk.CheckButton):
                        widget.connect("toggled", self.on_value_changed, key, col_name)
                    elif isinstance(widget, Gtk.TextView):
                        buffer = widget.get_buffer()
                        buffer.connect("changed", self.on_value_changed, key, col_name)

            row += 1

        override_check = Gtk.CheckButton(label="Enable editing .system_env values (auto saves overrides to .user_env on change)")
        override_check.connect("toggled", self.on_system_override_toggled)
        env_grid.attach(override_check, 0, row, 5, 1)
        row += 1
        save_btn = Gtk.Button(label="Save Changes")
        save_btn.connect("clicked", self.save_env_panel)
        env_grid.attach(save_btn, 0, row, 5, 1)

        self.gxi_window = Gtk.Window(title="GXI Manager")
        self.gxi_window.set_resizable(True)
        self.gxi_window.connect("delete-event", lambda w, e: self.hide_and_save_editor(w, 'GXI') or True)
        self.gxi_window.connect("configure-event", lambda w, e: self.save_editor_geometry(w, 'GXI_EDITOR') or False)
        gxi_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.gxi_paned = gxi_paned
        gxi_paned.set_position(500)
        self.gxi_window.add(gxi_paned)

        self.carousel_scrolled = Gtk.ScrolledWindow()
        self.carousel_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.carousel_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.carousel_box.set_border_width(10)
        self.carousel_scrolled.add(self.carousel_box)
        gxi_paned.pack1(self.carousel_scrolled, resize=True, shrink=False)

        editor_scrolled = Gtk.ScrolledWindow()
        editor_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.gxi_editor_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.gxi_editor_box.set_border_width(10)
        editor_scrolled.add(self.gxi_editor_box)
        gxi_paned.pack2(editor_scrolled, resize=True, shrink=False)

        top_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top_controls.set_margin_top(10)
        top_controls.set_margin_bottom(10)
        top_controls.set_halign(Gtk.Align.CENTER)
        reload_btn = Gtk.Button(label="Reload")
        reload_btn.connect("clicked", lambda w: self.load_all_gxi())
        top_controls.pack_start(reload_btn, False, False, 0)
        new_btn = Gtk.Button(label="New Target")
        new_btn.connect("clicked", self.on_new_target)
        top_controls.pack_start(new_btn, False, False, 0)
        select_all_btn = Gtk.Button(label="Select All")
        select_all_btn.connect("clicked", self.select_all_active)
        top_controls.pack_start(select_all_btn, False, False, 0)
        deselect_all_btn = Gtk.Button(label="Deselect All")
        deselect_all_btn.connect("clicked", self.deselect_all_active)
        top_controls.pack_start(deselect_all_btn, False, False, 0)
        self.archive_toggle_btn = Gtk.Button(label="Show Archive")
        self.archive_toggle_btn.connect("clicked", self.toggle_archive)
        top_controls.pack_start(self.archive_toggle_btn, False, False, 0)
        self.carousel_box.pack_start(top_controls, False, False, 0)

        self.thumb_image = Gtk.Image()
        self.gxi_editor_box.pack_start(self.thumb_image, False, False, 0)

        url_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        url_section.set_border_width(4)
        self.url_label = Gtk.Label(label="None")
        self.url_label.set_selectable(True)
        self.url_label.set_xalign(0)
        self.url_label.get_style_context().add_class("dim-label")
        self.url_label.set_hexpand(True)
        url_section.pack_start(self.url_label, False, False, 0)
        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        button_row.set_halign(Gtk.Align.CENTER)
        save_gxi_btn = Gtk.Button(label="Save GXI")
        save_gxi_btn.connect("clicked", self.save_current_gxi)
        button_row.pack_start(save_gxi_btn, False, False, 0)
        url_section.pack_start(button_row, False, False, 0)
        self.gxi_editor_box.pack_start(url_section, False, False, 0)
        self.born_label = Gtk.Label(label="Born On: Never")
        self.born_label.set_xalign(0)
        self.gxi_editor_box.pack_start(self.born_label, False, False, 0)
        comment_box = Gtk.Box(spacing=8)
        comment_label = Gtk.Label(label="Comment:")
        comment_box.pack_start(comment_label, False, False, 0)
        self.comment_entry = Gtk.Entry()
        self.comment_entry.set_hexpand(True)
        comment_box.pack_start(self.comment_entry, True, True, 0)
        self.gxi_editor_box.pack_start(comment_box, False, False, 0)
        stages_frame = Gtk.Frame(label="Stages")
        stages_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        stages_box.set_border_width(8)
        stages_frame.add(stages_box)
        self.gxi_editor_box.pack_start(stages_frame, False, False, 0)
        self.stage_checks = {}
        self.stage_entries = {}
        for stage in ['1', '2', '3', 'U']:
            stage_frame = Gtk.Frame(label=f"STAGE_{stage}")
            stage_grid = Gtk.Grid()
            stage_grid.set_column_spacing(8)
            stage_grid.set_row_spacing(4)
            stage_grid.set_border_width(8)
            stage_frame.add(stage_grid)
            stages_box.pack_start(stage_frame, False, False, 0)
            self.stage_checks[stage] = []
            self.stage_entries[stage] = []
            for i in range(5):
                check = Gtk.CheckButton()
                entry = Gtk.Entry()
                entry.set_hexpand(True)
                entry.connect("changed", self.on_gxi_entry_changed, check)
                stage_grid.attach(check, 0, i, 1, 1)
                stage_grid.attach(entry, 1, i, 1, 1)
                self.stage_checks[stage].append(check)
                self.stage_entries[stage].append(entry)
                check.connect("toggled", self.on_stage_check_toggled, stage, i)

        self.env_window.hide()
        self.gxi_window.hide()

        self.env_saved_width = safe_int(read_merged_key('ENV_EDITOR_WIDTH'))
        self.env_saved_height = safe_int(read_merged_key('ENV_EDITOR_HEIGHT'))
        self.env_saved_x_off = safe_int(read_merged_key('ENV_EDITOR_X_OFFSET'))
        self.env_saved_y_off = safe_int(read_merged_key('ENV_EDITOR_Y_OFFSET'))
        self.gxi_saved_width = safe_int(read_merged_key('GXI_EDITOR_WIDTH'))
        self.gxi_saved_height = safe_int(read_merged_key('GXI_EDITOR_HEIGHT'))
        self.gxi_saved_x_off = safe_int(read_merged_key('GXI_EDITOR_X_OFFSET'))
        self.gxi_saved_y_off = safe_int(read_merged_key('GXI_EDITOR_Y_OFFSET'))
        self.gxi_paned_position = safe_int(read_merged_key('GXI_PANED_POSITION') or 500)

        self.restore_all_geoms()

        GLib.idle_add(self.load_all_gxi)

        self.capture_click_positions = []

        self.update_status("Ready")

        default = get_merged_multiline('DEFAULT_PROMPT').strip()
        buffer = self.live_prompt_view.get_buffer()
        buffer.set_text(default)

    def on_push_prompt(self, widget):
        buffer = self.live_prompt_view.get_buffer()
        start, end = buffer.get_bounds()
        new_prompt = buffer.get_text(start, end, False).strip()
        if not new_prompt:
            return
        active_urls = [u for u in self.url_list if u in self.active_urls]
        if not active_urls:
            return
        for url in active_urls:
            path = self.gxi_paths.get(url)
            if not path:
                continue
            header_lines, prompts, histories = parse_gxi(path)
            prompts['1'].insert(0, '@' + new_prompt)
            apply_overflow(prompts)
            write_gxi(path, header_lines, prompts, histories)
        self.load_current_gxi()

    def on_push_comment(self, widget):
        buffer = self.live_comment_view.get_buffer()
        start, end = buffer.get_bounds()
        new_comment = buffer.get_text(start, end, False).strip()
        active_urls = [u for u in self.url_list if u in self.active_urls]
        if not active_urls:
            return
        for url in active_urls:
            path = self.gxi_paths.get(url)
            if not path:
                continue
            header_lines, prompts, histories = parse_gxi(path)
            new_header = []
            for line in header_lines:
                if line.strip().startswith('TARGET_DESC='):
                    new_header.append(f"TARGET_DESC={new_comment}\n")
                else:
                    new_header.append(line)
            write_gxi(path, new_header, prompts, histories)
        if self.current_gxi_path:
            self.comment_entry.set_text(new_comment)

    def handle_startup_source(self, startup_source):
        if not startup_source:
            return

        if startup_source.startswith('--url-file='):
            file_path = startup_source.split('=', 1)[1]
            urls = get_urls_from_input(file_path)
            added = 0
            for url in urls:
                if self.add_or_select_url(url):
                    added += 1
            log_debug(CAT_INIT, f"Imported {added} new URLs from file {file_path}")
            if added > 0:
                self.load_all_gxi()

        elif startup_source.startswith('--url='):
            url = startup_source.split('=', 1)[1]
            self.add_or_select_url(url)
            self.load_all_gxi()
            self.load_current_gxi()

        elif startup_source.startswith('--gxi='):
            gxi_path = startup_source.split('=', 1)[1]
            if not os.path.exists(gxi_path):
                log_debug(CAT_INIT, f"External GXI not found: {gxi_path}")
                return

            basename = os.path.basename(gxi_path)
            target_path = os.path.join(self.current_dir, basename)

            if os.path.exists(target_path):
                log_debug(CAT_INIT, f"Merging external GXI {gxi_path} into existing {target_path}")
                source_header, source_prompts, source_histories = parse_gxi(gxi_path)
                dest_header, dest_prompts, dest_histories = parse_gxi(target_path)

                for stage in ['U', '1', '2', '3']:
                    for p in reversed(source_prompts[stage]):
                        if p not in dest_prompts[stage]:
                            dest_prompts[stage].insert(0, p)

                apply_overflow(dest_prompts)

                write_gxi(target_path, dest_header, dest_prompts, dest_histories)
            else:
                shutil.copy(gxi_path, target_path)
                log_debug(CAT_INIT, f"Copied external GXI {gxi_path} to target_dir")

            try:
                with open(target_path, 'r', encoding='utf-8') as f:
                    first_line = f.readline().strip()
                    if first_line.startswith('TARGET_URL='):
                        url = first_line.split('=', 1)[1].strip().strip('"\'')
                        self.current_url = url
                        update_env(USER_ENV, 'DEFAULT_CURRENT_URL', url)
                        self.active_urls.add(url)
            except Exception as e:
                log_debug(CAT_INIT, f"Failed to extract URL from {target_path}: {e}")

            self.load_all_gxi()
            if self.current_url:
                self.load_current_gxi()

    def add_or_select_url(self, url):
        if not url:
            return False
        safe_name = urllib.parse.quote(url, safe='') + '.gxi'
        path = os.path.join(self.current_dir, safe_name)
        created = False
        if not os.path.exists(path):
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(f"TARGET_URL={url}\n")
                    f.write(f"BORN_ON={datetime.now().strftime('%Y-%m-%d')}\n")
                    f.write("TARGET_DESC=\n\n")
                    for stage in ['U', '1', '2', '3']:
                        f.write(f"STAGE_{stage}\n\n")
                        f.write(f".history_{stage}\n\n")
                created = True
                log_debug(CAT_INIT, f"Created new GXI for {url}")
            except Exception as e:
                log_debug(CAT_FILE, f"Failed to create GXI for {url}: {e}")
                return False
        self.active_urls.add(url)
        self.current_url = url
        update_env(USER_ENV, 'DEFAULT_CURRENT_URL', url)
        return created

    def update_status(self, text):
        self.parent_app.set_title(f"Blitz Talker — {text}")

    def update_fire_state(self):
        can_fire = bool(self.current_wids) and not self.firing
        self.fire_btn.set_sensitive(can_fire or self.firing)
        self.fire_btn.set_label("STOP" if self.firing else "FIRE")
        self.update_status("Ready" if not self.firing else "Firing...")

    def save_all_geoms(self):
        w, h = self.parent_app.get_size()
        x, y = self.parent_app.get_position()
        log_debug(CAT_GEOM, f"MAIN PANEL BEFORE SAVE: size={w}x{h}, pos=({x},{y})")
        try:
            monitor = Gdk.Display.get_default().get_primary_monitor()
            geom = monitor.get_geometry()
            y_off = geom.height - y - h
            update_env(USER_ENV, 'PANEL_DEFAULT_WIDTH', str(w))
            update_env(USER_ENV, 'PANEL_DEFAULT_HEIGHT', str(h))
            update_env(USER_ENV, 'PANEL_DEFAULT_X_OFFSET', str(x))
            update_env(USER_ENV, 'PANEL_DEFAULT_Y_OFFSET', str(y_off))
            log_debug(CAT_GEOM, f"MAIN PANEL SAVED: width={w}, height={h}, x_off={x}, y_off={y_off}")
        except Exception as e:
            log_debug(CAT_GUI, f"Main panel geometry save error: {e}")

        if self.env_window.get_visible():
            self.save_editor_geometry(self.env_window, 'ENV_EDITOR')
        if self.gxi_window.get_visible():
            self.save_editor_geometry(self.gxi_window, 'GXI_EDITOR')
            pos = self.gxi_paned.get_position()
            update_env(USER_ENV, 'GXI_PANED_POSITION', str(pos))
            log_debug(CAT_GEOM, f"GXI PANED SAVED position={pos}")

    def restore_all_geoms(self):
        width = safe_int(read_merged_key('PANEL_DEFAULT_WIDTH'))
        height = safe_int(read_merged_key('PANEL_DEFAULT_HEIGHT'))
        if width > 0 and height > 0:
            log_debug(CAT_GEOM, f"MAIN PANEL REQUESTED size={width}x{height}")
            self.parent_app.set_default_size(width, height)

        self.env_saved_width = safe_int(read_merged_key('ENV_EDITOR_WIDTH'))
        self.env_saved_height = safe_int(read_merged_key('ENV_EDITOR_HEIGHT'))
        self.env_saved_x_off = safe_int(read_merged_key('ENV_EDITOR_X_OFFSET'))
        self.env_saved_y_off = safe_int(read_merged_key('ENV_EDITOR_Y_OFFSET'))
        self.gxi_saved_width = safe_int(read_merged_key('GXI_EDITOR_WIDTH'))
        self.gxi_saved_height = safe_int(read_merged_key('GXI_EDITOR_HEIGHT'))
        self.gxi_saved_x_off = safe_int(read_merged_key('GXI_EDITOR_X_OFFSET'))
        self.gxi_saved_y_off = safe_int(read_merged_key('GXI_EDITOR_Y_OFFSET'))
        self.gxi_paned_position = safe_int(read_merged_key('GXI_PANED_POSITION') or 500)

    def save_editor_geometry(self, window, prefix):
        try:
            width, height = window.get_size()
            x, y = window.get_position()
            log_debug(CAT_GEOM, f"{prefix.upper()} BEFORE SAVE: size={width}x{height}, pos=({x},{y})")
            monitor = Gdk.Display.get_default().get_primary_monitor()
            geom = monitor.get_geometry()
            x_off = x
            y_off = geom.height - y - height
            update_env(USER_ENV, f'{prefix}_WIDTH', str(width))
            update_env(USER_ENV, f'{prefix}_HEIGHT', str(height))
            update_env(USER_ENV, f'{prefix}_X_OFFSET', str(x_off))
            update_env(USER_ENV, f'{prefix}_Y_OFFSET', str(y_off))
            log_debug(CAT_GEOM, f"{prefix.upper()} SAVED: width={width}, height={height}, x_off={x_off}, y_off={y_off}")
        except Exception as e:
            log_debug(CAT_GUI, f"Editor geometry save error ({prefix}): {e}")

    def apply_editor_geometry(self, window, width, height, x_off, y_off):
        title = window.get_title()
        log_debug(CAT_GEOM, f"{title.upper()} REQUESTED: width={width}, height={height}, x_off={x_off}, y_off={y_off}")
        if width <= 0:
            width = 1400 if 'GXI' in title else 1000
        if height <= 0:
            height = 900 if 'GXI' in title else 600
        window.set_default_size(width, height)
        window.resize(width, height)
        if x_off >= 0 and y_off >= 0:
            try:
                monitor = Gdk.Display.get_default().get_primary_monitor()
                geom = monitor.get_geometry()
                calculated_y = geom.height - height - y_off if height > 0 else geom.height - y_off
                window.move(x_off, calculated_y)
            except Exception as e:
                log_debug(CAT_GUI, f"Editor geometry apply error: {e}")
        actual_w, actual_h = window.get_size()
        actual_x, actual_y = window.get_position()
        log_debug(CAT_GEOM, f"{title.upper()} ACTUAL AFTER APPLY: size={actual_w}x{actual_h}, pos=({actual_x},{actual_y})")

    def toggle_env_panel(self, widget):
        if self.env_window.get_visible():
            self.save_editor_geometry(self.env_window, 'ENV_EDITOR')
            self.save_env_panel()
            self.env_window.hide()
            self.env_toggle.set_label("ENV Editor ▲")
        else:
            self.apply_editor_geometry(self.env_window, self.env_saved_width, self.env_saved_height, self.env_saved_x_off, self.env_saved_y_off)
            self.env_window.show_all()
            self.env_toggle.set_label("ENV Editor ▼")

    def toggle_gxi_panel(self, widget):
        if self.gxi_window.get_visible():
            self.save_editor_geometry(self.gxi_window, 'GXI_EDITOR')
            pos = self.gxi_paned.get_position()
            update_env(USER_ENV, 'GXI_PANED_POSITION', str(pos))
            log_debug(CAT_GEOM, f"GXI PANED SAVED on toggle hide position={pos}")
            self.gxi_window.hide()
            self.gxi_toggle.set_label("GXI Manager ▲")
        else:
            self.apply_editor_geometry(self.gxi_window, self.gxi_saved_width, self.gxi_saved_height, self.gxi_saved_x_off, self.gxi_saved_y_off)
            if self.gxi_paned_position > 0:
                self.gxi_paned.set_position(self.gxi_paned_position)
                log_debug(CAT_GEOM, f"GXI PANED RESTORED position={self.gxi_paned_position}")
            self.gxi_window.show_all()
            self.gxi_toggle.set_label("GXI Manager ▼")
            self.load_all_gxi()

    def hide_and_save_editor(self, window, prefix):
        if prefix == 'ENV':
            self.save_editor_geometry(self.env_window, 'ENV_EDITOR')
            self.save_env_panel()
        elif prefix == 'GXI':
            self.save_editor_geometry(self.gxi_window, 'GXI_EDITOR')
            pos = self.gxi_paned.get_position()
            update_env(USER_ENV, 'GXI_PANED_POSITION', str(pos))
            log_debug(CAT_GEOM, f"GXI PANED SAVED on delete-event position={pos}")
        window.hide()
        return True

    def show_url_mismatch_alert(self):
        dialog = Gtk.MessageDialog(
            transient_for=self.parent_app,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="GXI URL Mismatch Detected"
        )
        dialog.format_secondary_text(
            "The TARGET_URL inside the file does not match the filename encoding.\n"
            "A matching GXI file was found/created and loaded instead."
        )
        dialog.run()
        dialog.destroy()

    def on_system_override_toggled(self, check):
        self.system_override_enabled = check.get_active()
        for key in self.all_keys:
            widget = self.value_widgets[key]['system']
            if widget:
                if isinstance(widget, Gtk.TextView):
                    widget.set_editable(self.system_override_enabled)
                else:
                    widget.set_sensitive(self.system_override_enabled)

    def on_value_changed(self, widget, key, col_name):
        if self.updating_merged:
            return
        self.recompute_merged(key)
        if col_name == 'system' and self.system_override_enabled:
            self.save_single_key(key)

    def recompute_merged(self, key):
        self.updating_merged = True
        user_val = self.get_widget_value(self.value_widgets[key]['user'])
        imagine_val = self.get_widget_value(self.value_widgets[key]['imagine'])
        system_val = self.get_widget_value(self.value_widgets[key]['system'])
        merged_val = user_val or imagine_val or system_val or ''
        widget = self.value_widgets[key]['merged']
        self.set_widget_value(widget, merged_val)
        self.updating_merged = False

    def get_widget_value(self, widget):
        if isinstance(widget, Gtk.SpinButton):
            val = widget.get_value()
            return str(int(val)) if val.is_integer() else str(val)
        elif isinstance(widget, Gtk.CheckButton):
            return '1' if widget.get_active() else '0'
        elif isinstance(widget, Gtk.TextView):
            buffer = widget.get_buffer()
            start, end = buffer.get_bounds()
            return buffer.get_text(start, end, False)
        return ''

    def set_widget_value(self, widget, value):
        if isinstance(widget, Gtk.SpinButton):
            try:
                widget.set_value(float(value))
            except:
                pass
        elif isinstance(widget, Gtk.CheckButton):
            widget.set_active(value.lower() in ('1', 'y', 'true', 'yes', 'on'))
        elif isinstance(widget, Gtk.TextView):
            buffer = widget.get_buffer()
            buffer.set_text(value)

    def save_single_key(self, key):
        merged_widget = self.value_widgets[key]['merged']
        value = self.get_widget_value(merged_widget)
        if key in self.int_keys:
            try:
                value = str(int(float(value)))
            except:
                pass
        system_val = self.system_env.get(key, '')
        if value != system_val:
            update_env(USER_ENV, key, value)
        else:
            full_path = os.path.join(SCRIPT_DIR, USER_ENV)
            if os.path.exists(full_path):
                lines = []
                with open(full_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        if not line.strip().startswith(f'{key}='):
                            lines.append(line)
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.writelines(lines)

    def save_env_panel(self, widget=None):
        changed = False
        for key in self.all_keys:
            if key == 'DEFAULT_PROMPT':
                continue
            merged_widget = self.value_widgets[key]['merged']
            value = self.get_widget_value(merged_widget)
            if key in self.int_keys:
                try:
                    value = str(int(float(value)))
                except:
                    pass
            system_val = self.system_env.get(key, '')
            if value != system_val:
                update_env(USER_ENV, key, value)
                changed = True

        if 'DEFAULT_PROMPT' in self.all_keys:
            user_widget = self.value_widgets['DEFAULT_PROMPT']['user']
            prompt_val = self.get_widget_value(user_widget)
            update_env(USER_ENV, 'DEFAULT_PROMPT', prompt_val)
            changed = True

        if changed:
            prune_user_env()

    def on_debug_check_toggled(self, widget, key):
        categories = ['file', 'window', 'input', 'daemon', 'gui', 'init', 'geom']
        checked = [categories[i] for i, c in enumerate(self.debug_checks) if c.get_active()]
        new_val = ','.join(checked)
        buffer = self.value_widgets[key]['user'].get_buffer()
        buffer.set_text(new_val)
        self.recompute_merged(key)

    def select_all_active(self, widget):
        for check in self.active_checks.values():
            check.set_active(True)

    def deselect_all_active(self, widget):
        for check in self.active_checks.values():
            check.set_active(False)

    def toggle_archive(self, widget):
        self.is_archive = not self.is_archive
        self.current_dir = self.archive_dir if self.is_archive else self.target_dir
        self.archive_toggle_btn.set_label("Hide Archive" if self.is_archive else "Show Archive")
        self.load_all_gxi()

    def archive_gxi(self, widget, url):
        source_path = self.gxi_paths[url]
        dest_dir = self.archive_dir if not self.is_archive else self.target_dir
        dest_path = os.path.join(dest_dir, os.path.basename(source_path))
        try:
            shutil.move(source_path, dest_path)
        except Exception as e:
            log_debug(CAT_FILE, f"Archive move error: {e}")
        self.load_all_gxi()
        if self.current_url == url:
            self.current_url = None
            self.load_current_gxi()

    def on_carousel_row_clicked(self, widget, url):
        self.current_url = url
        self.load_current_gxi()
        for child in self.carousel_box.get_children()[1:]:
            child.get_style_context().remove_class("selected")
        widget.get_style_context().add_class("selected")

    def on_active_toggled(self, check, url):
        if check.get_active():
            self.active_urls.add(url)
        else:
            self.active_urls.discard(url)
        eventbox = self.row_widgets.get(url)
        if eventbox:
            if check.get_active():
                eventbox.get_style_context().add_class("active-row")
            else:
                eventbox.get_style_context().remove_class("active-row")

    def on_new_target(self, widget):
        dialog = Gtk.Dialog(title="New Target URL", transient_for=self.gxi_window if self.gxi_window.get_visible() else self.parent_app, flags=0)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OK, Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        entry.set_text("https://")
        entry.set_activates_default(True)
        dialog.get_content_area().pack_start(entry, True, True, 0)
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.show_all()
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            url = entry.get_text().strip()
            if url:
                self.add_or_select_url(url)
                self.load_all_gxi()
                self.load_current_gxi()
        dialog.destroy()

    def on_gxi_entry_changed(self, entry, check):
        text = entry.get_text().strip()
        check.set_sensitive(bool(text))

    def on_stage_check_toggled(self, check, stage, index):
        if not check.get_sensitive():
            check.set_active(False)
            return
        if check.get_active():
            for s in ['1', '2', '3', 'U']:
                for j in range(5):
                    if s != stage or j != index:
                        self.stage_checks[s][j].set_active(False)
            prompt = self.stage_entries[stage][index].get_text().strip()
            self.update_live_prompt_label(prompt or "")
            if self.current_url and self.current_url in self.active_checks:
                self.active_checks[self.current_url].set_active(True)

    def update_live_prompt_label(self, prompt_text):
        buffer = self.live_prompt_view.get_buffer()
        buffer.set_text(prompt_text)

    def get_active_prompt_for_url(self, url):
        dir_path = self.current_dir
        path = os.path.join(dir_path, urllib.parse.quote(url, safe='') + '.gxi')
        if os.path.exists(path):
            _, prompts, _ = parse_gxi(path)
            for stage in ['1', '2', '3', 'U']:
                stage_prompts = prompts.get(stage, [])
                for p in stage_prompts:
                    if p.startswith('@'):
                        return p.lstrip('@').strip()
        default = get_merged_multiline('DEFAULT_PROMPT').strip()
        if default:
            return default
        return None

    def update_live_prompt_from_selection(self):
        prompt = None
        if self.current_url:
            prompt = self.get_active_prompt_for_url(self.current_url)
        if not prompt:
            default = get_merged_multiline('DEFAULT_PROMPT').strip()
            if default:
                prompt = default
        buffer = self.live_prompt_view.get_buffer()
        buffer.set_text(prompt or "")

    def load_current_gxi(self):
        self.current_gxi_path = None
        self.current_histories = {}
        self.thumb_image.set_from_pixbuf(None)
        self.thumb_image.hide()

        if not self.current_url:
            self.live_comment_view.get_buffer().set_text("")
            self.update_live_prompt_from_selection()
            return

        self.url_label.set_text(self.current_url)
        safe_name = urllib.parse.quote(self.current_url, safe='') + '.gxi'
        path = os.path.join(self.current_dir, safe_name)
        self.current_gxi_path = path
        if not os.path.exists(path):
            log_debug(CAT_FILE, f"LOAD_CURRENT_GXI: File not found for current_url '{self.current_url}' - path {path}")
            self.live_comment_view.get_buffer().set_text("")
            self.update_live_prompt_from_selection()
            return

        file_url = None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                all_lines = f.readlines()
            if all_lines and all_lines[0].strip().startswith('TARGET_URL='):
                file_url_line = all_lines[0].strip()
                file_url = file_url_line.split('=', 1)[1].strip().strip('"\'')
        except:
            pass

        if file_url is None:
            new_line = f"TARGET_URL={self.current_url}\n"
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(new_line)
                    f.writelines(all_lines if 'all_lines' in locals() else [])
                log_debug(CAT_FILE, f"Repaired missing TARGET_URL in {path}")
            except Exception as e:
                log_debug(CAT_FILE, f"Failed to repair TARGET_URL in {path}: {e}")
            file_url = self.current_url

        if file_url != self.current_url:
            correct_safe_name = urllib.parse.quote(file_url, safe='') + '.gxi'
            correct_path = os.path.join(self.current_dir, correct_safe_name)
            if os.path.exists(correct_path):
                self.current_gxi_path = correct_path
                log_debug(CAT_FILE, f"Mismatch: loaded existing correct {correct_path}")
            else:
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        content_lines = f.readlines()[1:] if all_lines and all_lines[0].strip().startswith('TARGET_URL=') else all_lines
                    new_lines = [f"TARGET_URL={file_url}\n"] + content_lines
                    with open(correct_path, 'w', encoding='utf-8') as f:
                        f.writelines(new_lines)
                    self.current_gxi_path = correct_path
                    log_debug(CAT_FILE, f"Mismatch: created new correct {correct_path}")
                except Exception as e:
                    log_debug(CAT_FILE, f"Failed to create correct GXI {correct_path}: {e}")
                    self.current_gxi_path = path
            GLib.idle_add(self.show_url_mismatch_alert)

        header_lines, prompts, histories = parse_gxi(self.current_gxi_path)

        born_on = "Never"
        comment = ""
        for line in header_lines:
            stripped = line.strip()
            if stripped.startswith('BORN_ON='):
                born_on = stripped[8:].strip()
            elif stripped.startswith('TARGET_DESC='):
                comment = stripped[12:].strip()

        self.born_label.set_text(f"Born On: {born_on}")
        self.comment_entry.set_text(comment)
        self.live_comment_view.get_buffer().set_text(comment)

        self.current_histories = histories

        active_prompt = None
        for stage in ['1', '2', '3', 'U']:
            stage_prompts = prompts.get(stage, [])
            for i in range(5):
                entry = self.stage_entries[stage][i]
                check = self.stage_checks[stage][i]
                if i < len(stage_prompts):
                    p = stage_prompts[i]
                    is_active = p.startswith('@')
                    prompt_text = p.lstrip('@')
                    entry.set_text(prompt_text)
                    check.set_sensitive(bool(prompt_text))
                    if is_active:
                        check.set_active(True)
                        active_prompt = prompt_text
                else:
                    entry.set_text('')
                    check.set_sensitive(False)
                    check.set_active(False)

        thumb_path = os.path.join(self.current_dir, urllib.parse.quote(self.current_url, safe='') + '.png')
        if os.path.exists(thumb_path):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(thumb_path, 256, 256, True)
                self.thumb_image.set_from_pixbuf(pixbuf)
                self.thumb_image.show()
                log_debug(CAT_FILE, "LOAD_CURRENT_GXI: Loaded real thumbnail for right pane")
            except Exception as e:
                log_debug(CAT_FILE, f"LOAD_CURRENT_GXI: Right pane thumbnail load failed - hidden: {e}")
        else:
            log_debug(CAT_FILE, "LOAD_CURRENT_GXI: No thumbnail - image hidden in right pane")

        self.update_live_prompt_from_selection()

    def save_current_gxi(self):
        if not self.current_gxi_path or not self.current_url:
            return
        comment = self.comment_entry.get_text()
        born_on = datetime.now().strftime("%Y-%m-%d")
        header_lines, prompts, histories = parse_gxi(self.current_gxi_path)
        new_header = []
        updated = False
        for line in header_lines:
            if line.strip().startswith('BORN_ON='):
                new_header.append(f"BORN_ON={born_on}\n")
                updated = True
            elif line.strip().startswith('TARGET_DESC='):
                new_header.append(f"TARGET_DESC={comment}\n")
                updated = True
            else:
                new_header.append(line)
        if not updated:
            new_header.append(f"BORN_ON={born_on}\n")
            new_header.append(f"TARGET_DESC={comment}\n")
        for stage in ['U', '1', '2', '3']:
            for i in range(5):
                prompt = self.stage_entries[stage][i].get_text().strip()
                is_active = self.stage_checks[stage][i].get_active()
                if i < len(prompts[stage]):
                    prompts[stage][i] = f"{'@' if is_active else ''}{prompt}"
                else:
                    prompts[stage].append(f"{'@' if is_active else ''}{prompt}")
            prompts[stage] = prompts[stage][:5]
        apply_overflow(prompts)
        write_gxi(self.current_gxi_path, new_header, prompts, histories)
        self.update_live_prompt_from_selection()

    def on_stage(self, widget):
        self.save_current_gxi()
        if self.env_window.get_visible():
            self.save_env_panel()
        self.update_status("Staging windows...")
        stage_delay = safe_float(read_merged_key('STAGE_DELAY') or 2.0)
        grid_start_delay = safe_float(read_merged_key('GRID_START_DELAY') or 5)
        self.gentle_target_op('kill')
        self.current_wids = []
        self.capture_click_positions = []
        num = self.stage_spin.get_value_as_int()
        urls = [u for u in self.url_list if u in self.active_urls]
        if not urls:
            self.update_status("Ready")
            self.update_fire_state()
            return
        self.cycle_urls = urls

        head_flags = load_flags('BROWSER_FLAGS_HEAD')
        middle_flags = load_flags('BROWSER_FLAGS_MIDDLE')
        tail_prefix = get_merged_multiline('BROWSER_FLAGS_TAIL')
        browser = read_merged_key('BROWSER') or 'chromium'
        cmd_base = [browser] + head_flags + middle_flags
        for i in range(num):
            url = urls[i % len(urls)]
            if tail_prefix:
                cmd = cmd_base[:] + [tail_prefix + url]
            else:
                cmd = cmd_base[:] + [url]
            subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
            time.sleep(stage_delay)
        GLib.timeout_add(int(grid_start_delay * 1000), lambda: self.grid_windows(num) or False)
        if read_merged_key('AUTO_FIRE') in ('1', 'Y', 'true', 'True'):
            GLib.timeout_add(int((grid_start_delay + 5) * 1000), self.on_fire)

    def on_fire(self, widget=None):
        self.save_current_gxi()
        if self.env_window.get_visible():
            self.save_env_panel()
        if not self.firing:
            if not self.current_wids:
                return
            self.firing = True
            update_env(IMAGINE_ENV, 'FIRE_MODE', 'Y')
            self.update_fire_state()
            self.update_status("Firing...")
            sw, sh = 1920, 1080
            try:
                output = subprocess.check_output(['xdotool', 'getdisplaygeometry']).decode().strip()
                sw, sh = map(int, output.split())
            except:
                pass
            target_width = safe_int(read_merged_key('TARGET_WIDTH') or 640)
            target_height = safe_int(read_merged_key('TARGET_HEIGHT') or 500)
            center_x = (sw - target_width) // 2
            center_y = (sh - target_height) // 2
            offset_x = safe_int(read_merged_key('FIRE_STACK_X_OFFSET') or 0)
            offset_y = safe_int(read_merged_key('FIRE_STACK_Y_OFFSET') or 0)
            stack_x = center_x + offset_x
            stack_y = center_y + offset_y
            for wid in self.current_wids:
                xdo_resize_move(wid, target_width, target_height, stack_x, stack_y)
            self.daemon_thread = threading.Thread(target=self.daemon_thread_func, daemon=True)
            self.daemon_thread.start()
        else:
            self.firing = False
            update_env(IMAGINE_ENV, 'FIRE_MODE', 'N')
            self.update_fire_state()
            self.update_status("Stopped")

    def daemon_thread_func(self):
        total_shots = 0
        fire_count = self.fire_spin.get_value_as_int()
        round_delay = safe_float(read_merged_key('ROUND_DELAY') or 10)
        inter_target_delay = safe_float(read_merged_key('INTER_TARGET_DELAY') or 0.5)
        shot_delay = safe_float(read_merged_key('SHOT_DELAY') or 0.5)

        def update_status(text):
            GLib.idle_add(lambda: self.update_status(text) or False)
        update_status("Firing... 0 shots")

        for round_num in range(1, fire_count + 1):
            if round_num > 1:
                time.sleep(round_delay)
            if not self.current_wids or not self.firing:
                break

            for idx, wid in enumerate(self.current_wids, start=1):
                if not self.firing:
                    break

                cycle_url = self.cycle_urls[(idx - 1) % len(self.cycle_urls)]
                prompt = self.get_active_prompt_for_url(cycle_url)

                if prompt is None:
                    log_debug(CAT_DAEMON, f"Skipping {cycle_url} - no prompt available")
                    continue

                subprocess.run(['xdotool', 'click', '--repeat', '3', '4'])
                subprocess.run(['xdotool', 'click', '--clearmodifiers', '--window', str(wid), '1'])

                if self.send_prompt_check.get_active():
                    clipboard_set(prompt)
                    subprocess.run(['xdotool', 'key', '--window', str(wid), '--clearmodifiers', 'ctrl+a', 'ctrl+v', 'Return'])
                else:
                    subprocess.run(['xdotool', 'key', '--window', str(wid), '--clearmodifiers', 'Return'])

                time.sleep(shot_delay)
                total_shots += 1
                time.sleep(inter_target_delay)
                update_status(f"Firing round {round_num}/{fire_count} — {total_shots} shots")

                path = self.gxi_paths.get(cycle_url)
                if path and os.path.exists(path):
                    try:
                        with open(path, 'a', encoding='utf-8') as f:
                            f.write(f"{prompt}\n" if self.send_prompt_check.get_active() else "\n")
                    except:
                        pass

        update_status(f"Done — {total_shots} shots")
        self.firing = False
        update_env(IMAGINE_ENV, 'FIRE_MODE', 'N')
        GLib.idle_add(self.update_fire_state)
        GLib.timeout_add(5000, lambda: self.update_status("Ready") or False)

    def on_quit(self, widget):
        self.save_current_gxi()

        update_env(USER_ENV, 'FIRE_COUNT', str(self.fire_spin.get_value_as_int()))
        update_env(USER_ENV, 'STAGE_COUNT', str(self.stage_spin.get_value_as_int()))

        buffer = self.live_prompt_view.get_buffer()
        start, end = buffer.get_bounds()
        prompt_text = buffer.get_text(start, end, False)
        update_env(USER_ENV, 'DEFAULT_PROMPT', prompt_text)

        if self.env_window.get_visible():
            self.save_env_panel()

        self.save_all_geoms()
        update_env(USER_ENV, 'DEFAULT_CURRENT_URL', self.current_url or '')

        try:
            with open(self.target_list_file, 'w', encoding='utf-8') as f:
                for url in sorted(self.active_urls):
                    f.write(url + '\n')
        except:
            pass

        prune_user_env()
        prune_imagine_env()
        self.gentle_target_op('kill')
        Gtk.main_quit()

    def load_all_gxi(self):
        log_debug(CAT_FILE, "LOAD_ALL_GXI: Starting carousel rebuild")
        log_debug(CAT_FILE, f"LOAD_ALL_GXI: Scanning directory: {self.current_dir}")
        try:
            files = os.listdir(self.current_dir)
            log_debug(CAT_FILE, f"LOAD_ALL_GXI: Raw os.listdir ({len(files)} files): {files}")
        except Exception as e:
            log_debug(CAT_FILE, f"LOAD_ALL_GXI: os.listdir FAILED: {e}")
            files = []
        self.url_list = []
        self.gxi_paths = {}
        self.active_checks = {}
        self.row_widgets = {}
        for file in files:
            log_debug(CAT_FILE, f"LOAD_ALL_GXI: Processing file: {file}")
            if file.lower().endswith('.gxi'):
                log_debug(CAT_FILE, f"LOAD_ALL_GXI: Valid .gxi file found")
                path = os.path.join(self.current_dir, file)
                decoded_url = urllib.parse.unquote(file[:-4])
                log_debug(CAT_FILE, f"LOAD_ALL_GXI: Decoded URL: '{decoded_url}' from filename {file}")
                self.url_list.append(decoded_url)
                self.gxi_paths[decoded_url] = path
                log_debug(CAT_FILE, f"LOAD_ALL_GXI: Added to list and paths")
            else:
                log_debug(CAT_FILE, f"LOAD_ALL_GXI: Skipped (not .gxi): {file}")
        self.url_list.sort(key=str.lower)
        log_debug(CAT_FILE, f"LOAD_ALL_GXI: Final sorted url_list ({len(self.url_list)} items): {self.url_list}")
        for child in self.carousel_box.get_children()[1:]:
            self.carousel_box.remove(child)
        for url in self.url_list:
            log_debug(CAT_FILE, f"LOAD_ALL_GXI: Creating row for URL: '{url}'")
            eventbox = Gtk.EventBox()
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row_box.set_margin_start(10)
            row_box.set_margin_end(10)
            row_box.set_margin_top(6)
            row_box.set_margin_bottom(6)

            thumb_btn = Gtk.Button()
            thumb_btn.set_size_request(120, 120)
            thumb_btn.set_relief(Gtk.ReliefStyle.NORMAL)
            thumb_path = os.path.join(self.current_dir, urllib.parse.quote(url, safe='') + '.png')
            log_debug(CAT_FILE, f"LOAD_ALL_GXI: Thumbnail path: {thumb_path} exists={os.path.exists(thumb_path)}")
            if os.path.exists(thumb_path):
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(thumb_path, 120, 120, True)
                    img = Gtk.Image.new_from_pixbuf(pixbuf)
                    thumb_btn.add(img)
                    log_debug(CAT_FILE, f"LOAD_ALL_GXI: Loaded real thumbnail for '{url}'")
                except Exception as e:
                    log_debug(CAT_FILE, f"LOAD_ALL_GXI: Thumbnail load failed - empty button with dashed border for '{url}': {e}")
                    thumb_btn.get_style_context().add_class("missing-thumb")
            else:
                thumb_btn.get_style_context().add_class("missing-thumb")
                log_debug(CAT_FILE, f"LOAD_ALL_GXI: No thumbnail - empty button with dashed border for '{url}'")

            row_box.pack_start(thumb_btn, False, False, 0)

            url_label = Gtk.Label(label=url)
            url_label.set_xalign(0)
            url_label.set_ellipsize(Pango.EllipsizeMode.END)
            url_label.set_hexpand(True)
            row_box.pack_start(url_label, True, True, 0)

            check = Gtk.CheckButton(label="Active")
            check.set_active(url in self.active_urls)
            check.connect("toggled", self.on_active_toggled, url)
            self.active_checks[url] = check
            row_box.pack_start(check, False, False, 0)

            archive_btn = Gtk.Button(label="Restore" if self.is_archive else "Archive")
            archive_btn.connect("clicked", self.archive_gxi, url)
            row_box.pack_start(archive_btn, False, False, 0)

            eventbox.add(row_box)
            eventbox.connect("button-press-event", lambda w, e, u=url: self.on_carousel_row_clicked(w, u))
            if url == self.current_url:
                log_debug(CAT_FILE, f"LOAD_ALL_GXI: Marking as selected (matches current_url)")
                eventbox.get_style_context().add_class("selected")
            if check.get_active():
                eventbox.get_style_context().add_class("active-row")
            self.row_widgets[url] = eventbox
            self.carousel_box.pack_start(eventbox, False, False, 0)
            log_debug(CAT_FILE, f"LOAD_ALL_GXI: Row fully added and shown for '{url}'")

        self.carousel_box.show_all()
        log_debug(CAT_FILE, "LOAD_ALL_GXI: Rebuild complete - every row has a 120x120 button (real image or empty dashed border)")
        self.load_current_gxi()

    def grid_windows(self, expected_num):
        patterns = [p.strip().strip('"').strip("'").lower() for p in (read_merged_key('TARGET_PATTERNS') or '').split(',') if p.strip()]
        max_tries = 30
        last_total_windows = -1
        stagnant_limit = 3
        stagnant_count = 0
        last_matched = []
        grid_start_delay = safe_float(read_merged_key('GRID_START_DELAY') or 5)
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
            if len(matched) >= expected_num:
                self.current_wids = matched
                self._grid_ids(matched)
                self.gentle_target_op('activate', capture=True)
                self.update_status("Ready")
                self.update_fire_state()
                GLib.idle_add(lambda: self.parent_app.present())
                return False
            if stagnant_count >= stagnant_limit:
                if last_matched:
                    self.current_wids = sorted(last_matched, key=int)
                    self._grid_ids(self.current_wids)
                    self.gentle_target_op('activate', capture=True)
                self.update_status("Ready")
                self.update_fire_state()
                GLib.idle_add(lambda: self.parent_app.present())
                return False
            time.sleep(grid_start_delay)
        if last_matched:
            self.current_wids = sorted(last_matched, key=int)
            self._grid_ids(self.current_wids)
            self.gentle_target_op('activate', capture=True)
        self.update_status("Ready")
        self.update_fire_state()
        GLib.idle_add(lambda: self.parent_app.present())
        return False

    def _grid_ids(self, ids):
        result = subprocess.run(['xdotool', 'getdisplaygeometry'], capture_output=True, text=True)
        sw, sh = 1920, 1080
        try:
            sw, sh = map(int, result.stdout.strip().split())
        except:
            pass
        target_width = safe_int(read_merged_key('TARGET_WIDTH') or 640)
        target_height = safe_int(read_merged_key('TARGET_HEIGHT') or 500)
        target_overlap = safe_int(read_merged_key('MAX_OVERLAP_PERCENT') or 40)
        margin = 20
        available_width = sw - 2 * margin
        available_height = sh - 2 * margin
        n = len(ids)
        if n == 0:
            self.update_fire_state()
            return
        ids = sorted(ids, key=int)
        effective_step = max(1, int(target_width * (100 - target_overlap) / 100))
        max_cols_by_width = 1 + (available_width - target_width) // effective_step if available_width >= target_width else 1
        desired_rows = 2
        desired_cols = (n + desired_rows - 1) // desired_rows
        cols = desired_cols if desired_cols <= max_cols_by_width else max_cols_by_width
        rows = desired_rows if desired_cols <= max_cols_by_width else (n + cols - 1) // cols
        step_x = 0
        if cols == 1:
            step_x = 0
        else:
            min_total_width = target_width + (cols - 1) * effective_step
            if min_total_width <= available_width:
                step_x = (available_width - target_width) // (cols - 1)
                if step_x < effective_step:
                    step_x = effective_step
            else:
                step_x = max(1, (available_width - target_width) // (cols - 1))
        step_y = 0
        if rows > 1:
            vertical_effective_step = max(1, int(target_height * (100 - target_overlap) / 100))
            min_total_height = target_height + (rows - 1) * vertical_effective_step
            if min_total_height <= available_height:
                step_y = (available_height - target_height) // (rows - 1)
                if step_y < vertical_effective_step:
                    step_y = vertical_effective_step
            else:
                step_y = max(1, (available_height - target_height) // (rows - 1))
        x_start = margin + max(0, (available_width - (target_width + (cols - 1) * step_x)) // 2)
        y_start = margin + max(0, (available_height - (target_height + (rows - 1) * step_y)) // 2)
        relative_x = percent_to_pixels(read_merged_key('PROMPT_X_FROM_LEFT') or '50%', target_width)
        relative_y = target_height - percent_to_pixels(read_merged_key('PROMPT_Y_FROM_BOTTOM') or '10%', target_height)
        self.capture_click_positions = []
        try:
            for idx, wid in enumerate(ids):
                r = idx // cols
                c = idx % cols
                x = int(x_start + c * step_x)
                y = int(y_start + r * step_y)
                click_x = x + relative_x
                click_y = y + relative_y
                self.capture_click_positions.append((click_x, click_y))
                xdo_resize_move(wid, target_width, target_height, x, y)
        except:
            pass

    def gentle_target_op(self, op_type, sync=True, delay=None, capture=True):
        delay = safe_float(read_merged_key('TARGET_OP_DELAY') or 0.25)
        if op_type == 'kill':
            for wid_str in self.current_wids[:]:
                try:
                    wid_int = int(wid_str)
                    wid_hex = f"0x{wid_int:08x}"
                    subprocess.run(['wmctrl', '-i', '-c', wid_hex], check=False)
                    time.sleep(delay)
                except:
                    pass
            self.current_wids = []
            self.update_fire_state()
            return
        if op_type == 'activate' and self.current_wids:
            capture_mode = safe_int(read_merged_key('CAPTURE_MODE') or '0')
            if capture_mode == 0:
                capture = False
            gxi_dir = self.current_dir
            capture_tool = read_merged_key('CAPTURE_TOOL') or 'maim'
            target_width = safe_int(read_merged_key('TARGET_WIDTH') or 640)
            target_height = safe_int(read_merged_key('TARGET_HEIGHT') or 500)
            capture_x_from_left_str = read_merged_key('CAPTURE_X_FROM_LEFT') or '50%'
            capture_y_from_bottom_str = read_merged_key('CAPTURE_Y_FROM_BOTTOM') or '10%'
            capture_ratio_str = read_merged_key('CAPTURE_RATIO') or '50%'
            capture_left_edge = percent_to_pixels(capture_x_from_left_str, target_width)
            pixels_from_bottom = percent_to_pixels(capture_y_from_bottom_str, target_height)
            ratio = max(0.1, min(1.0, safe_float(capture_ratio_str.rstrip('%')) / 100 if '%' in capture_ratio_str else safe_float(capture_ratio_str)))
            side = int(target_width * ratio)
            rect_y = target_height - pixels_from_bottom - side
            rect_geometry = f"{side}x{side}+{capture_left_edge}+{rect_y}"
            harvested_urls = set()
            for idx, wid_str in enumerate(self.current_wids, start=1):
                subprocess.run(['xdotool', 'windowactivate', '--sync' if sync else '', wid_str])
                time.sleep(delay)
                if capture:
                    cycle_url = self.cycle_urls[(idx - 1) % len(self.cycle_urls)]
                    if cycle_url not in harvested_urls:
                        safe_name = urllib.parse.quote(cycle_url, safe='')
                        capture_path = os.path.join(gxi_dir, f"{safe_name}.png")
                        if idx - 1 < len(self.capture_click_positions):
                            click_x, click_y = self.capture_click_positions[idx - 1]
                            subprocess.run(['xdotool', 'mousemove', str(click_x), str(click_y),
                                            'click', '1',
                                            'key', '--window', wid_str, '--clearmodifiers', 'ctrl+a', 'ctrl+c'])
                            time.sleep(delay)
                            prompt = get_clipboard()
                            path = self.gxi_paths.get(cycle_url)
                            if prompt and path:
                                header_lines, prompts, histories = parse_gxi(path)
                                prompts['U'].insert(0, prompt)
                                apply_overflow(prompts)
                                write_gxi(path, header_lines, prompts, histories)
                            subprocess.run(['xdotool', 'key', '--window', wid_str, '--clearmodifiers', 'Delete'])
                            if capture_tool == 'maim':
                                cmd = ['maim', '--hidecursor', '-g', rect_geometry, '-i', wid_str, capture_path]
                            elif capture_tool == 'import':
                                cmd = ['import', '-window', wid_str, '-crop', f"{side}x{side}+{capture_left_edge}+{rect_y}", capture_path]
                            else:
                                cmd = ['maim', '--hidecursor', '-g', rect_geometry, '-i', wid_str, capture_path]
                            time.sleep(delay)
                            result = subprocess.run(cmd, capture_output=True)
                            if result.returncode == 0:
                                log_debug(CAT_DAEMON, f"CAPTURED thumbnail to {capture_path}")
                        harvested_urls.add(cycle_url)
                time.sleep(delay)

class UnifiedApp(Gtk.Window):
    def __init__(self):
        super().__init__(title="Blitz Talker — Ready")
        dedupe_and_prune_startup()
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(self.box)

        width_str = read_merged_key('PANEL_DEFAULT_WIDTH')
        height_str = read_merged_key('PANEL_DEFAULT_HEIGHT')
        self.saved_width = safe_int(width_str, -1)
        self.saved_height = safe_int(height_str, -1)
        self.saved_x_off = safe_int(read_merged_key('PANEL_DEFAULT_X_OFFSET'))
        self.saved_y_off = safe_int(read_merged_key('PANEL_DEFAULT_Y_OFFSET'))
        if self.saved_width > 0 and self.saved_height > 0:
            self.set_default_size(self.saved_width, self.saved_height)

        parser = argparse.ArgumentParser(description="Blitz Talker Control")
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--url", type=str, help="Load a single URL")
        group.add_argument("--gxi", type=str, help="Load a .gxi file (extracts URL and loads)")
        group.add_argument("--url-file", type=str, help="Load URLs from a file")
        args = parser.parse_args()

        startup_source = None
        if args.gxi:
            startup_source = f"--gxi={args.gxi}"
        elif args.url:
            startup_source = f"--url={args.url}"
        elif args.url_file:
            startup_source = f"--url-file={args.url_file}"

        self.gun = BlitzControl(self, startup_source=startup_source)
        self.box.pack_start(self.gun, True, True, 0)

        self.connect("realize", self.on_realize)
        self.connect("configure-event", self.on_configure)

    def on_realize(self, widget):
        self.gun.restore_all_geoms()
        try:
            monitor = Gdk.Display.get_default().get_primary_monitor()
            geom = monitor.get_geometry()
            saved_x = safe_int(read_merged_key('PANEL_DEFAULT_X_OFFSET'))
            saved_y_off = safe_int(read_merged_key('PANEL_DEFAULT_Y_OFFSET'))
            h = self.get_size().height
            calculated_y = geom.height - h - saved_y_off
            log_debug(CAT_GEOM, f"MAIN PANEL REALIZE REQUESTED: x={saved_x}, y_off={saved_y_off}, calculated_y={calculated_y}")
            self.move(saved_x, calculated_y)
            actual_w, actual_h = self.get_size()
            actual_x, actual_y = self.get_position()
            log_debug(CAT_GEOM, f"MAIN PANEL ACTUAL AFTER REALIZE: size={actual_w}x{actual_h}, pos=({actual_x},{actual_y})")
        except Exception as e:
            log_debug(CAT_GUI, f"Main window realize geometry error: {e}")

    def on_configure(self, widget, event):
        self.gun.save_all_geoms()
        return False

if __name__ == '__main__':
    app = UnifiedApp()
    app.show_all()
    Gtk.main()
