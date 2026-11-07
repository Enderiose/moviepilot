#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MoviePilot 核心：图片下载的「外网 -> 内网」兜底重试补丁

背景
----
Telegram 等渠道是由 MoviePilot 服务端先把图片下载成 bytes 再上传
（app/helper/image.py: ImageHelper.fetch_image -> send_photo(photo=bytes)），
而企业微信等渠道是把 URL 交给客户端去拉。
NAS 内部访问自己的公网域名:端口受 NAT 回环(hairpin)限制，实测：
  - 直连  https://e.286115.xyz:88/...            -> 000
  - 经    http://192.168.2.4:7890 (Clash) 代理    -> 000
  - 内网  http://192.168.2.6:8095/emby/...        -> 200 image/jpeg
所以企业微信有图、Telegram 无图（fetch_image 返回 None，降级为纯文本）。

补丁
----
1) 新增 ImageHelper._internal_equivalent_url()：把媒体服务器 play_host
   前缀的 URL 换成同一服务器的 host 地址。
2) fetch_image() / async_fetch_image()：首次请求失败后，用内网等价地址
   再取一次，仍失败才返回 None。

幂等：已存在 _internal_equivalent_url 时跳过。
"""
import os
import pathlib
import sys

# 应用根目录：容器内默认 /app；本地验证时可设 MP_APP_ROOT=<源码树目录>
APP_ROOT = os.environ.get("MP_APP_ROOT", "/app")
TARGET = pathlib.Path(APP_ROOT) / "app/helper/image.py"
MARK = "_internal_equivalent_url"

NEW_METHOD = '''    @staticmethod
    def _internal_equivalent_url(url: str) -> Optional[str]:
        """
        把媒体服务器的「外网播放地址」替换为「内网地址」。

        NAS 内部访问自己的公网域名/端口常受 NAT 回环(hairpin)限制，
        而本方法供 fetch_image 在首次下载失败后做一次等价替换重试。
        """
        try:
            from app.helper.mediaserver import MediaServerHelper
            for conf in (MediaServerHelper().get_configs() or {}).values():
                cfg = conf.config or {}
                play_host = str(cfg.get("play_host") or "").rstrip("/")
                host = str(cfg.get("host") or "").rstrip("/")
                if play_host and host and url.startswith(play_host):
                    return host + url[len(play_host):]
        except Exception as e:
            logger.debug(f"媒体服务器外网地址映射失败: {str(e)}")
        return None

'''

OLD_SYNC = '''        # 请求远程图片
        params = self._get_request_params(url, proxy, cookies)
        response = RequestUtils(**params).get_res(url=url)
        if response is None or response.status_code != 200:
            logger.warn(f"Failed to fetch image from URL: {url}")
            return None
'''

NEW_SYNC = '''        # 请求远程图片
        params = self._get_request_params(url, proxy, cookies)
        response = RequestUtils(**params).get_res(url=url)
        if response is None or response.status_code != 200:
            # 兜底：媒体服务器外网地址在 NAS 内部可能因 NAT 回环不通，换内网地址重试
            internal_url = self._internal_equivalent_url(url)
            if internal_url:
                logger.info(f"外网图片获取失败，改用内网地址重试: {internal_url}")
                response = RequestUtils(
                    **self._get_request_params(internal_url, None, cookies)
                ).get_res(url=internal_url)
        if response is None or response.status_code != 200:
            logger.warn(f"Failed to fetch image from URL: {url}")
            return None
'''

OLD_ASYNC = '''        # 请求远程图片
        params = self._get_request_params(url, proxy, cookies)
        response = await AsyncRequestUtils(**params).get_res(url=url)
        if response is None or response.status_code != 200:
            logger.warn(f"Failed to fetch image from URL: {url}")
            return None
'''

NEW_ASYNC = '''        # 请求远程图片
        params = self._get_request_params(url, proxy, cookies)
        response = await AsyncRequestUtils(**params).get_res(url=url)
        if response is None or response.status_code != 200:
            # 兜底：媒体服务器外网地址在 NAS 内部可能因 NAT 回环不通，换内网地址重试
            internal_url = self._internal_equivalent_url(url)
            if internal_url:
                logger.info(f"外网图片获取失败，改用内网地址重试: {internal_url}")
                response = await AsyncRequestUtils(
                    **self._get_request_params(internal_url, None, cookies)
                ).get_res(url=internal_url)
        if response is None or response.status_code != 200:
            logger.warn(f"Failed to fetch image from URL: {url}")
            return None
'''

ANCHOR = '''    @staticmethod
    def _get_request_params(url: str, proxy: Optional[bool], cookies: Optional[str | dict]) -> dict:'''


def main() -> int:
    if not TARGET.exists():
        print("ERROR: 目标文件不存在: %s" % TARGET)
        return 1

    src = TARGET.read_text(encoding="utf-8")
    if MARK in src:
        print("SKIP: 已存在 %s，补丁已打过。" % MARK)
        return 0

    for name, text in (("sync", OLD_SYNC), ("async", OLD_ASYNC), ("anchor", ANCHOR)):
        if src.count(text) != 1:
            print("ERROR: 锚点 %s 出现 %d 次（期望 1），中止，未修改。" % (name, src.count(text)))
            return 2

    new_src = src.replace(OLD_SYNC, NEW_SYNC, 1).replace(OLD_ASYNC, NEW_ASYNC, 1)
    new_src = new_src.replace(ANCHOR, NEW_METHOD + ANCHOR, 1)

    backup = TARGET.with_suffix(".py.bak.20261104")
    if os.environ.get("MP_PATCH_NO_BACKUP") == "1":
        # 镜像构建时不需要留备份，避免把 .bak 打进镜像
        backup = None
    else:
        backup.write_text(src, encoding="utf-8")
    TARGET.write_text(new_src, encoding="utf-8")

    import py_compile
    try:
        py_compile.compile(str(TARGET), doraise=True)
    except Exception as exc:
        TARGET.write_text(src, encoding="utf-8")
        print("ERROR: 语法校验失败，已回滚: %s" % exc)
        return 3

    print("OK: 补丁完成。备份=%s" % (backup if backup else "（已跳过，MP_PATCH_NO_BACKUP=1）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
