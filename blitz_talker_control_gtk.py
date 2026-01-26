#!/usr/bin/env python3
import subprocess
import os
import re
import shlex
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
        val = re.sub(r'\\\s*$', '', val)
        val = re.sub(r'\\\s*\n\s*', ' ', val)
        return shlex.split(val)
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
        if input_str:
            prompts = [input_str]
        else:
            prompts = ['']
    return prompts

def check_required_system_vars(system):
    missing = [k for k in REQUIRED_SYSTEM_VARS if k not in system or system[k] == ""]
    return missing

# Strict hierarchy: system defaults → imagine script state → user final override
def load_all_env(self):
    merged = load_env(SYSTEM_ENV, {})
    merged.update(load_env(IMAGINE_ENV, {}))
    merged.update(load_env(USER_ENV, {}))
    self.env = merged
    self.flags_head = load_flags('BROWSER_FLAGS_HEAD')
    self.flags_middle = load_flags('BROWSER_FLAGS_MIDDLE')
    self.flags_tail = load_flags('BROWSER_FLAGS_TAIL')

    # Multi PROMPT from user (overrides all) — optional
    collected_prompts = []
    if os.path.exists(USER_ENV):
        with open(USER_ENV, 'r') as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith('PROMPT='):
                    v = stripped.split('=', 1)[1].strip()
                    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                        v = v[1:-1]
                    collected_prompts.append(v)
    self.collected_prompts = collected_prompts

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

