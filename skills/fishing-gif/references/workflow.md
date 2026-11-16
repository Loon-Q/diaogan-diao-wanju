# 制作步骤

`PY` 是安装了 requirements.txt 的Python，`CLI` 是本技能 scripts/fishing_gif.py 的绝对路径。用户不用填写参数，由Codex审片并配置。Windows用 `.venv/Scripts/python.exe`，macOS/Linux用 `.venv/bin/python`。

```sh
PY CLI doctor
PY CLI inspect 原片.mp4 --out 审片目录
PY CLI prepare 原片.mp4 --out 新项目 --start 27 --duration 6 --motion lift
PY CLI matte 新项目/project.json
PY CLI track 新项目/project.json
PY CLI preview 新项目/project.json
PY CLI render 新项目/project.json
PY CLI complete 新项目/project.json --qa-notes "实际查看的接缝、动作、背景与完整出画"
PY CLI rotation
```

首次安装：`python -m venv 技能目录/.venv`，用该环境Python运行 `-m pip install -r 技能目录/requirements.txt`。安装包不含虚拟环境、个人素材或状态。

`prepare` 的 start/duration 基于原片时钟并对齐原生帧，示例在27秒起、33秒拾取；靠近结尾时会使用最后可解码帧并报告实际拾取时刻。缓存 `media/source-native.mkv` 使用无损FFV1，包含拾取帧，保留原RGB。最多选30秒，通常约6秒；不放大低分辨率视频。没有提前抠像或运动插帧。

默认 `--variant auto`。制作新项目用新目录；测试传 `--state-dir 隔离目录`。只在 `complete` 成功时推进一次轮换。修订使用同一原视频与状态，或复用配置后渲染新版本；保持用户确认的款式和动作。

查看拾取帧并填写 anchor、matte 和 cleanup。复杂轮廓按[抠像](matting.md)运行SAM2；已有灰度遮罩也可直接填 mask_file。`track` 在原生图像上逆向跟踪挂点，需先有主体遮罩。跟踪失败时检查入场时段，改选稳定片段或由Codex填写 anchor_track；不要让用户理解跟踪参数，也不要保留滑动的挂点。

补背景使用Codex内置 imagegen。取本期拾取帧，仅移除主体、所持道具及其投影。鞋/人物可用 protect_mask_file 保留可见原像素，被布遮住的小块鞋尖由补片恢复。没有图像生成能力则保留项目并如实说明。

`preview` 的 contact.jpg、关键帧与 exit-check.json 只供内部审查。不能用数值出画替代实际看成片；竿尖可能已离开画面但末梢道具仍在，需要逐项检查。`render` 默认仅输出 H.264 无声60fps MP4、报告到 output/v001 等新目录。仅用户明确要GIF时加 `--gif`，才额外计算独立50fps母版与GIF。

最终只展示MP4；不把GIF/分镜图当默认预览。完整版本保留约5～6秒含机器人的原片动作，再接约1秒钓走与短空镜。已确认试片扩展完整版时保持已确认动作，可按同参数重新渲染或将原片接到试片前；检查连接处原片时钟连续，无重复倒放。

v0.2配置使用 schema 2；v0.1老项目保留原渲染器或重新 prepare，同片仍保留选款。不要改老成片，也不要仅修改说明却继续运行v0.1慢速模板。
