#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Entry(.ent) -> PyGame 변환기

사용법:
  python entry_to_pygame_converter.py "HG OS 2 Beta _v2_0_6_.ent" HG_OS_2_Beta_pygame
  cd HG_OS_2_Beta_pygame
  python main.py

이 변환기는 .ent(gzip tar 또는 zip)를 풀고 project.json을 정규화한 뒤,
이미지/소리 리소스를 assets 폴더에 복사/변환하고 PyGame 런타임으로 실행 가능한 폴더를 만든다.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import os
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List

try:
    import cairosvg
except Exception:
    cairosvg = None

IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "bmp", "gif", "svg"}
SOUND_EXTS = {"mp3", "wav", "ogg", "m4a", "aac"}

SUPPORTED_BLOCKS = {
    "when_run_button_click", "when_scene_start", "when_message_cast", "when_object_click", "when_clone_start",
    "mouse_clicked", "mouse_click_cancled", "when_object_click_canceled",
    "repeat_inf", "repeat_basic", "repeat_while_true", "_if", "if_else", "wait_second", "wait_until_true",
    "set_variable", "change_variable", "get_variable", "show", "hide", "locate", "locate_xy", "locate_x", "locate_y",
    "move_x", "move_y", "move_to_angle", "move_direction", "rotate_relative", "set_scale_size", "change_scale_size",
    "change_to_some_shape", "get_pictures", "add_effect_amount", "change_effect_amount", "erase_all_effects",
    "text_write", "text_append", "text_change_font_color", "text_color",
    "message_cast", "start_scene", "start_neighbor_scene", "create_clone", "delete_clone", "remove_all_clones",
    "change_object_index", "sound_something_with_block", "sound_something_wait_with_block", "sound_something_second_with_block",
    "sound_silent_all", "play_bgm", "stop_bgm", "get_sounds", "ask_and_wait", "get_canvas_input_value",
    "add_value_to_list", "remove_value_from_list", "show_list", "hide_list", "value_of_index_from_list", "length_of_list",
    "is_included_in_list", "stop_object", "stop_run", "restart_project",
    "number", "text", "angle", "calc_basic", "calc_rand", "boolean_basic_operator", "boolean_and_or", "boolean_not",
    "coordinate_mouse", "coordinate_object", "reach_something", "is_object_clicked", "is_clicked", "combine_something",
    "get_nickname", "get_date", "substring", "length_of_string", "char_at", "count_match_string", "True",
    "set_visible_speech_to_text", "set_visible_project_timer", "set_visible_answer", "speech_to_text_convert",
    "speech_to_text_get_value", "read_text", "dialog_time", "choose_project_timer_action", "get_project_timer_value",
    "media_pipe_video_screen", "check_city_weather", "get_city_weather_data", "get_day_weather_data",
    "get_current_city_weather_data", "check_city_finedust", "get_korea_area_code", "is_current_device_type",
    "stop_repeat", "continue_repeat", "function_create", "function_create_value", "function_field_label", "function_field_string",
}


