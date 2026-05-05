import os
import struct
import threading
import configparser
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, ttk, scrolledtext

# ============================================================
# Конфигурация и логирование
# ============================================================
CONFIG_FILE = "pak_unpacker.ini"
LOG_FILE = "unpacker.log"

LOG_DEBUG = 10
LOG_INFO = 20
LOG_ERROR = 30

LEVEL_NAMES = {
    LOG_DEBUG: "Подробно",
    LOG_INFO:  "Кратко",
    LOG_ERROR: "Только ошибки"
}

class AppLogger:
    """Потокобезопасный логгер с записью в файл и выводом в GUI."""
    def __init__(self, root, gui_widget, level=LOG_INFO):
        self.root = root
        self.widget = gui_widget
        self.level = level
        self.lock = threading.Lock()
        self._init_file()

    def _init_file(self):
        with self.lock:
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S}, уровень: {LEVEL_NAMES.get(self.level, '?')} =====\n")

    def change_level(self, new_level):
        self.level = new_level
        self._log_text(f"Уровень логирования изменён на: {LEVEL_NAMES.get(new_level, new_level)}\n", "blue")

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

    def debug(self, message):
        self.log(message, LOG_DEBUG, color_tag="gray")

    def info(self, message):
        self.log(message, LOG_INFO, color_tag="black")

    def error(self, message):
        self.log(message, LOG_ERROR, color_tag="red")


# ============================================================
# Основная функция распаковки (исправленная)
# ============================================================
def unpack_pak(pak_path, output_base, logger):
    """
    Полностью повторяет алгоритм BMS-скрипта.
    """
    logger.info(f"Начало распаковки: {pak_path} -> {output_base}")
    try:
        with open(pak_path, 'rb') as f:
            # idstring "PAK\0"
            magic = f.read(4)
            logger.debug(f"Сигнатура: {magic!r}")
            if magic != b'PAK\x00':
                raise ValueError("Неверный формат файла: отсутствует сигнатура PAK\\0")

            # get DataOffset long
            DataOffset = struct.unpack('<I', f.read(4))[0]
            logger.debug(f"DataOffset = {DataOffset} (0x{DataOffset:X})")

            # get fileSize long
            fileSize = struct.unpack('<I', f.read(4))[0]
            logger.debug(f"fileSize = {fileSize}")

            # get dummy short
            dummy = struct.unpack('<H', f.read(2))[0]
            logger.debug(f"dummy = {dummy}")

            # get numFolder long
            numFolder = struct.unpack('<I', f.read(4))[0]
            logger.info(f"Количество элементов верхнего уровня: {numFolder}")

            archive_basename = os.path.basename(pak_path)
            output_root = os.path.join(output_base, archive_basename)
            os.makedirs(output_root, exist_ok=True)
            logger.debug(f"Создана корневая папка: {output_root}")

            # for i = 0 < numFolder
            for i in range(numFolder):
                folder_name = ""
                logger.debug(f"--- Обработка элемента #{i+1} верхнего уровня ---")
                _unpack_entry(f, output_root, folder_name, DataOffset, logger)

            logger.info("Распаковка успешно завершена.")
    except Exception as e:
        logger.error(f"Критическая ошибка при распаковке: {e}")
        raise


