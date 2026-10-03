# Four-screen live display

`web/` 提供六页轮播及固定左、中、右角色。`tools/feed_server.py` 只读过滤数据，`tools/stream.cjs` 使用 Chrome 截图和 FFmpeg 生成四路 HLS。`tools/overview.html` 是九画面预览，不作为电视连接状态证明。

端点、媒体路径、渲染依赖均由环境变量注入。macOS 统一入口见项目根目录 `deployment/macos/run.py`。视觉帧与认知声学使用各自的时效门限，原始视频、语音转写和人脸属性不进入展示投影。

旧 Linux 部署快照保留在本机完整备份，公开代码不携带其设备地址或登录数据。
