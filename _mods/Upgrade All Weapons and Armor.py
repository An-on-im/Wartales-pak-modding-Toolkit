import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

WEAPON_ARMOR_TYPES = {
    "Axe", "Axe2H", "Bow", "Crossbow", "Dagger", "FistWeapon",
    "Mace", "Mace2H", "Polearm", "Sword", "Sword2H",
    "Armor", "ArmorHeavy", "ArmorLight", "ArmorMedium",
    "Helmet", "HelmetHeavy", "HelmetLight", "HelmetMedium",
    "HorseArmor", "Shield"
}

def add_flags_to_items(obj, types_set):
    if isinstance(obj, dict):
        if 'type' in obj and obj['type'] in types_set:
            props = obj.get('props')
            if isinstance(props, dict) and 'flags' not in props:
                props['flags'] = 128
        for value in obj.values():
            add_flags_to_items(value, types_set)
    elif isinstance(obj, list):
        for item in obj:
            add_flags_to_items(item, types_set)

def process_file(input_path, output_path):
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        raise Exception(f"Error reading file: {e}")

    if not isinstance(data, dict) or 'sheets' not in data:
        raise Exception("Root key 'sheets' not found in file")

    add_flags_to_items(data['sheets'], WEAPON_ARMOR_TYPES)

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent='\t', ensure_ascii=False)
    except Exception as e:
        raise Exception(f"Error writing file: {e}")

    return output_path

class App:
    def __init__(self, root):
        self.root = root
        root.title("Add flags:128 to weapon/armor props")
        root.geometry("550x150")
        root.resizable(False, False)

        self.file_path = tk.StringVar()

        tk.Label(root, text="Select JSON file (e.g., data.cdb):").pack(pady=(10, 0))
        frame = tk.Frame(root)
        frame.pack(pady=5)
        tk.Entry(frame, textvariable=self.file_path, width=45).pack(side=tk.LEFT, padx=(0, 5))
        tk.Button(frame, text="Browse", command=self.browse_file).pack(side=tk.LEFT)

        tk.Button(root, text="Run", command=self.run_process,
                  bg="#2196F3", fg="white", padx=10, pady=5).pack(pady=15)

        self.status_label = tk.Label(root, text="Ready", fg="gray")
        self.status_label.pack(side=tk.BOTTOM, pady=5)

    def browse_file(self):
        filename = filedialog.askopenfilename(
            title="Select JSON file",
            filetypes=[("JSON files", "*.cdb *.json"), ("All files", "*.*")]
        )
        if filename:
            self.file_path.set(filename)

    def run_process(self):
        input_path = self.file_path.get().strip()
        if not input_path:
            messagebox.showerror("Error", "Please select a file!")
            return

        if not os.path.isfile(input_path):
            messagebox.showerror("Error", "File does not exist!")
            return

        input_path_obj = Path(input_path)
        output_path = input_path_obj.parent / f"{input_path_obj.stem}_modified{input_path_obj.suffix}"

        self.status_label.config(text="Processing...", fg="blue")
        self.root.update()

        try:
            process_file(input_path, output_path)
            self.status_label.config(text=f"Done! Saved to: {output_path}", fg="green")
            messagebox.showinfo("Success", f"File processed successfully.\nNew file:\n{output_path}")
        except Exception as e:
            self.status_label.config(text="Error", fg="red")
            messagebox.showerror("Error", str(e))

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()