def _unpack_entry(f, output_root, folder_name, DataOffset, logger):
    """
    Рекурсивная функция (StartFunction unpack).
    ИСПРАВЛЕНИЕ: после чтения данных восстанавливаем позицию в архиве.
    """
    # get namelen byte
    namelen = struct.unpack('<B', f.read(1))[0]
    logger.debug(f"namelen = {namelen}")

    # getdstring name namelen
    name_bytes = f.read(namelen)
    name = name_bytes.decode('latin-1')
    logger.debug(f"name = '{name}'")

    # get type byte
    entry_type = struct.unpack('<B', f.read(1))[0]
    logger.debug(f"type = {entry_type}")

    if entry_type == 0 or entry_type == 2:
        # --- Тип 0 или 2: файл ---
        if entry_type == 0:
            # get offset long
            offset = struct.unpack('<I', f.read(4))[0]
            logger.debug(f"  offset (long) = {offset} (0x{offset:X})")
        else:  # type == 2
            # get offset double
            offset_raw = struct.unpack('<d', f.read(8))[0]
            offset = int(offset_raw)
            logger.debug(f"  offset (double) = {offset_raw} -> {offset} (0x{offset:X})")

        # get size long
        size = struct.unpack('<I', f.read(4))[0]
        logger.debug(f"  size = {size}")

        # get dummy long
        dummy = struct.unpack('<I', f.read(4))[0]
        logger.debug(f"  dummy = {dummy}")

        # math offset += Dataoffset
        abs_offset = DataOffset + offset
        logger.debug(f"  абсолютное смещение = {abs_offset} (0x{abs_offset:X})")

        # Формирование пути
        relative_path = folder_name + '/' + name
        while relative_path.startswith('/'):
            relative_path = relative_path[1:]
        full_path = os.path.join(output_root, relative_path)
        logger.info(f"Извлечение файла: {relative_path} (смещение={abs_offset}, размер={size})")

        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        # --- КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ ---
        # Сохраняем текущую позицию (сразу после чтения заголовка)
        saved_pos = f.tell()
        # log fname offset size
        f.seek(abs_offset)
        data = f.read(size)
        with open(full_path, 'wb') as out:
            out.write(data)
        # Возвращаем позицию обратно, как в оригинальном log
        f.seek(saved_pos)
        # ---------------------------------
        logger.debug(f"  записан файл: {full_path}")

    elif entry_type == 1:
        # --- Тип 1: папка ---
        # string folderName += /
        # string folderName += name
        new_folder = folder_name + '/' + name
        logger.debug(f"Вход в папку: {new_folder}")

        # get numEntry long
        numEntry = struct.unpack('<I', f.read(4))[0]
        logger.debug(f"  количество элементов в папке: {numEntry}")

        for j in range(numEntry):
            # set folderName2 string folderName
            folder_name_copy = new_folder
            _unpack_entry(f, output_root, folder_name_copy, DataOffset, logger)
        logger.debug(f"Выход из папки: {new_folder}")

    else:
        msg = f"Обнаружен неподдерживаемый тип элемента: {entry_type}"
        logger.error(msg)
        raise NotImplementedError(msg)


