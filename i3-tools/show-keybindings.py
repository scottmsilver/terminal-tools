#!/usr/bin/env python3
"""Floating keybinding cheat sheet. Press Escape or q to close."""

import tkinter as tk

SECTIONS = [
    ("Navigation", [
        ("Mod+j / Left", "Focus left"),
        ("Mod+k / Down", "Focus down"),
        ("Mod+l / Up", "Focus up"),
        ("Mod+; / Right", "Focus right"),
        ("Mod+a", "Focus parent"),
        ("Mod+space", "Toggle tiling/float focus"),
    ]),
    ("Windows", [
        ("Mod+Shift+q", "Kill window"),
        ("Mod+h / v", "Split horiz / vert"),
        ("Mod+f", "Fullscreen"),
        ("Mod+s / w / e", "Stack / Tab / Split"),
        ("Mod+Shift+space", "Toggle floating"),
        ("Mod+r", "Resize mode"),
    ]),
    ("Workspaces", [
        ("Mod+1-0", "Switch (again = back)"),
        ("Mod+Shift+1-0", "Move window to ws"),
        ("Mod+Ctrl+1-0", "Move + follow"),
    ]),
    ("Marks", [
        ("Mod+m, letter", "Mark this window"),
        ("Mod+', letter", "Jump to marked window"),
    ]),
    ("Tools", [
        ("Mod+Return", "New terminal"),
        ("Mod+d", "App launcher (rofi)"),
        ("Mod+Tab", "Window switcher (rofi)"),
        ("Mod+c", "Clipboard history"),
        ("Mod+u", "Jump to urgent"),
        ("Mod+Shift+w", "Fix workspaces"),
        ("Mod+Shift+n", "Clear notifications"),
        ("Mod+Shift+d", "DPI switcher"),
    ]),
    ("Scratchpad / System", [
        ("Mod+minus", "Send to scratchpad"),
        ("Mod+plus", "Show scratchpad"),
        ("Mod+Shift+c", "Reload config"),
        ("Mod+Shift+r", "Restart i3"),
        ("Mod+Shift+e", "Exit i3"),
    ]),
]

BG = "#282a36"
FG = "#f8f8f2"
HEADING = "#bd93f9"
KEY_COLOR = "#8be9fd"
SEP = "#44475a"


def main():
    root = tk.Tk()
    root.title("Keybindings")
    root.configure(bg=BG)
    root.attributes("-topmost", True)
    root.bind("<Escape>", lambda e: root.destroy())
    root.bind("q", lambda e: root.destroy())
    root.bind("<FocusOut>", lambda e: root.destroy())
    root.focus_force()

    # Title
    tk.Label(
        root, text="i3 Keybindings", font=("Noto Sans", 14, "bold"),
        bg=BG, fg=HEADING, pady=8,
    ).grid(row=0, column=0, columnspan=2, sticky="w", padx=16)

    # 2 columns, 3 sections each
    cols = [SECTIONS[:3], SECTIONS[3:]]

    for col_idx, col_sections in enumerate(cols):
        frame = tk.Frame(root, bg=BG, padx=16, pady=4)
        frame.grid(row=1, column=col_idx, sticky="n", padx=(0, 16))

        for sec_idx, (title, bindings) in enumerate(col_sections):
            if sec_idx > 0:
                tk.Frame(frame, bg=SEP, height=1).pack(fill="x", pady=8)

            tk.Label(
                frame, text=title, font=("Noto Sans", 11, "bold"),
                bg=BG, fg=HEADING, anchor="w",
            ).pack(anchor="w")

            for key, desc in bindings:
                row_frame = tk.Frame(frame, bg=BG)
                row_frame.pack(fill="x", pady=1)
                tk.Label(
                    row_frame, text=key, font=("monospace", 10),
                    bg=BG, fg=KEY_COLOR, width=20, anchor="w",
                ).pack(side="left")
                tk.Label(
                    row_frame, text=desc, font=("Noto Sans", 10),
                    bg=BG, fg=FG, anchor="w",
                ).pack(side="left", padx=(4, 0))

    # Bottom hint
    tk.Label(
        root, text="Press Esc or q to close", font=("Noto Sans", 9),
        bg=BG, fg=SEP, pady=8,
    ).grid(row=2, column=0, columnspan=2)

    # Center on screen
    root.update_idletasks()
    w = root.winfo_width()
    h = root.winfo_height()
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"+{x}+{y}")

    root.mainloop()


if __name__ == "__main__":
    main()
