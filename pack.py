import os
import struct
import zlib
import threading
import configparser
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, ttk, scrolledtext

# ============================================================
# Конфигурация и логгер
# ============================================================
CONFIG_FILE = "pak_updater.ini"
LOG_FILE    = "pak_updater.log"

LOG_DEBUG = 10
LOG_INFO  = 20
LOG_ERROR = 30

LEVEL_NAMES = {
    LOG_DEBUG: "Подробно",
    LOG_INFO:  "Кратко",
    LOG_ERROR: "Только ошибки"
}

class AppLogger:
    """Потокобезопасный логгер с выводом в GUI и записью в файл."""
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
                        f"уровень: {LEVEL_NAMES.get(self.level, '?')} =====\n")

    def change_level(self, new_level):
        self.level = new_level
        self._log_text(f"Уровень логирования изменён на: "
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
# Основная функция обновления PAK
# ============================================================
def update_pak_in_memory(original_path, extracted_root, output_path, logger):
    """
    Загружает TOC в память, проходит по нему, заменяет изменённые файлы,
    формирует полный PAK в bytearray и сохраняет в output_path.
    """
    logger.info(f"Начало обновления: {original_path}")
    # Открываем оригинальный PAK
    with open(original_path, 'rb') as f:
        # --- Глобальный заголовок ---
        magic = f.read(4)
        if magic != b'PAK\x00':
            raise ValueError("Неверная сигнатура PAK")

        DataOffset = struct.unpack('<I', f.read(4))[0]   # смещение до данных
        fileSize   = struct.unpack('<I', f.read(4))[0]   # оригинальный размер
        global_dummy = struct.unpack('<H', f.read(2))[0] # 2 байта
        numFolder  = struct.unpack('<I', f.read(4))[0]   # число элементов верхнего уровня

        # Читаем весь TOC (оглавление) в память
        toc_size = DataOffset - 18
        toc_bytes = bytearray(f.read(toc_size))

    # Создаём выходной буфер: заголовок + TOC
    # Заголовок пока заполним оригинальными значениями, fileSize обновим позже
    header = bytearray(18)
    header[0:4] = magic
    struct.pack_into('<I', header, 4, DataOffset)
    # fileSize пока 0, запишем в конце
    struct.pack_into('<H', header, 12, global_dummy)
    struct.pack_into('<I', header, 14, numFolder)

    # Выходной буфер будет содержать заголовок + TOC, потом данные
    out_buf = bytearray(header)
    out_buf.extend(toc_bytes)  # добавляем оригинальный TOC (будем модифицировать прямо в нём)

    # Смещение TOC в out_buf (после заголовка)
    TOC_START = 18
    data_buf = bytearray()   # собираем здесь область данных

    # Текущее смещение в данных (относительно DataOffset)
    current_data_offset = 0

    # Рекурсивный обход TOC в out_buf с одновременным чтением из оригинального PAK для неизменённых файлов
    orig_file = open(original_path, 'rb')

    def read_orig_data(offset, size):
        """Чтение оригинальных данных файла по абсолютному смещению."""
        orig_file.seek(offset)
        return orig_file.read(size)

    def process_entries(toc_idx, count, parent_path=""):
        """Обрабатывает count элементов, начиная с toc_idx в out_buf.
        Возвращает новый toc_idx после обработки всех count элементов."""
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

            if etype == 1:  # папка
                numEntry = struct.unpack_from('<I', out_buf, idx)[0]
                idx += 4
                new_path = parent_path + "/" + name if parent_path else name
                idx = process_entries(idx, numEntry, new_path)
            elif etype in (0, 2):  # файл
                # Запомнили позицию offset-поля в out_buf
                offset_pos = idx
                # Читаем оригинальные поля
                if etype == 0:
                    orig_offset = struct.unpack_from('<I', out_buf, idx)[0]
                    idx += 4
                else:  # type 2
                    orig_offset = int(struct.unpack_from('<d', out_buf, idx)[0])
                    idx += 8
                size_pos = idx
                orig_size = struct.unpack_from('<I', out_buf, idx)[0]
                idx += 4
                dummy_pos = idx
                orig_dummy = struct.unpack_from('<I', out_buf, idx)[0]
                idx += 4

                # Формируем относительный путь
                rel_path = parent_path + "/" + name if parent_path else name
                # Проверяем наличие файла на диске
                disk_full_path = os.path.join(extracted_root, rel_path)
                modified = False
                if os.path.isfile(disk_full_path):
                    with open(disk_full_path, 'rb') as disk_f:
                        disk_data = disk_f.read()
                    new_adler = zlib.adler32(disk_data) & 0xFFFFFFFF
                    new_size = len(disk_data)
                    if new_adler != orig_dummy or new_size != orig_size:
                        modified = True
                    else:
                        # Не изменён, но на всякий случай проверим
                        pass
                else:
                    # Файл отсутствует в распакованной папке – оставляем оригинал
                    logger.info(f"Файл отсутствует на диске: {rel_path}, оставлен оригинал.")
                    modified = False

                # Вычисляем новый offset для этого файла
                new_offset = current_data_offset
                # Записываем offset в out_buf
                if etype == 0:
                    struct.pack_into('<I', out_buf, offset_pos, new_offset)
                else:
                    struct.pack_into('<d', out_buf, offset_pos, float(new_offset))

                if modified:
                    logger.info(f"Изменён: {rel_path} (новый размер={new_size})")
                    # Записываем size и dummy
                    struct.pack_into('<I', out_buf, size_pos, new_size)
                    struct.pack_into('<I', out_buf, dummy_pos, new_adler)
                    # Добавляем новые данные в data_buf
                    data_buf.extend(disk_data)
                    current_data_offset += new_size
                else:
                    logger.debug(f"Не изменён: {rel_path}")
                    # size и dummy не меняем (они уже правильные)
                    # Но нужно обновить? Если файл не изменён, но предыдущие файлы изменились,
                    # то его offset уже обновлён, а size и dummy остаются прежними.
                    # Это корректно.
                    # Читаем оригинальные данные по старому смещению
                    orig_abs = DataOffset + orig_offset
                    orig_data = read_orig_data(orig_abs, orig_size)
                    data_buf.extend(orig_data)
                    current_data_offset += orig_size
            else:
                raise ValueError(f"Неизвестный тип элемента {etype}")
        return idx

    # Запускаем обход
    process_entries(TOC_START, numFolder, "")

    # Теперь в data_buf все данные в правильном порядке.
    # Добавляем их к out_buf начиная с позиции DataOffset.
    # Так как out_buf уже содержит заголовок и TOC, нужно дописать данные.
    # out_buf должен быть длины DataOffset + len(data_buf)
    # assert len(out_buf) == DataOffset, "TOC размер должен точно соответствовать DataOffset"
    out_buf.extend(data_buf)

    # Вычисляем итоговый размер файла
    final_size = len(out_buf)
    # Обновляем fileSize в заголовке (смещение 8)
    struct.pack_into('<I', out_buf, 8, final_size)

    # Записываем результат в output_path
    with open(output_path, 'wb') as f:
        f.write(out_buf)

    orig_file.close()
    logger.info(f"Готово. Новый PAK сохранён: {output_path}, размер={final_size} байт.")


# ============================================================
# GUI приложение
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

        ttk.Label(top, text="Оригинальный PAK:").grid(row=0, column=0, sticky='e', padx=5, pady=5)
        ttk.Entry(top, textvariable=self.original_pak, width=50).grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(top, text="Обзор...", command=self.browse_original).grid(row=0, column=2, padx=5, pady=5)

        ttk.Label(top, text="Распакованная папка:").grid(row=1, column=0, sticky='e', padx=5, pady=5)
        ttk.Entry(top, textvariable=self.extracted_dir, width=50).grid(row=1, column=1, padx=5, pady=5)
        ttk.Button(top, text="Обзор...", command=self.browse_extracted).grid(row=1, column=2, padx=5, pady=5)

        ttk.Label(top, text="Сохранить как:").grid(row=2, column=0, sticky='e', padx=5, pady=5)
        ttk.Entry(top, textvariable=self.output_pak, width=50).grid(row=2, column=1, padx=5, pady=5)
        ttk.Button(top, text="Обзор...", command=self.browse_output).grid(row=2, column=2, padx=5, pady=5)

        ctrl = ttk.Frame(self.root, padding=5)
        ctrl.pack(fill=tk.X, side=tk.TOP)

        self.btn_update = ttk.Button(ctrl, text="Обновить архив", command=self.start_update)
        self.btn_update.grid(row=0, column=0, padx=5, pady=5)

        self.progress = ttk.Progressbar(ctrl, mode='indeterminate', length=200)
        self.progress.grid(row=0, column=1, padx=5, pady=5)

        self.status = ttk.Label(ctrl, text="Готов", foreground="gray")
        self.status.grid(row=0, column=2, padx=5, pady=5, sticky='w')

        # Уровень логирования
        lvl_frame = ttk.LabelFrame(self.root, text="Уровень логирования", padding=5)
        lvl_frame.pack(fill=tk.X, side=tk.TOP, padx=5, pady=5)

        for val, txt in [(LOG_DEBUG, "Подробно"), (LOG_INFO, "Кратко"), (LOG_ERROR, "Только ошибки")]:
            ttk.Radiobutton(lvl_frame, text=txt, variable=self.log_level_var, value=val,
                            command=self.on_loglevel_changed).pack(side=tk.LEFT, padx=10)

        # Лог
        log_frame = ttk.LabelFrame(self.root, text="Лог", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, side=tk.TOP, padx=5, pady=5)

        self.log_widget = scrolledtext.ScrolledText(log_frame, height=15, state='disabled', wrap=tk.WORD)
        self.log_widget.pack(fill=tk.BOTH, expand=True)

        self.logger = AppLogger(self.root, self.log_widget, level=self.log_level_var.get())

    def browse_original(self):
        path = filedialog.askopenfilename(title="Оригинальный PAK", filetypes=[("PAK files", "*.pak"), ("All files", "*.*")])
        if path:
            self.original_pak.set(path)
            # Автоматически предложим выходной путь (рядом, с суффиксом _updated)
            base, ext = os.path.splitext(path)
            self.output_pak.set(base + "_updated" + ext)

    def browse_extracted(self):
        path = filedialog.askdirectory(title="Распакованная папка")
        if path:
            self.extracted_dir.set(path)

    def browse_output(self):
        path = filedialog.asksaveasfilename(title="Сохранить обновлённый PAK", defaultextension=".pak",
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
            self.logger.error("Не все поля заполнены.")
            self.set_status("Заполните все поля", "red")
            return
        if not os.path.isfile(orig):
            self.logger.error("Оригинальный PAK не найден.")
            self.set_status("Файл не найден", "red")
            return
        if not os.path.isdir(extr):
            self.logger.error("Распакованная папка не существует.")
            self.set_status("Папка не найдена", "red")
            return

        self.save_settings()
        self.btn_update.config(state='disabled')
        self.progress.start()
        self.set_status("Обновление...", "blue")
        threading.Thread(target=self.run_update, args=(orig, extr, out), daemon=True).start()

    def run_update(self, orig, extr, out):
        try:
            update_pak_in_memory(orig, extr, out, self.logger)
            self.root.after(0, self.done, f"Готово: {out}", "green")
        except Exception as e:
            self.logger.error(str(e))
            self.root.after(0, self.done, f"Ошибка: {e}", "red")

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
            self.logger.info("Настройки загружены")
        except Exception as e:
            self.logger.error(f"Ошибка загрузки настроек: {e}")

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