# 猛玛 LARK A2 无线麦克风电适配说明

日期：2026-11-09。来源：**官方 WebHID 网页**（不是猜的，也不是抓包猜出来的）。

## 为什么是网页

猛玛给 LARK A2 的电脑端工具是一个网页：[res.hollyland.com/6118](https://res.hollyland.com/6118/index.html)。它用 `navigator.hid.requestDevice()` 直连接收器，所以协议全部以明文 JS 的形式躺在那 8.6 MB 的页面里。

## 设备与通道

| 项 | 值 |
| --- | --- |
| VID:PID | `3547:0411`（Shenzhen Hollyland Technology） |
| 产品名 | `Wireless Microphone` |
| 使用通道 | Usage Page `0xFF90` / Usage 1，in 64 / out 64 |
| report id | `0x55`（收发同号） |
| 另一通道 | `0xFF82` / report id `0x41`（本适配未使用） |

## 帧格式

```
请求（63 字节，不含 report id）
  [0] 0xAA  [1] 0xDD  [2] cmd  [3] device
  [4..5] payload 长度（大端）
  [6..]  payload

响应（读到的原始 report 含前置 report id 0x55）
  [1] 0xBB  [2] 0xDD  [3] cmd  [4] device
  [5..6] 长度（大端）
  [7..]  payload
```

设备字节：接收器 `0x40`、TX1 `0x41`、TX2 `0x42`；响应里 `0x81` = TX1、`0x82` = TX2。

## 命令（本适配只用到第一条）

| 名称 | 值 | 用途 |
| --- | --- | --- |
| `HEART_GET` | `0x10` | 心跳：在线状态、电量、降噪档、静音/充电位 |
| `NOISE_LEVEL_GET` / `SET` | `0x11` / `0x06` | 降噪档位（未实现） |
| `RULE_VERSION_GET` | `0x41` | 固件版本（未实现） |
| `VIBE_TEMPLATE_GET` / `SET` | `0x43` / `0x42` | 模板（未实现） |
| `VIBE_LANGUAGE_SET` | `0x4E` | 语言（未实现） |

## HEART payload 字段

| 索引 | 含义 |
| --- | --- |
| 0 | TX1 在线（1 = 在线） |
| 1 | TX2 在线 |
| 2 | TX1 电量百分比 |
| 3 | TX2 电量百分比 |
| 6 | 降噪档位 |
| 15 | bit0/bit1 = TX1/TX2 静音，bit2/bit3 = TX1/TX2 充电 |

## 本适配的取舍

- 双发射器取**在线发射器的最低电量**；两个都离线时返回未知，不显示 0%。
- 充电位按位判断，但实机当时未充电，**该位未实测验证**。
- 只做读取。网页支持的降噪切换、模板、语言等写入命令，本项目一概不实现。
- 电量小于等于 100 才接受，超出按无效处理。

## 验证记录

- 实机 `HEART_GET` 回包：`55 bb dd 10 80 00 10 01 00 62 00 00 00 00 05 00 00 00 00 00 00 04 00 ...`
- 解析：TX1 在线、电量 `0x62` = 98%、TX2 离线、未充电。
- `receiver_battery.read()` 返回 `ok=True level=98 status=discharging`。
