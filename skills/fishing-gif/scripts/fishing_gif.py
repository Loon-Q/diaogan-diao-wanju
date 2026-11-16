#!/usr/bin/env python3
"""Portable preparation, matting and deterministic video compositing CLI."""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import rotation

SKILL = Path(__file__).resolve().parents[1]
RODS = {
    "follow": "01-follow-plus.png", "like": "02-like-heart.png",
    "save": "03-save-star.png", "share": "04-share-arrow.png",
}


def deps():
    global cv2, np, Image, ImageDraw, Scene
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw
    from scene import Scene


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def ffmpeg():
    explicit = os.environ.get("FISHING_FFMPEG")
    if explicit:
        if not Path(explicit).is_file():
            raise ValueError("FISHING_FFMPEG does not point to a file.")
        return explicit
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ff(args):
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", *map(str, args)], check=True)


def doctor():
    missing = [name for name in ("numpy", "cv2", "PIL", "imageio_ffmpeg")
               if importlib.util.find_spec(name) is None]
    report = {"python": sys.version.split()[0], "python_supported": sys.version_info >= (3, 10),
              "missing_modules": missing, "image_generation": "Codex built-in tool required for clean plates"}
    if not missing:
        binary = ffmpeg()
        result = subprocess.run([binary, "-version"], capture_output=True, text=True, check=True)
        report.update(ffmpeg=binary, ffmpeg_version=result.stdout.splitlines()[0])
    report["ready"] = not missing and report["python_supported"]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready"] else 2


def probe(source):
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise ValueError(f"Cannot decode video: {source}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    if fps <= 0 or count <= 0:
        raise ValueError("Video timing unavailable; inspect with FFprobe before preparing.")
    meta = {"width": width, "height": height, "fps": fps, "frames": count, "duration": count / fps}
    binary = shutil.which("ffprobe")
    if binary:
        r = subprocess.run([binary, "-v", "error", "-select_streams", "v:0", "-show_streams",
                            "-of", "json", str(source)], capture_output=True, text=True, check=True)
        streams = json.loads(r.stdout).get("streams", [])
        if streams:
            for key in ("color_transfer", "color_primaries", "color_space", "pix_fmt"):
                meta[key] = streams[0].get(key)
    else:
        r = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(source)], capture_output=True, text=True)
        for transfer in ("smpte2084", "arib-std-b67"):
            if transfer in r.stderr:
                meta["color_transfer"] = transfer
    return meta