class BlitzControl(Gtk.Window):

    def __init__(self):
        panel_title = SYSTEM.get('PANEL_DEFAULT_TITLE', '.Blitz Talker.')
        super().__init__(title=panel_title)
        self.set_keep_above(True)
        self.set_border_width(12)
        load_all_env(self) # initial full merged load

        panel_w = int(self.env['PANEL_DEFAULT_WIDTH'])
        panel_h = int(self.env['PANEL_DEFAULT_HEIGHT'])
        x_offset = int(self.env['PANEL_DEFAULT_X_OFFSET'])
        y_offset = int(self.env['PANEL_DEFAULT_Y_OFFSET'])
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
        self.url_entry.set_text(read_key(USER_ENV, 'DEFAULT_URL', ''))
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

        if self.collected_prompts:
            if len(self.collected_prompts) > 1:
                prompt_text = f"Multiple prompts ({len(self.collected_prompts)}) in .user_env"
            else:
                prompt_text = self.collected_prompts[0]
        else:
            prompt_text = ''
        self.prompt_entry.set_text(prompt_text)

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
        burst_adj = Gtk.Adjustment(value=int(self.env['BURST_COUNT']), lower=1, upper=20, step_increment=1)
        self.burst_spin = Gtk.SpinButton(adjustment=burst_adj)
        self.burst_spin.connect("value-changed", lambda w: self.save_imagine())
        burst_hbox.pack_start(self.burst_spin, True, True, 0)
        controls_box.pack_start(burst_hbox, True, True, 0)

        # Rounds
        fire_hbox = Gtk.Box(spacing=6)
        fire_label = Gtk.Label(label="Rounds:")
        fire_hbox.pack_start(fire_label, False, False, 0)
        fire_adj = Gtk.Adjustment(value=int(self.env['FIRE_COUNT']), lower=1, upper=99, step_increment=1)
        self.fire_spin = Gtk.SpinButton(adjustment=fire_adj)
        self.fire_spin.connect("value-changed", lambda w: self.save_imagine())
        fire_hbox.pack_start(self.fire_spin, True, True, 0)
        controls_box.pack_start(fire_hbox, True, True, 0)

        # Targets
        stage_hbox = Gtk.Box(spacing=6)
        stage_label = Gtk.Label(label="Targets:")
        stage_hbox.pack_start(stage_label, False, False, 0)
        stage_adj = Gtk.Adjustment(value=int(self.env['STAGE_COUNT']), lower=1, upper=200, step_increment=1)
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

        self.fire_btn = Gtk.Button(label="FIRE" if self.env['FIRE_MODE'] == 'N' else "STOP")
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

    # NEW: special handling for PROMPT to preserve manual multiple lines when not changed
    def save_user(self):
        prompt_text = self.prompt_entry.get_text().strip()
        if not prompt_text.startswith("Multiple prompts"):
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
        load_all_env(self)
        num = self.stage_count
        url_input = self.url_entry.get_text().strip()
        urls = get_urls_from_input(url_input)
        if not urls:
            print("[STAGE] No URLs provided; aborting stage.")
            return

        cmd_base = [read_key(SYSTEM_ENV, 'BROWSER')] + load_flags('BROWSER_FLAGS_HEAD') + load_flags('BROWSER_FLAGS_MIDDLE') + load_flags('BROWSER_FLAGS_TAIL')

        print(f"[STAGE] Launching {num} windows...")
        for i in range(num):
            url = urls[i % len(urls)]
            cmd = cmd_base + [url]
            if load_flags('BROWSER_FLAGS_TAIL'):
                cmd[-2] = cmd[-2] + cmd[-1]
                cmd.pop()
            print(f"[STAGE] Launching: {' '.join(cmd)}")
            try:
                subprocess.Popen(cmd)
            except Exception as e:
                print(f"[STAGE] Failed to launch: {e}")

            time.sleep(self.stage_delay)

        print(f"[STAGE] Waiting {self.grid_start_delay}s before gridding")
        GLib.timeout_add(int(self.grid_start_delay * 1000), lambda: self.grid_windows(num) or False)

    def grid_windows(self, expected_num):
        load_all_env(self)
        patterns = [p.strip().strip('"').strip("'").lower() for p in self.window_patterns.split(',') if p.strip()]
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

            time.sleep(self.grid_start_delay)

        if last_matched:
            print("[GRID] Max tries reached. Gridding last matches.")
            self._grid_ids(last_matched)
        else:
            print("[GRID] Max tries reached, no matches.")
        return False

    def _grid_ids(self, ids):
        load_all_env(self)
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
        effective_step = max(1, int(self.default_width * (100 - self.max_overlap_pct) / 100))
        max_cols_by_width = 1 + (available_width - self.default_width) // effective_step if available_width >= self.default_width else 1
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
            total_grid_width = self.default_width
        else:
            min_total_width = self.default_width + (cols - 1) * effective_step
            if min_total_width <= available_width:
                extra_space = available_width - self.default_width
                step_x = extra_space // (cols - 1)
                if step_x < effective_step:
                    step_x = effective_step
            else:
                step_x = max(1, (available_width - self.default_width) // (cols - 1))
            total_grid_width = self.default_width + (cols - 1) * step_x

        vertical_effective_step = max(1, int(self.default_height * (100 - self.max_overlap_pct) / 100))
        if rows == 1:
            step_y = 0
        else:
            min_total_height = self.default_height + (rows - 1) * vertical_effective_step
            if min_total_height <= available_height:
                extra_vspace = available_height - self.default_height
                step_y = extra_vspace // (rows - 1)
                if step_y < vertical_effective_step:
                    step_y = vertical_effective_step
            else:
                step_y = max(1, (available_height - self.default_height) // (rows - 1))

        x_start = margin + max(0, (available_width - total_grid_width) // 2)
        y_start = margin + max(0, (available_height - (self.default_height + (rows - 1) * step_y)) // 2)

        with open(self.live_windows, 'w') as f: # NEW: uses configurable file name
            for idx, wid in enumerate(ids):
                r = idx // cols
                c = idx % cols
                x = int(x_start + c * step_x)
                y = int(y_start + r * step_y)
                try:
                    subprocess.run(['xdotool', 'windowsize', wid, str(self.default_width), str(self.default_height)], check=False)
                    subprocess.run(['xdotool', 'windowmove', wid, str(x), str(y)], check=False)
                except Exception as e:
                    print(f"[GRID] Failed on {wid}: {e}")
                f.write(wid + '\n')

    # NEW: central stop check (reloads env + checks FIRE_MODE)
    def should_stop(self):
        load_all_env(self)
        return self.fire_mode == 'N'

    def daemon_thread_func(self):
        total_shots = 0
        load_all_env(self)

    def _parse_shell_output(text):
            d = {}
            for line in text.splitlines():
                line = line.strip()
                if not line or ':' not in line:
                    continue
                k, v = line.split(':', 1)
                d[k.strip().upper()] = v.strip()
            return d

            for round_num in range(1, self.fire_count + 1):
            if self.should_stop():
                break

            # Fresh IDs from file each round
            if not os.path.exists(self.live_windows) or os.stat(self.live_windows).st_size == 0: # NEW: configurable
                continue
            with open(self.live_windows) as f: # NEW: configurable
                window_ids = [line.strip() for line in f if line.strip()]

            prompt_input = self.prompt_entry.get_text().strip()
            if prompt_input.startswith("Multiple prompts"):
                prompts = self.collected_prompts
            else:
                prompts = get_prompts_from_input(prompt_input)

            burst = self.burst_count

            for wid in window_ids:
                try:
                    # Activate
                    subprocess.run(['xdotool', 'windowactivate', '--sync', wid])

                    # Fresh geometry
                    geom = subprocess.check_output(['xdotool', 'getwindowgeometry', '--shell', wid]).decode()
                    geom_dict = _parse_shell_output(geom)
                    if 'WIDTH' not in geom_dict or 'HEIGHT' not in geom_dict:
                        raise RuntimeError("Could not parse window geometry")
                    width = int(geom_dict['WIDTH'])
                    height = int(geom_dict['HEIGHT'])

                    # Click calc
                    if '%' in self.prompt_x_from_left:
                        click_x = int(width * int(self.prompt_x_from_left.rstrip('%')) / 100)
                    else:
                        click_x = int(self.prompt_x_from_left)

                    if '%' in self.prompt_y_from_bottom:
                        pixels_from_bottom = int(height * int(self.prompt_y_from_bottom.rstrip('%')) / 100)
                    else:
                        pixels_from_bottom = int(self.prompt_y_from_bottom)
                    click_y = height - pixels_from_bottom

                    # Get mouse
                    mouse = subprocess.check_output(['xdotool', 'getmouselocation', '--shell']).decode()
                    mouse_dict = _parse_shell_output(mouse)
                    saved_x = mouse_dict.get('X')
                    saved_y = mouse_dict.get('Y')
                    if saved_x is None or saved_y is None:
                        raise RuntimeError("Could not parse mouse location")
                    saved_x = int(saved_x)
                    saved_y = int(saved_y)

                    # Move mouse
                    subprocess.run(['xdotool', 'mousemove', '--window', wid, str(click_x), str(click_y)])

                    # Remove keep above so target comes to top
                    self.set_keep_above(False)

                    # 3 scrolls
                    for _ in range(3):
                        subprocess.run(['xdotool', 'click', '4'])

                    # Focus click
                    subprocess.run(['xdotool', 'click', '1'])
                    # Burst
                    for j in range(1, burst + 1):
                        current_prompt = prompts[(j - 1) % len(prompts)]

                        if not current_prompt.strip() or current_prompt.strip() == '_':
                            current_prompt = ''
                            subprocess.run(['xdotool', 'key', '--window', wid, 'ctrl+a', 'Del', 'Return'])
                        else:
                            subprocess.run(['xclip', '-selection', 'clipboard'], input=current_prompt.encode(), check=False)
                            subprocess.run(['xdotool', 'key', '--window', wid, 'ctrl+a', 'ctrl+v', 'Return'])

                        time.sleep(self.shot_delay)

                    # Raise panel
                    self.set_keep_above(True)
                    # Restore mouse
                    subprocess.run(['xdotool', 'mousemove', str(saved_x), str(saved_y)])

                    total_shots += burst
                    GLib.idle_add(lambda r=round_num, s=total_shots: self.status_label.set_text(f"Round {r} - Shots {s}"))
                    # Check between windows
                    if self.should_stop():
                        break

                except Exception as e:
                    print(f"[DAEMON] FAILED on {wid}: {e}")
                    continue

            # Check between rounds
            if self.should_stop():
                break
            time.sleep(5.0)

        GLib.idle_add(lambda: self.status_label.set_text("COMPLETE"))
        update_env(IMAGINE_ENV, 'FIRE_MODE', 'N')

    def on_fire(self, widget):
        if self.fire_mode == 'N':
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
        if self.should_stop():
            label = "FIRE"
        else:
            label = "STOP"
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
        load_all_env(self)
        try:
            subprocess.run(['pkill', self.browser], check=False)
        except:
            pass

        # Get script's directory
        script_dir = os.path.dirname(os.path.abspath(__file__))

        # File to remove (change name)
        filename = self.live_windows # NEW: uses configurable file name
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
