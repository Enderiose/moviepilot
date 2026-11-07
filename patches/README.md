# patches/ —— 个人镜像补丁（非上游代码）

本目录的脚本会在 `docker/Dockerfile` 的 `final` 阶段、`COPY --from=prepare_code /app /app`
之后**逐个执行**，对上游源码做最小改动，用于修复本项目遇到的 NAS 侧问题。

## 执行方式

```dockerfile
COPY patches/ /patches/
RUN set -eu; \
    for p in /patches/[0-9][0-9]-*.py; do \
        echo "== applying $(basename "$p")"; \
        MP_APP_ROOT=/app MP_PATCH_NO_BACKUP=1 python3 "$p"; \
    done; \
    python3 -m py_compile /app/app/modules/emby/emby.py /app/app/helper/image.py
```

- 文件名 `NN-*.py` 的 `NN` 决定执行顺序
- `MP_APP_ROOT`：应用根目录，容器内 `/app`；本地验证时可指向源码树
- `MP_PATCH_NO_BACKUP=1`：不生成 `.bak` 文件（避免把备份打进镜像）

## 设计原则

1. **幂等**：脚本内带标记（如 `netstorm-patch:internal-probe`），重复执行输出 `SKIP` 并退出 0
2. **失败即中断构建**：上游重构导致锚点失配时脚本 `return 1`，
   `RUN set -eu` 让构建**失败**——宁可不出版本，也不出坏镜像
3. **只改必要的几行**：不做格式化、不改无关代码，便于和上游 diff 对照

## 补丁清单

| 文件 | 目标 | 作用 |
|---|---|---|
| `10-emby-image-probe-internal.py` | `app/modules/emby/emby.py` → `generate_external_image_link()` | 探测改用**内网** `_host`，返回仍用公网 `_playhost`。原实现拿公网地址做 GET 探测，NAS 内部访问自己的域名要 NAT 回环（hairpin）→ 必然超时 → 必然误报「未获取到返回数据」并返回 `None` |
| `20-emby-remoteimages-debug.py` | 同上文件 → `get_remote_image_by_id()` | 把 `Items/RemoteImages 未获取到返回数据，采用本地图片` 从 `info` 降为 `debug`（国产剧无 TMDB 图时必现，属正常回退） |
| `30-image-internal-fallback.py` | `app/helper/image.py` | 新增 `ImageHelper._internal_equivalent_url()`；`fetch_image` / `async_fetch_image` 首次失败后用内网等价地址重试。修复 Telegram 等「服务端下图」渠道无图 |
| `40-torrent-direct-then-proxy.py` | `app/helper/torrent.py` → `TorrentHelper.download_torrent()` | 拉取 `.torrent` 改为**直连优先 → 直连无响应才降级系统代理**。NAS 出网到部分站点/CDN 存在丢包与 MTU 黑洞（`ping -M do -s 1448` 全丢、`tcp_mtu_probing` 需设 2），报 `ReadTimeout` / `RemoteDisconnected`；只影响拉种子的 3 个请求，搜索/登录/RSS/announce 不动 |

## 本地验证（不用起容器）

```bash
git clone --depth 1 -b custom https://github.com/Enderiose/moviepilot.git /tmp/mpc
export MP_APP_ROOT=/tmp/mpc MP_PATCH_NO_BACKUP=1
for p in patches/[0-9][0-9]-*.py; do python3 "$p"; done
cd /tmp/mpc && python3 -m py_compile app/modules/emby/emby.py app/helper/image.py && git diff --stat
```

预期 `git diff --stat`：`app/helper/image.py | 36 +`、`app/modules/emby/emby.py | 27 ±`。

## 插件补丁不在这里

`mediaservermsg` 的图片兜底补丁属于**插件**改动，已直接体现在插件仓库
[`Enderiose/moviepilot-plugins`](https://github.com/Enderiose/moviepilot-plugins)
的 `plugins.v2/mediaservermsg/` 里，不打进镜像。

## 已知风险

上游 `docker/Dockerfile` 在 `prepare_code` 阶段会下载
`MoviePilot-Plugins/archive/refs/heads/main.zip` 并把 `plugins.v2/*` 灌进 `/app/app/plugins`。
上游 main 已转向 v3，**若哪天 `plugins.v2/` 被移除，构建会在该 `mv` 处失败**。
届时把那一行 URL 换成固定 tag 即可（2026-11-08 实测：上游 main 仍有 `plugins.v2/`，但我们的 34 个插件里有 **18 个已不在其中**，故必须靠 `plugins-vendor/` 兜底），例如：

```dockerfile
curl -sL "https://github.com/jxxghp/MoviePilot-Plugins/archive/refs/tags/<某个含 plugins.v2 的 tag>.zip"
```
