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

USER_ENV = '.user_env'
IMAGINE_ENV = '.imagine_env'
SYSTEM_ENV = '.system_env'
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEBUG_DIR = os.path.join(SCRIPT_DIR, '.debug')
os.makedirs(DEBUG_DIR, exist_ok=True)
FLOAT_ENV_POS = os.path.join(SCRIPT_DIR, '.float_env_pos')
FLOAT_GXI_POS = os.path.join(SCRIPT_DIR, '.float_gxi_pos')
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

def get_merged_multiline(key):
    for file in (USER_ENV, IMAGINE_ENV, SYSTEM_ENV):
        env = load_env_multiline(file)
        if key in env:
            log_debug(CAT_FILE, f"Merged multiline {key} = {repr(env[key])} from {file}")
            return env[key]
    log_debug(CAT_FILE, f"Merged multiline {key} not found")
    return ''

def percent_to_pixels(percent_str, dimension):
    if '%' in percent_str:
        return int(dimension * int(percent_str.rstrip('%')) / 100)
    return int(percent_str)

def _unquote_one_line(val):
    if val is None:
        return ''
    v = val.strip()
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        v = v[1:-1]
    return v.strip()

def load_user_prompts(user_env_path=USER_ENV):
    prompts = []
    has_default_prompt = False
    default_prompt_value = None
    full_path = os.path.join(SCRIPT_DIR, user_env_path)
    if not os.path.exists(full_path):
        return prompts, has_default_prompt, default_prompt_value
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
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
    except:
        pass
    return prompts, has_default_prompt, default_prompt_value

