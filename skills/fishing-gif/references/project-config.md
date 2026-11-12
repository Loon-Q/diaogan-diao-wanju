# 项目配置

从 `prepare` 生成的 `project.json` 开始。像素坐标基于本期拾取帧尺寸，不是原始4K尺寸。媒体路径相对项目目录；默认鱼竿从安装目录查找。

## 主体

- `matte.polygons`：多个外轮廓点列表，合并为主体，包含要带走的布、工具。`holes` 为要挖去的空隙。
- `mask_file`：可选灰度遮罩，白色主体、黑色背景；与拾取帧同尺寸，优先于轮廓。
- `grabcut`、`search_px`、`foreground_erosion_px`：在初始轮廓附近细化的范围。不能把大块背景标为确定前景。
- `reject_hsv`：本期背景色过滤，例如 `[{"low":[81,19,0],"high":[139,255,255]}]`。`protect_rectangles` 为 `[x0,y0,x1,y1]` 列表，保护主体同色细节。蓝地面参数不能用于蓝色主体。
- `min_component_area` 清理小孤点，不能删掉真实细线和配件；`feather` 默认0.45像素。
- `subject.anchor` 为主体真实接触点 `[x,y]`，要求位于不透明主体上。螃蟹选蟹背，不能因某条腿最高就钩在空中。

## 背景

- `cleanup.plate`：来自本期拾取帧、镜头匹配、已去除主体及投影的补片。
- `mode=patch`：按主体遮罩扩张；`dilate` 是奇数核尺寸，`feather` 为柔边，`ellipses` 为阴影 `[cx,cy,rx,ry,angle可选]` 列表。
- `mode=band`：适合大块平坦地面，`band=[渐入起点y,全覆盖起点y,全覆盖终点y,渐出终点y]`。覆盖所有被移走物体和投影，避免主体形状明暗残影。
- `mode=full`：仅用于整张背景匹配时。也可设置 `mask_file` 使用自定义灰度补片遮罩。

## 运动

- `rod.scale` 为1254×1254素材的缩放率，720宽竖屏默认0.66；`rod.grip` 是本期握柄支点，`rod.angle` 为度数。保持手和挂饰可读。可用 `rod.asset` 指定本期完整素材，仍需1254×1254与统一锚点，不得镜像转发箭头。
- `subject.path` 可选四个二维点定义连续三次Bezier曲线，第一点等于 `anchor`。终点确保主体与鱼竿都完全出画。不能拆成两次缓动造成半空停顿。
- `swing_degrees` 为整体摆动，主体大时减小。
- `pickup` 必须等于预处理片段帧数/60；`lift` 通常晚0.12秒，`exit` 再晚约3.4秒，`end` 留0.4～0.5秒空镜。
- `enter_start < enter_end < pickup`，`line_start < pickup`；短片段需缩短入场，接触后才能提起。
- 输出固定50fps GIF和60fps MP4。`output.gif_width` 默认最高540像素，动作时钟一致。
