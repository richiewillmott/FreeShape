"""FEATURE_PARITY.md is generated from freeshape/parity.py and must be kept current; every shared tool marked as
built must point at real code; every option's shared tool must exist."""
import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import parity as P  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_parity_doc_is_current():
    with open(os.path.join(ROOT, "FEATURE_PARITY.md"), encoding="utf-8") as fh:
        assert fh.read() == P.render_markdown(), "run: python -m freeshape.parity"


def test_shared_tools_point_at_real_code():
    for tid, (_name, _about, code) in P.SHARED.items():
        if code:
            mod, fn = code.split(":")
            assert hasattr(importlib.import_module(mod), fn), f"{tid}: {code} not found"


def test_options_use_known_tools_and_statuses():
    for fid, (_name, opts) in P.FEATURES.items():
        for opt, tool, st, _note in opts:
            assert tool is None or tool in P.SHARED, (fid, opt, tool)
            assert st in (P.DONE, P.PARTIAL, P.TODO, P.NA), (fid, opt, st)
    assert 0 < P.overall_parity() < 100
