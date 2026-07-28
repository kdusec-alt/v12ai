# -*- coding: utf-8 -*-
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_admin_ack_does_not_rerun_inside_fragment_callback():
    source = (ROOT / "v1068_runtime_patches.py").read_text(encoding="utf-8")
    start = source.index("def acknowledge_global_event_v1068")
    end = source.index("lifecycle.acknowledge_global_event =", start)
    callback = source[start:end]
    assert "st.rerun" not in callback
    assert "return True" in callback


def test_app_ack_path_has_no_forced_rerun():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    start = source.index('if event_id and st.button(')
    end = source.index("notice =", start)
    handler = source[start:end]
    assert "st.rerun()" not in handler
    assert "get_global_event_view()" in handler


def test_radar_rows_disclose_decision_role():
    source = (ROOT / "ui_v9_radar.py").read_text(encoding="utf-8")
    for key in ("Quantum 貢獻", "三大法人", "資券 / 融資融券", "外資期貨"):
        assert f'"{key}": "decision"' in source
    assert '"基本面": "reference"' in source
    assert "role-badge" in source