def parse_json_string(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return []
    return value or []


def safe_name(s: str) -> str:
    keep = []
    for ch in s:
        if ch.isalnum() or ch in "._-":
            keep.append(ch)
        else:
            keep.append("_")
    out = "".join(keep).strip("_")
    return out or "asset"


def extract_ent(ent_path: Path, workdir: Path) -> Path:
    # Entry .ent는 버전에 따라 zip이거나 gzip tar일 수 있다.
    if zipfile.is_zipfile(ent_path):
        with zipfile.ZipFile(ent_path) as zf:
            zf.extractall(workdir)
    else:
        try:
            with tarfile.open(ent_path, "r:gz") as tf:
                tf.extractall(workdir)
        except tarfile.TarError:
            # 혹시 순수 gzip이면 한 번 풀어서 tar 재시도
            raw = workdir / "raw.tar"
            with gzip.open(ent_path, "rb") as gf, open(raw, "wb") as f:
                shutil.copyfileobj(gf, f)
            with tarfile.open(raw, "r:") as tf:
                tf.extractall(workdir)
    candidates = list(workdir.rglob("project.json"))
    if not candidates:
        raise FileNotFoundError("project.json을 찾지 못했어. 이 .ent 구조는 아직 지원하지 않는 형태야.")
    return candidates[0]


def resolve_existing_asset(src: Path) -> Path:
    if src.exists():
        return src
    # Entry JSON의 fileurl 확장자와 실제 저장 확장자가 다른 경우가 꽤 있다.
    for ext in [".png", ".jpg", ".jpeg", ".webp", ".svg", ".mp3", ".wav", ".ogg"]:
        cand = src.with_suffix(ext)
        if cand.exists():
            return cand
    return src


def _sanitize_svg_bytes(raw: bytes) -> bytes:
    # Illustrator SVG의 내부 엔티티 선언 때문에 defusedxml이 막는 경우가 있어,
    # 외부 엔티티를 해석하지 않고 필요한 namespace 문자열만 직접 치환한다.
    txt = raw.decode("utf-8", errors="replace")
    import re
    txt = re.sub(r"<!DOCTYPE[\s\S]*?\]>", "", txt)
    replacements = {
        "&ns_extend;": "http://ns.adobe.com/Extensibility/1.0/",
        "&ns_ai;": "http://ns.adobe.com/AdobeIllustrator/10.0/",
        "&ns_graphs;": "http://ns.adobe.com/Graphs/1.0/",
        "&ns_vars;": "http://ns.adobe.com/Variables/1.0/",
        "&ns_imrep;": "http://ns.adobe.com/ImageReplacement/1.0/",
        "&ns_sfw;": "http://ns.adobe.com/SaveForWeb/1.0/",
        "&ns_custom;": "http://ns.adobe.com/GenericCustomNamespace/1.0/",
        "&ns_adobe_xpath;": "http://ns.adobe.com/XPath/1.0/",
    }
    for a, b in replacements.items():
        txt = txt.replace(a, b)
    return txt.encode("utf-8")


def convert_image(src: Path, dst: Path) -> None:
    src = resolve_existing_asset(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() == ".svg":
        if cairosvg is not None:
            raw = _sanitize_svg_bytes(src.read_bytes())
            cairosvg.svg2png(bytestring=raw, write_to=str(dst.with_suffix(".png")))
        else:
            shutil.copy2(src, dst)
    else:
        shutil.copy2(src, dst)


def walk_blocks(node: Any, counter: collections.Counter) -> None:
    if isinstance(node, dict):
        t = node.get("type")
        if t:
            counter[t] += 1
        for p in node.get("params") or []:
            walk_blocks(p, counter)
        for st in node.get("statements") or []:
            walk_blocks(st, counter)
    elif isinstance(node, list):
        for x in node:
            walk_blocks(x, counter)


def normalize_project(project_json: Path, outdir: Path) -> Dict[str, Any]:
    root = project_json.parent.parent if project_json.parent.name == "temp" else project_json.parent
    data = json.loads(project_json.read_text(encoding="utf-8"))
    assets_dir = outdir / "assets"
    img_dir = assets_dir / "images"
    snd_dir = assets_dir / "sounds"
    img_dir.mkdir(parents=True, exist_ok=True)
    snd_dir.mkdir(parents=True, exist_ok=True)

    objects = []
    block_counter = collections.Counter()

    for obj in data.get("objects", []):
        no = {
            "id": obj.get("id"),
            "name": obj.get("name"),
            "objectType": obj.get("objectType", "sprite"),
            "rotateMethod": obj.get("rotateMethod", "free"),
            "scene": obj.get("scene"),
            "selectedPictureId": obj.get("selectedPictureId"),
            "entity": obj.get("entity", {}),
            "text": obj.get("text"),
            "pictures": [],
            "sounds": [],
            "scripts": parse_json_string(obj.get("script")),
        }
        walk_blocks(no["scripts"], block_counter)
        sprite = obj.get("sprite") or {}
        for pic in sprite.get("pictures", []):
            fileurl = pic.get("fileurl") or ""
            src = root / fileurl if fileurl.startswith("temp/") else project_json.parent / fileurl
            src = resolve_existing_asset(src)
            ext = (pic.get("imageType") or src.suffix.lstrip(".") or "png").lower()
            if ext == "jpeg":
                ext = "jpg"
            # SVG는 변환 후 PNG로 저장
            out_ext = "png" if ext == "svg" else ext
            pid = pic.get("id") or safe_name(pic.get("filename", "pic"))
            dst_rel = f"assets/images/{safe_name(pid)}.{out_ext}"
            dst = outdir / dst_rel
            try:
                convert_image(src, dst)
                if ext == "svg" and dst.suffix.lower() != ".png":
                    dst_rel = f"assets/images/{safe_name(pid)}.png"
            except Exception as e:
                print(f"이미지 변환 실패: {src} -> {e}")
                continue
            npic = dict(pic)
            npic["path"] = dst_rel
            no["pictures"].append(npic)
        for snd in sprite.get("sounds", []):
            fileurl = snd.get("fileurl") or ""
            src = root / fileurl if fileurl.startswith("temp/") else project_json.parent / fileurl
            src = resolve_existing_asset(src)
            ext = (snd.get("ext") or snd.get("type") or src.suffix.lstrip(".") or "mp3").lower().replace(".", "")
            if ext not in SOUND_EXTS:
                ext = src.suffix.lstrip(".") or "mp3"
            sid = snd.get("id") or safe_name(snd.get("filename", "sound"))
            dst_rel = f"assets/sounds/{safe_name(sid)}.{ext}"
            dst = outdir / dst_rel
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            except Exception as e:
                print(f"소리 복사 실패: {src} -> {e}")
                continue
            nsnd = dict(snd)
            nsnd["path"] = dst_rel
            no["sounds"].append(nsnd)
        objects.append(no)

    for f in data.get("functions", []):
        content = parse_json_string(f.get("content"))
        walk_blocks(content, block_counter)

    out = {
        "name": data.get("name", "Converted Entry Project"),
        "speed": data.get("speed", 60),
        "interface": data.get("interface", {}),
        "scenes": data.get("scenes", []),
        "variables": data.get("variables", []),
        "messages": data.get("messages", []),
        "functions": data.get("functions", []),
        "objects": objects,
        "conversion": {
            "source": str(project_json),
            "object_count": len(objects),
            "scene_count": len(data.get("scenes", [])),
            "block_counts": dict(block_counter),
            "unsupported_blocks": {k: v for k, v in block_counter.items() if k not in SUPPORTED_BLOCKS and not k.startswith("func_") and not k.startswith("stringParam_")},
        },
    }
    return out


def write_launcher(outdir: Path) -> None:
    (outdir / "main.py").write_text("""#!/usr/bin/env python3\nfrom entry_pygame_runtime import main\n\nif __name__ == '__main__':\n    raise SystemExit(main())\n""", encoding="utf-8")
    (outdir / "requirements.txt").write_text("pygame>=2.5\npillow>=10.0\ncairosvg>=2.7\n", encoding="utf-8")
    (outdir / "README.md").write_text("""# Entry PyGame 변환본\n\n## 실행\n```bash\npython -m pip install -r requirements.txt\npython main.py\n```\n\n창 크기를 바꾸고 싶으면:\n```bash\npython entry_pygame_runtime.py project_data.json --scale 3\n```\n\n디버그 표시:\n```bash\npython entry_pygame_runtime.py project_data.json --debug\n```\n\n## 조작\n- 마우스 클릭: 엔트리 클릭 이벤트\n- 입력창이 뜨면 글자 입력 후 Enter\n- Esc: 종료\n- Ctrl+R: 프로젝트 재시작\n\n## 참고\nAI, 음성 인식, 카메라, 실시간 날씨/미세먼지 같은 외부 기능은 안전한 더미값 또는 no-op으로 처리돼.\n이미지/텍스트/음악/반복/장면 전환/변수/리스트/복제본 위주로 최대한 보존했어.\n""", encoding="utf-8")


def copy_runtime(outdir: Path) -> None:
    here = Path(__file__).resolve().parent
    src = here / "entry_pygame_runtime.py"
    if not src.exists():
        raise FileNotFoundError("entry_pygame_runtime.py가 변환기와 같은 폴더에 있어야 해.")
    shutil.copy2(src, outdir / "entry_pygame_runtime.py")


def convert(ent_path: Path, outdir: Path) -> Dict[str, Any]:
    ent_path = ent_path.resolve()
    outdir = outdir.resolve()
    if outdir.exists():
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="entry_extract_") as td:
        project_json = extract_ent(ent_path, Path(td))
        project = normalize_project(project_json, outdir)
    (outdir / "project_data.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
    copy_runtime(outdir)
    write_launcher(outdir)
    write_report(outdir, project)
    return project


def write_report(outdir: Path, project: Dict[str, Any]) -> None:
    conv = project.get("conversion", {})
    lines = []
    lines.append(f"프로젝트: {project.get('name')}\n")
    lines.append(f"장면 수: {conv.get('scene_count')}\n")
    lines.append(f"오브젝트 수: {conv.get('object_count')}\n")
    lines.append("\n장면 목록:\n")
    for s in project.get("scenes", []):
        lines.append(f"- {s.get('id')}: {s.get('name')}\n")
    lines.append("\n블록 사용량 TOP:\n")
    counts = conv.get("block_counts", {})
    for k, v in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:120]:
        mark = "지원" if (k in SUPPORTED_BLOCKS or k.startswith("func_") or k.startswith("stringParam_")) else "부분/미지원"
        lines.append(f"- {k}: {v} ({mark})\n")
    unsup = conv.get("unsupported_blocks", {})
    lines.append("\n부분/미지원 블록:\n")
    if unsup:
        for k, v in sorted(unsup.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"- {k}: {v}\n")
    else:
        lines.append("- 없음(또는 런타임에서 no-op/더미값 처리)\n")
    lines.append("\n외부 기능 처리:\n- AI/음성 인식/카메라/실시간 날씨는 런타임에서 안전한 no-op 또는 더미값으로 처리.\n")
    (outdir / "conversion_report.txt").write_text("".join(lines), encoding="utf-8")


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Convert Entry .ent project to a PyGame runnable folder")
    ap.add_argument("input_ent", help="input .ent file")
    ap.add_argument("output_dir", help="output folder")
    ns = ap.parse_args(argv)
    project = convert(Path(ns.input_ent), Path(ns.output_dir))
    print(f"변환 완료: {ns.output_dir}")
    print(f"장면 {project['conversion']['scene_count']}개, 오브젝트 {project['conversion']['object_count']}개")
    if project['conversion'].get('unsupported_blocks'):
        print("부분/미지원 블록:", project['conversion']['unsupported_blocks'])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
