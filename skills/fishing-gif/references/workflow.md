# 制作命令

`PY` 是已安装依赖的Python，`CLI` 是本技能 `scripts/fishing_gif.py` 的完整路径。路径必须引用，不把用户文件名拼接为可执行命令。

首次创建 `.venv`：`python -m venv 技能目录/.venv`，用该环境Python运行 `-m pip install -r 技能目录/requirements.txt`。macOS/Linux在 `.venv/bin/python`，Windows在 `.venv/Scripts/python.exe`。

```sh
PY CLI doctor
PY CLI inspect 输入.mp4 --out 审片目录
PY CLI prepare 输入.mp4 --out 新项目目录 --start 29.8 --duration 3.233333333
PY CLI matte 项目目录/project.json
PY CLI preview 项目目录/project.json
PY CLI render 项目目录/project.json
PY CLI complete 项目目录/project.json --qa-notes "实际查看了导出和具体动作接缝"
PY CLI rotation
```

`inspect` 输出 `timeline.jpg` 和 `source-info.json`，默认最多48张。长视频需要分段查看；不能只看首帧就声称审过全片。`prepare` 最长源片段8秒，保留比例，不超过720宽/1280高，不放大低分辨率输入。补帧出现细节扭曲时换时机或缩短片段。

`prepare --variant auto` 为默认；测试添加 `--state-dir 测试状态目录`。准备不推进轮换。相同内容按SHA256识别，不因改名换款。修改时复用项目；脚本自动另存输出版本。

查看拾取帧后按 [配置说明](project-config.md) 填写 `project.json`。`matte` 输出原像素主体 `subject.png` 及 `matte-checker.png`。补片用本期拾取帧通过内置图像生成工具制作，只去掉主体和其投影，保持场景；不复用旧视频背景。

`preview` 输出10张关键帧、`contact.jpg` 和 `exit-check.json`。数值出画检查不能替代视觉检查。`render` 输出 `output/v001` 等新版本，含GIF、MP4和报告，不覆盖旧版。GIF每帧20ms且循环；MP4为H.264无声。可额外生成小尺寸预览，但保持50fps，不能用15fps预览替代流畅版本。

完成前查看真实导出，必要时播放；记录实际验收范围。`complete` 验证配置及成片摘要后提交一次轮换，重复完成幂等。未完成的新片不推进游标，旧片延后重试仍保留原款。显式指定款式不推进游标。

最终简短说明选段和当前挂饰，嵌入GIF、链接MP4。用户不需要理解参数或每次选鱼竿。不得自动发送第三方或发布GitHub。
