"""Run notebooks/pdc_evidence_explorer.ipynb headless with nbclient, to validate it (not used by the interfaces).

Live (production, read-only; needs MONTANDON_API_TOKEN, keep the query small):
    uv run --env-file .env python scripts/run_notebook.py --countries PHL,BGD,NPL --years 2024-2024

Offline (no network): the notebook's requests are answered by tests/fake_stac.py from a pool of items:
    uv run python scripts/run_notebook.py --offline synthetic --countries PHL,BGD
    uv run python scripts/run_notebook.py --offline path/to/pool.json.gz --countries PHL,BGD,NPL

The executed copy is written to outputs/notebook/ (ignored). ``--dump CELL_ID`` also pickles the notebook's tables
after that cell, for scripts/compare_notebook_dashboard.py. The token is never printed.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time

import nbformat
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError

PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / "outputs" / "notebook"
sys.path.insert(0, str(PROJECT))

# Injected before the first cell: serve the notebook from the fake STAC server (offline runs only) and record every figure shown.
OFFLINE_SETUP = """
import sys
sys.path.insert(0, {project!r})
from tests.fake_stac import install_from_environment
FAKE = install_from_environment()
"""
RECORD_FIGURES = """
import plotly.graph_objects as go
SHOWN = []
_show = go.Figure.show
def _record(self, *args, **kwargs):
    SHOWN.append(self.to_dict())
    return _show(self, *args, **kwargs)
go.Figure.show = _record
"""
INVENTORY = """
import json as _json
print("FIGURES", len(SHOWN))
for _number, _fig in enumerate(SHOWN):
    _kinds = sorted({t.get("type") for t in _fig.get("data", [])})
    _notes = [a.get("text") for a in _fig.get("layout", {}).get("annotations", []) if a.get("text")]
    print("FIG", _number, _json.dumps({"traces": len(_fig.get("data", [])), "types": _kinds, "annotation": _notes[:1]}))
"""
DUMP = """
import pickle
pickle.dump({{"TABLES": TABLES, "SUMMARIES": SUMMARIES, "RETRIEVAL_LOG": RETRIEVAL_LOG, "QUERIES": QUERIES, "COUNTRIES": COUNTRIES}}, open({path!r}, "wb"))
"""


def code_cell(source: str, cell_id: str):
    cell = nbformat.v4.new_code_cell(source)
    cell["id"] = cell_id
    return cell


def filter_overrides(args: argparse.Namespace) -> str:
    """Code that sets the filter widgets the way the command line asks (the notebook itself stays untouched)."""
    first, last = (int(value) for value in args.years.split("-"))
    lines = [f"countries_choice.value = {tuple(args.countries.split(','))!r}", f"years_choice.value = ({first}, {last})"]
    if args.months:
        lines.append(f"for number, button in enumerate(month_buttons, start=1): button.value = number in {sorted(int(m) for m in args.months.split(','))!r}")
    if args.hazards:
        lines.append(f"hazards_choice.value = {tuple(args.hazards.split(','))!r}")
    lines += [f"measure_boxes[{name!r}].value = False" for name in filter(None, args.without.split(","))]
    if args.age:
        lines.append("measure_boxes['age_groups'].value = True")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--notebook", default=str(PROJECT / "notebooks" / "pdc_evidence_explorer.ipynb"))
    parser.add_argument("--countries", default="PHL", help="comma separated ISO3 codes (1-5)")
    parser.add_argument("--years", default="2024-2024", help="first-last year, at most 5 years")
    parser.add_argument("--months", default="", help="comma separated month numbers (default: all)")
    parser.add_argument("--hazards", default="", help="comma separated hazard group names (default: all)")
    parser.add_argument("--without", default="", help="measures to switch off: households,schools,hospitals,capital")
    parser.add_argument("--age", action="store_true", help="also retrieve the 14 age groups")
    parser.add_argument("--offline", default="", help="'synthetic' or the path of a pool of items; answers requests from tests/fake_stac.py")
    parser.add_argument("--failures", default="[]", help="offline only: JSON list of failure rules for tests/fake_stac.py")
    parser.add_argument("--stop-after", default="", help="id of the last cell to run")
    parser.add_argument("--dump", default="", help="id of the cell after which the notebook's tables are pickled")
    parser.add_argument("--out", default=str(OUTPUT / "pdc_evidence_explorer.executed.ipynb"))
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)

    if args.offline:
        from tests.fake_stac import FAILURES_VARIABLE, POOL_VARIABLE, dump_pool, synthetic_pool

        pool = args.offline
        if pool == "synthetic":
            pool = str(OUTPUT / "synthetic_pool.json")
            dump_pool(synthetic_pool(tuple(args.countries.split(",")), events=12), pool)
        os.environ[POOL_VARIABLE], os.environ[FAILURES_VARIABLE] = pool, args.failures
        os.environ.setdefault("MONTANDON_API_TOKEN", "offline-test-token")
    elif not os.environ.get("MONTANDON_API_TOKEN"):
        print("Live run: set MONTANDON_API_TOKEN (for example: uv run --env-file .env python scripts/run_notebook.py ...).")
        return 2

    notebook = nbformat.read(args.notebook, as_version=4)
    cells = []
    for cell in notebook.cells:
        cells.append(cell)
        if cell.get("id") == "filters":
            cells.append(code_cell(filter_overrides(args), "run-overrides"))
        if args.stop_after and cell.get("id") == args.stop_after:
            break
    if args.dump:
        position = next(index for index, cell in enumerate(cells) if cell.get("id") == args.dump)
        cells.insert(position + 1, code_cell(DUMP.format(path=str(OUTPUT / "tables.pkl")), "run-dump"))
    head = [code_cell(RECORD_FIGURES, "run-record")]
    if args.offline:
        head.insert(0, code_cell(OFFLINE_SETUP.format(project=str(PROJECT)), "run-offline"))
    notebook.cells = [*head, *cells, code_cell(INVENTORY, "run-inventory")]

    client = NotebookClient(notebook, timeout=1800, kernel_name="python3", resources={"metadata": {"path": str(PROJECT / "notebooks")}})
    started, status = time.time(), 0
    try:
        client.execute()
    except CellExecutionError as error:
        print("CELL FAILED:\n", str(error)[-6000:])
        status = 1
    print(f"executed in {time.time() - started:.0f} s; executed copy: {args.out}")
    nbformat.write(notebook, args.out)
    for cell in notebook.cells:
        for output in cell.get("outputs", []) if cell.cell_type == "code" else []:
            if output.output_type == "stream" and (output.text.startswith(("FIG", "Retrieved", "WARNING", "Every window")) or "Token found" in output.text):
                print(output.text.rstrip()[:600])
    return status


if __name__ == "__main__":
    sys.exit(main())
