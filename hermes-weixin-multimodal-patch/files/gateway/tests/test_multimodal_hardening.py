from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import importlib
from pathlib import Path
import threading
from types import ModuleType, SimpleNamespace
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_base_session_stubs() -> None:
    if "gateway.config" not in sys.modules:
        gateway_config = ModuleType("gateway.config")

        class Platform(Enum):
            LOCAL = "local"
            TELEGRAM = "telegram"
            DISCORD = "discord"
            WHATSAPP = "whatsapp"
            SLACK = "slack"
            SIGNAL = "signal"
            HOMEASSISTANT = "homeassistant"
            EMAIL = "email"
            SMS = "sms"
            DINGTALK = "dingtalk"
            FEISHU = "feishu"
            WECOM_CALLBACK = "wecom_callback"
            WECOM = "wecom"
            WEIXIN = "weixin"
            MATTERMOST = "mattermost"
            MATRIX = "matrix"
            API_SERVER = "api_server"
            WEBHOOK = "webhook"
            BLUEBUBBLES = "bluebubbles"

        @dataclass
        class PlatformConfig:
            name: str = "stub"

        @dataclass
        class GatewayConfig:
            sessions_dir: str = "."

        gateway_config.Platform = Platform
        gateway_config.PlatformConfig = PlatformConfig
        gateway_config.GatewayConfig = GatewayConfig
        gateway_config.SessionResetPolicy = object
        gateway_config.HomeChannel = object
        sys.modules["gateway.config"] = gateway_config

    if "hermes_constants" not in sys.modules:
        hermes_constants = ModuleType("hermes_constants")
        hermes_constants.get_hermes_home = lambda: Path(".")
        hermes_constants.get_hermes_dir = lambda *args, **kwargs: Path(".")
        hermes_constants.apply_ipv4_preference = lambda force=False: None
        sys.modules["hermes_constants"] = hermes_constants

_install_base_session_stubs()

from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, merge_pending_message_event
from gateway.session import SessionEntry, SessionSource, SessionStore
from gateway.config import Platform


def _source() -> SessionSource:
    return SessionSource(platform=Platform.WEIXIN, chat_id="chat-1", user_id="user-1")


def _event(
    text: str,
    *,
    message_type: MessageType = MessageType.TEXT,
    media_urls: list[str] | None = None,
    media_types: list[str] | None = None,
    message_id: str | None = None,
    timestamp: datetime | None = None,
) -> MessageEvent:
    return MessageEvent(
        text=text,
        message_type=message_type,
        source=_source(),
        media_urls=list(media_urls or []),
        media_types=list(media_types or []),
        message_id=message_id,
        timestamp=timestamp or datetime.now(),
    )


def _load_gateway_runner(monkeypatch: pytest.MonkeyPatch):
    gateway_config = sys.modules["gateway.config"]
    if not hasattr(gateway_config, "GatewayConfig"):
        @dataclass
        class GatewayConfig:
            sessions_dir: str = "."

        gateway_config.GatewayConfig = GatewayConfig
    if not hasattr(gateway_config, "load_gateway_config"):
        gateway_config.load_gateway_config = lambda: gateway_config.GatewayConfig()

    utils = ModuleType("utils")
    utils.atomic_yaml_write = lambda *args, **kwargs: None
    utils.is_truthy_value = lambda value: bool(value)
    monkeypatch.setitem(sys.modules, "utils", utils)

    dotenv = ModuleType("dotenv")
    dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", dotenv)

    monkeypatch.setitem(sys.modules, "hermes_cli", ModuleType("hermes_cli"))

    env_loader = ModuleType("hermes_cli.env_loader")
    env_loader.load_hermes_dotenv = lambda **kwargs: None
    monkeypatch.setitem(sys.modules, "hermes_cli.env_loader", env_loader)

    hermes_cli_config = ModuleType("hermes_cli.config")
    hermes_cli_config._expand_env_vars = lambda cfg: cfg
    hermes_cli_config.print_config_warnings = lambda: None
    monkeypatch.setitem(sys.modules, "hermes_cli.config", hermes_cli_config)

    gateway_delivery = ModuleType("gateway.delivery")

    class DeliveryRouter:
        def __init__(self, config):
            self.config = config

    gateway_delivery.DeliveryRouter = DeliveryRouter
    monkeypatch.setitem(sys.modules, "gateway.delivery", gateway_delivery)

    gateway_restart = ModuleType("gateway.restart")
    gateway_restart.DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT = 30.0
    gateway_restart.GATEWAY_SERVICE_RESTART_EXIT_CODE = 75
    gateway_restart.parse_restart_drain_timeout = lambda value=None: 30.0
    monkeypatch.setitem(sys.modules, "gateway.restart", gateway_restart)

    sys.modules.pop("gateway.run", None)
    return importlib.import_module("gateway.run").GatewayRunner


