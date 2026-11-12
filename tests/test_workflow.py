"""Meaningful state, export and installation checks; no user footage required."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "skills/fishing-gif/scripts/fishing_gif.py"
sys.path.insert(0, str(CLI.parent))
import rotation


def command(*args, success=True):
    r = subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True)
    if success and r.returncode:
        raise AssertionError(r.stdout + r.stderr)
    return r


def make_fixture(folder):
    import cv2
    import numpy as np
    from PIL import Image
    import imageio_ffmpeg
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    w, h = 240, 400
    background = np.full((h, w, 3), [209, 223, 235], np.uint8)
    Image.fromarray(background).save(folder / "clean-plate.png")
    video = folder / "输入视频 sample.mp4"
    pipe = subprocess.Popen([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", "30", "-i", "-",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], stdin=subprocess.PIPE)
    for i in range(36):
        frame = background.copy()
        y = 172 + round(2 * np.sin(2 * np.pi * i / 36))
        cv2.ellipse(frame, (120, 230), (40, 7), 0, 0, 360, (170, 185, 197), -1)
        cv2.circle(frame, (120, y), 34, (234, 52, 34), -1)
        cv2.rectangle(frame, (96, y + 24), (112, y + 52), (42, 48, 58), -1)
        cv2.rectangle(frame, (128, y + 24), (144, y + 52), (42, 48, 58), -1)
        cv2.circle(frame, (109, y - 3), 4, (250, 250, 250), -1)
        cv2.circle(frame, (131, y - 3), 4, (250, 250, 250), -1)
        pipe.stdin.write(frame.tobytes())
    pipe.stdin.close()
    assert pipe.wait() == 0
    return video


class RotationTests(unittest.TestCase):
    def test_cycle_retry_revision_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i, expected in enumerate(["follow", "like", "save", "share", "follow"]):
                key = f"video-{i}"
                self.assertEqual(rotation.reserve(key, key, tmp)["variant"], expected)
                self.assertEqual(rotation.status(tmp)["next_variant"], expected)
                self.assertEqual(rotation.reserve(key, "renamed", tmp)["variant"], expected)
                self.assertTrue(rotation.finish(key, {}, tmp)["advanced"])
                self.assertFalse(rotation.finish(key, {}, tmp)["advanced"])
                self.assertEqual(rotation.reserve(key, "revision", tmp)["variant"], expected)
            self.assertEqual(rotation.status(tmp)["next_variant"], "like")

    def test_unfinished_does_not_consume_and_late_retry_does_not_double_advance(self):
        with tempfile.TemporaryDirectory() as tmp:
            rotation.reserve("failed", "failed", tmp)
            self.assertEqual(rotation.reserve("new", "new", tmp)["variant"], "follow")
            rotation.finish("new", {}, tmp)
            self.assertEqual(rotation.reserve("failed", "renamed", tmp)["variant"], "follow")
            self.assertFalse(rotation.finish("failed", {}, tmp)["advanced"])
            self.assertEqual(rotation.status(tmp)["next_variant"], "like")


class ExportTests(unittest.TestCase):
    def test_portable_full_pipeline_and_corruption_guard(self):
        import numpy as np
        with tempfile.TemporaryDirectory(prefix="fishing-smoke-") as tmp:
            root = Path(tmp)
            video = make_fixture(root / "原片")
            state, project = root / "state", root / "project"
            command("doctor")
            command("inspect", video, "--out", root / "inspect")
            command("prepare", video, "--start", 0, "--duration", 1.2, "--out", project,
                    "--state-dir", state, "--width", 240)
            config_file = project / "project.json"
            c = json.loads(config_file.read_text())
            c["matte"]["polygons"] = [
                [[120 + 34 * float(np.cos(a)), 172 + 34 * float(np.sin(a))] for a in np.linspace(0, 2 * np.pi, 32, endpoint=False)],
                [[96,196],[112,196],[112,224],[96,224]],
                [[128,196],[144,196],[144,224],[128,224]],
            ]
            c["subject"]["anchor"] = [120, 150]
            c["cleanup"]["plate"] = str(root / "原片/clean-plate.png")
            c["cleanup"]["ellipses"] = [[120,230,44,10]]
            config_file.write_text(json.dumps(c))
            command("matte", config_file)
            command("preview", config_file)
            from PIL import Image
            final_preview = sorted((project / "preview").glob("09-*.png"))[0]
            actual = np.array(Image.open(final_preview).convert("RGB"))[125:240,75:165].astype(float)
            expected = np.array(Image.open(root / "原片/clean-plate.png").convert("RGB"))[125:240,75:165].astype(float)
            self.assertLess(float(np.abs(actual-expected).mean()), 1.5,
                            "A subject-shaped ghost remains on the known clean background.")
            self.assertLess(float(np.abs(actual[100:110]-expected[100:110]).max()), 5,
                            "The supplied shadow cleanup region left a visible shadow edge.")
            exit_info = json.loads((project / "preview/exit-check.json").read_text())
            self.assertEqual(exit_info, {"subject_visible_pixels":0,"rod_visible_pixels":0})
            command("render", config_file)
            report = json.loads((project / "output/v001/report.json").read_text())
            self.assertEqual(report["validation"]["gif"]["frame_delays_ms"], [20])
            self.assertEqual(report["validation"]["gif"]["frames"], 260)
            self.assertEqual(report["validation"]["mp4"]["frames"], 312)
            self.assertEqual(rotation.status(state)["next_variant"], "follow")
            command("complete", config_file, "--qa-notes", "Automated export contract test; visual review separate")
            self.assertEqual(rotation.status(state)["next_variant"], "like")
            command("complete", config_file, "--qa-notes", "Idempotence check")
            self.assertEqual(rotation.status(state)["next_variant"], "like")
            gif = project / report["files"]["gif"]["path"]
            with gif.open("ab") as f:
                f.write(b"changed")
            self.assertNotEqual(command("complete", config_file, "--qa-notes", "should reject", success=False).returncode, 0)

    def test_installation_outside_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "installed/fishing-gif"
            subprocess.run([sys.executable,str(REPO / "install.py"),"--dest",str(destination)],check=True,capture_output=True)
            self.assertTrue((destination / "SKILL.md").is_file())
            self.assertEqual(len(list((destination / "assets/rods").glob("0*.png"))),4)
            result = subprocess.run([sys.executable,str(destination / "scripts/fishing_gif.py"),"doctor"],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--make-fixture":
        print(make_fixture(sys.argv[2]))
    else:
        unittest.main()
