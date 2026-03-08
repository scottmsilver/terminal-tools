import i3ipc
import json

def get_workspace_windows(workspace_name=None):
    i3 = i3ipc.Connection()
    tree = i3.get_tree()
    
    if workspace_name is None:
        focused_workspace = next(ws for ws in i3.get_workspaces() if ws.focused)
        workspace_name = focused_workspace.name

    workspace_node = tree.find_named(workspace_name)
    if not workspace_node:
        return []

    windows = []
    # Find all windows in the workspace node
    for leaf in workspace_node[0].leaves():
        windows.append({
            "id": leaf.id,
            "window_class": leaf.window_class,
            "window_instance": leaf.window_instance,
            "name": leaf.name, # Title
            "focused": leaf.focused
        })
    
    return windows

if __name__ == "__main__":
    windows = get_workspace_windows()
    print(json.dumps(windows, indent=2))
