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
CAT_XDO = 1 << 7

GLOBAL_DEBUG_MASK = 0xFF

def log_debug(category, content):
    if GLOBAL_DEBUG_MASK & category:
        print(f"[DEBUG {category_name(category)}] {content}")

def category_name(bit):
    names = {CAT_FILE: "FILE", CAT_WINDOW: "WINDOW", CAT_INPUT: "INPUT",
             CAT_DAEMON: "DAEMON", CAT_GUI: "GUI", CAT_INIT: "INIT",
             CAT_GEOM: "GEOM", CAT_XDO: "XDO"}
    return names.get(bit, "UNKNOWN")

log_debug(CAT_INIT, "=== SCRIPT START - FULL BLAST DEBUG ENABLED ===")

def safe_int(val, default=0):
    try: return int(float(val))
    except: return default

def safe_float(val, default=0.0):
    try: return float(val)
    except: return default

def percent_to_pixels(percent_str, dimension):
    if '%' in str(percent_str):
        return int(dimension * int(str(percent_str).rstrip('%')) / 100)
    return int(percent_str)

def load_env_multiline(path):
    env = {}
    full_path = os.path.join(SCRIPT_DIR, path)
    log_debug(CAT_FILE, f"load_env_multiline: OPENING {full_path}")
    if not os.path.exists(full_path):
        log_debug(CAT_FILE, f"load_env_multiline: FILE NOT FOUND {full_path}")
        return env
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        log_debug(CAT_FILE, f"load_env_multiline: SUCCESS - {len(lines)} lines from {full_path}")
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
            value_part = m.group(2)

            stripped_value = value_part.strip()
            if stripped_value and stripped_value[0] in ('#', '~') and len(stripped_value.split()[0]) == 1:
                value = stripped_value[0]
                log_debug(CAT_FILE, f"load_env_multiline: LITERAL SPECIAL CHAR DETECTED {key} = '{value}' from {full_path}")
            else:
                value = value_part.split('#', 1)[0]

            while i + 1 < len(lines):
                next_line = lines[i + 1].rstrip('\n')
                next_stripped = next_line.strip()
                if next_stripped.startswith('#') or re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=', next_line):
                    break
                value += '\n' + next_line
                i += 1
                if not next_line.rstrip().endswith('\\'):
                    break

            value = value.replace('\\\n', '')
            value = re.sub(r'\s*\n\s*', ' ', value)
            value = value.strip()
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1].strip()

            env[key] = value
            log_debug(CAT_FILE, f"load_env_multiline: PARSED {key} = '{value}' from {full_path}")
            i += 1
    except Exception as e:
        log_debug(CAT_FILE, f"load_env_multiline: ERROR reading {full_path}: {e}")
    return env

def get_merged_multiline(key):
    if key in user_cache:
        log_debug(CAT_FILE, f"get_merged_multiline: {key} = '{user_cache[key]}' ← user_cache WINNER")
        return user_cache[key]
    for file in (IMAGINE_ENV, SYSTEM_ENV):
        env = load_env_multiline(file)
        if key in env:
            log_debug(CAT_FILE, f"get_merged_multiline: {key} = '{env[key]}' ← from {file}")
            return env[key]
    log_debug(CAT_FILE, f"get_merged_multiline: {key} NOT FOUND")
    return ''

def read_key(file, key):
    full_path = os.path.join(SCRIPT_DIR, file)
    log_debug(CAT_FILE, f"read_key: checking {full_path} for {key}")
    if not os.path.exists(full_path):
        log_debug(CAT_FILE, f"read_key: file missing {full_path}")
        return None
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip().startswith(f'{key}='):
                    v = line.split('=', 1)[1]
                    v = v.split('#', 1)[0].strip().strip('"\'')
                    log_debug(CAT_FILE, f"read_key: FOUND {key} = '{v}' in {file}")
                    return v
    except Exception as e:
        log_debug(CAT_FILE, f"read_key: ERROR on {file}: {e}")
    log_debug(CAT_FILE, f"read_key: {key} NOT FOUND in {file}")
    return None

def read_merged_key(key):
    if key in user_cache:
        log_debug(CAT_FILE, f"read_merged_key: {key} = '{user_cache[key]}' ← user_cache WINNER")
        return user_cache[key]
    for file in (IMAGINE_ENV, SYSTEM_ENV):
        val = read_key(file, key)
        if val is not None:
            log_debug(CAT_FILE, f"read_merged_key: {key} = '{val}' ← from {file}")
            return val
    log_debug(CAT_FILE, f"read_merged_key: {key} NOT FOUND")
    return None

