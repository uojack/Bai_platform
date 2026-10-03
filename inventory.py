"""Local deployment inventory. Real addresses and evidence are not source code."""
import json
import os
from pathlib import Path

def initial_inventory():
    path=Path(os.environ.get('BAIPLAYER_INVENTORY', str(Path(__file__).parent/'private/inventory.local.json')))
    return json.loads(path.read_text()) if path.exists() else []
