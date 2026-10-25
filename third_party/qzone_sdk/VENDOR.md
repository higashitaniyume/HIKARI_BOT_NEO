# qzone_sdk（vendored）

- 上游：https://github.com/Eganchiyu/qzone-sdk
- 版本：1.0.1（commit `2282b8bc50c185eb6f8f7a39a3c8c6a517484d77`）
- 许可：MIT（见同目录 `LICENSE`）
- 同步时间：2026-10-25

## 本地用法

`plugins/qzone/` 只使用其中的 `QZoneClient` / `QZoneConfig` / `ManualCookieProvider`
以及 `auth_from_cookies`、`compute_g_tk` 等纯函数；登录态由 NapCat 的
`get_cookies`（domain=`user.qzone.qq.com`）在每次调用时提供，不使用上游的
`NapCatAuthProvider`（它自带 websockets 依赖，而本项目的机器人已有 OneBot 连接）。

## 修改约定

不要直接改这个目录里的代码。上游有更新时按 `scripts/update_media_parser_vendor.ps1`
的思路重新覆盖，并在本文件记录新的 commit。
