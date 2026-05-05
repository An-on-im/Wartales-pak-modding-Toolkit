# pack.py
import os
import struct
import zlib
import threading
import configparser
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, ttk, scrolledtext

# ============================================================
# Configuration and logger
# ============================================================
CONFIG_FILE = "pak_updater.ini"
LOG_FILE    = "pak_updater.log"

LOG_DEBUG = 10
LOG_INFO  = 20
LOG_ERROR = 30

LEVEL_NAMES = {
    LOG_DEBUG: "Detailed",
    LOG_INFO:  "Brief",
    LOG_ERROR: "Errors only"
}

class AppLogger:
    """Thread-safe logger with GUI output and file recording."""
    def __init__(self, root, gui_widget, level=LOG_INFO):
        self.root = root
        self.widget = gui_widget
        self.level = level
        self.lock = threading.Lock()
        self._init_file()

    def _init_file(self):
        with self.lock:
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S}, "
                        f"level: {LEVEL_NAMES.get(self.level, '?')} =====\n")

    def change_level(self, new_level):
        self.level = new_level
        self._log_text(f"Log level changed to: "
                       f"{LEVEL_NAMES.get(new_level, new_level)}\n", "blue")

    def _log_text(self, message, color_tag=None):
        def _write():
            if not self.widget.winfo_exists():
                return
            self.widget.configure(state='normal')
            start = self.widget.index(tk.END)
            self.widget.insert(tk.END, message)
            end = self.widget.index(tk.END)
            if color_tag:
                self.widget.tag_add(color_tag, start, end)
                self.widget.tag_config(color_tag, foreground=color_tag)
            self.widget.see(tk.END)
            self.widget.configure(state='disabled')
        self.root.after(0, _write)

    def _write_file(self, msg):
        with self.lock:
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(msg)

    def log(self, message, level=LOG_INFO, color_tag=None):
        if level < self.level:
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        level_str = {LOG_DEBUG: "DEBUG", LOG_INFO: "INFO", LOG_ERROR: "ERROR"}.get(level, "INFO")
        line = f"[{timestamp}] {level_str}: {message}\n"
        self._write_file(line)
        self._log_text(line, color_tag)

    def debug(self, message): self.log(message, LOG_DEBUG, "gray")
    def info(self, message):  self.log(message, LOG_INFO, "black")
    def error(self, message): self.log(message, LOG_ERROR, "red")


