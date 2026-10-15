# 个人总览字体

`noto-sans-sc-vf.woff2` 是 Noto Sans SC 可变字体（字重 100–900），使用 FontTools 从本机安装的原始 `NotoSansSC-VF.ttf` 无损转换为 WOFF2，保留完整字形和可变字重。原字体来自 Google Fonts 的 Noto Sans SC / Adobe 思源黑体项目，版权与 SIL OFL 1.1 许可见 `OFL.txt`。

Bot 模板优先使用同系列的 Noto Sans CJK SC。网站使用自托管字体，避免用户设备缺少字体时回退到微软雅黑或其他系统字体。字体只在首次生成个人总览时加载；不使用远程字体服务。

上游：https://github.com/google/fonts/tree/main/ofl/notosanssc
