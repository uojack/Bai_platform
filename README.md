# BaiPlay

本地信息发布与实时空间画面开发项目。包含终端管理界面、场景与权限管理、华为电视 DLNA 适配、六页 AOS Pulse 轮播、全景厅三屏渲染，以及只读视觉和声学数据投影。

## 运行边界

管理界面的默认设备输出是模拟模式。四台电视实际播放由独立的 DLNA 监护程序和 HLS 媒体服务承担。HTTP 成功、DLNA `TRANSITIONING` 或持续请求视频分片，均不等于现场已经看见正确画面。物理电视开机恢复和三屏同步须现场验收。

视觉与声学数据先经过只读投影和时效判断；缺失、过期、代理指标与未实现项明确显示。dBFS 未经声压级标定；认知声学研究来源不能冒充屏幕附近的现场测量。不会通过显示服务向声学设备发送控制命令，也不发送网络时钟广播。

## 本机开发

建议 Python 3.12、Node.js、Google Chrome。安装依赖：

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm install
mkdir -p private
cp deployment/examples/site.json private/site.json
cp deployment/examples/displays.json private/displays.json
cp deployment/examples/inventory.json private/inventory.local.json
```

填写私有配置中的 Python、Node、Chrome、FFmpeg 和 Playwright 路径，以及经核验的网络接口、源端点和设备身份。`imageio-ffmpeg` 的二进制路径可用 `.venv/bin/python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"` 获取。`playwright` 可填安装后的 `node_modules/playwright-core` 绝对路径。

示例故意不包含可控制的电视。每台实际电视需提供 `address`、`mac_address`、`udn`、`role`、`filename`、`title`；先比对 UDN，再启用推送。重新绑定 IP 不应改变左、中、右角色。

```sh
.venv/bin/python deployment/macos/run.py manager
.venv/bin/python deployment/macos/run.py media
.venv/bin/python deployment/macos/run.py acoustics
.venv/bin/python deployment/macos/run.py feed
.venv/bin/python deployment/macos/run.py stream
```

管理页默认 `http://127.0.0.1:4340`；九画面预览默认 `http://127.0.0.1:18785/overview.html`。这些命令需在不同终端运行。私有媒体留在 `web/media`，不进入 Git。历史视频合成代码保留在 `video`，其输入媒体随完整本机备份保管。

## macOS 自动运行

```sh
sudo .venv/bin/python deployment/macos/install_daemons.py
```

安装五项系统管理的服务，但进程以项目所有者的普通账户运行。独立电视控制服务必须在源数据、媒体地址和跨网段访问全部验证后安装：

```sh
sudo .venv/bin/python deployment/macos/install_daemons.py --supervisor
```

切换前停止旧主机的电视监护程序，避免两台主机争抢同一电视。`caffeinate -i` 在服务运行时防止空闲睡眠，不阻止手动关机。FileVault 开启时冷启动仍可能需要用户解锁磁盘；安装成功不能替代真实重启验证。

每次升级先备份 `private`、`runtime` 和旧服务定义，保留恢复步骤。停用用 `sudo launchctl bootout system /Library/LaunchDaemons/com.baiyin.baiplay.<component>.plist`；移走该 plist 才能取消下一次自动启动。不得仅凭本机进程正常就停掉原播放主机。

## 验证

```sh
.venv/bin/python -m unittest discover -s tests -v
```

测试覆盖模拟场景发布与恢复、身份与会话、设备注册、权限、电视开机重试、播放结束恢复，以及尊重遥控器手动停止。合成测试设备使用文档专用网段。现场验证至少同时记录新媒体 URI、分片请求持续增长、画面数据时效与用户实屏确认。

## 仓库内容

本仓库仅保存可公开代码、依赖与部署模板。`private/`、登录数据库、初始密码、现场配置、历史部署记录、媒体和运行日志均已排除。完整开发资料由本机工作目录及原主机归档保管。同步前运行 `git diff --cached` 并检查新增文件，不能用 Git 代替完整数据备份。