# ============================================================
# Main PAK update function
# ============================================================
def update_pak_in_memory(original_path, extracted_root, output_path, logger):
    """
    Loads TOC into memory, iterates through it, replaces modified files,
    builds a complete PAK in a bytearray and saves it to output_path.
    """
    logger.info(f"Starting update: {original_path}")
    # Open original PAK
    with open(original_path, 'rb') as f:
        # --- Global header ---
        magic = f.read(4)
        if magic != b'PAK\x00':
            raise ValueError("Invalid PAK signature")

        DataOffset = struct.unpack('<I', f.read(4))[0]   # offset to data area
        fileSize   = struct.unpack('<I', f.read(4))[0]   # original file size
        global_dummy = struct.unpack('<H', f.read(2))[0] # 2 bytes, purpose unknown
        numFolder  = struct.unpack('<I', f.read(4))[0]   # number of top-level entries

        # Read the whole TOC (table of contents) into memory
        toc_size = DataOffset - 18
        toc_bytes = bytearray(f.read(toc_size))

    # Prepare output buffer: header + TOC
    header = bytearray(18)
    header[0:4] = magic
    struct.pack_into('<I', header, 4, DataOffset)
    # fileSize initially 0, will be fixed later
    struct.pack_into('<H', header, 12, global_dummy)
    struct.pack_into('<I', header, 14, numFolder)

    # Output buffer contains header + TOC, then data
    out_buf = bytearray(header)
    out_buf.extend(toc_bytes)  # add original TOC (we will modify it in place)

    # TOC offset in out_buf (after header)
    TOC_START = 18
    data_buf = bytearray()   # we collect the data area here

    # Current offset in the data area (relative to DataOffset)
    current_data_offset = 0

    # Recursively traverse TOC in out_buf while reading original data for unchanged files
    orig_file = open(original_path, 'rb')

    def read_orig_data(offset, size):
        """Read original file data from absolute position."""
        orig_file.seek(offset)
        return orig_file.read(size)

    def process_entries(toc_idx, count, parent_path=""):
        """Process 'count' entries starting at toc_idx in out_buf.
        Returns the new toc_idx after all entries."""
        nonlocal current_data_offset
        idx = toc_idx
        for _ in range(count):
            # namelen
            namelen = out_buf[idx]
            idx += 1
            # name
            name_bytes = out_buf[idx:idx+namelen]
            name = name_bytes.decode('latin-1')
            idx += namelen
            # type
            etype = out_buf[idx]
            idx += 1

            if etype == 1:  # folder
                numEntry = struct.unpack_from('<I', out_buf, idx)[0]
                idx += 4
                new_path = parent_path + "/" + name if parent_path else name
                idx = process_entries(idx, numEntry, new_path)
            elif etype in (0, 2):  # file
                # Remember the offset field position in out_buf
                offset_pos = idx
                # Read original fields
                if etype == 0:
                    orig_offset = struct.unpack_from('<I', out_buf, idx)[0]
                    idx += 4
                else:  # type 2
                    orig_offset = int(struct.unpack_from('<d', out_buf, idx)[0])
                    idx += 8
                size_pos = idx
                orig_size = struct.unpack_from('<I', out_buf, idx)[0]
                idx += 4
                adler_pos = idx
                orig_adler = struct.unpack_from('<I', out_buf, idx)[0]  # Adler-32 checksum of file data
                idx += 4

                # Build relative path
                rel_path = parent_path + "/" + name if parent_path else name
                # Check if the file exists in the extracted folder
                disk_full_path = os.path.join(extracted_root, rel_path)
                modified = False
                if os.path.isfile(disk_full_path):
                    with open(disk_full_path, 'rb') as disk_f:
                        disk_data = disk_f.read()
                    new_adler = zlib.adler32(disk_data) & 0xFFFFFFFF
                    new_size = len(disk_data)
                    if new_adler != orig_adler or new_size != orig_size:
                        modified = True
                else:
                    # File missing in extracted folder – keep the original
                    logger.info(f"File missing on disk: {rel_path}, keeping original.")
                    modified = False

                # Compute new offset for this file
                new_offset = current_data_offset
                # Write new offset into out_buf
                if etype == 0:
                    struct.pack_into('<I', out_buf, offset_pos, new_offset)
                else:
                    struct.pack_into('<d', out_buf, offset_pos, float(new_offset))

                if modified:
                    logger.info(f"Modified: {rel_path} (new size={new_size})")
                    # Update size and Adler checksum
                    struct.pack_into('<I', out_buf, size_pos, new_size)
                    struct.pack_into('<I', out_buf, adler_pos, new_adler)
                    # Append new data to data_buf
                    data_buf.extend(disk_data)
                    current_data_offset += new_size
                else:
                    logger.debug(f"Unchanged: {rel_path}")
                    # size and Adler remain unchanged; offset already updated.
                    # Read original data from the original PAK
                    orig_abs = DataOffset + orig_offset
                    orig_data = read_orig_data(orig_abs, orig_size)
                    data_buf.extend(orig_data)
                    current_data_offset += orig_size
            else:
                raise ValueError(f"Unknown element type {etype}")
        return idx

    # Start traversal
    process_entries(TOC_START, numFolder, "")

    # Now data_buf contains all files in the correct order.
    # Append it to out_buf starting at DataOffset position.
    out_buf.extend(data_buf)

    # Compute final file size
    final_size = len(out_buf)
    # Update fileSize in the header (offset 8)
    struct.pack_into('<I', out_buf, 8, final_size)

    # Write result to output_path
    with open(output_path, 'wb') as f:
        f.write(out_buf)

    orig_file.close()
    logger.info(f"Done. New PAK saved: {output_path}, size={final_size} bytes.")


