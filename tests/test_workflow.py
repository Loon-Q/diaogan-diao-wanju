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
    def configure(self,root,motion="lift",variant="auto"):
        import numpy as np
        video=make_fixture(root/"input")
        project=root/"project";state=root/"state"
        command("prepare",video,"--start",0,"--duration",1,"--out",project,
                "--state-dir",state,"--width",240,"--motion",motion,"--variant",variant)
        config=project/"project.json";c=json.loads(config.read_text())
        c["matte"]["polygons"]=[
            [[120+34*float(np.cos(a)),170+34*float(np.sin(a))] for a in np.linspace(0,2*np.pi,32,endpoint=False)],
            [[96,194],[112,194],[112,222],[96,222]],[[128,194],[144,194],[144,222],[128,222]]]
        c["subject"]["anchor"]=[120,150]
        if motion in ("drag","low_lift"):c["subject"]["pivot"]=[140,220]
        c["cleanup"]["plate"]=str(root/"input/clean-plate.png")
        c["cleanup"]["ellipses"]=[[120,230,44,10]]
        config.write_text(json.dumps(c));command("matte",config)
        return video,project,config,c,state

    def test_native_cache_exact_pickup_and_no_early_cutout(self):
        import cv2,numpy as np
        import fishing_gif as renderer
        renderer.deps()
        with tempfile.TemporaryDirectory() as tmp:
            video,project,config,c,state=self.configure(Path(tmp))
            a=cv2.VideoCapture(str(video));b=cv2.VideoCapture(str(project/c["media"]["native"]))
            for i in range(31):
                oa,fa=a.read();ob,fb=b.read();self.assertTrue(oa and ob)
                self.assertTrue(np.array_equal(fa,fb),f"Native frame {i} changed")
            a.release();b.release()
            scene=renderer.Scene(project,c)
            for t in [0,.25,.75,1]:
                actual=scene.render(t);native,_=scene.native(t)
                self.assertEqual(scene.stats["base"],"original_video")
                self.assertLessEqual(int(np.abs(actual[225:250,65:165].astype(int)-native[225:250,65:165].astype(int)).max()),1)
            scene.render(1+1/60)
            self.assertEqual(scene.stats["base"],"pickup_composite")

    def test_all_four_rigs_enter_offscreen_connect_and_clear(self):
        import fishing_gif as renderer
        renderer.deps()
        with tempfile.TemporaryDirectory() as tmp:
            _,project,config,c,_=self.configure(Path(tmp))
            for variant in rotation.ORDER:
                c["variant"]=variant;scene=renderer.Scene(project,c)
                scene.render(0)
                self.assertTrue(all(v==0 for k,v in scene.stats.items() if k.endswith("visible_pixels")))
                scene.render(c["timing"]["pickup"]+.2)
                self.assertAlmostEqual(scene.stats["rope_length"],scene.rope,places=3)
                self.assertTrue(all(v==0 for v in scene.exit_check().values()))

    def test_drag_keeps_ground_height_and_cleans_tail(self):
        import fishing_gif as renderer
        import numpy as np
        from PIL import Image
        renderer.deps()
        with tempfile.TemporaryDirectory() as tmp:
            _,project,config,c,_=self.configure(Path(tmp),motion="drag")
            scene=renderer.Scene(project,c)
            for u in [.1,.4,.8,1]:
                t=c["timing"]["lift"]+u*(c["timing"]["exit"]-c["timing"]["lift"])
                matrix,hook,_=scene.pose(t)
                hull=scene.ground_hull@matrix[:,:2].T+matrix[:,2]
                self.assertAlmostEqual(float(np.max(hull[:,1])),scene.ground_level,places=3)
                self.assertGreater(float(matrix[1,0]),.01)
                np.testing.assert_allclose(hook,matrix[:,:2]@scene.anchor+matrix[:,2],atol=1e-4)
            self.assertTrue(all(v==0 for v in scene.exit_check().values()))
            end=scene.render(c["timing"]["end"]-1/60)
            expected=np.array(Image.open(Path(tmp)/"input/clean-plate.png"))
            self.assertTrue(np.array_equal(end,scene.clean))
            self.assertLess(float(np.abs(end[125:240,75:165].astype(float)-expected[125:240,75:165].astype(float)).mean()),1.5)

    def test_low_lift_tips_before_lifting_and_stays_low(self):
        import fishing_gif as renderer
        import numpy as np
        renderer.deps()
        with tempfile.TemporaryDirectory() as tmp:
            _,project,config,c,_=self.configure(Path(tmp),motion="low_lift")
            c['subject']['rise_px']=18
            scene=renderer.Scene(project,c)
            first=scene.weighted_kinematics(c['timing']['pickup']+.08)
            self.assertEqual(first[1],0)
            self.assertEqual(first[2],0)
            self.assertGreater(first[0][1,0],.01)
            for u in [.1,.3,.6,1]:
                m,height,dx,_=scene.weighted_kinematics(c['timing']['pickup']+u*1.3)
                hull=scene.ground_hull@m[:,:2].T+m[:,2]
                self.assertAlmostEqual(float(np.max(hull[:,1])),scene.ground_level-height,places=3)
                self.assertLessEqual(height,18)
            self.assertTrue(all(v==0 for v in scene.exit_check().values()))
            c['subject']['motion']='drag';c['subject']['rise_px']=18
            with self.assertRaisesRegex(ValueError,'ground contact'):renderer.Scene(project,c)

    def test_filled_shadow_follows_and_fades_outside_in(self):
        import fishing_gif as renderer
        import numpy as np
        import cv2
        renderer.deps()
        with tempfile.TemporaryDirectory() as tmp:
            _,project,config,c,_=self.configure(Path(tmp),motion="low_lift")
            c['subject']['rise_px']=18
            c['cleanup']['ground_shadow']={'ellipses':[[120,230,35,14,0]],'reference':[120,190],'opacity':.3}
            scene=renderer.Scene(project,c)
            # A fully formed footprint fills the old gap between the feet.
            d=scene.shadow_distance
            self.assertGreater(d[230,120],0)
            actual_pose,actual_motion=scene.pose,scene.weighted_kinematics
            identity=np.array([[1,0,0],[0,1,0]],np.float32)
            scene.pose=lambda t:(identity,scene.anchor,1)
            scene.weighted_kinematics=lambda t:(identity,0,0,1)
            low=scene.ground_shadow_field(c['timing']['pickup']+.3)
            scene.weighted_kinematics=lambda t:(identity,18,0,1)
            high=scene.ground_shadow_field(c['timing']['pickup']+.3)
            scene.pose,scene.weighted_kinematics=actual_pose,actual_motion
            self.assertGreater(high[230,120],high[230,151])
            self.assertLess(high[230,151]/low[230,151],high[230,120]/low[230,120])
            fields=[scene.ground_shadow_field(c['timing']['pickup']+dt) for dt in [.17,.5,.7]]
            centers=[]
            yy,xx=np.indices(fields[0].shape)
            for f in fields:
                count,_,_,_=cv2.connectedComponentsWithStats((f>.02).astype(np.uint8))
                self.assertEqual(count-1,1)
                centers.append(float((xx*f).sum()/f.sum()))
            self.assertGreater(centers[-1],centers[0]+20)
            self.assertTrue(all(v==0 for v in scene.exit_check().values()))
            c['subject'].update(motion='drag',rise_px=0)
            drag=renderer.Scene(project,c)
            self.assertGreater(float(drag.ground_shadow_field(c['timing']['pickup']+.7).max()),.25)
            self.assertTrue(all(v==0 for v in drag.exit_check().values()))

    def test_optional_gif_and_anchor_tracking(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            _,project,config,c,state=self.configure(Path(tmp),variant="like")
            native=project/c["media"]["native"];before=hashlib.sha256(native.read_bytes()).hexdigest()
            command("track",config)
            tracked=json.loads(config.read_text())["subject"]["anchor_track"]
            self.assertEqual(len(tracked),31)
            self.assertLess(abs(tracked[0][2]-152),3)
            self.assertEqual(hashlib.sha256(native.read_bytes()).hexdigest(),before)
            command("render",config,"--gif")
            report=json.loads((project/"output/v001/report.json").read_text())
            self.assertEqual(report["validation"]["gif"]["frame_delays_ms"],[20])
            self.assertEqual(report["validation"]["gif"]["frames"],round(c["timing"]["end"]*50))
            command("complete",config,"--qa-notes","Synthetic explicit variant export")
            self.assertEqual(rotation.status(state)["next_variant"],"follow")

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
            self.assertTrue(all(v==0 for v in exit_info.values()))
            command("render", config_file)
            report = json.loads((project / "output/v001/report.json").read_text())
            self.assertNotIn("gif",report["files"])
            self.assertFalse(list((project/"output/v001").glob("*.gif")))
            self.assertEqual(report["validation"]["mp4"]["frames"],round(c["timing"]["end"]*60))
            self.assertEqual(rotation.status(state)["next_variant"], "follow")
            command("complete", config_file, "--qa-notes", "Automated export contract test; visual review separate")
            self.assertEqual(rotation.status(state)["next_variant"], "like")
            command("complete", config_file, "--qa-notes", "Idempotence check")
            self.assertEqual(rotation.status(state)["next_variant"], "like")
            mp4 = project / report["files"]["mp4"]["path"]
            with mp4.open("ab") as f:
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
            marker=destination/".venv/keep-dependencies.txt"
            marker.parent.mkdir();marker.write_text("keep")
            subprocess.run([sys.executable,str(REPO/"install.py"),"--dest",str(destination)],check=True,capture_output=True,env={**__import__("os").environ,"CODEX_HOME":str(Path(tmp)/"codex")})
            self.assertEqual(marker.read_text(),"keep")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--make-fixture":
        print(make_fixture(sys.argv[2]))
    else:
        unittest.main()
