#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MoviePilot 核心补丁（待执行）：把 `Items/RemoteImages 未获取到返回数据` 降为 debug

背景：
    国产剧等 Emby 内无 TMDB Backdrop 的条目，每次通知都会打一条
        emby.py - Items/RemoteImages 未获取到返回数据，采用本地图片
    属正常回退路径的提示，不是错误，但会刷日志。
    2026-11-07 已先修「Items/Id/Images ...」那条（patch-emby-image-probe-internal.py），
    本条按用户要求**延后到下次 moviepilot 重启时一并打**，避免额外重启。

改动：
    /app/app/modules/emby/emby.py 中
        logger.info(f"Items/RemoteImages 未获取到返回数据，采用本地图片")
    ->  logger.debug(...)
    仅改日志级别，**不改任何逻辑**。

用法（容器内，可重复执行）：
    docker cp patch-emby-remoteimages-debug.py moviepilot-v2:/tmp/
    docker exec moviepilot-v2 python3 /tmp/patch-emby-remoteimages-debug.py
    docker restart moviepilot-v2

回滚：
    docker cp /volume1/docker/moviepilot-v2/patches/emby.py.bak.<下次重启前的时间戳> \
      moviepilot-v2:/app/app/modules/emby/emby.py && docker restart moviepilot-v2
"""
import io
import os
import re
import sys

# 应用根目录：容器内默认 /app；本地验证时可设 MP_APP_ROOT=<源码树目录>
APP_ROOT = os.environ.get("MP_APP_ROOT", "/app")
TARGET = os.path.join(APP_ROOT, "app/modules/emby/emby.py")
MARK = "netstorm-patch:remoteimages-debug"
PATTERN = re.compile(
    r'^(?P<indent>[ \t]*)logger\.info\((?P<msg>f?"Items/RemoteImages 未获取到返回数据，采用本地图片")\)[ \t]*$',
    re.M,
)


def main() -> int:
    with io.open(TARGET, "r", encoding="utf-8") as f:
        src = f.read()

    if MARK in src:
        print("SKIP: 补丁已存在（%s）" % MARK)
        return 0

    m = PATTERN.search(src)
    if not m:
        print("ERROR: 未找到目标 logger.info 行（文件可能已变更）")
        return 1

    new_line = "%slogger.debug(%s)  # %s" % (m.group("indent"), m.group("msg"), MARK)

    # 结果自检：必须真的把原消息搬进去了（防住分组号写错把消息吃掉的情况）
    if "未获取到返回数据" not in new_line:
        print("ERROR: 替换结果不含原消息，已放弃写入")
        return 1

    src = src[: m.start()] + new_line + src[m.end():]

    try:
        compile(src, TARGET, "exec")
    except SyntaxError as e:
        print("ERROR: 替换后语法错误，已放弃写入: %s" % e)
        return 1

    with io.open(TARGET, "w", encoding="utf-8") as f:
        f.write(src)

    print("OK: 已将 Items/RemoteImages 提示降为 debug")
    print("  原行: %s" % m.group(0).strip())
    print("  新行: %s" % new_line.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
