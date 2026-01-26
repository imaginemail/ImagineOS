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

def read_key(file, key, default=''):
    if not os.path.exists(file):
        return default
    with open(file, 'r') as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith(f'{key}='):
                v = stripped.split('=', 1)[1].strip()
                if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                    v = v[1:-1]
                return v.strip()
    return default

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
        f.flush()
        os.fsync(f.fileno())

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

class BlitzControl(Gtk.Window):

    def __init__(self):
        super().__init__(title=".Blitz Talker.")
        self.set_keep_above(True)
        self.set_border_width(12)

        # Force safe start state
        update_env(IMAGINE_ENV, 'FIRE_MODE', 'N')

        # Apply panel settings
        self.set_title(read_key(SYSTEM_ENV, 'PANEL_DEFAULT_TITLE', '.Blitz Talker.'))
        self.set_default_size(int(read_key(SYSTEM_ENV, 'PANEL_DEFAULT_WIDTH', '600')),
                              int(read_key(SYSTEM_ENV, 'PANEL_DEFAULT_HEIGHT', '400')))

        try:
            output = subprocess.check_output(['xdotool', 'getdisplaygeometry']).decode().strip()
            sw, sh = map(int, output.split())
        except Exception:
            sw, sh = 1920, 1080

        pos_x = sw - int(read_key(SYSTEM_ENV, 'PANEL_DEFAULT_WIDTH', '600')) - int(read_key(SYSTEM_ENV, 'PANEL_DEFAULT_X_OFFSET', '20'))
        pos_y = sh - int(read_key(SYSTEM_ENV, 'PANEL_DEFAULT_HEIGHT', '400')) - int(read_key(SYSTEM_ENV, 'PANEL_DEFAULT_Y_OFFSET', '20'))
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

        if collected_prompts:
            if len(collected_prompts) > 1:
                prompt_text = f"Multiple prompts ({len(collected_prompts)}) in .user_env"
            else:
                prompt_text = collected_prompts[0]
        else:
            prompt_text = ''
        self.prompt_entry.set_text(prompt_text)

        if not collected_prompts and not self.prompt_entry.get_text().strip():
            self.prompt_entry.set_text(read_key(SYSTEM_ENV, 'DEFAULT_PROMPT', ''))

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
        burst_adj = Gtk.Adjustment(value=int(read_key(IMAGINE_ENV, 'BURST_COUNT', '4')), lower=1, upper=20, step_increment=1)
        self.burst_spin = Gtk.SpinButton(adjustment=burst_adj)
        burst_hbox.pack_start(self.burst_spin, True, True, 0)
        controls_box.pack_start(burst_hbox, True, True, 0)

        # Rounds
        fire_hbox = Gtk.Box(spacing=6)
        fire_label = Gtk.Label(label="Rounds:")
        fire_hbox.pack_start(fire_label, False, False, 0)
        fire_adj = Gtk.Adjustment(value=int(read_key(IMAGINE_ENV, 'FIRE_COUNT', '1')), lower=1, upper=99, step_increment=1)
        self.fire_spin = Gtk.SpinButton(adjustment=fire_adj)
        fire_hbox.pack_start(self.fire_spin, True, True, 0)
        controls_box.pack_start(fire_hbox, True, True, 0)

        # Targets
        stage_hbox = Gtk.Box(spacing=6)
        stage_label = Gtk.Label(label="Targets:")
        stage_hbox.pack_start(stage_label, False, False, 0)
        stage_adj = Gtk.Adjustment(value=int(read_key(USER_ENV, 'STAGE_COUNT', '8')), lower=1, upper=200, step_increment=1)
        self.stage_spin = Gtk.SpinButton(adjustment=stage_adj)
        stage_hbox.pack_start(self.stage_spin, True, True, 0)
        controls_box.pack_start(stage_hbox, True, True, 0)

        self.status_label = Gtk.Label(label="Ready")
        box.pack_start(self.status_label, False, False, 0)

        btn_box = Gtk.Box(spacing=8)
        box.pack_start(btn_box, False, False, 0)

        stage_btn = Gtk.Button(label="STAGE")
        stage_btn.connect("clicked", self.on_stage)
        btn_box.pack_start(stage_btn, True, True, 0)

        self.fire_btn = Gtk.Button(label="FIRE")
        self.fire_btn.connect("clicked", self.on_fire)
        btn_box.pack_start(self.fire_btn, True, True, 0)

        edit_btn = Gtk.Button(label="EDIT")
        edit_btn.connect("clicked", self.on_edit)
        btn_box.pack_start(edit_btn, True, True, 0)

        quit_btn = Gtk.Button(label="QUIT")
        quit_btn.connect("clicked", self.on_quit)
        btn_box.pack_start(quit_btn, True, True, 0)

        self.daemon_thread = None

        # Initial UI update
        self.update_fire_button()

    def update_fire_button(self):
        mode = read_key(IMAGINE_ENV, 'FIRE_MODE', 'N')
        self.fire_btn.set_label("STOP" if mode == 'Y' else "FIRE")

    def save_all(self):
        # Save prompt to .user_env
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

        # Save URL and STAGE_COUNT to .user_env
        update_env(USER_ENV, 'DEFAULT_URL', self.url_entry.get_text())
        update_env(USER_ENV, 'STAGE_COUNT', str(int(self.stage_spin.get_value())))

        # Save burst and fire count to .imagine_env
        update_env(IMAGINE_ENV, 'BURST_COUNT', str(int(self.burst_spin.get_value())))
        update_env(IMAGINE_ENV, 'FIRE_COUNT', str(int(self.fire_spin.get_value())))

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
            self.prompt_entry.set_text(filename)
            self.save_all()
        dialog.destroy()

    def on_stage(self, widget):
        self.save_all()

        # Kill old target windows
        try:
            subprocess.run(['pkill', read_key(SYSTEM_ENV, 'BROWSER')], check=False)
        except:
            pass

        # Clean old live_windows file
        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), read_key(SYSTEM_ENV, 'WINDOW_LIST'))
        if os.path.exists(file_path):
            os.remove(file_path)

        num = int(read_key(USER_ENV, 'STAGE_COUNT', '8'))
        url_input = read_key(USER_ENV, 'DEFAULT_URL', '')
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

            time.sleep(float(read_key(SYSTEM_ENV, 'STAGE_DELAY', '1')))

        print(f"[STAGE] Waiting {read_key(SYSTEM_ENV, 'GRID_START_DELAY', '3')}s before gridding")
        GLib.timeout_add(int(float(read_key(SYSTEM_ENV, 'GRID_START_DELAY', '3')) * 1000), lambda: self.grid_windows(num) or False)

    def on_fire(self, widget):
        self.save_all()
        if read_key(IMAGINE_ENV, 'FIRE_MODE', 'N') == 'N':
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

        current = self.prompt_entry.get_text()
        proc = subprocess.Popen(
            ['yad', '--text-info', '--on-top', '--editable', '--title=Edit Prompt',
             '--width=900', '--height=600', '--button=Save:0', '--button=Cancel:1'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        )
        out, _ = proc.communicate(current)
        if proc.returncode == 0:
            self.prompt_entry.set_text(out.strip())
            self.save_all()

    def on_quit(self, widget):
        self.save_all()
        try:
            subprocess.run(['pkill', read_key(SYSTEM_ENV, 'BROWSER')], check=False)
        except:
            pass

        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), read_key(SYSTEM_ENV, 'WINDOW_LIST'))
        if os.path.exists(file_path):
            os.remove(file_path)
        Gtk.main_quit()

    def grid_windows(self, expected_num):
        patterns = [p.strip().strip('"').strip("'").lower() for p in read_key(SYSTEM_ENV, 'WINDOW_PATTERNS').split(',') if p.strip()]
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

            time.sleep(float(read_key(SYSTEM_ENV, 'GRID_START_DELAY', '3')))

        if last_matched:
            print("[GRID] Max tries reached. Gridding last matches.")
            self._grid_ids(last_matched)
        else:
            print("[GRID] Max tries reached, no matches.")
        return False

    def _grid_ids(self, ids):
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
        effective_step = max(1, int(int(read_key(SYSTEM_ENV, 'DEFAULT_WIDTH')) * (100 - int(read_key(SYSTEM_ENV, 'MAX_OVERLAP_PERCENT'))) / 100))
        max_cols_by_width = 1 + (available_width - int(read_key(SYSTEM_ENV, 'DEFAULT_WIDTH'))) // effective_step if available_width >= int(read_key(SYSTEM_ENV, 'DEFAULT_WIDTH')) else 1
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
            total_grid_width = int(read_key(SYSTEM_ENV, 'DEFAULT_WIDTH'))
        else:
            min_total_width = int(read_key(SYSTEM_ENV, 'DEFAULT_WIDTH')) + (cols - 1) * effective_step
            if min_total_width <= available_width:
                extra_space = available_width - int(read_key(SYSTEM_ENV, 'DEFAULT_WIDTH'))
                step_x = extra_space // (cols - 1)
                if step_x < effective_step:
                    step_x = effective_step
            else:
                step_x = max(1, (available_width - int(read_key(SYSTEM_ENV, 'DEFAULT_WIDTH'))) // (cols - 1))
            total_grid_width = int(read_key(SYSTEM_ENV, 'DEFAULT_WIDTH')) + (cols - 1) * step_x

        vertical_effective_step = max(1, int(int(read_key(SYSTEM_ENV, 'DEFAULT_HEIGHT')) * (100 - int(read_key(SYSTEM_ENV, 'MAX_OVERLAP_PERCENT'))) / 100))
        if rows == 1:
            step_y = 0
        else:
            min_total_height = int(read_key(SYSTEM_ENV, 'DEFAULT_HEIGHT')) + (rows - 1) * vertical_effective_step
            if min_total_height <= available_height:
                extra_vspace = available_height - int(read_key(SYSTEM_ENV, 'DEFAULT_HEIGHT'))
                step_y = extra_vspace // (rows - 1)
                if step_y < vertical_effective_step:
                    step_y = vertical_effective_step
            else:
                step_y = max(1, (available_height - int(read_key(SYSTEM_ENV, 'DEFAULT_HEIGHT'))) // (rows - 1))

        x_start = margin + max(0, (available_width - total_grid_width) // 2)
        y_start = margin + max(0, (available_height - (int(read_key(SYSTEM_ENV, 'DEFAULT_HEIGHT')) + (rows - 1) * step_y)) // 2)

        with open(read_key(SYSTEM_ENV, 'WINDOW_LIST'), 'w') as f:
            for idx, wid in enumerate(ids):
                r = idx // cols
                c = idx % cols
                x = int(x_start + c * step_x)
                y = int(y_start + r * step_y)
                try:
                    subprocess.run(['xdotool', 'windowactivate', '--sync', wid])
                    time.sleep(float(read_key(SYSTEM_ENV, 'STAGE_DELAY', '1')))
                    subprocess.run(['xdotool', 'windowsize', wid, read_key(SYSTEM_ENV, 'DEFAULT_WIDTH'), read_key(SYSTEM_ENV, 'DEFAULT_HEIGHT')], check=False)
                    subprocess.run(['xdotool', 'windowmove', wid, str(x), str(y)], check=False)
                except Exception as e:
                    print(f"[GRID] Failed on {wid}: {e}")
                f.write(wid + '\n')

    def daemon_thread_func(self):
        total_shots = 0

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

        for round_num in range(1, int(read_key(IMAGINE_ENV, 'FIRE_COUNT', '1')) + 1):
            if read_key(IMAGINE_ENV, 'FIRE_MODE', 'N') == 'N':
                break

            if round_num > 1:
                time.sleep(float(read_key(SYSTEM_ENV, 'ROUND_DELAY', '10')))

            live_windows_file = read_key(SYSTEM_ENV, 'WINDOW_LIST')
            if not os.path.exists(live_windows_file) or os.stat(live_windows_file).st_size == 0:
                continue

            with open(live_windows_file) as f:
                window_ids = [line.strip() for line in f if line.strip()]

            prompts = []
            if os.path.exists(USER_ENV):
                with open(USER_ENV, 'r') as f:
                    for line in f:
                        stripped = line.strip()
                        if stripped.startswith('PROMPT='):
                            v = stripped.split('=', 1)[1].strip()
                            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                                v = v[1:-1]
                            prompts.append(v)

            if not prompts or all(not p.strip() for p in prompts):
                prompts = [read_key(SYSTEM_ENV, 'DEFAULT_PROMPT')]

            burst = int(read_key(IMAGINE_ENV, 'BURST_COUNT', '4'))

            for wid in window_ids:
                try:

                    subprocess.run(['xdotool', 'windowactivate', '--sync', wid])

                    geom_raw = subprocess.check_output(['xdotool', 'getwindowgeometry', '--shell', wid]).decode()
                    geom_dict = _parse_shell_output(geom_raw)
                    width = int(geom_dict['WIDTH'])
                    height = int(geom_dict['HEIGHT'])

                    prompt_x = read_key(SYSTEM_ENV, 'PROMPT_X_FROM_LEFT')
                    if '%' in prompt_x:
                        click_x = int(width * int(prompt_x.rstrip('%')) / 100)
                    else:
                        click_x = int(prompt_x)

                    prompt_y = read_key(SYSTEM_ENV, 'PROMPT_Y_FROM_BOTTOM')
                    if '%' in prompt_y:
                        pixels_from_bottom = int(height * int(prompt_y.rstrip('%')) / 100)
                    else:
                        pixels_from_bottom = int(prompt_y)
                    click_y = height - pixels_from_bottom

                    mouse_raw = subprocess.check_output(['xdotool', 'getmouselocation', '--shell']).decode()
                    mouse_dict = _parse_shell_output(mouse_raw)
                    saved_x = int(mouse_dict['X'])
                    saved_y = int(mouse_dict['Y'])

                    subprocess.run(['xdotool', 'mousemove', '--window', wid, str(click_x), str(click_y)])

                    self.set_keep_above(False)

                    for _ in range(3):
                        subprocess.run(['xdotool', 'click', '--window', wid, '4'])

                    subprocess.run(['xdotool', 'click', '--window', wid, '1'])

                    for j in range(1, burst + 1):
                        current_prompt = prompts[(j - 1) % len(prompts)]

                        send_prompt = current_prompt
                        if current_prompt.strip() in ('_', '~'):
                            send_prompt = ''
                        elif current_prompt.startswith('~~'):
                            send_prompt = '~' + current_prompt[2:]
                        elif current_prompt.startswith('~'):
                            send_prompt = current_prompt[1:]

                        if send_prompt == '':
                            subprocess.run(['xdotool', 'key', '--window', wid, 'ctrl+a', 'Del', 'Return'])
                        else:
                            subprocess.run(['xclip', '-selection', 'clipboard'], input=send_prompt.encode(), check=False)
                            subprocess.run(['xdotool', 'key', '--window', wid, 'ctrl+a', 'ctrl+v', 'Return'])

                        time.sleep(float(read_key(SYSTEM_ENV, 'SHOT_DELAY', '0.5')))

                    subprocess.run(['xdotool', 'mousemove', str(saved_x), str(saved_y)])

                    self.set_keep_above(True)
                    total_shots += burst
                    GLib.idle_add(lambda r=round_num, s=total_shots: self.status_label.set_text(f"Round {r} - Shots {s}"))

                    if read_key(IMAGINE_ENV, 'FIRE_MODE', 'N') == 'N':
                        break

                except Exception as e:
                    print(f"[DAEMON] FAILED on {wid}: {e}")
                    continue

        # Daemon complete: set mode to N, then update UI (button and status)
        update_env(IMAGINE_ENV, 'FIRE_MODE', 'N')
        GLib.idle_add(lambda: self.status_label.set_text("COMPLETE"))
        GLib.idle_add(self.update_fire_button)

if __name__ == '__main__':
    win = BlitzControl()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
