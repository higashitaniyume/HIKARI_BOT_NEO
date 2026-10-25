"""命令行接口：qzone 命令（python -m qzone_sdk 亦可）。"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

from . import __version__
from .auth import (
    AuthProvider,
    ManualCookieProvider,
    NapCatAuthProvider,
    QRCodeLoginProvider,
    default_cookie_file,
    resolve_provider,
    save_cookie_file,
)
from .auth.qrcode import LoginStatus
from .client import QZoneClient
from .config import QZoneConfig
from .errors import QZoneError


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 认证选择
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_provider(args) -> AuthProvider:
    """--auth 显式指定，否则自动解析：环境变量 Cookie → 凭证文件 → NapCat。"""
    if args.auth == "napcat":
        return NapCatAuthProvider(
            ws_url=args.ws_url or os.environ.get("QZONE_WS_URL") or "ws://localhost:3001"
        )
    if args.auth == "cookie":
        if not (args.cookie and args.uin):
            raise QZoneError("--auth cookie 需要同时提供 --cookie 和 --uin")
        return ManualCookieProvider(args.cookie, args.uin)
    if args.auth == "qrcode":
        return QRCodeLoginProvider()
    return resolve_provider(
        cookie=args.cookie,
        uin=args.uin,
        cookie_file=args.cookie_file,
        ws_url=args.ws_url,
        interactive=True,
    )


def _make_client(args) -> QZoneClient:
    return QZoneClient(QZoneConfig(auth_provider=_build_provider(args)))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 命令实现
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _print_json(result: dict) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))


async def cmd_publish(args) -> int:
    client = _make_client(args)
    result = await client.publish(args.content, visible=args.visible, image_paths=args.image)
    _print_json(result)
    return 0 if result.get("success") else 1


async def cmd_delete(args) -> int:
    client = _make_client(args)
    result = await client.delete(args.tid, curkey=args.curkey, timestamp=args.timestamp)
    _print_json(result)
    return 0 if result.get("success") else 1


async def cmd_like(args) -> int:
    client = _make_client(args)
    result = await client.like(args.target_qq, args.fid, args.cur_key, args.uni_key)
    _print_json(result)
    return 0 if result.get("success") else 1


async def cmd_comment(args) -> int:
    client = _make_client(args)
    result = await client.comment(args.target_qq, args.fid, args.content)
    _print_json(result)
    return 0 if result.get("success") else 1


async def cmd_forward(args) -> int:
    client = _make_client(args)
    result = await client.forward(args.target_qq, args.tid, args.content)
    _print_json(result)
    return 0 if result.get("success") else 1


async def cmd_feed(args) -> int:
    client = _make_client(args)
    feeds = await client.get_feed_list(page=args.page, count=args.count)
    if not feeds:
        print("没有获取到动态")
        return 0
    for f in feeds:
        name = f.get("nickname") or f.get("remark") or f.get("uin", "")
        print(f"[{f.get('appid_name')}] {name}: {(f.get('content') or '')[:60]} | key={f.get('key')}")
    return 0


async def cmd_moods(args) -> int:
    client = _make_client(args)
    moods = await client.get_friend_moods(args.qq, num=args.num)
    if not moods:
        print("没有获取到说说")
        return 0
    for m in moods:
        print(f"[{m.get('createTime')}] {m.get('content', '')[:60]} | tid={m.get('tid')}")
    return 0


async def cmd_login(args) -> int:
    from .utils.png import render_qr_ascii

    provider = QRCodeLoginProvider(qr_path=args.qr_out)
    last_status: LoginStatus | None = None
    deadline = time.monotonic() + args.timeout
    qr_count = 0
    try:
        while True:
            if time.monotonic() > deadline:
                print("登录超时，请重试。", file=sys.stderr)
                return 1
            if last_status is None or last_status is LoginStatus.EXPIRED:
                image = await provider.get_qr_image()
                path = os.path.abspath(provider.save_qr_image(image))
                qr_count += 1
                print(f"[{qr_count}] 二维码已保存到: {path}")
                print()
                try:
                    print(render_qr_ascii(image))   # 终端直接显示，手机可直接扫描
                except Exception:
                    print("（无法在终端渲染，请直接打开上面的图片文件）")
                print()
                print("请用手机 QQ 扫描上方二维码（扫不出时用图片查看器打开图片文件）")
                last_status = LoginStatus.WAITING
                deadline = time.monotonic() + args.timeout

            state = await provider.check_login()
            if state.status is LoginStatus.SUCCESS:
                print("扫码确认成功，正在获取凭证...")
                auth = await provider.finish_login(state)
                break
            if state.status is LoginStatus.EXPIRED:
                print("二维码已失效，正在重新获取...")
                last_status = LoginStatus.EXPIRED
                continue
            if (
                state.status is LoginStatus.SCANNED
                and last_status is not LoginStatus.SCANNED
            ):
                print("已扫码，请在手机上确认登录")
            elif (
                state.status is LoginStatus.WAITING
                and last_status is not LoginStatus.WAITING
            ):
                print("等待扫码...")
            last_status = state.status
            await asyncio.sleep(args.poll_interval)
    except QZoneError as e:
        print(f"登录失败: {e}", file=sys.stderr)
        return 1

    print(f"登录成功！QQ: {auth.uin}")
    if args.save:
        path = save_cookie_file(auth, args.cookie_file)
        print(f"凭证已保存到: {path}（注意保管，勿泄露）")
        print("提示: 之后直接运行 qzone publish / feed 等命令即可，无需 NapCat。")
    else:
        print("提示: 使用 --save 可保存凭证，后续命令无需 NapCat。")
    return 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 参数解析
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qzone",
        description="QQ 空间操作命令行工具（qzone-sdk）",
        epilog=(
            "示例:\n"
            "  qzone login --save                     # 扫码登录并保存凭证（推荐首次使用）\n"
            '  qzone publish "你好，世界"\n'
            '  qzone publish "看这张图" --image a.jpg b.jpg\n'
            '  qzone publish "私密日记" --visible 4\n'
            "  qzone feed --count 10\n"
            "  qzone moods 123456789 --num 5\n"
            "  qzone delete <tid>\n"
            "文档: https://github.com/Eganchiyu/qzone-sdk"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    auth = parser.add_argument_group(
        "认证方式",
        "未显式指定时按序自动选择：$QZONE_COOKIE/$QZONE_UIN → Cookie 文件 → NapCat。"
        "不依赖 NapCat 的最简方式：先运行 qzone login --save。",
    )
    auth.add_argument("--auth", choices=["napcat", "cookie", "qrcode"], help="强制指定认证方式")
    auth.add_argument("--ws-url", help="NapCat WebSocket 地址（默认 $QZONE_WS_URL 或 ws://localhost:3001）")
    auth.add_argument("--cookie", help="手动 Cookie 字符串（与 --uin 搭配）")
    auth.add_argument("--uin", help="手动 QQ 号（与 --cookie 搭配）")
    auth.add_argument("--cookie-file", help="Cookie 文件路径（qzone login --save 生成）")

    sub = parser.add_subparsers(dest="command", metavar="命令")

    p = sub.add_parser("publish", help="发布说说", description="发布一条说说，支持纯文本、本地图片与可见范围。")
    p.add_argument("content", help="说说内容")
    p.add_argument("--image", nargs="+", metavar="PATH", help="本地图片路径（可多个，最多 9 张）")
    p.add_argument("--visible", type=int, choices=[1, 3, 4], default=1, metavar="N",
                   help="可见范围：1=公开（默认）、3=QQ 好友、4=仅自己")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("delete", help="删除说说", description="按 tid 删除一条说说（curkey 留空时自动使用 tid）。")
    p.add_argument("tid", help="说说 ID")
    p.add_argument("--curkey", default="", help="说说 key（可选，留空自动使用 tid；新发布的说说二者相同）")
    p.add_argument("--timestamp", type=int, default=0, help="发布时间戳（可选）")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("like", help="点赞说说", description="点赞一条说说（fid/cur_key/uni_key 可从 feed 或 moods 输出获取）。")
    p.add_argument("target_qq", help="对方 QQ 号")
    p.add_argument("fid", help="说说 fid")
    p.add_argument("cur_key", help="当前 key")
    p.add_argument("uni_key", help="唯一 key")
    p.set_defaults(func=cmd_like)

    p = sub.add_parser("comment", help="评论说说", description="对一条说说发表评论。")
    p.add_argument("target_qq", help="对方 QQ 号")
    p.add_argument("fid", help="说说的 topicId，格式如 {uin}_{tid}__1")
    p.add_argument("content", help="评论内容")
    p.set_defaults(func=cmd_comment)

    p = sub.add_parser("forward", help="转发说说", description="转发一条说说并可选附言。")
    p.add_argument("target_qq", help="原作者 QQ 号")
    p.add_argument("tid", help="说说 ID")
    p.add_argument("content", nargs="?", default="", help="转发附言（可选）")
    p.set_defaults(func=cmd_forward)

    p = sub.add_parser("feed", help="获取好友动态", description="获取当前账号的好友动态信息流。")
    p.add_argument("--page", type=int, default=1, help="页码（默认 1）")
    p.add_argument("--count", type=int, default=10, help="每页数量（默认 10）")
    p.set_defaults(func=cmd_feed)

    p = sub.add_parser("moods", help="获取好友说说", description="获取指定 QQ 的说说列表。")
    p.add_argument("qq", help="目标 QQ 号")
    p.add_argument("--num", type=int, default=10, help="获取数量（默认 10）")
    p.set_defaults(func=cmd_moods)

    p = sub.add_parser("login", help="扫码登录", description="通过手机 QQ 扫码登录：二维码直接显示在终端并保存为图片文件；加 --save 可保存凭证，之后无需 NapCat。")
    p.add_argument("--qr-out", default="qrcode.png", help="二维码图片保存路径（默认 qrcode.png，会同时显示在终端）")
    p.add_argument("--save", action="store_true", help="登录成功后保存凭证到 Cookie 文件")
    p.add_argument("--cookie-file", help="凭证保存路径（配合 --save，默认 ~/.qzone_sdk/cookies.json）")
    p.add_argument("--timeout", type=int, default=120, help="等待扫码超时秒数（默认 120）")
    p.add_argument("--poll-interval", type=float, default=3, help="状态轮询间隔秒数（默认 3）")
    p.set_defaults(func=cmd_login)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return asyncio.run(args.func(args))
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 130
    except QZoneError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
