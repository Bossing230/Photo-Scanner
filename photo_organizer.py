import argparse
import hashlib
import os
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime

from PIL import Image

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}

# EXIF tag numbers
TAG_MAKE = 0x010F
TAG_MODEL = 0x0110
TAG_DATETIME = 0x0132
TAG_EXIF_IFD = 0x8769
TAG_DATETIME_ORIGINAL = 0x9003

# ---------------------------------------------------------------- INPUT ----
def find_photos(folder):
    """Return a list of image file paths inside folder (including subfolders)."""
    photos = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            if os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS:
                photos.append(os.path.join(root, name))
    return sorted(photos)

def read_metadata(path):
    """Return (date, camera) for a photo. date is a datetime, camera is a string.

    Date comes from EXIF 'date taken'; if missing, the file's modified time is used.
    Camera comes from EXIF Make + Model; if missing, 'Unknown Camera'.
    """
    date = None
    camera = None
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            make = str(exif.get(TAG_MAKE, "")).strip()
            model = str(exif.get(TAG_MODEL, "")).strip()
            if make or model:
                # Avoid "Canon Canon EOS" when the model already contains the make
                camera = model if make and model.lower().startswith(make.lower()) else f"{make} {model}".strip()

            raw_date = None
            try:
                raw_date = exif.get_ifd(TAG_EXIF_IFD).get(TAG_DATETIME_ORIGINAL)
            except Exception:
                pass
            raw_date = raw_date or exif.get(TAG_DATETIME)
            if raw_date:
                date = datetime.strptime(str(raw_date).strip()[:19], "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass  # unreadable metadata: fall back below

    if date is None:
        date = datetime.fromtimestamp(os.path.getmtime(path))
    return date, camera or "Unknown Camera"

# -------------------------------------------------------------- PROCESS ----
def safe_name(text):
    """Make text safe to use as a folder name."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .") or "Unknown"

def target_folder(dest, date, camera, mode):
    if mode == "date":
        return os.path.join(dest, f"{date.year}", f"{date.year}-{date.month:02d}")
    return os.path.join(dest, safe_name(camera))

def unique_path(path):
    """If path exists, add _1, _2 ... so nothing is overwritten."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(f"{base}_{n}{ext}"):
        n += 1
    return f"{base}_{n}{ext}"

def organize(source, dest, mode="date", move=False, dry_run=False, log=print):
    """Sort photos from source into folders inside dest. Returns a summary dict."""
    photos = find_photos(source)
    counts = defaultdict(int)
    for path in photos:
        date, camera = read_metadata(path)
        folder = target_folder(dest, date, camera, mode)
        new_path = unique_path(os.path.join(folder, os.path.basename(path)))
        label = os.path.relpath(folder, dest)
        action = "MOVE" if move else "COPY"
        log(f"{'[dry run] ' if dry_run else ''}{action}: {os.path.basename(path)}  ->  {label}")
        if not dry_run:
            os.makedirs(folder, exist_ok=True)
            (shutil.move if move else shutil.copy2)(path, new_path)
        counts[label] += 1
    log(f"Done. {len(photos)} photo(s) into {len(counts)} folder(s).")
    return {"total": len(photos), "folders": dict(counts)}

def file_hash(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()

def find_duplicates(source):
    """Return a list of groups; each group is a list of identical files."""
    by_size = defaultdict(list)
    for path in find_photos(source):
        by_size[os.path.getsize(path)].append(path)  # same size first = fast

    by_hash = defaultdict(list)
    for paths in by_size.values():
        if len(paths) > 1:
            for p in paths:
                by_hash[file_hash(p)].append(p)
    return [group for group in by_hash.values() if len(group) > 1]

def handle_duplicates(source, move_to=None, log=print):
    """List duplicates. If move_to is given, move extra copies there (keeps the first)."""
    groups = find_duplicates(source)
    extras = 0
    for i, group in enumerate(groups, 1):
        log(f"Group {i}: keep {group[0]}")
        for dup in group[1:]:
            extras += 1
            if move_to:
                os.makedirs(move_to, exist_ok=True)
                shutil.move(dup, unique_path(os.path.join(move_to, os.path.basename(dup))))
                log(f"   moved duplicate: {dup}")
            else:
                log(f"   duplicate:       {dup}")
    log(f"Found {len(groups)} group(s), {extras} extra copy/copies."
        + (f" Moved to {move_to}." if move_to and extras else ""))
    return {"groups": len(groups), "extras": extras}

# --------------------------------------------------------------- OUTPUT ----
def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
    root = tk.Tk()
    root.title("Photo Organizer")
    root.geometry("720x560")
    source_var, dest_var, mode_var, move_var, dry_var = (
        tk.StringVar(), tk.StringVar(), tk.StringVar(value="date"), tk.BooleanVar(), tk.BooleanVar(value=True)
    )
    def pick(var):
        folder = filedialog.askdirectory()
        if folder:
            var.set(folder)
    def log(msg):
        output.insert(tk.END, msg + "\n")
        output.see(tk.END)
        root.update_idletasks()
    def need(*vars_):
        if not all(v.get() for v in vars_):
            messagebox.showwarning("Missing folder", "Please choose the folder(s) first.")
            return False
        return True
    def do_organize():
        if not need(source_var, dest_var):
            return
        output.delete("1.0", tk.END)
        organize(source_var.get(), dest_var.get(), mode_var.get(), move_var.get(), dry_var.get(), log)
    def do_duplicates():
        if not need(source_var):
            return
        output.delete("1.0", tk.END)
        handle_duplicates(source_var.get(), None, log)
    def do_move_duplicates():
        if not need(source_var, dest_var):
            return
        output.delete("1.0", tk.END)
        handle_duplicates(source_var.get(), os.path.join(dest_var.get(), "Duplicates"), log)
    pad = {"padx": 10, "pady": 5}
    frame = ttk.Frame(root)
    frame.pack(fill="x", **pad)
    ttk.Label(frame, text="Photo folder:").grid(row=0, column=0, sticky="w")
    ttk.Entry(frame, textvariable=source_var, width=60).grid(row=0, column=1, **pad)
    ttk.Button(frame, text="Browse", command=lambda: pick(source_var)).grid(row=0, column=2)
    ttk.Label(frame, text="Output folder:").grid(row=1, column=0, sticky="w")
    ttk.Entry(frame, textvariable=dest_var, width=60).grid(row=1, column=1, **pad)
    ttk.Button(frame, text="Browse", command=lambda: pick(dest_var)).grid(row=1, column=2)
    opts = ttk.LabelFrame(root, text="Sort photos")
    opts.pack(fill="x", **pad)
    ttk.Radiobutton(opts, text="By date (year / month)", variable=mode_var, value="date").pack(side="left", **pad)
    ttk.Radiobutton(opts, text="By camera", variable=mode_var, value="camera").pack(side="left", **pad)
    ttk.Checkbutton(opts, text="Move (instead of copy)", variable=move_var).pack(side="left", **pad)
    ttk.Checkbutton(opts, text="Preview only", variable=dry_var).pack(side="left", **pad)
    buttons = ttk.Frame(root)
    buttons.pack(fill="x", **pad)
    ttk.Button(buttons, text="Organize photos", command=do_organize).pack(side="left", **pad)
    ttk.Button(buttons, text="Find duplicates", command=do_duplicates).pack(side="left", **pad)
    ttk.Button(buttons, text="Move duplicates to 'Duplicates' folder", command=do_move_duplicates).pack(side="left", **pad)

    output = scrolledtext.ScrolledText(root, height=20)
    output.pack(fill="both", expand=True, **pad)
    log("Choose a photo folder and an output folder, then click a button.")
    root.mainloop()

def main():
    if len(sys.argv) == 1:
        run_gui()
        return
    parser = argparse.ArgumentParser(description="Sort photos by date or camera, and find duplicates.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("organize", help="sort photos into folders")
    p.add_argument("source")
    p.add_argument("dest")
    p.add_argument("--by", choices=["date", "camera"], default="date")
    p.add_argument("--move", action="store_true", help="move files instead of copying")
    p.add_argument("--dry-run", action="store_true", help="show what would happen only")
    d = sub.add_parser("duplicates", help="find duplicate photos")
    d.add_argument("source")
    d.add_argument("--move-to", help="move extra copies into this folder")
    args = parser.parse_args()
    if args.command == "organize":
        organize(args.source, args.dest, args.by, args.move, args.dry_run)
    else:
        handle_duplicates(args.source, args.move_to)

if __name__ == "__main__":
    main()