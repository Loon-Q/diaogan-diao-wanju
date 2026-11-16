# 拾取帧抠像

只处理决定离地的那一帧。原片阶段不使用抠像或清理背景，即使已提前准备好资产。

细腿、灰色关节、钳子及所持布容易被粗轮廓或背景色过滤误删。优先用官方SAM2.1 small 生成遮罩，再检查和补点；简单轮廓可用核心 matte 的 polygons + GrabCut，已有工具输出的优质灰度遮罩也可直接用。SAM2是可选项，核心渲染不依赖PyTorch。

## 可选SAM2

同事不用提供API Key。由Codex在需要时安装，隔离环境和模型不随发布包分发。setup 会下载PyTorch、固定版本的官方SAM2代码及约184MB权重，首次需要网络、Python 3.10+ 和Git。

```sh
PY 技能目录/scripts/sam_mask.py setup
PY 技能目录/scripts/sam_mask.py segment 项目/project.json
PY 技能目录/scripts/fishing_gif.py matte 项目/project.json
```

在项目的 matte.sam2.objects 设置本期检查过的对象：

```json
{
  "objects": [
    {"box": [50,200,600,880], "positive": [[300,400],[100,450],[550,460]], "negative": [[320,700],[220,950]]},
    {"box": [180,735,295,875], "positive": [[245,820]], "negative": [[220,930]], "clip": [175,735,300,876]}
  ]
}
```

这是格式示例，不是新视频可复制的坐标。每个对象的 box、positive、negative 来自本期 pickup.png。多对象会合并，适合补上所持布；clip 限制该对象范围，subtract=true 可减掉误带的区域。segment 保存 assets/sam-mask.png 并填入mask_file，运行matte得到原始RGB主体及棋盘格。默认CPU；设备支持时可选 --device mps/cuda。

SAM输出仍需实际检查：全部腿与钳子、灰色零件、眼灯、工具，夹缝没有地面，鞋尖不随布移动。不以模型分数代替视觉验收。误删处补正点，误带处补负点或分对象处理；不要通过粗暴加厚整圈轮廓掩盖错误。

缺少SAM环境时如实使用核心遮罩流程，不声称已做SAM。setup不成功时先判断依赖或网络问题；不让同事提供第三方API Key，不使用非商业授权的替代模型冒充同一能力。

## 干净背景

只抠干净还不够。取同一拾取帧，用内置图像生成工具仅移除主体、所持物和其投影，保留场景、鞋和人物，恢复被遮住的小块地面或鞋尖。不得用新生成的整段视频替换原片铺垫。补片与原像素混合后实际看完整出画的尾段，检查色块、轮廓、纹理尺度与物体位置。
