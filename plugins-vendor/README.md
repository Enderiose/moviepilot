# plugins-vendor/ —— 钉死的插件版本（构建时覆盖镜像内版本）

## 为什么需要它

上游 `docker/Dockerfile` 的 `prepare_code` 阶段会把
`jxxghp/MoviePilot-Plugins@main` 的 `plugins.v2/*` 整包灌进 `/app/app/plugins`。
问题有两个：

1. **上游会把停更插件从 `plugins.v2` 删掉**。2026-11-07 实测：本项目在用的 34 个插件里，
   **18 个已不在上游 `plugins.v2`**（`adaptiveintroskip`、`autobackup`、`mediacovergenerator`、
   `weworkippw`、`embymetarefresh`、`torrenttransferenhanced`…）。
   只靠运行时 `docker cp` 补进去的话，**容器一重建（compose up / pull / DSM 重启容器）就全丢**。
2. 剩下 16 个虽然还在上游，但**版本会随上游漂移**，导致「验证过的组合」失效。

## 做法

`docker/Dockerfile` 在 `COPY --from=prepare_code /app /app` 之后、打补丁之前：

```dockerfile
COPY plugins-vendor/ /app/app/plugins/
```

因为是「目录内容合并」，只会覆盖同名的 34 个插件，镜像自带的其它插件（约 88 个）保持不变。

## 内容来源

- 取自现网容器 `moviepilot-v2` 的 `/app/app/plugins/`（即实际在跑、已通过兼容性验证的版本）
- 与插件仓库 <https://github.com/Enderiose/moviepilot-plugins> 的 `plugins.v2/` 同源
- 快照时间：2026-11-07
- 版权归各插件原作者（GPL-3.0），本目录仅作原样归档

## 怎么更新

```bash
# 1) 从容器重新导出（或从插件仓库同步）
docker cp moviepilot-v2:/app/app/plugins /tmp/pv
# 2) 只取在用的 34 个覆盖进来
for d in $(ls plugins-vendor); do rm -rf "plugins-vendor/$d"; cp -r "/tmp/pv/$d" "plugins-vendor/$d"; done
# 3) 去掉 __pycache__ 后提交
find plugins-vendor -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
```

## 注意

- 新增插件时，除了这里，也要同步更新
  [`Enderiose/moviepilot-plugins`](https://github.com/Enderiose/moviepilot-plugins) 的
  `package.v2.json`（否则插件市场里看不到、装不了）。
- 插件自带的 `requirements.txt` 不会被自动安装，需要在 `requirements.in` 里预装
  （当前已预装 `python-hosts`、`webdavclient3`，见该文件末尾）。