def inspect_video(args):
    source = Path(args.input).expanduser().resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    meta = probe(source)
    count = min(args.max_samples, max(2, int(math.ceil(meta["duration"]))))
    times = np.linspace(0, max(0, meta["duration"] - 1 / meta["fps"]), count)
    cols, thumb_w = min(8, count), 180
    thumb_h = round(thumb_w * meta["height"] / meta["width"])
    sheet = Image.new("RGB", (cols * thumb_w, math.ceil(count / cols) * (thumb_h + 25)), "#171b23")
    cap = cv2.VideoCapture(str(source))
    for i, t in enumerate(times):
        cap.set(cv2.CAP_PROP_POS_MSEC, float(t) * 1000)
        ok, frame = cap.read()
        if not ok:
            raise ValueError(f"Could not read timeline sample at {t:.3f}s")
        tile = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).resize((thumb_w, thumb_h))
        x, y = (i % cols) * thumb_w, (i // cols) * (thumb_h + 25)
        sheet.paste(tile, (x, y + 25))
        ImageDraw.Draw(sheet).text((x + 5, y + 4), f"{t:06.2f}s", fill="white")
    cap.release()
    sheet.save(out / "timeline.jpg", quality=90)
    save_json(out / "source-info.json", {**meta, "sample_times": times.tolist(), "source": str(source)})
    print(json.dumps({"timeline": str(out / "timeline.jpg"), **meta}, ensure_ascii=False))


def prepare(args):
    source = Path(args.input).expanduser().resolve()
    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("Project directory is not empty; use a new project directory.")
    meta = probe(source)
    if meta.get("color_transfer") in ("smpte2084", "arib-std-b67"):
        raise ValueError("HDR detected. Produce and visually check an SDR source before prepare.")
    if args.start < 0 or not .4 <= args.duration <= 30 or args.start >= meta["duration"]:
        raise ValueError("Choose a valid 0.4–30 second native segment.")
    fps = meta["fps"]
    first = round(args.start * fps)
    pickup_frame = min(meta["frames"] - 1, round((args.start + args.duration) * fps))
    count = pickup_frame - first + 1
    if count < 2:
        raise ValueError("The native segment must contain at least two frames.")
    duration = (count - 1) / fps
    scale = min(1, args.width / meta["width"], 1280 / meta["height"])
    w, h = max(2, round(meta["width"] * scale / 2) * 2), max(2, round(meta["height"] * scale / 2) * 2)
    (out / "media").mkdir(parents=True)
    (out / "assets").mkdir()
    native = out / "media/source-native.mkv"
    pipe = subprocess.Popen([ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
        "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", f"{fps:.12f}", "-i", "-", "-an",
        "-c:v", "ffv1", "-level", "3", "-pix_fmt", "bgr0", str(native)], stdin=subprocess.PIPE)
    cap = cv2.VideoCapture(str(source))
    cap.set(cv2.CAP_PROP_POS_FRAMES, first)
    last = None
    try:
        for i in range(count):
            ok, frame = cap.read()
            if not ok:
                raise ValueError(f"Cannot decode native frame {first+i}.")
            if frame.shape[:2] != (h, w):
                frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_LANCZOS4)
            last = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pipe.stdin.write(last.tobytes())
        pipe.stdin.close()
        if pipe.wait():
            raise RuntimeError("Lossless native cache encoding failed.")
    except BaseException:
        if pipe.poll() is None:
            pipe.terminate(); pipe.wait()
        raise
    finally:
        cap.release()
    Image.fromarray(last).save(out / "assets/pickup.png")
    digest = sha(source)
    reservation = rotation.reserve(digest, source.name, args.state_dir) if args.variant == "auto" else None
    variant = reservation["variant"] if reservation else args.variant
    intro = min(1.4, duration)
    enter_start = duration - intro
    enter_end = enter_start + intro * .54
    contact = duration - min(.12, intro * .09)
    flight = 1.05 if args.motion == "lift" else 1.3
    k = min(w / 720, h / 1280)
    config = {
        "schema": "fishing-gif-project/2", "source": {"file": str(source), "sha256": digest,
            "start": first/fps, "duration": duration, "first_frame": first,
            "pickup_frame": pickup_frame, "native_fps": fps, "native_frames": count, "original": meta},
        "canvas": {"width": w, "height": h}, "variant": variant,
        "rotation": {"automatic": reservation is not None,
            "state_dir": str(Path(args.state_dir).resolve()) if args.state_dir else str(rotation.default_dir().resolve())},
        "media": {"native": "media/source-native.mkv", "pickup": "assets/pickup.png"},
        "matte": {"polygons": [], "holes": [], "grabcut": True, "search_px": 5,
            "foreground_erosion_px": 2, "feather": .45, "output": "assets/subject.png"},
        "cleanup": {"plate": "assets/clean-plate.png", "mode": "patch", "dilate": 35,
            "feather": 10, "ellipses": []},
        "subject": {"anchor": None, "anchor_track": [], "motion": args.motion,
            "end": None, "swing_degrees": 3 if args.motion == "lift" else 0,
            "ground_slope": 0, "pivot": None},
        "rod": {"scale": .66*k, "angle": 19, "line_length": None,
            "pendant_scale": .66*k, "pendant_angle": -10},
        "timing": {"pickup": duration, "lift": duration, "exit": duration + flight,
            "end": math.ceil((duration+flight+.35)*10)/10,
            "enter_start": enter_start, "enter_end": enter_end, "line_start": enter_end, "contact": contact},
        "output": {"gif": False, "gif_width": min(540, w), "gif_fps": 50, "mp4_fps": 60}}
    save_json(out / "project.json", config)
    print(json.dumps({"project": str(out / "project.json"), "pickup": str(out / "assets/pickup.png"),
        "native_frames": count, "pickup_source_seconds": pickup_frame/fps, "variant": variant,
        "next": "Inspect pickup; set contact point, matte and clean plate. Prefix remains native video."}, ensure_ascii=False))