def _session_store_config_stub():
    class _ResetPolicy:
        mode = "none"
        idle_minutes = 0
        at_hour = 0

    class _Config:
        group_sessions_per_user = True
        thread_sessions_per_user = False

        def get_reset_policy(self, platform=None, session_type=None):
            return _ResetPolicy()

    return _Config()


def test_image_plus_two_fast_texts_form_one_logical_turn():
    pending_messages: dict[str, deque[MessageEvent]] = {}
    session_key = "wx:chat-1"
    started_at = datetime.now()

    merge_pending_message_event(
        pending_messages,
        session_key,
        _event(
            "",
            message_type=MessageType.PHOTO,
            media_urls=["image-1.jpg"],
            media_types=["image/jpeg"],
            message_id="img-1",
            timestamp=started_at,
        ),
    )
    merge_pending_message_event(
        pending_messages,
        session_key,
        _event("第一条说明", message_id="txt-1", timestamp=started_at + timedelta(seconds=1)),
    )
    merge_pending_message_event(
        pending_messages,
        session_key,
        _event("第二条说明", message_id="txt-2", timestamp=started_at + timedelta(seconds=2)),
    )

    queue = pending_messages[session_key]
    assert len(queue) == 1
    assert queue[0].message_type == MessageType.PHOTO
    assert queue[0].media_urls == ["image-1.jpg"]
    assert queue[0].text is not None
    assert "第一条说明" in queue[0].text
    assert "第二条说明" in queue[0].text
    assert queue[0].text.index("第一条说明") < queue[0].text.index("第二条说明")


def test_delayed_text_after_merge_window_stays_separate_turn():
    pending_messages: dict[str, deque[MessageEvent]] = {}
    session_key = "wx:chat-1"
    started_at = datetime.now()

    merge_pending_message_event(
        pending_messages,
        session_key,
        _event(
            "图像说明",
            message_type=MessageType.PHOTO,
            media_urls=["image-1.jpg"],
            media_types=["image/jpeg"],
            timestamp=started_at,
        ),
    )
    merge_pending_message_event(
        pending_messages,
        session_key,
        _event("晚到的补充", timestamp=started_at + timedelta(seconds=30)),
    )

    queue = pending_messages[session_key]
    assert len(queue) == 2
    assert [item.text for item in queue] == ["图像说明", "晚到的补充"]


def test_late_photo_stays_separate_turn():
    pending_messages: dict[str, deque[MessageEvent]] = {}
    session_key = "wx:chat-1"
    started_at = datetime.now()

    merge_pending_message_event(
        pending_messages,
        session_key,
        _event(
            "",
            message_type=MessageType.PHOTO,
            media_urls=["image-1.jpg"],
            media_types=["image/jpeg"],
            message_id="img-1",
            timestamp=started_at,
        ),
    )
    merge_pending_message_event(
        pending_messages,
        session_key,
        _event(
            "",
            message_type=MessageType.PHOTO,
            media_urls=["image-2.jpg"],
            media_types=["image/jpeg"],
            message_id="img-2",
            timestamp=started_at + timedelta(seconds=50),
        ),
    )

    queue = pending_messages[session_key]
    assert len(queue) == 2
    assert [item.media_urls for item in queue] == [["image-1.jpg"], ["image-2.jpg"]]


def test_two_plain_text_followups_stay_as_two_turns():
    pending_messages: dict[str, deque[MessageEvent]] = {}
    session_key = "wx:chat-1"

    merge_pending_message_event(pending_messages, session_key, _event("第一句"))
    merge_pending_message_event(pending_messages, session_key, _event("第二句"))

    queue = pending_messages[session_key]
    assert len(queue) == 2
    assert [item.text for item in queue] == ["第一句", "第二句"]