# ============================================================
# Графический интерфейс (без изменений, только интеграция логгера)
# ============================================================
class PakUnpackerApp:
    def __init__(self, root):
        self.root = root
        root.title("Shiro Games PAK Unpacker (Wartales)")
        root.resizable(True, True)
        root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.pak_path = tk.StringVar()
        self.output_path = tk.StringVar()
        self.log_level_var = tk.IntVar(value=LOG_INFO)

        self.setup_ui()
        self.load_settings()

    # ... (остальной код интерфейса идентичен предыдущему ответу)
    # Я приведу только ключевые методы, остальное без изменений.

    def setup_ui(self):
        tf = ttk.Frame(self.root, padding=5)
        tf.pack(fill=tk.X, side=tk.TOP)

        ttk.Label(tf, text="PAK файл:").grid(row=0, column=0, sticky='e', padx=5, pady=5)
        ttk.Entry(tf, textvariable=self.pak_path, width=50).grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(tf, text="Обзор...", command=self.browse_pak).grid(row=0, column=2, padx=5, pady=5)

        ttk.Label(tf, text="Папка для распаковки:").grid(row=1, column=0, sticky='e', padx=5, pady=5)
        ttk.Entry(tf, textvariable=self.output_path, width=50).grid(row=1, column=1, padx=5, pady=5)
        ttk.Button(tf, text="Обзор...", command=self.browse_output).grid(row=1, column=2, padx=5, pady=5)

        cf = ttk.Frame(self.root, padding=5)
        cf.pack(fill=tk.X, side=tk.TOP)

        self.extract_btn = ttk.Button(cf, text="Распаковать", command=self.start_extraction)
        self.extract_btn.grid(row=0, column=0, padx=5, pady=5)

        self.progress = ttk.Progressbar(cf, mode='indeterminate', length=200)
        self.progress.grid(row=0, column=1, padx=5, pady=5)

        self.status_label = ttk.Label(cf, text="Готов", foreground="gray")
        self.status_label.grid(row=0, column=2, padx=5, pady=5, sticky='w')

        lf = ttk.LabelFrame(self.root, text="Уровень логирования", padding=5)
        lf.pack(fill=tk.X, side=tk.TOP, padx=5, pady=5)

        ttk.Radiobutton(lf, text="Подробно", variable=self.log_level_var, value=LOG_DEBUG,
                        command=self.on_log_level_changed).pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(lf, text="Кратко", variable=self.log_level_var, value=LOG_INFO,
                        command=self.on_log_level_changed).pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(lf, text="Только ошибки", variable=self.log_level_var, value=LOG_ERROR,
                        command=self.on_log_level_changed).pack(side=tk.LEFT, padx=10)

        logf = ttk.LabelFrame(self.root, text="Лог", padding=5)
        logf.pack(fill=tk.BOTH, expand=True, side=tk.TOP, padx=5, pady=5)

        self.log_text = scrolledtext.ScrolledText(logf, height=15, state='disabled', wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.logger = AppLogger(self.root, self.log_text, level=self.log_level_var.get())

    def browse_pak(self):
        fn = filedialog.askopenfilename(title="Выберите PAK файл", filetypes=[("PAK files", "*.pak"), ("All files", "*.*")])
        if fn:
            self.pak_path.set(fn)
            self.logger.info(f"Выбран PAK: {fn}")

    def browse_output(self):
        dn = filedialog.askdirectory(title="Выберите папку для распаковки")
        if dn:
            self.output_path.set(dn)
            self.logger.info(f"Выбрана папка назначения: {dn}")

    def on_log_level_changed(self):
        new_level = self.log_level_var.get()
        self.logger.change_level(new_level)
        self.save_settings()

    def start_extraction(self):
        pak = self.pak_path.get()
        out = self.output_path.get()
        if not pak:
            self.logger.error("Не указан PAK файл.")
            self.set_status("Ошибка: укажите PAK файл", "red")
            return
        if not out:
            self.logger.error("Не указана папка для распаковки.")
            self.set_status("Ошибка: укажите папку назначения", "red")
            return
        if not os.path.isfile(pak):
            self.logger.error(f"Файл {pak} не существует.")
            self.set_status("Ошибка: файл не найден", "red")
            return

        self.save_settings()
        self.extract_btn.configure(state='disabled')
        self.progress.start()
        self.set_status("Идёт распаковка...", "blue")
        threading.Thread(target=self.run_extraction, args=(pak, out), daemon=True).start()

    def run_extraction(self, pak, out):
        try:
            unpack_pak(pak, out, self.logger)
            self.root.after(0, self.extraction_done, "Распаковка успешно завершена", "green")
        except Exception as e:
            self.root.after(0, self.extraction_done, f"Ошибка: {e}", "red")

    def extraction_done(self, message, color):
        self.progress.stop()
        self.extract_btn.configure(state='normal')
        self.set_status(message, color)

    def set_status(self, text, color="black"):
        self.status_label.config(text=text, foreground=color)

    def load_settings(self):
        config = configparser.ConfigParser()
        if not os.path.exists(CONFIG_FILE):
            return
        config.read(CONFIG_FILE, encoding='utf-8')
        try:
            if 'Settings' in config:
                sec = config['Settings']
                if 'pak_path' in sec and os.path.isfile(sec['pak_path']):
                    self.pak_path.set(sec['pak_path'])
                if 'output_path' in sec and os.path.isdir(sec['output_path']):
                    self.output_path.set(sec['output_path'])
                if 'log_level' in sec:
                    level = int(sec['log_level'])
                    if level in (LOG_DEBUG, LOG_INFO, LOG_ERROR):
                        self.log_level_var.set(level)
                        self.logger.change_level(level)
            self.logger.info("Настройки загружены")
        except Exception as e:
            self.logger.error(f"Ошибка загрузки настроек: {e}")

    def save_settings(self):
        config = configparser.ConfigParser()
        config['Settings'] = {
            'pak_path': self.pak_path.get(),
            'output_path': self.output_path.get(),
            'log_level': str(self.log_level_var.get())
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            config.write(f)

    def on_closing(self):
        self.save_settings()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = PakUnpackerApp(root)
    root.mainloop()