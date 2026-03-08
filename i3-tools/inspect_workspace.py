import i3ipc
import json
import subprocess
import re
import sys

def clean_text(text):
    if not text: return ""
    return re.sub(r"[\ud800-\udfff]", "", text)

def get_wezterm_panes():
    try:
        res = subprocess.run(["wezterm", "cli", "list", "--format", "json"], capture_output=True, text=True)
        return json.loads(res.stdout)
    except Exception as e:
        return {"error": str(e)}

def get_pane_text(pane_id):
    try:
        res = subprocess.run(["wezterm", "cli", "get-text", "--pane-id", str(pane_id), "--start-line", "-50"], capture_output=True, text=True)
        return clean_text(res.stdout)
    except Exception:
        return ""

def get_best_wezterm_pane(leaf_title, wez_panes):
    # Extract tab info from title like "[1/6] ..."
    match = re.match(r"^\[(\d+)/(\d+)\]", leaf_title)
    if not match:
        return None
    
    current_tab_idx = int(match.group(1))
    total_tabs = int(match.group(2))
    
    # Group panes by window_id
    wez_windows = {}
    for pane in wez_panes:
        wid = pane.get("window_id")
        if wid not in wez_windows:
            wez_windows[wid] = {}
        tid = pane.get("tab_id")
        if tid not in wez_windows[wid]:
            wez_windows[wid][tid] = []
        wez_windows[wid][tid].append(pane)
    
    # Find windows with the correct total tab count
    candidate_windows = []
    for wid, tabs in wez_windows.items():
        if len(tabs) == total_tabs:
            candidate_windows.append(wid)
            
    if not candidate_windows:
        return None
        
    # For each candidate window, find the pane in the correct tab index
    # We sort tabs by their ID to get a consistent 1-based index
    for wid in candidate_windows:
        tabs = wez_windows[wid]
        sorted_tab_ids = sorted(tabs.keys())
        target_tab_id = sorted_tab_ids[current_tab_idx - 1]
        
        # In that tab, find the active pane
        for pane in tabs[target_tab_id]:
            if pane.get("is_active"):
                return pane
                
    return None

def inspect(ws_num):
    i3 = i3ipc.Connection()
    tree = i3.get_tree()
    wez_panes = get_wezterm_panes()
    
    ws_node = next((n for n in tree.workspaces() if n.num == ws_num), None)
    if not ws_node:
        return {"error": f"Workspace {ws_num} not found"}

    metadata = {
        "workspace_num": ws_num,
        "windows": []
    }

    for leaf in ws_node.leaves():
        win_data = {
            "title": leaf.name,
            "class": leaf.window_class,
        }
        
        if "wezterm" in (leaf.window_class or "").lower():
            pane = get_best_wezterm_pane(leaf.name, wez_panes)
            if pane:
                win_data["matched_pane"] = {
                    "pane_id": pane["pane_id"],
                    "cwd": pane.get("cwd"),
                    "title": pane.get("title"),
                    "text_sample": get_pane_text(pane["pane_id"])[:200]
                }
            else:
                win_data["error"] = "Could not match WezTerm pane by tab count"
                
        metadata["windows"].append(win_data)
    
    return metadata

if __name__ == "__main__":
    target = 1
    if len(sys.argv) > 1:
        target = int(sys.argv[1])
    
    print(json.dumps(inspect(target), indent=2))
