#!/usr/bin/env python3
import subprocess
import os
import re
import shlex  # NEW: for precise flag splitting (preserves your deliberate spaces/no-spaces)
import time
import threading
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib

HOME = os.path.expanduser('~')
USER_ENV = '.user_env'
IMAGINE_ENV = '.imagine_env'
SYSTEM_ENV = '.system_env'

REQUIRED_SYSTEM_VARS = [
    'BROWSER',
    'WINDOW_PATTERNS',
    'DEFAULT_WIDTH',
    'DEFAULT_HEIGHT',
    'MAX_OVERLAP_PERCENT'
]

def load_env(file, defaults=None):
    env = {} if defaults is None else defaults.copy()
    if os.path.exists(file):
        with open(file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                k = k.strip()
                v = v.strip()
                if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                    v = v[1:-1]
                v = v.strip()
                env[k] = v
    return env

def load_flags(key):
    if not os.path.exists(SYSTEM_ENV):
        return []
    with open(SYSTEM_ENV, 'r') as f:
        content = f.read()
    pattern = rf'(?s){re.escape(key)}\s*=\s*["\']\s*(.*?)\s*["\']'
    match = re.search(pattern, content)
    if match:
        val = match.group(1)
        val = re.sub(r'\\\s*$', '', val)  # NEW: strip trailing backslash (prevents dangling escape error)
        val = re.sub(r'\\\s*\n\s*', ' ', val)  # NEW: handle proper continuations
        return shlex.split(val)  # NEW: clean multiline (no \) works too — shlex treats \n as space
    return []

def update_env(file, key, value):
    lines = []
    found = False
    if os.path.exists(file):
        with open(file, 'r') as f:
            for line in f:
                if line.strip().startswith(f'{key}='):
                    lines.append(f'{key}="{value}"\n')
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f'{key}="{value}"\n')
    with open(file, 'w') as f:
        f.writelines(lines)

def get_urls_from_input(input_str):
    urls = []
    input_str = input_str.strip()
    if os.path.isfile(input_str):
        with open(input_str, 'r') as f:
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
        with open(input_str, 'r') as f:
            for line in f:
                line = line.split('#', 1)[0].strip()
                if line:
                    prompts.append(line)
    else:
        if input_str:  # NEW: non-file = single prompt, commas safe (no split)
            prompts = [input_str]
        else:
            prompts = ['system is king']
    return prompts

def check_required_system_vars(system):
    missing = [k for k in REQUIRED_SYSTEM_VARS if k not in system or system[k] == ""]
    return missing

SYSTEM = load_env(SYSTEM_ENV, {})
missing = check_required_system_vars(SYSTEM)
if missing:
    print("\n[ERROR] Missing required system variables in .system_env:")
    for m in missing:
        print(f" - {m}")
    print("\nOpening .system_env in editor (kwrite) and exiting immediately.")
    try:
        subprocess.Popen(['kwrite', SYSTEM_ENV])
    except Exception as e:
        print(f"[ERROR] Failed to launch editor 'kwrite': {e}")
    os._exit(1)

FLAGS_HEAD = load_flags('BROWSER_FLAGS_HEAD')
FLAGS_MIDDLE = load_flags('BROWSER_FLAGS_MIDDLE')
FLAGS_TAIL = load_flags('BROWSER_FLAGS_TAIL')

