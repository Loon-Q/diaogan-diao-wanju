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
    global cv2, np, Image, ImageDraw
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw


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
        raise ValueError("Project directory is not empty. Use a new directory for a new preparation.")
    meta = probe(source)
    if meta.get("color_transfer") in ("smpte2084", "arib-std-b67"):
        raise ValueError("HDR detected. Produce and visually check an SDR source before prepare.")
    if args.start < 0 or not (0.5 <= args.duration <= 8) or args.start + args.duration > meta["duration"] + .001:
        raise ValueError("Choose a valid 0.5–8 second segment within the source.")
    scale = min(1, args.width / meta["width"], 1280 / meta["height"])
    w, h = max(2, round(meta["width"] * scale / 2) * 2), max(2, round(meta["height"] * scale / 2) * 2)
    n = max(1, round(args.duration * 60))
    duration = n / 60
    out.mkdir(parents=True, exist_ok=True)
    (out / "media").mkdir(exist_ok=True)
    (out / "assets").mkdir(exist_ok=True)
    cut = out / "media/source60.mp4"
    # Retain the selected source clock; compensate for minterpolate's trailing latency.
    interpolation = ("minterpolate=fps=60:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
                     if meta["fps"] < 59 else "fps=60")
    filters = f"scale={w}:{h}:flags=lanczos,tpad=stop_mode=clone:stop_duration=0.2,{interpolation},trim=end_frame={n}"
    run_ff(["-ss", args.start, "-t", duration + .2, "-i", source, "-vf", filters,
            "-an", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", cut])
    cap = cv2.VideoCapture(str(cut))
    last, decoded = None, 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        last, decoded = frame, decoded + 1
    cap.release()
    if decoded != n:
        raise ValueError(f"Prepared source has {decoded} frames; expected {n}.")
    Image.fromarray(cv2.cvtColor(last, cv2.COLOR_BGR2RGB)).save(out / "assets/pickup.png")
    digest = sha(source)
    reservation = rotation.reserve(digest, source.name, args.state_dir) if args.variant == "auto" else None
    variant = reservation["variant"] if reservation else args.variant
    config = {
        "schema": "fishing-gif-project/1", "source": {"file": str(source), "sha256": digest,
        "start": args.start, "duration": duration, "original": meta},
        "canvas": {"width": w, "height": h}, "variant": variant,
        "rotation": {"automatic": reservation is not None, "state_dir": str(Path(args.state_dir).resolve()) if args.state_dir else str(rotation.default_dir().resolve())},
        "media": {"source60": "media/source60.mp4", "pickup": "assets/pickup.png"},
        "matte": {"polygons": [], "holes": [], "grabcut": True, "search_px": 5,
                  "foreground_erosion_px": 2, "feather": .45, "output": "assets/subject.png"},
        "cleanup": {"plate": "assets/clean-plate.png", "mode": "patch", "dilate": 17,
                    "feather": 5, "ellipses": []},
        "subject": {"anchor": None, "path": None, "swing_degrees": 6},
        "rod": {"scale": .66 * w / 720, "grip": [.85 * w, .60 * h], "angle": 16},
        "timing": {"pickup": duration, "lift": duration + .12, "exit": duration + 3.52,
                   "end": math.ceil((duration + 4.0) * 10) / 10, "enter_start": min(.35,duration*.1),
                   "enter_end": min(1.8, duration * .6), "line_start": min(1.4, duration * .48)},
        "output": {"gif_width": min(540, w), "gif_fps": 50, "mp4_fps": 60},
    }
    save_json(out / "project.json", config)
    print(json.dumps({"project": str(out / "project.json"), "pickup": str(out / "assets/pickup.png"),
                      "variant": variant, "next": "Inspect pickup; supply matte geometry, anchor and clean plate."}, ensure_ascii=False))


def load_project(path):
    path = Path(path).resolve()
    c = json.loads(path.read_text(encoding="utf-8"))
    if c.get("schema") != "fishing-gif-project/1":
        raise ValueError("Unsupported project schema.")
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


class Scene:
    def __init__(self, root, c):
        self.root, self.c, self.t = root, c, c["timing"]
        self.w, self.h = c["canvas"]["width"], c["canvas"]["height"]
        self.last = rgb(root / c["media"]["pickup"])
        if self.last.shape != (self.h, self.w, 3):
            raise ValueError("Pickup dimensions differ from the canvas.")
        self.bot = np.array(Image.open(root / c["matte"]["output"]).convert("RGBA"))
        if self.bot.shape != (self.h, self.w, 4):
            raise ValueError("Subject dimensions differ from the canvas.")
        self.alpha = self.bot[..., 3]
        if not c["subject"].get("anchor"):
            raise ValueError("Choose a real contact point on the subject and set subject.anchor.")
        self.anchor = np.array(c["subject"]["anchor"], float)
        x, y = np.round(self.anchor).astype(int)
        if not (0 <= x < self.w and 0 <= y < self.h and self.alpha[y, x] > 80):
            raise ValueError("The fishing contact point is not on the subject matte.")
        plate = np.array(Image.open(root / c["cleanup"]["plate"]).convert("RGB").resize((self.w, self.h), Image.Resampling.LANCZOS))
        cfg = c["cleanup"]
        if cfg.get("mask_file"):
            patch = np.array(Image.open(root / cfg["mask_file"]).convert("L"))
            if patch.shape != self.alpha.shape:
                raise ValueError("Background mask size mismatch.")
        elif cfg["mode"] == "band":
            y0, y1, y2, y3 = cfg["band"]
            yy = np.arange(self.h, dtype=np.float32)[:, None]
            a, b = np.clip((yy - y0) / (y1 - y0), 0, 1), np.clip((y3 - yy) / (y3 - y2), 0, 1)
            patch = np.broadcast_to((255 * a * a * (3 - 2 * a) * b * b * (3 - 2 * b)).astype(np.uint8), (self.h, self.w)).copy()
        elif cfg["mode"] == "full":
            patch = np.full((self.h, self.w), 255, np.uint8)
        else:
            size = max(1, int(cfg.get("dilate", 17))) | 1
            patch = cv2.dilate(self.alpha, np.ones((size, size), np.uint8))
            for e in cfg.get("ellipses", []):
                cx, cy, rx, ry = e[:4]
                cv2.ellipse(patch, (int(cx), int(cy)), (int(rx), int(ry)), e[4] if len(e) > 4 else 0, 0, 360, 255, -1)
            sigma = cfg.get("feather", 5)
            if sigma > 0:
                kernel = int(math.ceil(sigma * 5)) | 1
                patch = cv2.GaussianBlur(patch, (kernel, kernel), sigma)
            # Feather the outside boundary, never blend old subject pixels back in.
            mandatory = cv2.dilate((self.alpha > 4).astype(np.uint8)*255, np.ones((5,5),np.uint8))
            for e in cfg.get("ellipses", []):
                cx,cy,rx,ry=e[:4]
                cv2.ellipse(mandatory,(int(cx),int(cy)),(int(rx),int(ry)),
                            e[4] if len(e)>4 else 0,0,360,255,-1)
            patch = np.maximum(patch, mandatory)
        if np.any((self.alpha > 200) & (patch < 250)):
            raise ValueError("Background cleanup mask does not fully cover the old subject. Expand it.")
        self.patch = patch.astype(np.float32) / 255
        a = self.patch[..., None]
        self.clean = (self.last * (1 - a) + plate * a).astype(np.uint8)
        Image.fromarray(self.clean).save(root / "assets/background-composite.png")
        self.shadow_mask = self.patch * (1 - cv2.dilate(self.alpha, np.ones((3, 3), np.uint8)).astype(np.float32) / 255)
        source_asset = c["rod"].get("asset")
        rod_path = root / source_asset if source_asset else SKILL / "assets/rods" / RODS[c["variant"]]
        self.input_paths = [root / c["media"]["source60"],root / c["media"]["pickup"],
                            root / c["matte"]["output"],root / c["cleanup"]["plate"],rod_path]
        self.rod = np.array(Image.open(rod_path).convert("RGBA"))
        if self.rod.shape[:2] != (1254, 1254):
            raise ValueError("Rod asset must use the 1254×1254 reference canvas.")
        cap = cv2.VideoCapture(str(root / c["media"]["source60"]))
        self.frames = []
        while True:
            ok, f = cap.read()
            if not ok:
                break
            self.frames.append(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))
        cap.release()
        if len(self.frames) != round(self.t["pickup"] * 60):
            raise ValueError("Prepared source frame count does not match pickup time.")
        if not (0 < self.t["pickup"] <= self.t["lift"] < self.t["exit"] < self.t["end"]):
            raise ValueError("Invalid pickup/lift/exit/end timing.")
        bbox = Image.fromarray(self.alpha).getbbox()
        if bbox is None:
            raise ValueError("Empty subject.")
        bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        self.path = np.array(c["subject"].get("path") or [self.anchor.tolist(),
            (self.anchor + [10, -.15 * self.h]).tolist(),
            (self.anchor + [.25 * self.w, -.45 * self.h]).tolist(),
            [self.w + max(.26 * self.w, .85 * bw), -max(.28 * self.h, 1.12 * bh)]], float)
        if self.path.shape != (4, 2) or np.linalg.norm(self.path[0] - self.anchor) > .01:
            raise ValueError("Use four Bezier points, with the first equal to subject.anchor.")

    def rod_matrix(self, t):
        cfg, k = self.c["rod"], self.w / 720
        enter = 1 - smooth(self.t["enter_start"], self.t["enter_end"], t)
        u = smooth(self.t["lift"], self.t["exit"], t)
        cast = math.sin(math.pi * smooth(self.t["enter_end"], self.t["pickup"], t))
        shift = np.array([(650 * enter + 50 * u + 950 * u ** 3) * k,
                          (-180 * enter - 530 * u + 12 * cast) * self.h / 1280])
        a = math.radians(cfg["angle"] + 8 * enter - 7 * u + 2 * cast + .25 * math.sin(t * 4) * (1 - u))
        rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]]) * cfg["scale"]
        pos = np.array(cfg["grip"]) + shift
        return np.column_stack([rot, pos - rot @ np.array([900., 930.])])

    def subject_matrix(self, t):
        u = smooth(self.t["lift"], self.t["exit"], t)
        p0, p1, p2, p3 = self.path
        pos = (1 - u) ** 3 * p0 + 3 * (1 - u) ** 2 * u * p1 + 3 * (1 - u) * u * u * p2 + u ** 3 * p3
        angle = self.c["subject"].get("swing_degrees", 6) * math.sin(u * math.pi * 3.2) * math.sin(min(1, u * 5) * math.pi / 2) * math.exp(-u * .7) + 8 * u * u
        a = math.radians(angle)
        rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
        return np.column_stack([rot, pos - rot @ self.anchor]), pos

    def line(self, t, tip, end):
        s = 2
        im = Image.new("RGBA", (self.w * s, self.h * s))
        if t < self.t["line_start"] or t > self.t["exit"] + .05:
            return np.array(im.resize((self.w, self.h), Image.Resampling.LANCZOS))
        if t < self.t["pickup"]:
            drop = smooth(self.t["line_start"], self.t["pickup"], t)
            end = tip + (end - tip) * drop
            end[0] += math.sin(t * 8) * 12 * (1 - drop) * self.w / 720
        slack = 16 * (1 - smooth(self.t["pickup"] - .15, self.t["lift"] + .1, t)) * self.w / 720
        pts = [tuple((tip * (1 - q) + end * q + [math.sin(q * math.pi) * slack, 0]) * s) for q in np.linspace(0, 1, 70)]
        draw = ImageDraw.Draw(im)
        draw.line(pts, fill=(30, 34, 35, 175), width=max(2, round(4 * self.w / 720)))
        draw.line([(x - .8, y) for x, y in pts], fill=(233, 237, 233, 210), width=max(1, round(2 * self.w / 720)))
        x, y = end
        r = max(2, 4 * self.w / 720)
        draw.arc([(x - r) * s, (y - 1) * s, (x + r) * s, (y + 2 * r) * s], 0, 270, fill=(65, 65, 64, 230), width=2)
        return np.array(im.resize((self.w, self.h), Image.Resampling.LANCZOS))

    def render(self, t):
        anchor = self.anchor.copy()
        if t < self.t["lift"]:
            p = min(len(self.frames) - 1, t * 60)
            i = int(p)
            bg = cv2.addWeighted(self.frames[i], 1 - (p - i), self.frames[min(i + 1, len(self.frames) - 1)], p - i, 0) if t < self.t["pickup"] else self.last.copy()
        else:
            matrix, anchor = self.subject_matrix(t)
            fade = 1 - smooth(self.t["lift"], self.t["lift"] + .48, t)
            a = (self.shadow_mask * fade)[..., None]
            bg = (self.clean * (1 - a) + self.last * a).astype(np.uint8)
            bot = cv2.warpAffine(self.bot, matrix, (self.w, self.h), flags=cv2.INTER_LINEAR)
            bg = blend(bg, bot)
        matrix = self.rod_matrix(t)
        tip = matrix[:, :2] @ np.array([103., 166.]) + matrix[:, 2]
        bg = blend(bg, self.line(t, tip, anchor))
        if t >= self.t["enter_start"]:
            bg = blend(bg, cv2.warpAffine(self.rod, matrix, (self.w, self.h), flags=cv2.INTER_LINEAR))
        return bg

    def exit_check(self):
        t = self.t["end"] - 1 / 50
        sm, _ = self.subject_matrix(t)
        rm = self.rod_matrix(t)
        subject = cv2.warpAffine(self.alpha, sm, (self.w, self.h))
        rod = cv2.warpAffine(self.rod[..., 3], rm, (self.w, self.h))
        return {"subject_visible_pixels": int(np.count_nonzero(subject > 24)),
                "rod_visible_pixels": int(np.count_nonzero(rod > 24))}


