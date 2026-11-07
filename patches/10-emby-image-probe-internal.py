#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MoviePilot 核心补丁：Emby.generate_external_image_link() 探测改用内网地址

问题：
    原实现用 {_playhost}Items/{id}/Images/{type} 做一次真实 GET 探测。
    _playhost 是公网地址，NAS 内部访问自己的公网域名需要 NAT 回环（hairpin），
    实测 000 超时 → 探测必然失败 → 必然打 INFO
    "Items/Id/Images 未获取到返回数据或无该影片Backdrop图片" 并返回 None，
    但图片实际存在（内网直取 200），属误报。

修复：
    1) 探测改走 {_host}emby/Items/{id}/Images/{type}（内网，实测 200）
    2) 返回给客户端的仍是 {_playhost}Items/{id}/Images/{type}（公网，手机可访问）
    3) 200  → 返回外网地址（日志降为 debug）
       404  → 确实没图，返回 None（日志 debug，不再是 info）
       其它 → 按地址直出（客户端可访问，优于直接丢弃）
       异常 → 按地址直出（同上）
    幂等：已打过（含标记）则跳过。

用法（容器内）：
    docker cp patch-emby-image-probe-internal.py moviepilot-v2:/tmp/
    docker exec moviepilot-v2 python3 /tmp/patch-emby-image-probe-internal.py
    docker restart moviepilot-v2
"""
import io
import os
import re
import sys

# 应用根目录：容器内默认 /app；本地验证时可设 MP_APP_ROOT=<源码树目录>
APP_ROOT = os.environ.get("MP_APP_ROOT", "/app")
TARGET = os.path.join(APP_ROOT, "app/modules/emby/emby.py")
MARK = "netstorm-patch:internal-probe"

NEW_FUNC = '''    def generate_external_image_link(self, item_id: str, image_type: str) -> Optional[str]:
        """
        根据ItemId和imageType查询本地对应图片
        :param item_id: 在Emby中的ID
        :param image_type: 图片类型，如Backdrop、Primary
        :return: 图片对应在外网播放器中的URL
        """
        if not self._playhost:
            logger.error("Emby外网播放地址未能获取或为空")
            return None

        # {MARK}
        # 外网播放地址只用于「返回给客户端」，探测必须走内网地址：
        # NAS 访问自己的公网域名需要 NAT 回环(hairpin)，通常不通 -> 旧实现必然误报"未获取到返回数据"
        external_url = f"{self._playhost}Items/{item_id}/Images/{image_type}"
        probe_url = f"{self._host}emby/Items/{item_id}/Images/{image_type}" if self._host else external_url
        try:
            res = RequestUtils(timeout=10).get_res(probe_url)
            code = getattr(res, "status_code", None)
            if code == 200:
                logger.debug(f"影片图片链接:{external_url}")
                return external_url
            if code == 404:
                logger.debug("Items/Id/Images 该影片确实无{}图片，item_id={}".format(image_type, item_id))
                return None
            logger.debug(f"Items/Id/Images 探测返回 {code}，按地址直出: {external_url}")
            return external_url
        except Exception as e:
            logger.debug(f"连接Items/Id/Images出错，按地址直出：{str(e)}")
            return external_url

'''.replace("{MARK}", MARK)


def main() -> int:
    with io.open(TARGET, "r", encoding="utf-8") as f:
        src = f.read()

    if MARK in src:
        print("SKIP: 补丁已存在（%s）" % MARK)
        return 0

    pattern = re.compile(
        r"^    def generate_external_image_link\(.*?(?=^    def )",
        re.S | re.M,
    )
    m = pattern.search(src)
    if not m:
        print("ERROR: 未找到 generate_external_image_link 函数")
        return 1

    old = m.group(0)
    src = src[: m.start()] + NEW_FUNC + src[m.end():]

    # 语法自检
    try:
        compile(src, TARGET, "exec")
    except SyntaxError as e:
        print("ERROR: 替换后语法错误，已放弃写入: %s" % e)
        return 1

    with io.open(TARGET, "w", encoding="utf-8") as f:
        f.write(src)

    print("OK: 已替换 generate_external_image_link（原 %d 行 -> 新 %d 行）"
          % (old.count("\n"), NEW_FUNC.count("\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