def clear_prompts_from_user():
    full_path = os.path.join(SCRIPT_DIR, USER_ENV)
    if not os.path.exists(full_path):
        return
    lines = []
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            old_lines = f.readlines()
        for line in old_lines:
            stripped = line.strip()
            if not stripped.startswith('PROMPT=') and not stripped.startswith('DEFAULT_PROMPT='):
                lines.append(line)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
            log_debug(CAT_FILE, f"Cleared prompts from {full_path}")
    except Exception as e:
        log_debug(CAT_FILE, f"Clear prompts error {full_path}: {e}")

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
            continuation_lines = [m.group(2)]
            i += 1
            while i < len(lines):
                next_line = lines[i].rstrip('\n')
                next_stripped = next_line.strip()
                if next_stripped == '' or next_stripped.startswith('#') or re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*', next_line):
                    break
                continuation_lines.append(next_line)
                i += 1
            raw_value = '\n'.join(continuation_lines)
            raw_value = raw_value.split('#', 1)[0]
            value = re.sub(r'\\\s*\n\s*', ' ', raw_value)
            value = re.sub(r'\s+', ' ', value).strip()
            if len(value) >= 2 and ((value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'"))):
                value = value[1:-1].strip()
            env[key] = value
            log_debug(CAT_FILE, f"Parsed from {path}: {key} = {repr(env[key])}")
    except Exception as e:
        log_debug(CAT_FILE, f"Load env multiline error {full_path}: {e}")
    return env

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
            rest = m.group(2).lstrip()
            value_part = rest.split('#', 1)[0].strip()
            parsed_value = _unquote_one_line(value_part)
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
            parsed_value = _unquote_one_line(value_part)
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

def get_urls_from_input(input_str):
    input_str = input_str.strip()
    if os.path.isfile(input_str):
        try:
            with open(input_str, 'r', encoding='utf-8') as f:
                return [line.split('#', 1)[0].strip() for line in f if line.strip()]
        except:
            pass
    return [u.strip().strip('"\'') for u in re.split(r'[,\s]+', input_str) if u.strip()]

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

class FloatEnvWindow(Gtk.Window):
    def __init__(self, gun):
        super().__init__(title="Float Env Editor")
        self.gun = gun
        self.set_default_size(800, 600)
        self.set_border_width(4)
        notebook = Gtk.Notebook()
        self.add(notebook)
        editor = EnvGxiEditor(gun)
        for i in reversed(range(editor.get_n_pages())):
            page = editor.get_nth_page(i)
            label = editor.get_tab_label(page)
            editor.remove_page(i)
            notebook.append_page(page, label)
        if os.path.exists(FLOAT_ENV_POS):
            try:
                with open(FLOAT_ENV_POS, 'r') as f:
                    x, y, w, h = map(int, f.read().strip().split(','))
                self.move(x, y)
                self.resize(w, h)
            except:
                pass
        self.connect("configure-event", self.on_configure)

    def on_configure(self, widget, event):
        x, y = self.get_position()
        w, h = self.get_size()
        try:
            with open(FLOAT_ENV_POS, 'w') as f:
                f.write(f"{x},{y},{w},{h}")
        except:
            pass
        return False

class FloatGxiWindow(Gtk.Window):
    def __init__(self, gun):
        super().__init__(title="Float GXI Editor")
        self.gun = gun
        self.set_default_size(1000, 700)
        self.set_border_width(4)
        gxi_box = gun.parent_app.editor.create_fancy_gxi_tab()
        self.add(gxi_box)
        if os.path.exists(FLOAT_GXI_POS):
            try:
                with open(FLOAT_GXI_POS, 'r') as f:
                    x, y, w, h = map(int, f.read().strip().split(','))
                self.move(x, y)
                self.resize(w, h)
            except:
                pass
        self.connect("configure-event", self.on_configure)

    def on_configure(self, widget, event):
        x, y = self.get_position()
        w, h = self.get_size()
        try:
            with open(FLOAT_GXI_POS, 'w') as f:
                f.write(f"{x},{y},{w},{h}")
        except:
            pass
        return False

class UnifiedApp(Gtk.Window):
    def __init__(self):
        super().__init__(title="Blitz Talker")
        dedupe_and_prune_startup()
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
        width_str = read_merged_key('PANEL_DEFAULT_WIDTH')
        height_str = read_merged_key('PANEL_DEFAULT_HEIGHT')
        x_off_str = read_merged_key('PANEL_DEFAULT_X_OFFSET')
        y_off_str = read_merged_key('PANEL_DEFAULT_Y_OFFSET')
        log_debug(CAT_GUI, f"Startup geometry read: WIDTH={width_str}, HEIGHT={height_str}, X_OFF={x_off_str}, Y_OFF={y_off_str}")
        try:
            self.saved_width = int(width_str) if width_str else -1
            self.saved_height = int(height_str) if height_str else -1
            self.saved_x_off = int(x_off_str) if x_off_str else 0
            self.saved_y_off = int(y_off_str) if y_off_str else 0
        except (ValueError, TypeError):
            log_debug(CAT_GUI, "Geometry parse error - using defaults")
            self.saved_width = -1
            self.saved_height = -1
            self.saved_x_off = 0
            self.saved_y_off = 0
        if self.saved_width > 0 and self.saved_height > 0:
            log_debug(CAT_GUI, f"Applying saved size: {self.saved_width}x{self.saved_height}")
            self.set_default_size(self.saved_width, self.saved_height)
        self.connect("realize", self.on_realize)
        self.connect("configure-event", self.on_configure)
        GLib.idle_add(lambda: self.paned.set_position(self.paned.get_allocated_width() - 380) or False)

    def on_realize(self, widget):
        try:
            monitor = Gdk.Display.get_default().get_primary_monitor()
            geom = monitor.get_geometry()
            win_w, win_h = self.get_size()
            calculated_y = geom.height - win_h - self.saved_y_off
            log_debug(CAT_GUI, f"Realized - applying position: x_off={self.saved_x_off}, y_off={self.saved_y_off}, calculated_y={calculated_y}, screen_height={geom.height}, win_h={win_h}")
            self.move(self.saved_x_off, calculated_y)
            GLib.timeout_add(300, self.log_actual_geometry, "startup")
            if GLOBAL_DEBUG_MASK & CAT_GEOM:
                message = f"Startup Geometry Report\n\nSaved offsets: x_off={self.saved_x_off}, y_off={self.saved_y_off}\nApplied size: {self.saved_width}x{self.saved_height} (if saved)\nScreen height: {geom.height}\n\nActual geometry logged shortly\nClick OK to continue"
                log_debug(CAT_GEOM, "Showing startup gxmessage")
                subprocess.call(['gxmessage', '-title', 'Startup Geometry', '-center', '-buttons', 'OK:0', message])
        except Exception as e:
            log_debug(CAT_GUI, f"Realize position error: {e}")
        return False

    def log_actual_geometry(self, phase):
        try:
            actual_x, actual_y = self.get_position()
            actual_w, actual_h = self.get_size()
            log_debug(CAT_GUI, f"Actual {phase} GDK geometry: pos ({actual_x},{actual_y}), size {actual_w}x{actual_h}")
            if GLOBAL_DEBUG_MASK & CAT_GEOM:
                try:
                    xid = str(self.get_window().get_xid())
                    result = subprocess.run(['xdotool', 'getwindowgeometry', xid], capture_output=True, text=True)
                    if result.returncode == 0:
                        lines = result.stdout.strip().splitlines()
                        pos_line = [l for l in lines if 'Position' in l][0]
                        geom_line = [l for l in lines if 'Geometry' in l][0]
                        pos = pos_line.split('Position: ')[1].split(' ')[0]
                        size = geom_line.split('Geometry: ')[1]
                        log_debug(CAT_GEOM, f"xdotool parsed: pos {pos}, size {size}")
                except Exception as e:
                    log_debug(CAT_GEOM, f"xdotool cross-check error: {e}")
        except Exception as e:
            log_debug(CAT_GUI, f"Actual geometry log error: {e}")
        return False

    def on_configure(self, widget, event):
        x, y = self.get_position()
        w, h = self.get_size()
        log_debug(CAT_GUI, f"Window moved/resized: pos ({x},{y}), size {w}x{h}")
        return False

class BlitzControl(Gtk.Box):
    def __init__(self, parent_app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.parent_app = parent_app
        self.set_border_width(2)
        self.firing = False
        self.daemon_thread = None
        self.url_input_str = ''
        self.url_list = []
        self.prompt_is_custom = False
        self._loaded_snapshot = {}
        update_env(IMAGINE_ENV, 'FIRE_MODE', 'N')
        list_path = os.path.join(SCRIPT_DIR, read_merged_key('WINDOW_LIST') or 'live_windows.txt')
        if os.path.exists(list_path):
            os.remove(list_path)
        url_box = Gtk.Box(spacing=1)
        self.pack_start(url_box, False, False, 0)
        url_label = Gtk.Label(label="Target URL(s):")
        url_box.pack_start(url_label, False, False, 0)
        self.url_entry = Gtk.Entry()
        self.url_entry.set_hexpand(True)
        merged_url = read_merged_key('DEFAULT_URL') or ''
        self.url_entry.set_text(merged_url)
        url_box.pack_start(self.url_entry, True, True, 0)
        pick_url_btn = Gtk.Button(label="Pick File")
        pick_url_btn.connect("clicked", self.on_pick_url_file)
        url_box.pack_start(pick_url_btn, False, False, 0)
        prompt_box = Gtk.Box(spacing=1)
        self.pack_start(prompt_box, False, False, 0)
        prompt_label = Gtk.Label(label="Prompt(s):")
        prompt_box.pack_start(prompt_label, False, False, 0)
        prompt_scroll = Gtk.ScrolledWindow()
        prompt_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.prompt_view = Gtk.TextView()
        self.prompt_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.prompt_buffer = self.prompt_view.get_buffer()
        prompt_text = '\n'.join(choose_prompts()[0])
        self.prompt_buffer.set_text(prompt_text)
        self.prompt_buffer.connect("changed", lambda b: setattr(self, 'prompt_is_custom', True))
        prompt_scroll.add(self.prompt_view)
        prompt_box.pack_start(prompt_scroll, True, True, 0)
        pick_prompt_btn = Gtk.Button(label="Pick File")
        pick_prompt_btn.connect("clicked", self.on_pick_prompt_file)
        prompt_box.pack_start(pick_prompt_btn, False, False, 0)
        controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)
        self.pack_start(controls_box, False, False, 0)
        rounds_label = Gtk.Label(label="Rounds:")
        controls_box.pack_start(rounds_label, False, False, 0)
        self.fire_spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(value=int(read_merged_key('FIRE_COUNT') or 1), lower=1, upper=999, step_increment=1))
        controls_box.pack_start(self.fire_spin, True, True, 0)
        targets_label = Gtk.Label(label="Targets:")
        controls_box.pack_start(targets_label, False, False, 0)
        self.stage_spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(value=int(read_merged_key('STAGE_COUNT') or 24), lower=1, upper=2000, step_increment=1))
        controls_box.pack_start(self.stage_spin, True, True, 0)
        self.status_label = Gtk.Label(label="Ready")
        self.pack_start(self.status_label, False, False, 0)
        float_box = Gtk.Box(spacing=4)
        self.pack_start(float_box, False, False, 0)
        float_env_btn = Gtk.Button(label="Float Env")
        float_env_btn.connect("clicked", self.on_float_env)
        float_box.pack_start(float_env_btn, False, False, 0)
        float_gxi_btn = Gtk.Button(label="Float GXI")
        float_gxi_btn.connect("clicked", self.on_float_gxi)
        float_box.pack_start(float_gxi_btn, False, False, 0)
        btn_box = Gtk.Box(spacing=1)
        self.pack_start(btn_box, False, False, 0)
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
        self.url_input_str = merged_url
        self.url_list = get_urls_from_input(self.url_input_str)
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
        * { min-width: 0px; min-height: 0px; }
        button { padding: 0px 3px; font-size: 8pt; }
        entry, spinbutton { padding: 0px; font-size: 8pt; }
        label { padding: 0px; font-size: 8pt; }
        textview text { font-size: 8pt; }
        """)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.update_fire_button()
        self.url_entry.connect("changed", self.on_url_changed)
        GLib.idle_add(self.auto_load_current_gxi)

    def on_float_env(self, widget):
        self.save_all()
        FloatEnvWindow(self).show_all()

    def on_float_gxi(self, widget):
        self.save_all()
        FloatGxiWindow(self).show_all()

    def on_url_changed(self, widget):
        self.url_input_str = self.url_entry.get_text().strip()
        self.url_list = get_urls_from_input(self.url_input_str)
        self.parent_app.editor.sync_from_gun()

    def auto_load_current_gxi(self):
        self.parent_app.editor.sync_from_gun()

    def update_fire_button(self):
        self.fire_btn.set_label("STOP" if self.firing else "FIRE")

    def save_all(self):
        url_input_str = self.url_entry.get_text().strip()
        current_prompts = self.prompt_buffer.get_text(self.prompt_buffer.get_start_iter(), self.prompt_buffer.get_end_iter(), False)
        current_fire = str(int(self.fire_spin.get_value()))
        current_stage = str(int(self.stage_spin.get_value()))
        system_values = load_env_multiline(SYSTEM_ENV)
        update_env(IMAGINE_ENV, 'FIRE_MODE', 'Y' if self.firing else 'N')
        if current_fire != (system_values.get('FIRE_COUNT') or ''):
            update_env(IMAGINE_ENV, 'FIRE_COUNT', current_fire)
        if url_input_str != (system_values.get('DEFAULT_URL') or ''):
            update_env(USER_ENV, 'DEFAULT_URL', url_input_str)
        if current_stage != (system_values.get('STAGE_COUNT') or ''):
            update_env(USER_ENV, 'STAGE_COUNT', current_stage)
        if self.prompt_is_custom:
            current_prompt = current_prompts.strip()
            if current_prompt:
                self.write_gxi_prompt(current_prompt)
            clear_prompts_from_user()
            self.prompt_is_custom = False
        prune_user_env()
        prune_imagine_env()
        self._loaded_snapshot.update({
            'URL_INPUT_STR': url_input_str,
            'PROMPTS': current_prompts,
            'FIRE_COUNT': current_fire,
            'STAGE_COUNT': current_stage,
        })
        self.url_input_str = url_input_str
        self.url_list = get_urls_from_input(self.url_input_str)
        self.auto_load_current_gxi()

    def on_pick_url_file(self, widget):
        self.save_all()
        dialog = Gtk.FileChooserDialog(title="Pick URL File", parent=self.parent_app, action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        filter_text = Gtk.FileFilter()
        filter_text.set_name("Text files")
        filter_text.add_mime_type("text/plain")
        dialog.add_filter(filter_text)
        if dialog.run() == Gtk.ResponseType.OK:
            self.url_entry.set_text(dialog.get_filename())
            self.save_all()
            self.auto_load_current_gxi()
        dialog.destroy()

    def on_pick_prompt_file(self, widget):
        self.save_all()
        dialog = Gtk.FileChooserDialog(title="Pick Prompt File", parent=self.parent_app, action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        filter_text = Gtk.FileFilter()
        filter_text.set_name("Text files")
        filter_text.add_mime_type("text/plain")
        dialog.add_filter(filter_text)
        if dialog.run() == Gtk.ResponseType.OK:
            filename = dialog.get_filename()
            text = ''
            try:
                with open(filename, 'r', encoding='utf-8') as f:
                    text = f.read()
            except:
                pass
            self.prompt_buffer.set_text(text)
            self.prompt_is_custom = True
            self.save_all()
            self.auto_load_current_gxi()
        dialog.destroy()

    def on_stage(self, widget):
        self.save_all()
        self.status_label.set_text("Staging windows...")
        stage_delay = float(read_merged_key('STAGE_DELAY') or 2.0)
        grid_start_delay = float(read_merged_key('GRID_START_DELAY') or 5)
        self.gentle_target_op('kill')
        file_path = os.path.join(SCRIPT_DIR, read_merged_key('WINDOW_LIST') or 'live_windows.txt')
        if os.path.exists(file_path):
            os.remove(file_path)
        num = self.stage_spin.get_value_as_int()
        urls = self.url_list
        if not urls:
            self.status_label.set_text("Ready")
            return
        head_flags = load_flags('BROWSER_FLAGS_HEAD')
        middle_flags = load_flags('BROWSER_FLAGS_MIDDLE')
        tail_prefix = get_merged_multiline('BROWSER_FLAGS_TAIL')
        browser = read_merged_key('BROWSER') or 'chromium'
        cmd_base = [browser] + head_flags + middle_flags
        log_debug(CAT_FILE, f"head_flags: {head_flags}")
        log_debug(CAT_FILE, f"middle_flags: {middle_flags}")
        log_debug(CAT_FILE, f"tail_prefix: {repr(tail_prefix)}")
        for i in range(num):
            url = urls[i % len(urls)]
            if tail_prefix:
                cmd = cmd_base[:] + [tail_prefix + url]
            else:
                cmd = cmd_base[:] + [url]
            safe_cmd = ' '.join(shlex.quote(arg) for arg in cmd)
            log_debug(CAT_FILE, f"Launching browser {i+1}/{num} for URL {url}: {safe_cmd}")
            subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
            time.sleep(stage_delay)
        GLib.timeout_add(int(grid_start_delay * 1000), lambda: self.grid_windows(num) or False)

    def on_fire(self, widget=None):
        self.save_all()
        if not self.firing:
            self.firing = True
            update_env(IMAGINE_ENV, 'FIRE_MODE', 'Y')
            sw, sh = 1920, 1080
            try:
                output = subprocess.check_output(['xdotool', 'getdisplaygeometry']).decode().strip()
                sw, sh = map(int, output.split())
            except:
                pass
            target_width = int(read_merged_key('TARGET_WIDTH') or 640)
            target_height = int(read_merged_key('TARGET_HEIGHT') or 500)
            center_x = (sw - target_width) // 2
            center_y = (sh - target_height) // 2
            offset_x = int(read_merged_key('FIRE_STACK_X_OFFSET') or 0)
            offset_y = int(read_merged_key('FIRE_STACK_Y_OFFSET') or 0)
            stack_x = center_x + offset_x
            stack_y = center_y + offset_y
            live_windows_file = os.path.join(SCRIPT_DIR, read_merged_key('WINDOW_LIST') or 'live_windows.txt')
            if os.path.exists(live_windows_file):
                with open(live_windows_file, 'r', encoding='utf-8') as f:
                    window_ids = [line.strip() for line in f if line.strip()]
                for wid in window_ids:
                    xdo_resize_move(wid, target_width, target_height, stack_x, stack_y)
            self.daemon_thread = threading.Thread(target=self.daemon_thread_func, daemon=True)
            self.daemon_thread.start()
            self.update_fire_button()
            self.status_label.set_text("Firing...")
        else:
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
        self.gentle_target_op('kill')
        file_path = os.path.join(SCRIPT_DIR, read_merged_key('WINDOW_LIST') or 'live_windows.txt')
        if os.path.exists(file_path):
            os.remove(file_path)
        prune_user_env()
        prune_imagine_env()
        try:
            width, height = self.parent_app.get_size()
            x, y = self.parent_app.get_position()
            monitor = Gdk.Display.get_default().get_primary_monitor()
            geom = monitor.get_geometry()
            x_off = x
            y_off = geom.height - y - height
            self.parent_app.log_actual_geometry("close")
            if GLOBAL_DEBUG_MASK & CAT_GEOM:
                message = f"Close Geometry Report\n\nGDK: pos ({x},{y}), size {width}x{height}\nCalculated offsets: x_off={x_off}, y_off={y_off}\nScreen height: {geom.height}\n\nSaving to .user_env\nClick OK to close"
                log_debug(CAT_GEOM, "Showing close gxmessage")
                subprocess.call(['gxmessage', '-title', 'Close Geometry', '-center', '-buttons', 'OK:0', message])
            log_debug(CAT_GUI, f"Saving geometry on quit: width={width}, height={height}, x_off={x_off}, y_off={y_off}")
            update_env(USER_ENV, 'PANEL_DEFAULT_WIDTH', str(width))
            update_env(USER_ENV, 'PANEL_DEFAULT_HEIGHT', str(height))
            update_env(USER_ENV, 'PANEL_DEFAULT_X_OFFSET', str(x_off))
            update_env(USER_ENV, 'PANEL_DEFAULT_Y_OFFSET', str(y_off))
        except Exception as e:
            log_debug(CAT_GUI, f"Geometry save error in on_quit: {e}")
        Gtk.main_quit()

    def grid_windows(self, expected_num):
        patterns = [p.strip().strip('"').strip("'").lower() for p in (read_merged_key('WINDOW_PATTERNS') or '').split(',') if p.strip()]
        grid_start_delay = float(read_merged_key('GRID_START_DELAY') or 5)
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
            if len(matched) >= expected_num:
                self._grid_ids(matched)
                self.status_label.set_text("Ready")
                return False
            if stagnant_count >= stagnant_limit:
                if last_matched:
                    self._grid_ids(sorted(last_matched, key=int))
                self.status_label.set_text("Ready")
                return False
            time.sleep(grid_start_delay)
        if last_matched:
            self._grid_ids(sorted(last_matched, key=int))
        self.status_label.set_text("Ready")
        return False

    def _grid_ids(self, ids):
        result = subprocess.run(['xdotool', 'getdisplaygeometry'], capture_output=True, text=True)
        sw, sh = 1920, 1080
        try:
            sw, sh = map(int, result.stdout.strip().split())
        except:
            pass
        target_width = int(read_merged_key('TARGET_WIDTH') or 640)
        target_height = int(read_merged_key('TARGET_HEIGHT') or 500)
        target_overlap = int(read_merged_key('MAX_OVERLAP_PERCENT') or 40)
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
        list_path = os.path.join(SCRIPT_DIR, read_merged_key('WINDOW_LIST') or 'live_windows.txt')
        try:
            with open(list_path, 'w', encoding='utf-8') as f:
                for idx, wid in enumerate(ids):
                    r = idx // cols
                    c = idx % cols
                    x = int(x_start + c * step_x)
                    y = int(y_start + r * step_y)
                    xdo_resize_move(wid, target_width, target_height, x, y)
                    f.write(wid + '\n')
        except:
            pass
        self.gentle_target_op('activate', sync=True)
        self.auto_load_current_gxi()
        if read_merged_key('AUTO_FIRE') in ('1', 'Y', 'true', 'True'):
            time.sleep(5)
            self.on_fire(None)

    def gentle_target_op(self, op_type, sync=True, delay=None):
        delay = float(read_merged_key('TARGET_OP_DELAY') or 0.25)
        file_path = os.path.join(SCRIPT_DIR, read_merged_key('WINDOW_LIST') or 'live_windows.txt')
        if not os.path.exists(file_path):
            if op_type == 'kill':
                subprocess.run(['pkill', '-f', read_merged_key('BROWSER')], check=False)
            return
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                window_ids = [line.strip() for line in f if line.strip()]
        except:
            return
        if not window_ids:
            return
        captured_urls = set()
        if op_type == 'activate':
            gxi_dir = os.path.join(SCRIPT_DIR, read_merged_key('TARGET_DIR') or '.imagine_targets')
            os.makedirs(gxi_dir, exist_ok=True)
            capture_mode = int(read_merged_key('CAPTURE_ENABLED') or '0')
            capture_tool = read_merged_key('CAPTURE_TOOL') or 'maim'
            target_width = int(read_merged_key('TARGET_WIDTH') or 640)
            target_height = int(read_merged_key('TARGET_HEIGHT') or 500)
            capture_x_from_left_str = read_merged_key('CAPTURE_X_FROM_LEFT') or '50%'
            capture_y_from_bottom_str = read_merged_key('CAPTURE_Y_FROM_BOTTOM') or '10%'
            capture_ratio_str = read_merged_key('CAPTURE_RATIO') or '50%'
            capture_left_edge = percent_to_pixels(capture_x_from_left_str, target_width)
            pixels_from_bottom = percent_to_pixels(capture_y_from_bottom_str, target_height)
            ratio = max(0.1, min(1.0, float(capture_ratio_str.rstrip('%')) / 100 if '%' in capture_ratio_str else float(capture_ratio_str)))
            side = int(target_width * ratio)
            rect_y = target_height - pixels_from_bottom - side
            rect_geometry = f"{side}x{side}+{capture_left_edge}+{rect_y}"
        for idx, wid in enumerate(window_ids, start=1):
            if op_type == 'activate':
                subprocess.run(['xdotool', 'windowactivate', '--sync' if sync else '', str(wid)], capture_output=True)
                if capture_mode > 0:
                    cycle_url = self.url_list[(idx - 1) % len(self.url_list)]
                    safe_name = urllib.parse.quote(cycle_url, safe='')
                    capture_path = os.path.join(gxi_dir, f"{safe_name}.png")
                    if cycle_url not in captured_urls:
                        do_capture = capture_mode == 2 or (capture_mode == 1 and not os.path.exists(capture_path))
                        if do_capture:
                            if capture_tool == 'maim':
                                cmd = ['maim', '--hidecursor', '-g', rect_geometry, '-i', str(wid), capture_path]
                            elif capture_tool == 'import':
                                cmd = ['import', '-window', str(wid), '-crop', f"{side}x{side}+{capture_left_edge}+{rect_y}", capture_path]
                            else:
                                cmd = ['maim', '--hidecursor', '-g', rect_geometry, '-i', str(wid), capture_path]
                            result = subprocess.run(cmd, capture_output=True)
                            if result.returncode == 0:
                                log_debug(CAT_DAEMON, f"CAPTURED thumbnail to {capture_path}")
                                GLib.idle_add(self.parent_app.editor.refresh_targets)
                                captured_urls.add(cycle_url)
            elif op_type == 'kill':
                subprocess.run(['wmctrl', '-i', '-c', f"0x{int(wid):08x}"], capture_output=True)
            time.sleep(delay)

    def daemon_thread_func(self):
        total_shots = 0
        fire_count = self.fire_spin.get_value_as_int()
        round_delay = float(read_merged_key('ROUND_DELAY') or 10)
        inter_window_delay = float(read_merged_key('INTER_WINDOW_DELAY') or 0.5)
        shot_delay = float(read_merged_key('SHOT_DELAY') or 0.5)
        single_xdotool = read_merged_key('SINGLE_XDOTOOL') in ('1', 'Y', 'true', 'True')
        prompt = self.prompt_buffer.get_text(self.prompt_buffer.get_start_iter(), self.prompt_buffer.get_end_iter(), False).strip()
        target_width = int(read_merged_key('TARGET_WIDTH') or 640)
        target_height = int(read_merged_key('TARGET_HEIGHT') or 500)
        relative_x = percent_to_pixels(read_merged_key('PROMPT_X_FROM_LEFT') or '50%', target_width)
        relative_y = target_height - percent_to_pixels(read_merged_key('PROMPT_Y_FROM_BOTTOM') or '10%', target_height)
        sw, sh = 1920, 1080
        try:
            output = subprocess.check_output(['xdotool', 'getdisplaygeometry']).decode().strip()
            sw, sh = map(int, output.split())
        except:
            pass
        stack_x = (sw - target_width) // 2 + int(read_merged_key('FIRE_STACK_X_OFFSET') or 0)
        stack_y = (sh - target_height) // 2 + int(read_merged_key('FIRE_STACK_Y_OFFSET') or 0)
        subprocess.run(['xdotool', 'mousemove', str(stack_x + relative_x), str(stack_y + relative_y)])
        def update_status(text):
            GLib.idle_add(lambda: self.status_label.set_text(text) or False)
        update_status("Firing... 0 shots")
        live_windows_file = os.path.join(SCRIPT_DIR, read_merged_key('WINDOW_LIST') or 'live_windows.txt')
        for round_num in range(1, fire_count + 1):
            if round_num > 1:
                time.sleep(round_delay)
            if not os.path.exists(live_windows_file):
                continue
            try:
                with open(live_windows_file, 'r', encoding='utf-8') as f:
                    window_ids = [line.strip() for line in f if line.strip()]
            except:
                continue
            for idx, wid in enumerate(window_ids, start=1):
                if not self.firing:
                    break
                #subprocess.run(['xdotool', 'windowactivate', str(wid)])
                subprocess.run(['xdotool', 'click', '--repeat', '3', '--window', str(wid), '4'])
                subprocess.run(['xdotool', 'click', '--window', str(wid), '1'])
                if prompt not in ('~', '#'):
                    clipboard_set(prompt)
                if single_xdotool:
                    key_cmd = ['xdotool', 'key', '--window', str(wid), '--clearmodifiers']
                    if prompt not in ('~', '#'):
                        key_cmd += ['ctrl+a', 'ctrl+v', 'Return']
                    elif prompt == '~':
                        key_cmd += ['ctrl+a', 'Delete', 'Return']
                    elif prompt == '#':
                        key_cmd += ['Return']
                    subprocess.run(key_cmd)
                else:
                    if prompt not in ('~', '#'):
                        subprocess.run(['xdotool', 'key', '--window', str(wid), '--clearmodifiers', 'ctrl+a'])
                        subprocess.run(['xdotool', 'key', '--window', str(wid), '--clearmodifiers', 'ctrl+v'])
                        subprocess.run(['xdotool', 'key', '--window', str(wid), '--clearmodifiers', 'Return'])
                    elif prompt == '~':
                        subprocess.run(['xdotool', 'key', '--window', str(wid), '--clearmodifiers', 'ctrl+a'])
                        subprocess.run(['xdotool', 'key', '--window', str(wid), '--clearmodifiers', 'Delete'])
                        subprocess.run(['xdotool', 'key', '--window', str(wid), '--clearmodifiers', 'Return'])
                    elif prompt == '#':
                        subprocess.run(['xdotool', 'key', '--window', str(wid), '--clearmodifiers', 'Return'])
                time.sleep(shot_delay)
                self.write_gxi_prompt(prompt)
                total_shots += 1
                time.sleep(inter_window_delay)
                update_status(f"Firing round {round_num}/{fire_count} — {total_shots} shots fired")
        update_status(f"Done — {total_shots} shots fired")
        self.firing = False
        update_env(IMAGINE_ENV, 'FIRE_MODE', 'N')
        GLib.idle_add(self.update_fire_button)
        GLib.timeout_add(5000, lambda: self.status_label.set_text("Ready") or False)
        GLib.idle_add(lambda: self.grid_windows(self.stage_spin.get_value_as_int()) or False)

    def write_gxi_prompt(self, active_prompt):
        if not active_prompt:
            return
        urls = self.url_list
        if not urls:
            return
        unique_urls = set(urls)
        if not unique_urls:
            return
        gxi_dir = os.path.join(SCRIPT_DIR, read_merged_key('TARGET_DIR') or '.imagine_targets')
        os.makedirs(gxi_dir, exist_ok=True)
        stage_marker = "STAGE_1"
        history_marker = ".history_1"
        append_line = active_prompt
        for url in unique_urls:
            safe_name = urllib.parse.quote(url, safe='') + '.gxi'
            gxi_path = os.path.join(gxi_dir, safe_name)
            lines = []
            stage_found = False
            history_index = None
            try:
                with open(gxi_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                for raw_line in content.splitlines():
                    line = raw_line.rstrip('\n')
                    lines.append(line)
                    stripped = line.strip()
                    if stripped == stage_marker:
                        stage_found = True
                    if stage_found and stripped.startswith('@'):
                        lines[-1] = stripped[1:]
                    if stripped == history_marker:
                        history_index = len(lines) - 1
            except:
                pass
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
            except:
                pass

class EnvGxiEditor(Gtk.Notebook):
    def __init__(self, gun):
        super().__init__()
        self.gun = gun
        self.append_page(self.create_dynamic_env_tab("System", SYSTEM_ENV), Gtk.Label(label="System"))
        self.append_page(self.create_dynamic_env_tab("User", USER_ENV), Gtk.Label(label="User"))
        self.append_page(self.create_dynamic_env_tab("Imagine", IMAGINE_ENV), Gtk.Label(label="Imagine"))
        self.append_page(self.create_all_dynamic_env_tab(), Gtk.Label(label="All Env"))
        self.append_page(self.create_fancy_gxi_tab(), Gtk.Label(label=".gxi Targets"))

    def create_dynamic_env_tab(self, name, path):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(8)
        scrolled.add(box)
        full_path = os.path.join(SCRIPT_DIR, path)
        if not os.path.exists(full_path):
            return scrolled
        lines = []
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except:
            return scrolled
        current_section = "General"
        frame = Gtk.Frame(label=current_section)
        box.pack_start(frame, False, False, 0)
        inner_grid = Gtk.Grid()
        inner_grid.set_column_spacing(12)
        inner_grid.set_row_spacing(6)
        inner_grid.set_border_width(8)
        frame.add(inner_grid)
        row = 0
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('=='):
                current_section = stripped.strip('=').strip()
                frame = Gtk.Frame(label=current_section)
                box.pack_start(frame, False, False, 0)
                inner_grid = Gtk.Grid()
                inner_grid.set_column_spacing(12)
                inner_grid.set_row_spacing(6)
                inner_grid.set_border_width(8)
                frame.add(inner_grid)
                row = 0
                continue
            if not stripped or stripped.startswith('#'):
                continue
            if '=' not in line:
                continue
            parts = line.split('=', 1)
            key = parts[0].strip()
            rest = parts[1]
            value = rest.split('#', 1)[0].strip().strip('"\'')
            comment = ''
            type_hint = 'str'
            if '#' in rest:
                comment_parts = rest.split('#')[1:]
                for p in comment_parts:
                    p_strip = p.strip()
                    if p_strip.startswith('type:'):
                        type_hint = p_strip[5:].strip()
                    else:
                        comment += p_strip + ' '
            label = Gtk.Label(label=f"{key}:")
            label.set_xalign(0)
            if comment:
                label.set_tooltip_text(comment)
            widget = self.make_dynamic_widget(type_hint, value)
            inner_grid.attach(label, 0, row, 1, 1)
            inner_grid.attach(widget, 1, row, 1, 1)
            row += 1
        return scrolled

    def make_dynamic_widget(self, type_hint, value):
        if type_hint == 'bool':
            widget = Gtk.CheckButton()
            widget.set_active(value in ('1', 'Y', 'true', 'True'))
            return widget
        if type_hint in ('int', 'float'):
            adj = Gtk.Adjustment(value=(int(value) if type_hint == 'int' else float(value or 0)), lower=0, upper=99999, step_increment=1)
            widget = Gtk.SpinButton(adjustment=adj, digits=(0 if type_hint == 'int' else 2))
            return widget
        if type_hint == 'multi':
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            items = [i.strip() for i in value.split(',') if i.strip()]
            for item in items:
                check = Gtk.CheckButton(label=item)
                check.set_active(True)
                vbox.pack_start(check, False, False, 0)
            return vbox
        if type_hint == 'multiline':
            view = Gtk.TextView()
            view.set_wrap_mode(Gtk.WrapMode.WORD)
            view.get_buffer().set_text(value)
            return view
        widget = Gtk.Entry()
        widget.set_text(value)
        return widget

    def create_all_dynamic_env_tab(self):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(8)
        scrolled.add(box)
        system_values = load_env_multiline(SYSTEM_ENV)
        merged = system_values.copy()
        merged.update(load_env_multiline(IMAGINE_ENV))
        merged.update(load_env_multiline(USER_ENV))
        current_section = "General"
        frame = Gtk.Frame(label=current_section)
        box.pack_start(frame, False, False, 0)
        inner_grid = Gtk.Grid()
        inner_grid.set_column_spacing(12)
        inner_grid.set_row_spacing(6)
        inner_grid.set_border_width(8)
        frame.add(inner_grid)
        row = 0
        lines = []
        try:
            with open(os.path.join(SCRIPT_DIR, SYSTEM_ENV), 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except:
            lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('=='):
                current_section = stripped.strip('=').strip()
                frame = Gtk.Frame(label=current_section)
                box.pack_start(frame, False, False, 0)
                inner_grid = Gtk.Grid()
                inner_grid.set_column_spacing(12)
                inner_grid.set_row_spacing(6)
                inner_grid.set_border_width(8)
                frame.add(inner_grid)
                row = 0
                continue
            if not stripped or stripped.startswith('#'):
                continue
            if '=' not in line:
                continue
            parts = line.split('=', 1)
            key = parts[0].strip()
            rest = parts[1]
            value = rest.split('#', 1)[0].strip().strip('"\'')
            comment = ''
            type_hint = 'str'
            if '#' in rest:
                comment_parts = rest.split('#')[1:]
                for p in comment_parts:
                    p_strip = p.strip()
                    if p_strip.startswith('type:'):
                        type_hint = p_strip[5:].strip()
                    else:
                        comment += p_strip + ' '
            sys_val = system_values.get(key, '')
            merged_val = merged.get(key, sys_val)
            label = Gtk.Label(label=f"{key}:")
            label.set_xalign(0)
            if comment:
                label.set_tooltip_text(comment)
            if merged_val != sys_val:
                label.get_style_context().add_class("override")
            widget = self.make_dynamic_widget(type_hint, merged_val)
            inner_grid.attach(label, 0, row, 1, 1)
            inner_grid.attach(widget, 1, row, 1, 1)
            row += 1
        save_btn = Gtk.Button(label="Save All Env (deltas only)")
        save_btn.connect("clicked", lambda w: self.gun.save_all())
        box.pack_start(save_btn, False, False, 0)
        return scrolled

    def create_fancy_gxi_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        controls_box = Gtk.Box(spacing=1)
        box.pack_start(controls_box, False, False, 0)
        refresh_btn = Gtk.Button(label="Refresh Gallery")
        refresh_btn.connect("clicked", self.refresh_targets)
        controls_box.pack_start(refresh_btn, False, False, 0)
        send_to_gun_btn = Gtk.Button(label="→ Gun")
        send_to_gun_btn.connect("clicked", self.send_url_to_gun)
        controls_box.pack_start(send_to_gun_btn, False, False, 0)
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
        desc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        box.pack_start(desc_box, True, True, 0)
        desc_label = Gtk.Label(label="TARGET_DESC:")
        desc_box.pack_start(desc_label, False, False, 0)
        desc_scroll = Gtk.ScrolledWindow()
        desc_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        desc_scroll.set_vexpand(True)
        desc_scroll.set_min_content_height(100)
        self.desc_view = Gtk.TextView()
        self.desc_view.set_wrap_mode(Gtk.WrapMode.WORD)
        desc_scroll.add(self.desc_view)
        desc_box.pack_start(desc_scroll, True, True, 0)
        self.stages_notebook = Gtk.Notebook()
        box.pack_start(self.stages_notebook, True, True, 0)
        self.fixed_stage_boxes = {}
        self.fixed_stage_checks = {}
        self.fixed_stage_entries = {}
        for stage in ['STAGE_1', 'STAGE_2', 'STAGE_3']:
            tab_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            tab_box.set_border_width(8)
            self.fixed_stage_boxes[stage] = []
            self.fixed_stage_checks[stage] = []
            self.fixed_stage_entries[stage] = []
            for i in range(5):
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
                check = Gtk.CheckButton()
                entry = Gtk.Entry()
                entry.set_hexpand(True)
                row.pack_start(check, False, False, 0)
                row.pack_start(entry, True, True, 0)
                tab_box.pack_start(row, False, False, 0)
                self.fixed_stage_checks[stage].append(check)
                self.fixed_stage_entries[stage].append(entry)
                self.fixed_stage_boxes[stage].append(row)
            self.stages_notebook.append_page(tab_box, Gtk.Label(label=stage))
        u_tab = self.create_dynamic_stage_tab('STAGE_U')
        self.stages_notebook.append_page(u_tab, Gtk.Label(label='STAGE_U'))
        btn_box = Gtk.Box(spacing=1)
        box.pack_start(btn_box, False, False, 0)
        save_btn = Gtk.Button(label="Save .gxi")
        save_btn.connect("clicked", self.save_current_gxi)
        btn_box.pack_start(save_btn, False, False, 0)
        self.current_gxi_path = None
        self.current_histories = {}
        self.refresh_targets()
        self.sync_from_gun()
        return box

    def create_dynamic_stage_tab(self, stage_name):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        self.u_listbox = Gtk.ListBox()
        scrolled.add(self.u_listbox)
        box.pack_start(scrolled, True, True, 0)
        btn_box = Gtk.Box(spacing=1)
        box.pack_start(btn_box, False, False, 0)
        add_btn = Gtk.Button(label="Add Prompt")
        add_btn.connect("clicked", lambda w: self.add_u_prompt())
        delete_btn = Gtk.Button(label="Delete Selected")
        delete_btn.connect("clicked", lambda w: self.delete_u_selected())
        btn_box.pack_start(add_btn, False, False, 0)
        btn_box.pack_start(delete_btn, False, False, 0)
        return box

    def add_u_prompt(self, prompt='', make_active=False):
        row = Gtk.ListBoxRow()
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        check = Gtk.CheckButton()
        entry = Gtk.Entry()
        entry.set_text(prompt)
        entry.set_hexpand(True)
        hbox.pack_start(check, False, False, 0)
        hbox.pack_start(entry, True, True, 0)
        row.add(hbox)
        row.check = check
        row.entry = entry
        self.u_listbox.add(row)
        if make_active:
            check.set_active(True)
        self.u_listbox.show_all()

    def delete_u_selected(self):
        selected = self.u_listbox.get_selected_row()
        if selected:
            self.u_listbox.remove(selected)

    def send_url_to_gun(self, widget):
        if not self.current_gxi_path:
            return
        url = urllib.parse.unquote(os.path.basename(self.current_gxi_path)[:-4])
        self.gun.url_entry.set_text(url)
        self.gun.save_all()

    def on_gallery_item_activated(self, flowbox, child):
        url = child.url
        self.load_gxi_by_url(url)

    def refresh_targets(self, widget=None):
        for child in self.gallery.get_children():
            self.gallery.remove(child)
        gxi_dir = os.path.join(SCRIPT_DIR, read_merged_key('TARGET_DIR') or '.imagine_targets')
        if not os.path.exists(gxi_dir):
            return
        placeholder = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 128, 128)
        placeholder.fill(0x808080ff)
        files = []
        try:
            files = sorted(os.listdir(gxi_dir))
        except:
            pass
        for filename in files:
            if filename.endswith('.gxi'):
                decoded = urllib.parse.unquote(filename[:-4])
                uuid = decoded.split('/')[-1] if '/' in decoded else 'unknown'
                thumb = placeholder
                for img_file in files:
                    if uuid in img_file and img_file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                        img_path = os.path.join(gxi_dir, img_file)
                        try:
                            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(img_path, 128, 128, True)
                            thumb = pixbuf
                            break
                        except:
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
        gxi_dir = os.path.join(SCRIPT_DIR, read_merged_key('TARGET_DIR') or '.imagine_targets')
        path = os.path.join(gxi_dir, safe_name)
        if not os.path.exists(path):
            return
        self.current_gxi_path = path
        self.current_histories = {}
        for stage in ['STAGE_1', 'STAGE_2', 'STAGE_3']:
            for i in range(5):
                self.fixed_stage_checks[stage][i].set_active(False)
                self.fixed_stage_entries[stage][i].set_text('')
        for row in self.u_listbox.get_children():
            self.u_listbox.remove(row)
        content = ''
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
        except:
            pass
        current_stage = None
        in_history = False
        current_prompts = {}
        history_prompts = {}
        for line in content.splitlines():
            stripped = line.strip()
            if stripped in ['STAGE_U', 'STAGE_1', 'STAGE_2', 'STAGE_3']:
                current_stage = stripped
                in_history = False
            elif stripped.startswith('.history_'):
                in_history = True
            elif stripped:
                if current_stage:
                    if in_history:
                        history_prompts.setdefault(current_stage, []).append(line.rstrip('\n'))
                    else:
                        current_prompts.setdefault(current_stage, []).append(line.rstrip('\n'))
                else:
                    current_prompts.setdefault('STAGE_U', []).append(line.rstrip('\n'))
        self.current_histories = history_prompts
        at_count = 0
        active_prompt = None
        for stage in ['STAGE_1', 'STAGE_2', 'STAGE_3']:
            prompts = current_prompts.get(stage, [])
            for i in range(5):
                if i < len(prompts):
                    p_line = prompts[i]
                    is_active = p_line.startswith('@')
                    prompt = p_line.lstrip('@')
                    self.fixed_stage_entries[stage][i].set_text(prompt)
                    if is_active:
                        self.fixed_stage_checks[stage][i].set_active(True)
                        at_count += 1
                        active_prompt = prompt
                else:
                    self.fixed_stage_entries[stage][i].set_text('')
            excess = prompts[5:]
            self.current_histories.setdefault(stage, []).extend(excess)
        u_prompts = current_prompts.get('STAGE_U', [])
        for p_line in u_prompts:
            is_active = p_line.startswith('@')
            prompt = p_line.lstrip('@')
            self.add_u_prompt(prompt, is_active)
            if is_active:
                at_count += 1
                active_prompt = prompt
        if at_count > 1:
            for stage in ['STAGE_1', 'STAGE_2', 'STAGE_3']:
                for check in self.fixed_stage_checks[stage]:
                    check.set_active(False)
            for row in self.u_listbox.get_children():
                row.check.set_active(False)
        if at_count == 1 and active_prompt:
            self.gun.prompt_buffer.set_text(active_prompt)
            self.gun.prompt_is_custom = True

    def save_current_gxi(self, widget):
        if not self.current_gxi_path:
            return
        desc_buffer = self.desc_view.get_buffer()
        desc = desc_buffer.get_text(desc_buffer.get_start_iter(), desc_buffer.get_end_iter(), False)
        active_prompt = None
        stage_prompts = {}
        for stage in ['STAGE_1', 'STAGE_2', 'STAGE_3']:
            prompts = []
            for i in range(5):
                text = self.fixed_stage_entries[stage][i].get_text().strip()
                if text:
                    is_active = self.fixed_stage_checks[stage][i].get_active()
                    if is_active:
                        active_prompt = text
                    prompts.append(f'@{text}' if is_active else text)
            stage_prompts[stage] = prompts
        u_prompts = []
        for row in self.u_listbox.get_children():
            text = row.entry.get_text().strip()
            if text:
                is_active = row.check.get_active()
                if is_active:
                    active_prompt = text
                u_prompts.append(f'@{text}' if is_active else text)
        stage_prompts['STAGE_U'] = u_prompts
        lines = [f'TARGET_DESC={desc}\n', '\n']
        for stage in ['STAGE_U', 'STAGE_1', 'STAGE_2', 'STAGE_3']:
            lines.append(f'{stage}\n')
            for p in stage_prompts.get(stage, []):
                lines.append(f'{p}\n')
            history_marker = '.history_U' if stage == 'STAGE_U' else f".history_{stage[-1]}"
            lines.append(f'{history_marker}\n')
            for p in self.current_histories.get(stage, []):
                lines.append(f'{p}\n')
            lines.append('\n')
        try:
            with open(self.current_gxi_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
        except:
            pass
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
    app = UnifiedApp()
    app.show_all()
    Gtk.main()