# ============================================================
# GUI application
# ============================================================
class PakUpdaterApp:
    def __init__(self, root):
        self.root = root
        root.title("Shiro Games PAK Updater (Wartales)")
        root.resizable(True, True)
        root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.original_pak   = tk.StringVar()
        self.extracted_dir  = tk.StringVar()
        self.output_pak     = tk.StringVar()
        self.log_level_var  = tk.IntVar(value=LOG_INFO)

        self.setup_ui()
        self.load_settings()

    def setup_ui(self):
        top = ttk.Frame(self.root, padding=5)
        top.pack(fill=tk.X, side=tk.TOP)

        ttk.Label(top, text="Original PAK:").grid(row=0, column=0, sticky='e', padx=5, pady=5)
        ttk.Entry(top, textvariable=self.original_pak, width=50).grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(top, text="Browse...", command=self.browse_original).grid(row=0, column=2, padx=5, pady=5)

        ttk.Label(top, text="Extracted folder:").grid(row=1, column=0, sticky='e', padx=5, pady=5)
        ttk.Entry(top, textvariable=self.extracted_dir, width=50).grid(row=1, column=1, padx=5, pady=5)
        ttk.Button(top, text="Browse...", command=self.browse_extracted).grid(row=1, column=2, padx=5, pady=5)

        ttk.Label(top, text="Save as:").grid(row=2, column=0, sticky='e', padx=5, pady=5)
        ttk.Entry(top, textvariable=self.output_pak, width=50).grid(row=2, column=1, padx=5, pady=5)
        ttk.Button(top, text="Browse...", command=self.browse_output).grid(row=2, column=2, padx=5, pady=5)

        ctrl = ttk.Frame(self.root, padding=5)
        ctrl.pack(fill=tk.X, side=tk.TOP)

        self.btn_update = ttk.Button(ctrl, text="Update archive", command=self.start_update)
        self.btn_update.grid(row=0, column=0, padx=5, pady=5)

        self.progress = ttk.Progressbar(ctrl, mode='indeterminate', length=200)
        self.progress.grid(row=0, column=1, padx=5, pady=5)

        self.status = ttk.Label(ctrl, text="Ready", foreground="gray")
        self.status.grid(row=0, column=2, padx=5, pady=5, sticky='w')

        # Log level selection
        lvl_frame = ttk.LabelFrame(self.root, text="Log level", padding=5)
        lvl_frame.pack(fill=tk.X, side=tk.TOP, padx=5, pady=5)

        for val, txt in [(LOG_DEBUG, "Detailed"), (LOG_INFO, "Brief"), (LOG_ERROR, "Errors only")]:
            ttk.Radiobutton(lvl_frame, text=txt, variable=self.log_level_var, value=val,
                            command=self.on_loglevel_changed).pack(side=tk.LEFT, padx=10)

        # Log window
        log_frame = ttk.LabelFrame(self.root, text="Log", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, side=tk.TOP, padx=5, pady=5)

        self.log_widget = scrolledtext.ScrolledText(log_frame, height=15, state='disabled', wrap=tk.WORD)
        self.log_widget.pack(fill=tk.BOTH, expand=True)

        self.logger = AppLogger(self.root, self.log_widget, level=self.log_level_var.get())

    def browse_original(self):
        path = filedialog.askopenfilename(title="Original PAK", filetypes=[("PAK files", "*.pak"), ("All files", "*.*")])
        if path:
            self.original_pak.set(path)
            base, ext = os.path.splitext(path)
            self.output_pak.set(base + "_updated" + ext)

    def browse_extracted(self):
        path = filedialog.askdirectory(title="Extracted folder")
        if path:
            self.extracted_dir.set(path)

    def browse_output(self):
        path = filedialog.asksaveasfilename(title="Save updated PAK as", defaultextension=".pak",
                                            filetypes=[("PAK files", "*.pak")])
        if path:
            self.output_pak.set(path)

    def on_loglevel_changed(self):
        new_lvl = self.log_level_var.get()
        self.logger.change_level(new_lvl)
        self.save_settings()

    def start_update(self):
        orig = self.original_pak.get()
        extr = self.extracted_dir.get()
        out  = self.output_pak.get()
        if not orig or not extr or not out:
            self.logger.error("All fields must be filled.")
            self.set_status("Fill all fields", "red")
            return
        if not os.path.isfile(orig):
            self.logger.error("Original PAK not found.")
            self.set_status("File not found", "red")
            return
        if not os.path.isdir(extr):
            self.logger.error("Extracted folder does not exist.")
            self.set_status("Folder not found", "red")
            return

        self.save_settings()
        self.btn_update.config(state='disabled')
        self.progress.start()
        self.set_status("Updating...", "blue")
        threading.Thread(target=self.run_update, args=(orig, extr, out), daemon=True).start()

    def run_update(self, orig, extr, out):
        try:
            update_pak_in_memory(orig, extr, out, self.logger)
            self.root.after(0, self.done, f"Done: {out}", "green")
        except Exception as e:
            self.logger.error(str(e))
            self.root.after(0, self.done, f"Error: {e}", "red")

    def done(self, msg, color):
        self.progress.stop()
        self.btn_update.config(state='normal')
        self.set_status(msg, color)

    def set_status(self, text, color="black"):
        self.status.config(text=text, foreground=color)

    def load_settings(self):
        cfg = configparser.ConfigParser()
        if not os.path.exists(CONFIG_FILE):
            return
        cfg.read(CONFIG_FILE, encoding='utf-8')
        try:
            if 'Settings' in cfg:
                s = cfg['Settings']
                if 'original_pak' in s and os.path.isfile(s['original_pak']):
                    self.original_pak.set(s['original_pak'])
                if 'extracted_dir' in s and os.path.isdir(s['extracted_dir']):
                    self.extracted_dir.set(s['extracted_dir'])
                if 'output_pak' in s:
                    self.output_pak.set(s['output_pak'])
                if 'log_level' in s:
                    lvl = int(s['log_level'])
                    if lvl in (LOG_DEBUG, LOG_INFO, LOG_ERROR):
                        self.log_level_var.set(lvl)
                        self.logger.change_level(lvl)
            self.logger.info("Settings loaded")
        except Exception as e:
            self.logger.error(f"Error loading settings: {e}")

    def save_settings(self):
        cfg = configparser.ConfigParser()
        cfg['Settings'] = {
            'original_pak': self.original_pak.get(),
            'extracted_dir': self.extracted_dir.get(),
            'output_pak': self.output_pak.get(),
            'log_level': str(self.log_level_var.get())
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            cfg.write(f)

    def on_closing(self):
        self.save_settings()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = PakUpdaterApp(root)
    root.mainloop()