def update_env(file, key, value):
    full_path = os.path.join(SCRIPT_DIR, file)
    log_debug(CAT_FILE, f"update_env: WRITING {key} = '{value}' to {full_path}")
    lines = []
    if os.path.exists(full_path):
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except:
            lines = []
    lines = [line for line in lines if not line.strip().startswith(f'{key}=')]
    lines.append(f'{key}="{value}"\n')
    try:
        with open(full_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
            f.flush()
            os.fsync(f.fileno())
        log_debug(CAT_FILE, f"update_env: SUCCESS wrote {key} to {full_path}")
    except Exception as e:
        log_debug(CAT_FILE, f"update_env: ERROR writing to {full_path}: {e}")

def prune_env(env_file, keep_prefixes=None, runtime_keys=None):
    log_debug(CAT_FILE, f"prune_env: STARTING prune on {env_file}")
    system_values = load_env_multiline(SYSTEM_ENV)
    full_path = os.path.join(SCRIPT_DIR, env_file)
    if not os.path.exists(full_path):
        log_debug(CAT_FILE, f"prune_env: file missing - skip {full_path}")
        return
    lines = []
    kept = 0
    pruned = 0
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            old_lines = f.readlines()
        for raw_line in old_lines:
            line = raw_line.rstrip('\n')
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                lines.append(raw_line)
                kept += 1
                continue
            m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$', line)
            if not m:
                lines.append(raw_line)
                kept += 1
                continue
            key = m.group(1)
            if keep_prefixes and any(key.startswith(p) for p in keep_prefixes):
                lines.append(raw_line)
                kept += 1
                continue
            if runtime_keys and key in runtime_keys:
                lines.append(raw_line)
                kept += 1
                continue
            rest = m.group(2).lstrip()
            value_part = rest.split('#', 1)[0].strip()
            parsed_value = value_part.strip('"\'')
            if key not in system_values:
                lines.append(raw_line)
                kept += 1
                continue
            sys_val = system_values[key]
            if parsed_value == sys_val:
                log_debug(CAT_FILE, f"prune_env: PRUNING redundant {key} = '{parsed_value}' from {env_file}")
                pruned += 1
                continue
            lines.append(raw_line)
            kept += 1
        with open(full_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        log_debug(CAT_FILE, f"prune_env: COMPLETE on {env_file} — kept {kept}, pruned {pruned}")
    except Exception as e:
        log_debug(CAT_FILE, f"prune_env: ERROR on {env_file}: {e}")

user_cache = load_env_multiline(USER_ENV)
log_debug(CAT_FILE, f"user_cache loaded with {len(user_cache)} keys")

def clean_workbench_cruft():
    wb = os.path.join(SCRIPT_DIR, '.imagine_workbench')
    if not os.path.exists(wb):
        return
    deleted = 0
    for f in os.listdir(wb):
        path = os.path.join(wb, f)
        if f.endswith('.txt') or '%3Ahttps' in f or '%253A' in f:
            try:
                os.remove(path)
                log_debug(CAT_FILE, f"clean_workbench_cruft: DELETED garbage {f}")
                deleted += 1
            except Exception as e:
                log_debug(CAT_FILE, f"clean_workbench_cruft: failed to delete {f}: {e}")
    if deleted:
        log_debug(CAT_FILE, f"clean_workbench_cruft: removed {deleted} garbage files")

clean_workbench_cruft()

def dedupe_and_prune_startup():
    log_debug(CAT_INIT, "=== STARTUP PRUNE BEGIN ===")
    prune_env(USER_ENV, keep_prefixes=['ENV_EDITOR_', 'GXI_EDITOR_', 'PANEL_DEFAULT_'])
    prune_env(IMAGINE_ENV, runtime_keys=RUNTIME_KEYS)
    log_debug(CAT_INIT, "=== STARTUP PRUNE COMPLETE ===")

def load_flags(key):
    flags_str = get_merged_multiline(key)
    return shlex.split(flags_str) if flags_str else []

def get_clipboard():
    try:
        return subprocess.check_output(['xclip', '-selection', 'clipboard', '-o'], timeout=2).decode('utf-8').strip()
    except:
        return ''

def clipboard_set(text):
    try:
        subprocess.run(['xclip', '-selection', 'clipboard'], input=text.encode('utf-8'), check=True)
    except:
        pass

def xdo_resize_move(wid, width, height, x, y):
    cmd = ['xdotool', 'windowsize', '--sync', wid, str(width), str(height),
           'windowmove', wid, str(x), str(y)]
    subprocess.run(cmd, capture_output=True, text=True)

def get_urls_from_input(input_str):
    input_str = input_str.strip()
    urls = []
    if os.path.isfile(input_str):
        try:
            with open(input_str, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.split('#', 1)[0].strip().strip('"\'')
                    if line and '://' in line:
                        urls.append(line)
        except:
            pass
    else:
        parts = re.split(r'[,\s]+', input_str)
        for p in parts:
            p = p.strip().strip('"\'')
            if p and '://' in p:
                urls.append(p)
    return urls

def parse_gxi(path):
    if not os.path.exists(path):
        return [], {'U':[], '1':[], '2':[], '3':[]}, {'U':[], '1':[], '2':[], '3':[]}, ""
    header_lines = []
    prompts = {'U':[], '1':[], '2':[], '3':[]}
    histories = {'U':[], '1':[], '2':[], '3':[]}
    comment = ""
    current_stage = None
    in_history = False
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('TARGET_URL=') or stripped.startswith('BORN_ON=') or stripped.startswith('ACCOUNT='):
                header_lines.append(line)
            elif stripped.startswith('TARGET_DESC='):
                header_lines.append(line)
                comment = stripped[12:].strip()
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
    except:
        pass
    return header_lines, prompts, histories, comment

def write_gxi(path, header_lines, prompts, histories, comment):
    new_header = [line for line in header_lines if not line.strip().startswith('TARGET_DESC=')]
    if comment:
        comment_lines = comment.split('\n')
        new_header.append(f"TARGET_DESC={comment_lines[0]}\n")
        for cl in comment_lines[1:]:
            new_header.append(cl + '\n')
    new_header.append("\n")
    for stage in ['U', '1', '2', '3']:
        new_header.append(f"STAGE_{stage}\n")
        for p in prompts.get(stage, []):
            new_header.append(p + '\n')
        history_marker = '.history_U' if stage == 'U' else f".history_{stage}"
        new_header.append(f"{history_marker}\n")
        for h in histories.get(stage, []):
            new_header.append(h + '\n')
        new_header.append("\n")
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.writelines(new_header)
    except:
        pass

def apply_overflow(prompts):
    order = ['U', '1', '2', '3']
    for i in range(len(order) - 1):
        stage = order[i]
        while len(prompts[stage]) > 5:
            overflow = prompts[stage].pop()
            prompts[order[i+1]].insert(0, overflow)

def dedupe_prompts(prompts):
    seen = {}
    new_prompts = {'U':[], '1':[], '2':[], '3':[]}
    order = ['1', '2', '3', 'U']
    for stage in order:
        for p in prompts.get(stage, []):
            clean = p.lstrip('@')
            if clean in seen:
                continue
            seen[clean] = True
            if p.startswith('@'):
                new_prompts[stage].append(p)
            else:
                new_prompts[stage].append(clean)
    for stage in order:
        while len(new_prompts[stage]) > 5:
            overflow = new_prompts[stage].pop()
            if stage == 'U':
                continue
            next_idx = order.index(stage) + 1
            next_stage = order[next_idx]
            new_prompts[next_stage].insert(0, overflow)
    return new_prompts

class BlitzControl(Gtk.Box):
    def __init__(self, parent_app, startup_source=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.parent_app = parent_app
        self.set_border_width(10)
        self.busy = False
        self.firing = False
        self.daemon_thread = None
        self.current_url = None
        self.batch_urls = set()
        self.gun_active_urls = set()
        self.current_wids = []
        self.cycle_urls = []
        self.target_dir = os.path.join(SCRIPT_DIR, read_merged_key('TARGET_DIR') or '.imagine_targets')
        os.makedirs(self.target_dir, exist_ok=True)
        self.workbench_dir = os.path.join(SCRIPT_DIR, '.imagine_workbench')
        os.makedirs(self.workbench_dir, exist_ok=True)
        self.archive_dir = os.path.join(SCRIPT_DIR, read_merged_key('ARCHIVE_DIR') or '.imagine_archives')
        os.makedirs(self.archive_dir, exist_ok=True)
        self.current_dir = self.target_dir
        self.is_archive = False
        self.target_list_file = os.path.join(SCRIPT_DIR, read_merged_key('TARGET_LIST') or 'live_windows.txt')
        self.gun_list_file = os.path.join(SCRIPT_DIR, read_merged_key('GUN_LIST') or 'gun_active_windows.txt')
        if os.path.exists(self.target_list_file):
            try:
                with open(self.target_list_file, 'r', encoding='utf-8') as f:
                    self.batch_urls = {line.strip() for line in f if line.strip()}
            except: pass
        if os.path.exists(self.gun_list_file):
            try:
                with open(self.gun_list_file, 'r', encoding='utf-8') as f:
                    self.gun_active_urls = {line.strip() for line in f if line.strip()}
            except: pass
        self.all_urls = []
        self.gxi_paths = {}
        self.wb_gxi_paths = {}
        self.gallery_checks = {}
        self.carousel_gun_checks = {}
        self.row_widgets = {}
        self.current_gxi_path = None
        self.current_histories = {}
        self.updating_merged = False
        self.system_override_enabled = False
        self.debug_checks = []
        self.filter_mode = 0
        self.mass_updating = False

        self.PROMPT_ERASE_CHAR  = read_merged_key('PROMPT_ERASE_CHAR')  or '~'
        self.PROMPT_SILENT_CHAR = read_merged_key('PROMPT_SILENT_CHAR') or '#'
        self.PROMPT_FIRE_CHAIN  = (read_merged_key('PROMPT_FIRE_CHAIN') or 'true').lower() == 'true'

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
        live_prompt_scrolled.set_hexpand(False)
        self.live_prompt_view = Gtk.TextView()
        self.live_prompt_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.live_prompt_view.set_editable(True)
        live_prompt_scrolled.add(self.live_prompt_view)
        live_prompt_row.pack_start(live_prompt_scrolled, True, True, 0)

        comment_container, self.live_comment_view = self.create_comment_box(min_height=72)
        live_comment_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
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
        live_comment_row.pack_start(comment_container, True, True, 0)
        main_box.pack_start(live_comment_row, False, False, 0)

        self.send_prompt_check = Gtk.CheckButton(label="Send (uncheck to regen)")
        send_val = read_merged_key('SEND_PROMPT_ON_FIRE') or '1'
        self.send_prompt_check.set_active(send_val.lower() in ('1', 'y', 'true', 'yes', 'on'))
        self.send_prompt_check.connect("toggled", self.on_send_prompt_toggled)

        self.harvest_prompt_check = Gtk.CheckButton(label="Harvest")
        harvest_val = read_merged_key('HARVEST_PROMPT_ON_STAGE') or '1'
        self.harvest_prompt_check.set_active(harvest_val.lower() in ('1', 'y', 'true', 'yes', 'on'))
        self.harvest_prompt_check.connect("toggled", self.on_harvest_prompt_toggled)

        self.acct_entry = Gtk.Entry()
        self.acct_entry.set_hexpand(True)
        self.acct_entry.set_width_chars(28)

        prompt_toggle_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        prompt_toggle_row.pack_start(Gtk.Label(label="Prompt"), False, False, 0)
        prompt_toggle_row.pack_start(self.send_prompt_check, False, False, 0)
        prompt_toggle_row.pack_start(self.harvest_prompt_check, False, False, 0)
        main_box.pack_start(prompt_toggle_row, False, False, 0)

        acct_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        acct_row.pack_start(Gtk.Label(label="Acct:"), False, False, 0)
        acct_row.pack_start(self.acct_entry, True, True, 0)

        push_btn = Gtk.Button(label="Push to active")
        push_btn.connect("clicked", self.on_push_account)
        acct_row.pack_start(push_btn, False, False, 0)

        main_box.pack_start(acct_row, False, False, 0)

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
        label.url-label { color: #88ccff; }
        """)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.gallery_window = Gtk.Window(title="Gallery")
        self.gallery_window.set_resizable(True)
        self.gallery_window.connect("delete-event", lambda w, e: self.hide_and_save_editor(w, 'GALLERY') or True)
        self.gallery_window.connect("configure-event", lambda w, e: self.save_editor_geometry(w, 'GALLERY_EDITOR') or False)
        gallery_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        gallery_top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        gallery_top.set_margin_top(10)
        gallery_top.set_margin_bottom(10)
        gallery_top.set_halign(Gtk.Align.CENTER)
        reload_g_btn = Gtk.Button(label="Reload")
        reload_g_btn.connect("clicked", lambda w: self.load_all_gxi())
        gallery_top.pack_start(reload_g_btn, False, False, 0)
        new_g_btn = Gtk.Button(label="New Target")
        new_g_btn.connect("clicked", self.on_new_target)
        gallery_top.pack_start(new_g_btn, False, False, 0)
        select_g_btn = Gtk.Button(label="Select All")
        select_g_btn.connect("clicked", self.select_all_gallery)
        gallery_top.pack_start(select_g_btn, False, False, 0)
        deselect_g_btn = Gtk.Button(label="Deselect All")
        deselect_g_btn.connect("clicked", self.deselect_all_gallery)
        gallery_top.pack_start(deselect_g_btn, False, False, 0)
        invert_g_btn = Gtk.Button(label="Invert")
        invert_g_btn.connect("clicked", self.on_invert_selection)
        gallery_top.pack_start(invert_g_btn, False, False, 0)
        gallery_vbox.pack_start(gallery_top, False, False, 0)
        gallery_scrolled = Gtk.ScrolledWindow()
        gallery_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.gallery_flowbox = Gtk.FlowBox()
        self.gallery_flowbox.set_orientation(Gtk.Orientation.HORIZONTAL)
        self.gallery_flowbox.set_homogeneous(False)
        self.gallery_flowbox.set_max_children_per_line(6)
        self.gallery_flowbox.set_row_spacing(12)
        self.gallery_flowbox.set_column_spacing(12)
        self.gallery_flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        gallery_scrolled.add(self.gallery_flowbox)
        gallery_vbox.pack_start(gallery_scrolled, True, True, 0)
        self.gallery_window.add(gallery_vbox)

        self.gxi_window = Gtk.Window(title="GXI Manager")
        self.gxi_window.set_resizable(True)
        self.gxi_window.connect("delete-event", lambda w, e: self.hide_and_save_editor(w, 'GXI') or True)
        self.gxi_window.connect("configure-event", lambda w, e: self.save_editor_geometry(w, 'GXI_EDITOR') or False)
        gxi_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.gxi_paned = gxi_paned
        gxi_paned.set_position(500)
        self.gxi_window.add(gxi_paned)

        left_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        controls_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        controls_box.set_margin_top(10)
        controls_box.set_margin_bottom(10)
        controls_box.set_halign(Gtk.Align.CENTER)

        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        reload_btn = Gtk.Button(label="Reload")
        reload_btn.connect("clicked", lambda w: self.load_all_gxi())
        row1.pack_start(reload_btn, True, True, 0)

        new_btn = Gtk.Button(label="New Target")
        new_btn.connect("clicked", self.on_new_target)
        row1.pack_start(new_btn, True, True, 0)

        save_active_btn = Gtk.Button(label="Save Active")
        save_active_btn.connect("clicked", self.save_active_gun)
        row1.pack_start(save_active_btn, True, True, 0)
        controls_box.pack_start(row1, False, False, 0)

        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        select_all_btn = Gtk.Button(label="Select All Gun")
        select_all_btn.connect("clicked", self.select_all_carousel)
        row2.pack_start(select_all_btn, True, True, 0)

        deselect_all_btn = Gtk.Button(label="Deselect All Gun")
        deselect_all_btn.connect("clicked", self.deselect_all_carousel)
        row2.pack_start(deselect_all_btn, True, True, 0)

        clear_gun_btn = Gtk.Button(label="Clear Carousel")
        clear_gun_btn.connect("clicked", self.clear_all_gun)
        row2.pack_start(clear_gun_btn, True, True, 0)
        controls_box.pack_start(row2, False, False, 0)

        row3 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.filter_btn = Gtk.Button(label="Filter: All")
        self.filter_btn.connect("clicked", self.on_filter_clicked)
        row3.pack_start(self.filter_btn, True, True, 0)

        invert_btn = Gtk.Button(label="Invert")
        invert_btn.connect("clicked", self.on_invert_selection)
        row3.pack_start(invert_btn, True, True, 0)

        self.archive_toggle_btn = Gtk.Button(label="Show Archive")
        self.archive_toggle_btn.connect("clicked", self.toggle_archive)
        row3.pack_start(self.archive_toggle_btn, True, True, 0)
        controls_box.pack_start(row3, False, False, 0)

        left_pane.pack_start(controls_box, False, False, 0)

        self.carousel_scrolled = Gtk.ScrolledWindow()
        self.carousel_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.carousel_box = Gtk.FlowBox()
        self.carousel_box.set_orientation(Gtk.Orientation.HORIZONTAL)
        self.carousel_box.set_homogeneous(True)
        self.carousel_box.set_max_children_per_line(2)
        self.carousel_box.set_row_spacing(8)
        self.carousel_box.set_column_spacing(8)
        self.carousel_scrolled.add(self.carousel_box)
        left_pane.pack_start(self.carousel_scrolled, True, True, 0)

        gxi_paned.pack1(left_pane, resize=True, shrink=False)

        editor_scrolled = Gtk.ScrolledWindow()
        editor_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.gxi_editor_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.gxi_editor_box.set_border_width(10)
        editor_scrolled.add(self.gxi_editor_box)
        gxi_paned.pack2(editor_scrolled, resize=True, shrink=False)

        self.thumb_image = Gtk.Image()
        self.gxi_editor_box.pack_start(self.thumb_image, False, False, 0)

        self.editor_active_check = Gtk.CheckButton(label="Gun Act")
        self.editor_active_check.connect("toggled", self.on_editor_active_toggled)
        self.gxi_editor_box.pack_start(self.editor_active_check, False, False, 0)

        url_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        url_section.set_border_width(4)
        self.url_label = Gtk.Label(label="None")
        self.url_label.set_selectable(True)
        self.url_label.set_xalign(0)
        self.url_label.get_style_context().add_class("url-label")
        self.url_label.set_hexpand(True)
        self.url_label.set_line_wrap(True)
        self.url_label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.url_label.set_max_width_chars(70)
        url_section.pack_start(self.url_label, False, False, 0)

        info_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.born_label = Gtk.Label(label="Born On: Never")
        self.born_label.set_xalign(0)
        info_row.pack_start(self.born_label, False, False, 0)

        acct_row = self.create_account_row(editable=True)
        info_row.pack_start(acct_row, True, True, 0)

        save_gxi_btn = Gtk.Button(label="Save GXI")
        save_gxi_btn.connect("clicked", self.save_current_gxi)
        info_row.pack_start(save_gxi_btn, False, False, 0)

        url_section.pack_start(info_row, False, False, 0)
        self.gxi_editor_box.pack_start(url_section, False, False, 0)

        comment_container, self.comment_view = self.create_comment_box(min_height=120)
        self.gxi_editor_box.pack_start(comment_container, False, False, 0)

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
        self.int_keys = {"STAGE_COUNT", "FIRE_COUNT", "TARGET_WIDTH", "TARGET_HEIGHT", "CAPTURE_MODE",
                         "MAX_OVERLAP_PERCENT", "FIRE_STACK_X_OFFSET", "FIRE_STACK_Y_OFFSET"}
        self.float_keys = {"STAGE_DELAY", "GRID_START_DELAY", "ROUND_DELAY", "INTER_TARGET_DELAY",
                           "SHOT_DELAY", "TARGET_OP_DELAY"}
        self.bool_keys = {"AUTO_FIRE", "DEDUPE_CAPTURES", "HARVEST_PROMPT_ON_STAGE", "SEND_PROMPT_ON_FIRE"}
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

            if key == 'DEBUG_MASK':
                categories = ['xdo', 'geom', 'init', 'gui', 'daemon', 'input', 'window', 'file']
                cap_labels = ['XDO', 'GEOM', 'INIT', 'GUI', 'DAEMON', 'INPUT', 'WINDOW', 'FILE']

                system_val = self.system_env.get(key, '')
                system_widget = Gtk.TextView()
                system_widget.get_buffer().set_text(system_val)
                system_widget.set_editable(False)
                system_frame = Gtk.Frame()
                system_frame.add(system_widget)
                env_grid.attach(system_frame, 1, row, 1, 1)
                self.value_widgets[key]['system'] = system_widget

                imagine_val = self.imagine_env.get(key, '')
                imagine_widget = Gtk.TextView()
                imagine_widget.get_buffer().set_text(imagine_val)
                imagine_widget.set_editable(False)
                imagine_frame = Gtk.Frame()
                imagine_frame.add(imagine_widget)
                env_grid.attach(imagine_frame, 2, row, 1, 1)
                self.value_widgets[key]['imagine'] = imagine_widget

                user_grid = Gtk.Grid()
                user_grid.set_column_spacing(12)
                user_grid.set_row_spacing(8)
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

                hidden_user = Gtk.TextView()
                hidden_user.get_buffer().set_text(user_val)
                hidden_user.set_visible(False)
                self.value_widgets[key]['user'] = hidden_user

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
                    widget = self.create_value_widget(key, val, readonly)
                    frame = Gtk.Frame()
                    frame.get_style_context().add_class(f"{col_name}-column")
                    frame.set_border_width(4)
                    frame.add(widget)
                    env_grid.attach(frame, col_idx, row, 1, 1)
                    self.value_widgets[key][col_name] = widget

            row += 1

        override_check = Gtk.CheckButton(label="Enable editing .system_env values (auto saves overrides to .user_env on change)")
        override_check.connect("toggled", self.on_system_override_toggled)
        env_grid.attach(override_check, 0, row, 5, 1)
        row += 1
        save_btn = Gtk.Button(label="Save Changes")
        save_btn.connect("clicked", self.save_env_panel)
        env_grid.attach(save_btn, 0, row, 5, 1)

        self.env_window.hide()
        self.gxi_window.hide()
        self.gallery_window.hide()

        self.env_saved_width = safe_int(read_merged_key('ENV_EDITOR_WIDTH'))
        self.env_saved_height = safe_int(read_merged_key('ENV_EDITOR_HEIGHT'))
        self.env_saved_x_off = safe_int(read_merged_key('ENV_EDITOR_X_OFFSET'))
        self.env_saved_y_off = safe_int(read_merged_key('ENV_EDITOR_Y_OFFSET'))
        self.gxi_saved_width = safe_int(read_merged_key('GXI_EDITOR_WIDTH'))
        self.gxi_saved_height = safe_int(read_merged_key('GXI_EDITOR_HEIGHT'))
        self.gxi_saved_x_off = safe_int(read_merged_key('GXI_EDITOR_X_OFFSET'))
        self.gxi_saved_y_off = safe_int(read_merged_key('GXI_EDITOR_Y_OFFSET'))
        self.gallery_saved_width = safe_int(read_merged_key('GALLERY_EDITOR_WIDTH') or 1200)
        self.gallery_saved_height = safe_int(read_merged_key('GALLERY_EDITOR_HEIGHT') or 800)
        self.gallery_saved_x_off = safe_int(read_merged_key('GALLERY_EDITOR_X_OFFSET') or 100)
        self.gallery_saved_y_off = safe_int(read_merged_key('GALLERY_EDITOR_Y_OFFSET') or 100)
        self.gxi_paned_position = safe_int(read_merged_key('GXI_PANED_POSITION') or 500)

        self.restore_all_geoms()

        if startup_source:
            self.handle_startup_source(startup_source)

        GLib.idle_add(self.load_all_gxi)

        self.capture_click_positions = []

        self.update_status("Ready")

        default = get_merged_multiline('DEFAULT_PROMPT').strip()
        buffer = self.live_prompt_view.get_buffer()
        buffer.set_text(default)

    def ensure_wb_copies(self):
        for url in list(self.batch_urls):
            safe = urllib.parse.quote(url, safe='')
            wb_path = os.path.join(self.workbench_dir, safe + '.gxi')
            target_path = os.path.join(self.target_dir, safe + '.gxi')
            wb_png = os.path.join(self.workbench_dir, safe + '.png')
            target_png = os.path.join(self.target_dir, safe + '.png')
            if not os.path.exists(wb_path) and os.path.exists(target_path):
                shutil.copy2(target_path, wb_path)
                log_debug(CAT_FILE, f"ensure_wb_copies: copied {url} .gxi to workbench")
            if not os.path.exists(wb_png) and os.path.exists(target_png):
                shutil.copy2(target_png, wb_png)
                log_debug(CAT_FILE, f"ensure_wb_copies: copied {url} .png to workbench")

    def on_send_prompt_toggled(self, widget):
        val = '1' if widget.get_active() else '0'
        update_env(USER_ENV, 'SEND_PROMPT_ON_FIRE', val)

    def on_harvest_prompt_toggled(self, widget):
        val = '1' if widget.get_active() else '0'
        update_env(USER_ENV, 'HARVEST_PROMPT_ON_STAGE', val)

    def create_account_row(self, editable=True, show_push=False, push_callback=None):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        label = Gtk.Label(label="Acct:")
        box.pack_start(label, False, False, 0)
        entry = Gtk.Entry()
        entry.set_hexpand(True)
        entry.set_editable(editable)
        box.pack_start(entry, True, True, 0)
        if show_push and push_callback:
            push_btn = Gtk.Button(label="Push to active")
            push_btn.connect("clicked", push_callback)
            box.pack_start(push_btn, False, False, 0)
        return box

    def create_comment_box(self, min_height=120):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        label = Gtk.Label(label="Comment:")
        label.set_xalign(0)
        box.pack_start(label, False, False, 0)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(min_height)
        scrolled.set_hexpand(False)
        textview = Gtk.TextView()
        textview.set_wrap_mode(Gtk.WrapMode.WORD)
        textview.set_editable(True)
        scrolled.add(textview)
        box.pack_start(scrolled, True, True, 0)
        return box, textview

    def create_value_widget(self, key, val, readonly):
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
            buffer.set_text(val or '')
        if isinstance(widget, (Gtk.SpinButton, Gtk.CheckButton)):
            widget.set_sensitive(not readonly)
        else:
            widget.set_editable(not readonly)
        return widget

    def on_debug_check_toggled(self, widget, key):
        categories = ['xdo', 'geom', 'init', 'gui', 'daemon', 'input', 'window', 'file']
        checked = [categories[i] for i, c in enumerate(self.debug_checks) if c.get_active()]
        new_val = ','.join(checked) if checked else 'off'
        buffer = self.value_widgets[key]['user'].get_buffer()
        buffer.set_text(new_val)
        self.recompute_merged(key)

    def on_filter_clicked(self, widget):
        self.filter_mode = (self.filter_mode + 1) % 3
        labels = ["All", "Checked only", "Unchecked only"]
        widget.set_label(f"Filter: {labels[self.filter_mode]}")
        self.load_all_gxi()

    def on_invert_selection(self, widget):
        for check in self.gallery_checks.values():
            check.set_active(not check.get_active())

    def on_push_prompt(self, widget):
        if self.busy: return
        buffer = self.live_prompt_view.get_buffer()
        start, end = buffer.get_bounds()
        push_text = buffer.get_text(start, end, False).strip()
        log_debug(CAT_GUI, f"Push to active triggered - text read from Live Prompt box: '{push_text}'")
        if not push_text:
            log_debug(CAT_GUI, "Push skipped - empty text in Live Prompt box")
            return
        active_urls = [u for u in self.gun_active_urls if u in self.batch_urls]
        log_debug(CAT_GUI, f"Push targets (checked active GXIs): {len(active_urls)} total - {active_urls}")
        if not active_urls:
            log_debug(CAT_GUI, "Push skipped - no checked active GXIs")
            return
        for url in active_urls:
            path = self.wb_gxi_paths.get(url)
            if not path:
                log_debug(CAT_GUI, f"Push skipped for {url} - no workbench path found")
                continue
            header_lines, prompts, histories, comment = parse_gxi(path)
            for stage in prompts:
                for i in range(len(prompts[stage])):
                    prompts[stage][i] = prompts[stage][i].lstrip('@')
            prompts['1'].insert(0, '@' + push_text)
            prompts = dedupe_prompts(prompts)
            write_gxi(path, header_lines, prompts, histories, comment)
            log_debug(CAT_GUI, f"Push wrote to workbench GXI for URL {url} - OK")
        if self.current_url in active_urls:
            self.load_current_gxi()

    def on_push_comment(self, widget):
        if self.busy: return
        buffer = self.live_comment_view.get_buffer()
        start, end = buffer.get_bounds()
        new_comment = buffer.get_text(start, end, False)
        if len(new_comment) > 4096:
            dialog = Gtk.MessageDialog(transient_for=self.parent_app, flags=0, message_type=Gtk.MessageType.WARNING,
                                       buttons=Gtk.ButtonsType.OK, text="Comment too long")
            dialog.format_secondary_text("Maximum 4096 characters allowed.")
            dialog.run()
            dialog.destroy()
            return
        active_urls = [u for u in self.gun_active_urls if u in self.batch_urls]
        log_debug(CAT_GUI, f"Push Comment to {len(active_urls)} checked active GXIs: {active_urls}")
        if not active_urls:
            return
        for url in active_urls:
            path = self.wb_gxi_paths.get(url)
            if not path:
                continue
            header_lines, prompts, histories, _ = parse_gxi(path)
            write_gxi(path, header_lines, prompts, histories, new_comment)
            log_debug(CAT_GUI, f"Push Comment wrote to workbench GXI for URL {url} - OK")
        if self.current_url in active_urls:
            self.load_current_gxi()

    def on_push_account(self, widget):
        if self.busy: return
        acct = self.acct_entry.get_text().strip()
        if not acct:
            return
        active_urls = [u for u in self.gun_active_urls if u in self.batch_urls]
        log_debug(CAT_GUI, f"Push Acct '{acct}' to {len(active_urls)} checked active GXIs: {active_urls}")
        if not active_urls:
            return
        for url in active_urls:
            path = self.wb_gxi_paths.get(url)
            if not path:
                continue
            header_lines, prompts, histories, comment = parse_gxi(path)
            new_header = [line for line in header_lines if not line.strip().startswith('ACCOUNT=')]
            new_header.append(f"ACCOUNT={acct}\n")
            write_gxi(path, new_header, prompts, histories, comment)
            log_debug(CAT_GUI, f"Push Acct wrote to workbench GXI for URL {url} - OK")
        if self.current_url in active_urls:
            self.load_current_gxi()

    def toggle_env_panel(self, widget):
        if self.busy: return
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
        if self.busy: return
        if self.gxi_window.get_visible():
            self.save_editor_geometry(self.gxi_window, 'GXI_EDITOR')
            pos = self.gxi_paned.get_position()
            update_env(USER_ENV, 'GXI_PANED_POSITION', str(pos))
            log_debug(CAT_GEOM, f"GXI PANED SAVED on toggle hide position={pos}")
            self.gxi_window.hide()
            self.gallery_window.hide()
            self.gxi_toggle.set_label("GXI Manager ▲")
        else:
            self.apply_editor_geometry(self.gxi_window, self.gxi_saved_width, self.gxi_saved_height, self.gxi_saved_x_off, self.gxi_saved_y_off)
            if self.gxi_paned_position > 0:
                self.gxi_paned.set_position(self.gxi_paned_position)
                log_debug(CAT_GEOM, f"GXI PANED RESTORED position={self.gxi_paned_position}")
            self.gxi_window.show_all()
            self.apply_editor_geometry(self.gallery_window, self.gallery_saved_width, self.gallery_saved_height, self.gallery_saved_x_off, self.gallery_saved_y_off)
            self.gallery_window.show_all()
            self.gxi_toggle.set_label("GXI Manager ▼")
            self.load_all_gxi()

    def hide_and_save_editor(self, window, prefix):
        if self.busy: return True
        if prefix == 'ENV':
            self.save_editor_geometry(self.env_window, 'ENV_EDITOR')
            self.save_env_panel()
        elif prefix == 'GXI':
            self.save_editor_geometry(self.gxi_window, 'GXI_EDITOR')
            pos = self.gxi_paned.get_position()
            update_env(USER_ENV, 'GXI_PANED_POSITION', str(pos))
            log_debug(CAT_GEOM, f"GXI PANED SAVED on delete-event position={pos}")
            self.gallery_window.hide()
        elif prefix == 'GALLERY':
            self.save_editor_geometry(self.gallery_window, 'GALLERY_EDITOR')
            if self.gxi_window.get_visible():
                self.gxi_window.hide()
        window.hide()
        return True

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
        self.gallery_saved_width = safe_int(read_merged_key('GALLERY_EDITOR_WIDTH') or 1200)
        self.gallery_saved_height = safe_int(read_merged_key('GALLERY_EDITOR_HEIGHT') or 800)
        self.gallery_saved_x_off = safe_int(read_merged_key('GALLERY_EDITOR_X_OFFSET') or 100)
        self.gallery_saved_y_off = safe_int(read_merged_key('GALLERY_EDITOR_Y_OFFSET') or 100)
        self.gxi_paned_position = safe_int(read_merged_key('GXI_PANED_POSITION') or 500)

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
        if self.gallery_window.get_visible():
            self.save_editor_geometry(self.gallery_window, 'GALLERY_EDITOR')

    def on_system_override_toggled(self, check):
        self.system_override_enabled = check.get_active()
        for key in self.all_keys:
            widget = self.value_widgets[key]['system']
            if widget:
                if isinstance(widget, Gtk.TextView):
                    widget.set_editable(self.system_override_enabled)
                else:
                    widget.set_sensitive(self.system_override_enabled)

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

    def save_env_panel(self, widget=None):
        if self.busy: return
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
            prune_env(USER_ENV)

    def save_active_gun(self, widget):
        try:
            with open(self.target_list_file, 'w', encoding='utf-8') as f:
                for url in sorted(self.batch_urls):
                    f.write(url + '\n')
            log_debug(CAT_FILE, f"Saved {len(self.batch_urls)} batch URLs to {self.target_list_file}")
        except Exception as e:
            log_debug(CAT_FILE, f"Failed to save batch list: {e}")

    def save_gun_active(self):
        try:
            with open(self.gun_list_file, 'w', encoding='utf-8') as f:
                for url in sorted(self.gun_active_urls):
                    f.write(url + '\n')
            log_debug(CAT_FILE, f"Saved {len(self.gun_active_urls)} gun active URLs to {self.gun_list_file}")
        except Exception as e:
            log_debug(CAT_FILE, f"Failed to save gun active list: {e}")

    def toggle_archive(self, widget):
        if self.busy: return
        self.is_archive = not self.is_archive
        self.current_dir = self.archive_dir if self.is_archive else self.target_dir
        self.archive_toggle_btn.set_label("Hide Archive" if self.is_archive else "Show Archive")
        self.load_all_gxi()

    def archive_gxi(self, widget, url):
        if self.busy: return
        source_path = os.path.join(self.target_dir, urllib.parse.quote(url, safe='') + '.gxi')
        if not os.path.exists(source_path):
            log_debug(CAT_FILE, f"Archive skipped - no path for {url}")
            return
        dest_path = os.path.join(self.archive_dir, os.path.basename(source_path))
        try:
            shutil.move(source_path, dest_path)
            log_debug(CAT_FILE, f"Archived {url} → {os.path.basename(dest_path)}")
        except Exception as e:
            log_debug(CAT_FILE, f"Archive move error for {url}: {e}")
            return
        self.batch_urls.discard(url)
        self.gun_active_urls.discard(url)
        log_debug(CAT_GUI, f"Archive cleaned {url} from batch + gun lists")
        self.save_active_gun(None)
        self.save_gun_active()
        self.load_all_gxi()
        if self.current_url == url:
            self.current_url = None
            self.load_current_gxi()

    def on_carousel_row_clicked(self, widget, url):
        if self.busy: return
        self.current_url = url
        self.load_current_gxi()
        for child in self.carousel_box.get_children():
            child.get_style_context().remove_class("selected")
        widget.get_style_context().add_class("selected")

    def on_gallery_row_clicked(self, widget, url):
        if self.busy: return
        self.current_url = url
        self.load_current_gxi()

    def on_new_target(self, widget):
        if self.busy: return
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
            if self.current_url and self.current_url in self.batch_urls:
                if self.current_url in self.carousel_gun_checks:
                    self.carousel_gun_checks[self.current_url].set_active(True)

    def update_live_prompt_label(self, prompt_text):
        buffer = self.live_prompt_view.get_buffer()
        buffer.set_text(prompt_text)
        log_debug(CAT_GUI, f"Live Prompt box populated from STAGE check toggle: '{prompt_text}'")

    def get_active_prompt_for_url(self, url):
        wb_path = os.path.join(self.workbench_dir, urllib.parse.quote(url, safe='') + '.gxi')
        if os.path.exists(wb_path):
            _, prompts, _, _ = parse_gxi(wb_path)
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
        source = "unknown"
        if self.current_url:
            prompt = self.get_active_prompt_for_url(self.current_url)
            if prompt:
                source = f"GXI active prompt for URL {self.current_url}"
        if not prompt:
            prompt = get_merged_multiline('DEFAULT_PROMPT').strip()
            if prompt:
                source = "DEFAULT_PROMPT fallback"
        buffer = self.live_prompt_view.get_buffer()
        buffer.set_text(prompt or "")
        log_debug(CAT_GUI, f"Live Prompt box populated from {source}: '{prompt or ''}'")

    def on_editor_active_toggled(self, check):
        if self.current_url:
            if check.get_active():
                self.gun_active_urls.add(self.current_url)
            else:
                self.gun_active_urls.discard(self.current_url)

    def update_status(self, text):
        self.parent_app.set_title(f"Blitz Talker — {text}")

    def update_fire_state(self):
        can_fire = bool(self.current_wids) and not self.firing and bool(self.gun_active_urls)
        self.fire_btn.set_sensitive(can_fire or self.firing)
        self.fire_btn.set_label("STOP" if self.firing else "FIRE")
        self.update_status("Ready" if not self.firing else "Firing...")

    def load_current_gxi(self):
        if self.busy: return
        self.current_gxi_path = None
        self.current_histories = {}
        self.thumb_image.set_from_pixbuf(None)
        self.thumb_image.hide()

        if not self.current_url:
            self.live_comment_view.get_buffer().set_text("")
            self.comment_view.get_buffer().set_text("")
            self.acct_entry.set_text("")
            self.editor_active_check.set_active(False)
            self.update_live_prompt_from_selection()
            return

        self.url_label.set_text(self.get_display_url(self.current_url))
        self.url_label.set_tooltip_text(self.current_url)

        safe_name = urllib.parse.quote(self.current_url, safe='') + '.gxi'
        wb_path = os.path.join(self.workbench_dir, safe_name)
        self.current_gxi_path = wb_path
        if not os.path.exists(wb_path):
            log_debug(CAT_FILE, f"LOAD_CURRENT_GXI: File not found in workbench for '{self.current_url}' - path {wb_path}")
            self.live_comment_view.get_buffer().set_text("")
            self.comment_view.get_buffer().set_text("")
            self.acct_entry.set_text("")
            self.editor_active_check.set_active(False)
            self.update_live_prompt_from_selection()
            return

        header_lines, prompts, histories, comment = parse_gxi(self.current_gxi_path)

        born_on = "Never"
        account = ""
        for line in header_lines:
            stripped = line.strip()
            if stripped.startswith('BORN_ON='):
                born_on = stripped[8:].strip()
            elif stripped.startswith('ACCOUNT='):
                account = stripped[8:].strip()

        self.born_label.set_text(f"Born On: {born_on}")
        self.acct_entry.set_text(account)
        self.comment_view.get_buffer().set_text(comment)
        self.live_comment_view.get_buffer().set_text(comment)
        self.editor_active_check.set_active(self.current_url in self.gun_active_urls)

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

        thumb_path = os.path.join(self.workbench_dir, urllib.parse.quote(self.current_url, safe='') + '.png')
        if os.path.exists(thumb_path):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(thumb_path, 256, 256, True)
                self.thumb_image.set_from_pixbuf(pixbuf)
                self.thumb_image.show()
                log_debug(CAT_FILE, "LOAD_CURRENT_GXI: Loaded real thumbnail for right pane")
            except Exception as e:
                log_debug(CAT_FILE, f"LOAD_CURRENT_GXI: Right pane thumbnail load failed - hidden: {e}")
        else:
            log_debug(CAT_FILE, "LOAD_CURRENT_GXI: No thumbnail - using placeholder")
            placeholder = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 256, 256)
            placeholder.fill(0x333333ff)
            self.thumb_image.set_from_pixbuf(placeholder)
            self.thumb_image.show()

        self.update_live_prompt_from_selection()

    def save_current_gxi(self, widget=None):
        if self.busy: return
        if not self.current_gxi_path or not self.current_url:
            return

        comment_buffer = self.comment_view.get_buffer()
        start, end = comment_buffer.get_bounds()
        comment = comment_buffer.get_text(start, end, False)

        if len(comment) > 4096:
            dialog = Gtk.MessageDialog(transient_for=self.parent_app, flags=0, message_type=Gtk.MessageType.WARNING,
                                       buttons=Gtk.ButtonsType.OK, text="Comment too long")
            dialog.format_secondary_text("Maximum allowed is 4096 characters.")
            dialog.run()
            dialog.destroy()
            return

        acct = self.acct_entry.get_text().strip()

        header_lines, prompts, histories, _ = parse_gxi(self.current_gxi_path)
        new_header = []
        for line in header_lines:
            if line.strip().startswith(('BORN_ON=', 'ACCOUNT=', 'TARGET_DESC=')):
                continue
            new_header.append(line)
        new_header.append(f"BORN_ON={datetime.now().strftime('%Y-%m-%d')}\n")
        new_header.append(f"ACCOUNT={acct}\n")
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

        prompts = dedupe_prompts(prompts)
        apply_overflow(prompts)
        write_gxi(self.current_gxi_path, new_header, prompts, histories, comment)
        self.update_live_prompt_from_selection()

    def on_stage(self, widget):
        if self.busy: return
        self.busy = True
        self.stage_btn.set_sensitive(False)
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
        urls = [u for u in self.all_urls if u in self.batch_urls]
        if not urls:
            self.update_status("Ready")
            self.update_fire_state()
            self.busy = False
            self.stage_btn.set_sensitive(True)
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
        self.busy = False
        self.stage_btn.set_sensitive(True)

    def gentle_target_op(self, op_type, sync=True, delay=None, capture=True):
        delay = safe_float(read_merged_key('TARGET_OP_DELAY') or 0.25)

        if op_type == 'kill':
            for wid_str in self.current_wids[:]:
                try:
                    wid_int = int(wid_str)
                    wid_hex = f"0x{wid_int:08x}"
                    log_debug(CAT_XDO, f"XDO: wmctrl -i -c {wid_hex} (close wid {wid_str})")
                    res = subprocess.run(['wmctrl', '-i', '-c', wid_hex], check=False, capture_output=True, text=True)
                    log_debug(CAT_XDO, f"XDO: wmctrl close returncode {res.returncode}" + (f", stderr: {res.stderr.strip()}" if res.returncode != 0 else ""))
                    time.sleep(delay)
                except Exception as e:
                    log_debug(CAT_XDO, f"XDO: error closing wid {wid_str}: {e}")
            self.current_wids = []
            self.update_fire_state()
            return

        if op_type == 'activate' and self.current_wids:
            capture_mode = safe_int(read_merged_key('CAPTURE_MODE') or '0')
            do_capture = capture and capture_mode != 0
            harvest_enabled = read_merged_key('HARVEST_PROMPT_ON_STAGE') in ('1', 'Y', 'true', 'True', 'yes', 'on') if do_capture else False

            processed_urls = set() if do_capture else None

            for idx, wid_str in enumerate(self.current_wids, start=1):
                log_debug(CAT_XDO, f"XDO: windowactivate {'--sync' if sync else ''} {wid_str}")
                subprocess.run(['xdotool', 'windowactivate', '--sync' if sync else '', wid_str], capture_output=True, text=True)
                time.sleep(delay)

                if do_capture:
                    cycle_url = self.cycle_urls[(idx - 1) % len(self.cycle_urls)]
                    if processed_urls is not None and cycle_url in processed_urls:
                        time.sleep(delay)
                        continue

                    safe_name = urllib.parse.quote(cycle_url, safe='')
                    capture_path = os.path.join(self.workbench_dir, f"{safe_name}.png")

                    if idx - 1 < len(self.capture_click_positions):
                        click_x, click_y = self.capture_click_positions[idx - 1]

                        try:
                            mouse_loc = subprocess.check_output(['xdotool', 'getmouselocation'], text=True).strip()
                            log_debug(CAT_XDO, f"XDO: mouse before ops on wid {wid_str}: {mouse_loc}")
                        except Exception as e:
                            log_debug(CAT_XDO, f"XDO: getmouselocation pre failed: {e}")

                        log_debug(CAT_XDO, f"XDO: mousemove {click_x} {click_y}, click 1 on wid {wid_str}")
                        subprocess.run(['xdotool', 'mousemove', str(click_x), str(click_y),
                                        'click', '1'], capture_output=True, text=True)
                        time.sleep(delay)

                        log_debug(CAT_XDO, f"XDO: key ctrl+a on {wid_str}")
                        subprocess.run(['xdotool', 'key', '--window', wid_str, '--clearmodifiers', 'ctrl+a'],
                                       capture_output=True, text=True)
                        time.sleep(0.1)

                        if harvest_enabled:
                            log_debug(CAT_XDO, f"XDO: key ctrl+c on {wid_str} (harvest)")
                            subprocess.run(['xdotool', 'key', '--window', wid_str, '--clearmodifiers', 'ctrl+c'],
                                           capture_output=True, text=True)
                            time.sleep(delay)

                            prompt = get_clipboard()
                            path = self.wb_gxi_paths.get(cycle_url)
                            if prompt and path:
                                header_lines, prompts, histories, _ = parse_gxi(path)
                                prompts['U'].insert(0, prompt)
                                apply_overflow(prompts)
                                write_gxi(path, header_lines, prompts, histories, "")

                        needs_delete = True
                        if needs_delete:
                            log_debug(CAT_XDO, f"XDO: key Delete on {wid_str}")
                            res = subprocess.run(['xdotool', 'key', '--window', wid_str, '--clearmodifiers', 'Delete'],
                                                 capture_output=True, text=True)
                            log_debug(CAT_XDO, f"XDO: Delete key returncode {res.returncode}" + (f", stderr: {res.stderr.strip()}" if res.returncode != 0 else ""))
                        time.sleep(delay)

                        log_debug(CAT_XDO, f"XDO: capturing with maim on wid {wid_str} → {capture_path}")
                        cmd = ['maim', '--hidecursor', '-i', wid_str, capture_path]
                        result = subprocess.run(cmd, capture_output=True)
                        if result.returncode == 0:
                            log_debug(CAT_XDO, f"XDO: capture success → {capture_path}")
                        else:
                            log_debug(CAT_XDO, f"XDO: capture failed, returncode {result.returncode}, stderr: {result.stderr.decode().strip()}")

                    if processed_urls is not None:
                        processed_urls.add(cycle_url)
                time.sleep(delay)

    def on_fire(self, widget=None):
        if self.busy: return
        self.busy = True
        self.fire_btn.set_sensitive(False)
        self.save_current_gxi()
        if self.env_window.get_visible():
            self.save_env_panel()
        if not self.firing:
            if not self.current_wids:
                self.busy = False
                self.fire_btn.set_sensitive(True)
                return
            self.firing = True
            update_env(IMAGINE_ENV, 'FIRE_MODE', 'Y')
            self.update_fire_state()
            self.update_status("Firing...")
            sw, sh = 1920, 1080
            log_debug(CAT_XDO, "XDO: getdisplaygeometry (stacking)")
            try:
                output = subprocess.check_output(['xdotool', 'getdisplaygeometry']).decode().strip()
                sw, sh = map(int, output.split())
                log_debug(CAT_XDO, f"XDO: getdisplaygeometry → {sw}x{sh}")
            except Exception as e:
                log_debug(CAT_XDO, f"XDO: getdisplaygeometry failed: {e}")
            target_width = safe_int(read_merged_key('TARGET_WIDTH') or 640)
            target_height = safe_int(read_merged_key('TARGET_HEIGHT') or 500)
            center_x = (sw - target_width) // 2
            center_y = (sh - target_height) // 2
            offset_x = safe_int(read_merged_key('FIRE_STACK_X_OFFSET') or 0)
            offset_y = safe_int(read_merged_key('FIRE_STACK_Y_OFFSET') or 0)
            stack_x = center_x + offset_x
            stack_y = center_y + offset_y
            for wid in self.current_wids:
                geom_res = subprocess.run(['xdotool', 'getwindowgeometry', '--shell', wid], capture_output=True, text=True)
                if geom_res.returncode == 0:
                    log_debug(CAT_XDO, f"XDO: pre-stack geometry wid {wid}:\n{geom_res.stdout.strip()}")
                xdo_resize_move(wid, target_width, target_height, stack_x, stack_y)
                geom_res = subprocess.run(['xdotool', 'getwindowgeometry', '--shell', wid], capture_output=True, text=True)
                if geom_res.returncode == 0:
                    log_debug(CAT_XDO, f"XDO: post-stack geometry wid {wid}:\n{geom_res.stdout.strip()}")

            relative_x = percent_to_pixels(read_merged_key('PROMPT_X_FROM_LEFT') or '50%', target_width)
            relative_y = target_height - percent_to_pixels(read_merged_key('PROMPT_Y_FROM_BOTTOM') or '10%', target_height)
            prompt_x = stack_x + relative_x
            prompt_y = stack_y + relative_y

            self.daemon_thread = threading.Thread(target=self.daemon_thread_func, args=(prompt_x, prompt_y, relative_x, relative_y), daemon=True)
            self.daemon_thread.start()
        else:
            self.firing = False
            update_env(IMAGINE_ENV, 'FIRE_MODE', 'N')
            self.update_fire_state()
            self.update_status("Stopped")
        self.busy = False
        self.fire_btn.set_sensitive(True)

    def daemon_thread_func(self, prompt_x, prompt_y, relative_x, relative_y):
        total_shots = 0
        fire_count = self.fire_spin.get_value_as_int()
        round_delay = safe_float(read_merged_key('ROUND_DELAY') or 10)
        inter_target_delay = safe_float(read_merged_key('INTER_TARGET_DELAY') or 0.5)
        shot_delay = safe_float(read_merged_key('SHOT_DELAY') or 0.5)

        wid = self.current_wids[0]
        log_debug(CAT_XDO, f"XDO: mousemove --window {wid} {relative_x} {relative_y} (single pre-loop move to prompt in stack)")
        subprocess.run(['xdotool', 'mousemove', '--window', str(wid), '--sync', str(relative_x), str(relative_y)],
                       capture_output=True, text=True)

        def update_status(text):
            GLib.idle_add(lambda: self.update_status(text) or False)
        update_status("Firing... 0 shots")

        gun_urls = [u for u in self.batch_urls if u in self.gun_active_urls]
        if not gun_urls:
            self.firing = False
            update_env(IMAGINE_ENV, 'FIRE_MODE', 'N')
            GLib.idle_add(self.update_fire_state)
            return

        for round_num in range(1, fire_count + 1):
            if round_num > 1:
                time.sleep(round_delay)
            if not self.current_wids or not self.firing:
                break

            for idx, wid in enumerate(self.current_wids, start=1):
                if not self.firing:
                    break

                cycle_url = gun_urls[(idx - 1) % len(gun_urls)]
                prompt = self.get_active_prompt_for_url(cycle_url)

                if prompt is None:
                    log_debug(CAT_DAEMON, f"Skipping {cycle_url} - no prompt available")
                    continue

                orig_clip = get_clipboard()

                if prompt.strip() == self.PROMPT_ERASE_CHAR:
                    gentle_target_op('key', '--window', str(wid), 'Control+a Delete')
                    clipboard_set("")
                    gentle_target_op('key', '--window', str(wid), 'Return')

                elif prompt.strip() == self.PROMPT_SILENT_CHAR:
                    clipboard_set("")
                    gentle_target_op('key', '--window', str(wid), 'Return')

                else:
                    clipboard_set(prompt)
                    if self.PROMPT_FIRE_CHAIN:
                        gentle_target_op('key', '--window', str(wid), '--delay', '5', 'Control+a Delete Control+v Return')
                    else:
                        gentle_target_op('key', '--window', str(wid), 'Control+a Delete')
                        gentle_target_op('key', '--window', str(wid), 'Control+v')
                        gentle_target_op('key', '--window', str(wid), 'Return')

                clipboard_set(orig_clip)

                time.sleep(shot_delay)
                total_shots += 1
                time.sleep(inter_target_delay)
                update_status(f"Firing round {round_num}/{fire_count} — {total_shots} shots")

                path = self.wb_gxi_paths.get(cycle_url)
                if path and os.path.exists(path):
                    try:
                        with open(path, 'a', encoding='utf-8') as f:
                            f.write(f"{prompt}\n")
                    except:
                        pass

        update_status(f"Done — {total_shots} shots")
        self.firing = False
        update_env(IMAGINE_ENV, 'FIRE_MODE', 'N')
        GLib.idle_add(self.update_fire_state)
        GLib.idle_add(lambda: self.grid_windows(len(self.current_wids)) or False)
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

        self.save_active_gun(None)
        self.save_gun_active()

        try:
            for file in os.listdir(self.workbench_dir):
                if file.lower().endswith(('.gxi', '.png')):
                    src = os.path.join(self.workbench_dir, file)
                    dst = os.path.join(self.target_dir, file)
                    shutil.copy2(src, dst)
                    log_debug(CAT_FILE, f"Clean quit copied {file} from workbench to targets")
        except Exception as e:
            log_debug(CAT_FILE, f"Clean quit copy error: {e}")

        prune_env(USER_ENV)
        prune_env(IMAGINE_ENV, runtime_keys=RUNTIME_KEYS)
        self.gentle_target_op('kill')
        Gtk.main_quit()

    def load_all_gxi(self):
        if self.busy: return
        self.busy = True
        log_debug(CAT_FILE, "LOAD_ALL_GXI: Starting rebuild")
        try:
            files = os.listdir(self.target_dir)
        except Exception as e:
            log_debug(CAT_FILE, f"LOAD_ALL_GXI: os.listdir FAILED: {e}")
            files = []
        self.all_urls = []
        self.gxi_paths = {}
        for file in files:
            if file.lower().endswith('.gxi'):
                decoded_url = urllib.parse.unquote(file[:-4])
                self.all_urls.append(decoded_url)
                self.gxi_paths[decoded_url] = os.path.join(self.target_dir, file)
        self.all_urls.sort(key=str.lower)

        self.batch_urls = {u for u in self.batch_urls if u in self.gxi_paths}
        self.gun_active_urls = {u for u in self.gun_active_urls if u in self.gxi_paths}
        log_debug(CAT_GUI, f"Auto-pruned ghosts - batch:{len(self.batch_urls)} gun:{len(self.gun_active_urls)}")

        for child in self.gallery_flowbox.get_children():
            self.gallery_flowbox.remove(child)
        for child in self.carousel_box.get_children():
            self.carousel_box.remove(child)

        self.gallery_checks.clear()
        self.carousel_gun_checks.clear()
        self.row_widgets.clear()

        for url in self.all_urls:
            if self.filter_mode == 1 and url not in self.batch_urls:
                continue
            if self.filter_mode == 2 and url in self.batch_urls:
                continue

            gallery_row = self.create_thumb_row(url, True)
            flow_child = Gtk.FlowBoxChild()
            flow_child.add(gallery_row)
            self.gallery_flowbox.add(flow_child)

        self.load_carousel()

        self.gallery_flowbox.show_all()
        self.carousel_box.show_all()
        self.load_current_gxi()

        self.save_active_gun(None)
        self.save_gun_active()
        self.busy = False

    def load_carousel(self):
        if self.busy: return
        for child in self.carousel_box.get_children():
            self.carousel_box.remove(child)
        self.carousel_gun_checks.clear()
        self.row_widgets.clear()

        try:
            wb_files = os.listdir(self.workbench_dir)
        except Exception as e:
            log_debug(CAT_FILE, f"load_carousel os.listdir workbench failed: {e}")
            wb_files = []
        for file in wb_files:
            if file.lower().endswith('.gxi'):
                decoded_url = urllib.parse.unquote(file[:-4])
                if decoded_url in self.batch_urls:
                    carousel_row = self.create_thumb_row(decoded_url, False)
                    self.carousel_box.add(carousel_row)

        # === FORCE CAROUSEL REDRAW (this fixes your exact bug) ===
        for child in self.carousel_box.get_children():
            child.show_all()
        self.carousel_box.show_all()
        self.carousel_box.queue_resize()
        self.carousel_box.queue_draw()
        self.carousel_scrolled.queue_draw()

        log_debug(CAT_GUI, f"load_carousel: added {len(self.carousel_box.get_children())} items to carousel")

        if not self.batch_urls:
            self.current_url = None
            self.load_current_gxi()

    def create_thumb_row(self, url, is_gallery):
        eventbox = Gtk.EventBox()
        row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        row_box.set_margin_start(10)
        row_box.set_margin_end(10)
        row_box.set_margin_top(6)
        row_box.set_margin_bottom(6)

        thumb_size = 100 if not is_gallery else 180
        thumb_btn = Gtk.Button()
        thumb_btn.set_size_request(thumb_size, thumb_size)
        thumb_btn.set_relief(Gtk.ReliefStyle.NORMAL)

        if is_gallery:
            thumb_path = os.path.join(self.target_dir, urllib.parse.quote(url, safe='') + '.png')
        else:
            thumb_path = os.path.join(self.workbench_dir, urllib.parse.quote(url, safe='') + '.png')
        if os.path.exists(thumb_path):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(thumb_path, thumb_size, thumb_size, True)
                img = Gtk.Image.new_from_pixbuf(pixbuf)
                thumb_btn.add(img)
            except Exception as e:
                log_debug(CAT_FILE, f"Thumbnail load failed: {e}")
                thumb_btn.get_style_context().add_class("missing-thumb")
        else:
            thumb_btn.get_style_context().add_class("missing-thumb")

        if is_gallery:
            thumb_btn.connect("clicked", lambda w, u=url: self.on_gallery_row_clicked(w, u))
        else:
            thumb_btn.connect("clicked", lambda w, u=url: self.on_carousel_row_clicked(w, u))

        row_box.pack_start(thumb_btn, False, False, 0)

        url_label = Gtk.Label(label=self.get_display_url(url))
        url_label.set_xalign(0)
        url_label.set_ellipsize(Pango.EllipsizeMode.END)
        url_label.set_max_width_chars(35)
        url_label.set_tooltip_text(url)
        url_label.get_style_context().add_class("url-label")
        row_box.pack_start(url_label, True, True, 0)

        check_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        check = Gtk.CheckButton(label="Batch" if is_gallery else "Gun Act")
        if is_gallery:
            check.set_active(url in self.batch_urls)
            check.connect("toggled", self.on_gallery_batch_toggled, url)
            self.gallery_checks[url] = check
        else:
            check.set_active(url in self.gun_active_urls)
            check.connect("toggled", self.on_carousel_gun_toggled, url)
            self.carousel_gun_checks[url] = check
        check_box.pack_start(check, False, False, 0)

        header_lines, _, _, _ = parse_gxi(os.path.join(self.workbench_dir if not is_gallery else self.target_dir, urllib.parse.quote(url, safe='') + '.gxi'))
        born_on = "Never"
        account = ""
        for line in header_lines:
            stripped = line.strip()
            if stripped.startswith('BORN_ON='):
                born_on = stripped[8:].strip()
            elif stripped.startswith('ACCOUNT='):
                account = stripped[8:].strip()

        born_label = Gtk.Label(label=f"Born On: {born_on}")
        born_label.set_xalign(0)
        check_box.pack_start(born_label, False, False, 0)

        acct_label = Gtk.Label(label=f"Acct: {account}")
        acct_label.set_xalign(0)
        acct_label.set_max_width_chars(18)
        acct_label.set_ellipsize(Pango.EllipsizeMode.END)
        check_box.pack_start(acct_label, False, False, 0)

        row_box.pack_start(check_box, False, False, 0)

        if is_gallery:
            archive_btn = Gtk.Button(label="Restore" if self.is_archive else "Archive")
            archive_btn.connect("clicked", self.archive_gxi, url)
            row_box.pack_start(archive_btn, False, False, 0)

        eventbox.add(row_box)
        if not is_gallery:
            if check.get_active():
                eventbox.get_style_context().add_class("active-row")
            self.row_widgets[url] = eventbox
        return eventbox

    def on_gallery_batch_toggled(self, check, url):
        if self.mass_updating:
            return
        safe = urllib.parse.quote(url, safe='')
        wb_path = os.path.join(self.workbench_dir, safe + '.gxi')
        target_path = os.path.join(self.target_dir, safe + '.gxi')
        wb_png = os.path.join(self.workbench_dir, safe + '.png')
        target_png = os.path.join(self.target_dir, safe + '.png')

        if check.get_active():
            self.batch_urls.add(url)
            if url not in self.gun_active_urls:
                self.gun_active_urls.add(url)
            if not os.path.exists(wb_path) and os.path.exists(target_path):
                shutil.copy2(target_path, wb_path)
            if not os.path.exists(wb_png) and os.path.exists(target_png):
                shutil.copy2(target_png, wb_png)
            log_debug(CAT_FILE, f"Added to carousel: {url}")
        else:
            self.batch_urls.discard(url)
            self.gun_active_urls.discard(url)
            if os.path.exists(wb_path):
                os.remove(wb_path)
            if os.path.exists(wb_png):
                os.remove(wb_png)
            log_debug(CAT_FILE, f"Removed from carousel: {url}")
        self.load_carousel()

    def on_carousel_gun_toggled(self, check, url):
        if self.mass_updating:
            return
        if check.get_active():
            self.gun_active_urls.add(url)
        else:
            self.gun_active_urls.discard(url)
        eventbox = self.row_widgets.get(url)
        if eventbox:
            if check.get_active():
                eventbox.get_style_context().add_class("active-row")
            else:
                eventbox.get_style_context().remove_class("active-row")

    def grid_windows(self, expected_num):
        if self.busy: return False
        self.busy = True
        patterns = {p.strip().strip('"').strip("'").lower() for p in (read_merged_key('TARGET_PATTERNS') or '').split(',') if p.strip()}
        max_tries = 30
        last_total_windows = -1
        stagnant_limit = 3
        stagnant_count = 0
        last_matched = []
        grid_start_delay = safe_float(read_merged_key('GRID_START_DELAY') or 5)
        for attempt in range(1, max_tries + 1):
            log_debug(CAT_XDO, "XDO: search --onlyvisible .")
            result = subprocess.run(['xdotool', 'search', '--onlyvisible', '.'], capture_output=True, text=True)
            all_ids = result.stdout.strip().splitlines() if result.returncode == 0 else []
            log_debug(CAT_XDO, f"XDO: search found {len(all_ids)} visible windows, returncode {result.returncode}")
            if len(all_ids) == last_total_windows:
                stagnant_count += 1
            else:
                stagnant_count = 0
            last_total_windows = len(all_ids)
            matched = []
            for wid in all_ids:
                log_debug(CAT_XDO, f"XDO: getwindowname {wid}")
                name_res = subprocess.run(['xdotool', 'getwindowname', wid], capture_output=True, text=True)
                name = name_res.stdout.strip()
                log_debug(CAT_XDO, f"XDO: wid {wid} title \"{name}\", returncode {name_res.returncode}")
                if name.lower() in patterns:
                    matched.append(wid)
            if matched:
                last_matched = matched[:]
            matched = sorted(matched, key=int)
            if len(matched) >= expected_num:
                self.current_wids = matched
                self._grid_ids(matched)
                time.sleep(grid_start_delay)
                self.gentle_target_op('activate', capture=True)
                self.update_status("Ready")
                self.update_fire_state()
                GLib.idle_add(lambda: self.parent_app.present())
                self.busy = False
                return False
            if stagnant_count >= stagnant_limit:
                if last_matched:
                    self.current_wids = sorted(last_matched, key=int)
                    self._grid_ids(self.current_wids)
                    time.sleep(grid_start_delay)
                    self.gentle_target_op('activate', capture=True)
                self.update_status("Ready")
                self.update_fire_state()
                GLib.idle_add(lambda: self.parent_app.present())
                self.busy = False
                return False
            time.sleep(grid_start_delay)
        if last_matched:
            self.current_wids = sorted(last_matched, key=int)
            self._grid_ids(self.current_wids)
            time.sleep(grid_start_delay)
            self.gentle_target_op('activate', capture=True)
        self.update_status("Ready")
        self.update_fire_state()
        GLib.idle_add(lambda: self.parent_app.present())
        self.busy = False
        return False

    def _grid_ids(self, ids):
        log_debug(CAT_XDO, "XDO: getdisplaygeometry")
        result = subprocess.run(['xdotool', 'getdisplaygeometry'], capture_output=True, text=True)
        sw, sh = 1920, 1080
        if result.returncode == 0:
            try:
                sw, sh = map(int, result.stdout.strip().split())
                log_debug(CAT_XDO, f"XDO: display geometry {sw}x{sh}")
            except:
                log_debug(CAT_XDO, "XDO: failed to parse display geometry")
        else:
            log_debug(CAT_XDO, f"XDO: getdisplaygeometry failed, returncode {result.returncode}")

        wx, wy, ww, wh = 0, 0, sw, sh
        work_result = subprocess.run(['xprop', '-root', '-notype', '_NET_WORKAREA'], capture_output=True, text=True)
        if work_result.returncode == 0:
            output = work_result.stdout.strip()
            numbers = re.findall(r'\d+', output)
            if len(numbers) >= 4:
                wx = int(numbers[0])
                wy = int(numbers[1])
                ww = int(numbers[2])
                wh = int(numbers[3])
                log_debug(CAT_XDO, f"XDO: workarea {wx},{wy} {ww}x{wh}")

        target_width = safe_int(read_merged_key('TARGET_WIDTH') or 640)
        target_height = safe_int(read_merged_key('TARGET_HEIGHT') or 500)
        target_overlap = safe_int(read_merged_key('MAX_OVERLAP_PERCENT') or 40)
        margin = 20
        available_width = ww - 2 * margin
        available_height = wh - 2 * margin
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
        x_start = wx + margin + max(0, (available_width - (target_width + (cols - 1) * step_x)) // 2)
        y_start = wy + margin + max(0, (available_height - (target_height + (rows - 1) * step_y)) // 2)
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

                geom_res = subprocess.run(['xdotool', 'getwindowgeometry', '--shell', wid], capture_output=True, text=True)
                if geom_res.returncode == 0:
                    log_debug(CAT_XDO, f"XDO: pre-grid geometry wid {wid}:\n{geom_res.stdout.strip()}")
                else:
                    log_debug(CAT_XDO, f"XDO: pre-grid getwindowgeometry failed for wid {wid}, returncode {geom_res.returncode}")

                xdo_resize_move(wid, target_width, target_height, x, y)

                geom_res = subprocess.run(['xdotool', 'getwindowgeometry', '--shell', wid], capture_output=True, text=True)
                if geom_res.returncode == 0:
                    log_debug(CAT_XDO, f"XDO: post-grid geometry wid {wid}:\n{geom_res.stdout.strip()}")
                else:
                    log_debug(CAT_XDO, f"XDO: post-grid getwindowgeometry failed for wid {wid}, returncode {geom_res.returncode}")
        except Exception as e:
            log_debug(CAT_XDO, f"XDO: exception during grid positioning: {e}")

    def select_all_carousel(self, widget):
        if self.busy: return
        self.busy = True
        self.mass_updating = True
        log_debug(CAT_GUI, f"Select All Gun START - {len(self.batch_urls)} items")
        for url in list(self.batch_urls):
            self.gun_active_urls.add(url)
        self.mass_updating = False
        self.load_carousel()
        log_debug(CAT_GUI, f"Select All Gun COMPLETE")
        self.busy = False

    def deselect_all_carousel(self, widget):
        if self.busy: return
        self.busy = True
        self.mass_updating = True
        log_debug(CAT_GUI, f"Deselect All Gun START - {len(self.batch_urls)} items")
        self.gun_active_urls.clear()
        self.mass_updating = False
        self.load_carousel()
        log_debug(CAT_GUI, f"Deselect All Gun COMPLETE")
        self.busy = False

    def clear_all_gun(self, widget=None):
        if self.busy: return
        self.busy = True
        self.mass_updating = True
        log_debug(CAT_GUI, "Clear Carousel START")

        wb = self.workbench_dir
        deleted = 0
        for f in os.listdir(wb):
            if f.lower().endswith(('.gxi', '.png')):
                try:
                    os.remove(os.path.join(wb, f))
                    deleted += 1
                except Exception as e:
                    log_debug(CAT_FILE, f"Failed to delete {f}: {e}")
        log_debug(CAT_FILE, f"Clear Carousel: deleted {deleted} files from workbench")

        self.gun_active_urls.clear()

        self.mass_updating = False
        self.load_carousel()
        log_debug(CAT_GUI, "Clear Carousel COMPLETE")
        self.busy = False

    def select_all_gallery(self, widget):
        if self.busy: return
        self.busy = True
        self.mass_updating = True
        log_debug(CAT_GUI, f"Select All Gallery START - {len(self.all_urls)} items")
        for url in self.all_urls:
            self.batch_urls.add(url)
            self.gun_active_urls.add(url)
        self.mass_updating = False
        self.ensure_wb_copies()
        GLib.idle_add(self.load_all_gxi)   # idle_add fixes bulk redraw
        log_debug(CAT_GUI, f"Select All Gallery COMPLETE")
        self.busy = False

    def deselect_all_gallery(self, widget):
        if self.busy: return
        self.busy = True
        self.mass_updating = True
        log_debug(CAT_GUI, f"Deselect All Gallery START - {len(self.all_urls)} items")

        wb = self.workbench_dir
        deleted = 0
        for f in os.listdir(wb):
            if f.lower().endswith(('.gxi', '.png')):
                try:
                    os.remove(os.path.join(wb, f))
                    deleted += 1
                except Exception as e:
                    log_debug(CAT_FILE, f"Failed to delete {f}: {e}")
        log_debug(CAT_FILE, f"Deselect All Gallery: deleted {deleted} files from workbench")

        self.batch_urls.clear()
        self.gun_active_urls.clear()

        self.mass_updating = False
        GLib.idle_add(self.load_all_gxi)   # idle_add fixes bulk redraw
        log_debug(CAT_GUI, f"Deselect All Gallery COMPLETE")
        self.busy = False

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
            log_debug(CAT_INIT, f"Imported and created {added} new GXIs from file {file_path}")
            if urls:
                self.current_url = urls[0]
                update_env(USER_ENV, 'DEFAULT_CURRENT_URL', self.current_url)

            self.wb_gxi_paths = {}
            for u in self.all_urls:
                safe_name = urllib.parse.quote(u, safe='') + '.gxi'
                self.wb_gxi_paths[u] = os.path.join(self.workbench_dir, safe_name)

            self.load_all_gxi()
            self.load_current_gxi()
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
            target_path = os.path.join(self.target_dir, basename)
            wb_path = os.path.join(self.workbench_dir, basename)
            if os.path.exists(target_path):
                shutil.copy2(target_path, wb_path)
            else:
                shutil.copy2(gxi_path, wb_path)
            try:
                with open(wb_path, 'r', encoding='utf-8') as f:
                    first_line = f.readline().strip()
                    if first_line.startswith('TARGET_URL='):
                        url = first_line.split('=', 1)[1].strip().strip('"\'')
                        self.current_url = url
                        update_env(USER_ENV, 'DEFAULT_CURRENT_URL', url)
                        self.batch_urls.add(url)
                        self.gun_active_urls.add(url)
            except Exception as e:
                log_debug(CAT_INIT, f"Failed to extract URL from {wb_path}: {e}")
            self.load_all_gxi()
            if self.current_url:
                self.load_current_gxi()

    def add_or_select_url(self, url):
        if not url or '://' not in url:
            log_debug(CAT_INIT, f"Rejected non-URL '{url}' - not adding to gallery or workbench")
            return False
        safe = urllib.parse.quote(url, safe='') + '.gxi'
        target_path = os.path.join(self.target_dir, safe)
        wb_path = os.path.join(self.workbench_dir, safe)
        created = False
        if not os.path.exists(wb_path):
            if os.path.exists(target_path):
                shutil.copy2(target_path, wb_path)
            else:
                with open(wb_path, 'w', encoding='utf-8') as f:
                    f.write(f"TARGET_URL={url}\n")
                    f.write(f"BORN_ON={datetime.now().strftime('%Y-%m-%d')}\n")
                    f.write("ACCOUNT=\n")
                    f.write("TARGET_DESC=\n\n")
                    for stage in ['U', '1', '2', '3']:
                        f.write(f"STAGE_{stage}\n\n")
                        f.write(f".history_{stage}\n\n")
                created = True
            log_debug(CAT_INIT, f"Created/copied to workbench for {url}")

            if not os.path.exists(target_path):
                shutil.copy2(wb_path, target_path)
                log_debug(CAT_INIT, f"Sync copy to target_dir for {url}")

        self.batch_urls.add(url)
        self.gun_active_urls.add(url)
        if not self.current_url:
            self.current_url = url
            update_env(USER_ENV, 'DEFAULT_CURRENT_URL', url)
        return created

    def get_display_url(self, url):
        if not url:
            return ""
        try:
            clean = urllib.parse.unquote(url)
            return clean
        except:
            return url

    def force_redraw_carousel(self):
        self.carousel_scrolled.queue_draw()
        self.carousel_box.queue_resize()
        return False

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
