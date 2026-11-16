# 项目配置（schema 2）

从 prepare 生成 project.json。所有坐标基于本期 pickup.png，路径相对项目目录。不要复制白色机器人或螃蟹的坐标到新视频。

## 原片与时钟

media.native 为无损原生视频；source.native_fps/native_frames 描述时钟。末帧必须与 pickup.png 像素完全相同。pickup=(native_frames-1)/native_fps，lift=pickup；到这一时刻为止都显示原片。渲染器不在此前替换主体或地面。

## 主体遮罩及挂点

matte.mask_file 接受同尺寸灰度遮罩，优先于 polygons。polygons/holes 可由Codex辅助GrabCut细修；search_px、foreground_erosion_px 控制边缘搜索，feather 控制小幅柔边。reject_hsv 仅适用于本期明确背景色，protect_rectangles 保护同色眼灯等细节；不能按上一条视频的蓝色阈值过滤新主体。

subject.anchor=[x,y] 是真实挂点，必须落在不透明主体上。选蟹背或机器人结构位置，避免因为脚最高而挂在空中。subject.anchor_track 可填原片时钟的 [秒,x,y] 递增列表，最后必须是 [pickup,anchor.x,anchor.y]；可运行 track 辅助生成，实际检查其稳定性。

## 背景及投影

cleanup.plate 为本期干净背景。mode=patch 默认按遮罩扩张，dilate/feather 决定覆盖和柔边；ellipses=[[cx,cy,rx,ry,可选angle]] 扩展到原投影。mode=band 使用 band=[y0,y1,y2,y3] 的渐入/全覆盖/渐出区；mode=full 仅用于全帧匹配。mask_file 可以提供自定义补片区域。必须完整清除原主体，不把原像素混回旧轮廓。

protect_mask_file 白色标记留下的可见鞋/人/物体。主体遮罩优先：布原先遮住的鞋尖仍需补片恢复。shadow_mask_file 可单独限定投影，避免拖走时把地板纹理一并移动。补片不能有色块、湿斑状修补或残影；不要用取自带阴影像素的空间色差场把旧轮廓重新印回地面。

## 提走与拖走

subject.motion=lift 默认；timing.exit-lift 约1.05秒，end 再留至少0.35秒。根据体量和动作清晰度调整，不长时间蓄力或停在半空。

lift 默认沿右上方连续加速、摆动约3度；end=[x,y] 可调整终点，终点和竿线必须完全出画。path 可选4点Bezier，起点等于anchor，避免过多旋转暴露二维贴片感。

drag 用于真实体量大、应保持地面接触的主体：prepare --motion drag，默认约1.3秒，沿地面向右拖走。end.x 决定终点，ground_slope 为地面投影方向dy/dx，竿线随牵引倾斜；默认不旋转、不升空，阴影随接地点移动。不能依赖普通平移生成不存在的脚步、背面或三维转身；严重遮挡、显著运镜时换镜头或补充素材。

## 鱼竿

rod.scale 在720×1280参考画面默认0.66，随画幅缩放。angle 默认19度，Codex应调整到手从画面边缘连接、竿尖有足够头部空间。line_length 不填时按挂点高度计算；小螃蟹头顶空间较小，应缩短线长，不能直接套白色机器人的长度。绷紧后线长保持不变，靠手、竿和主体一起移动完成提走。

pendant_scale/pendant_angle 控制独立吊坠的大小和朝向；避免覆盖机器人眼睛、挂点或夹爪。base_asset/pendant_asset 可替换注册素材，画布1254×1254。四款默认各有自己的挂饰连接点；自定义用 pendant_anchor 指定。转发箭头不镜像，只有一只手，绞盘静止。

enter_start<enter_end<=line_start<contact<=pickup==lift<exit<end。默认在最后约1.4秒送竿落线。竿、手、吊坠和线起始均在画外，无透明度淡入或突然显示。

output.mp4_fps=60。output.gif 默认为 false；只有用户要求才设true或 render --gif，GIF为50fps、gif_width默认540。原片25/30fps的真实画面按自身帧率采样，不能把重复帧说成原片动作升级到了60fps。
