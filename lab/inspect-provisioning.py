"""Inspect upstream provisioning without starting, stopping, settling or replaying."""
import json
from pathlib import Path
from provisioning_inspection import inspect_state

if __name__=='__main__':
    state=Path(__file__).resolve().parents[1]/'.lab/upstream'
    print(json.dumps(inspect_state(state),indent=2))