class BlitzControl(Gtk.Window):
    def __init__(self):
        panel_title = SYSTEM.get('PANEL_DEFAULT_TITLE', '.Blitz Talker.')
        super().__init__(title=panel_title)
        self.set_keep_above(True)
        self.set_border_width(12)

        self.system = SYSTEM
        self.flags_head = FLAGS_HEAD
        self.flags_middle = FLAGS_MIDDLE
        self.flags_tail = FLAGS_TAIL
        self.live_windows = self.system.get('WINDOW_LIST', 'live_windows.txt')  # NEW: configurable window list file

        self.user = load_env(USER_ENV, {
            'DEFAULT_URL': self.system.get('DEFAULT_URL', ''),
            'STAGE_COUNT': '24'
        })
        # NEW: collect multiple PROMPT= from .user_env
        collected_prompts = []
        temp_lines = []
        if os.path.exists(USER_ENV):
            with open(USER_ENV, 'r') as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith('PROMPT='):
                        v = stripped.split('=', 1)[1].strip()
                        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                            v = v[1:-1]
                        collected_prompts.append(v)
                    else:
                        temp_lines.append(line)
        self.collected_prompts = collected_prompts

        if collected_prompts:
            if len(collected_prompts) > 1:
                self.prompt_entry_text = f"Multiple prompts ({len(collected_prompts)}) in .user_env"
            else:
                self.prompt_entry_text = collected_prompts[0]
        else:
            self.prompt_entry_text = ''

        update_env(IMAGINE_ENV, 'FIRE_MODE', 'N')
        self.imagine = load_env(IMAGINE_ENV, {
            'BURST_COUNT': self.system.get('BURST_COUNT', '1'),
            'FIRE_COUNT': self.system.get('FIRE_COUNT', '1'),
            'FIRE_MODE': 'N'
        })

        panel_w = int(self.system.get('PANEL_DEFAULT_WIDTH'))
        panel_h = int(self.system.get('PANEL_DEFAULT_HEIGHT'))
        x_offset = int(self.system.get('PANEL_DEFAULT_X_OFFSET'))
        y_offset = int(self.system.get('PANEL_DEFAULT_Y_OFFSET'))
        self.set_default_size(panel_w, panel_h)

        try:
            output = subprocess.check_output(['xdotool', 'getdisplaygeometry']).decode().strip()
            sw, sh = map(int, output.split())
        except Exception:
            sw, sh = 1920, 1080

        pos_x = sw - panel_w - x_offset
        pos_y = sh - panel_h - y_offset
        if pos_x < 0: pos_x = 0
        if pos_y < 0: pos_y = 0
        self.move(pos_x, pos_y)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add(box)

        url_box = Gtk.Box(spacing=6)
        box.pack_start(url_box, False, False, 0)
        url_label = Gtk.Label(label="Target URL(s):")
        url_label.set_size_request(120, -1)
        url_box.pack_start(url_label, False, False, 0)
        self.url_entry = Gtk.Entry()
        self.url_entry.set_hexpand(True)
        self.url_entry.set_text(self.user.get('DEFAULT_URL'))
        self.url_entry.connect("changed", lambda w: self.save_user())
        url_box.pack_start(self.url_entry, True, True, 0)
        pick_url_btn = Gtk.Button(label="Pick File")
        pick_url_btn.connect("clicked", self.on_pick_url_file)
        url_box.pack_start(pick_url_btn, False, False, 0)

        prompt_box = Gtk.Box(spacing=6)
        box.pack_start(prompt_box, False, False, 0)
        prompt_label = Gtk.Label(label="Prompt(s):")
        prompt_label.set_size_request(120, -1)
        prompt_box.pack_start(prompt_label, False, False, 0)
        self.prompt_entry = Gtk.Entry()
        self.prompt_entry.set_hexpand(True)
        self.prompt_entry.set_text(self.prompt_entry_text)
        self.prompt_entry.connect("changed", lambda w: self.save_user())
        prompt_box.pack_start(self.prompt_entry, True, True, 0)
        pick_prompt_btn = Gtk.Button(label="Pick File")
        pick_prompt_btn.connect("clicked", self.on_pick_prompt_file)
        prompt_box.pack_start(pick_prompt_btn, False, False, 0)

        # Horizontal row for the three spin controls
        controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=30)
        box.pack_start(controls_box, False, False, 0)

        # Burst
        burst_hbox = Gtk.Box(spacing=6)
        burst_label = Gtk.Label(label="Burst:")
        burst_hbox.pack_start(burst_label, False, False, 0)
        burst_adj = Gtk.Adjustment(value=int(self.imagine.get('BURST_COUNT', 1)), lower=1, upper=20, step_increment=1)
        self.burst_spin = Gtk.SpinButton(adjustment=burst_adj)
        self.burst_spin.connect("value-changed", lambda w: self.save_imagine())
        burst_hbox.pack_start(self.burst_spin, True, True, 0)
        controls_box.pack_start(burst_hbox, True, True, 0)

        # Rounds
        fire_hbox = Gtk.Box(spacing=6)
        fire_label = Gtk.Label(label="Rounds:")
        fire_hbox.pack_start(fire_label, False, False, 0)
        fire_adj = Gtk.Adjustment(value=int(self.imagine.get('FIRE_COUNT', 1)), lower=1, upper=99, step_increment=1)
        self.fire_spin = Gtk.SpinButton(adjustment=fire_adj)
        self.fire_spin.connect("value-changed", lambda w: self.save_imagine())
        fire_hbox.pack_start(self.fire_spin, True, True, 0)
        controls_box.pack_start(fire_hbox, True, True, 0)

        # Targets
        stage_hbox = Gtk.Box(spacing=6)
        stage_label = Gtk.Label(label="Targets:")
        stage_hbox.pack_start(stage_label, False, False, 0)
        stage_adj = Gtk.Adjustment(value=int(self.user.get('STAGE_COUNT', 24)), lower=1, upper=200, step_increment=1)
        self.stage_spin = Gtk.SpinButton(adjustment=stage_adj)
        self.stage_spin.connect("value-changed", lambda w: self.save_user())
        stage_hbox.pack_start(self.stage_spin, True, True, 0)
        controls_box.pack_start(stage_hbox, True, True, 0)

        self.status_label = Gtk.Label(label="Ready")
        box.pack_start(self.status_label, False, False, 0)

        btn_box = Gtk.Box(spacing=8)
        box.pack_start(btn_box, False, False, 0)

        stage_btn = Gtk.Button(label="STAGE")
        stage_btn.connect("clicked", self.on_stage)
        btn_box.pack_start(stage_btn, True, True, 0)

        self.fire_btn = Gtk.Button(label="FIRE" if self.imagine.get('FIRE_MODE', 'N') == 'N' else "STOP")
        self.fire_btn.connect("clicked", self.on_fire)
        btn_box.pack_start(self.fire_btn, True, True, 0)

        edit_btn = Gtk.Button(label="EDIT")
        edit_btn.connect("clicked", self.on_edit)
        btn_box.pack_start(edit_btn, True, True, 0)

        quit_btn = Gtk.Button(label="QUIT")
        quit_btn.connect("clicked", self.on_quit)
        btn_box.pack_start(quit_btn, True, True, 0)

        self.daemon_thread = None
        GLib.timeout_add_seconds(1, self.poll_fire_mode)

    def add_entry(self, box, label, default):
        hbox = Gtk.Box(spacing=6)
        box.pack_start(hbox, False, False, 0)
        hbox.pack_start(Gtk.Label(label=label), False, False, 0)
        entry = Gtk.Entry()
        entry.set_text(default or '')
        entry.connect("changed", lambda w: self.save_user())
        hbox.pack_start(entry, True, True, 0)
        return entry

    def add_spin(self, box, label, default, minv, maxv):
        hbox = Gtk.Box(spacing=6)
        box.pack_start(hbox, False, False, 0)
        hbox.pack_start(Gtk.Label(label=label), False, False, 0)
        adj = Gtk.Adjustment(value=default, lower=minv, upper=maxv, step_increment=1)
        spin = Gtk.SpinButton(adjustment=adj)
        spin.connect("value-changed", lambda w: self.save_user() if 'windows' in label.lower() else self.save_imagine())
        hbox.pack_start(spin, True, True, 0)
        return spin

    def save_user(self):
        # NEW: special handling for PROMPT to preserve manual multiple lines when not changed
        prompt_text = self.prompt_entry.get_text().strip()
        if not prompt_text.startswith("Multiple prompts"):
            # clear old PROMPT lines, add new
            lines = []
            if os.path.exists(USER_ENV):
                with open(USER_ENV, 'r') as f:
                    for line in f:
                        if not line.strip().startswith('PROMPT='):
                            lines.append(line)
            if prompt_text:
                lines.append(f'PROMPT="{prompt_text}"\n')
            with open(USER_ENV, 'w') as f:
                f.writelines(lines)

        update_env(USER_ENV, 'DEFAULT_URL', self.url_entry.get_text())
        update_env(USER_ENV, 'STAGE_COUNT', str(int(self.stage_spin.get_value())))

    def save_imagine(self):
        update_env(IMAGINE_ENV, 'BURST_COUNT', str(int(self.burst_spin.get_value())))
        update_env(IMAGINE_ENV, 'FIRE_COUNT', str(int(self.fire_spin.get_value())))

    def on_pick_url_file(self, widget):
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
            self.save_user()
        dialog.destroy()

    def on_pick_prompt_file(self, widget):
        dialog = Gtk.FileChooserDialog(title="Pick Prompt File", parent=self, action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        filter_text = Gtk.FileFilter()
        filter_text.set_name("Text files")
        filter_text.add_mime_type("text/plain")
        dialog.add_filter(filter_text)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            filename = dialog.get_filename()
            self.prompt_entry.set_text(filename)
            self.save_user()
        dialog.destroy()

    def on_stage(self, widget):
        num = int(self.stage_spin.get_value())
        url_input = self.url_entry.get_text().strip()
        urls = get_urls_from_input(url_input)
        if not urls:
            print("[STAGE] No URLs provided; aborting stage.")
            return

        browser = self.system.get('BROWSER')
        cmd_base = [browser] + self.flags_head + self.flags_middle + self.flags_tail


        print(f"[STAGE] Launching {num} windows...")
        for i in range(num):
            url = urls[i % len(urls)]
            cmd = cmd_base + [url]
            if self.flags_tail:
                cmd[-2] = cmd[-2] + cmd[-1]
                cmd.pop()
            print(f"[STAGE] Launching: {' '.join(cmd)}")
            try:
                subprocess.Popen(cmd)
            except Exception as e:
                print(f"[STAGE] Failed to launch: {e}")
        	stage_delay = float(self.system.get('STAGE_DELAY', 3.0))
            GLib.timeout_add(int(stage_delay * 1000), lambda: None or False)

            time.sleep(stage_delay)

        grid_start_delay = int(self.system.get('GRID_START_DELAY'))
        print(f"[STAGE] Waiting {grid_start_delay}s before gridding")
        GLib.timeout_add_seconds(grid_start_delay, lambda: self.grid_windows(num) or False)

    def grid_windows(self, expected_num):
        patterns_raw = self.system.get('WINDOW_PATTERNS', 'Imagine - Grok')
        patterns = [p.strip().strip('"').strip("'").lower() for p in patterns_raw.split(',') if p.strip()]
        max_tries = 30

        last_total_windows = -1
        stagnant_limit = 3
        stagnant_count = 0
        last_matched = []
        print(f"[GRID] Waiting for up to {expected_num} windows matching any of {patterns}")
        for attempt in range(1, max_tries + 1):
            print(f"\n[GRID] Attempt {attempt}/{max_tries}")

            try:
                all_ids = subprocess.check_output(
                    ['xdotool', 'search', '--onlyvisible', '.']
                ).decode().strip().splitlines()
            except:
                all_ids = []

            print(f"[GRID] Visible windows: {len(all_ids)}")

            if len(all_ids) == last_total_windows:
                stagnant_count += 1
            else:
                stagnant_count = 0
            last_total_windows = len(all_ids)

            matched = []
            for wid in all_ids:
                try:
                    name = subprocess.check_output(
                        ['xdotool', 'getwindowname', wid],
                        stderr=subprocess.DEVNULL
                    ).decode().strip().lower()
                except:
                    continue
                if any(p in name for p in patterns):
                    matched.append(wid)

            print(f"[GRID] Matching windows: {len(matched)}")

            if matched:
                last_matched = matched[:]

            if len(matched) >= expected_num:
                print("[GRID] Enough matches found. Gridding.")
                self._grid_ids(matched)
                return False

            if stagnant_count >= stagnant_limit:
                if last_matched:
                    print("[GRID] Stagnant but matches exist. Gridding what we have.")
                    self._grid_ids(last_matched)
                else:
                    print("[GRID] Stagnant, no matches. Giving up.")
                return False

        	sleep_between = float(self.system.get('GRID_START_DELAY', 10.0))
            GLib.timeout_add(int(sleep_between * 1000), lambda: None or False)

        if last_matched:
            print("[GRID] Max tries reached. Gridding last matches.")
            self._grid_ids(last_matched)
        else:
            print("[GRID] Max tries reached, no matches.")
        return False

    def _grid_ids(self, ids):
        width = int(self.system.get('DEFAULT_WIDTH'))
        height = int(self.system.get('DEFAULT_HEIGHT'))
        max_overlap_pct = int(self.system.get('MAX_OVERLAP_PERCENT'))

        try:
            screen = subprocess.check_output(['xdotool', 'getdisplaygeometry']).decode().strip().split()
            sw, sh = int(screen[0]), int(screen[1])
        except:
            sw, sh = 1920, 1080

        margin = 20
        available_width = sw - 2 * margin
        available_height = sh - 2 * margin

        n = len(ids)
        if n == 0:
            return

        effective_step = max(1, int(width * (100 - max_overlap_pct) / 100))
        max_cols_by_width = 1 + (available_width - width) // effective_step if available_width >= width else 1

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
            total_grid_width = width
        else:
            min_total_width = width + (cols - 1) * effective_step
            if min_total_width <= available_width:
                extra_space = available_width - width
                step_x = extra_space // (cols - 1)
                if step_x < effective_step:
                    step_x = effective_step
            else:
                step_x = max(1, (available_width - width) // (cols - 1))
            total_grid_width = width + (cols - 1) * step_x

        vertical_effective_step = max(1, int(height * (100 - max_overlap_pct) / 100))
        if rows == 1:
            step_y = 0
        else:
            min_total_height = height + (rows - 1) * vertical_effective_step
            if min_total_height <= available_height:
                extra_vspace = available_height - height
                step_y = extra_vspace // (rows - 1)
                if step_y < vertical_effective_step:
                    step_y = vertical_effective_step
            else:
                step_y = max(1, (available_height - height) // (rows - 1))

        x_start = margin + max(0, (available_width - total_grid_width) // 2)
        y_start = margin + max(0, (available_height - (height + (rows - 1) * step_y)) // 2)

        with open(self.live_windows, 'w') as f:  # NEW: uses configurable file name
            for idx, wid in enumerate(ids):
                r = idx // cols
                c = idx % cols
                x = int(x_start + c * step_x)
                y = int(y_start + r * step_y)
                try:
                    subprocess.run(['xdotool', 'windowsize', wid, str(width), str(height)], check=False)
                    subprocess.run(['xdotool', 'windowmove', wid, str(x), str(y)], check=False)
                except Exception as e:
                    print(f"[GRID] Failed on {wid}: {e}")
                f.write(wid + '\n')

    def daemon_thread_func(self):
        total_shots = 0
        fire_count = int(self.imagine.get('FIRE_COUNT', 1))
        shot_delay = float(self.system.get('SHOT_DELAY', 0.5))

        prompt_x_pct = self.system.get('PROMPT_X_FROM_LEFT', '35%')
        prompt_y_pct = self.system.get('PROMPT_Y_FROM_BOTTOM', '25%')

        def _parse_shell_output(text):
            d = {}
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                if ':' in line:
                    k, v = line.split(':', 1)
                elif '=' in line:
                    k, v = line.split('=', 1)
                else:
                    continue
                d[k.strip().upper()] = v.strip()
            return d

        for round_num in range(1, fire_count + 1):
            self.imagine = load_env(IMAGINE_ENV, self.imagine)
            if self.imagine.get('FIRE_MODE', 'N') == 'N':
                break

            # Fresh IDs from file each round
            if not os.path.exists(self.live_windows) or os.stat(self.live_windows).st_size == 0:  # NEW: configurable
                continue

            with open(self.live_windows) as f:  # NEW: configurable
                window_ids = [line.strip() for line in f if line.strip()]

            prompt_input = self.prompt_entry.get_text().strip()
            if prompt_input.startswith("Multiple prompts"):
                prompts = self.collected_prompts
            else:
                prompts = get_prompts_from_input(prompt_input)

            burst = int(self.imagine.get('BURST_COUNT', 4))

            for wid in window_ids:
                try:
                    # Check FIRE_MODE before each window
                    self.imagine = load_env(IMAGINE_ENV, self.imagine)
                    if self.imagine.get('FIRE_MODE', 'N') == 'N':
                        break

                    # Activate
                    subprocess.run(['xdotool', 'windowactivate', '--sync', wid])

                    # Fresh geometry
                    geom = subprocess.check_output(['xdotool', 'getwindowgeometry', '--shell', wid]).decode()
                    geom_dict = _parse_shell_output(geom)
                    if 'WIDTH' not in geom_dict or 'HEIGHT' not in geom_dict:
                        raise RuntimeError("Could not parse window geometry")
                    width = int(float(geom_dict['WIDTH']))
                    height = int(float(geom_dict['HEIGHT']))

                    # Click calc
                    if '%' in prompt_x_pct:
                        click_x = int(width * int(prompt_x_pct.rstrip('%')) / 100)
                    else:
                        click_x = int(prompt_x_pct)

                    if '%' in prompt_y_pct:
                        pixels_from_bottom = int(height * int(prompt_y_pct.rstrip('%')) / 100)
                    else:
                        pixels_from_bottom = int(prompt_y_pct)
                    click_y = height - pixels_from_bottom

                    # Get mouse
                    mouse = subprocess.check_output(['xdotool', 'getmouselocation', '--shell']).decode()
                    mouse_dict = _parse_shell_output(mouse)
                    saved_x = mouse_dict.get('X')
                    saved_y = mouse_dict.get('Y')
                    if saved_x is None or saved_y is None:
                        raise RuntimeError("Could not parse mouse location")
                    saved_x = int(float(saved_x))
                    saved_y = int(float(saved_y))


                    # Move mouse
                    subprocess.run(['xdotool', 'mousemove', '--window', wid, str(click_x), str(click_y)])

                    self.set_keep_above(False)
                    # 3 scroll
                    for _ in range(3):
                        # allow stop between scrolls
                        self.imagine = load_env(IMAGINE_ENV, self.imagine)
                        if self.imagine.get('FIRE_MODE', 'N') == 'N':
                            break
                        subprocess.run(['xdotool', 'click', '4'])

                    # Focus click
                    subprocess.run(['xdotool', 'click', '1'])
                    # Burst
                    for j in range(1, burst + 1):
                        current_prompt = prompts[(j - 1) % len(prompts)]

                        # NEW: '_' (or empty after strip) = fallback blank prompt
                        if not current_prompt.strip() or current_prompt.strip() == '_':
                            current_prompt = 'system is king'

                        subprocess.run(['xclip', '-selection', 'clipboard'], input=current_prompt.encode(), check=False)

                        subprocess.run(['xdotool', 'key', '--window', wid, 'ctrl+a', 'ctrl+v', 'Return'])
                        GLib.timeout_add(int(shot_delay * 1000), lambda: None or False)

                    # Restore mouse
                    subprocess.run(['xdotool', 'mousemove', str(saved_x), str(saved_y)])

                    total_shots += burst
                    GLib.idle_add(lambda r=round_num, s=total_shots: self.status_label.set_text(f"Round {r} - Shots {s}"))
                    self.set_keep_above(True)

                except Exception as e:
                    print(f"[DAEMON] FAILED on {wid}: {e}")
                    continue

            # check FIRE_MODE between rounds for responsiveness
            self.imagine = load_env(IMAGINE_ENV, self.imagine)
            if self.imagine.get('FIRE_MODE', 'N') == 'N':
                break

            round_delay = float(self.system.get('ROUND_DELAY', 10.0))
            GLib.timeout_add(int(round_delay * 1000), lambda: None or False)

        GLib.idle_add(lambda: self.status_label.set_text("COMPLETE"))
        update_env(IMAGINE_ENV, 'FIRE_MODE', 'N')

    def on_fire(self, widget):
        if self.imagine['FIRE_MODE'] == 'N':
            update_env(IMAGINE_ENV, 'FIRE_MODE', 'Y')
            self.daemon_thread = threading.Thread(target=self.daemon_thread_func, daemon=True)
            self.daemon_thread.start()
            self.fire_btn.set_label("STOP")
            self.status_label.set_text("Firing...")
        else:
            update_env(IMAGINE_ENV, 'FIRE_MODE', 'N')
            self.fire_btn.set_label("FIRE")
            self.status_label.set_text("Stopped")

    def poll_fire_mode(self):
        self.imagine = load_env(IMAGINE_ENV, self.imagine)
        label = "STOP" if self.imagine.get('FIRE_MODE', 'N') == 'Y' else "FIRE"
        if self.fire_btn.get_label() != label:
            self.fire_btn.set_label(label)
        return True

    def on_edit(self, widget):
        current = self.url_entry.get_text()
        proc = subprocess.Popen(
            ['yad', '--text-info', '--on-top', '--editable', '--title=Edit Target URL(s)',
             '--width=800', '--height=500', '--button=Save:0', '--button=Cancel:1'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        )
        out, _ = proc.communicate(current)
        if proc.returncode == 0:
            self.url_entry.set_text(out.strip())
            self.save_user()

        current = self.prompt_entry.get_text()
        proc = subprocess.Popen(
            ['yad', '--text-info', '--on-top', '--editable', '--title=Edit Prompt',
             '--width=900', '--height=600', '--button=Save:0', '--button=Cancel:1'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        )
        out, _ = proc.communicate(current)
        if proc.returncode == 0:
            self.prompt_entry.set_text(out.strip())
            self.save_user()

    def on_quit(self, widget):
        browser = self.system.get('BROWSER')
        try:
            subprocess.run(['pkill', browser], check=False)
        except:
            pass

        # Get script's directory
        script_dir = os.path.dirname(os.path.abspath(__file__))

        # File to remove (change name)
        filename = self.live_windows  # NEW: uses configurable file name
        file_path = os.path.join(script_dir, filename)

        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"Removed {filename}")
        else:
            print(f"{filename} not found")
        Gtk.main_quit()

if __name__ == '__main__':
    win = BlitzControl()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
