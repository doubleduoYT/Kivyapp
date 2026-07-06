#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Entry -> PyGame runtime

이 파일은 변환된 Entry 프로젝트(project_data.json)를 직접 실행하는 범용 런타임이야.
목표는 HG OS 2 Beta처럼 이미지/텍스트 오브젝트가 많고, 장면 전환/방송/반복/효과/소리로 움직이는 작품을
PyGame에서 최대한 비슷하게 돌리는 것.

지원 범위:
- 장면, 오브젝트, 복제본, 이미지/텍스트 렌더링
- when_run_button_click / when_scene_start / when_message_cast / when_object_click / mouse_clicked/up / clone_start
- repeat, if/else, wait, wait_until, broadcast, scene change
- 위치, 크기, 모양 바꾸기, 투명도/밝기 일부, 텍스트 쓰기, 변수/리스트 일부
- mp3 효과음/BGM 재생
- AI, 음성 인식, 웹 날씨/미세먼지, 카메라 기능은 안전한 더미값/no-op 처리
"""
from __future__ import annotations

import argparse
import copy
import datetime as _dt
import json
import math
import os
import random
import sys
import time
import traceback
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, Iterable, List, Optional, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

try:
    import pygame
except Exception as exc:  # pragma: no cover
    print("pygame이 설치되어 있지 않아. 먼저 설치해줘: python -m pip install pygame pillow", file=sys.stderr)
    raise

STAGE_W = 480
STAGE_H = 270
FPS = 60
FRAME_DELAY = 1.0 / FPS


def _num(v: Any, default: float = 0.0) -> float:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        s = str(v).strip()
        if not s:
            return default
        return float(s.replace(",", ""))
    except Exception:
        return default


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(round(_num(v, default)))
    except Exception:
        return default


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    s = str(v).strip().lower()
    return s not in ("", "0", "false", "none", "null", "nan")


def _safe_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return str(v)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class StopThread(Exception):
    pass


class StopProject(Exception):
    pass


@dataclass
class RuntimeObject:
    data: Dict[str, Any]
    layer: int
    is_clone: bool = False
    clone_id: Optional[str] = None
    deleted: bool = False
    state: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        ent = copy.deepcopy(self.data.get("entity") or {})
        self.state = {
            "x": _num(ent.get("x", 0)),
            "y": _num(ent.get("y", 0)),
            "regX": _num(ent.get("regX", 0)),
            "regY": _num(ent.get("regY", 0)),
            "scaleX": _num(ent.get("scaleX", 1), 1),
            "scaleY": _num(ent.get("scaleY", 1), 1),
            "originalScaleX": _num(ent.get("scaleX", 1), 1),
            "originalScaleY": _num(ent.get("scaleY", 1), 1),
            "rotation": _num(ent.get("rotation", 0)),
            "direction": _num(ent.get("direction", 90), 90),
            "width": _num(ent.get("width", 0)),
            "height": _num(ent.get("height", 0)),
            "visible": bool(ent.get("visible", True)),
            "text": ent.get("text", self.data.get("text", "")) or "",
            "font": ent.get("font", ""),
            "fontSize": _num(ent.get("fontSize", 16), 16),
            "colour": ent.get("colour", "#000000"),
            "bgColor": ent.get("bgColor", "transparent"),
            "textAlign": ent.get("textAlign", 0),
            "lineBreak": ent.get("lineBreak", True),
            "selectedPictureId": self.data.get("selectedPictureId"),
            "effects": {"transparency": 0, "brightness": 0, "color": 0},
        }
        if not self.state["selectedPictureId"] and self.data.get("pictures"):
            self.state["selectedPictureId"] = self.data["pictures"][0].get("id")

    @property
    def id(self) -> str:
        return self.clone_id or self.data.get("id")

    @property
    def base_id(self) -> str:
        return self.data.get("id")

    @property
    def name(self) -> str:
        return self.data.get("name", self.id)

    @property
    def scene(self) -> str:
        return self.data.get("scene")

    @property
    def object_type(self) -> str:
        return self.data.get("objectType", "sprite")


@dataclass
class ThreadState:
    obj: RuntimeObject
    gen: Generator
    wake: float = 0.0
    scene_id: Optional[str] = None
    label: str = ""


class EntryRuntime:
    def __init__(self, project_path: str, scale: float = 1.0, debug: bool = False, start_scene: Optional[str] = None):
        self.project_path = os.path.abspath(project_path)
        self.root = os.path.dirname(self.project_path)
        with open(self.project_path, "r", encoding="utf-8") as f:
            self.project = json.load(f)
        self.scale = max(0.25, float(scale))
        self.debug = debug
        self.scenes: List[Dict[str, Any]] = self.project.get("scenes", [])
        self.scene_index = {s["id"]: i for i, s in enumerate(self.scenes)}
        self.scene_name = {s["id"]: s.get("name", s["id"]) for s in self.scenes}
        self.messages = {m["id"]: m.get("name", m["id"]) for m in self.project.get("messages", [])}
        self.message_by_name = {v: k for k, v in self.messages.items()}
        self.variables_meta = {v["id"]: v for v in self.project.get("variables", [])}
        self.vars: Dict[str, Any] = {}
        self.lists: Dict[str, List[Any]] = {}
        self.list_visible: Dict[str, bool] = {}
        for vid, meta in self.variables_meta.items():
            if meta.get("variableType") == "list":
                self.lists[vid] = [item.get("data", "") for item in (meta.get("array") or [])]
                self.vars[vid] = self.lists[vid]
                self.list_visible[vid] = bool(meta.get("visible", False))
            else:
                self.vars[vid] = meta.get("value", 0)
        self.answer = ""
        self.ask_prompt: Optional[str] = None
        self.ask_buffer = ""
        self.ask_done = False
        self.timer_start = time.monotonic()

        self.functions = self._parse_functions(self.project.get("functions", []))
        self.objects: List[RuntimeObject] = []
        self.objects_by_id: Dict[str, RuntimeObject] = {}
        # Entry의 objects 배열은 보통 "위에 있는 오브젝트 -> 아래에 있는 오브젝트" 순서로 저장돼.
        # 이전 버전은 이것을 일반 PyGame식으로 앞에서 뒤로 그려서, 배경/큰 패널이 아이콘과 로고 위를 덮어버렸어.
        # layer를 음수로 두면 배열의 뒤쪽 오브젝트가 먼저 그려지고, 앞쪽 오브젝트가 위에 올라온다.
        for i, od in enumerate(self.project.get("objects", [])):
            obj = RuntimeObject(od, layer=-i)
            self.objects.append(obj)
            self.objects_by_id[obj.base_id] = obj
        self.clones: List[RuntimeObject] = []
        self.threads: List[ThreadState] = []
        self.next_clone_num = 1

        self.current_scene = start_scene or (self.scenes[0]["id"] if self.scenes else "")
        if self.current_scene not in self.scene_index and self.scenes:
            self.current_scene = self.scenes[0]["id"]

        self.images: Dict[str, pygame.Surface] = {}
        self.sounds: Dict[str, Any] = {}
        self.sound_lengths: Dict[str, float] = {}
        self.bg_channel: Optional[pygame.mixer.Channel] = None
        self.mouse_pos_stage = (0.0, 0.0)
        self.mouse_down = False
        self.mouse_clicked_this_frame = False
        self.mouse_up_this_frame = False
        self.running = True
        self.screen: Optional[pygame.Surface] = None
        self.stage_surface: Optional[pygame.Surface] = None
        self.font_cache: Dict[Tuple[int, bool], pygame.font.Font] = {}
        self._block_log_once: set[str] = set()
        self._stepping = False
        self._pending_threads: List[ThreadState] = []
        # FastEntry 쪽 구현처럼 오브젝트별 활성 사운드를 관리해서 반복/중첩 재생을 줄인다.
        self._last_sfx_time: Dict[Tuple[str, str], float] = {}
        self._sound_channels: Dict[str, pygame.mixer.Channel] = {}
        self._sound_channel_end: Dict[str, float] = {}
        self._sound_channel_sid: Dict[str, str] = {}
        self._mixer_channel_count = 16
        self._current_bgm_sid: Optional[str] = None
        self._current_bgm_end: float = 0.0
        self._sfx_volume = 1.0
        self._bgm_volume = 1.0

        # 이미지 변환 캐시. FastEntry처럼 매 프레임 같은 이미지의 scale/rotate를 다시 계산하지 않는다.
        self._image_cache: "OrderedDict[Tuple[Any, ...], pygame.Surface]" = OrderedDict()
        self._image_cache_limit = 512

        # 이벤트 모자 캐시. 방송/클릭/장면 시작 때 모든 스택을 계속 재검색하지 않게 한다.
        self._hat_cache: Dict[str, List[Tuple[RuntimeObject, List[Dict[str, Any]], Optional[str]]]] = {}
        # 클릭 이벤트는 FastEntry처럼 매번 전체 스크립트를 훑지 않도록 오브젝트별 캐시를 둔다.
        # 또한 Entry에서는 위에 덮인 투명/배경 오브젝트 때문에 아래 버튼 클릭이 막히면 안 돼서,
        # 클릭 모자가 있는 오브젝트만 후보로 잡아준다.
        self._object_hat_types: Dict[str, set[str]] = {}
        self._build_hat_cache()

    # ---------- setup ----------
    def _parse_functions(self, funcs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for f in funcs:
            fid = f.get("id")
            if not fid:
                continue
            try:
                content = json.loads(f.get("content") or "[]")
            except Exception:
                content = []
            body: List[Dict[str, Any]] = []
            if content and isinstance(content, list) and content[0] and isinstance(content[0][0], dict):
                create = content[0][0]
                sts = create.get("statements") or []
                if sts and isinstance(sts[0], list):
                    body = sts[0]
            out[f"func_{fid}"] = {"id": fid, "type": f.get("type", "normal"), "body": body}
        return out

    def _build_hat_cache(self) -> None:
        self._hat_cache = {}
        self._object_hat_types = {}
        for obj in self.objects:
            hats: set[str] = set()
            for stack in self.scripts_for(obj):
                if not stack or not isinstance(stack[0], dict):
                    continue
                hat = stack[0]
                hat_type = hat.get("type")
                if not isinstance(hat_type, str):
                    continue
                if hat_type.startswith("when_") or hat_type in ("mouse_clicked", "mouse_click_cancled"):
                    hats.add(hat_type)
                    msg_id = None
                    if hat_type == "when_message_cast":
                        params = hat.get("params") or []
                        msg_id = params[1] if len(params) > 1 else None
                    self._hat_cache.setdefault(hat_type, []).append((obj, stack[1:], msg_id))
            self._object_hat_types[obj.base_id] = hats

    def object_has_hat(self, obj: RuntimeObject, hat_type: str) -> bool:
        return hat_type in self._object_hat_types.get(obj.base_id, set())

    def init_pygame(self) -> None:
        pygame.init()
        try:
            pygame.mixer.init()
            pygame.mixer.set_num_channels(32)
            self._mixer_channel_count = max(8, pygame.mixer.get_num_channels())
            self.bg_channel = pygame.mixer.Channel(0)
            pygame.mixer.set_reserved(1)
        except Exception as e:
            print("소리 장치 초기화 실패: 소리 없이 실행할게.", e)
            self.bg_channel = None
        win_w = max(120, int(round(STAGE_W * self.scale)))
        win_h = max(68, int(round(STAGE_H * self.scale)))
        self.screen = pygame.display.set_mode((win_w, win_h))
        title = self.project.get("name") or "Entry PyGame Port"
        pygame.display.set_caption(f"{title} - Entry PyGame Port")
        self.stage_surface = pygame.Surface((STAGE_W, STAGE_H), pygame.SRCALPHA)
        self._load_media()

    def _load_media(self) -> None:
        for obj in self.objects:
            for pic in obj.data.get("pictures", []):
                pid = pic.get("id")
                rel = pic.get("path")
                if not pid or not rel:
                    continue
                path = os.path.join(self.root, rel)
                try:
                    surf = pygame.image.load(path).convert_alpha()
                    self.images[pid] = surf
                except Exception as e:
                    print(f"이미지 로드 실패: {pic.get('name', pid)} ({path}) -> {e}")
            for snd in obj.data.get("sounds", []):
                sid = snd.get("id")
                rel = snd.get("path")
                if not sid or not rel or sid in self.sounds:
                    continue
                path = os.path.join(self.root, rel)
                try:
                    sound = pygame.mixer.Sound(path)
                    self.sounds[sid] = sound
                    self.sound_lengths[sid] = max(0.0, sound.get_length())
                except Exception as e:
                    if self.debug:
                        print(f"소리 로드 실패: {snd.get('name', sid)} ({path}) -> {e}")

    # ---------- project lifecycle ----------
    def reset_project(self) -> None:
        self.stop_sfx()
        if self.bg_channel:
            self.bg_channel.stop()
        self._current_bgm_sid = None
        self._current_bgm_end = 0.0
        self.threads.clear()
        self.clones.clear()
        for obj in self.objects:
            obj.deleted = False
            obj.__post_init__()
        self.timer_start = time.monotonic()
        self.spawn_hat("when_run_button_click", scene_only=True)
        self.spawn_hat("when_scene_start", scene_only=True)

    def set_scene(self, scene_id: str) -> None:
        if scene_id not in self.scene_index:
            if self.debug:
                print("없는 장면:", scene_id)
            return
        old = self.current_scene
        self.current_scene = scene_id
        # 장면 밖에서 시작된 효과음이 계속 남지 않게 정리한다. BGM은 stop_bgm 블록이 따로 맡는다.
        self.stop_sfx()
        # Entry 장면 전환처럼 이전 장면 스레드와 복제본은 정리한다.
        self.threads = [t for t in self.threads if t.scene_id is None or t.scene_id == scene_id]
        self.clones = [c for c in self.clones if c.scene == scene_id and not c.deleted]
        if self.debug:
            print(f"SCENE {self.scene_name.get(old, old)} -> {self.scene_name.get(scene_id, scene_id)}")
        self.spawn_hat("when_scene_start", scene_only=True)

    def neighbor_scene(self, mode: str) -> Optional[str]:
        if not self.scenes:
            return None
        idx = self.scene_index.get(self.current_scene, 0)
        if mode == "prev":
            idx = (idx - 1) % len(self.scenes)
        else:
            idx = (idx + 1) % len(self.scenes)
        return self.scenes[idx]["id"]

    # ---------- scripts ----------
    def scripts_for(self, obj: RuntimeObject) -> List[List[Dict[str, Any]]]:
        return obj.data.get("scripts", []) or []

    def _iter_active_objects(self, scene_only: bool = True, include_clones: bool = True) -> Iterable[RuntimeObject]:
        objs: List[RuntimeObject] = list(self.objects)
        if include_clones:
            objs += [c for c in self.clones if not c.deleted]
        for obj in objs:
            if obj.deleted:
                continue
            if scene_only and obj.scene != self.current_scene:
                continue
            yield obj

    def spawn_hat(self, hat_type: str, *, obj_filter: Optional[RuntimeObject] = None,
                  message_id: Optional[str] = None, scene_only: bool = True) -> None:
        if obj_filter is not None:
            for stack in self.scripts_for(obj_filter):
                if not stack:
                    continue
                hat = stack[0]
                if not isinstance(hat, dict) or hat.get("type") != hat_type:
                    continue
                if hat_type == "when_message_cast":
                    params = hat.get("params") or []
                    hid = params[1] if len(params) > 1 else None
                    if hid != message_id and self.messages.get(hid) != message_id:
                        continue
                self.add_thread(obj_filter, stack[1:], label=hat_type)
            return

        for obj, body, hid in self._hat_cache.get(hat_type, []):
            if obj.deleted:
                continue
            if scene_only and obj.scene != self.current_scene:
                continue
            if hat_type == "when_message_cast" and hid != message_id and self.messages.get(hid) != message_id:
                continue
            self.add_thread(obj, body, label=hat_type)

    def spawn_clone_start(self, clone: RuntimeObject) -> None:
        for stack in self.scripts_for(clone):
            if stack and isinstance(stack[0], dict) and stack[0].get("type") == "when_clone_start":
                self.add_thread(clone, stack[1:], label="when_clone_start")

    def add_thread(self, obj: RuntimeObject, blocks: List[Dict[str, Any]], label: str = "") -> None:
        gen = self.exec_blocks(obj, blocks)
        th = ThreadState(obj=obj, gen=gen, wake=0.0, scene_id=obj.scene, label=label)
        if self._stepping:
            self._pending_threads.append(th)
        else:
            self.threads.append(th)

    def step_threads(self) -> None:
        now = time.monotonic()
        alive: List[ThreadState] = []
        self._stepping = True
        self._pending_threads = []
        original_threads = list(self.threads)
        for th in original_threads:
            if th.obj.deleted:
                continue
            if th.scene_id is not None and th.scene_id != self.current_scene:
                continue
            if now < th.wake:
                alive.append(th)
                continue
            try:
                delay = next(th.gen)
                if delay is None:
                    delay = 0.0
                th.wake = now + max(0.0, float(delay))
                alive.append(th)
            except StopThread:
                pass
            except StopProject:
                self.running = False
                self._stepping = False
                self._pending_threads = []
                return
            except StopIteration:
                pass
            except Exception:
                print("스크립트 오류:", th.obj.name, th.label)
                traceback.print_exc()
        self._stepping = False
        # 장면 전환 중 생긴 새 스레드는 잃어버리지 않게 합친다.
        self.threads = [t for t in alive if t.scene_id is None or t.scene_id == self.current_scene] + self._pending_threads
        self._pending_threads = []

    def exec_blocks(self, obj: RuntimeObject, blocks: List[Dict[str, Any]]) -> Generator[float, None, None]:
        for block in blocks or []:
            if not isinstance(block, dict):
                continue
            yield from self.exec_block(obj, block)

    def exec_block(self, obj: RuntimeObject, block: Dict[str, Any]) -> Generator[float, None, None]:
        t = block.get("type")
        p = block.get("params") or []
        st = block.get("statements") or []

        if t in ("when_run_button_click", "when_scene_start", "when_message_cast", "when_object_click", "when_clone_start", "mouse_clicked", "mouse_click_cancled", "when_object_click_canceled"):
            return
            yield 0

        if t in ("repeat_inf",):
            body = st[0] if st else []
            while self.running and not obj.deleted:
                yield from self.exec_blocks(obj, body)
                yield FRAME_DELAY
            return

        if t == "repeat_basic":
            count = max(0, _int(self.eval_param(obj, p[0] if p else 0), 0))
            body = st[0] if st else []
            for _ in range(count):
                yield from self.exec_blocks(obj, body)
                yield FRAME_DELAY
            return

        if t == "repeat_while_true":
            cond = p[0] if p else None
            mode = p[1] if len(p) > 1 else "while"
            body = st[0] if st else []
            guard = 0
            while self.running and not obj.deleted:
                ok = _truthy(self.eval_param(obj, cond))
                if mode == "until":
                    ok = not ok
                if not ok:
                    break
                yield from self.exec_blocks(obj, body)
                yield FRAME_DELAY
                guard += 1
                if guard > 200000:
                    break
            return

        if t == "_if":
            if _truthy(self.eval_param(obj, p[0] if p else None)):
                yield from self.exec_blocks(obj, st[0] if st else [])
            return

        if t == "if_else":
            if _truthy(self.eval_param(obj, p[0] if p else None)):
                yield from self.exec_blocks(obj, st[0] if st else [])
            else:
                yield from self.exec_blocks(obj, st[1] if len(st) > 1 else [])
            return

        if t == "wait_second":
            delay = max(0.0, _num(self.eval_param(obj, p[0] if p else 0)))
            yield delay
            return

        if t == "wait_until_true":
            cond = p[0] if p else None
            while self.running and not _truthy(self.eval_param(obj, cond)):
                yield FRAME_DELAY
            return

        if t == "set_variable":
            if p:
                self.vars[p[0]] = self.eval_param(obj, p[1] if len(p) > 1 else 0)
            return
            yield 0

        if t == "change_variable":
            if p:
                vid = p[0]
                self.vars[vid] = _num(self.vars.get(vid, 0)) + _num(self.eval_param(obj, p[1] if len(p) > 1 else 0))
            return
            yield 0

        if t == "show":
            obj.state["visible"] = True
            return
            yield 0
        if t == "hide":
            obj.state["visible"] = False
            return
            yield 0

        if t == "locate_xy":
            obj.state["x"] = _num(self.eval_param(obj, p[0] if p else 0))
            obj.state["y"] = _num(self.eval_param(obj, p[1] if len(p) > 1 else 0))
            return
            yield 0
        if t == "locate_x":
            obj.state["x"] = _num(self.eval_param(obj, p[0] if p else 0))
            return
            yield 0
        if t == "locate_y":
            obj.state["y"] = _num(self.eval_param(obj, p[0] if p else 0))
            return
            yield 0
        if t == "locate":
            target = p[0] if p else None
            if target == "mouse":
                obj.state["x"], obj.state["y"] = self.mouse_pos_stage
            else:
                tobj = self.objects_by_id.get(str(target))
                if tobj:
                    obj.state["x"], obj.state["y"] = tobj.state["x"], tobj.state["y"]
            return
            yield 0
        if t == "move_x":
            obj.state["x"] += _num(self.eval_param(obj, p[0] if p else 0))
            return
            yield 0
        if t == "move_y":
            obj.state["y"] += _num(self.eval_param(obj, p[0] if p else 0))
            return
            yield 0
        if t in ("move_to_angle", "move_direction"):
            if t == "move_direction":
                angle = obj.state.get("direction", 90)
                amount = _num(self.eval_param(obj, p[0] if p else 0))
            else:
                angle = _num(self.eval_param(obj, p[0] if p else 90))
                amount = _num(self.eval_param(obj, p[1] if len(p) > 1 else 0))
            rad = math.radians(angle)
            obj.state["x"] += math.sin(rad) * amount
            obj.state["y"] += math.cos(rad) * amount
            return
            yield 0
        if t == "rotate_relative":
            obj.state["rotation"] += _num(self.eval_param(obj, p[0] if p else 0))
            obj.state["direction"] += _num(self.eval_param(obj, p[0] if p else 0))
            return
            yield 0

        if t == "set_scale_size":
            # Entry/FastEntry 기준의 size는 scaleX*100이 아니라 현재 모양의 시각적 평균 크기(px)에 가깝다.
            # 예: 512px 아이콘을 scale 0.002로 시작하면 size는 약 1이고, size 100은 100px 정도가 된다.
            target_size = _num(self.eval_param(obj, p[0] if p else 100), 100)
            self.set_object_size(obj, target_size)
            return
            yield 0
        if t == "change_scale_size":
            delta = _num(self.eval_param(obj, p[0] if p else 0))
            self.set_object_size(obj, self.get_object_size(obj) + delta)
            return
            yield 0
        if t == "stretch_scale_size":
            # 일부 작품은 가로/세로 크기 블록을 쓴다. 파라미터 순서가 작품/버전에 따라 달라서 둘 다 받는다.
            dim = p[0] if p else "WIDTH"
            value_node = p[1] if len(p) > 1 else 100
            if isinstance(dim, dict):
                dim, value_node = value_node, dim
            dim_s = _safe_text(self.eval_param(obj, dim)).upper()
            value = max(0.0, _num(self.eval_param(obj, value_node), 100))
            bw, bh = self.get_object_base_size(obj)
            if dim_s in ("WIDTH", "X", "가로") and bw > 1e-9:
                sign = -1 if obj.state.get("scaleX", 1) < 0 else 1
                obj.state["scaleX"] = sign * value / bw
            elif dim_s in ("HEIGHT", "Y", "세로") and bh > 1e-9:
                sign = -1 if obj.state.get("scaleY", 1) < 0 else 1
                obj.state["scaleY"] = sign * value / bh
            return
            yield 0
        if t == "reset_scale_size":
            obj.state["scaleX"] = obj.state.get("originalScaleX", obj.state.get("scaleX", 1))
            obj.state["scaleY"] = obj.state.get("originalScaleY", obj.state.get("scaleY", 1))
            return
            yield 0

        if t == "change_to_some_shape":
            shape = self.eval_param(obj, p[0] if p else None)
            resolved = self.resolve_picture_id(obj, shape)
            if resolved:
                obj.state["selectedPictureId"] = resolved
            return
            yield 0

        if t in ("add_effect_amount", "change_effect_amount"):
            effect = p[0] if p else "transparency"
            val = _num(self.eval_param(obj, p[1] if len(p) > 1 else 0))
            if t == "add_effect_amount":
                obj.state["effects"][effect] = obj.state["effects"].get(effect, 0) + val
            else:
                obj.state["effects"][effect] = val
            if effect == "transparency":
                obj.state["effects"][effect] = _clamp(obj.state["effects"].get(effect, 0), 0, 100)
            return
            yield 0
        if t == "erase_all_effects":
            obj.state["effects"] = {"transparency": 0, "brightness": 0, "color": 0}
            return
            yield 0

        if t == "text_write":
            obj.state["text"] = _safe_text(self.eval_param(obj, p[0] if p else ""))
            return
            yield 0
        if t == "text_append":
            obj.state["text"] = _safe_text(obj.state.get("text", "")) + _safe_text(self.eval_param(obj, p[0] if p else ""))
            return
            yield 0
        if t == "text_change_font_color":
            obj.state["colour"] = self.eval_param(obj, p[0] if p else obj.state.get("colour"))
            return
            yield 0

        if t == "message_cast":
            mid = p[0] if p else None
            self.spawn_hat("when_message_cast", message_id=mid, scene_only=True)
            return
            yield 0

        if t in ("start_scene", "start_neighbor_scene"):
            if t == "start_scene":
                sid = p[0] if p else None
            else:
                sid = self.neighbor_scene(p[0] if p else "next")
            if sid:
                self.set_scene(str(sid))
                raise StopThread()
            return
            yield 0

        if t == "create_clone":
            target = p[0] if p else "self"
            base = obj if target in (None, "self") else self.objects_by_id.get(str(target), obj)
            clone_data = base.data
            clone = RuntimeObject(clone_data, layer=base.layer + 0.01 * self.next_clone_num, is_clone=True, clone_id=f"{base.base_id}#clone{self.next_clone_num}")
            clone.state = copy.deepcopy(base.state)
            self.next_clone_num += 1
            self.clones.append(clone)
            self.spawn_clone_start(clone)
            return
            yield 0
        if t == "delete_clone":
            if obj.is_clone:
                obj.deleted = True
                raise StopThread()
            return
            yield 0
        if t == "remove_all_clones":
            for c in self.clones:
                if c.base_id == obj.base_id or not p:
                    c.deleted = True
            return
            yield 0

        if t == "change_object_index":
            mode = p[0] if p else "FORWARD"
            # layer가 클수록 화면 위쪽에 그려진다.
            delta = 1000 if mode == "FORWARD" else -1000
            obj.layer += delta
            return
            yield 0

        if t in ("sound_something_with_block", "sound_something_wait_with_block",
                 "sound_something_second_with_block", "sound_something_second_wait_with_block"):
            sid = self.resolve_sound_id(obj, self.eval_param(obj, p[0] if p else None))
            dur = None
            if t in ("sound_something_second_with_block", "sound_something_second_wait_with_block") and len(p) > 1:
                dur = max(0.0, _num(self.eval_param(obj, p[1]), 0))
            wait_for_sound = t in ("sound_something_wait_with_block", "sound_something_second_wait_with_block")
            self.play_sound(sid, obj.base_id, wait_for_sound=wait_for_sound, duration=dur)
            if wait_for_sound:
                yield dur if dur is not None else self.sound_lengths.get(str(sid), 0.0)
            return
        if t == "sound_silent_all":
            mode = _safe_text(p[0] if p else "all")
            if mode in ("thisOnly", "this", "thisObject"):
                self.stop_sfx(obj.base_id)
            else:
                self.stop_sfx()
                if self.bg_channel:
                    self.bg_channel.stop()
                self._current_bgm_sid = None
                self._current_bgm_end = 0.0
            return
            yield 0
        if t == "play_bgm":
            sid = self.resolve_sound_id(obj, self.eval_param(obj, p[0] if p else None), global_search=True)
            self.play_bgm(sid)
            return
            yield 0
        if t == "stop_bgm":
            if self.bg_channel:
                self.bg_channel.stop()
            self._current_bgm_sid = None
            self._current_bgm_end = 0.0
            return
            yield 0

        if t == "ask_and_wait":
            prompt = _safe_text(self.eval_param(obj, p[0] if p else ""))
            self.ask_prompt = prompt
            self.ask_buffer = ""
            self.ask_done = False
            while not self.ask_done and self.running:
                yield FRAME_DELAY
            self.answer = self.ask_buffer
            self.vars["1vu8"] = self.answer
            self.ask_prompt = None
            self.ask_done = False
            return

        if t == "add_value_to_list":
            value = self.eval_param(obj, p[0] if p else "")
            lid = p[1] if len(p) > 1 else None
            if lid:
                self.lists.setdefault(lid, []).append(value)
                self.vars[lid] = self.lists[lid]
            return
            yield 0
        if t == "remove_value_from_list":
            lid = p[0] if p else None
            idx = _int(self.eval_param(obj, p[1] if len(p) > 1 else 1), 1) - 1
            arr = self.lists.setdefault(lid, [])
            if 0 <= idx < len(arr):
                arr.pop(idx)
            return
            yield 0
        if t == "show_list":
            if p:
                self.list_visible[p[0]] = True
            return
            yield 0
        if t == "hide_list":
            if p:
                self.list_visible[p[0]] = False
            return
            yield 0

        if t == "stop_object":
            mode = p[0] if p else "thisOnly"
            if mode in ("all", "allObject"):
                self.threads.clear()
            elif mode in ("this", "thisObject"):
                for th in self.threads:
                    if th.obj.base_id == obj.base_id:
                        th.obj.deleted = True if th.obj.is_clone else th.obj.deleted
                raise StopThread()
            else:
                raise StopThread()
            return
            yield 0
        if t in ("stop_run", "restart_project"):
            if t == "restart_project":
                self.reset_project()
                raise StopThread()
            raise StopProject()

        # 확장/AI/음성/카메라/날씨 UI 블록: no-op 또는 더미 처리
        if t in (
            "set_visible_speech_to_text", "set_visible_project_timer", "set_visible_answer",
            "speech_to_text_convert", "media_pipe_video_screen", "read_text", "dialog_time",
            "choose_project_timer_action", "stop_repeat", "continue_repeat"
        ):
            return
            yield 0

        if t in self.functions:
            finfo = self.functions[t]
            if finfo.get("type") == "normal":
                yield from self.exec_blocks(obj, finfo.get("body", []))
            return

        # 계산 블록이 독립 스택으로 들어간 경우는 평가만 하고 버린다.
        if isinstance(t, str) and (t.startswith("func_") or t in self._value_block_names()):
            self.eval_param(obj, block)
            return

        if self.debug and t not in self._block_log_once:
            self._block_log_once.add(t)
            print("미지원 블록 no-op:", t)
        return
        yield 0

    def _value_block_names(self) -> set[str]:
        return {
            "number", "text", "angle", "get_variable", "calc_basic", "boolean_basic_operator",
            "boolean_and_or", "boolean_not", "coordinate_mouse", "coordinate_object", "reach_something",
            "is_object_clicked", "is_clicked", "mouse_clicked", "mouse_click_cancled", "get_pictures", "get_sounds",
            "combine_something", "get_nickname", "get_date", "get_canvas_input_value", "calc_rand",
            "text_color", "substring", "length_of_string", "char_at", "count_match_string",
            "value_of_index_from_list", "length_of_list", "is_included_in_list", "check_city_weather",
            "get_city_weather_data", "get_day_weather_data", "get_current_city_weather_data", "check_city_finedust",
            "get_korea_area_code", "is_current_device_type", "get_project_timer_value", "True",
        }

    # ---------- evaluation ----------
    def eval_param(self, obj: RuntimeObject, node: Any) -> Any:
        if not isinstance(node, dict):
            return node
        t = node.get("type")
        p = node.get("params") or []

        if t in ("number", "angle"):
            return _num(p[0] if p else 0)
        if t == "text":
            return p[0] if p else ""
        if t == "True":
            return True
        if t == "get_variable":
            return self.vars.get(p[0], 0) if p else 0
        if t == "get_canvas_input_value":
            return self.answer
        if t == "get_nickname":
            return "덥듀"
        if t == "get_pictures":
            return p[0] if p else None
        if t == "get_sounds":
            return p[0] if p else None
        if t == "text_color":
            return p[0] if p else "#000000"

        if t == "calc_basic":
            a = self.eval_param(obj, p[0] if p else 0)
            op = p[1] if len(p) > 1 else "PLUS"
            b = self.eval_param(obj, p[2] if len(p) > 2 else 0)
            if op in ("PLUS", "+"):
                if isinstance(a, str) or isinstance(b, str):
                    try:
                        return _num(a) + _num(b)
                    except Exception:
                        return _safe_text(a) + _safe_text(b)
                return _num(a) + _num(b)
            if op in ("MINUS", "-"):
                return _num(a) - _num(b)
            if op in ("MULTI", "MULTIPLY", "*"):
                return _num(a) * _num(b)
            if op in ("DIVIDE", "/"):
                den = _num(b)
                return 0 if abs(den) < 1e-9 else _num(a) / den
            if op in ("MOD", "%"):
                den = _num(b)
                return 0 if abs(den) < 1e-9 else _num(a) % den
            return 0

        if t == "calc_rand":
            a = _num(self.eval_param(obj, p[0] if p else 0))
            b = _num(self.eval_param(obj, p[1] if len(p) > 1 else 1))
            if abs(a - int(a)) < 1e-9 and abs(b - int(b)) < 1e-9:
                lo, hi = sorted([int(a), int(b)])
                return random.randint(lo, hi)
            lo, hi = sorted([a, b])
            return random.uniform(lo, hi)

        if t == "boolean_basic_operator":
            a = self.eval_param(obj, p[0] if p else None)
            op = p[1] if len(p) > 1 else "EQUAL"
            b = self.eval_param(obj, p[2] if len(p) > 2 else None)
            if op in ("EQUAL", "=="):
                return self._eq(a, b)
            if op in ("NOT_EQUAL", "!="):
                return not self._eq(a, b)
            if op in ("BIGGER", ">"):
                return _num(a) > _num(b)
            if op in ("SMALLER", "<"):
                return _num(a) < _num(b)
            if op in ("BIGGER_OR_EQUAL", ">="):
                return _num(a) >= _num(b)
            if op in ("SMALLER_OR_EQUAL", "<="):
                return _num(a) <= _num(b)
            return False

        if t == "boolean_and_or":
            a = _truthy(self.eval_param(obj, p[0] if p else False))
            op = p[1] if len(p) > 1 else "AND"
            b = _truthy(self.eval_param(obj, p[2] if len(p) > 2 else False))
            return (a and b) if op == "AND" else (a or b)
        if t == "boolean_not":
            # Entry JSON이 [null, cond, null] 또는 [cond] 형태 둘 다 쓴다.
            cond = p[1] if len(p) > 1 else (p[0] if p else False)
            return not _truthy(self.eval_param(obj, cond))

        if t == "coordinate_mouse":
            which = p[1] if len(p) > 1 else "x"
            return self.mouse_pos_stage[0] if which == "x" else self.mouse_pos_stage[1]
        if t == "coordinate_object":
            target = p[1] if len(p) > 1 else "self"
            prop = p[3] if len(p) > 3 else "x"
            tobj = obj if target == "self" else self.objects_by_id.get(str(target), obj)
            if prop == "x":
                return tobj.state.get("x", 0)
            if prop == "y":
                return tobj.state.get("y", 0)
            if prop == "size":
                return self.get_object_size(tobj)
            if prop in ("direction", "rotation"):
                return tobj.state.get(prop, 0)
            return tobj.state.get(prop, 0)

        if t == "reach_something":
            target = p[1] if len(p) > 1 else None
            if target == "mouse":
                return self.hit_test(obj, *self.mouse_pos_stage)
            tobj = self.objects_by_id.get(str(target))
            if tobj:
                r1 = self.object_rect_stage(obj)
                r2 = self.object_rect_stage(tobj)
                return bool(r1 and r2 and r1.colliderect(r2))
            return False
        if t in ("is_object_clicked",):
            return self.mouse_down and self.hit_test(obj, *self.mouse_pos_stage)
        if t in ("is_clicked", "mouse_clicked"):
            return self.mouse_clicked_this_frame or self.mouse_down
        if t == "mouse_click_cancled":
            return self.mouse_up_this_frame

        if t == "combine_something":
            return _safe_text(self.eval_param(obj, p[0] if p else "")) + _safe_text(self.eval_param(obj, p[1] if len(p) > 1 else ""))
        if t == "substring":
            s = _safe_text(self.eval_param(obj, p[0] if p else ""))
            a = max(1, _int(self.eval_param(obj, p[1] if len(p) > 1 else 1), 1))
            b = max(a, _int(self.eval_param(obj, p[2] if len(p) > 2 else a), a))
            return s[a-1:b]
        if t == "char_at":
            s = _safe_text(self.eval_param(obj, p[0] if p else ""))
            idx = _int(self.eval_param(obj, p[1] if len(p) > 1 else 1), 1) - 1
            return s[idx] if 0 <= idx < len(s) else ""
        if t == "length_of_string":
            return len(_safe_text(self.eval_param(obj, p[0] if p else "")))
        if t == "count_match_string":
            s = _safe_text(self.eval_param(obj, p[0] if p else ""))
            sub = _safe_text(self.eval_param(obj, p[1] if len(p) > 1 else ""))
            return s.count(sub) if sub else 0

        if t == "get_date":
            part = p[1] if len(p) > 1 else (p[0] if p else "HOUR")
            now = _dt.datetime.now()
            m = {
                "YEAR": now.year, "MONTH": now.month, "DATE": now.day, "DAY": now.day,
                "HOUR": now.hour, "MINUTE": now.minute, "SECOND": now.second,
                "DAYOFWEEK": now.isoweekday(), "WEEKDAY": now.isoweekday(),
            }
            return m.get(str(part).upper(), now.hour)

        if t == "value_of_index_from_list":
            lid = p[1] if len(p) > 1 else None
            idx = _int(self.eval_param(obj, p[3] if len(p) > 3 else 1), 1) - 1
            arr = self.lists.get(lid, [])
            return arr[idx] if 0 <= idx < len(arr) else ""
        if t == "length_of_list":
            lid = p[0] if p else None
            return len(self.lists.get(lid, []))
        if t == "is_included_in_list":
            val = self.eval_param(obj, p[0] if p else "")
            lid = p[1] if len(p) > 1 else None
            return _safe_text(val) in [_safe_text(x) for x in self.lists.get(lid, [])]

        if t in ("check_city_weather",):
            return True
        if t in ("get_city_weather_data", "get_day_weather_data", "get_current_city_weather_data"):
            key = p[-1] if p else "temperature"
            dummy = {
                "temperature": 24, "max_temperature": 28, "min_temperature": 20,
                "humidity": 45, "wind_speed": 2.5, "precipitation": 0,
                "weather": "맑음", "sky": "맑음",
            }
            return dummy.get(str(key), 0)
        if t == "check_city_finedust":
            return "좋음"
        if t == "get_korea_area_code":
            return "Seoul"
        if t == "is_current_device_type":
            return False
        if t == "get_project_timer_value":
            return time.monotonic() - self.timer_start

        if t in self.functions:
            # value 함수는 완전한 return 구조가 없는 경우가 많아서 안전한 더미값 반환.
            return 0

        if self.debug and t not in self._block_log_once:
            self._block_log_once.add(t)
            print("미지원 값 블록:", t)
        return 0

    def _eq(self, a: Any, b: Any) -> bool:
        if isinstance(a, (int, float)) or isinstance(b, (int, float)):
            return abs(_num(a) - _num(b)) < 1e-9
        return _safe_text(a) == _safe_text(b)

    # ---------- sound ----------
    def resolve_sound_id(self, obj: RuntimeObject, value: Any, *, global_search: bool = False) -> Optional[str]:
        if value is None:
            return None
        raw = _safe_text(value).strip()
        if not raw:
            return None

        def match_in(sounds: List[Dict[str, Any]]) -> Optional[str]:
            for snd in sounds or []:
                if raw == _safe_text(snd.get("id")):
                    return snd.get("id")
            for snd in sounds or []:
                names = [snd.get("name"), snd.get("filename"), os.path.basename(_safe_text(snd.get("path")))]
                if any(raw == _safe_text(n) for n in names):
                    return snd.get("id")
            if raw.replace(".", "", 1).isdigit():
                idx = _int(raw, 1) - 1
                if 0 <= idx < len(sounds or []):
                    return sounds[idx].get("id")
            return None

        found = match_in(obj.data.get("sounds", []) or [])
        if found:
            return found
        if raw in self.sounds:
            return raw
        if global_search:
            for other in self.objects:
                found = match_in(other.data.get("sounds", []) or [])
                if found:
                    return found
        return raw

    def _channel_for_sound_key(self, key: str) -> Optional[pygame.mixer.Channel]:
        try:
            if key in self._sound_channels:
                return self._sound_channels[key]
            usable = max(1, self._mixer_channel_count - 1)
            # 0번은 BGM 전용. 나머지를 FastEntry처럼 오브젝트별 활성 사운드 슬롯으로 쓴다.
            slot = 1 + (len(self._sound_channels) % usable)
            ch = pygame.mixer.Channel(slot)
            self._sound_channels[key] = ch
            return ch
        except Exception:
            return None

    def stop_sfx(self, obj_id: Optional[str] = None) -> None:
        try:
            if obj_id is None:
                seen = set()
                for ch in self._sound_channels.values():
                    if id(ch) not in seen:
                        ch.stop()
                        seen.add(id(ch))
                self._sound_channel_end.clear()
                self._sound_channel_sid.clear()
                self._last_sfx_time.clear()
            else:
                for key, ch in list(self._sound_channels.items()):
                    if key == obj_id or key.startswith(obj_id + "#"):
                        ch.stop()
                        self._sound_channel_end.pop(key, None)
                        self._sound_channel_sid.pop(key, None)
                for key in list(self._last_sfx_time):
                    if key[0] == obj_id:
                        self._last_sfx_time.pop(key, None)
        except Exception:
            pass

    def play_sound(self, sid: Any, obj_id: str = "", wait_for_sound: bool = False, duration: Optional[float] = None) -> None:
        if sid is None:
            return
        sid_s = str(sid)
        sound = self.sounds.get(sid_s)
        if not sound:
            return
        now = time.monotonic()
        obj_key = obj_id or "_global"
        key = (obj_key, sid_s)
        length = self.sound_lengths.get(sid_s, 0.0)
        play_time = max(0.0, duration if duration is not None else length)

        # 같은 오브젝트의 같은 소리가 이미 재생 중이면 새로 겹쳐 틀지 않는다.
        # 이게 이번 반복 효과음 문제의 핵심 수정이야.
        active_sid = self._sound_channel_sid.get(obj_key)
        active_until = self._sound_channel_end.get(obj_key, 0.0)
        ch = self._channel_for_sound_key(obj_key)
        if ch and active_sid == sid_s and (ch.get_busy() or now < active_until):
            return

        # 그래도 같은 블록이 매우 빠르게 두 번 들어오는 경우를 한 번 더 막는다.
        guard = max(0.08, min(1.0, play_time if play_time > 0 else 0.25))
        if not wait_for_sound and now - self._last_sfx_time.get(key, -999.0) < guard:
            return
        self._last_sfx_time[key] = now

        try:
            if ch:
                # FastEntry는 objectId별로 기존 active sound를 교체/관리한다.
                # PyGame에서는 같은 오브젝트 채널에만 재생해서 중첩 폭주를 막는다.
                ch.stop()
                ch.set_volume(self._sfx_volume)
                ch.play(sound, loops=0)
                self._sound_channel_sid[obj_key] = sid_s
                self._sound_channel_end[obj_key] = now + (play_time if play_time > 0 else max(length, 0.25))
            else:
                sound.play()
        except Exception:
            pass

    def play_bgm(self, sid: Any) -> None:
        if sid is None:
            return
        sid_s = str(sid)
        sound = self.sounds.get(sid_s)
        if not sound:
            return
        now = time.monotonic()
        try:
            # FastEntry의 playBackgroundMusic 기본값은 loop=false다.
            # 이전 버전처럼 loops=-1로 돌리면 부팅음/효과음이 무한 반복돼서 실제 엔트리와 달라진다.
            if self._current_bgm_sid == sid_s and self.bg_channel and (self.bg_channel.get_busy() or now < self._current_bgm_end):
                return
            self._current_bgm_sid = sid_s
            self._current_bgm_end = now + max(self.sound_lengths.get(sid_s, 0.0), 0.25)
            if self.bg_channel:
                self.bg_channel.stop()
                self.bg_channel.set_volume(self._bgm_volume)
                self.bg_channel.play(sound, loops=0)
            else:
                sound.play(loops=0)
        except Exception:
            pass

    # ---------- rendering / hit test ----------
    def stage_to_screen(self, x: float, y: float) -> Tuple[int, int]:
        return int(round(STAGE_W / 2 + x)), int(round(STAGE_H / 2 - y))

    def screen_to_stage(self, px: int, py: int) -> Tuple[float, float]:
        return px / self.scale - STAGE_W / 2, STAGE_H / 2 - py / self.scale

    def _hex_to_color(self, c: Any, fallback=(0, 0, 0, 255)):
        if not c or c == "transparent":
            return fallback
        s = str(c).strip()
        if s.startswith("#") and len(s) in (7, 9):
            try:
                r = int(s[1:3], 16); g = int(s[3:5], 16); b = int(s[5:7], 16)
                a = int(s[7:9], 16) if len(s) == 9 else 255
                return (r, g, b, a)
            except Exception:
                return fallback
        return fallback

    def get_font(self, size: int, bold: bool = False) -> pygame.font.Font:
        size = max(6, min(96, int(size)))
        key = (size, bold)
        if key not in self.font_cache:
            names = ["NanumGothic", "Malgun Gothic", "AppleGothic", "Noto Sans CJK KR", "Arial Unicode MS"]
            path = None
            for name in names:
                path = pygame.font.match_font(name, bold=bold)
                if path:
                    break
            self.font_cache[key] = pygame.font.Font(path, size) if path else pygame.font.SysFont(None, size, bold=bold)
        return self.font_cache[key]

    def resolve_picture_id(self, obj: RuntimeObject, value: Any) -> Optional[str]:
        if value is None:
            return None
        pics = obj.data.get("pictures", []) or []
        if not pics:
            return None
        raw = _safe_text(value).strip()
        # Entry 블록은 보통 id를 주지만, 이름/파일명/숫자 인덱스가 들어오는 작품도 있어서 전부 처리한다.
        for pic in pics:
            if raw == _safe_text(pic.get("id")):
                return pic.get("id")
        for pic in pics:
            names = [pic.get("name"), pic.get("filename"), os.path.basename(_safe_text(pic.get("path")))]
            if any(raw == _safe_text(n) for n in names):
                return pic.get("id")
        if raw.replace(".", "", 1).isdigit():
            idx = _int(raw, 1) - 1
            if 0 <= idx < len(pics):
                return pics[idx].get("id")
        return None

    def get_object_base_size(self, obj: RuntimeObject) -> Tuple[float, float]:
        """현재 모양의 원본 크기(px)를 돌려준다. FastEntry의 Entity::getSize 계산과 맞추기 위한 헬퍼."""
        if obj.object_type == "textBox" or obj.data.get("text") is not None or not obj.data.get("pictures"):
            return max(1.0, _num(obj.state.get("width"), 100)), max(1.0, _num(obj.state.get("height"), 24))
        pic = self.current_picture(obj)
        if pic:
            surf = self.images.get(pic.get("id"))
            if surf:
                w, h = surf.get_size()
                return max(1.0, float(w)), max(1.0, float(h))
            dim = pic.get("dimension") or {}
            return max(1.0, _num(dim.get("width"), obj.state.get("width", 100))), max(1.0, _num(dim.get("height"), obj.state.get("height", 100)))
        return max(1.0, _num(obj.state.get("width"), 100)), max(1.0, _num(obj.state.get("height"), 100))

    def get_object_size(self, obj: RuntimeObject) -> float:
        bw, bh = self.get_object_base_size(obj)
        return (bw * abs(_num(obj.state.get("scaleX", 1), 1)) + bh * abs(_num(obj.state.get("scaleY", 1), 1))) / 2.0

    def set_object_size(self, obj: RuntimeObject, size: float) -> None:
        target = max(0.0, _num(size, 0))
        current = self.get_object_size(obj)
        bw, bh = self.get_object_base_size(obj)
        if current <= 1e-9:
            base = max(1.0, (bw + bh) / 2.0)
            mag = target / base
            sign_x = -1 if obj.state.get("scaleX", 1) < 0 else 1
            sign_y = -1 if obj.state.get("scaleY", 1) < 0 else 1
            obj.state["scaleX"] = sign_x * mag
            obj.state["scaleY"] = sign_y * mag
            return
        mult = target / current
        obj.state["scaleX"] = _num(obj.state.get("scaleX", 1), 1) * mult
        obj.state["scaleY"] = _num(obj.state.get("scaleY", 1), 1) * mult

    def current_picture(self, obj: RuntimeObject) -> Optional[Dict[str, Any]]:
        pid = obj.state.get("selectedPictureId")
        for pic in obj.data.get("pictures", []):
            if pic.get("id") == pid:
                return pic
        # 잘못된 모양 id가 들어오면 무조건 첫 번째 모양으로 튀던 문제를 줄인다.
        resolved = self.resolve_picture_id(obj, pid)
        if resolved:
            obj.state["selectedPictureId"] = resolved
            for pic in obj.data.get("pictures", []):
                if pic.get("id") == resolved:
                    return pic
        pics = obj.data.get("pictures", [])
        return pics[0] if pics else None

    def _scaled_rect_from_entry_entity(self, obj: RuntimeObject, natural_w: float, natural_h: float, *, text_like: bool = False) -> pygame.Rect:
        """Entry의 x/y는 대부분 오브젝트의 중심 좌표로 동작한다.
        이전 버전은 textBox만 좌상단 좌표처럼 처리해서, 글상자/입력창/패널이 오른쪽 아래로 밀려 보였어.
        여기서는 이미지와 텍스트 모두 같은 중심 기준 좌표계로 맞춘다.
        """
        sx = abs(_num(obj.state.get("scaleX", 1), 1))
        sy = abs(_num(obj.state.get("scaleY", 1), 1))
        w = max(1, int(round(natural_w * sx)))
        h = max(1, int(round(natural_h * sy)))
        cx, cy = self.stage_to_screen(obj.state["x"], obj.state["y"])
        # Entry textBox는 regX/regY가 0으로 저장되는 경우가 많지만 실제 배치는 중심 좌표에 가깝다.
        # 그래서 텍스트류는 reg가 0이면 중심점을 기준으로 둔다.
        if text_like and abs(_num(obj.state.get("regX", 0))) < 1e-9 and abs(_num(obj.state.get("regY", 0))) < 1e-9:
            regx = w / 2
            regy = h / 2
        else:
            regx = _num(obj.state.get("regX", natural_w / 2), natural_w / 2) * sx
            regy = _num(obj.state.get("regY", natural_h / 2), natural_h / 2) * sy
        return pygame.Rect(int(round(cx - regx)), int(round(cy - regy)), w, h)

    def object_rect_stage(self, obj: RuntimeObject) -> Optional[pygame.Rect]:
        if obj.object_type == "textBox" or obj.data.get("text") is not None or not obj.data.get("pictures"):
            w = max(8, _num(obj.state.get("width"), 100))
            h = max(8, _num(obj.state.get("height"), 24))
            return self._scaled_rect_from_entry_entity(obj, w, h, text_like=True)
        pic = self.current_picture(obj)
        if not pic:
            return None
        surf = self.images.get(pic.get("id"))
        if not surf:
            return None
        w, h = surf.get_size()
        return self._scaled_rect_from_entry_entity(obj, w, h)

    def hit_test(self, obj: RuntimeObject, x: float, y: float) -> bool:
        if obj.deleted or not obj.state.get("visible", True):
            return False
        transparency = _clamp(_num(obj.state.get("effects", {}).get("transparency", 0)), 0, 100)
        if transparency >= 99.5:
            return False
        rect = self.object_rect_stage(obj)
        if not rect:
            return False
        px, py = self.stage_to_screen(x, y)
        if not rect.collidepoint(px, py):
            return False

        # FastEntry/Entry 쪽은 투명 PNG의 빈 영역을 그냥 사각형으로 클릭하지 않는다.
        # 그래서 큰 투명 이미지나 전환용 이미지가 버튼 위를 덮어도 아래 버튼을 누를 수 있게 알파 히트 테스트를 넣었다.
        if obj.object_type != "textBox" and obj.data.get("pictures"):
            pic = self.current_picture(obj)
            src = self.images.get(pic.get("id")) if pic else None
            if src is not None and abs(_num(obj.state.get("rotation", 0))) < 0.01:
                sx = _num(obj.state.get("scaleX", 1), 1)
                sy = _num(obj.state.get("scaleY", 1), 1)
                ax = max(abs(sx), 1e-9)
                ay = max(abs(sy), 1e-9)
                lx = (px - rect.left) / ax
                ly = (py - rect.top) / ay
                w, h = src.get_size()
                if sx < 0:
                    lx = w - 1 - lx
                if sy < 0:
                    ly = h - 1 - ly
                ix = int(_clamp(lx, 0, w - 1))
                iy = int(_clamp(ly, 0, h - 1))
                try:
                    return src.get_at((ix, iy)).a > 8
                except Exception:
                    return True
        return True

    def get_cached_image(self, pic_id: str, src: pygame.Surface, sw: int, sh: int, flip_x: bool, flip_y: bool, rot: float, alpha: int, brightness: float) -> pygame.Surface:
        # 회전값은 너무 세밀하게 잡으면 캐시가 폭증해서 0.1도 단위로 묶는다.
        rot_key = round(float(rot), 1)
        bright_key = int(round(_clamp(brightness, -100, 100)))
        key = (pic_id, sw, sh, bool(flip_x), bool(flip_y), rot_key, int(alpha), bright_key)
        cached = self._image_cache.get(key)
        if cached is not None:
            self._image_cache.move_to_end(key)
            return cached
        img = pygame.transform.smoothscale(src, (sw, sh))
        if flip_x or flip_y:
            img = pygame.transform.flip(img, flip_x, flip_y)
        if abs(rot_key) > 0.01:
            img = pygame.transform.rotate(img, -rot_key)
        if alpha < 255 or bright_key:
            img = img.copy()
        if alpha < 255:
            img.set_alpha(alpha)
        if bright_key:
            val = int(bright_key * 2.0)
            if val > 0:
                img.fill((val, val, val, 0), special_flags=pygame.BLEND_RGBA_ADD)
            else:
                img.fill((-val, -val, -val, 0), special_flags=pygame.BLEND_RGBA_SUB)
        self._image_cache[key] = img
        if len(self._image_cache) > self._image_cache_limit:
            self._image_cache.popitem(last=False)
        return img

    def draw_object(self, surface: pygame.Surface, obj: RuntimeObject) -> None:
        if obj.deleted or not obj.state.get("visible", True) or obj.scene != self.current_scene:
            return
        transparency = _clamp(_num(obj.state.get("effects", {}).get("transparency", 0)), 0, 100)
        alpha = int(255 * (100 - transparency) / 100)
        if alpha <= 0:
            return
        if obj.object_type == "textBox" or obj.data.get("text") is not None or not obj.data.get("pictures"):
            self.draw_text_object(surface, obj, alpha)
            return
        pic = self.current_picture(obj)
        if not pic:
            return
        src = self.images.get(pic.get("id"))
        if src is None:
            return
        w, h = src.get_size()
        sx = _num(obj.state.get("scaleX", 1), 1)
        sy = _num(obj.state.get("scaleY", 1), 1)
        sw = max(1, int(abs(w * sx)))
        sh = max(1, int(abs(h * sy)))
        rot = _num(obj.state.get("rotation", 0))
        brightness = _num(obj.state.get("effects", {}).get("brightness", 0))
        img = self.get_cached_image(pic.get("id"), src, sw, sh, sx < 0, sy < 0, rot, alpha, brightness)
        base_rect = self._scaled_rect_from_entry_entity(obj, w, h)
        rect = img.get_rect()
        # 회전한 경우 중심 기준 배치로 보정
        if abs(rot) > 0.01:
            rect.center = base_rect.center
        else:
            rect.topleft = base_rect.topleft
        surface.blit(img, rect)

    def draw_text_object(self, surface: pygame.Surface, obj: RuntimeObject, alpha: int) -> None:
        text = _safe_text(obj.state.get("text", ""))
        if text == "":
            return
        size = _int(obj.state.get("fontSize", 16), 16)
        bold = "bold" in _safe_text(obj.state.get("font", "")).lower()
        font = self.get_font(size, bold=bold)
        color = self._hex_to_color(obj.state.get("colour", "#000000"), (0, 0, 0, alpha))
        color = (color[0], color[1], color[2], alpha)
        lines = text.splitlines() or [text]
        rendered = [font.render(line if line else " ", True, color) for line in lines]
        base_w = max([r.get_width() for r in rendered] + [int(_num(obj.state.get("width", 1), 1))])
        base_h = max(sum(r.get_height() for r in rendered), int(_num(obj.state.get("height", 1), 1)))
        surf = pygame.Surface((max(1, base_w), max(1, base_h)), pygame.SRCALPHA)
        bg = obj.state.get("bgColor")
        if bg and bg != "transparent":
            bc = self._hex_to_color(bg, (0, 0, 0, 0))
            surf.fill((bc[0], bc[1], bc[2], min(alpha, bc[3])))
        y = 0
        for r in rendered:
            x = 0
            align = obj.state.get("textAlign", 0)
            if align in (1, "center"):
                x = (base_w - r.get_width()) // 2
            elif align in (2, "right"):
                x = base_w - r.get_width()
            surf.blit(r, (x, y))
            y += r.get_height()
        sxv = abs(_num(obj.state.get("scaleX", 1), 1))
        syv = abs(_num(obj.state.get("scaleY", 1), 1))
        if abs(sxv - 1) > 1e-6 or abs(syv - 1) > 1e-6:
            surf = pygame.transform.smoothscale(surf, (max(1, int(round(base_w * sxv))), max(1, int(round(base_h * syv)))))
        if alpha < 255:
            surf = surf.copy(); surf.set_alpha(alpha)
        rect = self._scaled_rect_from_entry_entity(obj, base_w, base_h, text_like=True)
        surface.blit(surf, rect.topleft)

    def draw_lists(self, surface: pygame.Surface) -> None:
        for lid, visible in self.list_visible.items():
            if not visible:
                continue
            meta = self.variables_meta.get(lid, {})
            x = _num(meta.get("x", -230)); y = _num(meta.get("y", 120))
            w = max(60, _int(meta.get("width", 120), 120)); h = max(40, _int(meta.get("height", 120), 120))
            px, py = self.stage_to_screen(x, y)
            rect = pygame.Rect(px, py, w, h)
            panel = pygame.Surface((w, h), pygame.SRCALPHA)
            panel.fill((255, 255, 255, 220))
            pygame.draw.rect(panel, (70, 70, 70, 255), panel.get_rect(), 1)
            font = self.get_font(10)
            title = font.render(meta.get("name", lid), True, (0, 0, 0))
            panel.blit(title, (4, 3))
            yy = 18
            for i, item in enumerate(self.lists.get(lid, [])[:12], 1):
                txt = font.render(f"{i}. {_safe_text(item)[:22]}", True, (0, 0, 0))
                panel.blit(txt, (4, yy)); yy += 13
                if yy > h - 12:
                    break
            surface.blit(panel, rect)

    def render(self) -> None:
        assert self.screen is not None and self.stage_surface is not None
        self.stage_surface.fill((255, 255, 255, 255))
        all_objs = [o for o in self.objects if o.scene == self.current_scene] + [c for c in self.clones if c.scene == self.current_scene and not c.deleted]
        for obj in sorted(all_objs, key=lambda o: o.layer):
            self.draw_object(self.stage_surface, obj)
        self.draw_lists(self.stage_surface)
        if self.ask_prompt is not None:
            self.draw_ask_overlay(self.stage_surface)
        if self.debug:
            self.draw_debug(self.stage_surface)
        scaled = pygame.transform.smoothscale(self.stage_surface, (self.screen.get_width(), self.screen.get_height()))
        self.screen.blit(scaled, (0, 0))
        pygame.display.flip()

    def draw_ask_overlay(self, surface: pygame.Surface) -> None:
        # Entry의 질문 입력 UI는 무대 중앙을 크게 가리는 모달보다는 하단 입력줄에 가깝다.
        # 이전 중앙 400x100 박스는 실제 오브젝트가 커진 것처럼 보여서 하단 바 형태로 수정했다.
        overlay = pygame.Surface((STAGE_W, STAGE_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 45))
        surface.blit(overlay, (0, 0))
        box = pygame.Rect(18, STAGE_H - 58, STAGE_W - 36, 46)
        pygame.draw.rect(surface, (255, 255, 255), box, border_radius=7)
        pygame.draw.rect(surface, (45, 45, 45), box, 1, border_radius=7)
        font = self.get_font(12, bold=True)
        small = self.get_font(12)
        prompt = _safe_text(self.ask_prompt or "입력")[:48]
        surface.blit(font.render(prompt, True, (0, 0, 0)), (box.x + 9, box.y + 5))
        input_rect = pygame.Rect(box.x + 9, box.y + 24, box.w - 78, 17)
        pygame.draw.rect(surface, (235, 235, 235), input_rect, border_radius=3)
        surface.blit(small.render(self.ask_buffer[-38:], True, (0, 0, 0)), (input_rect.x + 5, input_rect.y + 1))
        hint = self.get_font(9).render("Enter", True, (80, 80, 80))
        surface.blit(hint, (box.right - hint.get_width() - 12, input_rect.y + 2))

    def draw_debug(self, surface: pygame.Surface) -> None:
        font = self.get_font(10)
        txt = f"scene={self.scene_name.get(self.current_scene,self.current_scene)} threads={len(self.threads)} clones={len(self.clones)} mouse={self.mouse_pos_stage}"
        bar = pygame.Surface((STAGE_W, 14), pygame.SRCALPHA)
        bar.fill((0, 0, 0, 140))
        surface.blit(bar, (0, 0))
        surface.blit(font.render(txt, True, (255, 255, 255)), (4, 2))

    # ---------- events ----------
    def objects_at(self, x: float, y: float, *, hat_type: Optional[str] = None) -> List[RuntimeObject]:
        candidates = [o for o in self.objects if o.scene == self.current_scene] + [c for c in self.clones if c.scene == self.current_scene]
        found: List[RuntimeObject] = []
        for obj in sorted(candidates, key=lambda o: o.layer, reverse=True):
            if hat_type and not self.object_has_hat(obj, hat_type):
                continue
            if self.hit_test(obj, x, y):
                found.append(obj)
        return found

    def top_object_at(self, x: float, y: float) -> Optional[RuntimeObject]:
        objs = self.objects_at(x, y)
        return objs[0] if objs else None

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.QUIT:
            self.running = False
            return
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.running = False
                return
            if self.ask_prompt is not None:
                if event.key == pygame.K_RETURN:
                    self.ask_done = True
                elif event.key == pygame.K_BACKSPACE:
                    self.ask_buffer = self.ask_buffer[:-1]
                elif event.unicode:
                    self.ask_buffer += event.unicode
                return
            if event.key == pygame.K_r and (pygame.key.get_mods() & pygame.KMOD_CTRL):
                self.reset_project()
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.mouse_down = True
            self.mouse_clicked_this_frame = True
            x, y = self.screen_to_stage(*event.pos)
            self.mouse_pos_stage = (x, y)
            # 전역 mouse_clicked 모자
            self.spawn_hat("mouse_clicked", scene_only=True)
            # Entry/FastEntry 방식에 가깝게, 클릭 모자가 있는 모든 맞은 오브젝트를 실행한다.
            # 이전처럼 '제일 위 오브젝트 1개'만 고르면 날씨/음악 같은 장면에서 투명 패널이 홈버튼을 가로막았다.
            for obj in self.objects_at(x, y, hat_type="when_object_click"):
                self.spawn_hat("when_object_click", obj_filter=obj, scene_only=False)
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.mouse_down = False
            self.mouse_up_this_frame = True
            x, y = self.screen_to_stage(*event.pos)
            self.mouse_pos_stage = (x, y)
            self.spawn_hat("mouse_click_cancled", scene_only=True)
            for obj in self.objects_at(x, y, hat_type="when_object_click_canceled"):
                self.spawn_hat("when_object_click_canceled", obj_filter=obj, scene_only=False)
        if event.type == pygame.MOUSEMOTION:
            self.mouse_pos_stage = self.screen_to_stage(*event.pos)

    def run(self) -> None:
        self.init_pygame()
        self.reset_project()
        clock = pygame.time.Clock()
        while self.running:
            self.mouse_clicked_this_frame = False
            self.mouse_up_this_frame = False
            for event in pygame.event.get():
                self.handle_event(event)
            self.step_threads()
            self.render()
            clock.tick(FPS)
        pygame.quit()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Run an Entry project converted to PyGame")
    ap.add_argument("project", nargs="?", default="project_data.json", help="converted project_data.json")
    ap.add_argument("--scale", type=float, default=2.0, help="window scale; 2 = 960x540 (default), 1 = 480x270, 0.75 = 360x202")
    ap.add_argument("--debug", action="store_true", help="show debug overlay and unsupported block logs")
    ap.add_argument("--scene", default=None, help="start scene id or name")
    ns = ap.parse_args(argv)
    project = os.path.abspath(ns.project)
    start_scene = ns.scene
    if start_scene:
        try:
            with open(project, encoding="utf-8") as f:
                d = json.load(f)
            for s in d.get("scenes", []):
                if s.get("name") == start_scene:
                    start_scene = s.get("id")
                    break
        except Exception:
            pass
    EntryRuntime(project, scale=ns.scale, debug=ns.debug, start_scene=start_scene).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
