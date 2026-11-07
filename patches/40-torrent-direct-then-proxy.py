#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MoviePilot 核心补丁：拉取种子文件「直连优先，失败自动降级代理」

背景
----
NAS 出网到部分站点/CDN 存在丢包与 MTU 黑洞（`ping -M do -s 1448` 全丢，
`tcp_mtu_probing` 已设 2 仍偶发），拉 .torrent 时报
    ReadTimeout / RemoteDisconnected('Remote end closed connection without response') / ConnectTimeout
同一请求经 http://192.168.2.4:7890 稳定 <1s。但**有些站点经代理反而失败**
（猫站 ProxyError、高清杜比 SSLError），所以不能无条件走代理。

策略
----
每次 GET：
  1) 先直连（proxies=None）—— 和原行为完全一致
  2) 直连拿不到响应（异常/None）时，**再用 settings.PROXY 重试一次**
站点定义里的 proxy 开关被忽略（保持「只影响拉种子」的边界）。

影响面
----
仅 app/helper/torrent.py → TorrentHelper.download_torrent() 内的 3 处请求：
首次 GET、302 跟随 GET、首次下载确认表单 POST。
搜索 / 登录 / RSS / tracker announce 一律不变（announce 是 qB 干的）。

幂等：含标记 netstorm-patch:torrent-direct-then-proxy 时跳过。

用法：
    docker cp patch-torrent-direct-then-proxy.py moviepilot-v2:/tmp/
    docker exec moviepilot-v2 python3 /tmp/patch-torrent-direct-then-proxy.py
    docker restart moviepilot-v2
"""
import io
import os
import sys

APP_ROOT = os.environ.get("MP_APP_ROOT", "/app")
TARGET = os.path.join(APP_ROOT, "app/helper/torrent.py")
MARK = "netstorm-patch:torrent-direct-then-proxy"

# 上一版「强制代理」补丁的产物，先还原
FORCED = "proxies=settings.PROXY  # netstorm-patch:torrent-force-proxy"
PLAIN = "proxies=settings.PROXY if proxy else None"

# 首次 GET（唯一一处以 `req = ` 开头、后面紧跟 while 的）
OLD_GET = '''        # 下载种子文件
        req = RequestUtils(
            ua=ua,
            cookies=cookie,
            referer=referer,
            proxies=settings.PROXY if proxy else None
        ).get_res(url=url, allow_redirects=False)
        while req and req.status_code in [301, 302]:
            url = req.headers['Location']
            if url and url.startswith("magnet:"):
                return None, url, "", [], f"获取到磁力链接"
            req = RequestUtils(
                ua=ua,
                cookies=cookie,
                referer=referer,
                proxies=settings.PROXY if proxy else None
            ).get_res(url=url, allow_redirects=False)
'''

NEW_GET = '''        # 下载种子文件  # netstorm-patch:torrent-direct-then-proxy
        # 直连优先（与原行为一致）；直连拿不到响应时才降级到系统代理重试一次。
        # 原因：部分站点/CDN 从本机直连丢包（MTU/路径问题），但经代理稳定；
        # 另有部分站点经代理反而失败，所以不能无条件走代理。
        def _fetch_torrent(_url: str):
            _res = RequestUtils(
                ua=ua,
                cookies=cookie,
                referer=referer,
                proxies=None
            ).get_res(url=_url, allow_redirects=False)
            if _res is not None:
                return _res
            if not settings.PROXY:
                return None
            logger.warn(f"直连无响应，改用代理重试：{str(_url).split('?')[0]}")
            return RequestUtils(
                ua=ua,
                cookies=cookie,
                referer=referer,
                proxies=settings.PROXY
            ).get_res(url=_url, allow_redirects=False)

        req = _fetch_torrent(url)
        _hops = 0
        while req and req.status_code in [301, 302]:
            _hops += 1
            if _hops > 5:
                logger.warn("种子文件跳转次数过多，已放弃")
                req = None
                break
            url = req.headers['Location']
            if url and url.startswith("magnet:"):
                return None, url, "", [], f"获取到磁力链接"
            req = _fetch_torrent(url)
'''

# 首次下载确认表单 POST（第 3 处）
OLD_POST = '''                            req = RequestUtils(
                                ua=ua,
                                cookies=cookie,
                                referer=referer,
                                proxies=settings.PROXY if proxy else None
                            ).post_res(url=action, data=data)'''

NEW_POST = '''                            req = RequestUtils(
                                ua=ua,
                                cookies=cookie,
                                referer=referer,
                                proxies=settings.PROXY
                            ).post_res(url=action, data=data)'''


def main() -> int:
    if not os.path.isfile(TARGET):
        print("ERROR: 目标文件不存在: %s" % TARGET)
        return 1
    with io.open(TARGET, "r", encoding="utf-8") as f:
        src = f.read()

    if MARK in src:
        print("SKIP: 补丁已存在（%s）" % MARK)
        return 0

    # 先还原上一版强制代理补丁
    if FORCED in src:
        src = src.replace(FORCED, PLAIN)
        print("还原：已撤销上一版「强制代理」补丁")

    if OLD_GET not in src:
        print("ERROR: 未找到 download_torrent 的 GET 锚点（上游代码可能已变更）")
        return 1
    src = src.replace(OLD_GET, NEW_GET, 1)

    if OLD_POST in src:
        src = src.replace(OLD_POST, NEW_POST, 1)

    try:
        compile(src, TARGET, "exec")
    except SyntaxError as e:
        print("ERROR: 替换后语法错误，已放弃写入: %s" % e)
        return 1

    with io.open(TARGET, "w", encoding="utf-8") as f:
        f.write(src)

    print("OK: download_torrent 已改为「直连优先 → 失败降级代理」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