def test_pending_shadow_queue_or_replace_pending_event_uses_adapter_queue_only(monkeypatch: pytest.MonkeyPatch):
    gateway_runner_cls = _load_gateway_runner(monkeypatch)
    runner = gateway_runner_cls.__new__(gateway_runner_cls)
    adapter = SimpleNamespace(_pending_messages={})
    runner.adapters = {Platform.WEIXIN: adapter}
    runner._pending_messages = {"other-session": "legacy shadow pending"}

    event = _event("新的排队消息", message_id="txt-queue")
    runner._queue_or_replace_pending_event("wx:chat-1", event)

    assert "wx:chat-1" in adapter._pending_messages
    assert len(adapter._pending_messages["wx:chat-1"]) == 1
    assert adapter._pending_messages["wx:chat-1"][0].text == "新的排队消息"
    assert "wx:chat-1" not in runner._pending_messages
    assert runner._pending_messages["other-session"] == "legacy shadow pending"


def test_clear_pending_messages_clears_entire_session_queue(monkeypatch: pytest.MonkeyPatch):
    class _AdapterStub:
        clear_pending_messages = BasePlatformAdapter.clear_pending_messages

    adapter = _AdapterStub()
    adapter._pending_messages = {
        "wx:chat-1": deque([_event("第一条"), _event("第二条")]),
        "wx:chat-2": deque([_event("保留")]),
    }

    assert len(adapter._pending_messages["wx:chat-1"]) == 2

    adapter.clear_pending_messages("wx:chat-1")

    assert "wx:chat-1" not in adapter._pending_messages
    assert [item.text for item in adapter._pending_messages["wx:chat-2"]] == ["保留"]


def test_plain_text_user_turn_writes_mandatory_gateway_event(monkeypatch: pytest.MonkeyPatch):
    gateway_runner_cls = _load_gateway_runner(monkeypatch)
    runner = gateway_runner_cls.__new__(gateway_runner_cls)

    event = _event("纯文本消息", message_type=MessageType.TEXT)

    entry = runner._build_user_transcript_entry(
        event=event,
        message_text="纯文本消息",
        timestamp="2026-04-14T00:00:00Z",
    )

    assert entry["role"] == "user"
    assert entry["content"] == "纯文本消息"
    assert entry["gateway_event"] == {
        "original_text": "纯文本消息",
        "message_type": "text",
        "media_urls": [],
        "media_types": [],
        "structured_content": [{"type": "input_text", "text": "纯文本消息"}],
    }


def test_multiple_user_rows_gateway_event(monkeypatch: pytest.MonkeyPatch):
    gateway_runner_cls = _load_gateway_runner(monkeypatch)
    runner = gateway_runner_cls.__new__(gateway_runner_cls)

    event = _event("原始多模态输入", message_type=MessageType.TEXT)
    new_messages = [
        {"role": "user", "content": "第一条 user row"},
        {"role": "assistant", "content": "中间回复"},
        {"role": "user", "content": "第二条 user row"},
    ]

    decorated = runner._decorate_new_messages_for_transcript(
        new_messages=new_messages,
        event=event,
        message_text="原始多模态输入",
        timestamp="2026-04-14T00:00:00Z",
    )

    user_rows = [entry for entry in decorated if entry.get("role") == "user"]

    assert len(user_rows) == 2
    assert user_rows[0]["gateway_event"]["original_text"] == "原始多模态输入"
    assert user_rows[1]["gateway_event"]["original_text"] == "原始多模态输入"


def test_voice_attachment_serializes_as_input_audio(monkeypatch: pytest.MonkeyPatch):
    gateway_runner_cls = _load_gateway_runner(monkeypatch)

    event = _event(
        "",
        message_type=MessageType.VOICE,
        media_urls=["voice-note.ogg"],
        media_types=["audio/ogg"],
    )

    blocks = gateway_runner_cls._build_structured_user_content(event)

    assert blocks == [
        {"type": "input_audio", "audio_path": "voice-note.ogg", "media_type": "audio/ogg"}
    ]


def test_video_attachment_serializes_canonically_as_input_file(monkeypatch: pytest.MonkeyPatch):
    gateway_runner_cls = _load_gateway_runner(monkeypatch)
    runner = gateway_runner_cls.__new__(gateway_runner_cls)
    runner.config = SimpleNamespace(thread_sessions_per_user=False)
    runner.adapters = {}
    runner._model = "stub-model"
    runner._base_url = ""

    async def _unexpected_vision(*args, **kwargs):
        raise AssertionError("video should not be routed through vision enrichment")

    async def _unexpected_stt(*args, **kwargs):
        raise AssertionError("video should not be routed through transcription enrichment")

    runner._enrich_message_with_vision = _unexpected_vision
    runner._enrich_message_with_transcription = _unexpected_stt

    event = _event(
        "请看视频",
        message_type=MessageType.VIDEO,
        media_urls=["clip.mp4"],
        media_types=["video/mp4"],
    )

    blocks = gateway_runner_cls._build_structured_user_content(event)
    prepared = asyncio.run(
        runner._prepare_inbound_message_text(
            event=event,
            source=event.source,
            history=[],
        )
    )

    assert blocks == [
        {"type": "input_file", "file_path": "clip.mp4", "media_type": "video/mp4"},
        {"type": "input_text", "text": "请看视频"},
    ]
    assert prepared == "请看视频"


