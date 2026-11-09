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

## 控制功能（2026-11-09 追加验证）

网页驱动共 12 个命令，本适配只做**电量 + 降噪**，其余未实现。

### 降噪的真实机制

- 降噪档位**不是**用 `NOISE_LEVEL_GET` 单独查询，而是心跳 `HEART_GET` 的 **payload 第 6 字节**（0 = 关闭，1/2/3 = 10/15/20 dB）。
- `NOISE_SWITCH_SET (0x19)` 是**切换**（toggle），不是设值；payload 固定 `[0x02]`，每发一次状态翻转。
- `NOISE_LEVEL_SET (0x06)` 才是设档位，payload `[level]`。
- 网页的开启流程是「先发 0x19 打开，再发 0x06 设档位」。

### 实机闭环（2026-11-09 22:2x）

```
心跳 noise=0 → 0x19 → 心跳 noise=1 → 0x06[2] → 心跳 noise=2 → 0x19 → 心跳 noise=0
```

四个状态全部读回一致，证明开关与档位都是真的在改设备。

### 已实现

`app/lark_a2_control.py`：

- `build(cmd, payload, device)` — 6118 帧构建（64 字节含 report id）
- `parse_heart(payload)` — 在线/电量/降噪档/静音/充电
- `noise_level_request(level)` — 0–3，越界抛错
- `noise_switch_request()` — 切换开关

未实现：模板（0x42/0x43）、模板信息（0x44/0x45）、按键名（0x46/0x47）、语言（0x4E）。这些协议已知但需模板 CRUD 与校验，未做。

### 关于手机 APP 的独有功能

用户提到手机 APP 还有**单双声道**切换。人家把网页 JS 全量搜过：`channel` / `stereo` / `mono` / `声道` / `立体声` **零命中**，`workMode` 只是模板里的一个字节。说明单双声道属于**蓝牙/私有协议**，电脑端 WebHID 根本没有这条通路。

## 验证记录

- 实机 `HEART_GET` 回包：`55 bb dd 10 80 00 10 01 00 62 00 00 00 00 05 00 00 00 00 00 00 04 00 ...`
- 解析：TX1 在线、电量 `0x62` = 98%、TX2 离线、未充电。
- `receiver_battery.read()` 返回 `ok=True level=98 status=discharging`。