def preview(args):
    root, c = load_project(args.project)
    scene = Scene(root, c)
    t = c["timing"]
    times = [0, t["enter_end"], (t["enter_end"] + t["pickup"]) / 2, t["pickup"],
             t["lift"] + .25, t["lift"] + .8, (t["lift"] + t["exit"]) / 2,
             t["exit"] - .7, t["exit"], t["end"] - .05]
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


def validate_files(mp4, gif, expected_duration=None):
    meta = probe(mp4)
    run_ff(["-i", mp4, "-f", "null", "-"])
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
    if c["output"].get("gif_fps", 50) != 50 or c["output"].get("mp4_fps", 60) != 60:
        raise ValueError("This renderer uses 50fps GIF and 60fps MP4.")
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
        with tempfile.TemporaryDirectory(prefix="fishing-gif-") as tmp:
            master = Path(tmp) / "master50.mp4"
            encode_video(scene, master, 50)
            w = min(c["output"].get("gif_width", 540), scene.w)
            w = max(2, round(w / 2) * 2)
            h = max(2, round(w * scene.h / scene.w / 2) * 2)
            filters = f"[0:v]scale={w}:{h}:flags=lanczos,split[a][b];[a]palettegen=max_colors=192:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle"
            run_ff(["-i", master, "-filter_complex", filters, "-loop", "0", gif])
        validation = validate_files(mp4, gif, scene.t["end"])
        report = {"project_sha256": sha(args.project), "variant": c["variant"], "exit": exit_info,
                  "inputs": [{"path":str(p.resolve()),"sha256":sha(p)} for p in scene.input_paths],
                  "files": {"mp4": {"path": str(mp4.relative_to(root)), "sha256": sha(mp4)},
                            "gif": {"path": str(gif.relative_to(root)), "sha256": sha(gif)}},
                  "validation": validation, "visual_qa": "required before complete"}
        save_json(version / "report.json", report)
        save_json(root / "latest-render.json", {"report": str((version / 'report.json').relative_to(root))})
        print(json.dumps({"mp4": str(mp4), "gif": str(gif), "report": str(version / "report.json"),
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
    for name in ("matte", "preview", "render", "complete"):
        q = sub.add_parser(name); q.add_argument("project")
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
         "preview": preview, "render": render, "complete": complete}[args.command](args)
        return 0
    except (ValueError, FileNotFoundError, RuntimeError, ImportError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