def load_project(path):
    path = Path(path).resolve()
    c = json.loads(path.read_text(encoding="utf-8"))
    if c.get("schema") != "fishing-gif-project/2":
        raise ValueError("Use prepare to create a v0.2 native-video project; v0.1 projects remain usable with their original renderer.")
    return path.parent, c


def rgb(path):
    return np.array(Image.open(path).convert("RGB"))


def matte(args):
    root, c = load_project(args.project)
    frame = rgb(root / c["media"]["pickup"])
    h, w = frame.shape[:2]
    cfg = c["matte"]
    if cfg.get("mask_file"):
        mask = np.array(Image.open(root / cfg["mask_file"]).convert("L"))
        if mask.shape != (h, w):
            raise ValueError("Mask dimensions do not match pickup.")
    else:
        polygons = cfg.get("polygons", [])
        if not polygons:
            raise ValueError("Inspect pickup and specify matte.polygons or a grayscale mask_file.")
        mask = np.zeros((h, w), np.uint8)
        for p in polygons:
            cv2.fillPoly(mask, [np.round(p).astype(np.int32)], 255)
        for p in cfg.get("holes", []):
            cv2.fillPoly(mask, [np.round(p).astype(np.int32)], 0)
        if cfg.get("grabcut", True):
            radius = max(1, int(cfg.get("search_px", 5)))
            expanded = cv2.dilate(mask, np.ones((2 * radius + 1,) * 2, np.uint8))
            erode = max(1, int(cfg.get("foreground_erosion_px", 2)))
            inside = cv2.erode(mask, np.ones((2 * erode + 1,) * 2, np.uint8))
            gc = np.full((h, w), cv2.GC_BGD, np.uint8)
            gc[expanded > 0], gc[mask > 0], gc[inside > 0] = cv2.GC_PR_BGD, cv2.GC_PR_FGD, cv2.GC_FGD
            cv2.grabCut(frame, gc, None, np.zeros((1, 65)), np.zeros((1, 65)), 5, cv2.GC_INIT_WITH_MASK)
            mask = np.where((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
        if cfg.get("reject_hsv"):
            hsv = cv2.cvtColor(frame, cv2.COLOR_RGB2HSV)
            rejected = np.zeros((h, w), np.uint8)
            for rule in cfg["reject_hsv"]:
                rejected |= cv2.inRange(hsv, np.array(rule["low"]), np.array(rule["high"]))
            for x0, y0, x1, y1 in cfg.get("protect_rectangles", []):
                rejected[y0:y1, x0:x1] = 0
            mask[rejected > 0] = 0
    if cfg.get("min_component_area", 0):
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
        for j in range(1, count):
            if stats[j, cv2.CC_STAT_AREA] < cfg["min_component_area"]:
                mask[labels == j] = 0
    if cfg.get("feather", .45) > 0:
        mask = cv2.GaussianBlur(mask, (3, 3), cfg.get("feather", .45))
    if np.count_nonzero(mask) < 16:
        raise ValueError("Subject matte is empty.")
    Image.fromarray(np.dstack([frame, mask])).save(root / cfg["output"])
    yy, xx = np.indices((h, w))
    checker = np.where(((xx // 20 + yy // 20) % 2)[..., None], 210, 150)
    alpha = mask[..., None] / 255
    check = (frame * alpha + checker * (1 - alpha)).astype(np.uint8)
    Image.fromarray(check).save(root / "assets/matte-checker.png")
    print(json.dumps({"subject": str(root / cfg["output"]), "check": str(root / "assets/matte-checker.png")}, ensure_ascii=False))


def smooth(a, b, t):
    if b <= a:
        raise ValueError("Animation event times must be increasing.")
    u = np.clip((t - a) / (b - a), 0, 1)
    return float(u * u * (3 - 2 * u))


def blend(bg, fg):
    a = fg[..., 3:4].astype(np.float32) / 255
    return (bg * (1 - a) + fg[..., :3] * a).clip(0, 255).astype(np.uint8)




def track_anchor(args):
    root,c=load_project(args.project)
    if c["subject"].get("anchor") is None:
        raise ValueError("Set subject.anchor on the pickup before tracking.")
    cap=cv2.VideoCapture(str(root/c["media"]["native"]))
    frames=[]
    while True:
        ok,f=cap.read()
        if not ok:break
        frames.append(cv2.cvtColor(f,cv2.COLOR_BGR2GRAY))
    cap.release()
    alpha=np.array(Image.open(root/c["matte"]["output"]).convert("RGBA"))[...,3]
    points=cv2.goodFeaturesToTrack(frames[-1],100,.01,5,mask=(alpha>128).astype(np.uint8)*255)
    if points is None or len(points)<6:
        raise ValueError("Not enough subject features; inspect native frames and set anchor_track keyframes manually.")
    anchor=np.array(c["subject"]["anchor"],np.float32)
    fps=c["source"]["native_fps"]
    first=max(0,int(c["timing"]["enter_start"]*fps))
    track=[[c["timing"]["pickup"],*anchor.tolist()]]
    for i in range(len(frames)-1,first,-1):
        previous,status,error=cv2.calcOpticalFlowPyrLK(frames[i],frames[i-1],points,None,winSize=(25,25),maxLevel=3)
        if previous is None:raise ValueError("Anchor tracking lost the subject; choose a stable shot or supply keyframes.")
        good=status[:,0]>0
        if np.count_nonzero(good)<6:raise ValueError("Anchor tracking lost the subject; supply inspected keyframes.")
        affine,inliers=cv2.estimateAffinePartial2D(points[good],previous[good],method=cv2.RANSAC,ransacReprojThreshold=3)
        if affine is None or inliers.sum()<6:raise ValueError("Anchor tracking is inconsistent; inspect the entry interval.")
        anchor=affine[:,:2]@anchor+affine[:,2]
        if not (0<=anchor[0]<c["canvas"]["width"] and 0<=anchor[1]<c["canvas"]["height"]):
            raise ValueError("Tracked contact left the frame; select another shot.")
        track.append([(i-1)/fps,*anchor.tolist()])
        points=previous[good][inliers[:,0]>0].reshape(-1,1,2)
    c["subject"]["anchor_track"]=list(reversed(track))
    save_json(args.project,c)
    print(json.dumps({"keyframes":len(track),"native_pixels_changed":False}))


def preview(args):
    root, c = load_project(args.project)
    scene = Scene(root, c)
    t = c["timing"]
    flight = t["exit"] - t["lift"]
    times = [0, t["enter_end"], (t["enter_end"]+t["contact"])/2, t["contact"], t["pickup"],
             t["lift"] + .15*flight, t["lift"] + .4*flight, t["lift"] + .7*flight, t["exit"], t["end"]-.02]
    folder = root / "preview"
    folder.mkdir(exist_ok=True)
    tw, th = 216, round(216 * scene.h / scene.w)
    sheet = Image.new("RGB", (5 * tw, 2 * (th + 24)), "#181c24")
    for i, value in enumerate(times):
        im = Image.fromarray(scene.render(value))
        im.save(folder / f"{i:02d}-{value:.3f}.png")
        x, y = i % 5 * tw, i // 5 * (th + 24)
        sheet.paste(im.resize((tw, th)), (x, y + 24))
        ImageDraw.Draw(sheet).text((x + 5, y + 3), f"{value:.3f}s", fill="white")
    sheet.save(folder / "contact.jpg", quality=92)
    save_json(folder / "exit-check.json", scene.exit_check())
    print(json.dumps({"contact": str(folder / "contact.jpg"), **scene.exit_check()}, ensure_ascii=False))


def encode_video(scene, path, fps):
    count = round(scene.t["end"] * fps)
    pipe = subprocess.Popen([ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
        "-pix_fmt", "rgb24", "-s", f"{scene.w}x{scene.h}", "-r", str(fps), "-i", "-", "-an",
        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)], stdin=subprocess.PIPE)
    try:
        for n in range(count):
            pipe.stdin.write(scene.render(n / fps).tobytes())
            if n % fps == 0:
                print(f"{path.name}: {n}/{count}", flush=True)
        pipe.stdin.close()
        if pipe.wait() != 0:
            raise RuntimeError("Video encoding failed.")
    except BaseException:
        if pipe.poll() is None:
            pipe.terminate()
            pipe.wait()
        raise


def validate_files(mp4, gif=None, expected_duration=None):
    meta = probe(mp4)
    run_ff(["-i", mp4, "-f", "null", "-"])
    if abs(meta["fps"] - 60) > .01:
        raise ValueError("MP4 must be 60fps.")
    if expected_duration is not None and abs(meta["duration"]-expected_duration) > 1/60+.001:
        raise ValueError("MP4 duration mismatch.")
    if gif is None:
        return {"mp4": meta, "fully_decoded": True}
    with Image.open(gif) as im:
        duration, delays = 0, set()
        for i in range(im.n_frames):
            im.seek(i)
            im.load()
            delay = im.info.get("duration", 0)
            duration += delay
            delays.add(delay)
        info = {"size": list(im.size), "frames": im.n_frames, "duration_ms": duration,
                "frame_delays_ms": sorted(delays), "loop": im.info.get("loop")}
    if delays != {20} or info["loop"] != 0 or abs(meta["fps"] - 60) > .01:
        raise ValueError("Outputs do not meet the 50fps GIF / 60fps MP4 contract.")
    if expected_duration is not None:
        if abs(duration / 1000 - expected_duration) > .011 or abs(meta["duration"] - expected_duration) > 1 / 60 + .001:
            raise ValueError("Export duration mismatch.")
    return {"mp4": meta, "gif": info, "fully_decoded": True}


def render(args):
    root, c = load_project(args.project)
    if c["output"].get("mp4_fps", 60) != 60:
        raise ValueError("MP4 uses 60fps.")
    if (args.gif or c["output"].get("gif", False)) and c["output"].get("gif_fps",50)!=50:
        raise ValueError("Optional GIF uses 50fps.")
    scene = Scene(root, c)
    exit_info = scene.exit_check()
    if any(exit_info.values()):
        raise ValueError(f"Extend the exit trajectory; visible pixels remain: {exit_info}")
    out = root / "output"
    out.mkdir(exist_ok=True)
    index = 1
    while (out / f"v{index:03d}").exists():
        index += 1
    version = out / f"v{index:03d}"
    version.mkdir()
    mp4, gif = version / f"fishing-{c['variant']}-60fps.mp4", version / f"fishing-{c['variant']}-50fps.gif"
    try:
        encode_video(scene, mp4, 60)
        want_gif = bool(args.gif or c["output"].get("gif", False))
        if want_gif:
            with tempfile.TemporaryDirectory(prefix="fishing-gif-") as tmp:
                master = Path(tmp) / "master50.mp4"
                encode_video(scene, master, 50)
                w = min(c["output"].get("gif_width", 540), scene.w)
                w = max(2, round(w / 2) * 2)
                h = max(2, round(w * scene.h / scene.w / 2) * 2)
                filters = f"[0:v]scale={w}:{h}:flags=lanczos,split[a][b];[a]palettegen=max_colors=192:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle"
                run_ff(["-i", master, "-filter_complex", filters, "-loop", "0", gif])
        validation = validate_files(mp4, gif if want_gif else None, scene.t["end"])
        report = {"project_sha256": sha(args.project), "variant": c["variant"], "exit": exit_info,
                  "inputs": [{"path":str(p.resolve()),"sha256":sha(p)} for p in scene.input_paths],
                  "files": {"mp4": {"path": str(mp4.relative_to(root)), "sha256": sha(mp4)},
                            "gif": {"path": str(gif.relative_to(root)), "sha256": sha(gif) if want_gif else None}},
                  "validation": validation, "visual_qa": "required before complete"}
        if not want_gif:
            report["files"].pop("gif")
        save_json(version / "report.json", report)
        save_json(root / "latest-render.json", {"report": str((version / 'report.json').relative_to(root))})
        print(json.dumps({"mp4": str(mp4), "gif": str(gif) if want_gif else None, "report": str(version / "report.json"),
                          "next": "Visually inspect the delivered files, then run complete --qa-notes ..."}, ensure_ascii=False))
    except BaseException:
        save_json(version / "failed.json", {"status": "failed; rotation not advanced"})
        raise


def complete(args):
    root, c = load_project(args.project)
    latest = json.loads((root / "latest-render.json").read_text(encoding="utf-8"))
    report_path = root / latest["report"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report["project_sha256"] != sha(args.project) or report["variant"] != c["variant"]:
        raise ValueError("Project changed after render; render again before completing.")
    for value in report.get("inputs", []):
        if sha(value["path"]) != value["sha256"]:
            raise ValueError("An input changed after render; render again before completing.")
    for value in report["files"].values():
        if sha(root / value["path"]) != value["sha256"]:
            raise ValueError("An output changed after validation; render again.")
    if not args.qa_notes.strip():
        raise ValueError("Record what was actually visually checked.")
    if any(report["exit"].values()):
        raise ValueError("Subject or rod remains in frame.")
    result = {"variant": c["variant"], "advanced": False, "explicit_variant": True}
    if c["rotation"]["automatic"]:
        result = rotation.finish(c["source"]["sha256"], report["files"], c["rotation"].get("state_dir"), expected_variant=c["variant"])
    report["visual_qa"] = args.qa_notes
    report["rotation_result"] = result
    save_json(report_path, report)
    print(json.dumps({"completed": True, **result, "files": report["files"]}, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    q = sub.add_parser("inspect")
    q.add_argument("input"); q.add_argument("--out", required=True); q.add_argument("--max-samples", type=int, default=48)
    q = sub.add_parser("prepare")
    q.add_argument("input"); q.add_argument("--out", required=True); q.add_argument("--start", type=float, required=True)
    q.add_argument("--duration", type=float, required=True); q.add_argument("--width", type=int, default=720)
    q.add_argument("--variant", choices=["auto", *RODS], default="auto"); q.add_argument("--state-dir")
    q.add_argument("--motion", choices=["lift", "low_lift", "drag"], default="lift")
    for name in ("matte", "track", "preview", "render", "complete"):
        q = sub.add_parser(name); q.add_argument("project")
        if name == "render":
            q.add_argument("--gif", action="store_true", help="Also export GIF only when requested")
        if name == "complete":
            q.add_argument("--qa-notes", required=True)
    q = sub.add_parser("rotation"); q.add_argument("--state-dir")
    args = p.parse_args()
    try:
        if args.command == "doctor":
            return doctor()
        if args.command == "rotation":
            print(json.dumps(rotation.status(args.state_dir), ensure_ascii=False, indent=2)); return 0
        deps()
        {"inspect": inspect_video, "prepare": prepare, "matte": matte,
         "preview": preview, "track": track_anchor, "render": render, "complete": complete}[args.command](args)
        return 0
    except (ValueError, FileNotFoundError, RuntimeError, ImportError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