def test_mcp_reload_not_user(monkeypatch: pytest.MonkeyPatch):
    gateway_runner_cls = _load_gateway_runner(monkeypatch)
    runner = gateway_runner_cls.__new__(gateway_runner_cls)

    recorded: list[tuple[str, dict]] = []

    class _SessionStoreStub:
        def get_or_create_session(self, source):
            return SimpleNamespace(session_id="session-123")

        def append_to_transcript(self, session_id, entry):
            recorded.append((session_id, entry))

    mcp_tool = ModuleType("tools.mcp_tool")
    mcp_tool._servers = {"alpha": object()}
    mcp_tool._lock = threading.Lock()
    mcp_tool.shutdown_mcp_servers = lambda: None
    mcp_tool._load_mcp_config = lambda: {}
    mcp_tool.discover_mcp_tools = lambda: ["tool.alpha"]
    monkeypatch.setitem(sys.modules, "tools.mcp_tool", mcp_tool)

    runner.session_store = _SessionStoreStub()

    result = asyncio.run(runner._handle_reload_mcp_command(_event("/reload-mcp")))

    assert "MCP Servers Reloaded" in result
    assert recorded
    session_id, entry = recorded[0]
    assert session_id == "session-123"
    assert entry["role"] != "user"


def test_retry_rebuilds_multimodal_message_event(monkeypatch: pytest.MonkeyPatch):
    gateway_runner_cls = _load_gateway_runner(monkeypatch)
    runner = gateway_runner_cls.__new__(gateway_runner_cls)

    session_entry = SimpleNamespace(session_id="session-123", last_prompt_tokens=99)
    rewritten: list[tuple[str, list[dict]]] = []
    retried_events: list[MessageEvent] = []

    class _SessionStoreStub:
        def get_or_create_session(self, source):
            return session_entry

        def load_transcript(self, session_id):
            assert session_id == "session-123"
            return [
                {"role": "assistant", "content": "older"},
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "degraded content"}],
                    "gateway_event": {
                        "original_text": "原始图片说明",
                        "message_type": "photo",
                        "media_urls": ["photo-a.jpg"],
                        "media_types": ["image/jpeg"],
                    },
                },
                {"role": "assistant", "content": "latest reply"},
            ]

        def rewrite_transcript(self, session_id, messages):
            rewritten.append((session_id, messages))

    async def _handle_message(event: MessageEvent) -> str:
        retried_events.append(event)
        return "retried"

    runner.session_store = _SessionStoreStub()
    runner._handle_message = _handle_message

    result = asyncio.run(runner._handle_retry_command(_event("/retry")))

    assert result == "retried"
    assert rewritten == [("session-123", [{"role": "assistant", "content": "older"}])]
    assert session_entry.last_prompt_tokens == 0
    assert len(retried_events) == 1
    assert retried_events[0].text == "原始图片说明"
    assert retried_events[0].message_type == MessageType.PHOTO
    assert retried_events[0].media_urls == ["photo-a.jpg"]
    assert retried_events[0].media_types == ["image/jpeg"]


