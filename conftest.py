"""Test paths. The engine lives in jarvis/, the robot's own modules in fetch/, the research scripts in research/;
the test files import them by bare name (`import decision`), so put all three on the path once, here, and the whole
suite runs from anywhere with `python -m pytest tests -q`."""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
for d in ("jarvis", "fetch", "research", "."):
    p = os.path.join(ROOT, d)
    if p not in sys.path:
        sys.path.insert(0, p)


# The suite must leave data/ exactly as it found it. On 28 Nov 2026 a test run quietly incremented the
# observation counter "n" on 1010 live entries of data/auto/drift.json and moved their lastAt, because the
# drift and closing tests call the real writers and the real writers write to the real folder. In CI the
# tests run every hour before the robot's own fetch, so the stored record was being nudged every hour. This
# fixture snapshots every file under data/ before the session, restores any the tests changed, and reports
# them, so a test that writes where it should not is a visible failure instead of a silent edit.
import hashlib                                                                              # noqa: E402
import shutil                                                                               # noqa: E402

import pytest                                                                               # noqa: E402

DATA_DIR = os.path.join(ROOT, "data")


def _snapshot():
    out = {}
    for base, _dirs, files in os.walk(DATA_DIR):
        for f in files:
            p = os.path.join(base, f)
            with open(p, "rb") as fh:
                out[p] = hashlib.sha256(fh.read()).hexdigest()
    return out


@pytest.fixture(scope="session", autouse=True)
def data_folder_is_left_alone(tmp_path_factory):
    if not os.path.isdir(DATA_DIR):
        yield
        return
    before = _snapshot()
    keep = tmp_path_factory.mktemp("data-before")
    for p in before:
        dst = os.path.join(keep, os.path.relpath(p, DATA_DIR))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(p, dst)
    yield
    touched = sorted(os.path.relpath(p, DATA_DIR) for p, h in _snapshot().items()
                     if before.get(p) != h)
    # A file the tests created for themselves is scratch, not the record: it is removed below and does not
    # fail the run. Anything the robot actually reads does.
    scratch = [t for t in touched if os.path.basename(t).startswith("_test_")]
    touched = [t for t in touched if t not in scratch]
    for rel in touched + scratch:             # put the stored record back whatever the tests did to it
        src, dst = os.path.join(keep, rel), os.path.join(DATA_DIR, rel)
        if os.path.exists(src):
            shutil.copy2(src, dst)
        elif os.path.exists(dst):
            os.remove(dst)
    if touched:
        pytest.fail("tests wrote into data/: " + ", ".join(touched) + " (restored; write to a tmp_path instead)")
