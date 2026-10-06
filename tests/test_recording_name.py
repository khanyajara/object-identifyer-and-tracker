from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import streamlit_app as app
from streamlit.testing.v1 import AppTest
from services.dual_session_service import build_video_metadata


def test_single_name_is_saved_without_renaming_file():
    manager = SimpleNamespace(record={"video_id": "v", "filename": "original.mp4"})
    with patch.object(app, "VideoService") as service:
        app.name_completed_recording(manager, "  Morning drive  ")
    assert manager.record["title"] == "Morning drive"
    assert manager.record["filename"] == "original.mp4"
    service.return_value.save.assert_called_once_with(manager.record)


@pytest.mark.parametrize("name", [" ", "x" * 121])
def test_invalid_name_does_not_save(name):
    manager = SimpleNamespace(record={})
    with patch.object(app, "VideoService") as service, pytest.raises(ValueError):
        app.name_completed_recording(manager, name)
    service.assert_not_called()


def test_dual_name_survives_metadata_processing():
    manager = app.DualCameraUIManager.__new__(app.DualCameraUIManager)
    manager.session = {"cameras": [{"video_id": "front", "role": "front", "label": "Main"},
                                  {"video_id": "rear", "role": "rear", "label": "Cabin"}]}
    with patch.object(app.dual_session_service, "save_session") as save:
        app.name_completed_recording(manager, "Trip A")
    save.assert_called_once_with(manager.session)
    metadata = [build_video_metadata(manager.session, camera, []) for camera in manager.session["cameras"]]
    assert [item["title"] for item in metadata] == ["Trip A · Main", "Trip A · Cabin"]
    assert all(item["document_name"] == "Trip A" for item in metadata)


def test_popup_saves_name_then_processes_already_stopped_recording():
    script = '''
import streamlit as st
from types import SimpleNamespace
from unittest.mock import patch, Mock
import streamlit_app as app
if "initialized" not in st.session_state:
    st.session_state.initialized = True
    st.session_state.manager = SimpleNamespace(record={"video_id":"v", "filename":"original.mp4"})
    st.session_state.pending_recording_save = {"manager":st.session_state.manager, "settings":{}, "default_name":"Recording"}
def process(manager, settings, already_stopped=False):
    st.session_state.processed = already_stopped
with patch.object(app, "VideoService", return_value=Mock()), patch.object(app, "stop_and_process", side_effect=process):
    if st.session_state.get("pending_recording_save"):
        app.save_recording_dialog()
    else:
        st.write(st.session_state.manager.record["title"])
'''
    at = AppTest.from_string(script, default_timeout=40).run()
    assert not at.exception
    at.text_input[0].set_value("Evening trip")
    at.button[0].click().run()
    assert not at.exception
    assert at.session_state.manager.record["title"] == "Evening trip"
    assert at.session_state.processed is True
    assert "pending_recording_save" not in at.session_state