def test_load_transcript_prefers_equal_length_jsonl_with_gateway_event(tmp_path: Path):
    store = SessionStore(tmp_path, _session_store_config_stub())
    store._db = SimpleNamespace(
        get_messages_as_conversation=lambda session_id: [
            {"role": "user", "content": "plain"},
            {"role": "assistant", "content": "reply"},
        ]
    )

    transcript_path = store.get_transcript_path("session-123")
    transcript_path.write_text(
        "\n".join(
            [
                '{"role": "user", "content": "plain", "gateway_event": {"original_text": "原始文本", "message_type": "text", "media_urls": [], "media_types": []}}',
                '{"role": "assistant", "content": "reply"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = store.load_transcript("session-123")

    assert loaded[0]["gateway_event"]["original_text"] == "原始文本"
    assert loaded[0]["content"] == "plain"
    assert len(loaded) == 2


def test_undo_preview_prefers_original_text(monkeypatch: pytest.MonkeyPatch):
    gateway_runner_cls = _load_gateway_runner(monkeypatch)
    runner = gateway_runner_cls.__new__(gateway_runner_cls)

    class _SessionStoreStub:
        def get_or_create_session(self, source):
            return SimpleNamespace(session_id="session-123", last_prompt_tokens=41)

        def load_transcript(self, session_id):
            return [
                {"role": "assistant", "content": "older"},
                {
                    "role": "user",
                    "content": "[image placeholder]",
                    "gateway_event": {
                        "original_text": "用户当时发的原文说明，比 placeholder 更好",
                    },
                },
                {"role": "assistant", "content": "latest reply"},
            ]

        def rewrite_transcript(self, session_id, messages):
            assert session_id == "session-123"
            assert messages == [{"role": "assistant", "content": "older"}]

    runner.session_store = _SessionStoreStub()

    result = asyncio.run(runner._handle_undo_command(_event("/undo")))

    assert "Undid 2 message(s)" in result
    assert 'Removed: "用户当时发的原文说明，比 placeholder 更好"' in result


def test_rehydrate_preserved_user_gateway_events_restores_surviving_rows(monkeypatch: pytest.MonkeyPatch):
    gateway_runner_cls = _load_gateway_runner(monkeypatch)
    runner = gateway_runner_cls.__new__(gateway_runner_cls)

    original_history = [
        {
            "role": "user",
            "content": "keep me",
            "gateway_event": {
                "original_text": "原始保真文本",
                "message_type": "photo",
                "media_urls": ["keep.jpg"],
                "media_types": ["image/jpeg"],
            },
        },
        {"role": "assistant", "content": "reply"},
        {
            "role": "user",
            "content": "drop me",
            "gateway_event": {
                "original_text": "被压缩丢弃",
                "message_type": "text",
                "media_urls": [],
                "media_types": [],
            },
        },
    ]
    compressed = [
        {"role": "system", "content": "summary"},
        {"role": "user", "content": "keep me"},
        {"role": "assistant", "content": "reply"},
    ]

    rehydrated = runner._rehydrate_preserved_user_gateway_events(original_history, compressed)

    assert rehydrated[1]["gateway_event"] == original_history[0]["gateway_event"]
    assert "gateway_event" not in rehydrated[0]
    assert "gateway_event" not in rehydrated[2]


def test_auto_compress_rehydrates_surviving_user_rows_before_rewrite(monkeypatch: pytest.MonkeyPatch):
    gateway_runner_cls = _load_gateway_runner(monkeypatch)
    runner = gateway_runner_cls.__new__(gateway_runner_cls)

    history = [
        {
            "role": "user",
            "content": "keep me",
            "gateway_event": {
                "original_text": "自动压缩前的原始文本",
                "message_type": "photo",
                "media_urls": ["keep.jpg"],
                "media_types": ["image/jpeg"],
            },
        },
        {"role": "assistant", "content": "reply"},
    ]
    compressed = [
        {"role": "system", "content": "summary"},
        {"role": "user", "content": "keep me"},
        {"role": "assistant", "content": "reply"},
    ]

    rewritten = runner._rehydrate_preserved_user_gateway_events(history, compressed)

    assert rewritten[1]["gateway_event"]["original_text"] == "自动压缩前的原始文本"
    assert rewritten[1]["gateway_event"]["media_urls"] == ["keep.jpg"]


def test_rehydrate_preserved_user_gateway_events_duplicate_content_uses_neighbor_anchor(monkeypatch: pytest.MonkeyPatch):
    gateway_runner_cls = _load_gateway_runner(monkeypatch)
    runner = gateway_runner_cls.__new__(gateway_runner_cls)

    original_history = [
        {
            "role": "user",
            "content": "same",
            "gateway_event": {
                "original_text": "第一条 same",
                "message_type": "text",
                "media_urls": [],
                "media_types": [],
            },
        },
        {"role": "assistant", "content": "reply-a"},
        {
            "role": "user",
            "content": "same",
            "gateway_event": {
                "original_text": "第二条 same",
                "message_type": "photo",
                "media_urls": ["second.jpg"],
                "media_types": ["image/jpeg"],
            },
        },
        {"role": "assistant", "content": "reply-b"},
    ]
    rewritten = [
        {"role": "system", "content": "summary"},
        {"role": "user", "content": "same"},
        {"role": "assistant", "content": "reply-b"},
    ]

    rehydrated = runner._rehydrate_preserved_user_gateway_events(original_history, rewritten)

    assert rehydrated[1]["gateway_event"]["original_text"] == "第二条 same"
    assert rehydrated[1]["gateway_event"]["media_urls"] == ["second.jpg"]
