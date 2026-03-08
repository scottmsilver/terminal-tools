# AI-Powered i3 Workspace Namer

An intelligent utility that automatically renames i3 window manager workspaces based on their content. It uses local LLMs (Gemma 3) via Ollama to analyze terminal context, Git repositories, and even visual screenshots.

## Features

- **Multimodal Analysis**: Uses screenshots to understand non-terminal applications.
- **Terminal Aware**: Surgically extracts CWD and terminal text from WezTerm panes.
- **Git Intelligent**: Automatically identifies canonical repository names, including support for Git Worktrees.
- **Deterministic Mapping**: Uses a "Focus Probe" technique to 100% accurately map WezTerm windows to i3 workspaces.
- **Privacy First**: Runs entirely locally using Ollama and Gemma 3.
- **Non-Disruptive**: Operates in the background using container IDs for renaming.

## Prerequisites

- **i3wm**
- **WezTerm**
- **Ollama** (running `gemma3:12b` or `gemma3:4b`)
- **ImageMagick** (for screenshots)
- **Python 3.10+** with `i3ipc` and `requests`

## Installation

1. Clone this repository into your tools directory.
2. Install the required Python packages:
   ```bash
   pip install -r requirements.txt
   ```
3. Ensure Ollama is running and you have the models pulled:
   ```bash
   ollama pull gemma3:12b
   ollama pull gemma3:4b
   ```

## Usage

### One-time Rename
To rename all active workspaces once:
```bash
./workspace_namer.py
```

### Stashing Context
To capture screenshots for visual analysis (optional but recommended for non-terminal workspaces):
```bash
# This cycles through workspaces once to capture state
mkdir -p /tmp/i3_shots
python3 -c "import i3ipc, subprocess, time; i3=i3ipc.Connection(); orig=next(ws for ws in i3.get_workspaces() if ws.focused).name; [ (i3.command(f'workspace {ws.name}'), time.sleep(0.2), subprocess.run(['import', '-window', 'root', '-resize', '1024', f'/tmp/i3_shots/{ws.num}.jpg'])) for ws in i3.get_workspaces() ]; i3.command(f'workspace {orig}')"
```

## Debugging

This project includes a web-based debug viewer to see exactly what metadata and screenshots are being sent to the LLM.

1. Run the namer script.
2. Start the debug server:
   ```bash
   python3 -m http.server 9999 --bind 127.0.0.1
   ```
3. Open **[http://localhost:9999/debug.html](http://localhost:9999/debug.html)** in your browser.

## Project Structure

- `workspace_namer.py`: The main naming utility.
- `inspect_workspace.py`: A tool to output raw metadata for a specific workspace.
- `debug.html`: Web UI for inspecting LLM payloads.
- `requirements.txt`: Python dependencies.
